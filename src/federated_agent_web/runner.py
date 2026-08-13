"""One-shot node runner — restart-safe operational slice (§3).

``NodeRunner`` uses the transport contract, ``ReplayStore``,
``PendingDelegationStore``, ``PinnedManifestTrustContext``, ``VerificationPolicy``,
``verify()``, and ``CapabilityExecutor``. It processes at most one inbound
envelope per invocation and exits. No endless loop, no service, no daemon.

The runner does NOT duplicate verification logic from ``verify.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import canonical
import uuid as _uuid

from .demo import CapabilityExecutor
from .documents import KIND_DELEGATION, KIND_RECEIPT, content_digest_of, now_utc_z
from .identity import NodeIdentity, validate_manifest_chain
from .pending import PendingDelegationStore
from .replay import ReplayStore
from .transports import FilesystemTransport, Transport
from .verify import (
    PinnedManifestTrustContext,
    VerificationPolicy,
    verify,
)

__all__ = ["NodeRunner", "run_once"]


def _load_peer_trust(peer_dir: Path) -> PinnedManifestTrustContext:
    """Load a trusted peer's manifest chain from its public state only.

    Reads ``<peer_dir>/node.json``, extracts the manifest chain, validates
    it with the existing ``validate_manifest_chain()`` mechanism, and returns
    a ``PinnedManifestTrustContext``. Never reads or requires peer private
    keys — this is a trust anchor, not a signing identity.
    """
    node_path = peer_dir / "node.json"
    if not node_path.is_file():
        raise FileNotFoundError(f"{node_path} not found")
    data = json.loads(node_path.read_text(encoding="utf-8"))
    manifests: list[dict[str, Any]] = list(data.get("manifests", []))
    if not manifests:
        raise ValueError("peer trust: empty or missing manifest chain")
    chain_check = validate_manifest_chain(manifests)
    if not chain_check.ok:
        raise ValueError(f"peer manifest chain invalid: {chain_check.reason}")
    return PinnedManifestTrustContext(
        chain=manifests,
        head_sequence=int(manifests[-1]["body"]["manifest_sequence"]),
        head_digest=canonical.content_digest(manifests[-1]),
        pinned_at=datetime.now(timezone.utc),
    )


class NodeRunner:
    """Restart-safe one-shot worker for a persisted FAW node."""

    def __init__(
        self,
        identity: NodeIdentity,
        trust_context: PinnedManifestTrustContext,
        transport: Transport,
        state_dir: Path,
        work_dir: Path,
        *,
        role: str,
    ) -> None:
        self.identity = identity
        self.trust_context = trust_context
        self.transport = transport
        self.state_dir = Path(state_dir)
        self.work_dir = Path(work_dir)
        self.role = role
        self.replay = ReplayStore(self.state_dir / "replay")
        self.pending = PendingDelegationStore(self.state_dir / "pending")
        self.policy = VerificationPolicy(
            allowed_actions={"hash_file"},
            allowed_external_effects=frozenset({"none"}),
        )

    def run_once(self) -> int:
        """Process at most one inbound envelope; exit non-zero on failure."""
        envelopes = self.transport.poll()
        if not envelopes:
            print("idle: no inbound envelopes")
            return 0

        envelope = envelopes[0]
        if self.role == "executor":
            return self._run_executor(envelope)
        if self.role == "issuer":
            return self._run_issuer(envelope)
        print(f"error: unknown role {self.role!r}")
        return 1

    # -- executor ---------------------------------------------------------

    def _run_executor(self, envelope: Any) -> int:
        raw = envelope.document_bytes
        result = verify(
            raw,
            expected_kind=KIND_DELEGATION,
            local_node_id=self.identity.node_id,
            trust_context=self.trust_context,
            local_policy=self.policy,
            now=datetime.now(timezone.utc),
            replay_store=self.replay,
        )

        if not result.ok:
            reason = result.reason or f"step {result.step}"
            self.transport.nack(envelope.message_id, f"verification failed: {reason}")
            print(f"nack: {reason}")
            return 1

        # Verification succeeded; now safe to parse the validated document.
        delegation = canonical.parse_strict(raw)
        body = delegation["body"]
        attempt_id = body["attempt_id"]
        issuer_id = body["issuer_node_id"]
        delegation_digest = result.delegation_digest

        if result.deduplicated:
            if result.terminal_receipt is not None:
                # Resend the existing terminal receipt.
                receipt_bytes = canonical.canonical_bytes(result.terminal_receipt)
                sent = self.transport.send(receipt_bytes, issuer_id)
                if not sent.ok:
                    print(f"error: receipt resend failed for {attempt_id}: {sent.error}")
                    return 1
                self.transport.ack(envelope.message_id)
                print(f"deduplicated: resent terminal receipt for {attempt_id}")
                return 0
            if result.replay_state in ("pending", "executing"):
                # No terminal receipt; fail closed.
                self.transport.nack(
                    envelope.message_id,
                    f"replay state is {result.replay_state} with no terminal receipt",
                )
                print(f"nack: replay state {result.replay_state}, no terminal receipt")
                return 1

        if not result.admitted:
            print(f"error: delegation {attempt_id} not admitted")
            return 1

        # Mark executing before the capability runs.
        record = self.replay.get(issuer_id, attempt_id)
        if record is not None:
            self.replay.mark_executing(record)

        # Execute the capability.
        executor = CapabilityExecutor(self.identity)
        try:
            receipt = executor.execute(delegation, self.work_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"error: capability failed: {exc}")
            receipt = self.identity.sign_document(
                KIND_RECEIPT,
                {
                    "receipt_id": str(_uuid.uuid4()),
                    "task_id": body["task_id"],
                    "attempt_id": attempt_id,
                    "delegation_digest": delegation_digest,
                    "executor_node_id": self.identity.node_id,
                    "status": "failed",
                    "started_at": now_utc_z(),
                    "finished_at": now_utc_z(),
                    "artifacts": [],
                    "usage": {},
                    "failure": {"code": "runner.error", "message": str(exc)[:2048]},
                },
            )

        # Persist terminal receipt BEFORE transport delivery.
        if record is not None:
            self.replay.attach_terminal(record, receipt)

        # Send receipt to the issuer.
        receipt_bytes = canonical.canonical_bytes(receipt)
        sent = self.transport.send(receipt_bytes, issuer_id)
        if not sent.ok:
            print(f"error: receipt delivery failed for {attempt_id}: {sent.error}")
            # Do NOT acknowledge the inbound delegation.
            return 1

        # Acknowledge only after successful delivery.
        self.transport.ack(envelope.message_id)
        status = receipt["body"]["status"]
        print(f"executed: {status} for {attempt_id} -> {issuer_id}")
        return 0

    # -- issuer -----------------------------------------------------------

    def _run_issuer(self, envelope: Any) -> int:
        raw = envelope.document_bytes
        result = verify(
            raw,
            expected_kind=KIND_RECEIPT,
            local_node_id=self.identity.node_id,
            trust_context=self.trust_context,
            local_policy=VerificationPolicy(),
            now=datetime.now(timezone.utc),
            pending_store=self.pending,
        )

        if not result.ok:
            reason = result.reason or f"step {result.step}"
            self.transport.nack(envelope.message_id, f"receipt verification failed: {reason}")
            print(f"nack: {reason}")
            return 1

        self.transport.ack(envelope.message_id)
        receipt_doc = canonical.parse_strict(raw)
        body = receipt_doc["body"]
        print(f"accepted: receipt {body['receipt_id']} for {body['task_id']}/{body['attempt_id']} ({body['status']})")
        return 0


def run_once(
    *,
    identity_dir: Path,
    trust_dir: Path,
    transport_root: Path,
    state_dir: Path,
    work_dir: Path,
    role: str,
    transport: Transport | None = None,
) -> int:
    """Load identity and trust context, then process one envelope.

    Convenience wrapper for CLI use; constructs the ``NodeRunner`` and calls
    ``run_once()``.
    """
    identity = NodeIdentity.load(identity_dir)
    trust_context = _load_peer_trust(trust_dir)
    if transport is None:
        transport = FilesystemTransport(transport_root, identity.node_id)
    runner = NodeRunner(
        identity=identity,
        trust_context=trust_context,
        transport=transport,
        state_dir=state_dir,
        work_dir=work_dir,
        role=role,
    )
    return runner.run_once()

"""One-shot node runner — restart-safe operational slice (§3).

``NodeRunner`` uses the existing ``FilesystemTransport``, ``ReplayStore``,
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
from .identity import NodeIdentity
from .pending import PendingDelegationStore
from .replay import ReplayStore
from .transports import FilesystemTransport
from .verify import (
    PinnedManifestTrustContext,
    VerificationPolicy,
    verify,
)

__all__ = ["NodeRunner", "run_once"]


def _load_peer_trust(peer_dir: Path) -> PinnedManifestTrustContext:
    identity = NodeIdentity.load(peer_dir)
    return PinnedManifestTrustContext.from_chain(identity.manifests)


class NodeRunner:
    """Restart-safe one-shot worker for a persisted FAW node."""

    def __init__(
        self,
        identity: NodeIdentity,
        trust_context: PinnedManifestTrustContext,
        transport: FilesystemTransport,
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
        delegation = canonical.parse_strict(envelope.document_bytes)
        body = delegation["body"]
        attempt_id = body["attempt_id"]
        issuer_id = body["issuer_node_id"]

        result = verify(
            envelope.document_bytes,
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
        receipt_doc = canonical.parse_strict(envelope.document_bytes)

        result = verify(
            envelope.document_bytes,
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
) -> int:
    """Load identity and trust context, then process one envelope.

    Convenience wrapper for CLI use; constructs the ``NodeRunner`` and calls
    ``run_once()``.
    """
    identity = NodeIdentity.load(identity_dir)
    trust_context = _load_peer_trust(trust_dir)
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

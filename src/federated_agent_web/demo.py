"""Deterministic two-node offline end-to-end demo (§12).

Creates two ephemeral node identities, exchanges signed manifests, delivers a
signed delegation through the filesystem adapter, verifies it at the receiver,
executes a deterministic capability (SHA-256 of an input file), and returns a
signed terminal receipt that the issuer verifies against its pending store.

No network access is used. Exits non-zero if any invariant fails.
"""

from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import canonical
from .canonical import digest_bytes
from .documents import (
    KIND_DELEGATION,
    KIND_RECEIPT,
    content_digest_of,
)
from .execution import default_execution_registry, receipt_from_result
from .identity import NodeIdentity
from .pending import PendingDelegationStore
from .replay import ReplayStore
from .transports import FilesystemTransport
from .verify import (
    PinnedManifestTrustContext,
    VerificationPolicy,
    verify,
)

__all__ = ["CapabilityExecutor", "run_demo"]

DEMO_INPUT_BYTES = b"FAW deterministic demo input (v0.2)\nline two\n"
CAPABILITY = "hash_file"


class CapabilityExecutor:
    """Deterministic executor for the ``hash_file`` capability.

    Honors the delegation deadline: if execution would start after the
    deadline, a ``timed_out`` terminal receipt is emitted instead. The wall
    clock is injectable so tests can force the deadline path deterministically.
    """

    def __init__(self, node: NodeIdentity, now_fn: Callable[[], datetime] | None = None) -> None:
        self.node = node
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def execute(self, delegation: dict, workdir: Path) -> dict:
        result = default_execution_registry(self.node, now_fn=self.now_fn).execute(
            delegation, workdir
        )
        return receipt_from_result(self.node, delegation, result)


# The executor needs the delegation digest for receipt binding; the demo passes
# it explicitly via a module-level slot set in run_demo (single-threaded).

def _ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def run_demo(workdir: Path | None = None, verbose: bool = True) -> int:
    """Run the §12 demo; return 0 on success, 1 on failure."""
    base = Path(workdir) if workdir is not None else Path(tempdir())
    base.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        if verbose:
            print(message)

    try:
        # 1. Two ephemeral node identities.
        node_a = NodeIdentity.create(display_name="Demo Node A", capabilities=[CAPABILITY])
        node_b = NodeIdentity.create(display_name="Demo Node B", capabilities=[CAPABILITY])

        # 2. Generate and verify both genesis manifests (self-consistent).
        for node in (node_a, node_b):
            manifest = node.head_manifest
            result = verify(
                canonical.canonical_bytes(manifest),
                expected_kind="faw-node-manifest",
                local_node_id=node.node_id,
                trust_context=PinnedManifestTrustContext.from_chain(node.manifests),
                local_policy=VerificationPolicy(),
                now=datetime.now(timezone.utc),
            )
            assert result.ok, f"manifest verification failed for {node.node_id}: {result.reason}"
        log("manifest: two genesis manifests generated and verified")

        # Trust: A pins B, B pins A (locally approved anchors).
        ctx_a_pins_b = PinnedManifestTrustContext.from_chain(node_b.manifests)
        ctx_b_pins_a = PinnedManifestTrustContext.from_chain(node_a.manifests)

        # 3. Node A issues one signed delegation to node B.
        input_path = base / "input.bin"
        input_path.write_bytes(DEMO_INPUT_BYTES)
        input_digest = digest_bytes(DEMO_INPUT_BYTES)
        issued = datetime.now(timezone.utc)
        expires = issued + timedelta(seconds=600)
        deadline = issued + timedelta(seconds=1200)
        delegation = node_a.sign_document(
            KIND_DELEGATION,
            {
                "task_id": str(uuid.uuid4()),
                "attempt_id": str(uuid.uuid4()),
                "issuer_node_id": node_a.node_id,
                "target_node_id": node_b.node_id,
                "capability": CAPABILITY,
                "input": {
                    "kind": "refs",
                    "refs": [{"digest": input_digest, "location": str(input_path)}],
                },
                "authority": {
                    "actions": [CAPABILITY],
                    "filesystem_scope": {"read_paths": [str(input_path)]},
                    "external_effect_scope": {"allowed_effects": ["none"]},
                    "expiry": _ts(deadline + timedelta(seconds=3600)),
                },
                "budget": {"max_wall_seconds": 60, "max_output_bytes": 8192},
                "deadline": _ts(deadline),
                "expected_output": {
                    "kind": "artifact",
                    "media_type": "application/json",
                    "required_artifacts": ["result.json"],
                    "expects_repository_mutation": False,
                },
                "expires_at": _ts(expires),
            },
        )
        delegation_digest = content_digest_of(delegation)
        log(f"delegation: {delegation_digest}")

        # 4. Register outstanding BEFORE handing to a transport (§11).
        transport_root = base / "transport"
        pending_a = PendingDelegationStore(base / "pending_a")
        pending_a.register_outstanding(delegation, delegation_digest)
        transport_a = FilesystemTransport(transport_root, node_a.node_id)
        transport_b = FilesystemTransport(transport_root, node_b.node_id)

        # 5. Deliver through the filesystem adapter.
        sent = transport_a.send(canonical.canonical_bytes(delegation), node_b.node_id)
        assert sent.ok, f"send failed: {sent.error}"
        envelopes = transport_b.poll()
        assert len(envelopes) == 1, "expected exactly one delivered envelope"
        envelope = envelopes[0]

        # 6. Verify before execution (audience: B; trust: B pins A).
        executor = CapabilityExecutor(node_b)
        policy = VerificationPolicy(allowed_actions={CAPABILITY})
        admission = verify(
            envelope.document_bytes,
            expected_kind=KIND_DELEGATION,
            local_node_id=node_b.node_id,
            trust_context=ctx_b_pins_a,
            local_policy=policy,
            now=datetime.now(timezone.utc),
            replay_store=ReplayStore(base / "replay_b"),
        )
        assert admission.ok and admission.admitted, f"admission failed: {admission.reason}"
        log(f"admission: attempt {delegation['body']['attempt_id']} admitted at {node_b.node_id}")

        # 7. Execute the deterministic capability.
        receipt = executor.execute(delegation, base / "node_b_work")

        # 8. Deliver and verify the receipt at node A (issuer side).
        sent = transport_b.send(canonical.canonical_bytes(receipt), node_a.node_id)
        assert sent.ok, f"receipt send failed: {sent.error}"
        envelopes = transport_a.poll()
        assert len(envelopes) == 1, "expected exactly one receipt envelope"
        acceptance = verify(
            envelopes[0].document_bytes,
            expected_kind=KIND_RECEIPT,
            local_node_id=node_a.node_id,
            trust_context=ctx_a_pins_b,
            local_policy=VerificationPolicy(),
            now=datetime.now(timezone.utc),
            pending_store=pending_a,
        )
        assert acceptance.ok, f"receipt acceptance failed: {acceptance.reason}"
        assert acceptance.terminal_receipt is not None

        # 9. Print the §12 identifiers and digests.
        receipt_body = receipt["body"]
        artifact = receipt_body["artifacts"][0]
        receipt_digest = content_digest_of(receipt)
        log(f"task_id:        {delegation['body']['task_id']}")
        log(f"attempt_id:     {delegation['body']['attempt_id']}")
        log(f"delegation_digest: {delegation_digest}")
        log(f"receipt_digest: {receipt_digest}")
        log(f"artifact_digest: {artifact['digest']}")
        log(f"receipt status: {receipt_body['status']}")

        # 10. Invariant checks.
        assert receipt_body["delegation_digest"] == delegation_digest, "receipt bound to wrong delegation"
        assert receipt_body["executor_node_id"] == node_b.node_id
        assert artifact["digest"] == digest_bytes(
            Path(artifact["location"]).read_bytes()
        ), "artifact digest must be over exact raw artifact bytes"
        assert pending_a.get_outstanding(receipt_body["task_id"], receipt_body["attempt_id"]).state == "terminal"
        log("demo: OK")
        return 0
    except AssertionError as exc:
        log(f"demo: FAILED: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - demo reports any failure
        log(f"demo: FAILED: {type(exc).__name__}: {exc}")
        return 1


def tempdir() -> str:
    import tempfile

    return tempfile.mkdtemp(prefix="faw-demo-")

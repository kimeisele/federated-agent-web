"""Operational proof that the one-shot runner works over Nadi, not just files."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from federated_agent_web import canonical
from federated_agent_web.canonical import digest_bytes
from federated_agent_web.documents import content_digest_of
from federated_agent_web.identity import NodeIdentity
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.runner import NodeRunner
from federated_agent_web.transports import NadiTransport
from federated_agent_web.transports.nadi import RelayPublishResult
from federated_agent_web.verify import PinnedManifestTrustContext


class StubRelayBackend:
    def __init__(self) -> None:
        self.mailboxes: dict[str, list] = {}

    def publish(self, envelopes: list) -> list[RelayPublishResult]:
        for envelope in envelopes:
            self.mailboxes.setdefault(envelope.destination_address, []).append(envelope)
        return [RelayPublishResult(envelope.message_id, True) for envelope in envelopes]

    def fetch(self, destination_address: str) -> list:
        return list(self.mailboxes.get(destination_address, []))


def _ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_node_runner_delegation_and_receipt_round_trip_over_nadi(tmp_path):
    """Two runners complete the signed lifecycle through an opaque Nadi relay."""
    issuer = NodeIdentity.create(display_name="Nadi runner issuer", capabilities=["hash_file"])
    executor = NodeIdentity.create(display_name="Nadi runner executor", capabilities=["hash_file"])
    backend = StubRelayBackend()

    issuer_transport = NadiTransport(
        state_root=tmp_path / "transport-issuer",
        node_id=issuer.node_id,
        relay_address="issuer-relay",
        routes={executor.node_id: "executor-relay"},
        backend=backend,
    )
    executor_transport = NadiTransport(
        state_root=tmp_path / "transport-executor",
        node_id=executor.node_id,
        relay_address="executor-relay",
        routes={issuer.node_id: "issuer-relay"},
        backend=backend,
    )

    input_bytes = b"runner over nadi\n"
    input_path = tmp_path / "input.bin"
    input_path.write_bytes(input_bytes)
    current = datetime.now(timezone.utc)
    delegation = issuer.sign_document(
        "faw-delegation",
        {
            "task_id": str(uuid.uuid4()),
            "attempt_id": str(uuid.uuid4()),
            "issuer_node_id": issuer.node_id,
            "target_node_id": executor.node_id,
            "capability": "hash_file",
            "input": {
            "kind": "refs",
            "refs": [{"digest": digest_bytes(input_bytes), "location": str(input_path)}],
        },
            "authority": {
            "actions": ["hash_file"],
            "filesystem_scope": {"read_paths": [str(input_path)]},
            "external_effect_scope": {"allowed_effects": ["none"]},
                "expiry": _ts(current + timedelta(hours=1)),
            },
            "budget": {"max_wall_seconds": 60, "max_output_bytes": 8192},
            "deadline": _ts(current + timedelta(minutes=20)),
            "expected_output": {
                "kind": "artifact",
                "media_type": "application/json",
                "required_artifacts": ["result.json"],
                "expects_repository_mutation": False,
            },
            "expires_at": _ts(current + timedelta(minutes=10)),
        },
    )

    issuer_state = tmp_path / "issuer-state"
    pending = PendingDelegationStore(issuer_state / "pending")
    pending.register_outstanding(delegation, content_digest_of(delegation))
    sent = issuer_transport.send(canonical.canonical_bytes(delegation), executor.node_id)
    assert sent.ok, sent.error

    executor_run = NodeRunner(
        executor,
        PinnedManifestTrustContext.from_chain(issuer.manifests),
        executor_transport,
        tmp_path / "executor-state",
        tmp_path / "executor-work",
        role="executor",
    )
    assert executor_run.run_once() == 0

    issuer_run = NodeRunner(
        issuer,
        PinnedManifestTrustContext.from_chain(executor.manifests),
        issuer_transport,
        issuer_state,
        tmp_path / "issuer-work",
        role="issuer",
    )
    assert issuer_run.run_once() == 0

    body = delegation["body"]
    record = pending.get_outstanding(body["task_id"], body["attempt_id"])
    assert record is not None
    assert record.state == "terminal"
    assert record.terminal_receipt is not None
    assert record.terminal_receipt["body"]["status"] == "succeeded"

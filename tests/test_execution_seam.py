"""Capability registry and neutral-result boundary tests."""

import uuid
from datetime import datetime, timedelta, timezone

from federated_agent_web import canonical
from federated_agent_web.execution import (
    ExecutionRegistry,
    RuntimeResult,
    receipt_from_result,
)
from federated_agent_web import runner as runner_module
from federated_agent_web.runner import NodeRunner
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.transports.base import (
    Transport,
    TransportEnvelope,
    TransportSendResult,
)
from federated_agent_web.verify import PinnedManifestTrustContext, VerificationPolicy, verify
def _delegation(issuer, executor):
    current = datetime.now(timezone.utc)
    ts = lambda value: value.isoformat().replace("+00:00", "Z")
    return issuer.sign_document("faw-delegation", {
        "task_id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "issuer_node_id": issuer.node_id,
        "target_node_id": executor.node_id,
        "capability": "hash_file",
        "input": {"kind": "inline", "data": {"seed": "execution-seam"}},
        "authority": {
            "actions": ["hash_file"],
            "external_effect_scope": {"allowed_effects": ["none"]},
            "expiry": ts(current + timedelta(hours=1)),
        },
        "budget": {"max_wall_seconds": 60, "max_output_bytes": 8192},
        "deadline": ts(current + timedelta(minutes=20)),
        "expected_output": {
            "kind": "artifact",
            "media_type": "application/json",
            "required_artifacts": ["result.json"],
            "expects_repository_mutation": False,
        },
        "expires_at": ts(current + timedelta(minutes=10)),
    })


class MemoryTransport(Transport):
    def __init__(self, raw: bytes):
        self.envelopes = [TransportEnvelope("message-1", raw, "local")]
        self.sent = []
        self.acked = []
        self.nacked = []

    def send(self, document: bytes, destination: str) -> TransportSendResult:
        self.sent.append((document, destination))
        return TransportSendResult("sent-1", True)

    def poll(self) -> list[TransportEnvelope]:
        return list(self.envelopes)

    def ack(self, transport_message_id: str) -> None:
        self.acked.append(transport_message_id)
        self.envelopes = []

    def nack(self, transport_message_id: str, reason: str) -> None:
        self.nacked.append((transport_message_id, reason))
        self.envelopes = []


class StubExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, delegation, workdir):
        self.calls += 1
        current = datetime.now(timezone.utc)
        return RuntimeResult("succeeded", current, current, usage={"output_bytes": 0})


def test_runtime_result_is_bound_and_signed_by_faw(tmp_path, issuer, executor):
    delegation = _delegation(issuer, executor)
    pending = PendingDelegationStore(tmp_path / "pending")
    pending.register_outstanding(delegation, canonical.content_digest(delegation))
    current = datetime.now(timezone.utc)
    result = RuntimeResult(
        "failed",
        current,
        current,
        failure={"code": "runtime.failed", "message": "controlled failure"},
    )
    receipt = receipt_from_result(executor, delegation, result)
    assert receipt["body"]["delegation_digest"] == canonical.content_digest(delegation)
    assert receipt["body"]["executor_node_id"] == executor.node_id
    verified = verify(
        canonical.canonical_bytes(receipt),
        expected_kind="faw-receipt",
        local_node_id=issuer.node_id,
        trust_context=PinnedManifestTrustContext.from_chain(executor.manifests),
        local_policy=VerificationPolicy(),
        now=current,
        pending_store=pending,
    )
    assert verified.ok, verified.reason


def test_custom_capability_executes_once_after_admission(tmp_path, issuer, executor):
    delegation = _delegation(issuer, executor)
    body = delegation["body"]
    body["capability"] = "custom.echo"
    body["authority"]["actions"] = ["custom.echo"]
    custom = issuer.sign_document("faw-delegation", body)
    transport = MemoryTransport(canonical.canonical_bytes(custom))
    stub = StubExecutor()
    runner = NodeRunner(
        executor,
        PinnedManifestTrustContext.from_chain(issuer.manifests),
        transport,
        tmp_path / "state",
        tmp_path / "work",
        role="executor",
        execution_registry=ExecutionRegistry({"custom.echo": stub}),
        execution_policy=VerificationPolicy(
            allowed_actions={"custom.echo"},
            allowed_external_effects=frozenset({"none"}),
        ),
    )
    assert runner.run_once() == 0
    assert stub.calls == 1
    assert len(transport.sent) == 1


def test_executor_not_called_when_verification_rejects(tmp_path, issuer, executor):
    delegation = _delegation(issuer, executor)
    body = delegation["body"]
    body["target_node_id"] = issuer.node_id
    wrong_audience = issuer.sign_document("faw-delegation", body)
    transport = MemoryTransport(canonical.canonical_bytes(wrong_audience))
    stub = StubExecutor()
    runner = NodeRunner(
        executor,
        PinnedManifestTrustContext.from_chain(issuer.manifests),
        transport,
        tmp_path / "state",
        tmp_path / "work",
        role="executor",
        execution_registry=ExecutionRegistry({"hash_file": stub}),
    )
    assert runner.run_once() == 1
    assert stub.calls == 0
    assert transport.nacked


def test_registry_unknown_capability_has_no_fallback(issuer, executor, tmp_path):
    delegation = _delegation(issuer, executor)
    body = delegation["body"]
    body["capability"] = "unknown"
    registry = ExecutionRegistry()
    try:
        registry.execute(delegation, tmp_path)
    except ValueError as exc:
        assert "no executor registered" in str(exc)
    else:
        raise AssertionError("unknown capability unexpectedly executed")


def test_run_once_wrapper_forwards_registry_and_policy(tmp_path, issuer, executor, monkeypatch):
    issuer_dir, executor_dir = tmp_path / "issuer", tmp_path / "executor"
    issuer.to_json(issuer_dir)
    executor.to_json(executor_dir)
    stub = StubExecutor()
    registry = ExecutionRegistry({"custom.echo": stub})
    policy = VerificationPolicy(allowed_actions={"custom.echo"})
    captured = {}

    def fake_run_once(self):
        captured["registry"] = self.execution_registry
        captured["policy"] = self.policy
        return 0

    monkeypatch.setattr(NodeRunner, "run_once", fake_run_once)
    assert runner_module.run_once(
        identity_dir=executor_dir,
        trust_dir=issuer_dir,
        transport_root=tmp_path / "transport",
        state_dir=tmp_path / "state",
        work_dir=tmp_path / "work",
        role="executor",
        execution_registry=registry,
        execution_policy=policy,
    ) == 0
    assert captured == {"registry": registry, "policy": policy}

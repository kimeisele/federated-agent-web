"""Transport and state conformance (§11, §13 "Transport and state")."""

from __future__ import annotations

from datetime import timedelta

from federated_agent_web import canonical
from federated_agent_web.demo import run_demo
from federated_agent_web.documents import KIND_DELEGATION, KIND_RECEIPT, content_digest_of
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.replay import ReplayStore
from federated_agent_web.verify import VerificationPolicy, verify

from .conftest import build_delegation, build_receipt, make_node_pair, now, trust_for


class TestTransportState:
    def test_pending_registered_before_transport_send(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        pending = PendingDelegationStore(tmp_path / "pending")
        pending.register_outstanding(delegation, content_digest_of(delegation))
        record = pending.get_outstanding(
            delegation["body"]["task_id"], delegation["body"]["attempt_id"]
        )
        assert record is not None and record.state == "outstanding"
        # Registration happened before any transport activity: no outbox exists yet.
        assert not (tmp_path / "transport").exists()

    def test_partial_multi_target_failure_retains_failed_message(self, tmp_path):
        issuer, executor = make_node_pair()
        from federated_agent_web.transports import FilesystemTransport

        root = tmp_path / "transport"
        sender = FilesystemTransport(root, issuer.node_id)
        receiver = FilesystemTransport(root, executor.node_id)
        # Sabotage the other destination: a FILE where its node dir must be a dir.
        blocked = root / "urn_faw_blocked-node-0001"
        blocked.parent.mkdir(parents=True, exist_ok=True)
        blocked.write_text("i am a file")
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        payload = canonical.canonical_bytes(delegation)

        good = sender.send(payload, executor.node_id)
        bad = sender.send(payload, "urn:faw:blocked-node-0001")
        assert good.ok, good.error
        assert not bad.ok
        # The failed message is retained in the sender's outbox (unacknowledged).
        pending_count = sender._outbox_pending()
        assert pending_count == 1
        # The successful delivery is in the receiver's inbox and unaffected.
        assert len(receiver.poll()) == 1

    def test_ack_removes_only_acknowledged_message(self, tmp_path):
        issuer, executor = make_node_pair()
        from federated_agent_web.transports import FilesystemTransport

        root = tmp_path / "transport"
        sender = FilesystemTransport(root, issuer.node_id)
        receiver = FilesystemTransport(root, executor.node_id)
        first = build_delegation(issuer, target_node_id=executor.node_id)
        second = build_delegation(issuer, target_node_id=executor.node_id)
        sender.send(canonical.canonical_bytes(first), executor.node_id)
        sender.send(canonical.canonical_bytes(second), executor.node_id)
        envelopes = receiver.poll()
        assert len(envelopes) == 2
        receiver.ack(envelopes[0].message_id)
        remaining = receiver.poll()
        assert len(remaining) == 1
        assert remaining[0].message_id == envelopes[1].message_id

    def test_duplicate_transport_delivery_safe(self, tmp_path):
        issuer, executor = make_node_pair()
        from federated_agent_web.transports import FilesystemTransport

        root = tmp_path / "transport"
        sender = FilesystemTransport(root, issuer.node_id)
        receiver = FilesystemTransport(root, executor.node_id)
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        payload = canonical.canonical_bytes(delegation)
        sender.send(payload, executor.node_id)
        sender.send(payload, executor.node_id)  # same bytes, delivered twice
        replay = ReplayStore(tmp_path / "replay")
        handler_calls = {"count": 0}
        for envelope in receiver.poll():
            result = verify(
                envelope.document_bytes,
                expected_kind=KIND_DELEGATION,
                local_node_id=executor.node_id,
                trust_context=trust_for(issuer),
                local_policy=VerificationPolicy(),
                now=now(),
                replay_store=replay,
            )
            assert result.ok, result.reason
            if result.admitted:
                handler_calls["count"] += 1
            receiver.ack(envelope.message_id)
        # At-most-once handler admission despite duplicate transport delivery.
        assert handler_calls["count"] == 1

    def test_malformed_input_never_reaches_handler(self, tmp_path):
        issuer, executor = make_node_pair()
        replay = ReplayStore(tmp_path / "replay")
        handler_calls = {"count": 0}
        for garbage in (b"not json at all", b'{"kind": "faw-delegation"}', b'{"a": 1, "a": 2}'):
            result = verify(
                garbage,
                expected_kind=KIND_DELEGATION,
                local_node_id=executor.node_id,
                trust_context=trust_for(issuer),
                local_policy=VerificationPolicy(),
                now=now(),
                replay_store=replay,
            )
            assert not result.ok
            if result.admitted:
                handler_calls["count"] += 1
        assert handler_calls["count"] == 0

    def test_receipt_acceptance_closes_only_matching_record(self, tmp_path):
        issuer, executor = make_node_pair()
        pending = PendingDelegationStore(tmp_path / "pending")
        delegation_a = build_delegation(issuer, target_node_id=executor.node_id)
        delegation_b = build_delegation(issuer, target_node_id=executor.node_id)
        pending.register_outstanding(delegation_a, content_digest_of(delegation_a))
        pending.register_outstanding(delegation_b, content_digest_of(delegation_b))
        receipt_a = build_receipt(executor, delegation_a)
        result = verify(
            canonical.canonical_bytes(receipt_a),
            expected_kind=KIND_RECEIPT,
            local_node_id=issuer.node_id,
            trust_context=trust_for(executor),
            local_policy=VerificationPolicy(),
            now=now(),
            pending_store=pending,
        )
        assert result.ok, result.reason
        record_a = pending.get_outstanding(
            delegation_a["body"]["task_id"], delegation_a["body"]["attempt_id"]
        )
        record_b = pending.get_outstanding(
            delegation_b["body"]["task_id"], delegation_b["body"]["attempt_id"]
        )
        assert record_a.state == "terminal"
        assert record_b.state == "outstanding"  # untouched

    def test_offline_demo_completes_end_to_end(self, tmp_path):
        assert run_demo(tmp_path / "demo", verbose=False) == 0

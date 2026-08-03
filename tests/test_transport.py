"""Non-adapter transport state tests (§11, §13 "Transport and state").

Adapter-neutral transport properties live in ``test_transport_contract.py``;
this module keeps the stateful, filesystem-independent behaviors separate.
"""

from __future__ import annotations

from federated_agent_web import canonical
from federated_agent_web.demo import run_demo
from federated_agent_web.documents import KIND_RECEIPT, content_digest_of
from federated_agent_web.pending import PendingDelegationStore
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

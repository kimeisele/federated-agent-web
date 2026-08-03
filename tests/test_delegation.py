"""Delegation and receipt conformance (§9, §10, §13 "Delegation and receipt")."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from federated_agent_web import canonical
from federated_agent_web.demo import CapabilityExecutor
from federated_agent_web.documents import KIND_DELEGATION, KIND_RECEIPT, content_digest_of
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.replay import ReplayStore
from federated_agent_web.verify import PinnedManifestTrustContext, VerificationPolicy, verify

from .conftest import (
    build_delegation,
    build_receipt,
    make_node_pair,
    now,
    ts,
    trust_for,
)

DELAYED = timedelta(seconds=600)
LATER = timedelta(seconds=1200)


def _verify_doc(doc, *, expected_kind, chain, local_node_id=None, policy=None, at=None, replay=None, pending=None):
    return verify(
        canonical.canonical_bytes(doc),
        expected_kind=expected_kind,
        local_node_id=local_node_id,
        trust_context=trust_for_chain(chain),
        local_policy=policy or VerificationPolicy(),
        now=at or now(),
        replay_store=replay,
        pending_store=pending,
    )


def trust_for_chain(chain):
    return PinnedManifestTrustContext.from_chain(chain)


class TestDelegationAdmission:
    def test_valid_delegation_admitted(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        replay = ReplayStore(tmp_path / "replay")
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id, replay=replay,
        )
        assert result.ok and result.admitted, result.reason
        assert result.delegation_digest == content_digest_of(delegation)
        assert replay.get(issuer.node_id, delegation["body"]["attempt_id"]) is not None

    def test_expired_delegation_rejected(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(
            issuer,
            target_node_id=executor.node_id,
            expires_at=ts(now() - timedelta(seconds=10)),
            deadline=ts(now() - timedelta(seconds=5)),
        )
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id, replay=ReplayStore(tmp_path / "r"),
        )
        assert not result.ok and result.step == 4

    def test_issued_at_equals_expires_at_rejected(self):
        issuer, executor = make_node_pair()
        # Envelope issued_at is ~now; an expires_at in the past is <= issued_at.
        delegation = build_delegation(
            issuer, target_node_id=executor.node_id,
            expires_at=ts(now() - timedelta(seconds=10)),
        )
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 4
        assert "issued_at >= expires_at" in result.reason

    def test_expires_at_after_deadline_rejected(self):
        issuer, executor = make_node_pair()
        base = now()
        delegation = build_delegation(
            issuer,
            target_node_id=executor.node_id,
            expires_at=ts(base + timedelta(seconds=2000)),
            deadline=ts(base + timedelta(seconds=1000)),
        )
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 4

    def test_authority_expiry_before_deadline_rejected(self):
        issuer, executor = make_node_pair()
        base = now()
        delegation = build_delegation(
            issuer,
            target_node_id=executor.node_id,
            deadline=ts(base + timedelta(seconds=1200)),
            authority={
                "actions": ["hash_file"],
                "expiry": ts(base + timedelta(seconds=600)),  # < deadline
            },
        )
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 4

    def test_relayed_delegation_rejected_before_key_resolution(self):
        # Issued to node C, delivered to node B: audience rejection must occur
        # at step 3, before key resolution (step 5) or any admission.
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id="urn:faw:other-node-0001")
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 3

    def test_capability_addressed_requires_explicit_policy(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        body = delegation["body"]
        del body["target_node_id"]
        body["capability_target"] = {"capability": "hash_file"}
        delegation = issuer.sign_document(KIND_DELEGATION, body)
        # No policy match -> rejected at step 3.
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 3
        # Explicit matching policy -> admitted.
        policy = VerificationPolicy(capability_targets={"hash_file": executor.node_id})
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id, policy=policy,
        )
        assert result.ok, result.reason

    def test_insufficient_authority_rejected(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(
            issuer,
            target_node_id=executor.node_id,
            authority={
                "actions": ["compute.thing"],  # capability hash_file not included
                "expiry": ts(now() + timedelta(seconds=7200)),
            },
        )
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id, replay=ReplayStore(tmp_path / "r"),
        )
        assert not result.ok and result.step == 10

    def test_action_not_permitted_by_local_policy_rejected(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        policy = VerificationPolicy(allowed_actions={"compute.thing"})
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id, policy=policy,
        )
        assert not result.ok and result.step == 10

    def test_unenforceable_budget_rejected(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(
            issuer,
            target_node_id=executor.node_id,
            budget={"max_wall_seconds": 60, "max_cost_usd": "0.01"},
        )
        policy = VerificationPolicy(can_enforce_cost=False)
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id, policy=policy,
        )
        assert not result.ok and result.step == 10
        assert "max_cost_usd" in result.reason

    def test_unbounded_budget_rejected(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id, budget={})
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 10

    def test_budget_over_local_cap_rejected(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(
            issuer,
            target_node_id=executor.node_id,
            budget={"max_wall_seconds": 10000, "max_output_bytes": 8192},
        )
        policy = VerificationPolicy(max_wall_seconds_cap=3600)
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id, policy=policy,
        )
        assert not result.ok and result.step == 10


class TestReplayProtection:
    def test_duplicate_delivery_at_most_once(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        replay = ReplayStore(tmp_path / "replay")
        raw = canonical.canonical_bytes(delegation)
        first = verify(
            raw, expected_kind=KIND_DELEGATION, local_node_id=executor.node_id,
            trust_context=trust_for(issuer), local_policy=VerificationPolicy(), now=now(),
            replay_store=replay,
        )
        second = verify(
            raw, expected_kind=KIND_DELEGATION, local_node_id=executor.node_id,
            trust_context=trust_for(issuer), local_policy=VerificationPolicy(), now=now(),
            replay_store=replay,
        )
        assert first.ok and first.admitted
        assert second.ok and not second.admitted and second.deduplicated
        assert replay.get(issuer.node_id, delegation["body"]["attempt_id"]).state == "pending"

    def test_dedup_returns_terminal_receipt_when_present(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        replay = ReplayStore(tmp_path / "replay")
        raw = canonical.canonical_bytes(delegation)
        verify(
            raw, expected_kind=KIND_DELEGATION, local_node_id=executor.node_id,
            trust_context=trust_for(issuer), local_policy=VerificationPolicy(), now=now(),
            replay_store=replay,
        )
        record = replay.get(issuer.node_id, delegation["body"]["attempt_id"])
        receipt = build_receipt(executor, delegation)
        replay.attach_terminal(record, receipt)
        third = verify(
            raw, expected_kind=KIND_DELEGATION, local_node_id=executor.node_id,
            trust_context=trust_for(issuer), local_policy=VerificationPolicy(), now=now(),
            replay_store=replay,
        )
        assert third.ok and third.deduplicated
        assert third.terminal_receipt is not None
        assert third.terminal_receipt["body"]["status"] == "succeeded"

    def test_reused_attempt_different_digest_integrity_violation(self, tmp_path):
        issuer, executor = make_node_pair()
        attempt_id = "9c16a6bc-b317-44ec-a438-a7d723c7434a"
        first_doc = build_delegation(issuer, target_node_id=executor.node_id, attempt_id=attempt_id)
        replay = ReplayStore(tmp_path / "replay")
        verify(
            canonical.canonical_bytes(first_doc), expected_kind=KIND_DELEGATION,
            local_node_id=executor.node_id, trust_context=trust_for(issuer),
            local_policy=VerificationPolicy(), now=now(), replay_store=replay,
        )
        # Same issuer + attempt, different content -> different digest.
        second_doc = build_delegation(
            issuer, target_node_id=executor.node_id, attempt_id=attempt_id,
            budget={"max_wall_seconds": 999, "max_output_bytes": 1},
        )
        result = verify(
            canonical.canonical_bytes(second_doc), expected_kind=KIND_DELEGATION,
            local_node_id=executor.node_id, trust_context=trust_for(issuer),
            local_policy=VerificationPolicy(), now=now(), replay_store=replay,
        )
        assert not result.ok and result.step == 9

    def test_retry_same_task_new_attempt_allowed(self, tmp_path):
        issuer, executor = make_node_pair()
        task_id = "cb00f9fa-0cea-4a2b-918a-1ba2f542a0c4"
        attempt_id = "9c16a6bc-b317-44ec-a438-a7d723c7434a"
        replay = ReplayStore(tmp_path / "replay")
        for attempt in (attempt_id, "5e2f8a1b-4a2b-47ec-9c38-000000000001"):
            delegation = build_delegation(
                issuer, target_node_id=executor.node_id, task_id=task_id, attempt_id=attempt,
            )
            result = verify(
                canonical.canonical_bytes(delegation), expected_kind=KIND_DELEGATION,
                local_node_id=executor.node_id, trust_context=trust_for(issuer),
                local_policy=VerificationPolicy(), now=now(), replay_store=replay,
            )
            assert result.ok and result.admitted, result.reason


class TestDeadlineEnforcement:
    def test_executor_rejects_input_outside_filesystem_scope(self, tmp_path):
        from federated_agent_web.demo import CapabilityExecutor
        from federated_agent_web.canonical import digest_bytes

        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        inside = tmp_path / "inside.bin"
        inside.write_bytes(b"payload")
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"payload")
        body = delegation["body"]
        body["input"] = {
            "kind": "refs",
            "refs": [{"digest": digest_bytes(b"payload"), "location": str(outside)}],
        }
        body["authority"]["filesystem_scope"] = {"read_paths": [str(inside)]}
        delegation = issuer.sign_document(KIND_DELEGATION, body)
        instance = CapabilityExecutor(executor)
        import pytest as _pytest

        with _pytest.raises(ValueError, match="outside declared filesystem"):
            instance.execute(delegation, tmp_path / "work")

    def test_execution_past_deadline_emits_timed_out(self, tmp_path):
        issuer, executor = make_node_pair()
        base = now()
        delegation = build_delegation(
            issuer,
            target_node_id=executor.node_id,
            expires_at=ts(base + timedelta(seconds=20)),
            deadline=ts(base + timedelta(seconds=30)),
        )
        input_path = tmp_path / "input.bin"
        input_path.write_bytes(b"payload")
        from federated_agent_web.canonical import digest_bytes

        body = delegation["body"]
        body["input"] = {
            "kind": "refs",
            "refs": [{"digest": digest_bytes(b"payload"), "location": str(input_path)}],
        }
        delegation = issuer.sign_document(KIND_DELEGATION, body)

        # Clock jumps past the deadline between admission and execution.
        clock = {"t": base}
        executor_instance = CapabilityExecutor(executor, now_fn=lambda: clock["t"])
        clock["t"] = base + timedelta(seconds=120)
        receipt = executor_instance.execute(delegation, tmp_path / "work")
        assert receipt["body"]["status"] == "timed_out"
        assert receipt["body"]["task_id"] == body["task_id"]
        assert receipt["body"]["attempt_id"] == body["attempt_id"]


class TestPendingRegistration:
    def test_capability_addressed_registration_requires_concrete_target(self, tmp_path):
        from federated_agent_web.pending import PendingDelegationStore, PendingStoreError

        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        body = delegation["body"]
        del body["target_node_id"]
        body["capability_target"] = {"capability": "hash_file"}
        delegation = issuer.sign_document(KIND_DELEGATION, body)
        pending = PendingDelegationStore(tmp_path / "pending")
        import pytest as _pytest

        with _pytest.raises(PendingStoreError, match="concrete target"):
            pending.register_outstanding(delegation, content_digest_of(delegation))


class TestReceiptBinding:
    def _accept(self, tmp_path, receipt, *, issuer, executor):
        pending = PendingDelegationStore(tmp_path / "pending")
        return verify(
            canonical.canonical_bytes(receipt),
            expected_kind=KIND_RECEIPT,
            local_node_id=issuer.node_id,
            trust_context=trust_for(executor),
            local_policy=VerificationPolicy(),
            now=now(),
            pending_store=pending,
        )

    def _setup(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        digest = content_digest_of(delegation)
        pending = PendingDelegationStore(tmp_path / "pending")
        pending.register_outstanding(delegation, digest)
        return issuer, executor, delegation, digest, pending

    def test_receipt_binds_to_exact_delegation(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        receipt = build_receipt(executor, delegation)
        result = verify(
            canonical.canonical_bytes(receipt), expected_kind=KIND_RECEIPT,
            local_node_id=issuer.node_id, trust_context=trust_for(executor),
            local_policy=VerificationPolicy(), now=now(), pending_store=pending,
        )
        assert result.ok and result.admitted, result.reason
        record = pending.get_outstanding(delegation["body"]["task_id"], delegation["body"]["attempt_id"])
        assert record.state == "terminal"

    def test_receipt_with_wrong_digest_rejected(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        receipt = build_receipt(executor, delegation, delegation_digest="sha256:" + "cd" * 32)
        result = verify(
            canonical.canonical_bytes(receipt), expected_kind=KIND_RECEIPT,
            local_node_id=issuer.node_id, trust_context=trust_for(executor),
            local_policy=VerificationPolicy(), now=now(), pending_store=pending,
        )
        assert not result.ok and result.step == 8

    def test_receipt_wrong_task_or_attempt_rejected(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        from uuid import uuid4

        for bad in (str(uuid4()), None):
            kwargs = {"task_id": bad} if bad else {"attempt_id": str(uuid4())}
            receipt = build_receipt(executor, delegation, **kwargs)
            result = verify(
                canonical.canonical_bytes(receipt), expected_kind=KIND_RECEIPT,
                local_node_id=issuer.node_id, trust_context=trust_for(executor),
                local_policy=VerificationPolicy(), now=now(), pending_store=pending,
            )
            assert not result.ok and result.step == 8

    def test_receipt_from_non_target_executor_rejected(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        other, _ = make_node_pair()
        # A receipt genuinely signed by a node that is not the delegation's
        # target: signature verifies against that node's manifest, but the
        # issuer-side binding (executor == target) fails at step 8.
        receipt = build_receipt(other, delegation)
        result = verify(
            canonical.canonical_bytes(receipt), expected_kind=KIND_RECEIPT,
            local_node_id=issuer.node_id, trust_context=trust_for(other),
            local_policy=VerificationPolicy(), now=now(), pending_store=pending,
        )
        assert not result.ok and result.step == 8

    def test_receipt_unknown_delegation_rejected(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        receipt = build_receipt(executor, delegation, task_id="11111111-1111-4111-8111-111111111111")
        result = verify(
            canonical.canonical_bytes(receipt), expected_kind=KIND_RECEIPT,
            local_node_id=issuer.node_id, trust_context=trust_for(executor),
            local_policy=VerificationPolicy(), now=now(), pending_store=pending,
        )
        assert not result.ok and result.step == 8

    def test_second_terminal_receipt_rejected(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        first = build_receipt(executor, delegation)
        assert verify(
            canonical.canonical_bytes(first), expected_kind=KIND_RECEIPT,
            local_node_id=issuer.node_id, trust_context=trust_for(executor),
            local_policy=VerificationPolicy(), now=now(), pending_store=pending,
        ).ok
        second = build_receipt(executor, delegation, status="failed")
        result = verify(
            canonical.canonical_bytes(second), expected_kind=KIND_RECEIPT,
            local_node_id=issuer.node_id, trust_context=trust_for(executor),
            local_policy=VerificationPolicy(), now=now(), pending_store=pending,
        )
        assert not result.ok and result.step == 8
        assert "already" in result.reason

    def test_receipt_requires_pending_store(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        receipt = build_receipt(executor, delegation)
        result = verify(
            canonical.canonical_bytes(receipt), expected_kind=KIND_RECEIPT,
            local_node_id=issuer.node_id, trust_context=trust_for(executor),
            local_policy=VerificationPolicy(), now=now(),
        )
        assert not result.ok and result.step == 8

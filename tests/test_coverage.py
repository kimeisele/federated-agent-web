"""Targeted branch-coverage tests for security-critical core paths.

These tests fill meaningful gaps identified by the baseline coverage report
for canonical.py, verify.py, replay.py, and pending.py. Each test asserts a
semantic property, not mere line execution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from federated_agent_web import canonical
from federated_agent_web.documents import (
    KIND_DELEGATION,
    KIND_RECEIPT,
    content_digest_of,
)
from federated_agent_web.pending import PendingDelegationStore, PendingStoreError
from federated_agent_web.replay import (
    ReplayAlreadyAdmitted,
    ReplayIntegrityViolation,
    ReplayStore,
)
from federated_agent_web.verify import VerificationPolicy, verify

from .conftest import (
    build_delegation,
    build_receipt,
    make_node_pair,
    now,
    ts,
    trust_for,
)


class TestReplayStorePaths:
    def test_attach_terminal_updates_state(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        replay = ReplayStore(tmp_path / "replay")
        record = replay.create(issuer.node_id, delegation["body"]["attempt_id"],
                               content_digest_of(delegation))
        receipt = build_receipt(executor, delegation)
        updated = replay.attach_terminal(record, receipt)
        assert updated.state == "terminal"
        assert updated.receipt is not None
        # Re-attaching the same receipt is idempotent
        updated2 = replay.attach_terminal(updated, receipt)
        assert updated2.state == "terminal"

    def test_mark_executing_from_pending(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        replay = ReplayStore(tmp_path / "replay")
        record = replay.create(issuer.node_id, delegation["body"]["attempt_id"],
                               content_digest_of(delegation))
        updated = replay.mark_executing(record)
        assert updated.state == "executing"

    def test_mark_executing_from_executing_idempotent(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        replay = ReplayStore(tmp_path / "replay")
        record = replay.create(issuer.node_id, delegation["body"]["attempt_id"],
                               content_digest_of(delegation))
        replay.mark_executing(record)
        # Re-mark from executing is allowed
        updated = replay.mark_executing(record)
        assert updated.state == "executing"

    def test_mark_executing_from_terminal_rejected(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        replay = ReplayStore(tmp_path / "replay")
        record = replay.create(issuer.node_id, delegation["body"]["attempt_id"],
                               content_digest_of(delegation))
        receipt = build_receipt(executor, delegation)
        replay.attach_terminal(record, receipt)
        with pytest.raises(ReplayIntegrityViolation, match="cannot start executing from terminal"):
            replay.mark_executing(record)

    def test_create_existing_different_digest_raises(self, tmp_path):
        issuer, executor = make_node_pair()
        at_id = "9c16a6bc-b317-44ec-a438-a7d723c7434a"
        replay = ReplayStore(tmp_path / "replay")
        d1 = build_delegation(issuer, target_node_id=executor.node_id, attempt_id=at_id)
        replay.create(issuer.node_id, at_id, content_digest_of(d1))
        with pytest.raises(ReplayIntegrityViolation):
            replay.create(issuer.node_id, at_id, "sha256:" + "bb" * 32)


class TestPendingStorePaths:
    def test_list_outstanding_includes_registered(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        pending = PendingDelegationStore(tmp_path / "pending")
        pending.register_outstanding(delegation, content_digest_of(delegation))
        outstanding = pending.list_outstanding()
        assert len(outstanding) == 1
        assert outstanding[0].state == "outstanding"

    def test_register_duplicate_rejected(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        pending = PendingDelegationStore(tmp_path / "pending")
        pending.register_outstanding(delegation, content_digest_of(delegation))
        with pytest.raises(PendingStoreError, match="already registered"):
            pending.register_outstanding(delegation, content_digest_of(delegation))

    def test_register_issuer_node_mismatch_rejected(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        delegation["issuer"]["node_id"] = "urn:faw:wrong-node-0001"
        delegation["body"]["issuer_node_id"] = issuer.node_id
        pending = PendingDelegationStore(tmp_path / "pending")
        with pytest.raises(PendingStoreError, match="issuer_node_id"):
            pending.register_outstanding(delegation, content_digest_of(delegation))


class TestVerifyMissingBranches:
    def test_stale_allowed_qualified_pass(self):
        """Stale context with reject_stale=False produces a qualified ok."""
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        window = issuer.freshness_window_seconds
        pinned_at = now() - timedelta(seconds=window + 1000)
        chain = __import__("federated_agent_web.verify", fromlist=["PinnedManifestTrustContext"]).PinnedManifestTrustContext.from_chain(
            issuer.manifests, pinned_at=pinned_at)
        result = verify(
            canonical.canonical_bytes(delegation),
            expected_kind=KIND_DELEGATION,
            local_node_id=executor.node_id,
            trust_context=chain,
            local_policy=VerificationPolicy(reject_stale=False),
            now=now(),
        )
        assert result.ok
        assert result.stale
        assert "stale" in (result.reason or "")

    def test_delegation_no_replay_store_ok(self):
        """Verification without a replay store returns ok (admitted=False)."""
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        result = verify(
            canonical.canonical_bytes(delegation),
            expected_kind=KIND_DELEGATION,
            local_node_id=executor.node_id,
            trust_context=trust_for(issuer),
            local_policy=VerificationPolicy(allowed_actions={"hash_file"}),
            now=now(),
        )
        assert result.ok
        assert not result.admitted

    def test_nondelegation_replay_store_skipped(self):
        """Manifest verification never hits the replay path."""
        issuer, _ = make_node_pair()
        manifest = issuer.head_manifest
        result = verify(
            canonical.canonical_bytes(manifest),
            expected_kind="faw-node-manifest",
            local_node_id=None,
            trust_context=trust_for(issuer),
            local_policy=VerificationPolicy(),
            now=now(),
            replay_store=ReplayStore(Path("/tmp/irrelevant")),
        )
        assert result.ok

    def test_concurrent_admission_dup_deduplicates(self, tmp_path):
        """Step 11: AlreadyAdmitted with matching digest deduplicates."""
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        digest_bytes = canonical.canonical_bytes(delegation)
        replay = ReplayStore(tmp_path / "r")
        # Pre-create the record to simulate a concurrent create
        replay.create(issuer.node_id, delegation["body"]["attempt_id"],
                      content_digest_of(delegation))
        result = verify(
            digest_bytes,
            expected_kind=KIND_DELEGATION,
            local_node_id=executor.node_id,
            trust_context=trust_for(issuer),
            local_policy=VerificationPolicy(allowed_actions={"hash_file"}),
            now=now(),
            replay_store=replay,
        )
        assert result.ok
        assert result.deduplicated
        assert not result.admitted

    def test_authority_external_effect_denied_from_policy(self, tmp_path):
        """External effect denied by local policy hits step 10 branch."""
        issuer, executor = make_node_pair()
        delegation = build_delegation(
            issuer, target_node_id=executor.node_id,
            authority={
                "actions": ["hash_file"],
                "external_effect_scope": {"allowed_effects": ["molotov"]},
                "expiry": ts(now() + timedelta(seconds=7200)),
            },
        )
        policy = VerificationPolicy(
            allowed_actions={"hash_file"},
            allowed_external_effects=frozenset({"none"}),
        )
        result = verify(
            canonical.canonical_bytes(delegation),
            expected_kind=KIND_DELEGATION,
            local_node_id=executor.node_id,
            trust_context=trust_for(issuer),
            local_policy=policy,
            now=now(),
            replay_store=ReplayStore(tmp_path / "r"),
        )
        assert not result.ok
        assert result.reason_code == "authority.external_effect_denied"

    def test_budget_max_cost_unenforceable(self, tmp_path):
        """max_cost_usd not enforceable → step 10 budget branch."""
        issuer, executor = make_node_pair()
        delegation = build_delegation(
            issuer, target_node_id=executor.node_id,
            budget={"max_wall_seconds": 60, "max_cost_usd": "0.01"},
        )
        policy = VerificationPolicy(can_enforce_cost=False, allowed_actions={"hash_file"})
        result = verify(
            canonical.canonical_bytes(delegation),
            expected_kind=KIND_DELEGATION,
            local_node_id=executor.node_id,
            trust_context=trust_for(issuer),
            local_policy=policy,
            now=now(),
            replay_store=ReplayStore(tmp_path / "r"),
        )
        assert not result.ok
        assert result.reason_code == "budget.unenforceable"

    def test_budget_wall_seconds_over_cap_rejected(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(
            issuer, target_node_id=executor.node_id,
            budget={"max_wall_seconds": 10000, "max_output_bytes": 8192},
        )
        policy = VerificationPolicy(max_wall_seconds_cap=3600, allowed_actions={"hash_file"})
        result = verify(
            canonical.canonical_bytes(delegation),
            expected_kind=KIND_DELEGATION,
            local_node_id=executor.node_id,
            trust_context=trust_for(issuer),
            local_policy=policy,
            now=now(),
            replay_store=ReplayStore(tmp_path / "r"),
        )
        assert not result.ok
        assert result.reason_code == "budget.unenforceable"


class TestCanonicalIntegerDomain:
    def test_canonical_bytes_raises_on_out_of_domain(self, tmp_path):
        """canonical_bytes wraps rfc8785 IntegerDomainError as CanonicalizationError."""
        with pytest.raises(canonical.CanonicalizationError):
            canonical.canonical_bytes({"n": 9007199254740992})

    def test_out_of_domain_integer_rejected_at_parse(self):
        with pytest.raises(canonical.CanonicalizationError, match="exceeds"):
            canonical.parse_strict(b'{"n": 9007199254740992}')

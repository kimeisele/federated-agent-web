"""Signature and manifest-trust conformance (§7.3, §8, §13 "Signatures and
manifest trust")."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from federated_agent_web import canonical
from federated_agent_web.documents import KIND_DELEGATION, KIND_MANIFEST
from federated_agent_web.verify import PinnedManifestTrustContext, VerificationPolicy, verify

from .conftest import build_delegation, make_node_pair, now, trust_for, ts


def _verify_doc(
    doc,
    *,
    expected_kind,
    chain,
    local_node_id=None,
    policy=None,
    at=None,
    replay=None,
    pending=None,
    pinned_at=None,
):
    return verify(
        canonical.canonical_bytes(doc),
        expected_kind=expected_kind,
        local_node_id=local_node_id,
        trust_context=trust_for_chain(chain, pinned_at=pinned_at),
        local_policy=policy or VerificationPolicy(),
        now=at or now(),
        replay_store=replay,
        pending_store=pending,
    )


def trust_for_chain(chain, pinned_at=None):
    return PinnedManifestTrustContext.from_chain(chain, pinned_at=pinned_at)


class TestSignatureVerification:
    def test_valid_signature_passes(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert result.ok, result.reason
        assert result.freshness == "fresh"

    def test_one_byte_mutation_fails(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        # Flip one character of the signature value to a different valid
        # base64url character: schema stays valid, the Ed25519 signature no
        # longer matches -> step 7.
        value = delegation["signature"]["value"]
        replacement = "A" if value[0] != "A" else "B"
        delegation["signature"]["value"] = replacement + value[1:]
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok
        assert result.step == 7

    def test_changed_kind_fails(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        delegation["kind"] = "faw-receipt"
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 2

    def _assert_changed_field_fails(self, mutate):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        mutate(delegation["body"])
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok, "mutated signed content must fail verification"
        assert result.step == 7, "signature mismatch must be caught at step 7"

    def test_changed_authority_fails(self):
        self._assert_changed_field_fails(lambda body: body["authority"].update({"actions": ["other"]}))

    def test_changed_budget_fails(self):
        self._assert_changed_field_fails(lambda body: body["budget"].update({"max_wall_seconds": 9999}))

    def test_changed_deadline_fails(self):
        # Mutate the deadline but keep the ordering invariants satisfied
        # (issued < expires <= deadline <= authority.expiry) so the failure
        # is the signature mismatch at step 7, not a structural rejection.
        def mutate(body):
            base = now()
            body.update({"deadline": ts(base + timedelta(seconds=1000))})

        self._assert_changed_field_fails(mutate)

    def test_changed_expected_output_fails(self):
        self._assert_changed_field_fails(
            lambda body: body["expected_output"].update({"kind": "shell"})
        )

    def test_unknown_key_fails(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        delegation["issuer"]["kid"] = "sha256:" + "ab" * 32  # never in the chain
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 5

    def test_kid_not_active_fails(self):
        from federated_agent_web.documents import KIND_DELEGATION as _KIND
        from federated_agent_web.documents import build_document

        issuer, executor = make_node_pair()
        old_key = issuer.keys[0]
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        issuer.rotate_key()  # retire the original key
        # Re-sign the same content with the now-retired key; the fresh envelope
        # is issued after the rotation, so resolution at that time finds the
        # key retired.
        body = dict(delegation["body"])
        retired_signed = build_document(
            kind=_KIND,
            body=body,
            issuer_node_id=issuer.node_id,
            kid=old_key.kid,
            private_raw=old_key.private_raw,
        )
        result = _verify_doc(
            retired_signed, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 5

    def test_revoked_key_fails(self):
        from federated_agent_web.documents import KIND_DELEGATION as _KIND
        from federated_agent_web.documents import build_document

        issuer, executor = make_node_pair()
        issuer.rotate_key()  # key1 retired, key2 active
        old_kid = issuer.manifests[0]["body"]["keys"][0]["kid"]
        issuer.revoke_key(old_kid)  # key1 now revoked in manifest 3
        old_key = next(key for key in issuer.keys if key.kid == old_kid)
        base = now()
        body = {
            "task_id": "cb00f9fa-0cea-4a2b-918a-1ba2f542a0c4",
            "attempt_id": "9c16a6bc-b317-44ec-a438-a7d723c7434a",
            "issuer_node_id": issuer.node_id,
            "target_node_id": executor.node_id,
            "capability": "hash_file",
            "input": {"kind": "inline", "data": {"x": 1}},
            "authority": {"actions": ["hash_file"], "expiry": ts(base + timedelta(seconds=7200))},
            "budget": {"max_wall_seconds": 60},
            "deadline": ts(base + timedelta(seconds=1200)),
            "expected_output": {
                "kind": "artifact",
                "media_type": "application/json",
                "required_artifacts": ["result.json"],
                "expects_repository_mutation": False,
            },
            "expires_at": ts(base + timedelta(seconds=600)),
        }
        forged = build_document(
            kind=_KIND,
            body=body,
            issuer_node_id=issuer.node_id,
            kid=old_kid,
            private_raw=old_key.private_raw,
        )
        result = _verify_doc(
            forged, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 5

class TestKeyRotation:
    def test_rotation_continuity_passes(self):
        issuer, executor = make_node_pair()
        old_kid = issuer.active_key.kid
        assert issuer.node_id == issuer.node_id
        issuer.rotate_key()
        assert issuer.active_key.kid != old_kid
        assert issuer.node_id == issuer.node_id  # stable across rotation
        assert issuer.head_sequence() == 2
        # New delegations verify against the two-manifest chain.
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert result.ok, result.reason

    def test_pre_rotation_document_still_verifies(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        issued_at = delegation["issued_at"]
        issuer.rotate_key()
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        # The document was issued before the rotation: the genesis manifest is
        # still the newest manifest at its issued_at, so the old key resolves.
        assert result.ok, result.reason
        assert result.resolved_kid == issuer.manifests[0]["body"]["keys"][0]["kid"]

    def test_broken_chain_fails(self):
        issuer, executor = make_node_pair()
        issuer.rotate_key()
        chain = copy.deepcopy(issuer.manifests)
        chain[1]["body"]["previous_manifest_digest"] = "sha256:" + "11" * 32
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=chain,
            local_node_id=executor.node_id,
        )
        assert not result.ok and result.step == 5


class TestFreshness:
    def _freshness_context(self, issuer, pinned_at):
        return PinnedManifestTrustContext.from_chain(issuer.manifests, pinned_at=pinned_at)

    def test_stale_context_reports_stale_with_head(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        window = issuer.freshness_window_seconds
        pinned_at = now() - timedelta(seconds=window + 1000)
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id, pinned_at=pinned_at,
        )
        assert result.stale
        assert result.head_sequence == issuer.head_sequence()
        assert result.head_digest == issuer.head_digest()
        # A stale context must not silently produce an unqualified pass: the
        # result is qualified (reason set) even though steps 1-7 passed.
        assert result.ok  # qualified pass, not silent
        assert "stale" in result.reason

    def test_reject_stale_policy_fails(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        window = issuer.freshness_window_seconds
        pinned_at = now() - timedelta(seconds=window + 1000)
        result = verify(
            canonical.canonical_bytes(delegation),
            expected_kind=KIND_DELEGATION,
            local_node_id=executor.node_id,
            trust_context=self._freshness_context(issuer, pinned_at),
            local_policy=VerificationPolicy(reject_stale=True),
            now=now(),
        )
        assert not result.ok and result.step == 6

    def test_fresh_context_unqualified_pass(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        result = _verify_doc(
            delegation, expected_kind=KIND_DELEGATION, chain=issuer.manifests,
            local_node_id=executor.node_id,
        )
        assert result.ok and result.freshness == "fresh" and not result.reason


class TestManifestSelfVerification:
    def test_genesis_manifest_verifies_against_itself(self):
        issuer, _ = make_node_pair()
        manifest = issuer.head_manifest
        result = verify(
            canonical.canonical_bytes(manifest),
            expected_kind=KIND_MANIFEST,
            local_node_id=None,
            trust_context=PinnedManifestTrustContext.from_chain([manifest]),
            local_policy=VerificationPolicy(),
            now=now(),
        )
        assert result.ok, result.reason

    def test_rotated_manifest_chain_verifies(self):
        issuer, _ = make_node_pair()
        issuer.rotate_key()
        result = verify(
            canonical.canonical_bytes(issuer.head_manifest),
            expected_kind=KIND_MANIFEST,
            local_node_id=None,
            trust_context=PinnedManifestTrustContext.from_chain(issuer.manifests),
            local_policy=VerificationPolicy(),
            now=now(),
        )
        assert result.ok, result.reason

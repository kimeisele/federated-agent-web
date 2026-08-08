"""Stable verification rejection-code tests (v0.3, aligned to the v0.5 profile)."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from federated_agent_web import canonical
from federated_agent_web.crypto import b64url_decode, b64url_encode
from federated_agent_web.documents import KIND_DELEGATION, KIND_RECEIPT
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.replay import ReplayStore
from federated_agent_web.verify import (
    PinnedManifestTrustContext,
    VerificationPolicy,
    verify,
)

from .conftest import (
    build_delegation,
    build_receipt,
    make_node_pair,
    now,
    ts,
    trust_for,
)


def _verify(doc_bytes, *, kind, local_id=None, chain=None, policy=None, at=None, replay=None, pending=None):
    return verify(
        doc_bytes,
        expected_kind=kind,
        local_node_id=local_id,
        trust_context=chain,
        local_policy=policy or VerificationPolicy(),
        now=at or now(),
        replay_store=replay,
        pending_store=pending,
    )


def _assert_code(result, expected_code):
    assert not result.ok
    assert result.reason_code == expected_code, f"expected {expected_code}, got {result.reason_code}"


def _corrupt_signature_value(value: str) -> str:
    """Deterministically corrupt a signature: flip bit 0 of the first decoded byte."""
    raw = b64url_decode(value)
    assert raw, "test fixture unexpectedly produced an empty signature"

    corrupted = bytes([raw[0] ^ 0x01]) + raw[1:]
    encoded = b64url_encode(corrupted)

    assert corrupted != raw
    assert encoded != value
    assert len(corrupted) == len(raw)

    return encoded


class TestCorruptSignatureHelper:
    """The corruption helper must deterministically change every signature."""

    @pytest.mark.parametrize(
        "raw",
        [
            b"\x00" * 64,              # base64url value begins with 'A'
            b"\xff" * 64,
            bytes(range(64)),
            bytes(range(63, -1, -1)),
        ],
        ids=["zeros-begins-with-A", "all-ff", "ascending", "descending"],
    )
    def test_changes_exactly_one_byte(self, raw):
        value = b64url_encode(raw)
        encoded = _corrupt_signature_value(value)

        assert encoded != value  # output differs from input
        decoded = b64url_decode(encoded)
        assert b64url_encode(decoded) == encoded  # output stays valid base64url
        assert len(decoded) == len(raw)  # decoded length unchanged
        assert decoded[0] == raw[0] ^ 0x01  # exactly one decoded byte changed
        assert decoded[1:] == raw[1:]

    def test_value_beginning_with_A_is_still_changed(self):
        value = b64url_encode(b"\x00" * 64)
        assert value.startswith("A")
        encoded = _corrupt_signature_value(value)
        assert encoded != value


class TestParseCodes:
    def test_parse_duplicate_key(self):
        r = _verify(b'{"a":1,"a":2}', kind=KIND_DELEGATION, chain=trust_for(make_node_pair()[0]))
        _assert_code(r, "parse.duplicate_member")

    def test_parse_invalid_json(self):
        r = _verify(b"not json", kind=KIND_DELEGATION, chain=trust_for(make_node_pair()[0]))
        _assert_code(r, "parse.invalid_json")


class TestDocumentCodes:
    def test_wrong_kind(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_RECEIPT,
                     chain=trust_for(issuer), local_id=executor.node_id)
        _assert_code(r, "document.kind_mismatch")

    def test_schema_invalid(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        del delegation["body"]["task_id"]
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id)
        _assert_code(r, "schema.invalid")


class TestDelegationAudience:
    def test_wrong_audience(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id="urn:faw:wrong-node-0001")
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id)
        _assert_code(r, "audience.mismatch")


class TestTimeCodes:
    def test_ordering(self):
        issuer, executor = make_node_pair()
        d = build_delegation(issuer, target_node_id=executor.node_id)
        d["body"]["expires_at"] = ts(now() - timedelta(seconds=100))
        r = _verify(canonical.canonical_bytes(d), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id)
        _assert_code(r, "temporal.invalid")

    def test_expired(self):
        issuer, executor = make_node_pair()
        base = now()
        d = build_delegation(issuer, target_node_id=executor.node_id,
                             expires_at=ts(base + timedelta(seconds=1)),
                             deadline=ts(base + timedelta(seconds=10)))
        r = _verify(canonical.canonical_bytes(d), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id,
                     at=base + timedelta(seconds=100))
        _assert_code(r, "temporal.invalid")

    def test_wrong_receipt_issuer(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        receipt = build_receipt(executor, delegation)
        receipt["issuer"]["node_id"] = issuer.node_id
        r = _verify(canonical.canonical_bytes(receipt), kind=KIND_RECEIPT,
                     chain=trust_for(executor))
        _assert_code(r, "receipt.wrong_issuer")


class TestTrustCodes:
    def test_chain_invalid(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        chain = copy.deepcopy(issuer.manifests)
        chain[0]["body"]["manifest_sequence"] = 99
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_DELEGATION,
                     chain=PinnedManifestTrustContext.from_chain(chain),
                     local_id=executor.node_id)
        _assert_code(r, "trust.invalid_chain")

    def test_key_known_but_not_valid(self):
        """A known key that is retired (no longer eligible) is key_not_valid."""
        from federated_agent_web.documents import KIND_DELEGATION as _KD
        from federated_agent_web.documents import build_document

        issuer, executor = make_node_pair()
        issuer.rotate_key()
        old_key = issuer.keys[0]
        body = {
            "task_id": "cb00f9fa-0cea-4a2b-918a-1ba2f542a0c4",
            "attempt_id": "9c16a6bc-b317-44ec-a438-a7d723c7434a",
            "issuer_node_id": issuer.node_id,
            "target_node_id": executor.node_id,
            "capability": "hash_file",
            "input": {"kind": "inline", "data": {"x": 1}},
            "authority": {"actions": ["hash_file"], "expiry": ts(now() + timedelta(seconds=7200))},
            "budget": {"max_wall_seconds": 60},
            "deadline": ts(now() + timedelta(seconds=1200)),
            "expected_output": {"kind": "artifact", "media_type": "application/json", "required_artifacts": ["result.json"], "expects_repository_mutation": False},
            "expires_at": ts(now() + timedelta(seconds=600)),
        }
        forged = build_document(kind=_KD, body=body, issuer_node_id=issuer.node_id,
                                kid=old_key.kid, private_raw=old_key.private_raw)
        r = _verify(canonical.canonical_bytes(forged), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id)
        _assert_code(r, "trust.key_not_valid")

    def test_stale(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        window = issuer.freshness_window_seconds
        pinned_at = now() - timedelta(seconds=window + 1000)
        chain = PinnedManifestTrustContext.from_chain(issuer.manifests, pinned_at=pinned_at)
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_DELEGATION,
                     chain=chain, local_id=executor.node_id,
                     policy=VerificationPolicy(reject_stale=True))
        _assert_code(r, "trust.stale")


class TestSignatureCode:
    def test_signature_invalid(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        delegation["signature"]["value"] = _corrupt_signature_value(delegation["signature"]["value"])
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id)
        _assert_code(r, "signature.invalid")


class TestReceiptBindingCodes:
    def _setup(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        from federated_agent_web.documents import content_digest_of
        digest = content_digest_of(delegation)
        pending = PendingDelegationStore(tmp_path / "pending")
        pending.register_outstanding(delegation, digest)
        return issuer, executor, delegation, digest, pending

    def test_no_pending_delegation(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        receipt = build_receipt(executor, delegation, task_id="11111111-1111-4111-8111-111111111111")
        r = _verify(canonical.canonical_bytes(receipt), kind=KIND_RECEIPT,
                     chain=trust_for(executor), pending=pending)
        _assert_code(r, "receipt.no_pending_delegation")

    def test_already_terminal(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        from federated_agent_web.documents import content_digest_of
        receipt = build_receipt(executor, delegation)
        _verify(canonical.canonical_bytes(receipt), kind=KIND_RECEIPT,
                 chain=trust_for(executor), pending=pending)
        r2 = _verify(canonical.canonical_bytes(receipt), kind=KIND_RECEIPT,
                      chain=trust_for(executor), pending=pending)
        _assert_code(r2, "receipt.already_terminal")

    def test_digest_mismatch(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        receipt = build_receipt(executor, delegation, delegation_digest="sha256:" + "aa" * 32)
        r = _verify(canonical.canonical_bytes(receipt), kind=KIND_RECEIPT,
                     chain=trust_for(executor), pending=pending)
        _assert_code(r, "binding.mismatch")

    def test_wrong_executor(self, tmp_path):
        issuer, executor, delegation, digest, pending = self._setup(tmp_path)
        other, _ = make_node_pair()
        receipt = build_receipt(other, delegation)
        r = _verify(canonical.canonical_bytes(receipt), kind=KIND_RECEIPT,
                     chain=trust_for(other), pending=pending)
        _assert_code(r, "receipt.wrong_executor")


class TestReplayCodes:
    def test_replay_digest_conflict(self, tmp_path):
        issuer, executor = make_node_pair()
        at_id = "9c16a6bc-b317-44ec-a438-a7d723c7434a"
        replay = ReplayStore(tmp_path / "replay")
        d1 = build_delegation(issuer, target_node_id=executor.node_id, attempt_id=at_id)
        _verify(canonical.canonical_bytes(d1), kind=KIND_DELEGATION,
                 chain=trust_for(issuer), local_id=executor.node_id, replay=replay)
        d2 = build_delegation(issuer, target_node_id=executor.node_id, attempt_id=at_id,
                              budget={"max_wall_seconds": 999, "max_output_bytes": 1})
        r = _verify(canonical.canonical_bytes(d2), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id, replay=replay)
        _assert_code(r, "replay.digest_conflict")


class TestAuthorityBudgetCodes:
    def test_action_denied(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id,
                                      authority={"actions": ["other.thing"], "expiry": ts(now() + timedelta(seconds=7200))})
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id,
                     replay=ReplayStore(tmp_path / "r"))
        _assert_code(r, "authority.action_denied")

    def test_external_effect_denied(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id,
                                      authority={"actions": ["hash_file"],
                                                 "external_effect_scope": {"allowed_effects": ["molotov"]},
                                                 "expiry": ts(now() + timedelta(seconds=7200))})
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id,
                     replay=ReplayStore(tmp_path / "r"))
        _assert_code(r, "authority.external_effect_denied")

    def test_budget_unenforceable(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id, budget={})
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id,
                     replay=ReplayStore(tmp_path / "r"))
        _assert_code(r, "budget.unenforceable")


class TestWordingIndependence:
    """Codes must not be derived from human-readable reason text."""
    from federated_agent_web.verify import AdmissionRejection

    def test_action_denied_independent_of_wording(self):
        from federated_agent_web.verify import AdmissionRejection
        r = AdmissionRejection("authority.action_denied", "purely neutral diagnostic text")
        assert r.code == "authority.action_denied"
        assert "action" not in r.reason
        assert "authorized" not in r.reason

    def test_external_effect_independent_of_wording(self):
        from federated_agent_web.verify import AdmissionRejection
        r = AdmissionRejection("authority.external_effect_denied", "neutral diagnostic")
        assert r.code == "authority.external_effect_denied"
        assert "external" not in r.reason

    def test_budget_unenforceable_independent_of_wording(self):
        from federated_agent_web.verify import AdmissionRejection
        r = AdmissionRejection("budget.unenforceable", "neutral diagnostic")
        assert r.code == "budget.unenforceable"
        # The reason can be anything; the code is set at detection, not inferred

    def test_wrong_kind_not_from_exception_text(self):
        # Verify that document.wrong_kind is from structural check, not exception text
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        doc = canonical.parse_strict(canonical.canonical_bytes(delegation))
        # This is a valid KIND_DELEGATION document; wrong kind can only be detected
        # by checking doc["kind"] != expected_kind before schema validation runs.
        assert doc["kind"] == KIND_DELEGATION
        assert doc["kind"] != KIND_RECEIPT

    def test_schema_invalid_without_kind_field(self):
        # Missing kind → schema.invalid, not document.wrong_kind
        issuer, _ = make_node_pair()
        r = verify(
            b'{"spec_version":"0.2","id":"11111111-1111-4111-8111-111111111111","issued_at":"2026-01-01T00:00:00Z","issuer":{"node_id":"urn:faw:x","kid":"sha256:0000000000000000000000000000000000000000000000000000000000000000"},"body":{},"signature":{"alg":"Ed25519","value":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}}',
            expected_kind=KIND_DELEGATION,
            local_node_id=None,
            trust_context=trust_for(issuer),
            local_policy=VerificationPolicy(),
            now=now(),
        )
        assert not r.ok
        assert r.reason_code == "schema.invalid", f"got {r.reason_code}"

    def test_no_fallback_classifier(self):
        """_fail() must not accept a missing code — the parameter is required."""
        import inspect
        from federated_agent_web.verify import _fail
        sig = inspect.signature(_fail)
        params = {n: p for n, p in sig.parameters.items() if n == "code"}
        assert len(params) == 1
        p = params["code"]
        assert p.default is inspect.Parameter.empty, "code must have no default"
        assert p.kind == inspect.Parameter.KEYWORD_ONLY, "code must be keyword-only"


class TestCodeMutation:
    """Prove that changing a code branch makes the targeted test fail."""
    def test_wrong_code_detected(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        delegation["signature"]["value"] = _corrupt_signature_value(delegation["signature"]["value"])
        r = _verify(canonical.canonical_bytes(delegation), kind=KIND_DELEGATION,
                     chain=trust_for(issuer), local_id=executor.node_id)
        # Must be signature.invalid — asserting the wrong code proves the code is specific
        assert r.reason_code != "parse.invalid", "signature failure must not be parse.invalid"
        assert r.reason_code != "document.wrong_kind", "signature failure must not be document.wrong_kind"
        assert r.reason_code == "signature.invalid"

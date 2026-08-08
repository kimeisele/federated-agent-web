"""v0.5 semantic-alignment regression tests (issue #29).

Proves the reference verifier now executes the merged interoperability
profile before language-neutral fixtures are frozen:

- nanosecond-precise timestamp comparison (fractional digits 7–9 never
  truncated in protocol semantics);
- typed strict-parse failure classes (never classified by message text);
- the thirteen profile rejection categories at the top level;
- unknown-key versus known-but-invalid-key distinction.

Fixtures here are ordinary internal reference tests; nothing is created
under ``vectors/``.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from federated_agent_web import canonical, crypto
from federated_agent_web.documents import (
    KIND_DELEGATION,
    KIND_MANIFEST,
    KIND_RECEIPT,
    build_document,
    content_digest_of,
    datetime_to_ns,
    parse_timestamp_ns,
)
from federated_agent_web.identity import resolve_key_detailed
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.verify import (
    PinnedManifestTrustContext,
    VerificationPolicy,
    verify,
)

from .conftest import build_delegation, build_receipt, make_node_pair, trust_for

NS = 1_000_000_000
TARGET = "urn:faw:executor-0001"
NODE_ID = "urn:faw:boundary-node-0001"

# Fixed instants around 2026-01-01 used across the boundary tests.
BASE_NS = parse_timestamp_ns("2026-01-01T00:00:00.500000000Z")


def _format_ns(value_ns: int) -> str:
    seconds, nanos = divmod(value_ns, NS)
    base = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    if nanos:
        return f"{base}.{nanos:09d}".rstrip("0") + "Z"
    return base + "Z"


def _fresh_key() -> tuple[bytes, bytes, str]:
    private_raw, public_raw = crypto.generate_keypair()
    return private_raw, public_raw, crypto.kid_for(public_raw)


def _genesis_manifest(
    *,
    key: tuple[bytes, bytes, str],
    issued_at: str,
    valid_from: str,
    valid_until: str | None = None,
) -> dict:
    """Self-signed genesis manifest for a single custom key entry."""
    keys_entry: dict = {
        "kid": key[2],
        "alg": "Ed25519",
        "public_key": crypto.b64url_encode(key[1]),
        "status": "active",
        "valid_from": valid_from,
    }
    if valid_until is not None:
        keys_entry["valid_until"] = valid_until
    body = {
        "node_id": NODE_ID,
        "display_name": "boundary node",
        "manifest_sequence": 1,
        "previous_manifest_digest": None,
        "manifest_freshness_window_seconds": 3600,
        "capabilities": ["hash_file"],
        "endpoints": [],
        "keys": [keys_entry],
        "authorization_policy": {"default_deny": True, "required_grants": []},
        "cost_class": {"tier": "free"},
        "rate_limits": {},
        "status": "active",
    }
    return build_document(
        kind=KIND_MANIFEST,
        body=body,
        issuer_node_id=NODE_ID,
        kid=key[2],
        private_raw=key[0],
        issued_at=issued_at,
        doc_id="11111111-1111-4111-8111-111111111111",
    )


def _delegation_at(
    *,
    key: tuple[bytes, bytes, str],
    issued_at: str,
    expires_at: str,
    deadline: str,
    issuer_node_id: str = NODE_ID,
) -> dict:
    body = {
        "task_id": "22222222-2222-4222-8222-222222222222",
        "attempt_id": "33333333-3333-4333-8333-333333333333",
        "issuer_node_id": issuer_node_id,
        "target_node_id": TARGET,
        "capability": "hash_file",
        "input": {"kind": "inline", "data": {"seed": "boundary"}},
        "authority": {
            "actions": ["hash_file"],
            "external_effect_scope": {"allowed_effects": ["none"]},
            "expiry": "2027-01-01T00:00:00Z",
        },
        "budget": {"max_wall_seconds": 60},
        "deadline": deadline,
        "expected_output": {
            "kind": "artifact",
            "media_type": "application/json",
            "required_artifacts": ["result.json"],
            "expects_repository_mutation": False,
        },
        "expires_at": expires_at,
    }
    return build_document(
        kind=KIND_DELEGATION,
        body=body,
        issuer_node_id=issuer_node_id,
        kid=key[2],
        private_raw=key[0],
        issued_at=issued_at,
    )


def _verify_at(doc_bytes: bytes, *, kind: str, chain, local_id=None, at=None, pending=None, policy=None):
    return verify(
        doc_bytes,
        expected_kind=kind,
        local_node_id=local_id,
        trust_context=chain,
        local_policy=policy or VerificationPolicy(),
        now=at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        pending_store=pending,
    )


class TestNanosecondTimestampParsing:
    """parse_timestamp_ns is exact to the nanosecond, with integer arithmetic."""

    def test_digits_7_to_9_distinguished(self):
        # Fails against the pre-change implementation: parse_timestamp
        # truncates to six digits, so these compared equal.
        assert parse_timestamp_ns("2026-01-01T00:00:00.500000000Z") < parse_timestamp_ns(
            "2026-01-01T00:00:00.500000001Z"
        )

    def test_equivalent_fractions_compare_equal(self):
        assert parse_timestamp_ns("2026-01-01T00:00:00.5Z") == BASE_NS
        assert parse_timestamp_ns("2026-01-01T00:00:00.50Z") == BASE_NS
        assert parse_timestamp_ns("2026-01-01T00:00:00.500000000Z") == BASE_NS

    def test_whole_second_and_mixed_digits(self):
        assert parse_timestamp_ns("2026-01-01T00:00:00Z") == BASE_NS - 500_000_000
        assert parse_timestamp_ns("2026-01-01T00:00:00.000000001Z") == parse_timestamp_ns("2026-01-01T00:00:00Z") + 1

    def test_valid_calendar_instants_required(self):
        for bad in (
            "2026-13-01T00:00:00Z",      # month 13 matches the pattern
            "2026-02-30T00:00:00Z",      # Feb 30 matches the pattern
            "2026-01-01T25:00:00Z",      # hour 25 matches the pattern
            "2026-01-01T00:00:00+01:00",  # non-Z offset
            "2026-01-01T00:00:00.1234567890Z",  # ten fractional digits
            "0000-01-01T00:00:00Z",      # year 0000 is not RFC 3339
            "not-a-timestamp",
        ):
            with pytest.raises(ValueError):
                parse_timestamp_ns(bad)

    def test_nine_digit_fraction_accepted(self):
        assert parse_timestamp_ns("2026-01-01T00:00:00.123456789Z") == parse_timestamp_ns("2026-01-01T00:00:00Z") + 123_456_789

    def test_datetime_to_ns_is_lossless(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, 500000, tzinfo=timezone.utc)
        assert datetime_to_ns(dt) == BASE_NS


def _dt_from_ns(value_ns: int) -> datetime:
    seconds, nanos = divmod(value_ns, NS)
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds, microseconds=nanos // 1000)


class TestDelegationNanosecondBoundary:
    """A one-nanosecond separation is honored by admission checks."""

    def _ordering(self, *, issued_ns: int, expires_ns: int, deadline_ns: int):
        issuer, executor = make_node_pair()
        # Anchor to the issuer's real genesis instant so the chain applies.
        genesis_ns = parse_timestamp_ns(issuer.head_manifest["issued_at"])
        delegation = _delegation_at(
            key=(issuer.active_key.private_raw, issuer.active_key.public_raw, issuer.active_key.kid),
            issued_at=_format_ns(genesis_ns + issued_ns),
            expires_at=_format_ns(genesis_ns + expires_ns),
            deadline=_format_ns(genesis_ns + deadline_ns),
            issuer_node_id=issuer.node_id,
        )
        return _verify_at(
            canonical.canonical_bytes(delegation),
            kind=KIND_DELEGATION,
            chain=trust_for(issuer),
            local_id=TARGET,
            at=_dt_from_ns(genesis_ns + issued_ns),
        )

    def test_one_nanosecond_ordering_valid(self):
        result = self._ordering(issued_ns=1_000_000_000, expires_ns=1_000_000_001, deadline_ns=1_000_000_002)
        assert result.ok, result.reason

    def test_reversed_nanosecond_ordering_rejected(self):
        result = self._ordering(issued_ns=1_000_000_001, expires_ns=1_000_000_000, deadline_ns=1_000_000_002)
        assert not result.ok
        assert result.reason_code == "temporal.invalid"


class TestReceiptNanosecondOrdering:
    def test_finished_one_nanosecond_after_started_accepted(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        pending = PendingDelegationStore(tmp_path / "pending")
        pending.register_outstanding(delegation, content_digest_of(delegation))
        started = _format_ns(BASE_NS)
        finished = _format_ns(BASE_NS + 1)
        receipt = _receipt_at(executor, delegation, started_at=started, finished_at=finished)
        result = _verify_at(
            canonical.canonical_bytes(receipt),
            kind=KIND_RECEIPT,
            chain=trust_for(executor),
            pending=pending,
        )
        assert result.ok, result.reason

    def test_finished_before_started_rejected(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        started = _format_ns(BASE_NS + 1)
        finished = _format_ns(BASE_NS)
        receipt = _receipt_at(executor, delegation, started_at=started, finished_at=finished)
        result = _verify_at(
            canonical.canonical_bytes(receipt),
            kind=KIND_RECEIPT,
            chain=trust_for(executor),
        )
        assert not result.ok
        assert result.reason_code == "temporal.invalid"


def _receipt_at(executor, delegation, *, started_at: str, finished_at: str) -> dict:
    body = {
        "receipt_id": "44444444-4444-4444-8444-444444444444",
        "task_id": delegation["body"]["task_id"],
        "attempt_id": delegation["body"]["attempt_id"],
        "delegation_digest": content_digest_of(delegation),
        "executor_node_id": executor.node_id,
        "status": "succeeded",
        "started_at": started_at,
        "finished_at": finished_at,
        "artifacts": [
            {
                "name": "result.json",
                "media_type": "application/json",
                "digest": "sha256:" + "ab" * 32,
                "size": 1,
                "location": "mem://result.json",
            }
        ],
        "usage": {"wall_seconds": 1, "output_bytes": 1},
    }
    return build_document(
        kind=KIND_RECEIPT,
        body=body,
        issuer_node_id=executor.node_id,
        kid=executor.active_key.kid,
        private_raw=executor.active_key.private_raw,
    )


class TestKeyValidityBoundary:
    """Half-open validity interval: valid_from <= issued_at < valid_until."""

    def _context(self, valid_from: str, valid_until: str | None) -> tuple[tuple, PinnedManifestTrustContext]:
        key = _fresh_key()
        genesis = _genesis_manifest(
            key=key,
            issued_at="2026-01-01T00:00:00Z",
            valid_from=valid_from,
            valid_until=valid_until,
        )
        return key, PinnedManifestTrustContext.from_chain([genesis])

    def _verify_delegation(self, key, context, issued_at: str, executor_node_id: str) -> object:
        delegation = _delegation_at(
            key=key,
            issued_at=issued_at,
            expires_at=_format_ns(parse_timestamp_ns(issued_at) + 60 * NS),
            deadline=_format_ns(parse_timestamp_ns(issued_at) + 120 * NS),
        )
        return _verify_at(
            canonical.canonical_bytes(delegation),
            kind=KIND_DELEGATION,
            chain=context,
            local_id=executor_node_id,
            at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def test_issued_at_equal_valid_from_eligible(self):
        key, context = self._context("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
        result = self._verify_delegation(key, context, "2026-01-01T00:00:00Z", TARGET)
        assert result.ok, result.reason

    def test_issued_at_equal_valid_until_not_valid(self):
        key, context = self._context("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
        result = self._verify_delegation(key, context, "2026-01-02T00:00:00Z", TARGET)
        assert not result.ok
        assert result.reason_code == "trust.key_not_valid"

    def test_one_nanosecond_before_valid_until_eligible(self):
        key, context = self._context("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
        before = _format_ns(parse_timestamp_ns("2026-01-02T00:00:00Z") - 1)
        result = self._verify_delegation(key, context, before, TARGET)
        assert result.ok, result.reason

    def test_known_key_outside_interval_is_key_not_valid_not_unknown(self):
        key, context = self._context("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
        result = self._verify_delegation(key, context, "2026-01-03T00:00:00Z", TARGET)
        assert not result.ok
        assert result.reason_code == "trust.key_not_valid"
        assert "not valid" in result.reason or "valid only" in result.reason


class TestKeyClassification:
    """resolve_key_detailed distinguishes unknown from known-but-invalid."""

    def test_unknown_kid(self):
        issuer, executor = make_node_pair()
        foreign = _fresh_key()
        delegation = _delegation_at(
            key=foreign,
            issued_at=_format_ns(BASE_NS),
            expires_at=_format_ns(BASE_NS + 60 * NS),
            deadline=_format_ns(BASE_NS + 120 * NS),
            issuer_node_id=issuer.node_id,
        )
        result = _verify_at(
            canonical.canonical_bytes(delegation),
            kind=KIND_DELEGATION,
            chain=trust_for(issuer),
            local_id=TARGET,
        )
        assert not result.ok
        assert result.reason_code == "trust.unknown_key"

    def test_detailed_resolution_codes(self):
        key = _fresh_key()
        genesis = _genesis_manifest(
            key=key, issued_at="2026-01-01T00:00:00Z",
            valid_from="2026-01-01T00:00:00Z", valid_until="2026-01-02T00:00:00Z",
        )
        chain = [genesis]
        at_valid = parse_timestamp_ns("2026-01-01T06:00:00Z")
        assert resolve_key_detailed(chain, NODE_ID, key[2], at_valid).ok
        assert resolve_key_detailed(chain, NODE_ID, key[2], parse_timestamp_ns("2026-01-03T00:00:00Z")).code == "trust.key_not_valid"
        assert resolve_key_detailed(chain, NODE_ID, "sha256:" + "00" * 32, at_valid).code == "trust.unknown_key"


class TestManifestSigningContextNanoseconds:
    """Successor-manifest signing context keeps fractional digits 7–9.

    Fails pre-change: the old key's valid_until truncates to six digits and
    becomes <= the successor's issued_at, breaking the chain.
    """

    def test_key_valid_until_one_nanosecond_after_successor_issued_at(self):
        key1 = _fresh_key()
        key2 = _fresh_key()
        genesis_at = "2026-01-01T00:00:00.500000000Z"
        successor_at = "2026-01-01T00:00:00.500000000Z"
        valid_until = "2026-01-01T00:00:00.500000001Z"  # K1 valid 1 ns past successor issue

        genesis = _genesis_manifest(
            key=key1, issued_at=genesis_at,
            valid_from="2026-01-01T00:00:00.500000000Z",
            valid_until=valid_until,
        )
        successor_body = {
            "node_id": NODE_ID,
            "display_name": "boundary node",
            "manifest_sequence": 2,
            "previous_manifest_digest": content_digest_of(genesis),
            "manifest_freshness_window_seconds": 3600,
            "capabilities": ["hash_file"],
            "endpoints": [],
            "keys": [
                {
                    "kid": key1[2],
                    "alg": "Ed25519",
                    "public_key": crypto.b64url_encode(key1[1]),
                    "status": "retired",
                    "valid_from": "2026-01-01T00:00:00.500000000Z",
                    "valid_until": valid_until,
                },
                {
                    "kid": key2[2],
                    "alg": "Ed25519",
                    "public_key": crypto.b64url_encode(key2[1]),
                    "status": "active",
                    "valid_from": successor_at,
                },
            ],
            "authorization_policy": {"default_deny": True, "required_grants": []},
            "cost_class": {"tier": "free"},
            "rate_limits": {},
            "status": "active",
        }
        successor = build_document(
            kind=KIND_MANIFEST,
            body=successor_body,
            issuer_node_id=NODE_ID,
            kid=key1[2],
            private_raw=key1[0],
            issued_at=successor_at,
            doc_id="55555555-5555-4555-8555-555555555555",
        )
        context = PinnedManifestTrustContext.from_chain([genesis, successor])

        delegation = _delegation_at(
            key=key2,
            issued_at="2026-01-01T00:00:01Z",
            expires_at="2026-01-01T00:01:01Z",
            deadline="2026-01-01T00:02:01Z",
        )
        result = _verify_at(
            canonical.canonical_bytes(delegation),
            kind=KIND_DELEGATION,
            chain=context,
            local_id=TARGET,
            at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert result.ok, result.reason


class TestStrictParseTypedFailure:
    """Typed, machine-distinguishable parse failures; never message matching."""

    @pytest.mark.parametrize(
        ("payload", "exc_type", "reason_code"),
        [
            (b"not json", canonical.InvalidJsonError, "parse.invalid_json"),
            (b'{"a":1,"a":2}', canonical.DuplicateMemberError, "parse.duplicate_member"),
            (b'{"a":"\xff\xfe"}', canonical.InvalidUnicodeError, "parse.invalid_unicode"),
            (b'{"a":"\\ud800"}', canonical.InvalidUnicodeError, "parse.invalid_unicode"),
            (b'{"a":9007199254740992}', canonical.UnsupportedNumberError, "canonicalization.number_out_of_domain"),
            (b'{"a":-9007199254740992}', canonical.UnsupportedNumberError, "canonicalization.number_out_of_domain"),
            (b'{"a":NaN}', canonical.UnsupportedNumberError, "canonicalization.number_out_of_domain"),
            (b'{"a":Infinity}', canonical.UnsupportedNumberError, "canonicalization.number_out_of_domain"),
            (b'{"a":-Infinity}', canonical.UnsupportedNumberError, "canonicalization.number_out_of_domain"),
            (b'{"a":-0}', canonical.UnsupportedNumberError, "canonicalization.number_out_of_domain"),
        ],
        ids=[
            "malformed-json", "duplicate-member", "invalid-utf8", "lone-surrogate",
            "plus-2p53", "minus-2p53", "nan", "infinity", "-infinity", "negative-zero",
        ],
    )
    def test_parse_class_and_verifier_category(self, payload, exc_type, reason_code):
        with pytest.raises(exc_type):
            canonical.parse_strict(payload)
        issuer, executor = make_node_pair()
        result = _verify_at(
            payload, kind=KIND_DELEGATION, chain=trust_for(issuer), local_id=executor.node_id
        )
        assert not result.ok
        assert result.reason_code == reason_code

    def test_safe_integer_boundaries_accepted(self):
        obj = canonical.parse_strict(b'{"max":9007199254740991,"min":-9007199254740991}')
        assert obj["max"] == 9007199254740991
        assert obj["min"] == -9007199254740991

    def test_out_of_domain_rejected_at_parse(self):
        for payload in (b'{"a":9007199254740992}', b'{"a":-9007199254740992}'):
            with pytest.raises(canonical.UnsupportedNumberError):
                canonical.parse_strict(payload)


class TestExistingVerificationCategories:
    """The profile categories surface for the shared conformance failures."""

    def test_kind_mismatch(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        result = _verify_at(
            canonical.canonical_bytes(delegation),
            kind=KIND_RECEIPT,
            chain=trust_for(issuer),
            local_id=executor.node_id,
        )
        assert result.reason_code == "document.kind_mismatch"

    def test_audience_mismatch(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id="urn:faw:wrong-node-0001")
        result = _verify_at(
            canonical.canonical_bytes(delegation),
            kind=KIND_DELEGATION,
            chain=trust_for(issuer),
            local_id=executor.node_id,
        )
        assert result.reason_code == "audience.mismatch"

    def test_trust_invalid_chain(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        chain = copy.deepcopy(issuer.manifests)
        chain[0]["body"]["manifest_sequence"] = 99
        result = _verify_at(
            canonical.canonical_bytes(delegation),
            kind=KIND_DELEGATION,
            chain=PinnedManifestTrustContext.from_chain(chain),
            local_id=executor.node_id,
        )
        assert result.reason_code == "trust.invalid_chain"

    def test_signature_invalid(self):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        raw = crypto.b64url_decode(delegation["signature"]["value"])
        delegation["signature"]["value"] = crypto.b64url_encode(bytes([raw[0] ^ 0x01]) + raw[1:])
        result = _verify_at(
            canonical.canonical_bytes(delegation),
            kind=KIND_DELEGATION,
            chain=trust_for(issuer),
            local_id=executor.node_id,
        )
        assert result.reason_code == "signature.invalid"

    def test_binding_mismatch(self, tmp_path):
        issuer, executor = make_node_pair()
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        pending = PendingDelegationStore(tmp_path / "pending")
        pending.register_outstanding(delegation, content_digest_of(delegation))
        receipt = build_receipt(executor, delegation, delegation_digest="sha256:" + "aa" * 32)
        result = _verify_at(
            canonical.canonical_bytes(receipt),
            kind=KIND_RECEIPT,
            chain=trust_for(executor),
            pending=pending,
        )
        assert result.reason_code == "binding.mismatch"

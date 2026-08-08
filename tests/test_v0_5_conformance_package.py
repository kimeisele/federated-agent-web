"""Package-validation test for the language-neutral conformance package.

Validates ``conformance/v0.2/`` entirely from ``manifest.json`` and the raw
fixture bytes: byte identity, matrix completeness, category mapping, and the
accept/reject contract, executed through the public verification interface.
The package itself does not depend on Python sources or tests.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from federated_agent_web.documents import parse_timestamp_ns
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.verify import PinnedManifestTrustContext, VerificationPolicy, verify

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "conformance" / "v0.2"
NS = 1_000_000_000

# Exact profile matrix: expected category per negative, null for accept.
EXPECTED_MATRIX: dict[str, str | None] = {
    "N01": "parse.duplicate_member",
    "N02": "parse.invalid_json",
    "N03": "parse.invalid_unicode",
    "N04": "parse.invalid_unicode",
    "N05": "canonicalization.number_out_of_domain",
    "N06": "canonicalization.number_out_of_domain",
    "N07": "schema.invalid",
    "N08": "document.kind_mismatch",
    "N09": "audience.mismatch",
    "N10": "temporal.invalid",
    "N11": "trust.key_not_valid",
    "N12": "trust.unknown_key",
    "N13": "trust.invalid_chain",
    "N14": "signature.invalid",
    "N15": "binding.mismatch",
    "P01": None,
    "P02": None,
    "P03": None,
    "P04": None,
    "P05": None,
}


def _load_manifest() -> dict:
    return json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))


def _dt_from_ns(value_ns: int) -> datetime:
    seconds, nanos = divmod(value_ns, NS)
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds, microseconds=nanos // 1000)


def _verify_fixture(rec: dict) -> object:
    data = (PACKAGE / rec["bytes"]).read_bytes()
    chain = [json.loads((PACKAGE / p).read_text(encoding="utf-8")) for p in rec["trust_chain"]]
    context = PinnedManifestTrustContext.from_chain(chain, pinned_at=_dt_from_ns(parse_timestamp_ns(rec["now"])))
    pending = None
    if rec.get("pending"):
        delegation = json.loads((PACKAGE / rec["pending"]["delegation_source"]).read_text(encoding="utf-8"))
        store = PendingDelegationStore(pathlib.Path(tempfile.mkdtemp(prefix="faw-pkg-")))
        store.register_outstanding(delegation, rec["pending"]["delegation_digest"])
        pending = store
    return verify(
        data,
        expected_kind=rec["expected_kind"],
        local_node_id=rec.get("local_node_id"),
        trust_context=context,
        local_policy=VerificationPolicy(
            clock_skew_seconds=rec["local_policy"]["clock_skew_seconds"],
            reject_stale=rec["local_policy"]["reject_stale"],
        ),
        now=_dt_from_ns(parse_timestamp_ns(rec["now"])),
        pending_store=pending,
    )


class TestPackageByteIdentity:
    def test_files_map_covers_every_package_file(self):
        manifest = _load_manifest()
        actual = {
            str(p.relative_to(PACKAGE)).replace("\\", "/")
            for p in PACKAGE.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        }
        assert set(manifest["files"]) == actual

    def test_hashes_and_sizes_match_raw_bytes(self):
        manifest = _load_manifest()
        for rel, meta in manifest["files"].items():
            data = (PACKAGE / rel).read_bytes()
            assert hashlib.sha256(data).hexdigest() == meta["sha256"], rel
            assert len(data) == meta["size_bytes"], rel

    def test_package_has_no_python_dependency(self):
        manifest = _load_manifest()
        for rec in manifest["fixtures"]:
            refs = [rec["bytes"], *rec["trust_chain"]]
            if rec.get("pending"):
                refs.append(rec["pending"]["delegation_source"])
            for ref in refs:
                assert str(pathlib.PurePosixPath(ref)).startswith(("context/", "positive/", "negative/", "source/")), ref
        for rel, data in ((rel, (PACKAGE / rel).read_bytes()) for rel in manifest["files"]):
            lowered = data.lower()
            assert b"import " not in lowered
            assert b"tests/" not in lowered
            assert b"src/" not in lowered


class TestMatrixCompleteness:
    def test_all_profile_cases_present(self):
        manifest = _load_manifest()
        fixture_ids = [rec["id"] for rec in manifest["fixtures"]]
        for case_id in EXPECTED_MATRIX:
            assert case_id in fixture_ids, case_id

    def test_categories_match_profile_matrix(self):
        manifest = _load_manifest()
        by_id = {rec["id"]: rec for rec in manifest["fixtures"]}
        for case_id, category in EXPECTED_MATRIX.items():
            rec = by_id[case_id]
            assert rec["expect"] == ("accept" if category is None else "reject"), case_id
            assert rec["expected_category"] == category, case_id

    def test_support_fixtures_expected_accepted(self):
        manifest = _load_manifest()
        by_id = {rec["id"]: rec for rec in manifest["fixtures"]}
        for support_id in ("delegation-source", "receipt-source", "manifest-source"):
            assert by_id[support_id]["expect"] == "accept", support_id


class TestFixtureContract:
    @pytest.mark.parametrize("case_id", list(EXPECTED_MATRIX) + ["delegation-source", "receipt-source", "manifest-source"])
    def test_verification_matches_contract(self, case_id):
        manifest = _load_manifest()
        rec = next(r for r in manifest["fixtures"] if r["id"] == case_id)
        result = _verify_fixture(rec)
        if rec["expect"] == "accept":
            assert result.ok, f"{case_id}: {result.reason_code} {result.reason}"
            assert result.reason_code is None
        else:
            assert not result.ok, f"{case_id}: expected reject, got ok"
            assert result.reason_code == rec["expected_category"], (
                f"{case_id}: expected {rec['expected_category']}, got {result.reason_code}"
            )

    def test_mutation_fixtures_carry_source_and_mutation(self):
        manifest = _load_manifest()
        for rec in manifest["fixtures"]:
            if rec["expect"] == "reject":
                assert rec["source"], rec["id"]
                assert rec["mutation"], rec["id"]


class TestRecordShape:
    def test_records_are_language_neutral_and_deterministic(self):
        manifest = _load_manifest()
        assert manifest["faw_spec_version"] == "0.2"
        assert manifest["defaults"]["now"]
        for rec in manifest["fixtures"]:
            for key in ("id", "expect", "expected_kind", "expected_category", "bytes", "sha256", "size_bytes",
                        "now", "trust_chain", "local_policy", "source", "mutation"):
                assert key in rec, f"{rec['id']} missing {key}"
            assert isinstance(rec["trust_chain"], list) and rec["trust_chain"], rec["id"]
            assert rec["local_policy"]["clock_skew_seconds"] >= 0, rec["id"]
            if rec.get("pending"):
                for key in ("task_id", "attempt_id", "delegation_digest", "executor_node_id", "status"):
                    assert key in rec["pending"], f"{rec['id']} pending missing {key}"

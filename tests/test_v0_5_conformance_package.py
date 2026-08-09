"""Package-validation test for the language-neutral conformance package.

Validates ``conformance/v0.2/`` entirely from ``manifest.json`` and the raw
fixture bytes: byte identity, matrix completeness, category mapping, the
accept/reject contract, explicit trust freshness (``pinned_at``), explicit
language-neutral local policy (no implicit Python defaults), and tightened
mutation provenance (every reject ``source`` is a recorded, verified-accept
fixture). Executed through the public verification interface; the package
itself does not depend on Python sources or tests.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from federated_agent_web.documents import content_digest_of, parse_timestamp_ns
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

SUPPORT_IDS = ("delegation-source", "receipt-source", "manifest-source", "rotation-successor-source")

# Every field the record must encode so the consumer builds policy with no
# implicit defaults.
POLICY_FIELDS = (
    "clock_skew_seconds",
    "reject_stale",
    "can_enforce_tokens",
    "can_enforce_cost",
    "allowed_external_effects",
    "allowed_actions",
    "capability_targets",
    "max_wall_seconds_cap",
    "max_output_bytes_cap",
)


def _load_manifest() -> dict:
    return json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))


def _dt_from_ns(value_ns: int) -> datetime:
    seconds, nanos = divmod(value_ns, NS)
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds, microseconds=nanos // 1000)


def _policy_from_record(rec: dict) -> VerificationPolicy:
    """Construct policy entirely from manifest data; no implicit defaults."""
    policy = rec["local_policy"]
    missing = [f for f in POLICY_FIELDS if f not in policy]
    assert not missing, f"{rec['id']} local_policy missing {missing}"
    return VerificationPolicy(
        clock_skew_seconds=int(policy["clock_skew_seconds"]),
        reject_stale=bool(policy["reject_stale"]),
        can_enforce_tokens=bool(policy["can_enforce_tokens"]),
        can_enforce_cost=bool(policy["can_enforce_cost"]),
        allowed_external_effects=frozenset(policy["allowed_external_effects"]),
        allowed_actions=set(policy["allowed_actions"]) if policy["allowed_actions"] is not None else None,
        capability_targets=dict(policy["capability_targets"]),
        max_wall_seconds_cap=policy["max_wall_seconds_cap"],
        max_output_bytes_cap=policy["max_output_bytes_cap"],
    )


def _verify_fixture(rec: dict) -> object:
    data = (PACKAGE / rec["bytes"]).read_bytes()
    chain = [json.loads((PACKAGE / p).read_text(encoding="utf-8")) for p in rec["trust_chain"]]
    context = PinnedManifestTrustContext.from_chain(chain, pinned_at=_dt_from_ns(parse_timestamp_ns(rec["pinned_at"])))
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
        local_policy=_policy_from_record(rec),
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
            if rec.get("source"):
                refs.append(rec["source"])
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
        for support_id in SUPPORT_IDS:
            assert by_id[support_id]["expect"] == "accept", support_id


class TestTrustFreshness:
    def test_pinned_at_explicit_per_fixture(self):
        manifest = _load_manifest()
        for rec in manifest["fixtures"]:
            assert "pinned_at" in rec, rec["id"]
            parse_timestamp_ns(rec["pinned_at"])  # must parse as a FAW timestamp

    def test_head_sequence_and_digest_derived_from_chain(self):
        """Head = last trust_chain manifest; sequence/digest per the contract."""
        manifest = _load_manifest()
        for rec in manifest["fixtures"]:
            chain = [json.loads((PACKAGE / p).read_text(encoding="utf-8")) for p in rec["trust_chain"]]
            head = chain[-1]
            context = PinnedManifestTrustContext.from_chain(
                chain, pinned_at=_dt_from_ns(parse_timestamp_ns(rec["pinned_at"]))
            )
            assert context.head_sequence == int(head["body"]["manifest_sequence"]), rec["id"]
            assert context.head_digest == content_digest_of(head), rec["id"]

    def test_all_fixtures_fresh_under_explicit_pinned_at(self):
        """Recorded fixtures are strictly fresh under the >= rule."""
        manifest = _load_manifest()
        for rec in manifest["fixtures"]:
            chain = [json.loads((PACKAGE / p).read_text(encoding="utf-8")) for p in rec["trust_chain"]]
            head = chain[-1]
            window_ns = int(head["body"]["manifest_freshness_window_seconds"]) * NS
            pinned = parse_timestamp_ns(rec["pinned_at"])
            now = parse_timestamp_ns(rec["now"])
            assert pinned + window_ns >= now, rec["id"]

    def test_freshness_contract_declares_equality_boundary(self):
        """The machine-readable contract pins the >= rule explicitly."""
        manifest = _load_manifest()
        freshness = manifest["defaults"]["trust_chain"]["freshness"]
        assert ">=" in freshness["fresh"]
        assert "< now" in freshness["stale"]
        assert "equality_boundary" in freshness
        assert "fresh" in freshness["equality_boundary"]

    def test_equality_boundary_classifies_fresh(self):
        """pinned_at + window == now is fresh; one ns below is stale.

        Explicit regression so a second-language implementation cannot read
        the boundary differently. All values derive from manifest-recorded
        data: the fixture's recorded ``now`` and the head manifest's
        ``manifest_freshness_window_seconds``. The only deviation is the
        explicitly constructed pinned_at needed to hit the boundary.
        """
        manifest = _load_manifest()
        rec = next(r for r in manifest["fixtures"] if r["id"] == "delegation-source")
        chain = [json.loads((PACKAGE / p).read_text(encoding="utf-8")) for p in rec["trust_chain"]]
        window_ns = int(chain[-1]["body"]["manifest_freshness_window_seconds"]) * NS
        now_ns = parse_timestamp_ns(rec["now"])
        data = (PACKAGE / rec["bytes"]).read_bytes()

        def run(pinned_ns: int) -> object:
            context = PinnedManifestTrustContext.from_chain(chain, pinned_at=_dt_from_ns(pinned_ns))
            return verify(
                data,
                expected_kind=rec["expected_kind"],
                local_node_id=rec.get("local_node_id"),
                trust_context=context,
                local_policy=_policy_from_record(rec),
                now=_dt_from_ns(now_ns),
                pending_store=None,
            )

        at_equality = run(now_ns - window_ns)      # pinned_at + window == now
        assert at_equality.ok, at_equality.reason
        assert at_equality.freshness == "fresh"
        assert at_equality.reason_code is None

        below_equality = run(now_ns - window_ns - 1)  # pinned_at + window < now
        assert below_equality.freshness == "stale"
        assert below_equality.ok  # qualified result under the recorded policy

        # Under a rejecting stale policy the boundary-below case is a
        # trust.stale rejection; the equality case stays fresh.
        rejecting_policy = _policy_from_record(rec)
        rejecting_policy.reject_stale = True
        context = PinnedManifestTrustContext.from_chain(chain, pinned_at=_dt_from_ns(now_ns - window_ns - 1))
        rejected = verify(
            data,
            expected_kind=rec["expected_kind"],
            local_node_id=rec.get("local_node_id"),
            trust_context=context,
            local_policy=rejecting_policy,
            now=_dt_from_ns(now_ns),
            pending_store=None,
        )
        assert rejected.freshness == "stale"
        assert not rejected.ok
        assert rejected.reason_code == "trust.stale"


class TestFixtureContract:
    @pytest.mark.parametrize("case_id", list(EXPECTED_MATRIX) + list(SUPPORT_IDS))
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

    def test_reject_sources_resolve_to_accepted_fixtures(self):
        """Every reject source is a recorded fixture that itself verifies accept."""
        manifest = _load_manifest()
        by_bytes = {rec["bytes"]: rec for rec in manifest["fixtures"]}
        verified_accept: set[str] = set()
        for rec in manifest["fixtures"]:
            if rec["expect"] == "accept":
                assert _verify_fixture(rec).ok, rec["id"]
                verified_accept.add(rec["id"])
        for rec in manifest["fixtures"]:
            if rec["expect"] != "reject":
                continue
            assert rec["source"], rec["id"]
            source_rec = by_bytes.get(rec["source"])
            assert source_rec is not None, f"{rec['id']} source {rec['source']} is not a recorded fixture"
            assert source_rec["expect"] == "accept", f"{rec['id']} source {rec['source']} not expected accept"
            assert source_rec["id"] in verified_accept, f"{rec['id']} source {rec['source']} did not verify accept"


class TestRecordShape:
    def test_records_are_language_neutral_and_deterministic(self):
        manifest = _load_manifest()
        assert manifest["faw_spec_version"] == "0.2"
        assert manifest["defaults"]["now"]
        assert manifest["defaults"]["trust_chain"]["freshness"]
        for rec in manifest["fixtures"]:
            for key in ("id", "expect", "expected_kind", "expected_category", "bytes", "sha256", "size_bytes",
                        "now", "pinned_at", "trust_chain", "local_policy", "source", "mutation"):
                assert key in rec, f"{rec['id']} missing {key}"
            assert isinstance(rec["trust_chain"], list) and rec["trust_chain"], rec["id"]
            missing = [f for f in POLICY_FIELDS if f not in rec["local_policy"]]
            assert not missing, f"{rec['id']} local_policy missing {missing}"
            if rec.get("pending"):
                for key in ("task_id", "attempt_id", "delegation_digest", "executor_node_id", "status"):
                    assert key in rec["pending"], f"{rec['id']} pending missing {key}"

    def test_verification_input_mutations_are_exact(self):
        manifest = _load_manifest()
        by_id = {rec["id"]: rec for rec in manifest["fixtures"]}
        n08 = by_id["N08"]
        assert "expected_kind" in n08["mutation"] and "faw-receipt" in n08["mutation"]
        assert n08["source"] == "source/delegation-source.json"
        n09 = by_id["N09"]
        assert "local_node_id" in n09["mutation"] and "urn:faw:conformance-other-node-0001" in n09["mutation"]
        assert n09["source"] == "source/delegation-source.json"

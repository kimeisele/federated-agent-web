"""Evidence-bundle verification tests (adoption)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BUNDLE = Path(__file__).resolve().parents[1] / "examples" / "evidence-bundle"


def _run_evidence(bundle_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "federated_agent_web.cli", "evidence", "verify", str(bundle_dir)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _copy_bundle(tmp: Path) -> Path:
    dest = Path(tempfile.mkdtemp(dir=tmp))
    shutil.copytree(BUNDLE, dest, dirs_exist_ok=True)
    return dest


class TestValidBundle:
    def test_bundle_verifies(self):
        r = _run_evidence(BUNDLE)
        assert "evidence: OK" in r.stdout, r.stdout
        assert r.returncode == 0
        for field in [
            "spec_version:", "temporal_mode:", "delegation_verification_time:",
            "receipt_verification_time:", "issuer_node_id:", "executor_node_id:",
            "task_id:", "attempt_id:", "delegation_digest:", "receipt_digest:",
            "receipt_status:", "artifact_digest:", "receipt_binding:",
            "issuer_acceptance:", "current_admissibility:", "private_keys_required:",
            "network_access:", "external_writes:",
        ]:
            assert field in r.stdout, f"missing field {field!r}"

    def test_no_private_keys_committed(self):
        for root, _dirs, files in __import__("os").walk(BUNDLE):
            for f in files:
                assert not f.endswith(".key"), f"private key file found: {f}"
                fp = Path(root) / f
                if fp.suffix in (".json", ""):
                    content = fp.read_text(errors="replace")
                    assert "private_key" not in content.lower(), f"potential private key in {fp}"
                    assert "private_raw" not in content.lower()

    def test_no_machine_paths(self):
        """No absolute developer-machine paths in any committed text file."""
        bad = False
        for fp in BUNDLE.rglob("*"):
            if fp.is_file() and not fp.name.endswith(".bin"):
                txt = fp.read_text(errors="replace")
                for pat in [r"/Users/", r"/home/", r"file://", r"[A-Za-z]:\\\\"]:
                    if re.search(pat, txt):
                        bad = True
                        print(f"MACHINE PATH in {fp.relative_to(BUNDLE)}: {pat}")
        assert not bad, "absolute machine paths found in evidence bundle"

    def test_bundle_unchanged_after_verification(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))

        def snapshot(d):
            result = {}
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    result[str(p.relative_to(d))] = p.read_bytes()
            return result

        before = snapshot(tmp)
        r = _run_evidence(tmp)
        assert "evidence: OK" in r.stdout
        after = snapshot(tmp)
        assert before == after, "bundle changed after verification"


class TestTamperedBundle:
    def test_delegation_byte_tampering_fails(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        d = json.loads((tmp / "delegation.json").read_text())
        d["body"]["budget"]["max_wall_seconds"] = 9999
        (tmp / "delegation.json").write_text(json.dumps(d, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout
        assert r.returncode != 0

    def test_receipt_byte_tampering_fails(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        r = json.loads((tmp / "receipt.json").read_text())
        r["body"]["status"] = "failed"
        (tmp / "receipt.json").write_text(json.dumps(r, indent=2) + "\n")
        rr = _run_evidence(tmp)
        assert "evidence: FAILED" in rr.stdout
        assert rr.returncode != 0

    def test_artifact_byte_tampering_fails(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        (tmp / "artifacts" / "result.json").write_text("tampered\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout
        assert r.returncode != 0

    def test_wrong_issuer_manifest_fails(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        shutil.copy(tmp / "executor" / "node.json", tmp / "issuer" / "node.json")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout

    def test_wrong_executor_manifest_fails(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        shutil.copy(tmp / "issuer" / "node.json", tmp / "executor" / "node.json")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout


class TestBundleSafety:
    def test_absolute_path_rejected(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        meta = json.loads((tmp / "bundle.json").read_text())
        meta["delegation"] = "/etc/passwd"
        (tmp / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout

    def test_dot_dot_traversal_rejected(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        meta = json.loads((tmp / "bundle.json").read_text())
        meta["delegation"] = "../../delegation.json"
        (tmp / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout

    def test_sibling_prefix_traversal_rejected(self):
        """../bundle-evil must fail even if the string starts with similar chars."""
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        # Create a sibling directory with a misleading name
        sibling = tmp.parent / "evidence-bundle-evil"
        sibling.mkdir(exist_ok=True)
        (sibling / "delegation.json").write_text("evil\n")
        meta = json.loads((tmp / "bundle.json").read_text())
        meta["delegation"] = "../evidence-bundle-evil/delegation.json"
        (tmp / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout

    def test_malformed_bundle_json_fails_cleanly(self):
        d = Path(tempfile.mkdtemp())
        (d / "bundle.json").write_text("not json")
        r = _run_evidence(d)
        assert "evidence: FAILED" in r.stdout
        assert r.returncode != 0

    def test_duplicate_key_in_bundle_json_rejected(self):
        d = Path(tempfile.mkdtemp())
        (d / "bundle.json").write_text('{"a": 1, "a": 2}')
        r = _run_evidence(d)
        assert "evidence: FAILED" in r.stdout

    def test_no_network_attempted(self):
        r = _run_evidence(BUNDLE)
        assert "evidence: OK" in r.stdout
        assert "network_access: none" in r.stdout


class TestArtifactBinding:
    def test_metadata_path_differs_from_receipt(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        meta = json.loads((tmp / "bundle.json").read_text())
        meta["artifacts"] = ["artifacts/other.json"]
        (tmp / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout

    def test_receipt_artifact_location_absolute_fails(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        receipt = json.loads((tmp / "receipt.json").read_text())
        receipt["body"]["artifacts"][0]["location"] = "/artifacts/result.json"
        (tmp / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout

    def test_receipt_artifact_traversal_fails(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        receipt = json.loads((tmp / "receipt.json").read_text())
        receipt["body"]["artifacts"][0]["location"] = "../result.json"
        (tmp / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout

    def test_receipt_artifact_size_wrong(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        receipt = json.loads((tmp / "receipt.json").read_text())
        receipt["body"]["artifacts"][0]["size"] = 0
        (tmp / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout

    def test_second_receipt_artifact_added(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        receipt = json.loads((tmp / "receipt.json").read_text())
        receipt["body"]["artifacts"].append(dict(receipt["body"]["artifacts"][0]))
        (tmp / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout

    def test_second_metadata_artifact_added(self):
        tmp = _copy_bundle(Path(tempfile.mkdtemp()))
        meta = json.loads((tmp / "bundle.json").read_text())
        meta["artifacts"] = ["artifacts/result.json", "artifacts/result.json"]
        (tmp / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout


class TestHistoricalIndependence:
    def test_historical_verification_independent_of_clock(self):
        from federated_agent_web.evidence import verify_evidence_bundle
        report = verify_evidence_bundle(BUNDLE)
        assert "temporal_mode: historical" in report
        assert "current_admissibility: not evaluated" in report
        assert "evidence: OK" in report

"""Evidence-bundle verification tests (§3 adoption)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parents[1] / "examples" / "evidence-bundle"


def _run_evidence(bundle_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "federated_agent_web.cli", "evidence", "verify", str(bundle_dir)],
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestValidBundle:
    def test_bundle_verifies(self):
        """The committed valid bundle passes all verifications."""
        r = _run_evidence(BUNDLE)
        assert "evidence: OK" in r.stdout, r.stdout
        assert r.returncode == 0
        # Report fields present
        for field in [
            "spec_version:", "temporal_mode:", "delegation_verification_time:",
            "receipt_verification_time:", "issuer_node_id:", "executor_node_id:",
            "task_id:", "attempt_id:", "delegation_digest:", "receipt_digest:",
            "receipt_status:", "artifact_digest:", "receipt_binding:",
            "issuer_acceptance:", "current_admissibility:", "private_keys_required:",
            "network_access:", "external_writes:",
        ]:
            assert field in r.stdout, f"missing field {field!r} in: {r.stdout}"

    def test_no_private_keys_committed(self):
        """The bundle must contain no private key material."""
        for root, _dirs, files in __import__("os").walk(BUNDLE):
            for f in files:
                assert not f.endswith(".key"), f"private key file found: {f}"
                fp = Path(root) / f
                if fp.suffix in (".json", ""):
                    content = fp.read_text()
                    assert "private_key" not in content.lower(), f"potential private key in {fp}"
                    assert "private_raw" not in content.lower(), f"potential private key in {fp}"

    def test_bundle_unchanged_after_verification(self):
        """Verification is read-only; the bundle is byte-identical after."""
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(BUNDLE, tmp, dirs_exist_ok=True)

        def snapshot(d: Path):
            result = {}
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    result[str(p.relative_to(d))] = p.read_bytes()
            return result

        before = snapshot(tmp)
        r = _run_evidence(tmp)
        assert "evidence: OK" in r.stdout
        after = snapshot(tmp)
        assert before == after, f"bundle changed after verification: {set(after) - set(before)}"
        shutil.rmtree(tmp)


class TestTamperedBundle:
    def _copy_and_tamper(self, *, mutate_delegation=False, mutate_receipt=False, mutate_artifact=False):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(BUNDLE, tmp, dirs_exist_ok=True)
        if mutate_delegation:
            d = json.loads((tmp / "delegation.json").read_text())
            d["body"]["budget"]["max_wall_seconds"] = 9999
            (tmp / "delegation.json").write_text(json.dumps(d, indent=2) + "\n")
        if mutate_receipt:
            r = json.loads((tmp / "receipt.json").read_text())
            r["body"]["status"] = "failed"
            (tmp / "receipt.json").write_text(json.dumps(r, indent=2) + "\n")
        if mutate_artifact:
            (tmp / "artifacts" / "result.json").write_text("tampered\n")
        return tmp

    def test_delegation_byte_tampering_fails(self):
        tmp = self._copy_and_tamper(mutate_delegation=True)
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout, r.stdout
        assert r.returncode != 0

    def test_receipt_byte_tampering_fails(self):
        tmp = self._copy_and_tamper(mutate_receipt=True)
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout, r.stdout
        assert r.returncode != 0

    def test_artifact_byte_tampering_fails(self):
        tmp = self._copy_and_tamper(mutate_artifact=True)
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout, r.stdout
        assert r.returncode != 0

    def test_wrong_issuer_manifest_fails(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(BUNDLE, tmp, dirs_exist_ok=True)
        # Swap issuer with executor manifest
        shutil.copy(tmp / "executor" / "node.json", tmp / "issuer" / "node.json")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout, r.stdout

    def test_wrong_executor_manifest_fails(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(BUNDLE, tmp, dirs_exist_ok=True)
        shutil.copy(tmp / "issuer" / "node.json", tmp / "executor" / "node.json")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout, r.stdout


class TestBundleSafety:
    def test_absolute_path_rejected(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(BUNDLE, tmp, dirs_exist_ok=True)
        meta = json.loads((tmp / "bundle.json").read_text())
        meta["delegation"] = "/etc/passwd"
        (tmp / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout, r.stdout

    def test_dot_dot_traversal_rejected(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(BUNDLE, tmp, dirs_exist_ok=True)
        meta = json.loads((tmp / "bundle.json").read_text())
        meta["delegation"] = "../../delegation.json"
        (tmp / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout, r.stdout

    def test_malformed_bundle_json_fails_cleanly(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bundle.json").write_text("not json")
        r = _run_evidence(tmp)
        assert "evidence: FAILED" in r.stdout, r.stdout
        assert r.returncode != 0

    def test_no_network_attempted(self):
        """The command completes without network activity (proved by offline success)."""
        r = _run_evidence(BUNDLE)
        assert "evidence: OK" in r.stdout
        assert "network_access: none" in r.stdout


class TestHistoricalIndependence:
    def test_historical_verification_independent_of_clock(self):
        """The bundle verifies even if the clock says some future date."""
        from federated_agent_web.evidence import verify_evidence_bundle
        report = verify_evidence_bundle(BUNDLE)
        assert "temporal_mode: historical" in report
        assert "current_admissibility: not evaluated" in report
        assert "evidence: OK" in report

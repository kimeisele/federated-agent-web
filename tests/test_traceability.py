"""Focused verifier tests for MUST-to-test traceability (v0.3)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify_traceability.py"
INVENTORY = ROOT / "docs" / "TRACEABILITY_V0_2.json"
REPORT = ROOT / "docs" / "IMPLEMENTATION_REPORT.md"

REAL_SECTION = "6. Closed schemas and versioned extension"
REAL_SOURCE_TEXT = "Every object in the three normative schemas MUST set `additionalProperties: false`, including at minimum the document envelope, `issuer`, `body`, `authority`, `budget`, `signature`, artifact entries, and node-manifest key entries."
REAL_IMPL = "schemas/delegation.schema.json"
REAL_TEST = "tests/test_schemas.py::TestClosedSchemas::test_envelope_rejects_unknown_members"


def _run_verifier(tmp_path: Path, inventory_text: str, report_text: str,
                  spec_text: str | None = None, nodes: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the verifier against temporary fixtures."""
    spec = tmp_path / "spec.md"
    spec.write_text(spec_text or f"# {REAL_SECTION}\n\n{REAL_SOURCE_TEXT}\n\nMore text without MUST here.\n")
    inv = tmp_path / "inv.json"
    inv.write_text(inventory_text)
    rep = tmp_path / "report.md"
    rep.write_text(report_text)
    nodes_file = tmp_path / "nodes.txt"
    nodes_file.write_text("\n".join(nodes or [REAL_TEST]) + "\n")
    return subprocess.run(
        [sys.executable, str(VERIFIER), "--pytest-nodes", str(nodes_file),
         "--spec", str(spec), "--inventory", str(inv), "--report", str(rep)],
        capture_output=True, text=True, timeout=15,
    )


def _valid_inventory() -> dict:
    return {
        "spec_file": "docs/federated-agent-web-build-spec-v0.2.md",
        "spec_version": "0.2",
        "requirements": [{
            "id": "FAW-V02-6-001",
            "section": REAL_SECTION,
            "source_text": REAL_SOURCE_TEXT,
            "summary": "Closed schemas.",
            "implementation": [REAL_IMPL],
            "tests": [REAL_TEST],
            "evidence_class": "schema enforcement",
            "status": "covered",
        }],
        "excluded_paragraphs": [],
    }


def _valid_report() -> str:
    return (
        "| ID | Source | Obligation | Implementation | Executable test evidence |\n"
        "|---|---|---|---|---|\n"
        f"| `FAW-V02-6-001` | {REAL_SECTION} | Closed schemas. | `{REAL_IMPL}` | `{REAL_TEST}` |\n"
    )


def _valid_spec() -> str:
    return f"# {REAL_SECTION}\n\n{REAL_SOURCE_TEXT}\n\nMore text without MUST here.\n"


class TestTraceabilityVerifier:
    def test_real_inventory_passes(self, tmp_path):
        """The committed inventory + report + real pytest nodes pass."""
        sys.path.insert(0, str(ROOT / "scripts"))
        from verify_traceability import main as vmain
        collected = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", str(ROOT / "tests")],
            capture_output=True, text=True, timeout=60,
        )
        assert collected.returncode == 0, "pytest --collect-only must succeed"
        nodes = tmp_path / "nodes.txt"
        nodes.write_text(collected.stdout)
        rc = vmain(["--pytest-nodes", str(nodes), "--inventory", str(INVENTORY),
                    "--report", str(REPORT)])
        assert rc == 0, "committed traceability inventory must pass"

    # -- multiplicity / mapping regressions --------------------------------

    def test_duplicate_requirement_source_pair_rejected(self, tmp_path):
        inv = _valid_inventory()
        inv["requirements"].append(dict(inv["requirements"][0]))
        inv["requirements"][1]["id"] = "FAW-V02-6-002"
        r = _run_verifier(tmp_path, json.dumps(inv), _valid_report(), spec_text=_valid_spec())
        assert r.returncode != 0
        assert "duplicate (section, source_text) mapping" in r.stdout

    def test_duplicate_exclusion_source_pair_rejected(self, tmp_path):
        inv = _valid_inventory()
        inv["excluded_paragraphs"] = [
            {"section": "x", "source_text": "A sentence that MUST be excluded here.",
             "reason": "quoted non-normative material (non-normative)", "classification": "quoted non-normative material"},
            {"section": "x", "source_text": "A sentence that MUST be excluded here.",
             "reason": "quoted non-normative material (non-normative)", "classification": "quoted non-normative material"},
        ]
        spec_text = "# x\n\n" + "A sentence that MUST be excluded here.\n"
        r = _run_verifier(tmp_path, json.dumps(inv), _valid_report(), spec_text=spec_text)
        assert r.returncode != 0
        assert "duplicate (section, source_text) mapping" in r.stdout

    def test_requirement_exclusion_overlap_rejected(self, tmp_path):
        inv = _valid_inventory()
        inv["excluded_paragraphs"] = [{
            "section": REAL_SECTION, "source_text": REAL_SOURCE_TEXT,
            "reason": "quoted non-normative material (non-normative)",
            "classification": "quoted non-normative material",
        }]
        r = _run_verifier(tmp_path, json.dumps(inv), _valid_report(), spec_text=_valid_spec())
        assert r.returncode != 0
        assert "both requirement and exclusion" in r.stdout

    def test_correct_source_text_wrong_section_rejected(self, tmp_path):
        inv = _valid_inventory()
        inv["requirements"][0]["section"] = "9. Delegation contract"
        r = _run_verifier(tmp_path, json.dumps(inv), _valid_report(), spec_text=_valid_spec())
        assert r.returncode != 0
        assert "does not match any extracted source unit" in r.stdout

    def test_missing_requirement_rejected(self, tmp_path):
        inv = _valid_inventory()
        inv["requirements"].pop()
        r = _run_verifier(tmp_path, json.dumps(inv), _valid_report(), spec_text=_valid_spec())
        assert r.returncode != 0
        assert "mapped zero times" in r.stdout

    def test_stale_source_quote_rejected(self, tmp_path):
        inv = _valid_inventory()
        inv["requirements"][0]["source_text"] = "This clause no longer exists in the spec and MUST be detected."
        r = _run_verifier(tmp_path, json.dumps(inv), _valid_report(), spec_text=_valid_spec())
        assert r.returncode != 0
        assert "no longer in spec" in r.stdout

    # -- report synchronization regressions --------------------------------

    def test_report_abbreviated_test_names_rejected(self, tmp_path):
        inv = _valid_inventory()
        report = (
            "| ID | Source | Obligation | Implementation | Executable test evidence |\n"
            "|---|---|---|---|---|\n"
            f"| `FAW-V02-6-001` | {REAL_SECTION} | Closed schemas. | `{REAL_IMPL}` | `test_envelope_rejects_unknown_members` |\n"
        )
        r = _run_verifier(tmp_path, json.dumps(inv), report, spec_text=_valid_spec())
        assert r.returncode != 0
        assert "abbreviated test name" in r.stdout

    def test_report_missing_full_node_id_rejected(self, tmp_path):
        inv = _valid_inventory()
        report = (
            "| ID | Source | Obligation | Implementation | Executable test evidence |\n"
            "|---|---|---|---|---|\n"
            f"| `FAW-V02-6-001` | {REAL_SECTION} | Closed schemas. | `{REAL_IMPL}` | `tests/other.py::test_other` |\n"
        )
        nodes = ["tests/other.py::test_other"]
        r = _run_verifier(tmp_path, json.dumps(inv), report, spec_text=_valid_spec(), nodes=nodes)
        assert r.returncode != 0
        assert "missing full pytest node" in r.stdout

    def test_report_missing_implementation_path_rejected(self, tmp_path):
        inv = _valid_inventory()
        report = (
            "| ID | Source | Obligation | Implementation | Executable test evidence |\n"
            "|---|---|---|---|---|\n"
            f"| `FAW-V02-6-001` | {REAL_SECTION} | Closed schemas. | `src/other.py` | `{REAL_TEST}` |\n"
        )
        r = _run_verifier(tmp_path, json.dumps(inv), report, spec_text=_valid_spec())
        assert r.returncode != 0
        assert "missing implementation path" in r.stdout

    def test_duplicate_report_rows_rejected(self, tmp_path):
        inv = _valid_inventory()
        row = f"| `FAW-V02-6-001` | {REAL_SECTION} | Closed schemas. | `{REAL_IMPL}` | `{REAL_TEST}` |"
        report = ("| ID | Source | Obligation | Implementation | Executable test evidence |\n"
                  "|---|---|---|---|---|\n" + row + "\n" + row + "\n")
        r = _run_verifier(tmp_path, json.dumps(inv), report, spec_text=_valid_spec())
        assert r.returncode != 0
        assert "duplicate report rows" in r.stdout

    def test_unknown_pytest_node_rejected(self, tmp_path):
        inv = _valid_inventory()
        inv["requirements"][0]["tests"] = ["tests/nonexistent.py::test_never"]
        r = _run_verifier(tmp_path, json.dumps(inv), _valid_report(), spec_text=_valid_spec())
        assert r.returncode != 0
        assert "not collected" in r.stdout

    def test_uncovered_status_rejected(self, tmp_path):
        inv = _valid_inventory()
        inv["requirements"][0]["status"] = "TODO"
        r = _run_verifier(tmp_path, json.dumps(inv), _valid_report(), spec_text=_valid_spec())
        assert r.returncode != 0
        assert "status must be 'covered'" in r.stdout

    def test_unjustified_exclusion_rejected(self, tmp_path):
        inv = _valid_inventory()
        inv["excluded_paragraphs"] = [{
            "section": "9. Delegation contract",
            "source_text": "Some paragraph that MUST do something.",
            "reason": "no good reason",
            "classification": "quoted non-normative material",
        }]
        spec_text = "# 9. Delegation contract\n\n" + "Some paragraph that MUST do something.\n"
        r = _run_verifier(tmp_path, json.dumps(inv), _valid_report(), spec_text=spec_text)
        assert r.returncode != 0
        assert "non-normative" in r.stdout

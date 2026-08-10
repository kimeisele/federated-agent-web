"""Reflexive runner validation: the Python reference harness driven THROUGH
the generic runner over the full committed P01–P05 / N01–N15 matrix.

This is the mandatory reflexive validation (issue #43): the implementation is
not complete merely because direct unit tests pass — the real path
runner -> stdin JSON -> Python reference harness -> stdout verdict JSON ->
runner comparison must pass every fixture with the exact expected category.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from federated_agent_web import conformance_runner as cr

REPO_ROOT = Path(__file__).resolve().parents[1]
CONF_DIR = REPO_ROOT / "conformance" / "v0.2"
SRC_DIR = REPO_ROOT / "src"

# Run with the same interpreter that is running the tests, with the package
# source on PYTHONPATH so the harness subprocess resolves it.
_HARNESS_CMD = None


def _harness_cmd() -> str:
    global _HARNESS_CMD
    if _HARNESS_CMD is None:
        import shlex
        import sys

        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SRC_DIR) + (os.pathsep + existing if existing else "")
        os.environ.update(env)
        _HARNESS_CMD = f"{shlex.quote(sys.executable)} -m federated_agent_web.harness"
    return _HARNESS_CMD


def _expected(fixture_id: str) -> tuple[str, str | None]:
    manifest = json.loads((CONF_DIR / "manifest.json").read_text(encoding="utf-8"))
    record = next(f for f in manifest["fixtures"] if f["id"] == fixture_id)
    return record["expect"], record.get("expected_category")


def test_reflexive_p01_p05_accept() -> None:
    results = cr.collect_results(_harness_cmd(), fixture_ids=["P01", "P02", "P03", "P04", "P05"])
    assert len(results) == 5
    for entry in results:
        assert entry["pass"], entry
        assert entry["actual_verdict"] == "accept"
        assert entry["actual_category"] is None


def test_reflexive_n01_n15_exact_categories() -> None:
    fixture_ids = [f"N{i:02d}" for i in range(1, 16)]
    results = cr.collect_results(_harness_cmd(), fixture_ids=fixture_ids)
    assert len(results) == 15
    for entry in results:
        assert entry["pass"], entry
        assert entry["actual_verdict"] == "reject"
        expected_category = _expected(entry["fixture_id"])[1]
        assert entry["actual_category"] == expected_category, entry


def test_reflexive_full_matrix_passes() -> None:
    """The complete reflexive path over every committed P/N fixture."""
    results = cr.collect_results(_harness_cmd())
    assert len(results) == 20
    failures = [e for e in results if not e["pass"]]
    assert not failures, failures
    # Exact category agreement for every N fixture.
    for entry in results:
        expect_verdict, expect_category = _expected(entry["fixture_id"])
        assert entry["actual_verdict"] == expect_verdict
        if expect_category is not None:
            assert entry["actual_category"] == expect_category
        else:
            assert entry["actual_category"] is None


def test_runner_exit_code_is_zero() -> None:
    assert cr.run_conformance(_harness_cmd()) == 0

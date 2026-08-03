#!/usr/bin/env python3
"""MUST-to-test traceability verifier for the FAW v0.2 governing spec.

Scans ``docs/federated-agent-web-build-spec-v0.2.md`` outside fenced code
blocks, extracts every paragraph or list item containing uppercase ``MUST``
or ``MUST NOT``, and verifies that ``docs/TRACEABILITY_V0_2.json``:

- contains exactly that source inventory (no silent omissions);
- has stable unique IDs, existing implementation paths, non-empty test lists;
- every test node appears in the supplied pytest collection list;
- every requirement is covered (no TODO/unmapped/uncovered statuses);
- explicit exclusions are justified and stale exclusions fail.

Usage:
    python -m pytest --collect-only -q > /tmp/faw-pytest-nodes.txt
    python scripts/verify_traceability.py --pytest-nodes /tmp/faw-pytest-nodes.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_FILE = ROOT / "docs" / "federated-agent-web-build-spec-v0.2.md"
INVENTORY_FILE = ROOT / "docs" / "TRACEABILITY_V0_2.json"
REPORT_FILE = ROOT / "docs" / "IMPLEMENTATION_REPORT.md"

FENCE_RE = re.compile(r"^```", re.MULTILINE)
MUST_RE = re.compile(r"\bMUST(?:\s+NOT)?\b")
BAD_STATUSES = {"todo", "unknown", "unmapped", "uncovered"}
ALLOWED_EVIDENCE = {
    "runtime verification",
    "schema enforcement",
    "static repository invariant",
    "CLI behavior",
    "persistence/state behavior",
    "documentation or packaging invariant",
}


def extract_source_inventory(spec_text: str) -> list[str]:
    """Return normalized paragraphs/list items containing MUST/MUST NOT.

    Ignores fenced code blocks; normalizes wrapped whitespace; treats each
    bullet or numbered list item as its own unit.
    """
    # Remove fenced code blocks (``` ... ```) entirely.
    lines = spec_text.split("\n")
    out: list[str] = []
    in_fence = False
    for line in lines:
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out.append(line)

    # Group into paragraphs; split list items onto their own units.
    units: list[str] = []
    current: list[str] = []
    list_item_re = re.compile(r"^(\s*[-*+]|\s*\d+[.)])\s+")

    def flush() -> None:
        if current:
            units.append(" ".join(current))
            current.clear()

    for line in out:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if list_item_re.match(line):
            flush()
            current.append(list_item_re.sub("", stripped))
        else:
            # Continuation of previous unit
            current.append(stripped)
    flush()

    # Filter to MUST-bearing units, normalize whitespace
    result = []
    for unit in units:
        normalized = re.sub(r"\s+", " ", unit).strip()
        if normalized and MUST_RE.search(normalized):
            result.append(normalized)
    return result


def section_for(spec_text: str, source_text: str) -> str:
    """Find the governing section heading for a source clause."""
    pos = spec_text.find(source_text[:80])
    if pos < 0:
        return "?"
    prefix = spec_text[:pos]
    headings = re.findall(r"^#{1,6}\s+(.+)$", prefix, re.MULTILINE)
    return headings[-1] if headings else "?"


def load_pytest_nodes(path: Path) -> set[str]:
    nodes: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        # pytest --collect-only -q emits one node per line like
        # tests/test_x.py::TestCls::test_y  (no summary noise when -q)
        if line.startswith("tests/") and "::" in line:
            nodes.add(line)
    return nodes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pytest-nodes", type=Path, required=True,
                        help="path to `pytest --collect-only -q` output")
    parser.add_argument("--spec", type=Path, default=SPEC_FILE)
    parser.add_argument("--inventory", type=Path, default=INVENTORY_FILE)
    parser.add_argument("--report", type=Path, default=REPORT_FILE)
    args = parser.parse_args(argv)

    spec_text = args.spec.read_text()
    inventory = json.loads(args.inventory.read_text())
    pytest_nodes = load_pytest_nodes(args.pytest_nodes)

    errors: list[str] = []

    # 1. Source inventory vs JSON inventory
    source_items = extract_source_inventory(spec_text)
    json_items = [r["source_text"] for r in inventory["requirements"]]
    excluded = inventory.get("excluded_paragraphs", [])
    excluded_texts = [e["source_text"] for e in excluded]

    missing = [s for s in source_items if s not in json_items and s not in excluded_texts]
    if missing:
        errors.append(f"source clauses missing from inventory: {len(missing)}")
        for m in missing[:3]:
            errors.append(f"  MISSING: {m[:100]}")

    stale = [s for s in json_items + excluded_texts if s not in source_items]
    if stale:
        errors.append(f"inventory/exclusion clauses no longer in spec: {len(stale)}")
        for s in stale[:3]:
            errors.append(f"  STALE: {s[:100]}")

    # 2. Per-requirement checks
    seen_ids: set[str] = set()
    report_text = args.report.read_text()
    for req in inventory["requirements"]:
        rid = req["id"]
        if rid in seen_ids:
            errors.append(f"duplicate requirement ID: {rid}")
        seen_ids.add(rid)
        if not req.get("section"):
            errors.append(f"{rid}: missing section")
        if not req.get("summary"):
            errors.append(f"{rid}: missing summary")
        if not req.get("implementation"):
            errors.append(f"{rid}: missing implementation paths")
        for impl in req.get("implementation", []):
            if not (ROOT / impl).exists():
                errors.append(f"{rid}: implementation path missing: {impl}")
        tests = req.get("tests", [])
        if not tests:
            errors.append(f"{rid}: empty test list")
        for node in tests:
            if node not in pytest_nodes:
                errors.append(f"{rid}: test node not collected: {node}")
        if req.get("status") != "covered":
            errors.append(f"{rid}: status must be 'covered', got {req.get('status')!r}")
        if req.get("evidence_class") not in ALLOWED_EVIDENCE:
            errors.append(f"{rid}: invalid evidence_class {req.get('evidence_class')!r}")
        if rid not in report_text:
            errors.append(f"{rid}: missing from implementation report")

    # 3. Exclusion checks
    for excl in excluded:
        if not excl.get("source_text"):
            errors.append("exclusion with empty source_text")
        if "non-normative" not in excl.get("reason", "").lower():
            errors.append(f"exclusion not justified as non-normative: {excl.get('source_text', '')[:80]}")
        if excl.get("classification") != "quoted non-normative material":
            errors.append(f"exclusion wrong classification: {excl.get('source_text', '')[:80]}")

    # 4. Report: every JSON requirement ID must be in report table and vice versa
    table_ids = set(re.findall(r"\|?\s*`?(FAW-V02-\d+(?:\.\d+)?-\d+)`?\s*\|", report_text))
    json_ids = set(seen_ids)
    report_missing = json_ids - table_ids
    if report_missing:
        errors.append(f"report table missing requirement rows: {sorted(report_missing)[:5]}")
    json_missing = table_ids - json_ids
    if json_missing:
        errors.append(f"report rows absent from JSON inventory: {sorted(json_missing)[:5]}")

    if errors:
        print("TRACEABILITY: FAILED")
        for e in errors:
            print(f"  {e}")
        return 1

    total = len(inventory["requirements"])
    print(f"TRACEABILITY: OK")
    print(f"requirements: {total}")
    print(f"exclusions: {len(excluded)}")
    print(f"uncovered: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())

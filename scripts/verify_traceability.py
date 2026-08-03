#!/usr/bin/env python3
"""MUST-to-test traceability verifier for the FAW v0.2 governing spec.

Scans ``docs/federated-agent-web-build-spec-v0.2.md`` outside fenced code
blocks, extracts every paragraph or list item containing uppercase ``MUST``
or ``MUST NOT`` together with its governing heading, and verifies that
``docs/TRACEABILITY_V0_2.json``:

- maps every extracted (section, source_text) unit exactly once;
- has stable unique IDs, existing implementation paths, non-empty test lists;
- every test node appears in the supplied pytest collection list;
- every requirement is covered (no TODO/unmapped/uncovered statuses);
- the implementation-report table rows contain the exact section,
  every implementation path, and every complete pytest node ID.

Usage:
    python -m pytest --collect-only -q > /tmp/faw-pytest-nodes.txt
    python scripts/verify_traceability.py --pytest-nodes /tmp/faw-pytest-nodes.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SourceRequirement:
    """A normalized MUST/MUST NOT unit together with its governing heading."""

    section: str
    source_text: str

    def as_pair(self) -> tuple[str, str]:
        return (self.section, self.source_text)


def extract_source_requirements(spec_text: str) -> list[SourceRequirement]:
    """Return normalized (section, source_text) units containing MUST/MUST NOT.

    Ignores fenced code blocks; tracks the most recent Markdown heading;
    normalizes wrapped whitespace; treats each bullet or numbered list item
    as its own unit.
    """
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

    units: list[tuple[str, str]] = []  # (heading, text)
    current_heading = ""
    current: list[str] = []
    list_item_re = re.compile(r"^(\s*[-*+]|\s*\d+[.)])\s+")
    heading_re = re.compile(r"^#{1,6}\s+(.+)$")

    def flush() -> None:
        if current:
            text = " ".join(current)
            if MUST_RE.search(text):
                units.append((current_heading, re.sub(r"\s+", " ", text).strip()))
            current.clear()

    for line in out:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        m = heading_re.match(stripped)
        if m:
            flush()
            current_heading = m.group(1).strip()
            continue
        if list_item_re.match(line):
            flush()
            current.append(list_item_re.sub("", stripped))
        else:
            current.append(stripped)
    flush()

    return [SourceRequirement(section=s, source_text=t) for s, t in units]


def load_pytest_nodes(path: Path) -> set[str]:
    nodes: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("tests/") and "::" in line:
            nodes.add(line)
    return nodes


def _row_cells(row: str) -> list[str]:
    """Split a markdown table row into cells (skips the first empty cell)."""
    cells = [c.strip() for c in row.split("|")]
    return [c for c in cells if c != ""]


def parse_report_rows(report_text: str) -> tuple[dict[str, dict], set[str]]:
    """Parse the traceability table into {requirement_id: {section, impl, tests}}.

    Returns (rows, duplicate_ids) where duplicate_ids are IDs with more than
    one table row.
    """
    rows: dict[str, dict] = {}
    seen: Counter = Counter()
    in_table = False
    for line in report_text.splitlines():
        s = line.strip()
        if s.startswith("| ID |"):
            in_table = True
            continue
        if in_table and s.startswith("|") and s.endswith("|"):
            cells = _row_cells(s)
            if len(cells) < 5:
                continue
            rid = cells[0].strip("` ")
            if not re.match(r"FAW-V02-\d+(?:\.\d+)?-\d+$", rid):
                continue
            seen[rid] += 1
            section = cells[1].strip()
            impl = [p.strip().strip("`") for p in re.split(r"<br>|,", cells[3]) if p.strip()]
            tests = [n.strip().strip("`") for n in re.split(r"<br>", cells[4]) if n.strip()]
            rows[rid] = {"section": section, "implementation": impl, "tests": tests}
        elif in_table and s == "":
            in_table = False
    duplicates = {rid for rid, count in seen.items() if count > 1}
    return rows, duplicates


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

    # ---- 1. Section-aware source extraction ----
    extracted = extract_source_requirements(spec_text)
    extracted_pairs: Counter = Counter(sr.as_pair() for sr in extracted)
    extracted_ids: dict[tuple[str, str], str] = {sr.as_pair(): sr.as_pair()[0] for sr in extracted}
    for pair, count in extracted_pairs.items():
        if count > 1:
            errors.append(f"extracted source unit appears {count}x (duplicate clause): {pair[1][:80]}")

    # ---- 2. One-to-one mapping checks ----
    req_pairs: list[tuple[str, str]] = [
        (r.get("section", ""), r.get("source_text", "")) for r in inventory["requirements"]
    ]
    excl_pairs: list[tuple[str, str]] = [
        (e.get("section", ""), e.get("source_text", "")) for e in inventory.get("excluded_paragraphs", [])
    ]
    mapped_pairs: Counter = Counter(req_pairs + excl_pairs)
    seen_ids: Counter = Counter(r["id"] for r in inventory["requirements"])

    for rid, count in seen_ids.items():
        if count > 1:
            errors.append(f"duplicate requirement ID: {rid}")

    for pair, count in mapped_pairs.items():
        if count > 1:
            errors.append(f"duplicate (section, source_text) mapping ({count}x): {pair[1][:80]}")

    if req_pairs and excl_pairs:
        overlap = set(req_pairs) & set(excl_pairs)
        if overlap:
            errors.append(f"source pair appears as both requirement and exclusion: {next(iter(overlap))[1][:80]}")

    # Every extracted pair must be mapped exactly once.
    for pair in extracted_pairs:
        if mapped_pairs[pair] == 0:
            errors.append(f"extracted source unit mapped zero times: {pair[1][:80]}")
        if mapped_pairs[pair] > 1:
            errors.append(f"extracted source unit mapped more than once: {pair[1][:80]}")

    # Stale inventory/exclusion pairs.
    for pair in req_pairs + excl_pairs:
        if extracted_pairs[pair] == 0:
            errors.append(f"inventory/exclusion pair no longer in spec: {pair[1][:80]}")

    # ---- 3. Per-requirement checks ----
    report_text = args.report.read_text()
    report_rows, dup_report_rows = parse_report_rows(report_text)

    for req in inventory["requirements"]:
        rid = req["id"]
        pair = (req.get("section", ""), req.get("source_text", ""))
        if pair not in extracted_pairs:
            errors.append(f"{rid}: (section, source_text) does not match any extracted source unit")
        if req.get("section") != extracted_ids.get(pair):
            # extracted_ids maps pair->section; if present, it equals req section by construction
            pass
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

        # ---- 5. Report synchronization ----
        if rid not in report_rows:
            errors.append(f"{rid}: missing from implementation report table")
        else:
            row = report_rows[rid]
            if req.get("section") not in row["section"]:
                errors.append(f"{rid}: report row has wrong section {row['section']!r}")
            for impl in req.get("implementation", []):
                if impl not in row["implementation"]:
                    errors.append(f"{rid}: report row missing implementation path {impl}")
            for node in tests:
                if node not in row["tests"]:
                    errors.append(f"{rid}: report row missing full pytest node {node}")
            # A short test name alone must not satisfy the check.
            for node in tests:
                if node.split("::")[-1] in row["tests"] and node not in row["tests"]:
                    errors.append(f"{rid}: report row contains abbreviated test name for {node}")

    # Report rows not in JSON (unknown) and duplicates.
    json_ids = {r["id"] for r in inventory["requirements"]}
    for rid in report_rows:
        if rid not in json_ids:
            errors.append(f"unknown report row requirement: {rid}")
    for rid in dup_report_rows:
        errors.append(f"duplicate report rows for requirement: {rid}")

    # ---- 4. Exclusion checks ----
    for excl in inventory.get("excluded_paragraphs", []):
        pair = (excl.get("section", ""), excl.get("source_text", ""))
        if pair not in extracted_pairs:
            errors.append(f"exclusion pair does not match any extracted source unit: {excl.get('source_text', '')[:80]}")
        if "non-normative" not in excl.get("reason", "").lower():
            errors.append(f"exclusion not justified as non-normative: {excl.get('source_text', '')[:80]}")
        if excl.get("classification") != "quoted non-normative material":
            errors.append(f"exclusion wrong classification: {excl.get('source_text', '')[:80]}")

    if errors:
        print("TRACEABILITY: FAILED")
        for e in errors:
            print(f"  {e}")
        return 1

    total = len(inventory["requirements"])
    print("TRACEABILITY: OK")
    print(f"requirements: {total}")
    print(f"exclusions: {len(inventory.get('excluded_paragraphs', []))}")
    print("uncovered: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())

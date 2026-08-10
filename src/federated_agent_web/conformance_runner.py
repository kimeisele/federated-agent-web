"""Generic language-neutral conformance harness runner.

Drives an external harness implementing the process contract in
``docs/HARNESS.md`` over the committed conformance package
(``conformance/v0.2/manifest.json`` is the runner-side truth). For every
P01–P05 and N01–N15 fixture it verifies fixture byte identity, constructs a
non-leaking inline request, invokes the harness over stdin/stdout, validates
the result envelope, and compares the verdict/category against the manifest
expectation.

Failure classification is deliberate:

- ``CONFORMANCE FAILURE`` — the harness produced a valid result that does not
  match the manifest expectation (wrong verdict or wrong rejection category);
- ``HARNESS OPERATIONAL FAILURE`` — the harness invocation itself failed
  (malformed result, invariant violation, timeout, non-zero exit, invalid
  envelope), including runner-side input errors.

Expected answer data (``expect``, ``expected_category``, fixture IDs) stays
runner-side only and is never sent to the harness.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

HARNESS_VERSION = "1"
PROTOCOL_VERSION = "0.2"

STABLE_CATEGORIES = frozenset(
    {
        "parse.invalid_json",
        "parse.duplicate_member",
        "parse.invalid_unicode",
        "canonicalization.number_out_of_domain",
        "schema.invalid",
        "document.kind_mismatch",
        "audience.mismatch",
        "temporal.invalid",
        "trust.invalid_chain",
        "trust.unknown_key",
        "trust.key_not_valid",
        "signature.invalid",
        "binding.mismatch",
    }
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "conformance" / "v0.2" / "manifest.json"

PENDING_FIELDS = ("task_id", "attempt_id", "delegation_digest", "executor_node_id", "status")


class HarnessOperationalFailure(Exception):
    """Operational/harness failure (never a protocol verdict mismatch)."""


class ConformanceFailure(Exception):
    """The harness produced a valid result contradicting the manifest."""


def _is_pn(fixture_id: str) -> bool:
    if not fixture_id or fixture_id[0] not in ("P", "N"):
        return False
    return fixture_id[1:].isdigit()


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessOperationalFailure(f"cannot load manifest {path}: {exc}") from exc


def _check_identity(path: Path, expected_sha256: str, expected_size: int) -> None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HarnessOperationalFailure(f"cannot read fixture {path}: {exc}") from exc
    actual_size = len(data)
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_size != expected_size:
        raise HarnessOperationalFailure(
            f"fixture {path.name} size {actual_size} != manifest {expected_size}"
        )
    if actual_sha256 != expected_sha256:
        raise HarnessOperationalFailure(
            f"fixture {path.name} sha256 {actual_sha256[:16]}... != manifest {expected_sha256[:16]}..."
        )


def build_request(record: dict[str, Any], conf_dir: Path) -> dict[str, Any]:
    """Construct a non-leaking inline request for one fixture record.

    Only verification context is included. ``expect``, ``expected_category``,
    the fixture ID, ``source``, ``mutation``, and ``pending.delegation_source``
    are deliberately absent.
    """
    doc_bytes = (conf_dir / record["bytes"]).read_bytes()
    chain = []
    for chain_path in record.get("trust_chain") or []:
        chain.append({"bytes_b64": base64.b64encode((conf_dir / chain_path).read_bytes()).decode("ascii")})
    pending = None
    if record.get("pending"):
        pending = {key: record["pending"][key] for key in PENDING_FIELDS}
    return {
        "harness_version": HARNESS_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": uuid.uuid4().hex,
        "document_bytes_b64": base64.b64encode(doc_bytes).decode("ascii"),
        "expected_kind": record["expected_kind"],
        "now": record["now"],
        "pinned_at": record["pinned_at"],
        "trust_chain": chain,
        "local_node_id": record.get("local_node_id"),
        "local_policy": record["local_policy"],
        "pending": pending,
    }


def _validate_result(request: dict[str, Any], stdout: bytes) -> tuple[str, str | None]:
    """Validate the result envelope and invariants. Returns (verdict, category)."""
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HarnessOperationalFailure(f"malformed result JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise HarnessOperationalFailure("result is not a JSON object")
    if result.get("harness_version") != HARNESS_VERSION:
        raise HarnessOperationalFailure(
            f"result harness_version {result.get('harness_version')!r} != request {HARNESS_VERSION!r}"
        )
    if result.get("protocol_version") != PROTOCOL_VERSION:
        raise HarnessOperationalFailure(
            f"result protocol_version {result.get('protocol_version')!r} != request {PROTOCOL_VERSION!r}"
        )
    if result.get("request_id") != request["request_id"]:
        raise HarnessOperationalFailure("result request_id does not match request request_id")
    verdict = result.get("verdict")
    if verdict == "accept":
        if "category" in result:
            raise HarnessOperationalFailure('accept result must not contain "category"')
        return "accept", None
    if verdict == "reject":
        category = result.get("category")
        if not isinstance(category, str) or category not in STABLE_CATEGORIES:
            raise HarnessOperationalFailure(
                f"reject result must contain exactly one stable category, got {category!r}"
            )
        if len([k for k in result if k == "category"]) != 1:
            raise HarnessOperationalFailure("reject result must contain exactly one category")
        return "reject", category
    raise HarnessOperationalFailure(f"unknown verdict {verdict!r}")


def _compare(record: dict[str, Any], verdict: str, category: str | None) -> None:
    expect_accept = record["expect"] == "accept"
    if expect_accept:
        if verdict != "accept":
            raise ConformanceFailure(
                f"expected accept, harness returned reject {category}"
            )
        return
    if verdict != "reject":
        raise ConformanceFailure("expected reject, harness returned accept")
    expected_category = record.get("expected_category")
    if category != expected_category:
        raise ConformanceFailure(
            f"category {category!r} != expected {expected_category!r}"
        )


def collect_results(
    harness_cmd: str,
    manifest_path: str | Path | None = None,
    timeout: float = 30.0,
    fixture_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run the harness over the selected conformance fixtures and return one
    result dict per fixture. Raises HarnessOperationalFailure for runner-side
    input errors; per-fixture outcomes are classified in each dict.
    """
    manifest = _load_manifest(Path(manifest_path) if manifest_path else DEFAULT_MANIFEST)
    conf_dir = Path(manifest_path).parent if manifest_path else DEFAULT_MANIFEST.parent

    argv = shlex.split(harness_cmd)
    if not argv:
        raise HarnessOperationalFailure("--harness command line is empty")

    records = [f for f in manifest["fixtures"] if _is_pn(f["id"])]
    if fixture_ids is not None:
        wanted = set(fixture_ids)
        records = [f for f in records if f["id"] in wanted]

    results: list[dict[str, Any]] = []
    conformance_failures = 0
    operational_failures = 0

    for record in records:
        fid = record["id"]
        entry: dict[str, Any] = {
            "fixture_id": fid,
            "expected_verdict": record["expect"],
            "expected_category": record.get("expected_category"),
        }
        try:
            _check_identity(
                conf_dir / record["bytes"],
                record["sha256"],
                record["size_bytes"],
            )
            request = build_request(record, conf_dir)
            proc = subprocess.run(
                argv,
                input=json.dumps(request).encode("utf-8"),
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode != 0:
                stderr_tail = proc.stderr.decode("utf-8", errors="replace").strip()[-400:]
                raise HarnessOperationalFailure(
                    f"harness exited {proc.returncode}: {stderr_tail}"
                )
            verdict, category = _validate_result(request, proc.stdout)
            entry["actual_verdict"] = verdict
            entry["actual_category"] = category
            _compare(record, verdict, category)
            entry["pass"] = True
            entry["failure"] = None
        except subprocess.TimeoutExpired:
            operational_failures += 1
            entry["pass"] = False
            entry["failure"] = "HARNESS OPERATIONAL FAILURE"
            entry["operational_error"] = f"timeout after {timeout}s"
        except HarnessOperationalFailure as exc:
            operational_failures += 1
            entry["pass"] = False
            entry["failure"] = "HARNESS OPERATIONAL FAILURE"
            entry["operational_error"] = str(exc)
        except ConformanceFailure as exc:
            conformance_failures += 1
            entry["pass"] = False
            entry["failure"] = "CONFORMANCE FAILURE"
            entry["operational_error"] = str(exc)
        except Exception as exc:  # runner-side internal error
            operational_failures += 1
            entry["pass"] = False
            entry["failure"] = "HARNESS OPERATIONAL FAILURE"
            entry["operational_error"] = f"runner internal error: {exc}"
        results.append(entry)

    return results


def run_conformance(
    harness_cmd: str,
    manifest_path: str | Path | None = None,
    timeout: float = 30.0,
    fixture_ids: list[str] | None = None,
) -> int:
    """Run the harness over the conformance package and report per fixture.

    Returns 0 iff every selected fixture passes; 1 otherwise.
    """
    results = collect_results(harness_cmd, manifest_path, timeout, fixture_ids)
    conformance_failures = sum(1 for e in results if e["failure"] == "CONFORMANCE FAILURE")
    operational_failures = sum(1 for e in results if e["failure"] == "HARNESS OPERATIONAL FAILURE")
    for entry in results:
        line = (
            f"{entry['fixture_id']}: expected {entry['expected_verdict']}"
            + (f" ({entry['expected_category']})" if entry.get("expected_category") else "")
            + f" | actual {entry.get('actual_verdict')}"
            + (f" ({entry.get('actual_category')})" if entry.get("actual_category") else "")
            + f" | {'PASS' if entry['pass'] else 'FAIL'}"
        )
        if not entry["pass"]:
            line += f" | {entry['failure']}: {entry.get('operational_error', '')}"
        print(line)

    print(f"conformance-run: {sum(e['pass'] for e in results)}/{len(results)} fixtures passed")
    if conformance_failures:
        print(f"conformance-run: {conformance_failures} CONFORMANCE FAILURE(s)")
    if operational_failures:
        print(f"conformance-run: {operational_failures} HARNESS OPERATIONAL FAILURE(s)")
    return 0 if all(e["pass"] for e in results) else 1

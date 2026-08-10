"""Contract-boundary tests for the language-neutral conformance harness.

Focused on the interface boundary defined in docs/HARNESS.md: request
envelope, result envelope invariants, exit-code semantics, stdout/stderr
separation, raw-byte preservation, the in-memory pending adapter, and CLI
compatibility. The reflexive runner validation over the full P/N matrix
lives in test_conformance_runner.py.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


from federated_agent_web import conformance_runner as cr
from federated_agent_web.harness import HARNESS_VERSION, PROTOCOL_VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]
CONF_DIR = REPO_ROOT / "conformance" / "v0.2"
SRC_DIR = REPO_ROOT / "src"

HARNESS_CMD = f"{shlex.quote(sys.executable)} -m federated_agent_web.harness"


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) + (os.pathsep + existing if existing else "")
    return env


def _run_harness(request: dict) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "federated_agent_web.harness"],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        env=_child_env(),
    )


def _record(fixture_id: str) -> dict:
    manifest = json.loads((CONF_DIR / "manifest.json").read_text(encoding="utf-8"))
    return next(f for f in manifest["fixtures"] if f["id"] == fixture_id)


def _request_for(fixture_id: str) -> dict:
    return cr.build_request(_record(fixture_id), CONF_DIR)


def _fake_harness(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"


def _run_runner(harness_cmd: str, fixture_ids: list[str]) -> list[dict]:
    return cr.collect_results(harness_cmd, fixture_ids=fixture_ids)


# ---------------------------------------------------------------------------
# Envelope behavior of the reference harness (real stdin/stdout subprocess)
# ---------------------------------------------------------------------------


def test_valid_accept() -> None:
    proc = _run_harness(_request_for("P01"))
    assert proc.returncode == 0
    result = json.loads(proc.stdout.decode())
    assert result == {
        "harness_version": HARNESS_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": result["request_id"],
        "verdict": "accept",
    }
    assert "category" not in result


def test_valid_reject() -> None:
    proc = _run_harness(_request_for("N14"))
    assert proc.returncode == 0  # protocol rejection is a successful execution
    result = json.loads(proc.stdout.decode())
    assert result["verdict"] == "reject"
    assert result["category"] == "signature.invalid"


def test_invalid_utf8_raw_bytes_survive_transport() -> None:
    # N03 contains a raw 0xFF byte; the base64 inline transport must preserve
    # it so the harness still detects invalid UTF-8.
    proc = _run_harness(_request_for("N03"))
    assert proc.returncode == 0
    result = json.loads(proc.stdout.decode())
    assert result["verdict"] == "reject"
    assert result["category"] == "parse.invalid_unicode"


def test_duplicate_member_raw_bytes_survive_transport() -> None:
    proc = _run_harness(_request_for("N01"))
    assert proc.returncode == 0
    result = json.loads(proc.stdout.decode())
    assert result["verdict"] == "reject"
    assert result["category"] == "parse.duplicate_member"


def test_pending_positive_receipt_binding() -> None:
    proc = _run_harness(_request_for("P03"))
    assert proc.returncode == 0
    assert json.loads(proc.stdout.decode())["verdict"] == "accept"


def test_pending_n15_digest_mismatch() -> None:
    proc = _run_harness(_request_for("N15"))
    assert proc.returncode == 0
    result = json.loads(proc.stdout.decode())
    assert result["verdict"] == "reject"
    assert result["category"] == "binding.mismatch"


def test_operational_failure_is_nonzero_not_protocol_reject() -> None:
    # A request with an unsupported harness version is an operational failure:
    # non-zero exit and NO result JSON on stdout.
    request = _request_for("P01")
    request["harness_version"] = "99"
    proc = _run_harness(request)
    assert proc.returncode != 0
    assert proc.stdout.strip() == b""


def test_invalid_base64_is_operational_failure() -> None:
    request = _request_for("P01")
    request["document_bytes_b64"] = "!!!not-base64!!!"
    proc = _run_harness(request)
    assert proc.returncode != 0
    assert proc.stdout.strip() == b""


def test_malformed_envelope_is_operational_failure() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "federated_agent_web.harness"],
        input=b"{not json",
        capture_output=True,
        env=_child_env(),
    )
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# Runner-side result validation (fake harnesses emit canned results)
# ---------------------------------------------------------------------------


def test_accept_with_category_is_operational_failure(tmp_path) -> None:
    harness = _fake_harness(
        tmp_path,
        "accept_with_category",
        "import json,sys; json.dump({'harness_version': '1', 'protocol_version': '0.2', "
        "'request_id': json.load(sys.stdin)['request_id'], 'verdict': 'accept', 'category': 'schema.invalid'}, sys.stdout)\n",
    )
    results = _run_runner(harness, ["P01"])
    assert not results[0]["pass"]
    assert results[0]["failure"] == "HARNESS OPERATIONAL FAILURE"


def test_reject_without_category_is_operational_failure(tmp_path) -> None:
    harness = _fake_harness(
        tmp_path,
        "reject_no_category",
        "import json,sys; json.dump({'harness_version': '1', 'protocol_version': '0.2', "
        "'request_id': json.load(sys.stdin)['request_id'], 'verdict': 'reject'}, sys.stdout)\n",
    )
    results = _run_runner(harness, ["P01"])
    assert results[0]["failure"] == "HARNESS OPERATIONAL FAILURE"


def test_unknown_category_is_operational_failure(tmp_path) -> None:
    harness = _fake_harness(
        tmp_path,
        "unknown_category",
        "import json,sys; json.dump({'harness_version': '1', 'protocol_version': '0.2', "
        "'request_id': json.load(sys.stdin)['request_id'], 'verdict': 'reject', 'category': 'made.up.code'}, sys.stdout)\n",
    )
    results = _run_runner(harness, ["P01"])
    assert results[0]["failure"] == "HARNESS OPERATIONAL FAILURE"


def test_wrong_request_id_is_operational_failure(tmp_path) -> None:
    harness = _fake_harness(
        tmp_path,
        "wrong_request_id",
        "import json,sys; json.dump({'harness_version': '1', 'protocol_version': '0.2', "
        "'request_id': 'different-id', 'verdict': 'accept'}, sys.stdout)\n",
    )
    results = _run_runner(harness, ["P01"])
    assert results[0]["failure"] == "HARNESS OPERATIONAL FAILURE"


def test_wrong_harness_version_is_operational_failure(tmp_path) -> None:
    harness = _fake_harness(
        tmp_path,
        "wrong_version",
        "import json,sys; json.dump({'harness_version': '9', 'protocol_version': '0.2', "
        "'request_id': json.load(sys.stdin)['request_id'], 'verdict': 'accept'}, sys.stdout)\n",
    )
    results = _run_runner(harness, ["P01"])
    assert results[0]["failure"] == "HARNESS OPERATIONAL FAILURE"


def test_extra_stdout_logging_is_operational_failure(tmp_path) -> None:
    harness = _fake_harness(
        tmp_path,
        "extra_stdout",
        "import json,sys; print('banner: ready'); json.dump({'harness_version': '1', "
        "'protocol_version': '0.2', 'request_id': json.load(sys.stdin)['request_id'], 'verdict': 'accept'}, sys.stdout)\n",
    )
    results = _run_runner(harness, ["P01"])
    assert results[0]["failure"] == "HARNESS OPERATIONAL FAILURE"


def test_nonzero_harness_exit_is_operational_failure(tmp_path) -> None:
    harness = _fake_harness(
        tmp_path,
        "nonzero_exit",
        "import sys; sys.stderr.write('crash\\n'); sys.exit(3)\n",
    )
    results = _run_runner(harness, ["P01"])
    assert results[0]["failure"] == "HARNESS OPERATIONAL FAILURE"
    assert "exited 3" in results[0]["operational_error"]


def test_malformed_result_json_is_operational_failure(tmp_path) -> None:
    harness = _fake_harness(
        tmp_path,
        "malformed_result",
        "import sys; sys.stdout.write('this is not json')\n",
    )
    results = _run_runner(harness, ["P01"])
    assert results[0]["failure"] == "HARNESS OPERATIONAL FAILURE"


def test_timeout_is_operational_failure(tmp_path) -> None:
    harness = _fake_harness(
        tmp_path,
        "slow",
        "import time; time.sleep(30)\n",
    )
    results = cr.collect_results(harness, fixture_ids=["P01"], timeout=0.5)
    assert results[0]["failure"] == "HARNESS OPERATIONAL FAILURE"
    assert "timeout" in results[0]["operational_error"]


def test_conformance_failure_classification(tmp_path) -> None:
    # A valid result contradicting the manifest is CONFORMANCE FAILURE, not an
    # operational failure: P01 expects accept, harness returns reject.
    harness = _fake_harness(
        tmp_path,
        "wrong_verdict",
        "import json,sys; json.dump({'harness_version': '1', 'protocol_version': '0.2', "
        "'request_id': json.load(sys.stdin)['request_id'], 'verdict': 'reject', 'category': 'signature.invalid'}, sys.stdout)\n",
    )
    results = _run_runner(harness, ["P01"])
    assert results[0]["failure"] == "CONFORMANCE FAILURE"


# ---------------------------------------------------------------------------
# CLI compatibility
# ---------------------------------------------------------------------------


def test_legacy_conformance_cli_local_node(tmp_path) -> None:
    # A valid local node directory (manifest at .well-known/faw-node.json)
    # keeps the legacy behavior: exit 0 with "OK".
    node_dir = tmp_path / "node"
    (node_dir / ".well-known").mkdir(parents=True)
    (node_dir / ".well-known" / "faw-node.json").write_bytes(
        (CONF_DIR / "context" / "issuer-manifest.json").read_bytes()
    )
    proc = subprocess.run(
        [sys.executable, "-m", "federated_agent_web.cli", "conformance", str(node_dir)],
        capture_output=True,
        env=_child_env(),
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_legacy_conformance_cli_error_semantics(tmp_path) -> None:
    empty = tmp_path / "no-node"
    empty.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "federated_agent_web.cli", "conformance", str(empty)],
        capture_output=True,
        env=_child_env(),
        text=True,
    )
    assert proc.returncode == 1
    assert "not found" in proc.stdout


def test_conformance_run_cli() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "federated_agent_web.cli",
            "conformance",
            "run",
            "--harness",
            HARNESS_CMD,
            "--manifest",
            str(CONF_DIR / "manifest.json"),
        ],
        capture_output=True,
        env=_child_env(),
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "20/20 fixtures passed" in proc.stdout

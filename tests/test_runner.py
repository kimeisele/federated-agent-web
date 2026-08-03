"""Process-boundary integration test — two persisted nodes with subprocess CLI (§5)."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid

from federated_agent_web import canonical
from federated_agent_web.canonical import digest_bytes
from federated_agent_web.documents import (
    KIND_DELEGATION,
    content_digest_of,
)
from federated_agent_web.identity import NodeIdentity
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.transports import FilesystemTransport


def _faw(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["faw", *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_two_node_process_boundary(tmp_path):
    """Two separately persisted nodes, subprocess CLI, full delegation lifecycle.

    Proves:
    1. A's pending store has the delegation before transport send.
    2. B runs once as executor, verifies and executes hash_file.
    3. B sends a signed terminal receipt.
    4. A runs once as issuer, accepts the receipt and closes the record.
    5. The artifact digest matches the exact artifact bytes.
    6. Re-delivering the same delegation and re-running B does not execute
       hash_file a second time.
    7. Restarting the CLI processes does not lose replay or pending state.
    """
    # ---- setup -----------------------------------------------------------
    issuer = NodeIdentity.create(display_name="Integration Issuer", capabilities=["hash_file"])
    executor = NodeIdentity.create(display_name="Integration Executor", capabilities=["hash_file"])

    issuer_dir = tmp_path / "issuer"
    executor_dir = tmp_path / "executor"
    transport_root = tmp_path / "transport"
    state_a = tmp_path / "state-a"
    state_b = tmp_path / "state-b"
    work_a = tmp_path / "work-a"
    work_b = tmp_path / "work-b"

    issuer.to_json(issuer_dir)
    executor.to_json(executor_dir)

    input_path = tmp_path / "input.bin"
    input_data = b"integration test input\n"
    input_path.write_bytes(input_data)
    input_digest = digest_bytes(input_data)

    # Build delegation from issuer to executor.
    from federated_agent_web.documents import now_utc_z
    from datetime import timedelta
    base = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    body = {
        "task_id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "issuer_node_id": issuer.node_id,
        "target_node_id": executor.node_id,
        "capability": "hash_file",
        "input": {
            "kind": "refs",
            "refs": [{"digest": input_digest, "location": str(input_path)}],
        },
        "authority": {
            "actions": ["hash_file"],
            "filesystem_scope": {"read_paths": [str(input_path)]},
            "external_effect_scope": {"allowed_effects": ["none"]},
            "expiry": (base + timedelta(seconds=7200)).astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "budget": {"max_wall_seconds": 60, "max_output_bytes": 8192},
        "deadline": (base + timedelta(seconds=1200)).astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
        "expected_output": {
            "kind": "artifact",
            "media_type": "application/json",
            "required_artifacts": ["result.json"],
            "expects_repository_mutation": False,
        },
        "expires_at": (base + timedelta(seconds=600)).astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    delegation = issuer.sign_document(KIND_DELEGATION, body)
    delegation_digest = content_digest_of(delegation)
    delegation_bytes = canonical.canonical_bytes(delegation)

    # 1. A registers the delegation BEFORE transport send.
    pending_a = PendingDelegationStore(state_a / "pending")
    pending_a.register_outstanding(delegation, delegation_digest)
    assert pending_a.get_outstanding(body["task_id"], body["attempt_id"]) is not None

    # Deliver the delegation into B's inbox via the filesystem transport.
    transport_a = FilesystemTransport(transport_root, issuer.node_id)
    _ = FilesystemTransport(transport_root, executor.node_id)  # initialise B's inbox dir
    sent = transport_a.send(delegation_bytes, executor.node_id)
    assert sent.ok, sent.error

    # 2. Node B runs once as executor.
    common = [
        "--transport-root", str(transport_root),
        "--state-dir", str(state_b),
        "--work-dir", str(work_b),
    ]
    run_b = _faw(
        "node", "run-once",
        "--identity", str(executor_dir),
        "--trust", str(issuer_dir),
        "--role", "executor",
        *common,
    )
    assert "executed:" in run_b.stdout, f"B stdout: {run_b.stdout}  stderr: {run_b.stderr}"
    assert run_b.returncode == 0

    # 3. Node A runs once as issuer, accepting the receipt.
    common_a = [
        "--transport-root", str(transport_root),
        "--state-dir", str(state_a),
        "--work-dir", str(work_a),
    ]
    run_a = _faw(
        "node", "run-once",
        "--identity", str(issuer_dir),
        "--trust", str(executor_dir),
        "--role", "issuer",
        *common_a,
    )
    assert "accepted:" in run_a.stdout, f"A stdout: {run_a.stdout}  stderr: {run_a.stderr}"
    assert run_a.returncode == 0

    # 5. The pending record is terminal and the artifact exists.
    record = pending_a.get_outstanding(body["task_id"], body["attempt_id"])
    assert record is not None
    assert record.state == "terminal"
    assert record.terminal_receipt is not None

    receipt = record.terminal_receipt
    assert receipt["body"]["status"] == "succeeded"
    artifacts = receipt["body"]["artifacts"]
    assert len(artifacts) >= 1
    artifact_path = __import__("pathlib").Path(artifacts[0]["location"])
    assert artifact_path.is_file()
    expected_digest = digest_bytes(artifact_path.read_bytes())
    assert artifacts[0]["digest"] == expected_digest, "artifact digest must match exact bytes"

    # 6. Re-deliver the same delegation and re-run B — must deduplicate.
    sent2 = transport_a.send(delegation_bytes, executor.node_id)
    assert sent2.ok
    run_b2 = _faw(
        "node", "run-once",
        "--identity", str(executor_dir),
        "--trust", str(issuer_dir),
        "--role", "executor",
        *common,
    )
    assert "deduplicated:" in run_b2.stdout, f"B2 stdout: {run_b2.stdout}"
    assert run_b2.returncode == 0

    # 7. Re-run B with empty inbox — idle.
    run_b3 = _faw(
        "node", "run-once",
        "--identity", str(executor_dir),
        "--trust", str(issuer_dir),
        "--role", "executor",
        *common,
    )
    assert "idle:" in run_b3.stdout
    assert run_b3.returncode == 0

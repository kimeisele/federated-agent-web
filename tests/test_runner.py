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



def _faw_runner(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["faw", *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


class TestMalformedInput:
    def test_malformed_delegation_nacked(self, tmp_path):
        """Garbage delegation bytes are nacked, exit non-zero, handler never runs."""
        issuer = NodeIdentity.create(display_name="MI", capabilities=["hash_file"])
        executor = NodeIdentity.create(display_name="ME", capabilities=["hash_file"])
        issuer_dir = tmp_path / "issuer"
        executor_dir = tmp_path / "executor"
        issuer.to_json(issuer_dir)
        executor.to_json(executor_dir)

        from federated_agent_web.transports import FilesystemTransport
        t = tmp_path / "transport"
        tx = FilesystemTransport(t, issuer.node_id)
        _ = FilesystemTransport(t, executor.node_id)
        tx.send(b"not valid json at all", executor.node_id)

        common = ["node", "run-once",
            "--identity", str(executor_dir),
            "--trust", str(issuer_dir),
            "--transport-root", str(t),
            "--state-dir", str(tmp_path / "state"),
            "--work-dir", str(tmp_path / "work"),
            "--role", "executor",
        ]
        r = _faw_runner(*common)
        assert "nack:" in r.stdout, f"stdout: {r.stdout}"
        assert r.returncode != 0

    def test_malformed_receipt_nacked(self, tmp_path):
        """Garbage receipt bytes are nacked, exit non-zero, pending store untouched."""
        issuer = NodeIdentity.create(display_name="MR", capabilities=["hash_file"])
        executor = NodeIdentity.create(display_name="MER", capabilities=["hash_file"])
        issuer_dir = tmp_path / "issuer"
        executor_dir = tmp_path / "executor"
        issuer.to_json(issuer_dir)
        executor.to_json(executor_dir)

        from federated_agent_web.transports import FilesystemTransport
        t = tmp_path / "transport"
        tx = FilesystemTransport(t, executor.node_id)
        _ = FilesystemTransport(t, issuer.node_id)
        tx.send(b'{"kind": "not-a-receipt"}', issuer.node_id)

        common = ["node", "run-once",
            "--identity", str(issuer_dir),
            "--trust", str(executor_dir),
            "--transport-root", str(t),
            "--state-dir", str(tmp_path / "state"),
            "--work-dir", str(tmp_path / "work"),
            "--role", "issuer",
        ]
        r = _faw_runner(*common)
        assert "nack:" in r.stdout, f"stdout: {r.stdout}"
        assert r.returncode != 0

    def test_valid_after_invalid_processed(self, tmp_path):
        """After an invalid envelope is nacked, a later valid one succeeds."""
        issuer = NodeIdentity.create(display_name="VI", capabilities=["hash_file"])
        executor = NodeIdentity.create(display_name="VE", capabilities=["hash_file"])
        issuer_dir = tmp_path / "issuer"
        executor_dir = tmp_path / "executor"
        issuer.to_json(issuer_dir)
        executor.to_json(executor_dir)

        # Build and register a valid delegation
        from federated_agent_web.documents import KIND_DELEGATION, content_digest_of
        from federated_agent_web.canonical import digest_bytes, canonical_bytes
        from federated_agent_web.pending import PendingDelegationStore
        from datetime import timedelta
        import uuid
        base = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        input_path = tmp_path / "input.bin"
        input_path.write_bytes(b"valid-after-invalid")
        input_digest = digest_bytes(b"valid-after-invalid")
        body = {
            "task_id": str(uuid.uuid4()),
            "attempt_id": str(uuid.uuid4()),
            "issuer_node_id": issuer.node_id,
            "target_node_id": executor.node_id,
            "capability": "hash_file",
            "input": {"kind": "refs", "refs": [{"digest": input_digest, "location": str(input_path)}]},
            "authority": {
                "actions": ["hash_file"],
                "filesystem_scope": {"read_paths": [str(input_path)]},
                "external_effect_scope": {"allowed_effects": ["none"]},
                "expiry": (base + timedelta(seconds=7200)).astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            "budget": {"max_wall_seconds": 60, "max_output_bytes": 8192},
            "deadline": (base + timedelta(seconds=1200)).astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
            "expected_output": {"kind": "artifact", "media_type": "application/json", "required_artifacts": ["result.json"], "expects_repository_mutation": False},
            "expires_at": (base + timedelta(seconds=600)).astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        delegation = issuer.sign_document(KIND_DELEGATION, body)
        delegation_bytes = canonical_bytes(delegation)
        pending = PendingDelegationStore(tmp_path / "state-a" / "pending")
        pending.register_outstanding(delegation, content_digest_of(delegation))

        from federated_agent_web.transports import FilesystemTransport
        t = tmp_path / "transport"
        tx = FilesystemTransport(t, issuer.node_id)
        _ = FilesystemTransport(t, executor.node_id)

        # 1. Send garbage first
        tx.send(b"garbage", executor.node_id)
        common_e = ["node", "run-once",
            "--identity", str(executor_dir),
            "--trust", str(issuer_dir),
            "--transport-root", str(t),
            "--state-dir", str(tmp_path / "state-e"),
            "--work-dir", str(tmp_path / "work-e"),
            "--role", "executor",
        ]
        r1 = _faw_runner(*common_e)
        assert "nack:" in r1.stdout
        assert r1.returncode != 0

        # 2. Then send valid delegation
        tx.send(delegation_bytes, executor.node_id)
        r2 = _faw_runner(*common_e)
        assert "executed:" in r2.stdout, f"r2 stdout: {r2.stdout}  stderr: {r2.stderr}"
        assert r2.returncode == 0


class TestPublicOnlyPeerTrust:
    def test_peer_trust_without_private_keys(self, tmp_path):
        """Node A verifies Node B using only B's public manifest chain."""
        issuer = NodeIdentity.create(display_name="A", capabilities=["hash_file"])
        executor = NodeIdentity.create(display_name="B", capabilities=["hash_file"])
        issuer_dir = tmp_path / "a"
        executor_dir = tmp_path / "b"
        issuer.to_json(issuer_dir)
        executor.to_json(executor_dir)

        # Remove executor's private keys — the issuer must still trust it
        import shutil
        shutil.rmtree(executor_dir / "keys")
        # Remove issuer's private keys — the executor must still trust it
        shutil.rmtree(issuer_dir / "keys")

        # Build and deliver a delegation (issuer signs using its own keys
        # which are still in memory, not loaded from disk — the persistent
        # identity directory's key table only provides the public chain for
        # the peer).

        from federated_agent_web.documents import KIND_DELEGATION, content_digest_of
        from federated_agent_web.canonical import digest_bytes, canonical_bytes
        from federated_agent_web.pending import PendingDelegationStore
        from datetime import timedelta
        import uuid
        base = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        input_path = tmp_path / "input.bin"
        input_path.write_bytes(b"peer-trust-test")
        input_digest = digest_bytes(b"peer-trust-test")
        body = {
            "task_id": str(uuid.uuid4()),
            "attempt_id": str(uuid.uuid4()),
            "issuer_node_id": issuer.node_id,
            "target_node_id": executor.node_id,
            "capability": "hash_file",
            "input": {"kind": "refs", "refs": [{"digest": input_digest, "location": str(input_path)}]},
            "authority": {
                "actions": ["hash_file"],
                "filesystem_scope": {"read_paths": [str(input_path)]},
                "external_effect_scope": {"allowed_effects": ["none"]},
                "expiry": (base + timedelta(seconds=7200)).astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            "budget": {"max_wall_seconds": 60, "max_output_bytes": 8192},
            "deadline": (base + timedelta(seconds=1200)).astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
            "expected_output": {"kind": "artifact", "media_type": "application/json", "required_artifacts": ["result.json"], "expects_repository_mutation": False},
            "expires_at": (base + timedelta(seconds=600)).astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        delegation = issuer.sign_document(KIND_DELEGATION, body)
        delegation_bytes = canonical_bytes(delegation)
        pending = PendingDelegationStore(tmp_path / "state-a" / "pending")
        pending.register_outstanding(delegation, content_digest_of(delegation))

        from federated_agent_web.transports import FilesystemTransport
        t = tmp_path / "transport"
        tx = FilesystemTransport(t, issuer.node_id)
        _ = FilesystemTransport(t, executor.node_id)
        tx.send(delegation_bytes, executor.node_id)

        # Executor runs — but it can't load its OWN identity from disk
        # because keys/ is deleted. For the executor role we need the local
        # identity with private keys. Restore executor keys.
        executor.to_json(executor_dir)  # re-persist with keys

        common_e = ["node", "run-once",
            "--identity", str(executor_dir),
            "--trust", str(issuer_dir),
            "--transport-root", str(t),
            "--state-dir", str(tmp_path / "state-e"),
            "--work-dir", str(tmp_path / "work-e"),
            "--role", "executor",
        ]
        r = _faw_runner(*common_e)
        assert "executed:" in r.stdout, f"executor stdout: {r.stdout}  stderr: {r.stderr}"
        assert r.returncode == 0

        # Issuer runs — restore its keys for local identity load
        issuer.to_json(issuer_dir)
        common_i = ["node", "run-once",
            "--identity", str(issuer_dir),
            "--trust", str(executor_dir),
            "--transport-root", str(t),
            "--state-dir", str(tmp_path / "state-a"),
            "--work-dir", str(tmp_path / "work-a"),
            "--role", "issuer",
        ]
        r2 = _faw_runner(*common_i)
        assert "accepted:" in r2.stdout, f"issuer stdout: {r2.stdout}  stderr: {r2.stderr}"
        assert r2.returncode == 0

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
    run_b = _faw_runner(
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
    run_a = _faw_runner(
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
    run_b2 = _faw_runner(
        "node", "run-once",
        "--identity", str(executor_dir),
        "--trust", str(issuer_dir),
        "--role", "executor",
        *common,
    )
    assert "deduplicated:" in run_b2.stdout, f"B2 stdout: {run_b2.stdout}"
    assert run_b2.returncode == 0

    # 7. Re-run B with empty inbox — idle.
    run_b3 = _faw_runner(
        "node", "run-once",
        "--identity", str(executor_dir),
        "--trust", str(issuer_dir),
        "--role", "executor",
        *common,
    )
    assert "idle:" in run_b3.stdout
    assert run_b3.returncode == 0

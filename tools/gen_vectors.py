#!/usr/bin/env python3
"""Generate the static golden vectors under ``vectors/``.

Run once from the repository root:

    python tools/gen_vectors.py

The fixtures are committed as static JSON + byte files so that a second,
independent implementation can reproduce canonical bytes, content digests,
public keys/kids, signatures, delegation digest, receipt digest, and artifact
digest without importing the Python package (see vectors/README.md).

Keys generated here are ephemeral TEST-ONLY keys; they must never be used
outside tests.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from federated_agent_web import canonical, crypto  # noqa: E402
from federated_agent_web.canonical import digest_bytes  # noqa: E402
from federated_agent_web.demo import DEMO_INPUT_BYTES  # noqa: E402
from federated_agent_web.documents import (  # noqa: E402
    KIND_DELEGATION,
    KIND_RECEIPT,
    content_digest_of,
    now_utc_z,
)
from federated_agent_web.identity import NodeIdentity  # noqa: E402


def _write(dir_path: Path, name: str, data: bytes) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / name
    path.write_bytes(data)
    return path


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    base = ROOT / "vectors"

    # --- canonicalization -------------------------------------------------
    canon_dir = base / "canonicalization"
    cases = {
        "nested": {"z": {"b": 2, "a": 1}, "y": [3, 1, 2]},
        "unicode": {"s": "héllo — wörld", "emoji": "🚀"},
        "numbers": {"exp_high": 1e30, "exp_low": 1e-7, "decimal": 0.1, "int": 42, "int_large": 9007199254740991},
        "strings": {"escaped": "line1\nline2\t\"quoted\"", "slash": "a/b\\c"},
    }
    for name, obj in cases.items():
        canon = canonical.canonical_bytes(obj)
        _write(canon_dir, f"{name}.json", json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        _write(canon_dir, f"{name}.canonical.hex", canon.hex().encode("ascii") + b"\n")

    # --- signatures --------------------------------------------------------
    sig_dir = base / "signatures"
    private_raw, public_raw = crypto.generate_keypair()
    kid = crypto.kid_for(public_raw)
    message = {
        "kind": "faw-test-message",
        "spec_version": "0.2",
        "id": str(uuid.uuid4()),
        "issued_at": now_utc_z(),
        "payload": {"note": "golden signature vector", "n": 7},
    }
    message_bytes = canonical.canonical_bytes(message)
    signature = crypto.sign_canonical(message_bytes, private_raw)
    assert crypto.verify_canonical(message_bytes, signature, public_raw)
    _write(sig_dir, "message.json", json.dumps(message, indent=2).encode("utf-8"))
    _write(sig_dir, "message-canonical.hex", message_bytes.hex().encode("ascii") + b"\n")
    _write(sig_dir, "content-digest", content_digest_of(message).encode("ascii") + b"\n")
    _write(
        sig_dir,
        "keypair.json",
        json.dumps(
            {
                "note": "TEST-ONLY ephemeral key; never use outside tests",
                "algorithm": "Ed25519",
                "kid": kid,
                "public_key_b64url": crypto.b64url_encode(public_raw),
                "private_key_b64url": crypto.b64url_encode(private_raw),
                "signature_alg": "Ed25519",
                "signature_value_b64url": signature,
            },
            indent=2,
        ).encode("utf-8"),
    )

    # --- delegation --------------------------------------------------------
    del_dir = base / "delegations"
    issuer = NodeIdentity.create(display_name="Vector Issuer", capabilities=["hash_file"])
    _write(del_dir, "issuer-manifest.json", json.dumps(issuer.head_manifest, indent=2).encode("utf-8"))
    input_path = ROOT / "vectors" / "receipts" / "artifact-input.bin"
    _write(base / "receipts", "artifact-input.bin", DEMO_INPUT_BYTES)
    input_digest = digest_bytes(DEMO_INPUT_BYTES)
    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(seconds=600)
    deadline = issued + timedelta(seconds=1200)
    delegation = issuer.sign_document(
        KIND_DELEGATION,
        {
            "task_id": str(uuid.uuid4()),
            "attempt_id": str(uuid.uuid4()),
            "issuer_node_id": issuer.node_id,
            "target_node_id": "urn:faw:vector-target-0001",
            "capability": "hash_file",
            "input": {
                "kind": "refs",
                "refs": [{"digest": input_digest, "location": str(input_path)}],
            },
            "authority": {
                "actions": ["hash_file"],
                "filesystem_scope": {"read_paths": [str(input_path)]},
                "external_effect_scope": {"allowed_effects": ["none"]},
                "expiry": _ts(deadline + timedelta(seconds=3600)),
            },
            "budget": {"max_wall_seconds": 60, "max_output_bytes": 8192},
            "deadline": _ts(deadline),
            "expected_output": {
                "kind": "artifact",
                "media_type": "application/json",
                "required_artifacts": ["result.json"],
                "expects_repository_mutation": False,
            },
            "expires_at": _ts(expires),
        },
    )
    delegation_digest = content_digest_of(delegation)
    _write(del_dir, "delegation.json", json.dumps(delegation, indent=2).encode("utf-8"))
    _write(del_dir, "delegation-digest", delegation_digest.encode("ascii") + b"\n")

    # --- receipt ------------------------------------------------------------
    rec_dir = base / "receipts"
    executor = NodeIdentity.create(display_name="Vector Executor", capabilities=["hash_file"])
    _write(rec_dir, "executor-manifest.json", json.dumps(executor.head_manifest, indent=2).encode("utf-8"))
    result = {
        "capability": "hash_file",
        "task_id": delegation["body"]["task_id"],
        "attempt_id": delegation["body"]["attempt_id"],
        "input": str(input_path),
        "input_digest": input_digest,
        "input_size": len(DEMO_INPUT_BYTES),
        "executor": executor.node_id,
    }
    artifact = json.dumps(result, indent=2).encode("utf-8")
    artifact_digest = digest_bytes(artifact)
    _write(rec_dir, "artifact.bin", artifact)
    _write(rec_dir, "artifact-digest", artifact_digest.encode("ascii") + b"\n")
    started = datetime.now(timezone.utc)
    receipt = executor.sign_document(
        KIND_RECEIPT,
        {
            "receipt_id": str(uuid.uuid4()),
            "task_id": delegation["body"]["task_id"],
            "attempt_id": delegation["body"]["attempt_id"],
            "delegation_digest": delegation_digest,
            "executor_node_id": executor.node_id,
            "status": "succeeded",
            "started_at": _ts(started),
            "finished_at": _ts(started + timedelta(milliseconds=5)),
            "artifacts": [
                {
                    "name": "result.json",
                    "media_type": "application/json",
                    "digest": artifact_digest,
                    "size": len(artifact),
                    "location": str(rec_dir / "artifact.bin"),
                }
            ],
            "usage": {"wall_seconds": 0.005, "output_bytes": len(artifact)},
        },
    )
    receipt_digest = content_digest_of(receipt)
    _write(rec_dir, "receipt.json", json.dumps(receipt, indent=2).encode("utf-8"))
    _write(rec_dir, "receipt-digest", receipt_digest.encode("ascii") + b"\n")

    print(f"vectors written under {base}")
    print(f"  delegation digest: {delegation_digest}")
    print(f"  receipt digest:    {receipt_digest}")
    print(f"  artifact digest:   {artifact_digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

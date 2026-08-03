"""Golden vector reproduction (§13 "Golden vectors", §17).

The fixtures under ``vectors/`` are static JSON/hex/bytes files. A second,
independent implementation must be able to reproduce every digest and
signature from them without importing the Python package: canonical bytes
(JCS), content digests (SHA-256 over canonical bytes), Ed25519 signatures
over the canonical bytes, and artifact digests (SHA-256 over raw bytes).

These tests recompute the values with this implementation and assert they
equal the committed fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

from federated_agent_web import canonical, crypto
from federated_agent_web.canonical import digest_bytes
from federated_agent_web.documents import (
    KIND_DELEGATION,
    KIND_MANIFEST,
    KIND_RECEIPT,
    content_digest_of,
    validate_document,
)

ROOT = Path(__file__).resolve().parents[1]


def _read(dir_name: str, name: str) -> bytes:
    return (ROOT / "vectors" / dir_name / name).read_bytes()


def _read_text(dir_name: str, name: str) -> str:
    return _read(dir_name, name).decode("utf-8").strip()


class TestSignatureVectors:
    def test_canonical_bytes_reproduce(self):
        message = json.loads(_read_text("signatures", "message.json"))
        expected_hex = _read_text("signatures", "message-canonical.hex")
        assert canonical.canonical_bytes(message).hex() == expected_hex

    def test_content_digest_reproduces(self):
        message = json.loads(_read_text("signatures", "message.json"))
        assert content_digest_of(message) == _read_text("signatures", "content-digest")

    def test_signature_verifies_and_kid_matches(self):
        keypair = json.loads(_read_text("signatures", "keypair.json"))
        message = json.loads(_read_text("signatures", "message.json"))
        public_raw = crypto.b64url_decode(keypair["public_key_b64url"])
        assert crypto.kid_for(public_raw) == keypair["kid"]
        canonical_bytes = canonical.canonical_bytes(canonical.strip_signature(message))
        assert crypto.verify_canonical(canonical_bytes, keypair["signature_value_b64url"], public_raw)

    def test_private_key_marked_test_only(self):
        keypair = json.loads(_read_text("signatures", "keypair.json"))
        assert "TEST-ONLY" in keypair["note"]


class TestDelegationVectors:
    def test_delegation_digest_reproduces(self):
        delegation = json.loads(_read_text("delegations", "delegation.json"))
        assert content_digest_of(delegation) == _read_text("delegations", "delegation-digest")

    def test_delegation_signature_verifies_with_issuer_manifest(self):
        delegation = json.loads(_read_text("delegations", "delegation.json"))
        manifest = json.loads(_read_text("delegations", "issuer-manifest.json"))
        validate_document(delegation, KIND_DELEGATION)
        validate_document(manifest, KIND_MANIFEST)
        kid = delegation["issuer"]["kid"]
        entry = next(e for e in manifest["body"]["keys"] if e["kid"] == kid)
        public_raw = crypto.b64url_decode(entry["public_key"])
        canonical_bytes = canonical.canonical_bytes(canonical.strip_signature(delegation))
        assert crypto.verify_canonical(canonical_bytes, delegation["signature"]["value"], public_raw)

    def test_delegation_references_vector_input(self):
        delegation = json.loads(_read_text("delegations", "delegation.json"))
        ref = delegation["body"]["input"]["refs"][0]
        assert ref["digest"] == digest_bytes(_read("receipts", "artifact-input.bin"))


class TestReceiptVectors:
    def test_artifact_digest_over_raw_bytes(self):
        artifact = _read("receipts", "artifact.bin")
        assert digest_bytes(artifact) == _read_text("receipts", "artifact-digest")

    def test_receipt_digest_reproduces(self):
        receipt = json.loads(_read_text("receipts", "receipt.json"))
        assert content_digest_of(receipt) == _read_text("receipts", "receipt-digest")

    def test_receipt_binds_to_vector_delegation(self):
        receipt = json.loads(_read_text("receipts", "receipt.json"))
        delegation_digest = _read_text("delegations", "delegation-digest")
        assert receipt["body"]["delegation_digest"] == delegation_digest
        assert receipt["body"]["artifacts"][0]["digest"] == _read_text("receipts", "artifact-digest")

    def test_receipt_signature_verifies_with_executor_manifest(self):
        receipt = json.loads(_read_text("receipts", "receipt.json"))
        manifest = json.loads(_read_text("receipts", "executor-manifest.json"))
        validate_document(receipt, KIND_RECEIPT)
        entry = next(e for e in manifest["body"]["keys"] if e["kid"] == receipt["issuer"]["kid"])
        public_raw = crypto.b64url_decode(entry["public_key"])
        canonical_bytes = canonical.canonical_bytes(canonical.strip_signature(receipt))
        assert crypto.verify_canonical(canonical_bytes, receipt["signature"]["value"], public_raw)


class TestIndependentReproducibility:
    def test_fixtures_are_plain_data(self):
        """The fixtures contain no package-specific artifacts: JSON, hex, and
        raw bytes only — reproducible by any JCS + SHA-256 + Ed25519 stack."""
        for dir_name in ("canonicalization", "signatures", "delegations", "receipts"):
            for path in sorted((ROOT / "vectors" / dir_name).glob("*")):
                assert path.name not in {".DS_Store", ".gitignore"}
                assert not path.name.endswith((".pyc", ".py"))

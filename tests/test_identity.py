"""Identity persistence and loading tests (§2 operational slice)."""

from __future__ import annotations

import pytest

from federated_agent_web.documents import KIND_RECEIPT, content_digest_of
from federated_agent_web.identity import NodeIdentity


class TestIdentityRoundTrip:
    def test_save_load_round_trip(self, tmp_path):
        node = NodeIdentity.create(display_name="RoundTrip", capabilities=["hash_file"])
        identity_dir = tmp_path / "node"
        node.to_json(identity_dir)
        loaded = NodeIdentity.load(identity_dir)
        assert loaded.node_id == node.node_id
        assert loaded.display_name == node.display_name
        assert len(loaded.manifests) == len(node.manifests)
        assert loaded.head_digest() == node.head_digest()
        assert loaded.active_key.kid == node.active_key.kid

    def test_loaded_identity_signs_valid_receipt(self, tmp_path):
        from federated_agent_web.documents import build_document

        node = NodeIdentity.create(display_name="Signer", capabilities=["hash_file"])
        identity_dir = tmp_path / "node"
        node.to_json(identity_dir)
        loaded = NodeIdentity.load(identity_dir)
        body = {
            "receipt_id": "9c16a6bc-b317-44ec-a438-a7d723c7434a",
            "task_id": "cb00f9fa-0cea-4a2b-918a-1ba2f542a0c4",
            "attempt_id": "8a3054e9-78aa-4a80-b02a-8960767ca8a2",
            "delegation_digest": "sha256:" + "ff" * 32,
            "executor_node_id": loaded.node_id,
            "status": "succeeded",
            "started_at": "2026-08-03T12:00:00.000000Z",
            "finished_at": "2026-08-03T12:00:01.000000Z",
            "artifacts": [],
            "usage": {"wall_seconds": 1},
        }
        receipt = loaded.sign_document(KIND_RECEIPT, body)
        assert receipt["issuer"]["node_id"] == loaded.node_id
        assert receipt["body"]["receipt_id"] == body["receipt_id"]

    def test_missing_active_private_key_rejected(self, tmp_path):
        node = NodeIdentity.create(display_name="KeyMissing", capabilities=["hash_file"])
        identity_dir = tmp_path / "node"
        node.to_json(identity_dir)
        kid = node.active_key.kid
        (identity_dir / "keys" / f"{kid}.key").unlink()
        with pytest.raises(FileNotFoundError, match=f"active key {kid}"):
            NodeIdentity.load(identity_dir)

    def test_mismatched_kid_rejected(self, tmp_path):
        node = NodeIdentity.create(display_name="BadKid", capabilities=["hash_file"])
        identity_dir = tmp_path / "node"
        node.to_json(identity_dir)
        kid = node.active_key.kid
        other, _ = __import__("federated_agent_web.crypto", fromlist=["generate_keypair"]).generate_keypair()
        (identity_dir / "keys" / f"{kid}.key").write_bytes(other)
        with pytest.raises(ValueError, match="private key.*does not match"):
            NodeIdentity.load(identity_dir)

    def test_save_load_with_rotation(self, tmp_path):
        node = NodeIdentity.create(display_name="Rotator", capabilities=["hash_file"])
        node.rotate_key()
        identity_dir = tmp_path / "node"
        node.to_json(identity_dir)
        loaded = NodeIdentity.load(identity_dir)
        assert loaded.head_sequence() == 2
        assert loaded.active_key.kid == node.active_key.kid
        # The retired key's private material is present.
        retired_kid = node.keys[0].kid
        assert retired_kid != loaded.active_key.kid

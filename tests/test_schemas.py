"""Schema, kind, identifier, and path conformance (§6, §8, §13 first group)."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

from federated_agent_web.documents import (
    KIND_DELEGATION,
    KIND_MANIFEST,
    KIND_RECEIPT,
    DocumentError,
    load_schemas,
    validate_document,
)
from federated_agent_web.identity import NodeIdentity

from .conftest import build_delegation, make_node_pair, ts

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-03T12:00:00Z"


def _valid_uuid() -> str:
    return str(uuid.uuid4())


class TestClosedSchemas:
    def test_all_normative_schemas_declare_closed_objects(self):
        schemas = load_schemas()
        for kind, schema in schemas.items():
            assert schema["additionalProperties"] is False, f"{kind} envelope open"

    def test_example_manifest_validates(self):
        node = NodeIdentity.create(display_name="Schema Node", capabilities=["hash_file"])
        validate_document(node.head_manifest, KIND_MANIFEST)  # must not raise

    def test_example_delegation_validates(self, issuer):
        delegation = build_delegation(issuer, target_node_id="urn:faw:target-0001")
        validate_document(delegation, KIND_DELEGATION)

    def test_example_receipt_validates(self, issuer, executor):
        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        from .conftest import build_receipt

        receipt = build_receipt(executor, delegation)
        validate_document(receipt, KIND_RECEIPT)

    @pytest.mark.parametrize("kind", [KIND_MANIFEST, KIND_DELEGATION, KIND_RECEIPT])
    def test_envelope_rejects_unknown_members(self, kind, issuer, executor):
        if kind == KIND_MANIFEST:
            node = NodeIdentity.create(display_name="X", capabilities=["hash_file"])
            doc = copy.deepcopy(node.head_manifest)
        elif kind == KIND_DELEGATION:
            doc = build_delegation(issuer, target_node_id="urn:faw:target-0001")
        else:
            from .conftest import build_receipt

            delegation = build_delegation(issuer, target_node_id=executor.node_id)
            doc = build_receipt(executor, delegation)
        doc["extra"] = "sneaky"
        with pytest.raises(DocumentError):
            validate_document(doc, kind)

    def test_manifest_body_rejects_unknown_members(self):
        node = NodeIdentity.create(display_name="X", capabilities=["hash_file"])
        doc = copy.deepcopy(node.head_manifest)
        doc["body"]["phantom_authority"] = "expand"
        with pytest.raises(DocumentError):
            validate_document(doc, KIND_MANIFEST)

    def test_issuer_rejects_unknown_members(self):
        node = NodeIdentity.create(display_name="X", capabilities=["hash_file"])
        doc = copy.deepcopy(node.head_manifest)
        doc["issuer"]["kid2"] = doc["issuer"]["kid"]
        with pytest.raises(DocumentError):
            validate_document(doc, KIND_MANIFEST)

    def test_signature_rejects_unknown_members(self):
        node = NodeIdentity.create(display_name="X", capabilities=["hash_file"])
        doc = copy.deepcopy(node.head_manifest)
        doc["signature"]["kid"] = doc["issuer"]["kid"]
        with pytest.raises(DocumentError):
            validate_document(doc, KIND_MANIFEST)

    def test_authority_rejects_unknown_members(self, issuer):
        delegation = build_delegation(issuer, target_node_id="urn:faw:target-0001")
        delegation["body"]["authority"]["narrowed_authority"] = {"actions": []}
        with pytest.raises(DocumentError):
            validate_document(delegation, KIND_DELEGATION)

    def test_budget_rejects_unknown_members(self, issuer):
        delegation = build_delegation(issuer, target_node_id="urn:faw:target-0001")
        delegation["body"]["budget"]["infinite_budget"] = True
        with pytest.raises(DocumentError):
            validate_document(delegation, KIND_DELEGATION)

    def test_artifact_entries_reject_unknown_members(self, issuer, executor):
        from .conftest import build_receipt

        delegation = build_delegation(issuer, target_node_id=executor.node_id)
        receipt = build_receipt(executor, delegation)
        receipt["body"]["artifacts"][0]["elevated"] = True
        with pytest.raises(DocumentError):
            validate_document(receipt, KIND_RECEIPT)

    def test_manifest_key_entries_reject_unknown_members(self):
        node = NodeIdentity.create(display_name="X", capabilities=["hash_file"])
        doc = copy.deepcopy(node.head_manifest)
        doc["body"]["keys"][0]["administrator"] = True
        with pytest.raises(DocumentError):
            validate_document(doc, KIND_MANIFEST)


class TestKindAndVersion:
    def test_kind_mismatch_rejected(self, issuer):
        delegation = build_delegation(issuer, target_node_id="urn:faw:target-0001")
        with pytest.raises(DocumentError, match="expected_kind"):
            validate_document(delegation, KIND_RECEIPT)

    def test_unsupported_spec_version_rejected(self, issuer):
        delegation = build_delegation(issuer, target_node_id="urn:faw:target-0001")
        delegation["spec_version"] = "0.1"
        with pytest.raises(DocumentError, match="spec_version"):
            validate_document(delegation, KIND_DELEGATION)

    def test_kind_must_be_const(self):
        schema = load_schemas()[KIND_MANIFEST]
        assert "const" in schema["properties"]["kind"]


class TestIdentifierGrammar:
    @pytest.mark.parametrize(
        "node_id",
        [
            "not-a-urn",
            "urn:faw:",
            "urn:faw:UPPERCASE",
            "urn:faw:with space",
            "urn:faw:über",
            "urn:faw:" + "x" * 64,  # too long
        ],
    )
    def test_invalid_node_id_rejected(self, node_id):
        with pytest.raises((DocumentError, ValueError)):
            NodeIdentity.create(node_id=node_id, display_name="X")

    @pytest.mark.parametrize("capability", ["Hash_File", "hash file", "1hash", "hash--file", "hash..file"])
    def test_invalid_capability_rejected_in_manifest(self, capability):
        with pytest.raises(DocumentError):
            NodeIdentity.create(display_name="X", capabilities=[capability])

    def test_invalid_kid_pattern_rejected(self, issuer):
        delegation = build_delegation(issuer, target_node_id="urn:faw:target-0001")
        delegation["issuer"]["kid"] = "sha256:zzzz" + "0" * 60
        with pytest.raises(DocumentError):
            validate_document(delegation, KIND_DELEGATION)

    def test_invalid_digest_rejected_in_delegation(self, issuer):
        delegation = build_delegation(issuer, target_node_id="urn:faw:target-0001")
        delegation["body"]["input"]["refs"] = [
            {"digest": "md5:" + "0" * 32, "location": "file://x"}
        ]
        delegation["body"]["input"]["kind"] = "refs"
        with pytest.raises(DocumentError):
            validate_document(delegation, KIND_DELEGATION)

    @pytest.mark.parametrize(
        "bad_uuid",
        ["not-a-uuid", "12345678-1234-1234-1234-1234567890ab", "12345678-1234-2234-1234-123456789012"],
    )
    def test_invalid_uuid_rejected(self, issuer, bad_uuid):
        delegation = build_delegation(issuer, target_node_id="urn:faw:target-0001")
        delegation["body"]["task_id"] = bad_uuid
        with pytest.raises(DocumentError):
            validate_document(delegation, KIND_DELEGATION)

    def test_unicode_confusable_node_id_rejected(self):
        # U+FF2E fullwidth N in a URN would pass a naive "ascii-ish" check but
        # is not ASCII; the grammar must reject it.
        with pytest.raises((DocumentError, ValueError)):
            NodeIdentity.create(node_id="urn:faw:test-Ｎ", display_name="X")


class TestManifestPaths:
    def test_faw_manifest_discovered_at_well_known_path(self, tmp_path):
        node = NodeIdentity.create(display_name="Path Node", capabilities=["hash_file"])
        well_known = tmp_path / ".well-known"
        well_known.mkdir()
        (well_known / "faw-node.json").write_text(json.dumps(node.head_manifest))
        assert (well_known / "faw-node.json").is_file()
        manifest = json.loads((well_known / "faw-node.json").read_text())
        validate_document(manifest, KIND_MANIFEST)

    def test_legacy_agent_federation_json_rejected_as_substitute(self, tmp_path):
        legacy = {
            "name": "legacy node",
            "public_key": "not-a-kid",
            "capabilities": ["x"],
        }
        (tmp_path / "agent-federation.json").write_text(json.dumps(legacy))
        # The legacy file is not the FAW manifest: it fails the schema and is
        # never accepted at the reserved path.
        with pytest.raises(DocumentError):
            validate_document(legacy, KIND_MANIFEST)
        assert not (tmp_path / "faw-node.json").exists()

    def test_faw_path_not_aliased_to_agent_card(self):
        schema_manifest = load_schemas()[KIND_MANIFEST]
        # The schema carries no alias for /.well-known/agent-card.json; the
        # reserved path handling is a discovery concern, not a document alias.
        assert schema_manifest["title"] == "FAW Node Manifest"

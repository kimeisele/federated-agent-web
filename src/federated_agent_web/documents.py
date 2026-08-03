"""Normative document envelope, schema validation, and builders (§6, §9, §10)."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from . import canonical, crypto

__all__ = [
    "KIND_MANIFEST",
    "KIND_DELEGATION",
    "KIND_RECEIPT",
    "SUPPORTED_SPEC_VERSION",
    "DocumentError",
    "now_utc_z",
    "parse_timestamp",
    "validate_document",
    "build_document",
    "content_digest_of",
    "load_schemas",
]

KIND_MANIFEST = "faw-node-manifest"
KIND_DELEGATION = "faw-delegation"
KIND_RECEIPT = "faw-receipt"
SUPPORTED_SPEC_VERSION = "0.2"

_SCHEMA_FILES = {
    KIND_MANIFEST: "node-manifest.schema.json",
    KIND_DELEGATION: "delegation.schema.json",
    KIND_RECEIPT: "receipt.schema.json",
}


class DocumentError(ValueError):
    """Raised for schema-invalid or structurally inconsistent documents."""


def now_utc_z() -> str:
    """UTC RFC 3339 timestamp with ``Z`` (§6).

    Microsecond precision is preserved so same-second events (key rotation,
    document issuance) keep a strict total order; the schema permits up to 9
    fractional digits.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    """Parse a UTC RFC 3339 ``Z`` timestamp; raise ValueError otherwise."""
    if not value.endswith("Z"):
        raise ValueError(f"timestamp must end with 'Z': {value!r}")
    body = value[:-1]
    try:
        if "." in body:
            main, fraction = body.split(".", 1)
            fraction = (fraction + "000000")[:6]
            return datetime.strptime(f"{main}.{fraction}", "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
        return datetime.strptime(body, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"invalid RFC 3339 timestamp {value!r}") from exc


# ---------------------------------------------------------------------------
# Schema loading (source-tree schemas first, package data as fallback)
# ---------------------------------------------------------------------------

_schema_validators: dict[str, Draft202012Validator] | None = None


def _schema_dir_candidates() -> list[Path]:
    env = os.environ.get("FAW_SCHEMA_DIR")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parent.parent.parent / "schemas")
    try:
        from importlib.resources import files  # type: ignore[attr-defined]

        package_schemas = files("federated_agent_web") / "schemas"  # type: ignore[arg-type]
        if package_schemas.is_dir():
            candidates.append(Path(str(package_schemas)))
    except Exception:
        pass
    return candidates


def load_schemas() -> dict[str, dict[str, Any]]:
    """Load the three normative schemas from disk (JSON objects)."""
    dirs = _schema_dir_candidates()
    schemas: dict[str, dict[str, Any]] = {}
    for kind, filename in _SCHEMA_FILES.items():
        for directory in dirs:
            candidate = directory / filename
            if candidate.is_file():
                schemas[kind] = json.loads(candidate.read_text(encoding="utf-8"))
                break
        if kind not in schemas:
            raise DocumentError(f"schema file {filename} not found in {[str(d) for d in dirs]}")
    return schemas


def validators() -> dict[str, Draft202012Validator]:
    global _schema_validators
    if _schema_validators is None:
        _schema_validators = {kind: Draft202012Validator(schema) for kind, schema in load_schemas().items()}
    return _schema_validators


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_document(doc: dict[str, Any], expected_kind: str) -> None:
    """Validate ``doc`` against the schema for ``expected_kind``.

    The verifier supplies ``expected_kind``; it is never inferred from the
    document itself (§7.2, §7.3 step 2).
    """
    if expected_kind not in validators():
        raise DocumentError(f"unknown document kind {expected_kind!r}")
    if doc.get("kind") != expected_kind:
        raise DocumentError(
            f"kind {doc.get('kind')!r} does not match expected_kind {expected_kind!r}"
        )
    if doc.get("spec_version") != SUPPORTED_SPEC_VERSION:
        raise DocumentError(
            f"unsupported spec_version {doc.get('spec_version')!r} (expected {SUPPORTED_SPEC_VERSION!r})"
        )
    try:
        validators()[expected_kind].validate(doc)
    except ValidationError as exc:
        raise DocumentError(f"schema validation failed: {exc.message}") from exc


# ---------------------------------------------------------------------------
# Building and signing
# ---------------------------------------------------------------------------


def content_digest_of(doc: dict[str, Any]) -> str:
    """``sha256:<hex>`` over JCS(document without top-level signature) (§7.2)."""
    return canonical.content_digest(doc)


def build_document(
    *,
    kind: str,
    body: dict[str, Any],
    issuer_node_id: str,
    kid: str,
    private_raw: bytes,
    spec_version: str = SUPPORTED_SPEC_VERSION,
    issued_at: str | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    """Build a signed document envelope (§6) and validate it against its schema.

    Signing input: JCS(document minus top-level ``signature``) — the signature
    member is added last and never part of its own signing input (§7.2).
    """
    document: dict[str, Any] = {
        "kind": kind,
        "spec_version": spec_version,
        "id": doc_id or str(uuid.uuid4()),
        "issued_at": issued_at or now_utc_z(),
        "issuer": {"node_id": issuer_node_id, "kid": kid},
        "body": body,
    }
    canonical_bytes = canonical.canonical_bytes(canonical.strip_signature(document))
    document["signature"] = {
        "alg": "Ed25519",
        "value": crypto.sign_canonical(canonical_bytes, private_raw),
    }
    validate_document(document, kind)
    return document

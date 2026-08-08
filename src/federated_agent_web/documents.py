"""Normative document envelope, schema validation, and builders (§6, §9, §10)."""

from __future__ import annotations

import json
import os
import re
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
    "parse_timestamp_ns",
    "datetime_to_ns",
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


# FAW timestamp grammar: UTC, ``Z`` suffix, zero or 1–9 fractional digits.
_TIMESTAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_timestamp(value: str) -> datetime:
    """Parse a UTC RFC 3339 ``Z`` timestamp; raise ValueError otherwise.

    Compatibility API: returns a ``datetime`` at microsecond precision. The
    exact nanosecond representation used by protocol comparisons is
    ``parse_timestamp_ns``; semantic comparisons must not go through this
    function's six-digit truncation.
    """
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


def parse_timestamp_ns(value: str) -> int:
    """Return the exact UTC instant of a FAW timestamp as integer nanoseconds.

    Accepts only the FAW timestamp grammar (UTC, ``Z``, zero or 1–9 fractional
    digits) and requires the value to denote a real calendar instant — pattern
    matching alone is not sufficient (v0.5 profile §2). All nine fractional
    digits are preserved exactly using integer arithmetic; no floating-point
    epoch conversion is performed. ``.5Z``, ``.50Z`` and ``.500000000Z`` map to
    the same instant; ``.500000000Z`` and ``.500000001Z`` differ.
    """
    match = _TIMESTAMP_RE.match(value)
    if match is None:
        raise ValueError(f"invalid RFC 3339 timestamp {value!r}")
    year, month, day, hour, minute, second, fraction = match.groups()
    try:
        instant = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second),
            tzinfo=timezone.utc,
        )
    except ValueError as exc:
        raise ValueError(f"invalid RFC 3339 timestamp {value!r}") from exc
    delta = instant - _EPOCH
    seconds = delta.days * 86400 + delta.seconds
    fraction_ns = int((fraction or "").ljust(9, "0") or "0")
    return seconds * 1_000_000_000 + fraction_ns


def datetime_to_ns(value: datetime) -> int:
    """Convert a UTC-aware ``datetime`` to integer nanoseconds, losslessly.

    Python ``datetime`` carries at most microsecond precision, so the
    conversion is exact. Naive datetimes are treated as UTC.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    delta = value - _EPOCH
    seconds = delta.days * 86400 + delta.seconds
    return seconds * 1_000_000_000 + delta.microseconds * 1000


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

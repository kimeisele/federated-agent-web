"""Experimental Nadi/GitHub transport adapter (v0.4, non-normative).

Implements the FAW ``Transport`` interface over a Nadi-style relay mailbox.
The adapter separates:

- **FAW node identity** (``node_id``) — stable ``urn:faw:...`` identity;
- **relay mailbox address** (``relay_address``) — the outer Nadi source/
  target and mailbox path string;
- **relay credential** — held by the injected backend, never a trust anchor.

FAW documents travel byte-for-byte inside a ``faw.document`` wrapper whose
fields are untrusted transport metadata. The wrapper is never a normative
signed document and never authority.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from .. import canonical, crypto
from ..transports.base import Transport, TransportEnvelope, TransportSendResult

__all__ = [
    "RelayEnvelope",
    "RelayPublishResult",
    "NadiRelayBackend",
    "NadiTransport",
    "wrap_document",
    "unwrap_document",
]

FAW_DOCUMENT_OPERATION = "faw.document"
WRAPPER_KEYS = {
    "message_id",
    "source_node_id",
    "destination_node_id",
    "media_type",
    "encoding",
    "document",
    "document_sha256",
    "created_at",
    "experimental",
}
_NODE_ID_RE = re.compile(r"^urn:faw:[a-z0-9](?:[a-z0-9._-]{0,62})$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class NadiError(ValueError):
    """Raised for malformed or integrity-violating relay wrapper content."""


# ---------------------------------------------------------------------------
# Relay boundary types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelayEnvelope:
    message_id: str
    source_address: str
    destination_address: str
    operation: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RelayPublishResult:
    message_id: str
    ok: bool
    error: str | None = None


class NadiRelayBackend(Protocol):
    """Injected relay backend. Every result is tied to one exact message ID."""

    def publish(self, envelopes: list[RelayEnvelope]) -> list[RelayPublishResult]:
        ...

    def fetch(self, destination_address: str) -> list[RelayEnvelope]:
        ...


# ---------------------------------------------------------------------------
# faw.document wrapper
# ---------------------------------------------------------------------------


def _now_utc_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def wrap_document(
    *,
    message_id: str,
    source_node_id: str,
    destination_node_id: str,
    document_bytes: bytes,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build the ``faw.document`` wrapper payload (transport metadata only)."""
    return {
        "message_id": message_id,
        "source_node_id": source_node_id,
        "destination_node_id": destination_node_id,
        "media_type": "application/faw+json",
        "encoding": "base64url",
        "document": crypto.b64url_encode(document_bytes),
        "document_sha256": canonical.digest_bytes(document_bytes),
        "created_at": created_at or _now_utc_z(),
        "experimental": True,
    }


def _valid_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _valid_timestamp(value: str) -> bool:
    """Validate a real UTC RFC 3339 timestamp ending in ``Z`` (no offset)."""
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    body = value[:-1]
    if "+" in body or "-" in body[10:]:
        return False  # any offset other than UTC is rejected
    try:
        if "." in body:
            main, fraction = body.split(".", 1)
            if not fraction or not fraction.isdigit():
                return False
            fraction = (fraction + "000000")[:6]
            parsed = datetime.strptime(f"{main}.{fraction}", "%Y-%m-%dT%H:%M:%S.%f")
        else:
            parsed = datetime.strptime(body, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return parsed.year >= 1970


def unwrap_document(
    payload: dict[str, Any],
    *,
    local_node_id: str,
    outer_message_id: str,
) -> tuple[str, bytes, str]:
    """Validate a wrapper and return ``(message_id, document_bytes, source_node_id)``.

    Fails closed: unknown members, wrong types, malformed base64, wrong media
    type, digest mismatch, wrong wrapper destination, or outer/wrapper message
    ID mismatch all raise ``NadiError``. Wrapper metadata is never authority.
    """
    if not isinstance(payload, dict):
        raise NadiError("wrapper payload is not an object")
    if set(payload.keys()) != WRAPPER_KEYS:
        raise NadiError(f"unknown or missing wrapper members: {sorted(set(payload) ^ WRAPPER_KEYS)}")
    message_id = payload["message_id"]
    if not isinstance(message_id, str) or not _valid_uuid(message_id):
        raise NadiError("wrapper message_id is not a canonical UUID")
    if message_id != outer_message_id:
        raise NadiError("outer relay message ID differs from wrapper message ID")
    source_node_id = payload["source_node_id"]
    destination_node_id = payload["destination_node_id"]
    if not isinstance(source_node_id, str) or not _NODE_ID_RE.match(source_node_id):
        raise NadiError("wrapper source_node_id is not a FAW node ID")
    if not isinstance(destination_node_id, str) or not _NODE_ID_RE.match(destination_node_id):
        raise NadiError("wrapper destination_node_id is not a FAW node ID")
    if destination_node_id != local_node_id:
        raise NadiError(f"wrapper destination {destination_node_id} != local FAW node {local_node_id}")
    if payload.get("media_type") != "application/faw+json":
        raise NadiError(f"wrong media_type {payload.get('media_type')!r}")
    if payload.get("encoding") != "base64url":
        raise NadiError(f"wrong encoding {payload.get('encoding')!r}")
    if payload.get("experimental") is not True:
        raise NadiError("experimental flag must be true")
    created_at = payload.get("created_at")
    if not _valid_timestamp(created_at):
        raise NadiError("wrapper created_at is not a valid UTC RFC3339 timestamp")
    encoded = payload.get("document")
    if not isinstance(encoded, str):
        raise NadiError("wrapper document is not a string")
    # Canonical base64url without padding: charset-only plus canonical round trip.
    if not _B64URL_RE.match(encoded) or "=" in encoded:
        raise NadiError("wrapper document is not canonical base64url")
    try:
        document_bytes = crypto.b64url_decode(encoded)
    except ValueError as exc:
        raise NadiError(f"malformed base64url document: {exc}") from exc
    if crypto.b64url_encode(document_bytes) != encoded:
        raise NadiError("wrapper document is not canonical base64url")
    declared_digest = payload.get("document_sha256")
    if not isinstance(declared_digest, str) or not _DIGEST_RE.match(declared_digest):
        raise NadiError("wrapper document_sha256 is not a sha256:<hex> digest")
    if declared_digest != canonical.digest_bytes(document_bytes):
        raise NadiError("wrapper document_sha256 does not match document bytes")
    return message_id, document_bytes, source_node_id


# ---------------------------------------------------------------------------
# Local durable state
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class NadiTransport(Transport):
    """FAW ``Transport`` over a Nadi-style relay with per-message state.

    ``node_id`` is the local FAW node ID. ``relay_address`` is the local outer
    mailbox address. ``routes`` maps remote FAW node IDs to remote relay
    addresses. ``backend`` performs relay publication and fetch; no default
    route exists.

    Outbox staging uses a commit marker: ``.msg`` + ``.meta`` + ``.ready``
    (last), so a crash can never make an incomplete record appear ready.
    """

    def __init__(
        self,
        state_root: Path,
        node_id: str,
        relay_address: str,
        routes: Mapping[str, str],
        backend: NadiRelayBackend,
    ) -> None:
        if not _NODE_ID_RE.match(node_id):
            raise ValueError(f"invalid FAW node_id {node_id!r}")
        self.state_root = Path(state_root)
        self.node_id = node_id
        self.relay_address = relay_address
        self.routes = dict(routes)
        self.backend = backend
        self.outbox_dir = self.state_root / "outbox"
        self.inbox_dir = self.state_root / "inbox"
        self.failed_dir = self.state_root / "failed"
        self.acknowledged_dir = self.state_root / "acknowledged"
        for directory in (self.outbox_dir, self.inbox_dir, self.failed_dir, self.acknowledged_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # -- Transport API -----------------------------------------------------

    def send(self, document: bytes, destination: str) -> TransportSendResult:
        # destination is a FAW node ID; resolve through local routes.
        relay_target = self.routes.get(destination)
        message_id = str(uuid.uuid4())
        if relay_target is None:
            # Fail closed: stage a complete but unrouted record; nothing is
            # published. The staged message is retained.
            self._stage(message_id, document, destination, relay_target=None)
            return TransportSendResult(
                message_id=message_id,
                ok=False,
                error=f"no route for FAW node {destination}",
            )
        self._stage(message_id, document, destination, relay_target=relay_target)
        wrapper = wrap_document(
            message_id=message_id,
            source_node_id=self.node_id,
            destination_node_id=destination,
            document_bytes=document,
        )
        envelope = RelayEnvelope(
            message_id=message_id,
            source_address=self.relay_address,
            destination_address=relay_target,
            operation=FAW_DOCUMENT_OPERATION,
            payload=wrapper,
        )
        results = self.backend.publish([envelope])
        # Exact correspondence: exactly one result total, for the attempted ID.
        if len(results) != 1 or results[0].message_id != message_id:
            return TransportSendResult(
                message_id=message_id,
                ok=False,
                error=f"invalid publish result set: {[r.message_id for r in results]}",
            )
        result = results[0]
        if result.ok:
            self._outbox_mark_delivered(message_id)
            return TransportSendResult(message_id=message_id, ok=True)
        return TransportSendResult(message_id=message_id, ok=False, error=result.error or "publish failed")

    def poll(self) -> list[TransportEnvelope]:
        for envelope in self.backend.fetch(self.relay_address):
            if envelope.operation != FAW_DOCUMENT_OPERATION:
                continue
            if self._is_suppressed(envelope.message_id):
                continue
            # Outer relay boundary: the envelope must be addressed to the local
            # relay address, with valid outer source/destination grammar.
            if envelope.destination_address != self.relay_address:
                self._record_failed(envelope.message_id,
                                    f"outer destination {envelope.destination_address!r} != local relay {self.relay_address!r}")
                continue
            if not _valid_relay_address(envelope.source_address) or not _valid_relay_address(envelope.destination_address):
                self._record_failed(envelope.message_id, "invalid outer relay address grammar")
                continue
            try:
                wrapper_message_id, document_bytes, source_node_id = unwrap_document(
                    envelope.payload,
                    local_node_id=self.node_id,
                    outer_message_id=envelope.message_id,
                )
            except NadiError as exc:
                self._record_failed(envelope.message_id, str(exc))
                self._record_relay_evidence(envelope.message_id, envelope)
                continue
            self._import(wrapper_message_id, envelope, document_bytes, source_node_id)
        return self._list_inbox()

    def ack(self, transport_message_id: str) -> None:
        # Durable tombstone BEFORE removing inbox data.
        _atomic_write(self.acknowledged_dir / f"{transport_message_id}.ack",
                      json.dumps({"message_id": transport_message_id}).encode())
        (self.inbox_dir / f"{transport_message_id}.msg").unlink(missing_ok=True)
        (self.inbox_dir / f"{transport_message_id}.meta").unlink(missing_ok=True)

    def nack(self, transport_message_id: str, reason: str) -> None:
        # Preserve actual failed evidence before removing inbox copies.
        msg = self.inbox_dir / f"{transport_message_id}.msg"
        meta = self.inbox_dir / f"{transport_message_id}.meta"
        if msg.exists():
            _atomic_write(self.failed_dir / f"{transport_message_id}.msg", msg.read_bytes())
        if meta.exists():
            _atomic_write(self.failed_dir / f"{transport_message_id}.meta", meta.read_bytes())
        _atomic_write(self.failed_dir / f"{transport_message_id}.nack",
                      json.dumps({"message_id": transport_message_id, "reason": reason}).encode())
        msg.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)

    # -- internal helpers ---------------------------------------------------

    def _stage(self, message_id: str, document: bytes, destination_faw: str, relay_target: str | None) -> None:
        meta = {
            "message_id": message_id,
            "destination_node_id": destination_faw,
            "relay_target": relay_target,
            "created_at": _now_utc_z(),
        }
        _atomic_write(self.outbox_dir / f"{message_id}.msg", document)
        _atomic_write(self.outbox_dir / f"{message_id}.meta", json.dumps(meta).encode())
        # Commit marker last: a crash before this leaves the record incomplete.
        _atomic_write(self.outbox_dir / f"{message_id}.ready", b"ready")

    def _outbox_mark_delivered(self, message_id: str) -> None:
        for suffix in (".msg", ".meta", ".ready"):
            (self.outbox_dir / f"{message_id}{suffix}").unlink(missing_ok=True)

    def _is_suppressed(self, message_id: str) -> bool:
        return (self.acknowledged_dir / f"{message_id}.ack").exists() or (
            self.failed_dir / f"{message_id}.nack"
        ).exists()

    def _record_failed(self, message_id: str, reason: str) -> None:
        _atomic_write(self.failed_dir / f"{message_id}.nack",
                      json.dumps({"message_id": message_id, "reason": reason}).encode())

    def _record_relay_evidence(self, message_id: str, envelope: RelayEnvelope) -> None:
        evidence = {
            "message_id": envelope.message_id,
            "source_address": envelope.source_address,
            "destination_address": envelope.destination_address,
            "operation": envelope.operation,
            "payload": envelope.payload,
        }
        _atomic_write(self.failed_dir / f"{message_id}.relay.json",
                      json.dumps(evidence).encode())

    def _import(self, message_id: str, envelope: RelayEnvelope, document_bytes: bytes, source_node_id: str) -> None:
        msg_path = self.inbox_dir / f"{message_id}.msg"
        if msg_path.exists():
            # Idempotent for identical bytes; integrity conflict otherwise.
            if msg_path.read_bytes() != document_bytes:
                self._quarantine_conflict(message_id, envelope, document_bytes, msg_path)
            return
        meta = {
            "message_id": message_id,
            "destination": self.node_id,
            "source": envelope.source_address,
            "wrapper_source_node_id": source_node_id,
        }
        _atomic_write(msg_path, document_bytes)
        _atomic_write(self.inbox_dir / f"{message_id}.meta", json.dumps(meta).encode())

    def _quarantine_conflict(self, message_id: str, envelope: RelayEnvelope, incoming: bytes, msg_path: Path) -> None:
        """Same ID with different bytes: quarantine inbox copies, preserve evidence."""
        reason = f"same message ID {message_id} with different bytes"
        self._record_failed(message_id, reason)
        # Preserve the original inbox bytes + metadata under failed/.
        _atomic_write(self.failed_dir / f"{message_id}.msg", msg_path.read_bytes())
        meta_path = msg_path.with_suffix(".meta")
        if meta_path.exists():
            _atomic_write(self.failed_dir / f"{message_id}.meta", meta_path.read_bytes())
        # Preserve the incoming conflicting envelope evidence.
        self._record_relay_evidence(message_id, envelope)
        # Remove the inbox copy: never expose the old message again.
        msg_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

    def _list_inbox(self) -> list[TransportEnvelope]:
        envelopes: list[TransportEnvelope] = []
        for path in sorted(self.inbox_dir.glob("*.msg")):
            if path.name.startswith(".tmp-"):
                continue
            message_id = path.stem
            # Fail-closed guard: acknowledged or failed IDs never surface.
            if self._is_suppressed(message_id):
                continue
            meta: dict[str, Any] = {}
            meta_path = path.with_suffix(".meta")
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                except json.JSONDecodeError:
                    pass
            envelopes.append(TransportEnvelope(
                message_id=message_id,
                document_bytes=path.read_bytes(),
                destination=meta.get("destination", self.node_id),
                source=meta.get("source"),
            ))
        return envelopes


def _valid_relay_address(address: str) -> bool:
    from .nadi_github import is_valid_relay_address

    return is_valid_relay_address(address)

"""Offline loopback/filesystem transport adapter (§11).

Design:
- ``send`` durably writes the document to the sender's outbox first (atomic
  tmp+rename), then attempts delivery into the destination node's inbox.
  On delivery failure the outbox copy is retained and the message is NOT
  acknowledged — partial multi-target failure preserves every undelivered
  message.
- ``poll`` lists the node's inbox; message IDs are durable UUIDs.
- ``ack`` removes exactly one inbox message. ``nack`` moves it to ``failed/``
  for inspection — it is never silently dropped.
- Duplicate delivery (e.g. the same message file copied twice) is expected and
  safe: the core replay store deduplicates before any handler runs.

No network access is used anywhere in this adapter.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .base import Transport, TransportEnvelope, TransportSendResult

__all__ = ["FilesystemTransport"]


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


class FilesystemTransport(Transport):
    """Loopback transport rooted at ``root``; each node owns inbox/outbox dirs."""

    def __init__(self, root: Path, node_id: str) -> None:
        self.root = Path(root)
        self.node_id = node_id
        safe = _safe_component(node_id)
        self.inbox_dir = self.root / safe / "inbox"
        self.outbox_dir = self.root / safe / "outbox"
        self.failed_dir = self.root / safe / "failed"
        for directory in (self.inbox_dir, self.outbox_dir, self.failed_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # -- helpers ----------------------------------------------------------

    def _outbox_dir_for(self, destination: str) -> Path:
        return self.outbox_dir / _safe_component(destination)

    def _envelope_from_file(self, path: Path) -> TransportEnvelope | None:
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        meta_path = path.with_suffix(".meta")
        meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return TransportEnvelope(
            message_id=path.stem,
            document_bytes=raw,
            destination=meta.get("destination", "unknown"),
            source=meta.get("source"),
        )

    # -- Transport API ----------------------------------------------------

    def send(self, document: bytes, destination: str) -> TransportSendResult:
        message_id = str(uuid.uuid4())
        outbox_path = self._outbox_dir_for(destination) / f"{message_id}.msg"
        try:
            _atomic_write(outbox_path, document)
            # Attempt delivery into the destination node's inbox.
            inbox_path = self.root / _safe_component(destination) / "inbox" / f"{message_id}.msg"
            _atomic_write(inbox_path, document)
            (self.root / _safe_component(destination) / "inbox" / f"{message_id}.meta").write_text(
                json.dumps({"message_id": message_id, "destination": destination, "source": self.node_id}) + "\n"
            )
            (self._outbox_dir_for(destination) / f"{message_id}.delivered").write_text(
                json.dumps({"message_id": message_id, "destination": destination}) + "\n"
            )
            outbox_path.unlink(missing_ok=True)
            return TransportSendResult(message_id=message_id, ok=True)
        except OSError as exc:
            # Outbox copy remains for retry; nothing was acknowledged.
            return TransportSendResult(message_id=message_id, ok=False, error=str(exc))

    def poll(self) -> list[TransportEnvelope]:
        envelopes: list[TransportEnvelope] = []
        try:
            entries = sorted(self.inbox_dir.iterdir(), key=lambda p: p.name)
        except FileNotFoundError:
            return envelopes
        for path in entries:
            if path.suffix != ".msg" or path.name.startswith(".tmp-"):
                continue
            envelope = self._envelope_from_file(path)
            if envelope is not None:
                envelopes.append(envelope)
        return envelopes

    def ack(self, transport_message_id: str) -> None:
        path = self.inbox_dir / f"{transport_message_id}.msg"
        path.unlink(missing_ok=True)

    def nack(self, transport_message_id: str, reason: str) -> None:
        source = self.inbox_dir / f"{transport_message_id}.msg"
        if not source.exists():
            return
        target = self.failed_dir / f"{transport_message_id}.msg"
        _atomic_write(target, source.read_bytes())
        (self.failed_dir / f"{transport_message_id}.meta").write_text(
            json.dumps({"message_id": transport_message_id, "reason": reason}) + "\n"
        )
        source.unlink(missing_ok=True)

    def _fail_count(self) -> int:
        return sum(1 for _ in self.failed_dir.glob("*.msg"))

    def _outbox_pending(self) -> int:
        return sum(len(list(d.glob("*.msg"))) for d in self.outbox_dir.glob("*") if d.is_dir())

    def state_summary(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "inbox_pending": len(self.poll()),
            "outbox_undelivered": self._outbox_pending(),
            "failed": self._fail_count(),
        }


def _safe_component(value: str) -> str:
    """Map an arbitrary destination to a filesystem-safe component."""
    return value.replace("/", "_").replace(":", "_")[:96]

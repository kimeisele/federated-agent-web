"""Receiver-side replay store (§9).

Persists one record per ``(issuer_node_id, attempt_id)`` with the compared
``delegation_digest`` and the attempt's current state. Creation is atomic
(O_EXCL), which is the admission gate: the same attempt is never admitted to a
handler twice within the persisted replay window.

Retention is the caller's policy; records should survive until ``expires_at``
plus a configurable clock-skew window.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .documents import now_utc_z

__all__ = ["ReplayRecord", "ReplayStore", "ReplayAlreadyAdmitted", "ReplayIntegrityViolation"]

STATE_PENDING = "pending"
STATE_EXECUTING = "executing"
STATE_TERMINAL = "terminal"


class ReplayAlreadyAdmitted(Exception):
    """The (issuer_node_id, attempt_id) record already exists."""

    def __init__(self, record: "ReplayRecord") -> None:
        super().__init__("attempt already admitted")
        self.record = record


class ReplayIntegrityViolation(Exception):
    """Same (issuer_node_id, attempt_id) but a different delegation digest."""


@dataclass
class ReplayRecord:
    issuer_node_id: str
    attempt_id: str
    delegation_digest: str
    state: str = STATE_PENDING
    receipt: dict[str, Any] | None = None
    created_at: str = field(default_factory=now_utc_z)
    updated_at: str = field(default_factory=now_utc_z)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issuer_node_id": self.issuer_node_id,
            "attempt_id": self.attempt_id,
            "delegation_digest": self.delegation_digest,
            "state": self.state,
            "receipt": self.receipt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReplayRecord":
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})


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


class ReplayStore:
    """Durable filesystem replay store keyed by (issuer_node_id, attempt_id)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _record_path(self, issuer_node_id: str, attempt_id: str) -> Path:
        safe = hashlib.sha256(issuer_node_id.encode("utf-8")).hexdigest()[:24]
        return self.root / safe / f"{attempt_id}.json"

    def get(self, issuer_node_id: str, attempt_id: str) -> ReplayRecord | None:
        path = self._record_path(issuer_node_id, attempt_id)
        if not path.is_file():
            return None
        return ReplayRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def create(self, issuer_node_id: str, attempt_id: str, delegation_digest: str) -> ReplayRecord:
        """Atomically create the admission record.

        Raises ``ReplayAlreadyAdmitted`` if the key already exists (callers
        then deduplicate or reject per §9); raises ``ReplayIntegrityViolation``
        if the existing record carries a different delegation digest.
        """
        record = ReplayRecord(
            issuer_node_id=issuer_node_id,
            attempt_id=attempt_id,
            delegation_digest=delegation_digest,
        )
        path = self._record_path(issuer_node_id, attempt_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record.to_dict(), indent=2).encode("utf-8")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            existing = self.get(issuer_node_id, attempt_id)
            if existing is None:
                raise ReplayAlreadyAdmitted(record) from exc
            if existing.delegation_digest != delegation_digest:
                raise ReplayIntegrityViolation(
                    f"attempt {attempt_id} replayed with digest {delegation_digest} "
                    f"but stored digest is {existing.delegation_digest}"
                ) from exc
            raise ReplayAlreadyAdmitted(existing) from exc
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def _update(self, record: ReplayRecord) -> ReplayRecord:
        record.updated_at = now_utc_z()
        _atomic_write(self._record_path(record.issuer_node_id, record.attempt_id),
                      json.dumps(record.to_dict(), indent=2).encode("utf-8"))
        return record

    def mark_executing(self, record: ReplayRecord) -> ReplayRecord:
        if record.state not in (STATE_PENDING, STATE_EXECUTING):
            raise ReplayIntegrityViolation(f"cannot start executing from {record.state}")
        record.state = STATE_EXECUTING
        return self._update(record)

    def attach_terminal(self, record: ReplayRecord, receipt: dict[str, Any]) -> ReplayRecord:
        if record.state == STATE_TERMINAL:
            return record
        record.state = STATE_TERMINAL
        record.receipt = receipt
        return self._update(record)

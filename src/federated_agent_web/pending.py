"""Issuer-side pending-delegation store (§11).

Receipt verification is stateful: the issuer records every delegation it
hands to a transport as outstanding, then accepts exactly one valid terminal
receipt per attempt, atomically. Unknown, non-outstanding, already-terminal,
digest-mismatched, or non-target-executor receipts are rejected.

The reference implementation provides a durable filesystem backend used by
the demo and tests; caller applications may choose another backend with the
same conceptual operations.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .documents import now_utc_z

__all__ = ["PendingDelegation", "PendingDelegationStore", "PendingStoreError"]

STATE_OUTSTANDING = "outstanding"
STATE_TERMINAL = "terminal"


class PendingStoreError(Exception):
    """A pending-store operation violated a binding or state invariant."""


@dataclass
class PendingDelegation:
    task_id: str
    attempt_id: str
    delegation_digest: str
    delegation: dict[str, Any]
    issuer_node_id: str
    target_node_id: str
    issued_at: str
    expires_at: str
    deadline: str
    state: str = STATE_OUTSTANDING
    terminal_receipt: dict[str, Any] | None = None
    created_at: str = field(default_factory=now_utc_z)
    updated_at: str = field(default_factory=now_utc_z)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "delegation_digest": self.delegation_digest,
            "delegation": self.delegation,
            "issuer_node_id": self.issuer_node_id,
            "target_node_id": self.target_node_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "deadline": self.deadline,
            "state": self.state,
            "terminal_receipt": self.terminal_receipt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingDelegation":
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


class PendingDelegationStore:
    """Durable issuer-side store of outstanding delegations.

    ``accept_terminal`` serializes competing receipt arrivals with an advisory
    lock so exactly one terminal receipt closes a record.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.root / ".lock"

    def _record_path(self, task_id: str, attempt_id: str) -> Path:
        return self.root / task_id / f"{attempt_id}.json"

    def register_outstanding(self, delegation: dict[str, Any], delegation_digest: str) -> None:
        """Persist a delegation as outstanding before it is handed to a transport."""
        body = delegation["body"]
        if body["issuer_node_id"] != delegation["issuer"]["node_id"]:
            raise PendingStoreError("body.issuer_node_id must equal envelope issuer.node_id")
        record = PendingDelegation(
            task_id=body["task_id"],
            attempt_id=body["attempt_id"],
            delegation_digest=delegation_digest,
            delegation=delegation,
            issuer_node_id=body["issuer_node_id"],
            target_node_id=body.get("target_node_id", ""),
            issued_at=delegation["issued_at"],
            expires_at=body["expires_at"],
            deadline=body["deadline"],
        )
        path = self._record_path(record.task_id, record.attempt_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise PendingStoreError(
                f"delegation {record.task_id}/{record.attempt_id} already registered"
            ) from exc
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(record.to_dict(), indent=2).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def get_outstanding(self, task_id: str, attempt_id: str) -> PendingDelegation | None:
        path = self._record_path(task_id, attempt_id)
        if not path.is_file():
            return None
        return PendingDelegation.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_outstanding(self) -> list[PendingDelegation]:
        records: list[PendingDelegation] = []
        for path in sorted(self.root.glob("*/[0-9a-f-]*.json")):
            if path.name.startswith(".tmp-"):
                continue
            try:
                records.append(PendingDelegation.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError):
                continue
        return records

    def accept_terminal(self, receipt: dict[str, Any]) -> PendingDelegation:
        """Atomically close the matching outstanding record with ``receipt``.

        Binding checks (§10): the receipt's task/attempt must exist and be
        outstanding, its ``delegation_digest`` must match the record, and its
        ``executor_node_id`` must equal the record's concrete target node.
        """
        body = receipt["body"]
        task_id, attempt_id = body["task_id"], body["attempt_id"]
        digest = body["delegation_digest"]
        executor = body["executor_node_id"]

        with open(self._lock_path, "a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                record = self.get_outstanding(task_id, attempt_id)
                if record is None:
                    raise PendingStoreError(f"receipt for unknown delegation {task_id}/{attempt_id}")
                if record.state != STATE_OUTSTANDING:
                    raise PendingStoreError(
                        f"delegation {task_id}/{attempt_id} is already {record.state}"
                    )
                if record.delegation_digest != digest:
                    raise PendingStoreError(
                        f"receipt digest {digest} does not match pending {record.delegation_digest}"
                    )
                if executor != record.target_node_id:
                    raise PendingStoreError(
                        f"receipt executor {executor} != target {record.target_node_id}"
                    )
                record.state = STATE_TERMINAL
                record.terminal_receipt = receipt
                record.updated_at = now_utc_z()
                _atomic_write(
                    self._record_path(task_id, attempt_id),
                    json.dumps(record.to_dict(), indent=2).encode("utf-8"),
                )
                return record
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

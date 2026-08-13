"""Runtime-neutral capability execution seam.

Executors return plain results.  FAW remains responsible for delegation
verification, replay admission, receipt binding, and receipt signatures.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .canonical import digest_bytes
from .documents import KIND_RECEIPT, content_digest_of
from .identity import NodeIdentity

__all__ = [
    "CapabilityExecutorProtocol",
    "ExecutionRegistry",
    "HashFileExecutor",
    "RuntimeResult",
    "default_execution_registry",
    "receipt_from_result",
]


def _ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RuntimeResult:
    """Provider- and protocol-neutral terminal execution result."""

    status: str
    started_at: datetime
    finished_at: datetime
    artifacts: tuple[dict[str, Any], ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)
    failure: dict[str, Any] | None = None
    evidence: tuple[dict[str, Any], ...] = ()


class CapabilityExecutorProtocol(Protocol):
    def execute(self, delegation: dict[str, Any], workdir: Path) -> RuntimeResult: ...


class ExecutionRegistry:
    """Exact capability-to-executor mapping; unknown names never fall back."""

    def __init__(self, executors: dict[str, CapabilityExecutorProtocol] | None = None) -> None:
        self._executors = dict(executors or {})

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(self._executors)

    def register(self, capability: str, executor: CapabilityExecutorProtocol) -> None:
        if not capability or capability in self._executors:
            raise ValueError(f"capability already registered or empty: {capability!r}")
        self._executors[capability] = executor

    def execute(self, delegation: dict[str, Any], workdir: Path) -> RuntimeResult:
        capability = delegation["body"]["capability"]
        executor = self._executors.get(capability)
        if executor is None:
            raise ValueError(f"no executor registered for capability {capability!r}")
        return executor.execute(delegation, workdir)


class HashFileExecutor:
    """Deterministic reference capability, independent of receipt signing."""

    def __init__(self, executor_node_id: str, now_fn=None) -> None:
        self.executor_node_id = executor_node_id
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def execute(self, delegation: dict[str, Any], workdir: Path) -> RuntimeResult:
        body = delegation["body"]
        started = self.now_fn()
        deadline = datetime.fromisoformat(body["deadline"].replace("Z", "+00:00"))
        if started > deadline:
            return RuntimeResult("timed_out", started, started)

        input_refs = body["input"].get("refs", [])
        if not input_refs:
            raise ValueError("hash_file capability requires refs input")
        location = Path(input_refs[0]["location"])
        read_paths = [
            Path(path).resolve()
            for path in body["authority"].get("filesystem_scope", {}).get("read_paths", [])
        ]
        if read_paths and location.resolve() not in read_paths:
            raise ValueError(f"input {location} outside declared filesystem read scope")
        data = location.read_bytes()
        actual_digest = digest_bytes(data)
        if actual_digest != input_refs[0]["digest"]:
            raise ValueError(f"input digest mismatch: {actual_digest} != {input_refs[0]['digest']}")

        payload = {
            "capability": "hash_file",
            "task_id": body["task_id"],
            "attempt_id": body["attempt_id"],
            "input": str(location),
            "input_digest": actual_digest,
            "input_size": len(data),
            "executor": self.executor_node_id,
        }
        artifact_bytes = json.dumps(payload, indent=2).encode("utf-8")
        artifact_path = workdir / "artifacts" / f"{body['task_id']}-{body['attempt_id']}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(artifact_bytes)
        finished = self.now_fn()
        return RuntimeResult(
            status="succeeded",
            started_at=started,
            finished_at=finished,
            artifacts=({
                "name": "result.json",
                "media_type": "application/json",
                "digest": digest_bytes(artifact_bytes),
                "size": len(artifact_bytes),
                "location": str(artifact_path),
            },),
            usage={
                "wall_seconds": max(0.0, (finished - started).total_seconds()),
                "output_bytes": len(artifact_bytes),
            },
        )


def default_execution_registry(node: NodeIdentity, *, now_fn=None) -> ExecutionRegistry:
    return ExecutionRegistry({"hash_file": HashFileExecutor(node.node_id, now_fn=now_fn)})


def receipt_from_result(
    node: NodeIdentity,
    delegation: dict[str, Any],
    result: RuntimeResult,
) -> dict[str, Any]:
    """Bind a neutral result to one delegation and sign the terminal receipt."""
    body = delegation["body"]
    receipt_body: dict[str, Any] = {
        "receipt_id": str(uuid.uuid4()),
        "task_id": body["task_id"],
        "attempt_id": body["attempt_id"],
        "delegation_digest": content_digest_of(delegation),
        "executor_node_id": node.node_id,
        "status": result.status,
        "started_at": _ts(result.started_at),
        "finished_at": _ts(result.finished_at),
        "artifacts": list(result.artifacts),
        "usage": dict(result.usage),
    }
    if result.failure is not None:
        receipt_body["failure"] = dict(result.failure)
    if result.evidence:
        receipt_body["evidence"] = list(result.evidence)
    return node.sign_document(KIND_RECEIPT, receipt_body)


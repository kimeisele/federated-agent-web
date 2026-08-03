"""Shared fixtures and builders for the conformance suite."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from federated_agent_web import canonical
from federated_agent_web.canonical import digest_bytes
from federated_agent_web.documents import (
    KIND_DELEGATION,
    KIND_RECEIPT,
    content_digest_of,
)
from federated_agent_web.identity import NodeIdentity
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.replay import ReplayStore
from federated_agent_web.transports import FilesystemTransport
from federated_agent_web.verify import PinnedManifestTrustContext, VerificationPolicy

CAPABILITY = "hash_file"


def now() -> datetime:
    return datetime.now(timezone.utc)


def ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_node_pair() -> tuple[NodeIdentity, NodeIdentity]:
    issuer = NodeIdentity.create(display_name="Issuer A", capabilities=[CAPABILITY, "compute.thing"])
    executor = NodeIdentity.create(display_name="Executor B", capabilities=[CAPABILITY])
    return issuer, executor


def trust_for(node: NodeIdentity, pinned_at: datetime | None = None) -> PinnedManifestTrustContext:
    return PinnedManifestTrustContext.from_chain(node.manifests, pinned_at=pinned_at)


def build_delegation(
    issuer: NodeIdentity,
    *,
    target_node_id: str,
    capability: str = CAPABILITY,
    task_id: str | None = None,
    attempt_id: str | None = None,
    issued_at: str | None = None,
    expires_at: str | None = None,
    deadline: str | None = None,
    budget: dict[str, Any] | None = None,
    authority: dict[str, Any] | None = None,
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = now()
    expires = expires_at or ts(base + timedelta(seconds=600))
    deadline = deadline or ts(base + timedelta(seconds=1200))
    if budget is None:
        budget = {"max_wall_seconds": 60, "max_output_bytes": 8192}
    if authority is None:
        authority = {
            "actions": [capability],
            "external_effect_scope": {"allowed_effects": ["none"]},
            "expiry": ts(base + timedelta(seconds=7200)),
        }
    if input_data is None:
        input_data = {"kind": "inline", "data": {"seed": "conformance"}}
    body = {
        "task_id": task_id or str(uuid.uuid4()),
        "attempt_id": attempt_id or str(uuid.uuid4()),
        "issuer_node_id": issuer.node_id,
        "target_node_id": target_node_id,
        "capability": capability,
        "input": input_data,
        "authority": authority,
        "budget": budget,
        "deadline": deadline,
        "expected_output": {
            "kind": "artifact",
            "media_type": "application/json",
            "required_artifacts": ["result.json"],
            "expects_repository_mutation": False,
        },
        "expires_at": expires,
    }
    return issuer.sign_document(KIND_DELEGATION, body)


def build_receipt(
    executor: NodeIdentity,
    delegation: dict[str, Any],
    *,
    status: str = "succeeded",
    delegation_digest: str | None = None,
    executor_node_id: str | None = None,
    task_id: str | None = None,
    attempt_id: str | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = delegation["body"]
    started = now()
    artifact = b'{"result": "ok"}\n'
    if artifacts is None:
        artifacts = [
            {
                "name": "result.json",
                "media_type": "application/json",
                "digest": digest_bytes(artifact),
                "size": len(artifact),
                "location": "mem://result.json",
            }
        ]
    receipt_body = {
        "receipt_id": str(uuid.uuid4()),
        "task_id": task_id or body["task_id"],
        "attempt_id": attempt_id or body["attempt_id"],
        "delegation_digest": delegation_digest or content_digest_of(delegation),
        "executor_node_id": executor_node_id or executor.node_id,
        "status": status,
        "started_at": ts(started),
        "finished_at": ts(started + timedelta(seconds=1)),
        "artifacts": artifacts,
        "usage": usage or {"wall_seconds": 0.5, "output_bytes": len(artifact)},
    }
    if status in ("failed", "rejected", "timed_out"):
        receipt_body["failure"] = {"code": "demo.failure", "message": "deterministic failure"}
    return executor.sign_document(KIND_RECEIPT, receipt_body)


@pytest.fixture
def node_pair() -> tuple[NodeIdentity, NodeIdentity]:
    return make_node_pair()


@pytest.fixture
def issuer(node_pair: tuple[NodeIdentity, NodeIdentity]) -> NodeIdentity:
    return node_pair[0]


@pytest.fixture
def executor(node_pair: tuple[NodeIdentity, NodeIdentity]) -> NodeIdentity:
    return node_pair[1]


@pytest.fixture
def transport_pair(tmp_path: Path) -> tuple[FilesystemTransport, FilesystemTransport]:
    issuer, executor = make_node_pair()
    root = tmp_path / "transport"
    return (
        FilesystemTransport(root, issuer.node_id),
        FilesystemTransport(root, executor.node_id),
    )

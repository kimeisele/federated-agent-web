"""Adapter-neutral transport conformance harness (v0.4).

The shared property tests in ``test_transport_contract.py`` run against every
registered transport case. Adapter-specific setup and fault injection live in
the harness objects below; shared test bodies never branch on the adapter
name.

Registering a future Nadi/GitHub adapter means adding its harness to
``TRANSPORT_CASES`` — nothing in the shared tests changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from federated_agent_web.identity import NodeIdentity
from federated_agent_web.transports import FilesystemTransport
from federated_agent_web.transports.base import TransportEnvelope

TRANSPORT_CASES: list[Any] = []


@dataclass
class TransportPair:
    """Sender + receiver transports plus the identities they operate for."""

    sender: Any
    receiver: Any
    issuer: NodeIdentity
    executor: NodeIdentity
    transport_root: Path


class TransportHarness:
    """Adapter-neutral interface for shared property tests.

    A concrete harness provides create_pair and fault-injection helpers; the
    shared tests use only this interface plus the public ``Transport`` API
    (``send``/``poll``/``ack``/``nack``) and the core verification stores.
    """

    name: str = "abstract"

    def create_pair(self, tmp_path: Path) -> TransportPair:
        raise NotImplementedError

    def force_delivery_failure(self, pair: TransportPair, destination: str) -> None:
        raise NotImplementedError

    def pending_outbox_ids(self, pair: TransportPair) -> list[str]:
        raise NotImplementedError

    def duplicate_inbound(self, pair: TransportPair, envelope: TransportEnvelope) -> None:
        raise NotImplementedError

    def failed_message_ids(self, pair: TransportPair) -> list[str]:
        raise NotImplementedError

    def set_source_metadata(self, pair: TransportPair, envelope: TransportEnvelope, new_source: str) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Filesystem harness
# ---------------------------------------------------------------------------


class FilesystemTransportHarness(TransportHarness):
    """Harness for ``FilesystemTransport``.

    Fault injection and evidence inspection use filesystem state; the shared
    property tests remain adapter-neutral.
    """

    name = "filesystem"

    def create_pair(self, tmp_path: Path) -> TransportPair:
        issuer = NodeIdentity.create(display_name="Contract Issuer", capabilities=["hash_file"])
        executor = NodeIdentity.create(display_name="Contract Executor", capabilities=["hash_file"])
        root = tmp_path / "transport"
        sender = FilesystemTransport(root, issuer.node_id)
        receiver = FilesystemTransport(root, executor.node_id)
        return TransportPair(sender=sender, receiver=receiver, issuer=issuer,
                             executor=executor, transport_root=root)

    def force_delivery_failure(self, pair: TransportPair, destination: str) -> None:
        from federated_agent_web.transports.filesystem import _safe_component

        blocked = pair.transport_root / _safe_component(destination)
        blocked.parent.mkdir(parents=True, exist_ok=True)
        blocked.write_text("i am a file, not a directory")

    def pending_outbox_ids(self, pair: TransportPair) -> list[str]:
        ids: list[str] = []
        for outbox_dir in (pair.transport_root / _safe(pair.sender.node_id) / "outbox").glob("*"):
            if outbox_dir.is_dir():
                ids.extend(p.stem for p in outbox_dir.glob("*.msg"))
        return ids

    def duplicate_inbound(self, pair: TransportPair, envelope: TransportEnvelope) -> None:
        import shutil

        source = pair.transport_root / _safe(pair.receiver.node_id) / "inbox" / f"{envelope.message_id}.msg"
        target = pair.transport_root / _safe(pair.receiver.node_id) / "inbox" / f"dup-{envelope.message_id}.msg"
        shutil.copy(source, target)

    def failed_message_ids(self, pair: TransportPair) -> list[str]:
        failed_dir = pair.transport_root / _safe(pair.receiver.node_id) / "failed"
        return [p.stem for p in failed_dir.glob("*.msg")] if failed_dir.exists() else []

    def set_source_metadata(self, pair: TransportPair, envelope: TransportEnvelope, new_source: str) -> None:
        import json

        meta_path = pair.transport_root / _safe(pair.receiver.node_id) / "inbox" / f"{envelope.message_id}.meta"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            meta["source"] = new_source
            meta_path.write_text(json.dumps(meta))


def _safe(value: str) -> str:
    from federated_agent_web.transports.filesystem import _safe_component

    return _safe_component(value)


filesystem_transport_case = FilesystemTransportHarness()
TRANSPORT_CASES.append(filesystem_transport_case)

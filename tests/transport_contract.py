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


# ---------------------------------------------------------------------------
# Nadi/GitHub stub harness
# ---------------------------------------------------------------------------


class StubRelayBackend:
    """In-memory relay mailbox: per-destination queues plus failure injection.

    Mirrors the GitHub mailbox model: messages persist in destination queues,
    rereading returns the same contents, and a per-destination failure switch
    makes publication fail for exactly that destination.
    """

    def __init__(self) -> None:
        self.mailboxes: dict[str, list] = {}
        self.blocked: set[str] = set()

    def block(self, destination_address: str) -> None:
        self.blocked.add(destination_address)

    def publish(self, envelopes: list) -> list:
        from federated_agent_web.transports.nadi import RelayPublishResult

        results = []
        for envelope in envelopes:
            if envelope.destination_address in self.blocked:
                results.append(RelayPublishResult(
                    message_id=envelope.message_id, ok=False, error="destination blocked"))
                continue
            self.mailboxes.setdefault(envelope.destination_address, []).append(envelope)
            results.append(RelayPublishResult(message_id=envelope.message_id, ok=True))
        return results

    def fetch(self, destination_address: str) -> list:
        return list(self.mailboxes.get(destination_address, []))


class NadiStubTransportHarness(TransportHarness):
    """Harness for ``NadiTransport`` with a stub relay backend.

    Relay addresses are deliberately distinct from FAW node IDs, proving the
    transport does not require FAW identity == relay address.
    """

    name = "nadi-stub"

    def create_pair(self, tmp_path: Path) -> TransportPair:
        from federated_agent_web.transports.nadi import NadiTransport

        issuer = NodeIdentity.create(display_name="Nadi Issuer", capabilities=["hash_file"])
        executor = NodeIdentity.create(display_name="Nadi Executor", capabilities=["hash_file"])
        backend = StubRelayBackend()
        issuer_relay = "relay-" + issuer.node_id.replace("urn:faw:", "")[:32]
        executor_relay = "relay-" + executor.node_id.replace("urn:faw:", "")[:32]
        sender = NadiTransport(
            state_root=tmp_path / "nadi-state-sender",
            node_id=issuer.node_id,
            relay_address=issuer_relay,
            routes={executor.node_id: executor_relay},
            backend=backend,
        )
        receiver = NadiTransport(
            state_root=tmp_path / "nadi-state-receiver",
            node_id=executor.node_id,
            relay_address=executor_relay,
            routes={issuer.node_id: issuer_relay},
            backend=backend,
        )
        pair = TransportPair(sender=sender, receiver=receiver, issuer=issuer,
                             executor=executor, transport_root=tmp_path)
        pair.sender_state = tmp_path / "nadi-state-sender"
        pair.receiver_state = tmp_path / "nadi-state-receiver"
        pair.backend = backend
        pair.routes = {issuer.node_id: issuer_relay, executor.node_id: executor_relay}
        return pair

    def force_delivery_failure(self, pair: TransportPair, destination: str) -> None:
        # If the destination has no route, send() already fails closed and
        # retains the staged message; otherwise block the relay destination.
        if destination in pair.routes:
            pair.backend.block(pair.routes[destination])

    def pending_outbox_ids(self, pair: TransportPair) -> list[str]:
        outbox = pair.sender_state / "outbox"
        return [p.stem for p in outbox.glob("*.msg")] if outbox.exists() else []

    def duplicate_inbound(self, pair: TransportPair, envelope) -> None:
        import shutil

        inbox = pair.receiver_state / "inbox"
        source = inbox / f"{envelope.message_id}.msg"
        target = inbox / f"dup-{envelope.message_id}.msg"
        shutil.copy(source, target)

    def failed_message_ids(self, pair: TransportPair) -> list[str]:
        failed = pair.receiver_state / "failed"
        return [p.stem for p in failed.glob("*.nack")] if failed.exists() else []

    def set_source_metadata(self, pair: TransportPair, envelope, new_source: str) -> None:
        import json as _json

        meta_path = pair.receiver_state / "inbox" / f"{envelope.message_id}.meta"
        if meta_path.exists():
            meta = _json.loads(meta_path.read_text())
            meta["source"] = new_source
            meta_path.write_text(_json.dumps(meta))


nadi_stub_transport_case = NadiStubTransportHarness()
TRANSPORT_CASES.append(nadi_stub_transport_case)


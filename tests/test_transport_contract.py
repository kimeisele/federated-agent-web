"""Shared adapter-neutral transport conformance properties (v0.4).

Each test runs against every case in ``TRANSPORT_CASES`` via the
``transport_case`` fixture. The test bodies use only the public ``Transport``
API (``send``/``poll``/``ack``/``nack``), the harness interface, and the core
verification stores. No test branches on the adapter name.

The future Nadi/GitHub adapter is added by appending its harness to
``TRANSPORT_CASES`` in ``transport_contract.py``; these properties then run
against it unchanged.
"""

from __future__ import annotations

import uuid

import pytest

from federated_agent_web import canonical
from federated_agent_web.documents import (
    KIND_DELEGATION,
    KIND_RECEIPT,
    content_digest_of,
)
from federated_agent_web.pending import PendingDelegationStore
from federated_agent_web.replay import ReplayStore
from federated_agent_web.verify import VerificationPolicy, verify

from .conftest import build_delegation, now, trust_for
from .transport_contract import TRANSPORT_CASES


@pytest.fixture(params=TRANSPORT_CASES, ids=lambda case: case.name)
def transport_case(request):
    return request.param


def _policy():
    return VerificationPolicy(
        allowed_actions={"hash_file"},
        allowed_external_effects=frozenset({"none"}),
    )


class TestSharedTransportContract:
    def test_exact_document_bytes(self, transport_case, tmp_path):
        """send() → poll() returns byte-for-byte identical document bytes."""
        pair = transport_case.create_pair(tmp_path)
        payload = b"exact bytes \x00\x01\xff payload\n"
        result = pair.sender.send(payload, pair.executor.node_id)
        assert result.ok, result.error
        envelopes = pair.receiver.poll()
        assert len(envelopes) == 1
        assert envelopes[0].document_bytes == payload

    def test_envelope_routing(self, transport_case, tmp_path):
        """Destination preserved; source exposed; message ID non-empty and stable."""
        pair = transport_case.create_pair(tmp_path)
        result = pair.sender.send(b"routing test", pair.executor.node_id)
        assert result.ok
        envelopes = pair.receiver.poll()
        assert len(envelopes) == 1
        envelope = envelopes[0]
        # The public Transport destination is the FAW node ID passed to send().
        assert envelope.destination == pair.executor.node_id
        # Transport provenance is untrusted and must not be required to equal
        # FAW identity; it only has to be present and stable.
        assert envelope.source is not None
        assert envelope.source != ""
        assert envelope.message_id
        # Stable across polls.
        repolled = pair.receiver.poll()[0]
        assert repolled.message_id == envelope.message_id
        assert repolled.source == envelope.source

    def test_distinct_message_ids(self, transport_case, tmp_path):
        """Two sends produce two different transport message IDs."""
        pair = transport_case.create_pair(tmp_path)
        first = pair.sender.send(b"one", pair.executor.node_id)
        second = pair.sender.send(b"two", pair.executor.node_id)
        assert first.ok and second.ok
        assert first.message_id != second.message_id

    def test_per_message_ack(self, transport_case, tmp_path):
        """Acknowledging one message does not remove another."""
        pair = transport_case.create_pair(tmp_path)
        pair.sender.send(b"first", pair.executor.node_id)
        pair.sender.send(b"second", pair.executor.node_id)
        envelopes = pair.receiver.poll()
        assert len(envelopes) == 2
        pair.receiver.ack(envelopes[0].message_id)
        remaining = pair.receiver.poll()
        assert len(remaining) == 1
        assert remaining[0].message_id == envelopes[1].message_id

    def test_per_message_nack(self, transport_case, tmp_path):
        """Nacking one message preserves failed evidence; another is unaffected."""
        pair = transport_case.create_pair(tmp_path)
        pair.sender.send(b"bad", pair.executor.node_id)
        pair.sender.send(b"good", pair.executor.node_id)
        envelopes = pair.receiver.poll()
        assert len(envelopes) == 2
        pair.receiver.nack(envelopes[0].message_id, "test failure")
        failed = transport_case.failed_message_ids(pair)
        assert envelopes[0].message_id in failed
        remaining = pair.receiver.poll()
        assert [e.message_id for e in remaining] == [envelopes[1].message_id]

    def test_partial_destination_failure(self, transport_case, tmp_path):
        """One successful delivery stays successful; one failed stays pending by ID."""
        pair = transport_case.create_pair(tmp_path)
        transport_case.force_delivery_failure(pair, "urn:faw:blocked-node-0001")
        good = pair.sender.send(b"good", pair.executor.node_id)
        bad = pair.sender.send(b"bad", "urn:faw:blocked-node-0001")
        assert good.ok, good.error
        assert not bad.ok
        # Success for one message never clears another.
        assert len(pair.receiver.poll()) == 1
        # The failed message remains pending in the sender's outbox by its ID.
        pending_ids = transport_case.pending_outbox_ids(pair)
        assert bad.message_id in pending_ids

    def test_duplicate_delivery_at_most_once(self, transport_case, tmp_path):
        """Duplicate transport delivery is observable; replay allows one admission."""
        pair = transport_case.create_pair(tmp_path)
        delegation = build_delegation(pair.issuer, target_node_id=pair.executor.node_id)
        payload = canonical.canonical_bytes(delegation)
        result = pair.sender.send(payload, pair.executor.node_id)
        assert result.ok
        envelopes = pair.receiver.poll()
        assert len(envelopes) == 1
        transport_case.duplicate_inbound(pair, envelopes[0])

        replay = ReplayStore(tmp_path / "replay")
        admissions = 0
        for envelope in pair.receiver.poll():
            v = verify(
                envelope.document_bytes,
                expected_kind=KIND_DELEGATION,
                local_node_id=pair.executor.node_id,
                trust_context=trust_for(pair.issuer),
                local_policy=_policy(),
                now=now(),
                replay_store=replay,
            )
            assert v.ok, v.reason
            if v.admitted:
                admissions += 1
            pair.receiver.ack(envelope.message_id)
        assert admissions == 1

    def test_malformed_input_never_reaches_handler(self, transport_case, tmp_path):
        """Malformed bytes reach the inbox but never admit to a handler."""
        pair = transport_case.create_pair(tmp_path)
        result = pair.sender.send(b"not a signed delegation", pair.executor.node_id)
        assert result.ok
        replay = ReplayStore(tmp_path / "replay")
        admissions = 0
        for envelope in pair.receiver.poll():
            v = verify(
                envelope.document_bytes,
                expected_kind=KIND_DELEGATION,
                local_node_id=pair.executor.node_id,
                trust_context=trust_for(pair.issuer),
                local_policy=_policy(),
                now=now(),
                replay_store=replay,
            )
            assert not v.ok
            if v.admitted:
                admissions += 1
            pair.receiver.ack(envelope.message_id)
        assert admissions == 0

    def test_delegation_receipt_round_trip(self, transport_case, tmp_path):
        """Signed delegation and receipt travel and verify through the core."""
        from federated_agent_web.canonical import digest_bytes
        from federated_agent_web.demo import CapabilityExecutor

        pair = transport_case.create_pair(tmp_path)
        workdir = tmp_path / "work"
        input_path = workdir / "input.bin"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(b"round trip input\n")

        # Build the delegation with the file ref upfront so the executor can
        # hash it.
        from datetime import timedelta

        base = now()
        future = lambda secs: (base + timedelta(seconds=secs)).isoformat().replace("+00:00", "Z")
        body = {
            "task_id": str(uuid.uuid4()),
            "attempt_id": str(uuid.uuid4()),
            "issuer_node_id": pair.issuer.node_id,
            "target_node_id": pair.executor.node_id,
            "capability": "hash_file",
            "input": {
                "kind": "refs",
                "refs": [{"digest": digest_bytes(b"round trip input\n"), "location": str(input_path)}],
            },
            "authority": {
                "actions": ["hash_file"],
                "filesystem_scope": {"read_paths": [str(input_path)]},
                "external_effect_scope": {"allowed_effects": ["none"]},
                "expiry": future(3600),
            },
            "budget": {"max_wall_seconds": 60, "max_output_bytes": 8192},
            "deadline": future(1200),
            "expected_output": {
                "kind": "artifact",
                "media_type": "application/json",
                "required_artifacts": ["result.json"],
                "expects_repository_mutation": False,
            },
            "expires_at": future(600),
        }
        delegation = pair.issuer.sign_document(KIND_DELEGATION, body)
        digest = content_digest_of(delegation)
        pending = PendingDelegationStore(tmp_path / "pending")
        pending.register_outstanding(delegation, digest)

        # Issuer -> executor
        result = pair.sender.send(canonical.canonical_bytes(delegation), pair.executor.node_id)
        assert result.ok
        envelopes = pair.receiver.poll()
        assert len(envelopes) == 1
        replay = ReplayStore(tmp_path / "replay")
        admission = verify(
            envelopes[0].document_bytes,
            expected_kind=KIND_DELEGATION,
            local_node_id=pair.executor.node_id,
            trust_context=trust_for(pair.issuer),
            local_policy=_policy(),
            now=now(),
            replay_store=replay,
        )
        assert admission.ok and admission.admitted, admission.reason
        pair.receiver.ack(envelopes[0].message_id)

        # Executor signs and sends a terminal receipt back.
        executor = CapabilityExecutor(pair.executor)
        receipt = executor.execute(delegation, workdir)
        result = pair.receiver.send(canonical.canonical_bytes(receipt), pair.issuer.node_id)
        assert result.ok

        received = pair.sender.poll()
        assert len(received) == 1
        acceptance = verify(
            received[0].document_bytes,
            expected_kind=KIND_RECEIPT,
            local_node_id=pair.issuer.node_id,
            trust_context=trust_for(pair.executor),
            local_policy=VerificationPolicy(),
            now=now(),
            pending_store=pending,
        )
        assert acceptance.ok, acceptance.reason
        record = pending.get_outstanding(body["task_id"], body["attempt_id"])
        assert record.state == "terminal"

    def test_no_transport_authority(self, transport_case, tmp_path):
        """Changing transport source metadata never changes document authority."""
        pair = transport_case.create_pair(tmp_path)
        delegation = build_delegation(pair.issuer, target_node_id=pair.executor.node_id)
        payload = canonical.canonical_bytes(delegation)
        result = pair.sender.send(payload, pair.executor.node_id)
        assert result.ok
        envelope = pair.receiver.poll()[0]
        original_source = envelope.source

        transport_case.set_source_metadata(pair, envelope, "urn:faw:evil-node-9999")
        re_polled = pair.receiver.poll()[0]
        assert re_polled.source != original_source
        # Document bytes unchanged; verification outcome identical.
        assert re_polled.document_bytes == payload
        v = verify(
            re_polled.document_bytes,
            expected_kind=KIND_DELEGATION,
            local_node_id=pair.executor.node_id,
            trust_context=trust_for(pair.issuer),
            local_policy=_policy(),
            now=now(),
        )
        assert v.ok, v.reason
        # A tampered document still fails regardless of transport source.
        pair.sender.send(b"tampered\x00", pair.executor.node_id)
        tampered = [e for e in pair.receiver.poll() if e.document_bytes != payload][0]
        v2 = verify(
            tampered.document_bytes,
            expected_kind=KIND_DELEGATION,
            local_node_id=pair.executor.node_id,
            trust_context=trust_for(pair.issuer),
            local_policy=_policy(),
            now=now(),
        )
        assert not v2.ok

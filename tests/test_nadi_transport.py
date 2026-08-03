"""Focused tests for the experimental Nadi/GitHub transport adapter (v0.4).

All tests use a stub relay backend or a fake GitHub client — no real network,
no `gh` invocation. The shared adapter-neutral suite lives in
``test_transport_contract.py``; these tests cover Nadi-specific behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from federated_agent_web import canonical
from federated_agent_web.crypto import b64url_encode
from federated_agent_web.identity import NodeIdentity
from federated_agent_web.transports import (
    FilesystemTransport,
    GitHubNadiRelayBackend,
    NadiTransport,
)
from federated_agent_web.transports.nadi import (
    NadiError,
    wrap_document,
    unwrap_document,
)
from federated_agent_web.transports.nadi_github import is_valid_relay_address

from .transport_contract import StubRelayBackend


def _make_pair(tmp_path: Path):
    issuer = NodeIdentity.create(display_name="Nadi Issuer", capabilities=["hash_file"])
    executor = NodeIdentity.create(display_name="Nadi Executor", capabilities=["hash_file"])
    backend = StubRelayBackend()
    issuer_relay = "relay-issuer-" + issuer.node_id[-8:]
    executor_relay = "relay-executor-" + executor.node_id[-8:]
    sender = NadiTransport(
        state_root=tmp_path / "sender-state",
        node_id=issuer.node_id,
        relay_address=issuer_relay,
        routes={executor.node_id: executor_relay},
        backend=backend,
    )
    receiver = NadiTransport(
        state_root=tmp_path / "receiver-state",
        node_id=executor.node_id,
        relay_address=executor_relay,
        routes={issuer.node_id: issuer_relay},
        backend=backend,
    )
    return sender, receiver, issuer, executor, backend, issuer_relay, executor_relay


class TestIdentityAndRouting:
    def test_faw_id_distinct_from_relay_address(self, tmp_path):
        sender, _r, issuer, _e, _b, issuer_relay, _er = _make_pair(tmp_path)
        assert issuer.node_id != issuer_relay
        assert sender.relay_address == issuer_relay
        assert sender.node_id == issuer.node_id

    def test_explicit_route_resolution(self, tmp_path):
        sender, _r, issuer, executor, _b, _ir, executor_relay = _make_pair(tmp_path)
        assert sender.routes[executor.node_id] == executor_relay
        assert executor_relay != executor.node_id

    def test_missing_route_retains_staged_message(self, tmp_path):
        sender, _r, issuer, _e, _b, _ir, _er = _make_pair(tmp_path)
        result = sender.send(b"staged", "urn:faw:no-such-route")
        assert not result.ok
        assert "no route" in result.error
        staged = list((tmp_path / "sender-state" / "outbox").glob("*.msg"))
        assert len(staged) == 1
        assert staged[0].stem == result.message_id

    def test_wrong_wrapper_destination_quarantined(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayEnvelope

        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        wrapper = wrap_document(
            message_id="11111111-1111-4111-8111-111111111111",
            source_node_id=issuer.node_id,
            destination_node_id="urn:faw:someone-else",
            document_bytes=b"x",
        )
        envelope = RelayEnvelope(
            message_id="11111111-1111-4111-8111-111111111111",
            source_address=_ir,
            destination_address=executor_relay,
            operation="faw.document",
            payload=wrapper,
        )
        backend.mailboxes.setdefault(executor_relay, []).append(envelope)
        assert receiver.poll() == []  # quarantined, not imported
        failed = list((tmp_path / "receiver-state" / "failed").glob("*.nack"))
        assert len(failed) == 1


class TestWrapperValidation:
    def _payload(self, **overrides):
        payload = wrap_document(
            message_id="22222222-2222-4222-8222-222222222222",
            source_node_id="urn:faw:issuer-0001",
            destination_node_id="urn:faw:local-node-0001",
            document_bytes=b"payload bytes",
            created_at="2026-08-03T12:00:00Z",
        )
        payload.update(overrides)
        return payload

    def _unwrap(self, payload):
        return unwrap_document(
            payload,
            local_node_id="urn:faw:local-node-0001",
            local_relay_address="relay-local",
            outer_message_id="22222222-2222-4222-8222-222222222222",
        )

    def test_exact_member_validation(self):
        payload = self._payload()
        payload["extra"] = "sneaky"
        with pytest.raises(NadiError, match="unknown or missing"):
            self._unwrap(payload)

    def test_malformed_base64(self):
        with pytest.raises(NadiError, match="base64url"):
            self._unwrap(self._payload(document="!!!not-base64!!!"))

    def test_wrong_media_type(self):
        with pytest.raises(NadiError, match="media_type"):
            self._unwrap(self._payload(media_type="text/plain"))

    def test_digest_mismatch(self):
        with pytest.raises(NadiError, match="document_sha256"):
            self._unwrap(self._payload(document=b64url_encode(b"different bytes")))

    def test_wrong_destination(self):
        with pytest.raises(NadiError, match="destination"):
            self._unwrap(self._payload(destination_node_id="urn:faw:other-0001"))

    def test_outer_wrapper_id_mismatch(self):
        with pytest.raises(NadiError, match="message ID differs"):
            unwrap_document(
                self._payload(),
                local_node_id="urn:faw:local-node-0001",
                local_relay_address="relay-local",
                outer_message_id="33333333-3333-4333-8333-333333333333",
            )

    def test_valid_round_trip(self):
        message_id, document_bytes, source = self._unwrap(self._payload())
        assert document_bytes == b"payload bytes"
        assert source == "urn:faw:issuer-0001"


class TestDurableState:
    def test_repeated_identical_mailbox_entry_idempotent(self, tmp_path):
        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        payload = b"idempotent"
        sender.send(payload, executor.node_id)
        first = receiver.poll()
        assert len(first) == 1
        # Same mailbox contents reread (stub returns same list).
        second = receiver.poll()
        assert len(second) == 1
        assert second[0].message_id == first[0].message_id

    def test_acknowledged_message_suppressed_after_reread(self, tmp_path):
        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        sender.send(b"ack me", executor.node_id)
        envelope = receiver.poll()[0]
        receiver.ack(envelope.message_id)
        # Tombstone persists; reread suppresses.
        assert receiver.poll() == []
        assert (tmp_path / "receiver-state" / "acknowledged" / f"{envelope.message_id}.ack").exists()

    def test_nacked_message_suppressed_after_reread(self, tmp_path):
        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        sender.send(b"nack me", executor.node_id)
        envelope = receiver.poll()[0]
        receiver.nack(envelope.message_id, "rejected locally")
        assert receiver.poll() == []
        failed = list((tmp_path / "receiver-state" / "failed").glob("*.nack"))
        assert len(failed) == 1

    def test_same_id_different_bytes_integrity_conflict(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayEnvelope

        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        mid = "44444444-4444-4444-8444-444444444444"
        wrapper = wrap_document(message_id=mid, source_node_id=issuer.node_id,
                                destination_node_id=executor.node_id, document_bytes=b"v1")
        backend.mailboxes.setdefault(executor_relay, []).append(RelayEnvelope(
            message_id=mid, source_address=_ir, destination_address=executor_relay,
            operation="faw.document", payload=wrapper))
        receiver.poll()  # imports v1
        # Second read with different bytes under the same ID.
        wrapper2 = wrap_document(message_id=mid, source_node_id=issuer.node_id,
                                 destination_node_id=executor.node_id, document_bytes=b"v2")
        backend.mailboxes[executor_relay].append(RelayEnvelope(
            message_id=mid, source_address=_ir, destination_address=executor_relay,
            operation="faw.document", payload=wrapper2))
        receiver.poll()
        failed = list((tmp_path / "receiver-state" / "failed").glob("*.nack"))
        assert any("different bytes" in f.read_text() for f in failed)

    def test_partial_publication_results_per_message(self, tmp_path):
        sender, _r, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        other = NodeIdentity.create(display_name="Other", capabilities=["hash_file"])
        other_relay = "relay-other"
        sender.routes[other.node_id] = other_relay
        backend.block(other_relay)
        good = sender.send(b"good", executor.node_id)
        bad = sender.send(b"bad", other.node_id)
        assert good.ok
        assert not bad.ok
        pending = list((tmp_path / "sender-state" / "outbox").glob("*.msg"))
        assert [p.stem for p in pending] == [bad.message_id]

    def test_transport_source_mismatch_no_authority(self, tmp_path):
        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        delegation_doc = b"not-a-real-delegation-but-bytes"
        sender.send(delegation_doc, executor.node_id)
        envelope = receiver.poll()[0]
        # Change transport provenance only.
        meta_path = tmp_path / "receiver-state" / "inbox" / f"{envelope.message_id}.meta"
        meta = json.loads(meta_path.read_text())
        meta["source"] = "urn:faw:evil-9999"
        meta_path.write_text(json.dumps(meta))
        repolled = receiver.poll()[0]
        assert repolled.source == "urn:faw:evil-9999"
        assert repolled.document_bytes == delegation_doc


class TestNoRejectedImports:
    def test_adapter_does_not_import_nadi_kit(self):
        import subprocess
        import sys

        check = (
            "import sys; sys.path.insert(0, 'src'); "
            "import federated_agent_web.transports.nadi as n; "
            "import federated_agent_web.transports.nadi_github as g; "
            "assert not hasattr(n, 'NadiNode'); "
            "assert not hasattr(g, 'NadiNode'); "
            "print('clean')"
        )
        r = subprocess.run([sys.executable, "-c", check], capture_output=True,
                           text=True, timeout=15, cwd=Path(__file__).resolve().parents[1])
        assert r.returncode == 0, r.stderr
        assert "clean" in r.stdout

    def test_nadi_modules_do_not_import_steward_federation(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        for name in ("nadi.py", "nadi_github.py"):
            tree = ast.parse((root / "src" / "federated_agent_web" / "transports" / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "nadi_kit" not in alias.name, f"{name} imports nadi_kit"
                        assert "steward" not in alias.name, f"{name} imports steward-federation"
                if isinstance(node, ast.ImportFrom):
                    assert "nadi_kit" not in (node.module or ""), f"{name} imports nadi_kit"


class TestRelayAddressGrammar:
    @pytest.mark.parametrize("address", ["good-address", "node1", "a.b_c", "relay-abc123"])
    def test_valid(self, address):
        assert is_valid_relay_address(address)

    @pytest.mark.parametrize("address", ["", "a/b", "../escape", "a_to_b", "with space", "a\\b"])
    def test_invalid(self, address):
        assert not is_valid_relay_address(address)


class TestGitHubBackendFake:
    class FakeClient:
        def __init__(self):
            self.files: dict[str, list[dict]] = {}
            self.write_count = 0
            self.force_conflict_once = False

        def list_paths(self, directory):
            return [f"nadi/{p.split('/',1)[1]}" for p in self.files if p.startswith(directory + "/")]

        def read_json(self, path):
            if path not in self.files:
                raise RuntimeError("not found")
            return list(self.files[path]), f"sha-{self.write_count}"

        def write_json(self, path, value, *, expected_sha, message):
            if self.force_conflict_once and expected_sha and expected_sha != f"sha-{self.write_count - 1}":
                self.force_conflict_once = False
                raise RuntimeError("conflict: sha mismatch")
            self.files[path] = value
            self.write_count += 1

    def test_publish_fetch_round_trip(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayEnvelope

        client = self.FakeClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        envelope = RelayEnvelope(
            message_id="55555555-5555-4555-8555-555555555555",
            source_address="relay-a",
            destination_address="relay-b",
            operation="faw.document",
            payload={"message_id": "55555555-5555-4555-8555-555555555555", "experimental": True},
        )
        results = backend.publish([envelope])
        assert len(results) == 1 and results[0].ok
        assert "nadi/relay-a_to_relay-b.json" in client.files
        fetched = backend.fetch("relay-b")
        assert len(fetched) == 1
        assert fetched[0].message_id == envelope.message_id

    def test_same_id_same_content_idempotent(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayEnvelope

        client = self.FakeClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        envelope = RelayEnvelope(
            message_id="66666666-6666-4666-8666-666666666666",
            source_address="relay-a", destination_address="relay-b",
            operation="faw.document",
            payload={"message_id": "66666666-6666-4666-8666-666666666666", "experimental": True},
        )
        backend.publish([envelope])
        backend.publish([envelope])
        entries = client.files["nadi/relay-a_to_relay-b.json"]
        assert len(entries) == 1

    def test_same_id_different_content_conflict(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayEnvelope

        client = self.FakeClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        e1 = RelayEnvelope("77777777-7777-4777-8777-777777777777", "relay-a", "relay-b",
                           "faw.document", {"id": "77777777-7777-4777-8777-777777777777", "payload": {"v": 1}})
        e2 = RelayEnvelope("77777777-7777-4777-8777-777777777777", "relay-a", "relay-b",
                           "faw.document", {"id": "77777777-7777-4777-8777-777777777777", "payload": {"v": 2}})
        backend.publish([e1])
        results = backend.publish([e2])
        assert not results[0].ok
        assert "integrity conflict" in results[0].error

    def test_one_target_failure_isolated(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayEnvelope

        client = self.FakeClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)

        class BadClient(self.FakeClient):
            def write_json(self, path, value, *, expected_sha, message):
                if "bad" in path:
                    raise RuntimeError("boom")
                super().write_json(path, value, expected_sha=expected_sha, message=message)

        backend.client = BadClient()
        ok_env = RelayEnvelope("88888888-8888-4888-8888-888888888888", "relay-a", "relay-good",
                               "faw.document", {"id": "88888888-8888-4888-8888-888888888888"})
        bad_env = RelayEnvelope("99999999-9999-4999-8999-999999999999", "relay-a", "relay-bad",
                                "faw.document", {"id": "99999999-9999-4999-8999-999999999999"})
        results = backend.publish([ok_env, bad_env])
        by_id = {r.message_id: r.ok for r in results}
        assert by_id[ok_env.message_id] is True
        assert by_id[bad_env.message_id] is False

    def test_no_whole_mailbox_clearing(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayEnvelope

        client = self.FakeClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        e1 = RelayEnvelope("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "relay-a", "relay-b",
                           "faw.document", {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"})
        e2 = RelayEnvelope("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "relay-a", "relay-b",
                           "faw.document", {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"})
        backend.publish([e1])
        backend.publish([e2])
        entries = client.files["nadi/relay-a_to_relay-b.json"]
        assert [e["id"] for e in entries] == [e1.message_id, e2.message_id]

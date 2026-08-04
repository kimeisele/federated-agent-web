"""Focused tests for the experimental Nadi/GitHub transport adapter (v0.4).

All tests use a stub relay backend, a fake high-level GitHub client, or a
monkeypatched ``subprocess.run`` — no real network, no `gh` invocation, no
live GitHub write. The shared adapter-neutral suite lives in
``test_transport_contract.py``; these tests cover Nadi-specific behavior.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from federated_agent_web import canonical
from federated_agent_web.crypto import b64url_encode
from federated_agent_web.identity import NodeIdentity
from federated_agent_web.transports import (
    GitHubNadiRelayBackend,
    NadiTransport,
)
from federated_agent_web.transports.nadi import (
    NadiError,
    wrap_document,
    unwrap_document,
)
from federated_agent_web.transports.nadi_github import (
    GhCliMailboxClient,
    MailboxClientError,
    MailboxConflict,
    MailboxNotFound,
    is_valid_relay_address,
)

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
        staged = list((tmp_path / "sender-state" / "outbox").glob("*.ready"))
        assert len(staged) == 1
        assert staged[0].stem == result.message_id


class TestOuterBoundary:
    def test_wrong_outer_destination_quarantined(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayEnvelope

        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        wrapper = wrap_document(
            message_id="11111111-1111-4111-8111-111111111111",
            source_node_id=issuer.node_id,
            destination_node_id=executor.node_id,
            document_bytes=b"x",
        )
        # Outer destination differs from the local relay address.
        envelope = RelayEnvelope(
            message_id="11111111-1111-4111-8111-111111111111",
            source_address=_ir,
            destination_address="relay-someone-else",
            operation="faw.document",
            payload=wrapper,
        )
        backend.mailboxes.setdefault(executor_relay, []).append(envelope)
        # Fetch is by local relay address; the envelope is delivered there but
        # its outer destination differs from the local relay address, so it
        # must produce failed evidence and never enter the inbox.
        assert receiver.poll() == []
        assert not list((tmp_path / "receiver-state" / "inbox").glob("*.msg"))
        failed = list((tmp_path / "receiver-state" / "failed").glob("*.nack"))
        assert len(failed) == 1
        assert "outer destination" in failed[0].read_text()

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
        # Relay envelope evidence preserved without credentials.
        relay_evidence = list((tmp_path / "receiver-state" / "failed").glob("*.relay.json"))
        assert len(relay_evidence) == 1


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

    def test_padded_base64_rejected(self):
        with pytest.raises(NadiError, match="base64url"):
            self._unwrap(self._payload(document=b64url_encode(b"payload bytes") + "="))

    def test_noncanonical_base64_rejected(self):
        # A valid charset but non-canonical representation.
        with pytest.raises(NadiError, match="base64url"):
            self._unwrap(self._payload(document="A"))

    def test_wrong_media_type(self):
        with pytest.raises(NadiError, match="media_type"):
            self._unwrap(self._payload(media_type="text/plain"))

    def test_digest_mismatch(self):
        with pytest.raises(NadiError, match="document_sha256"):
            self._unwrap(self._payload(document=b64url_encode(b"different bytes")))

    def test_digest_wrong_shape(self):
        with pytest.raises(NadiError, match="document_sha256"):
            self._unwrap(self._payload(document_sha256="md5:" + "0" * 32))

    def test_wrong_destination(self):
        with pytest.raises(NadiError, match="destination"):
            self._unwrap(self._payload(destination_node_id="urn:faw:other-0001"))

    def test_outer_wrapper_id_mismatch(self):
        with pytest.raises(NadiError, match="message ID differs"):
            unwrap_document(
                self._payload(),
                local_node_id="urn:faw:local-node-0001",
                outer_message_id="33333333-3333-4333-8333-333333333333",
            )

    def test_noncanonical_uuid_rejected(self):
        with pytest.raises(NadiError, match="canonical UUID"):
            self._unwrap(self._payload(message_id="abababab-abab-4bab-8bab-abababababab".upper()))

    def test_impossible_timestamp_rejected(self):
        for bad in ("2026-99-99T12:00:00Z", "2026-02-30T12:00:00Z", "2026-01-01T25:00:00Z",
                    "2026-01-01T12:00:00+02:00", "not-a-date"):
            with pytest.raises(NadiError, match="timestamp"):
                self._unwrap(self._payload(created_at=bad))

    def test_valid_round_trip(self):
        message_id, document_bytes, source = self._unwrap(self._payload())
        assert document_bytes == b"payload bytes"
        assert source == "urn:faw:issuer-0001"


class _EchoBackend:
    """Publish backend whose result set is derived from the attempted envelope."""

    def __init__(self, mode="ok"):
        self.mode = mode  # ok | empty | two | unknown-extra | duplicate

    def publish(self, envelopes):
        from federated_agent_web.transports.nadi import RelayPublishResult

        envelope = envelopes[0]
        if self.mode == "empty":
            return []
        if self.mode == "two":
            return [RelayPublishResult(envelope.message_id, True),
                    RelayPublishResult("other-id", True)]
        if self.mode == "unknown-extra":
            return [RelayPublishResult(envelope.message_id, True),
                    RelayPublishResult("unknown-extra", True)]
        if self.mode == "duplicate":
            return [RelayPublishResult(envelope.message_id, True),
                    RelayPublishResult(envelope.message_id, True)]
        if self.mode == "fail":
            return [RelayPublishResult(envelope.message_id, False, error="boom")]
        return [RelayPublishResult(envelope.message_id, True)]

    def fetch(self, destination_address):
        return []


class TestPublishResultCorrespondence:
    def _send(self, tmp_path, mode):
        sender, _r, issuer, executor, _b, _ir, _er = _make_pair(tmp_path)
        sender.backend = _EchoBackend(mode)
        return sender.send(b"payload", executor.node_id)

    def test_exact_one_result_ok(self, tmp_path):
        result = self._send(tmp_path, "ok")
        assert result.ok

    def test_zero_results_fails_and_retains(self, tmp_path):
        result = self._send(tmp_path, "empty")
        assert not result.ok
        assert "invalid publish result" in result.error
        assert list((tmp_path / "sender-state" / "outbox").glob(f"{result.message_id}.ready"))

    def test_two_results_fails(self, tmp_path):
        result = self._send(tmp_path, "two")
        assert not result.ok
        assert "invalid publish result" in result.error

    def test_unknown_extra_id_fails(self, tmp_path):
        result = self._send(tmp_path, "unknown-extra")
        assert not result.ok
        assert "invalid publish result" in result.error

    def test_duplicate_expected_id_fails(self, tmp_path):
        result = self._send(tmp_path, "duplicate")
        assert not result.ok
        assert "invalid publish result" in result.error

    def test_failed_publish_retains_staged(self, tmp_path):
        result = self._send(tmp_path, "fail")
        assert not result.ok
        assert "boom" in result.error
        assert list((tmp_path / "sender-state" / "outbox").glob(f"{result.message_id}.ready"))


class TestDurableState:
    def test_repeated_identical_mailbox_entry_idempotent(self, tmp_path):
        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        payload = b"idempotent"
        sender.send(payload, executor.node_id)
        first = receiver.poll()
        assert len(first) == 1
        second = receiver.poll()
        assert len(second) == 1
        assert second[0].message_id == first[0].message_id

    def test_acknowledged_message_suppressed_after_reread(self, tmp_path):
        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        sender.send(b"ack me", executor.node_id)
        envelope = receiver.poll()[0]
        receiver.ack(envelope.message_id)
        assert receiver.poll() == []
        assert (tmp_path / "receiver-state" / "acknowledged" / f"{envelope.message_id}.ack").exists()

    def test_nack_preserves_actual_failed_evidence(self, tmp_path):
        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        payload = b"nack me"
        sender.send(payload, executor.node_id)
        envelope = receiver.poll()[0]
        receiver.nack(envelope.message_id, "rejected locally")
        # Full failed evidence: msg + meta + nack.
        assert (tmp_path / "receiver-state" / "failed" / f"{envelope.message_id}.msg").read_bytes() == payload
        assert (tmp_path / "receiver-state" / "failed" / f"{envelope.message_id}.meta").exists()
        assert (tmp_path / "receiver-state" / "failed" / f"{envelope.message_id}.nack").exists()
        # Suppressed on reread even when the remote mailbox still has it.
        assert receiver.poll() == []

    def test_same_id_different_bytes_quarantined(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayEnvelope

        sender, receiver, issuer, executor, backend, _ir, executor_relay = _make_pair(tmp_path)
        mid = "44444444-4444-4444-8444-444444444444"
        wrapper1 = wrap_document(message_id=mid, source_node_id=issuer.node_id,
                                 destination_node_id=executor.node_id, document_bytes=b"v1")
        backend.mailboxes.setdefault(executor_relay, []).append(RelayEnvelope(
            message_id=mid, source_address=_ir, destination_address=executor_relay,
            operation="faw.document", payload=wrapper1))
        receiver.poll()  # imports v1
        assert [e.message_id for e in receiver.poll()] == [mid]
        # Second read with different bytes under the same ID.
        wrapper2 = wrap_document(message_id=mid, source_node_id=issuer.node_id,
                                 destination_node_id=executor.node_id, document_bytes=b"v2")
        backend.mailboxes[executor_relay].append(RelayEnvelope(
            message_id=mid, source_address=_ir, destination_address=executor_relay,
            operation="faw.document", payload=wrapper2))
        # After conflict: the conflicted ID never surfaces.
        assert all(e.message_id != mid for e in receiver.poll())
        # Original bytes and conflict evidence remain under failed/.
        failed = (tmp_path / "receiver-state" / "failed")
        assert (failed / f"{mid}.msg").read_bytes() == b"v1"
        assert any(f.name == f"{mid}.relay.json" for f in failed.iterdir())
        assert (failed / f"{mid}.nack").exists()


class TestAtomicStaging:
    def test_ready_marker_last(self, tmp_path):
        sender, _r, issuer, executor, _b, _ir, _er = _make_pair(tmp_path)
        # Use a failing publish so the complete record is retained.
        sender.backend = _EchoBackend("fail")
        result = sender.send(b"atomic", executor.node_id)
        assert not result.ok
        outbox = tmp_path / "sender-state" / "outbox"
        ready = list(outbox.glob("*.ready"))
        assert len(ready) == 1
        assert ready[0].stem == result.message_id
        message_id = ready[0].stem
        assert (outbox / f"{message_id}.msg").exists()
        assert (outbox / f"{message_id}.meta").exists()

    def test_delivered_removes_whole_record(self, tmp_path):
        sender, _r, issuer, executor, _b, _ir, _er = _make_pair(tmp_path)
        result = sender.send(b"atomic", executor.node_id)
        outbox = tmp_path / "sender-state" / "outbox"
        assert not list(outbox.glob(f"{result.message_id}.*"))

    def test_failed_publication_retains_ready_record(self, tmp_path):
        from federated_agent_web.transports.nadi import RelayPublishResult

        sender, _r, issuer, executor, _b, _ir, _er = _make_pair(tmp_path)
        sender.backend = _EchoBackend("fail")
        result = sender.send(b"atomic", executor.node_id)
        assert not result.ok
        outbox = tmp_path / "sender-state" / "outbox"
        assert (outbox / f"{result.message_id}.ready").exists()


class TestRelayAddressGrammar:
    @pytest.mark.parametrize("address", ["good-address", "node1", "a.b_c", "relay-abc123"])
    def test_valid(self, address):
        assert is_valid_relay_address(address)

    @pytest.mark.parametrize("address", ["", "a/b", "../escape", "a_to_b", "with space", "a\\b"])
    def test_invalid(self, address):
        assert not is_valid_relay_address(address)

    def test_hub_repo_validation(self):
        for bad in ("", "kimeisele", "../owner/repo", "a/b/c", "owner/repo/extra"):
            with pytest.raises(ValueError, match="owner/repository"):
                GhCliMailboxClient(bad)
        GhCliMailboxClient("kimeisele/federated-agent-web")


class _FakeGhClient:
    """High-level fake GitHub client (used by backend-level tests)."""

    def __init__(self):
        self.files: dict[str, list[dict]] = {}
        self.write_count = 0
        self.force_conflict_once = False
        self.conflict_raises = 0

    def list_paths(self, directory):
        return [f"nadi/{p.split('/', 1)[1]}" for p in self.files if p.startswith(directory + "/")]

    def read_json(self, path):
        if path not in self.files:
            raise MailboxNotFound(f"not found: {path}")
        return list(self.files[path]), f"sha-{self.write_count}"

    def write_json(self, path, value, *, expected_sha, message):
        if self.force_conflict_once and expected_sha and expected_sha != f"sha-{self.write_count - 1}":
            self.force_conflict_once = False
            raise MailboxConflict("sha mismatch")
        if self.conflict_raises > 0:
            self.conflict_raises -= 1
            raise MailboxConflict("sha mismatch")
        self.files[path] = value
        self.write_count += 1


class TestGitHubBackendFake:
    def _envelope(self, message_id, source="relay-a", target="relay-b", payload=None):
        from federated_agent_web.transports.nadi import RelayEnvelope

        return RelayEnvelope(
            message_id=message_id, source_address=source, destination_address=target,
            operation="faw.document",
            payload=payload if payload is not None else {"id": message_id, "experimental": True},
        )

    def test_publish_fetch_round_trip(self, tmp_path):
        client = _FakeGhClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        envelope = self._envelope("55555555-5555-4555-8555-555555555555")
        results = backend.publish([envelope])
        assert len(results) == 1 and results[0].ok
        assert "nadi/relay-a_to_relay-b.json" in client.files
        fetched = backend.fetch("relay-b")
        assert len(fetched) == 1
        assert fetched[0].message_id == envelope.message_id

    def test_same_id_same_content_idempotent(self, tmp_path):
        client = _FakeGhClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        envelope = self._envelope("66666666-6666-4666-8666-666666666666")
        backend.publish([envelope])
        backend.publish([envelope])
        entries = client.files["nadi/relay-a_to_relay-b.json"]
        assert len(entries) == 1

    def test_same_id_different_content_conflict(self, tmp_path):
        client = _FakeGhClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        e1 = self._envelope("77777777-7777-4777-8777-777777777777", payload={"id": "x", "v": 1})
        e2 = self._envelope("77777777-7777-4777-8777-777777777777", payload={"id": "x", "v": 2})
        backend.publish([e1])
        results = backend.publish([e2])
        assert not results[0].ok
        assert "integrity conflict" in results[0].error

    def test_two_sources_one_target_two_mailboxes(self, tmp_path):
        client = _FakeGhClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        e1 = self._envelope("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", source="relay-a", target="relay-b")
        e2 = self._envelope("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", source="relay-c", target="relay-b")
        results = backend.publish([e1, e2])
        assert all(r.ok for r in results)
        assert "nadi/relay-a_to_relay-b.json" in client.files
        assert "nadi/relay-c_to_relay-b.json" in client.files

    def test_one_target_failure_isolated(self, tmp_path):
        client = _FakeGhClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)

        class BadClient(_FakeGhClient):
            def write_json(self, path, value, *, expected_sha, message):
                if "bad" in path:
                    raise MailboxClientError("boom")
                super().write_json(path, value, expected_sha=expected_sha, message=message)

        backend.client = BadClient()
        ok_env = self._envelope("88888888-8888-4888-8888-888888888888", target="relay-good")
        bad_env = self._envelope("99999999-9999-4999-8999-999999999999", target="relay-bad")
        results = backend.publish([ok_env, bad_env])
        by_id = {r.message_id: r.ok for r in results}
        assert by_id[ok_env.message_id] is True
        assert by_id[bad_env.message_id] is False

    def test_no_whole_mailbox_clearing(self, tmp_path):
        client = _FakeGhClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        e1 = self._envelope("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        e2 = self._envelope("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        backend.publish([e1])
        backend.publish([e2])
        entries = client.files["nadi/relay-a_to_relay-b.json"]
        assert [e["id"] for e in entries] == [e1.message_id, e2.message_id]

    def test_conflict_retried_once(self, tmp_path):
        client = _FakeGhClient()
        client.conflict_raises = 1  # first write conflicts, second succeeds
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        envelope = self._envelope("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        results = backend.publish([envelope])
        assert results[0].ok
        assert client.write_count == 1

    def test_malformed_entry_isolated(self, tmp_path):
        client = _FakeGhClient()
        backend = GitHubNadiRelayBackend(hub_repo="kimeisele/fake", client=client)
        client.files["nadi/relay-a_to_relay-b.json"] = [
            {"id": "not-an-object-with-string-id", "payload": "wrong"},  # malformed
            {"id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
             "correlation_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
             "source": "relay-a", "target": "relay-b",
             "operation": "faw.document", "payload": {"id": "d"}},
        ]
        fetched = backend.fetch("relay-b")
        assert len(fetched) == 1
        assert fetched[0].message_id == "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


class TestGhCliClient:
    """Exercise the real CLI adapter without executing `gh`."""

    class FakeProc:
        def __init__(self, stdout: bytes, returncode: int = 0, stderr: bytes = b""):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def _monkeypatched_client(self, monkeypatch, responses):
        calls: list[tuple[list, bytes | None]] = []

        def fake_run(argv, input=None, capture_output=True, text=False, timeout=60):
            calls.append((list(argv), input))
            return self.FakeProc(stdout=responses.pop(0))

        monkeypatch.setattr(subprocess, "run", fake_run)
        return GhCliMailboxClient("kimeisele/fake"), calls

    def test_list_paths_parses_json_list(self, monkeypatch):
        listing = json.dumps([{"path": "nadi/a_to_b.json", "type": "file"}]).encode()
        client, _calls = self._monkeypatched_client(monkeypatch, [listing])
        assert client.list_paths("nadi") == ["nadi/a_to_b.json"]

    def test_write_json_uses_stdin_and_no_content_in_argv(self, monkeypatch):
        client, calls = self._monkeypatched_client(monkeypatch, [b"{}"])
        client.write_json("nadi/a_to_b.json", [{"id": "x"}],
                          expected_sha="sha-1", message="append")
        argv, stdin = calls[0]
        assert "--input" in argv and "-" in argv
        assert "-f" not in argv or "content=" not in " ".join(argv)
        body = json.loads(stdin.decode())
        assert body["message"] == "append"
        assert body["sha"] == "sha-1"
        assert "content" in body
        # No base64 content or document bytes in argv.
        assert not any("b64" in a or "==" in a for a in argv)

    def test_shell_never_used(self, monkeypatch):
        client, calls = self._monkeypatched_client(monkeypatch, [b"{}"])
        client.write_json("nadi/a_to_b.json", [], expected_sha=None, message="m")
        for argv, _ in calls:
            assert "shell" not in argv

    def test_missing_mailbox_distinguished_from_auth_failure(self, monkeypatch):
        def fake_run(argv, input=None, capture_output=True, text=False, timeout=60):
            joined = " ".join(argv)
            if joined.endswith("contents/nadi"):
                return self.FakeProc(b"", returncode=1,
                                     stderr=b"gh api repos/k/f/contents/nadi: Not Found (HTTP 404)")
            return self.FakeProc(b"", returncode=1, stderr=b"Bad credentials (HTTP 401)")

        monkeypatch.setattr(subprocess, "run", fake_run)
        client = GhCliMailboxClient("kimeisele/fake")
        assert client.list_paths("nadi") == []  # genuine missing → empty
        with pytest.raises(MailboxClientError):
            client.read_json("nadi/a_to_b.json")  # auth failure → error

    def test_conflict_classified(self, monkeypatch):
        def fake_run(argv, input=None, capture_output=True, text=False, timeout=60):
            return self.FakeProc(b"", returncode=1,
                                 stderr=b"API ... 409: conflict - sha does not match")

        monkeypatch.setattr(subprocess, "run", fake_run)
        client = GhCliMailboxClient("kimeisele/fake")
        with pytest.raises(MailboxConflict):
            client.read_json("nadi/a_to_b.json")

    def test_invalid_json_output_fails_safely(self, monkeypatch):
        client, _ = self._monkeypatched_client(monkeypatch, [b"not json at all"])
        with pytest.raises(MailboxClientError, match="invalid JSON"):
            client.list_paths("nadi")


class TestNoRejectedImports:
    def test_adapter_does_not_import_nadi_kit(self):
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

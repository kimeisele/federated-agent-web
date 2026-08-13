"""CLI configuration boundary for the Nadi node transport."""

from pathlib import Path

import pytest

from federated_agent_web import cli
from federated_agent_web.cli import _parse_nadi_routes, build_parser
from federated_agent_web.identity import NodeIdentity
from federated_agent_web.transports import NadiTransport


def test_nadi_routes_are_explicit_and_keep_identity_separate_from_relay():
    assert _parse_nadi_routes([
        "urn:faw:peer-a=relay-a",
        "urn:faw:peer-b=relay-b",
    ]) == {
        "urn:faw:peer-a": "relay-a",
        "urn:faw:peer-b": "relay-b",
    }


@pytest.mark.parametrize("value", ["missing-separator", "=relay", "urn:faw:peer="])
def test_malformed_nadi_route_fails_closed(value):
    with pytest.raises(ValueError, match="FAW_NODE_ID=RELAY_ADDRESS"):
        _parse_nadi_routes([value])


def test_duplicate_nadi_route_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        _parse_nadi_routes(["urn:faw:peer=relay-a", "urn:faw:peer=relay-b"])


def test_node_cli_exposes_nadi_github_without_changing_filesystem_default():
    parser = build_parser()
    common = [
        "node", "run-once", "--identity", "identity", "--trust", "trust",
        "--transport-root", "transport", "--state-dir", "state",
        "--work-dir", "work", "--role", "executor",
    ]
    assert parser.parse_args(common).transport == "filesystem"
    configured = parser.parse_args(common + [
        "--transport", "nadi-github",
        "--nadi-relay-address", "relay-a",
        "--nadi-hub-repo", "owner/hub",
        "--nadi-route", "urn:faw:peer=relay-b",
    ])
    assert configured.transport == "nadi-github"
    assert configured.nadi_route == ["urn:faw:peer=relay-b"]


def test_node_cli_builds_nadi_transport_and_passes_it_to_runner(tmp_path, monkeypatch):
    local = NodeIdentity.create(display_name="CLI local")
    peer = NodeIdentity.create(display_name="CLI peer")
    local_dir, peer_dir = tmp_path / "local", tmp_path / "peer"
    local.to_json(local_dir)
    peer.to_json(peer_dir)
    captured = {}

    class FakeClient:
        def __init__(self, hub_repo):
            self.hub_repo = hub_repo

    def fake_run_once(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "GhCliMailboxClient", FakeClient)
    monkeypatch.setattr(cli, "_runner_run_once", fake_run_once)
    result = cli.main([
        "node", "run-once",
        "--identity", str(local_dir),
        "--trust", str(peer_dir),
        "--transport-root", str(tmp_path / "transport"),
        "--state-dir", str(tmp_path / "state"),
        "--work-dir", str(tmp_path / "work"),
        "--role", "executor",
        "--transport", "nadi-github",
        "--nadi-relay-address", "local-relay",
        "--nadi-hub-repo", "owner/hub",
        "--nadi-route", f"{peer.node_id}=peer-relay",
    ])
    assert result == 0
    transport = captured["transport"]
    assert isinstance(transport, NadiTransport)
    assert transport.node_id == local.node_id
    assert transport.relay_address == "local-relay"
    assert transport.routes == {peer.node_id: "peer-relay"}
    assert captured["transport_root"] == Path(tmp_path / "transport")

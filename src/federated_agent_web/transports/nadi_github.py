"""GitHub mailbox backend for the Nadi/GitHub transport adapter (v0.4).

Thin, experimental, non-normative. Uses the existing Nadi-compatible mailbox
layout ``nadi/<source>_to_<target>.json`` in a configurable repository, with
optimistic content-SHA updates. Never clears a whole mailbox; one per-message
result per attempted message.

The GitHub client is injected so tests require no GitHub access. The default
client shells out to the caller's existing ``gh`` authentication.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol

from .nadi import (
    FAW_DOCUMENT_OPERATION,
    NadiRelayBackend,
    RelayEnvelope,
    RelayPublishResult,
)

__all__ = ["GitHubMailboxClient", "GhCliMailboxClient", "GitHubNadiRelayBackend", "is_valid_relay_address"]

# Strict mailbox-safe grammar: no slashes, no path traversal, and no "_to_"
# substring so mailbox path parsing stays unambiguous.
_RELAY_ADDRESS_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,63})$")
_MAILBOX_RE = re.compile(r"^nadi/([A-Za-z0-9._-]+)_to_([A-Za-z0-9._-]+)\.json$")


def is_valid_relay_address(address: str) -> bool:
    return bool(_RELAY_ADDRESS_RE.match(address)) and "_to_" not in address


class GitHubMailboxClient(Protocol):
    """Injected GitHub content-API client (tests use a fake)."""

    def list_paths(self, directory: str) -> list[str]: ...

    def read_json(self, path: str) -> tuple[list[dict], str | None]: ...

    def write_json(
        self,
        path: str,
        value: list[dict],
        *,
        expected_sha: str | None,
        message: str,
    ) -> None: ...


class GhCliMailboxClient:
    """GitHub content-API client via the caller's existing `gh` CLI.

    Uses argument arrays (never ``shell=True``); large JSON bodies go through
    stdin; never logs tokens, credentials, or document contents; performs no
    operation at import time.
    """

    def __init__(self, hub_repo: str) -> None:
        self.hub_repo = hub_repo

    def _gh(self, *args: str, stdin: bytes | None = None) -> dict[str, Any]:
        argv = ["gh", "api", *args]
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=False,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"gh api failed: {proc.stderr.decode(errors='replace')[:300]}")
        return json.loads(proc.stdout.decode())

    def list_paths(self, directory: str) -> list[str]:
        try:
            listing = self._gh(f"repos/{self.hub_repo}/contents/{directory}",
                               "--jq", ".[].path")
        except RuntimeError:
            return []
        if isinstance(listing, list):
            return [str(p) for p in listing]
        return []

    def read_json(self, path: str) -> tuple[list[dict], str | None]:
        data = self._gh(f"repos/{self.hub_repo}/contents/{path}")
        sha = data.get("sha")
        raw = base64.b64decode(data["content"]).decode()
        return list(json.loads(raw)), sha

    def write_json(
        self,
        path: str,
        value: list[dict],
        *,
        expected_sha: str | None,
        message: str,
    ) -> None:
        content = base64.b64encode(json.dumps(value).encode()).decode()
        argv = [
            f"repos/{self.hub_repo}/contents/{path}",
            "-X", "PUT",
            "-f", f"message={message}",
            "-f", f"content={content}",
        ]
        if expected_sha:
            argv.append("-f")
            argv.append(f"sha={expected_sha}")
        self._gh(*argv)


def _mailbox_path(source: str, target: str) -> str:
    if not is_valid_relay_address(source) or not is_valid_relay_address(target):
        raise ValueError(f"invalid relay address in mailbox path: {source!r} -> {target!r}")
    return f"nadi/{source}_to_{target}.json"


def _to_relay_envelope(entry: dict[str, Any]) -> RelayEnvelope | None:
    message_id = str(entry.get("id") or entry.get("correlation_id") or "")
    if not message_id:
        return None
    return RelayEnvelope(
        message_id=message_id,
        source_address=str(entry.get("source", "")),
        destination_address=str(entry.get("target", "")),
        operation=str(entry.get("operation", "")),
        payload=dict(entry.get("payload") or {}),
    )


@dataclass
class GitHubNadiRelayBackend:
    """Nadi relay backend persisted to a GitHub mailbox repository."""

    hub_repo: str
    client: GitHubMailboxClient

    def publish(self, envelopes: list[RelayEnvelope]) -> list[RelayPublishResult]:
        results: list[RelayPublishResult] = []
        by_target: dict[str, list[RelayEnvelope]] = {}
        for envelope in envelopes:
            by_target.setdefault(envelope.destination_address, []).append(envelope)

        for target, group in by_target.items():
            try:
                path = _mailbox_path(group[0].source_address, target)
                self._append_to_mailbox(path, group)
                for envelope in group:
                    results.append(RelayPublishResult(message_id=envelope.message_id, ok=True))
            except Exception as exc:  # noqa: BLE001 - one target's failure is isolated
                for envelope in group:
                    results.append(
                        RelayPublishResult(message_id=envelope.message_id, ok=False, error=str(exc))
                    )
        return results

    def fetch(self, destination_address: str) -> list[RelayEnvelope]:
        fetched: list[RelayEnvelope] = []
        for path in self.client.list_paths("nadi"):
            match = _MAILBOX_RE.match(path)
            if not match:
                continue
            _, target = match.groups()
            if target != destination_address:
                continue
            try:
                entries, _sha = self.client.read_json(path)
            except Exception:  # noqa: BLE001 - skip unreadable mailboxes
                continue
            for entry in entries:
                envelope = _to_relay_envelope(entry)
                if envelope is not None and envelope.operation == FAW_DOCUMENT_OPERATION:
                    fetched.append(envelope)
        return fetched

    # -- mailbox mutation ---------------------------------------------------

    def _append_to_mailbox(self, path: str, group: list[RelayEnvelope]) -> None:
        # Read-modify-write with optimistic SHA; retry a conflict once.
        for attempt in range(2):
            try:
                entries, sha = self.client.read_json(path)
            except Exception:  # noqa: BLE001 - treat as empty/new mailbox
                entries, sha = [], None
            existing_ids = {str(e.get("id") or e.get("correlation_id") or "") for e in entries}
            appended: list[dict[str, Any]] = []
            for envelope in group:
                message_id = envelope.message_id
                if message_id in existing_ids:
                    # Same ID plus same content is idempotent; different content
                    # fails as an integrity conflict.
                    prior = next(e for e in entries
                                 if str(e.get("id") or e.get("correlation_id") or "") == message_id)
                    if prior.get("payload") != envelope.payload:
                        raise ValueError(f"integrity conflict: message {message_id} re-published with different content")
                    continue
                entry = {
                    "source": envelope.source_address,
                    "target": envelope.destination_address,
                    "operation": envelope.operation,
                    "payload": envelope.payload,
                    "timestamp": 0.0,
                    "priority": 1,
                    "correlation_id": envelope.message_id,
                    "ttl_s": 7200.0,
                    "id": envelope.message_id,
                }
                appended.append(entry)
                existing_ids.add(message_id)
            if not appended:
                return  # all already present and identical
            try:
                self.client.write_json(
                    path,
                    entries + appended,
                    expected_sha=sha,
                    message=f"faw.document relay append ({len(appended)} message(s))",
                )
                return
            except RuntimeError as exc:
                if attempt == 0 and "conflict" in str(exc).lower():
                    continue  # retry after rereading
                raise
        raise RuntimeError("mailbox write conflict retried and failed")

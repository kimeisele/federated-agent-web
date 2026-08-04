"""GitHub mailbox backend for the Nadi/GitHub transport adapter (v0.4).

Thin, experimental, non-normative. Uses the existing Nadi-compatible mailbox
layout ``nadi/<source>_to_<target>.json`` in a configurable repository, with
optimistic content-SHA updates. Never clears a whole mailbox; one per-message
result per attempted message.

The GitHub client is injected so tests require no GitHub access. The default
client shells out to the caller's existing ``gh`` authentication; JSON bodies
are sent through stdin, never as command-line arguments.
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

__all__ = [
    "GitHubMailboxClient",
    "GhCliMailboxClient",
    "GitHubNadiRelayBackend",
    "is_valid_relay_address",
    "MailboxNotFound",
    "MailboxConflict",
    "MailboxClientError",
]

# Strict mailbox-safe grammar: no slashes, no path traversal, and no "_to_"
# substring so mailbox path parsing stays unambiguous.
_RELAY_ADDRESS_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,63})$")
_MAILBOX_RE = re.compile(r"^nadi/([A-Za-z0-9._-]+)_to_([A-Za-z0-9._-]+)\.json$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class MailboxNotFound(Exception):
    """A genuine missing mailbox (treated as an empty new mailbox)."""


class MailboxConflict(Exception):
    """Optimistic-SHA write conflict (retryable at most once)."""


class MailboxClientError(Exception):
    """Any other client failure: auth, parse, rate-limit, network."""


def is_valid_relay_address(address: str) -> bool:
    return bool(_RELAY_ADDRESS_RE.match(address)) and "_to_" not in address


def _validate_repo(hub_repo: str) -> None:
    if not isinstance(hub_repo, str) or not _REPO_RE.match(hub_repo):
        raise ValueError(f"hub_repo must be 'owner/repository', got {hub_repo!r}")


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

    Uses argument arrays (never ``shell=True``); JSON request bodies go
    through stdin via ``--input -``; never logs tokens, credentials, or
    document contents; performs no operation at import time.
    """

    def __init__(self, hub_repo: str) -> None:
        _validate_repo(hub_repo)
        self.hub_repo = hub_repo

    def _gh_json(self, *args: str, stdin: bytes | None = None) -> Any:
        argv = ["gh", "api", *args]
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=False,
            timeout=60,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")
            if "Not Found" in stderr:
                raise MailboxNotFound(stderr[:200])
            if "conflict" in stderr.lower() or "sha" in stderr.lower() and "409" in stderr:
                raise MailboxConflict(stderr[:200])
            raise MailboxClientError(stderr[:300])
        try:
            return json.loads(proc.stdout.decode())
        except json.JSONDecodeError as exc:
            raise MailboxClientError(f"gh api returned invalid JSON: {exc}") from exc

    def list_paths(self, directory: str) -> list[str]:
        try:
            listing = self._gh_json(f"repos/{self.hub_repo}/contents/{directory}")
        except MailboxNotFound:
            return []
        if not isinstance(listing, list):
            raise MailboxClientError("gh api directory listing is not a JSON list")
        paths: list[str] = []
        for item in listing:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise MailboxClientError("directory listing contains a non-object/non-path entry")
            paths.append(item["path"])
        return paths

    def read_json(self, path: str) -> tuple[list[dict], str | None]:
        try:
            data = self._gh_json(f"repos/{self.hub_repo}/contents/{path}")
        except MailboxNotFound as exc:
            raise MailboxNotFound(str(exc)) from exc
        if not isinstance(data, dict):
            raise MailboxClientError("contents response is not an object")
        sha = data.get("sha")
        content = data.get("content")
        if not isinstance(content, str):
            raise MailboxClientError("contents response has no string content field")
        try:
            raw = base64.b64decode(content).decode()
        except (ValueError, UnicodeDecodeError) as exc:
            raise MailboxClientError(f"contents response has invalid base64 content: {exc}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MailboxClientError(f"mailbox content is invalid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise MailboxClientError("mailbox top-level JSON is not a list")
        for member in parsed:
            if not isinstance(member, dict):
                raise MailboxClientError("mailbox list contains a non-object member")
        return parsed, sha

    def write_json(
        self,
        path: str,
        value: list[dict],
        *,
        expected_sha: str | None,
        message: str,
    ) -> None:
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(json.dumps(value).encode()).decode(),
        }
        if expected_sha is not None:
            body["sha"] = expected_sha
        # JSON body goes through stdin; no document bytes appear in argv.
        self._gh_json(
            f"repos/{self.hub_repo}/contents/{path}",
            "-X", "PUT",
            "--input", "-",
            stdin=json.dumps(body).encode("utf-8"),
        )


def _mailbox_path(source: str, target: str) -> str:
    if not is_valid_relay_address(source) or not is_valid_relay_address(target):
        raise ValueError(f"invalid relay address in mailbox path: {source!r} -> {target!r}")
    return f"nadi/{source}_to_{target}.json"


def _mailbox_entries_equal(entry: dict[str, Any], envelope: RelayEnvelope) -> bool:
    """Complete outer-entry comparison for idempotency (source/target/op/payload/ids)."""
    return (
        str(entry.get("source")) == envelope.source_address
        and str(entry.get("target")) == envelope.destination_address
        and str(entry.get("operation")) == envelope.operation
        and entry.get("payload") == envelope.payload
        and str(entry.get("correlation_id") or "") == envelope.message_id
        and str(entry.get("id") or "") == envelope.message_id
    )


def _entry_to_envelope(entry: Any) -> RelayEnvelope | None:
    """Return a validated envelope or None when the entry is malformed.

    A malformed entry never raises out of the mailbox loop.
    """
    if not isinstance(entry, dict):
        return None
    message_id = entry.get("id")
    correlation_id = entry.get("correlation_id")
    if not isinstance(message_id, str) or not isinstance(correlation_id, str):
        return None
    if message_id != correlation_id:
        return None
    source = entry.get("source")
    target = entry.get("target")
    operation = entry.get("operation")
    payload = entry.get("payload")
    if not isinstance(source, str) or not is_valid_relay_address(source):
        return None
    if not isinstance(target, str) or not is_valid_relay_address(target):
        return None
    if operation != FAW_DOCUMENT_OPERATION:
        return None
    if not isinstance(payload, dict):
        return None
    return RelayEnvelope(
        message_id=message_id,
        source_address=source,
        destination_address=target,
        operation=operation,
        payload=payload,
    )


@dataclass
class GitHubNadiRelayBackend:
    """Nadi relay backend persisted to a GitHub mailbox repository."""

    hub_repo: str
    client: GitHubMailboxClient

    def __post_init__(self) -> None:
        _validate_repo(self.hub_repo)

    def publish(self, envelopes: list[RelayEnvelope]) -> list[RelayPublishResult]:
        results: list[RelayPublishResult] = []
        # Group by the complete mailbox key (source, destination).
        by_mailbox: dict[tuple[str, str], list[RelayEnvelope]] = {}
        for envelope in envelopes:
            by_mailbox.setdefault((envelope.source_address, envelope.destination_address), []).append(envelope)

        for (source, target), group in by_mailbox.items():
            # Reject duplicate attempted message IDs before writing.
            attempted_ids = [e.message_id for e in group]
            if len(set(attempted_ids)) != len(attempted_ids):
                for envelope in group:
                    results.append(RelayPublishResult(
                        message_id=envelope.message_id, ok=False, error="duplicate attempted message ID"))
                continue
            try:
                path = _mailbox_path(source, target)
                self._append_to_mailbox(path, group)
                for envelope in group:
                    results.append(RelayPublishResult(message_id=envelope.message_id, ok=True))
            except Exception as exc:  # noqa: BLE001 - one mailbox's failure is isolated
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
            path_source, path_target = match.groups()
            if path_target != destination_address:
                continue
            try:
                entries, _sha = self.client.read_json(path)
            except (MailboxNotFound, MailboxClientError):
                continue  # skip unreadable mailboxes; do not hide valid ones
            for entry in entries:
                envelope = _entry_to_envelope(entry)
                if envelope is None:
                    continue  # malformed entry isolated, not fatal
                # Outer source/target must agree with the mailbox path.
                if envelope.source_address != path_source or envelope.destination_address != path_target:
                    continue
                fetched.append(envelope)
        return fetched

    # -- mailbox mutation ---------------------------------------------------

    def _append_to_mailbox(self, path: str, group: list[RelayEnvelope]) -> None:
        # Read-modify-write with optimistic SHA; retry a conflict once.
        for attempt in range(2):
            try:
                entries, sha = self.client.read_json(path)
            except MailboxNotFound:
                entries, sha = [], None
            except MailboxClientError as exc:
                raise MailboxClientError(str(exc)) from exc
            existing_ids = {str(e.get("id") or e.get("correlation_id") or "") for e in entries}
            appended: list[dict[str, Any]] = []
            for envelope in group:
                message_id = envelope.message_id
                if message_id in existing_ids:
                    prior = next(e for e in entries
                                 if str(e.get("id") or e.get("correlation_id") or "") == message_id)
                    if not _mailbox_entries_equal(prior, envelope):
                        raise ValueError(
                            f"integrity conflict: message {message_id} re-published with different content")
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
            except MailboxConflict:
                if attempt == 0:
                    continue  # retry once after rereading
                raise MailboxConflict("mailbox write conflict retried and failed")
        raise MailboxConflict("mailbox write conflict retried and failed")

"""Stable node identity with rotatable keys (§8).

``NodeIdentity.node_id`` is stable and never derived from the active key.
Each manifest lists the full key table with current statuses; a normal
rotation increments the sequence, links the previous manifest digest, is
signed by the previous active key, and retires the old key.

Trust is NOT established here: a self-signed genesis proves key possession
only. Verifiers supply a ``PinnedManifestTrustContext`` built from locally
approved anchors (see verify.py).
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import canonical, crypto
from .documents import (
    KIND_MANIFEST,
    build_document,
    content_digest_of,
    now_utc_z,
    parse_timestamp,
    validate_document,
)

__all__ = [
    "NodeKey",
    "NodeIdentity",
    "generate_node_id",
    "validate_manifest_chain",
    "resolve_key_for",
    "ChainValidation",
]

_NODE_ID_RE = re.compile(r"^urn:faw:[a-z0-9](?:[a-z0-9._-]{0,62})$")
DEFAULT_FRESHNESS_WINDOW_SECONDS = 3600


def generate_node_id() -> str:
    """Return a random stable ``urn:faw:`` node identifier (ASCII, ≤64 chars)."""
    return "urn:faw:" + secrets.token_hex(8)  # 16 hex chars, lowercase


@dataclass
class NodeKey:
    """A node key table entry plus the (secret) private material."""

    private_raw: bytes
    public_raw: bytes
    kid: str
    status: str = "active"
    valid_from: str = field(default_factory=now_utc_z)
    valid_until: str | None = None
    revoked_at: str | None = None
    replaces: str | None = None

    def to_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "kid": self.kid,
            "alg": "Ed25519",
            "public_key": crypto.b64url_encode(self.public_raw),
            "status": self.status,
            "valid_from": self.valid_from,
        }
        if self.valid_until is not None:
            entry["valid_until"] = self.valid_until
        if self.revoked_at is not None:
            entry["revoked_at"] = self.revoked_at
        if self.replaces is not None:
            entry["replaces"] = self.replaces
        return entry


class NodeIdentity:
    """A stable node identity plus its manifest chain and private keys."""

    def __init__(
        self,
        node_id: str,
        display_name: str,
        keys: list[NodeKey],
        manifests: list[dict[str, Any]],
        freshness_window_seconds: int = DEFAULT_FRESHNESS_WINDOW_SECONDS,
    ) -> None:
        if not _NODE_ID_RE.match(node_id):
            raise ValueError(f"invalid node_id {node_id!r}")
        if not keys:
            raise ValueError("identity requires at least one key")
        self.node_id = node_id
        self.display_name = display_name
        self.keys = keys
        self.manifests: list[dict[str, Any]] = list(manifests)
        self.freshness_window_seconds = freshness_window_seconds

    # -- construction -----------------------------------------------------

    @classmethod
    def create(
        cls,
        node_id: str | None = None,
        display_name: str = "FAW node",
        capabilities: list[str] | None = None,
        endpoints: list[dict[str, str]] | None = None,
        authorization_policy: dict[str, Any] | None = None,
        cost_class: dict[str, Any] | None = None,
        rate_limits: dict[str, Any] | None = None,
        freshness_window_seconds: int = DEFAULT_FRESHNESS_WINDOW_SECONDS,
    ) -> "NodeIdentity":
        """Create an identity with a fresh key and a self-signed genesis manifest."""
        node_id = node_id or generate_node_id()
        private_raw, public_raw = crypto.generate_keypair()
        key = NodeKey(private_raw=private_raw, public_raw=public_raw, kid=crypto.kid_for(public_raw))
        identity = cls(
            node_id=node_id,
            display_name=display_name,
            keys=[key],
            manifests=[],
            freshness_window_seconds=freshness_window_seconds,
        )
        manifest = identity._build_manifest(
            sequence=1,
            previous_digest=None,
            capabilities=capabilities or [],
            endpoints=endpoints or [],
            authorization_policy=authorization_policy or {"default_deny": True, "required_grants": []},
            cost_class=cost_class or {"tier": "free"},
            rate_limits=rate_limits or {},
            signer=key,
            issuer_node_id=node_id,
        )
        identity.manifests.append(manifest)
        validate_document(manifest, KIND_MANIFEST)
        return identity

    # -- properties -------------------------------------------------------

    @property
    def active_key(self) -> NodeKey:
        active = [key for key in self.keys if key.status == "active"]
        if not active:
            raise RuntimeError(f"node {self.node_id} has no active key")
        return active[-1]

    @property
    def head_manifest(self) -> dict[str, Any]:
        return self.manifests[-1]

    def head_sequence(self) -> int:
        return int(self.head_manifest["body"]["manifest_sequence"])

    def head_digest(self) -> str:
        return content_digest_of(self.head_manifest)

    def save_keytable(self, directory: Path) -> None:
        """Persist private key material (mode 0600). For backup/CLI use only."""
        directory.mkdir(parents=True, exist_ok=True)
        for key in self.keys:
            path = directory / f"{key.kid}.key"
            with open(path, "wb") as handle:
                handle.write(key.private_raw)
            path.chmod(0o600)

    # -- manifest building ------------------------------------------------

    def _build_manifest(
        self,
        *,
        sequence: int,
        previous_digest: str | None,
        capabilities: list[str],
        endpoints: list[dict[str, str]],
        authorization_policy: dict[str, Any],
        cost_class: dict[str, Any],
        rate_limits: dict[str, Any],
        signer: NodeKey,
        issuer_node_id: str,
    ) -> dict[str, Any]:
        body = {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "manifest_sequence": sequence,
            "previous_manifest_digest": previous_digest,
            "manifest_freshness_window_seconds": self.freshness_window_seconds,
            "capabilities": capabilities,
            "endpoints": endpoints,
            "keys": [key.to_entry() for key in self.keys],
            "authorization_policy": authorization_policy,
            "cost_class": cost_class,
            "rate_limits": rate_limits,
            "status": "active",
        }
        return build_document(
            kind=KIND_MANIFEST,
            body=body,
            issuer_node_id=issuer_node_id,
            kid=signer.kid,
            private_raw=signer.private_raw,
        )

    def rotate_key(self, capabilities: list[str] | None = None) -> dict[str, Any]:
        """Rotate to a fresh key: new genesis-linked manifest signed by the old key.

        The old key is retired in the new manifest; ``node_id`` is unchanged.
        Returns the new manifest and appends it to the chain.
        """
        old_key = self.active_key
        previous = self.head_manifest
        private_raw, public_raw = crypto.generate_keypair()
        new_key = NodeKey(
            private_raw=private_raw,
            public_raw=public_raw,
            kid=crypto.kid_for(public_raw),
            valid_from=now_utc_z(),
            replaces=old_key.kid,
        )
        old_key.status = "retired"
        old_key.valid_until = now_utc_z()
        self.keys.append(new_key)
        manifest = self._build_manifest(
            sequence=self.head_sequence() + 1,
            previous_digest=content_digest_of(previous),
            capabilities=capabilities if capabilities is not None else list(previous["body"]["capabilities"]),
            endpoints=list(previous["body"]["endpoints"]),
            authorization_policy=dict(previous["body"]["authorization_policy"]),
            cost_class=dict(previous["body"]["cost_class"]),
            rate_limits=dict(previous["body"]["rate_limits"]),
            signer=old_key,
            issuer_node_id=self.node_id,
        )
        self.manifests.append(manifest)
        validate_document(manifest, KIND_MANIFEST)
        return manifest

    def revoke_key(self, kid: str) -> dict[str, Any]:
        """Publish a manifest revoking ``kid``, signed by a still-valid key."""
        if self.active_key.kid == kid:
            raise ValueError("cannot revoke the only active key with itself")
        target = next((key for key in self.keys if key.kid == kid), None)
        if target is None:
            raise ValueError(f"unknown key {kid}")
        target.status = "revoked"
        target.revoked_at = now_utc_z()
        previous = self.head_manifest
        manifest = self._build_manifest(
            sequence=self.head_sequence() + 1,
            previous_digest=content_digest_of(previous),
            capabilities=list(previous["body"]["capabilities"]),
            endpoints=list(previous["body"]["endpoints"]),
            authorization_policy=dict(previous["body"]["authorization_policy"]),
            cost_class=dict(previous["body"]["cost_class"]),
            rate_limits=dict(previous["body"]["rate_limits"]),
            signer=self.active_key,
            issuer_node_id=self.node_id,
        )
        self.manifests.append(manifest)
        validate_document(manifest, KIND_MANIFEST)
        return manifest

    def sign_document(self, kind: str, body: dict[str, Any], kid: str | None = None) -> dict[str, Any]:
        """Build and sign a document of ``kind`` with the active key (or ``kid``)."""
        key = next((k for k in self.keys if k.kid == (kid or self.active_key.kid)), None)
        if key is None or key.status != "active":
            raise ValueError(f"key {kid or self.active_key.kid} is not active")
        return build_document(kind=kind, body=body, issuer_node_id=self.node_id, kid=key.kid, private_raw=key.private_raw)

    def to_json(self, directory: Path) -> None:
        """Persist the identity (public state) as ``node.json`` plus key table."""
        directory.mkdir(parents=True, exist_ok=True)
        public = {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "freshness_window_seconds": self.freshness_window_seconds,
            "manifests": self.manifests,
        }
        (directory / "node.json").write_text(json.dumps(public, indent=2) + "\n")
        self.save_keytable(directory / "keys")

    @classmethod
    def load(cls, directory: Path) -> "NodeIdentity":
        """Load a persisted identity from ``directory``.

        Reads ``node.json`` (node metadata + manifest chain) and the ``keys/``
        directory (private key material). Reconstructs the full ``NodeKey``
        table from the current (head) manifest's key entries, pairs each entry
        with the corresponding private-key file, and verifies that every loaded
        private key derives the expected public key and ``kid``.

        Fail-closed on missing, malformed, or mismatched key material;
        requires at least one active signing key with private material.
        """
        node_path = directory / "node.json"
        if not node_path.is_file():
            raise FileNotFoundError(f"{node_path} not found")
        data = json.loads(node_path.read_text(encoding="utf-8"))
        node_id = str(data["node_id"])
        display_name = str(data["display_name"])
        freshness_window_seconds = int(data.get("freshness_window_seconds", DEFAULT_FRESHNESS_WINDOW_SECONDS))
        manifests: list[dict[str, Any]] = list(data["manifests"])
        if not manifests:
            raise ValueError("loaded identity has no manifest chain")
        if not _NODE_ID_RE.match(node_id):
            raise ValueError(f"invalid node_id {node_id!r} in persisted data")
        keys_dir = directory / "keys"
        keys: list[NodeKey] = []
        head_keys = manifests[-1]["body"]["keys"]
        for entry in head_keys:
            kid = entry["kid"]
            key_file = keys_dir / f"{kid}.key"
            if not key_file.is_file():
                if entry["status"] == "active":
                    raise FileNotFoundError(
                        f"active key {kid} has no private material at {key_file}"
                    )
                # Retired or revoked keys without private material are OK.
                keys.append(NodeKey(
                    private_raw=b"",
                    public_raw=crypto.b64url_decode(entry["public_key"]),
                    kid=kid,
                    status=entry["status"],
                    valid_from=entry["valid_from"],
                    valid_until=entry.get("valid_until"),
                    revoked_at=entry.get("revoked_at"),
                    replaces=entry.get("replaces"),
                ))
                continue
            private_raw = key_file.read_bytes()
            if len(private_raw) != 32:
                raise ValueError(f"key file {key_file} has wrong length ({len(private_raw)}), expected 32")
            derived_public = crypto.private_key_from_raw(private_raw).public_key().public_bytes_raw()
            expected_public = crypto.b64url_decode(entry["public_key"])
            if derived_public != expected_public:
                raise ValueError(f"private key {kid} does not match manifest public key")
            derived_kid = crypto.kid_for(derived_public)
            if derived_kid != kid:
                raise ValueError(f"private key derived kid {derived_kid} != manifest kid {kid}")
            keys.append(NodeKey(
                private_raw=private_raw,
                public_raw=derived_public,
                kid=kid,
                status=entry["status"],
                valid_from=entry["valid_from"],
                valid_until=entry.get("valid_until"),
                revoked_at=entry.get("revoked_at"),
                replaces=entry.get("replaces"),
            ))
        active_with_key = [k for k in keys if k.status == "active" and k.private_raw]
        if not active_with_key:
            raise ValueError(f"no active key with private material for node {node_id}")
        identity = cls(
            node_id=node_id,
            display_name=display_name,
            keys=keys,
            manifests=manifests,
            freshness_window_seconds=freshness_window_seconds,
        )
        for manifest in manifests:
            validate_document(manifest, KIND_MANIFEST)
        return identity


# ---------------------------------------------------------------------------
# Manifest chain validation and key resolution (§8, §7.3 step 5)
# ---------------------------------------------------------------------------


@dataclass
class ChainValidation:
    ok: bool
    reason: str = ""
    head_sequence: int | None = None
    head_digest: str | None = None


def validate_manifest_chain(
    chain: list[dict[str, Any]],
    *,
    head_sequence: int | None = None,
    head_digest: str | None = None,
) -> ChainValidation:
    """Validate a pinned manifest chain starting from the locally approved anchor.

    Rules enforced (§8):
      1. sequences increase monotonically;
      2. each non-genesis manifest's ``previous_manifest_digest`` equals the
         digest of the previous manifest in the chain;
      3. every manifest is schema-valid and correctly signed: the genesis by a
         key active in itself (possession proof), each successor by a key that
         was active in the previous manifest at the successor's ``issued_at``;
      4. the chain head matches the supplied pinned head sequence/digest.
    """
    if not chain:
        return ChainValidation(ok=False, reason="empty manifest chain")
    for index, manifest in enumerate(chain):
        try:
            validate_document(manifest, KIND_MANIFEST)
        except Exception as exc:  # schema or structural failure
            return ChainValidation(ok=False, reason=f"manifest[{index}] invalid: {exc}")
        body = manifest["body"]
        if int(body["manifest_sequence"]) != index + 1:
            return ChainValidation(ok=False, reason=f"manifest[{index}] sequence {body['manifest_sequence']} != {index + 1}")
        if index > 0:
            expected = content_digest_of(chain[index - 1])
            actual = body["previous_manifest_digest"]
            if actual != expected:
                return ChainValidation(ok=False, reason=f"manifest[{index}] broken digest link: {actual} != {expected}")
        else:
            if body["previous_manifest_digest"] is not None:
                return ChainValidation(ok=False, reason="genesis manifest must have null previous_manifest_digest")
        if not _signature_valid_in_context(manifest, chain[:index]):
            return ChainValidation(ok=False, reason=f"manifest[{index}] signature invalid for its issuing context")
        if body["node_id"] != manifest["issuer"]["node_id"]:
            return ChainValidation(ok=False, reason=f"manifest[{index}] issuer/node_id mismatch")
    head = chain[-1]
    sequence = int(head["body"]["manifest_sequence"])
    digest = content_digest_of(head)
    if head_sequence is not None and sequence != head_sequence:
        return ChainValidation(ok=False, reason=f"head sequence {sequence} != pinned {head_sequence}", head_sequence=sequence, head_digest=digest)
    if head_digest is not None and digest != head_digest:
        return ChainValidation(ok=False, reason=f"head digest {digest} != pinned {head_digest}", head_sequence=sequence, head_digest=digest)
    return ChainValidation(ok=True, reason="ok", head_sequence=sequence, head_digest=digest)


def _signature_valid_in_context(manifest: dict[str, Any], prior: list[dict[str, Any]]) -> bool:
    """Verify ``manifest``'s signature with a key active in the prior context."""
    issuer = manifest["issuer"]
    at = parse_timestamp(manifest["issued_at"])
    context = prior[-1] if prior else manifest
    public_raw = _find_active_key(context, issuer["kid"], at)
    if public_raw is None:
        return False
    data = canonical.canonical_bytes(canonical.strip_signature(manifest))
    return crypto.verify_canonical(data, manifest["signature"]["value"], public_raw)


def _find_active_key(manifest: dict[str, Any], kid: str, at: datetime) -> bytes | None:
    """Return the raw public key for ``kid`` if it is active in ``manifest`` at ``at``."""
    for entry in manifest["body"]["keys"]:
        if entry["kid"] != kid:
            continue
        if entry["status"] != "active":
            return None
        if parse_timestamp(entry["valid_from"]) > at:
            return None
        if entry.get("valid_until") is not None and parse_timestamp(entry["valid_until"]) <= at:
            return None
        if entry.get("revoked_at") is not None:
            return None
        try:
            return crypto.b64url_decode(entry["public_key"])
        except ValueError:
            return None
    return None


def resolve_key_for(
    chain: list[dict[str, Any]],
    node_id: str,
    kid: str,
    at: datetime,
) -> bytes | None:
    """Resolve ``kid`` to raw public bytes for ``node_id`` within a validated chain.

    Only the newest manifest with ``issued_at <= at`` is consulted; the key
    must be active at ``at``. Returns ``None`` when unresolved or inactive.
    """
    relevant: list[dict[str, Any]] = [m for m in chain if parse_timestamp(m["issued_at"]) <= at]
    if not relevant:
        return None
    head = relevant[-1]
    if head["body"]["node_id"] != node_id:
        return None
    return _find_active_key(head, kid, at)


def resolve_key_in_manifest(manifest: dict[str, Any], kid: str, at: datetime) -> bytes | None:
    """Resolve ``kid`` against a single manifest's key table at time ``at``.

    Used for a node manifest's own signature: the signing key is validated
    against the manifest state that precedes it (the previous manifest, or the
    genesis manifest itself for a self-signed anchor).
    """
    return _find_active_key(manifest, kid, at)


def chain_manifest_freshness(chain: list[dict[str, Any]]) -> int:
    return int(chain[-1]["body"]["manifest_freshness_window_seconds"])

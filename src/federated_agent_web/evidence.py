"""Offline evidence-bundle verification for FAW adoption.

Operates entirely on committed public material. No private keys, no network
access, no external writes. Reuses existing core verification without
duplicating it.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import canonical
from .canonical import digest_bytes
from .documents import (
    KIND_DELEGATION,
    KIND_RECEIPT,
    content_digest_of,
    parse_timestamp,
    validate_document,
)
from .identity import validate_manifest_chain
from .pending import PendingDelegationStore
from .verify import (
    PinnedManifestTrustContext,
    VerificationPolicy,
    verify,
)

__all__ = ["verify_evidence_bundle", "EvidenceError"]


class EvidenceError(ValueError):
    """Raised when an evidence bundle cannot be verified."""


def _safe_resolve(bundle_root: Path, relative: str) -> Path:
    """Resolve a bundle-relative path; reject traversal and absolute paths."""
    if not relative:
        raise EvidenceError("path must not be empty")
    if relative.startswith("/") or (len(relative) > 1 and relative[1] == ":"):
        raise EvidenceError(f"absolute path rejected: {relative!r}")
    root = bundle_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise EvidenceError(f"path escapes bundle: {relative!r}")
    return resolved


def _load_public_chain(path: Path) -> tuple[list[dict[str, Any]], PinnedManifestTrustContext]:
    data = canonical.parse_strict(path.read_bytes())
    manifests: list[dict[str, Any]] = list(data["manifests"])
    if not manifests:
        raise EvidenceError(f"empty manifest chain in {path}")
    chain_check = validate_manifest_chain(manifests)
    if not chain_check.ok:
        raise EvidenceError(f"invalid manifest chain in {path}: {chain_check.reason}")
    trust = PinnedManifestTrustContext(
        chain=manifests,
        head_sequence=int(manifests[-1]["body"]["manifest_sequence"]),
        head_digest=content_digest_of(manifests[-1]),
        pinned_at=parse_timestamp(manifests[-1]["issued_at"]),
    )
    return manifests, trust


def _report_ok(result: dict[str, Any]) -> str:
    lines = [
        "evidence: OK",
        f"spec_version: 0.2",
        f"temporal_mode: historical",
        f"delegation_verification_time: {result['delegation_verification_time']}",
        f"receipt_verification_time: {result['receipt_verification_time']}",
        f"issuer_node_id: {result['issuer_node_id']}",
        f"executor_node_id: {result['executor_node_id']}",
        f"task_id: {result['task_id']}",
        f"attempt_id: {result['attempt_id']}",
        f"delegation_digest: {result['delegation_digest']}",
        f"receipt_digest: {result['receipt_digest']}",
        f"receipt_status: {result['receipt_status']}",
        f"artifact_digest: {result['artifact_digest']}",
        "receipt_binding: verified",
        "issuer_acceptance: not asserted",
        "current_admissibility: not evaluated",
        "private_keys_required: no",
        "network_access: none",
        "external_writes: none",
    ]
    return "\n".join(lines)


def verify_evidence_bundle(bundle_dir: Path) -> str:
    """Verify a complete evidence bundle; return a report string.

    Raises ``EvidenceError`` on any verification failure.
    """
    bundle_root = bundle_dir.resolve()
    meta_path = bundle_root / "bundle.json"
    if not meta_path.is_file():
        raise EvidenceError(f"bundle.json not found in {bundle_dir}")

    meta = canonical.parse_strict(meta_path.read_bytes())

    # 1. Resolve all paths safely
    issuer_path = _safe_resolve(bundle_root, str(meta["issuer_state"]))
    executor_path = _safe_resolve(bundle_root, str(meta["executor_state"]))
    delegation_path = _safe_resolve(bundle_root, str(meta["delegation"]))
    receipt_path = _safe_resolve(bundle_root, str(meta["receipt"]))
    artifacts_meta = list(meta["artifacts"])
    if len(artifacts_meta) != 1:
        raise EvidenceError(f"bundle.json must declare exactly 1 artifact, got {len(artifacts_meta)}")
    artifact_ref = str(artifacts_meta[0])

    for p in [issuer_path, executor_path, delegation_path, receipt_path]:
        if not p.is_file():
            raise EvidenceError(f"bundle file not found: {p}")

    # 2. Load public manifest chains (strict parse)
    issuer_chain, issuer_trust = _load_public_chain(issuer_path)
    executor_chain, executor_trust = _load_public_chain(executor_path)
    issuer_node_id = issuer_chain[-1]["body"]["node_id"]
    executor_node_id = executor_chain[-1]["body"]["node_id"]

    # 3. Strict parse and schema-validate documents
    delegation = canonical.parse_strict(delegation_path.read_bytes())
    validate_document(delegation, KIND_DELEGATION)
    receipt = canonical.parse_strict(receipt_path.read_bytes())
    validate_document(receipt, KIND_RECEIPT)

    # 4. Verify relationships the core does not enforce in this context
    dbody = delegation["body"]
    rbody = receipt["body"]
    if dbody["issuer_node_id"] != issuer_node_id:
        raise EvidenceError("delegation issuer does not match issuer manifest chain")
    if dbody["target_node_id"] != executor_node_id:
        raise EvidenceError("delegation target does not match executor manifest chain")
    if rbody["executor_node_id"] != executor_node_id:
        raise EvidenceError("receipt executor does not match executor manifest chain")
    if rbody["task_id"] != dbody["task_id"]:
        raise EvidenceError("receipt task_id does not match delegation")
    if rbody["attempt_id"] != dbody["attempt_id"]:
        raise EvidenceError("receipt attempt_id does not match delegation")

    delegation_digest = content_digest_of(delegation)
    receipt_digest = content_digest_of(receipt)
    if rbody["delegation_digest"] != delegation_digest:
        raise EvidenceError("receipt delegation_digest does not match committed delegation")

    # 5. Historical delegation verification at receipt started_at
    #    (uses core verify(), which includes signature verification)
    delegation_time = parse_timestamp(rbody["started_at"])
    receipt_time = parse_timestamp(receipt["issued_at"])

    issuer_trust_hist = PinnedManifestTrustContext(
        chain=issuer_chain,
        head_sequence=issuer_trust.head_sequence,
        head_digest=issuer_trust.head_digest,
        pinned_at=delegation_time,
    )
    delegation_result = verify(
        delegation_path.read_bytes(),
        expected_kind=KIND_DELEGATION,
        local_node_id=executor_node_id,
        trust_context=issuer_trust_hist,
        local_policy=VerificationPolicy(
            allowed_actions={"hash_file"},
            allowed_external_effects=frozenset({"none"}),
        ),
        now=delegation_time,
    )
    if not delegation_result.ok:
        raise EvidenceError(f"historical delegation verification failed: {delegation_result.reason}")

    # 6. Receipt binding through ephemeral pending store
    #    (uses core verify(), which includes signature verification)
    with tempfile.TemporaryDirectory() as td:
        pending = PendingDelegationStore(Path(td))
        pending.register_outstanding(delegation, delegation_digest)

        executor_trust_hist = PinnedManifestTrustContext(
            chain=executor_chain,
            head_sequence=executor_trust.head_sequence,
            head_digest=executor_trust.head_digest,
            pinned_at=receipt_time,
        )
        receipt_result = verify(
            receipt_path.read_bytes(),
            expected_kind=KIND_RECEIPT,
            local_node_id=issuer_node_id,
            trust_context=executor_trust_hist,
            local_policy=VerificationPolicy(),
            now=receipt_time,
            pending_store=pending,
        )
        if not receipt_result.ok:
            raise EvidenceError(f"receipt binding verification failed: {receipt_result.reason}")

    # 7. Verify artifact — exactly one, path bound, digest and size match
    receipt_artifacts = list(rbody["artifacts"])
    if len(receipt_artifacts) != 1:
        raise EvidenceError(f"receipt must declare exactly 1 artifact, got {len(receipt_artifacts)}")
    ra = receipt_artifacts[0]

    if ra["location"] != artifact_ref:
        raise EvidenceError(
            f"bundle metadata artifact path {artifact_ref!r} != receipt location {ra['location']!r}"
        )
    if ra["location"].startswith("/") or ".." in ra["location"]:
        raise EvidenceError(f"receipt artifact location must be a relative path: {ra['location']!r}")

    artifact_path = _safe_resolve(bundle_root, ra["location"])
    if not artifact_path.is_file():
        raise EvidenceError(f"artifact not found: {artifact_path}")

    artifact_bytes = artifact_path.read_bytes()
    actual_size = len(artifact_bytes)
    if actual_size != ra["size"]:
        raise EvidenceError(f"artifact size {actual_size} != receipt {ra['size']}")

    actual_digest = digest_bytes(artifact_bytes)
    if actual_digest != ra["digest"]:
        raise EvidenceError(f"artifact digest {actual_digest} != receipt {ra['digest']}")

    if artifact_path.name != ra["name"]:
        raise EvidenceError(f"artifact file name {artifact_path.name!r} != receipt name {ra['name']!r}")

    return _report_ok({
        "delegation_verification_time": rbody["started_at"],
        "receipt_verification_time": receipt["issued_at"],
        "issuer_node_id": issuer_node_id,
        "executor_node_id": executor_node_id,
        "task_id": dbody["task_id"],
        "attempt_id": dbody["attempt_id"],
        "delegation_digest": delegation_digest,
        "receipt_digest": receipt_digest,
        "receipt_status": rbody["status"],
        "artifact_digest": actual_digest,
    })

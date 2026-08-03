"""Offline evidence-bundle verification for FAW adoption (§3).

Operates entirely on committed public material. No private keys, no network
access, no external writes. Reuses existing core verification without
duplicating it.
"""

from __future__ import annotations

import json
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
    if not relative or relative.startswith("/"):
        raise EvidenceError(f"path must be relative: {relative!r}")
    resolved = (bundle_root / relative).resolve()
    if not str(resolved).startswith(str(bundle_root.resolve())):
        raise EvidenceError(f"path escapes bundle: {relative!r}")
    return resolved


def _load_public_chain(path: Path) -> tuple[list[dict[str, Any]], PinnedManifestTrustContext]:
    data = json.loads(path.read_text(encoding="utf-8"))
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

    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # 1. Resolve all paths safely
    issuer_path = _safe_resolve(bundle_root, str(meta["issuer_state"]))
    executor_path = _safe_resolve(bundle_root, str(meta["executor_state"]))
    delegation_path = _safe_resolve(bundle_root, str(meta["delegation"]))
    receipt_path = _safe_resolve(bundle_root, str(meta["receipt"]))
    artifact_rel = str(meta["artifacts"][0])
    artifact_path = _safe_resolve(bundle_root, artifact_rel)

    for p in [issuer_path, executor_path, delegation_path, receipt_path, artifact_path]:
        if not p.is_file():
            raise EvidenceError(f"bundle file not found: {p}")

    # 2. Load public manifest chains
    issuer_chain, issuer_trust = _load_public_chain(issuer_path)
    executor_chain, executor_trust = _load_public_chain(executor_path)
    issuer_node_id = issuer_chain[-1]["body"]["node_id"]
    executor_node_id = executor_chain[-1]["body"]["node_id"]

    # 3. Strict parse and schema-validate documents
    delegation = canonical.parse_strict(delegation_path.read_bytes())
    validate_document(delegation, KIND_DELEGATION)
    receipt = canonical.parse_strict(receipt_path.read_bytes())
    validate_document(receipt, KIND_RECEIPT)

    # 4. Verify relationships that the core does not check in verify()
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

    # 5. Verify signatures against respective manifest chains
    if not _verify_doc_signature(delegation, issuer_chain):
        raise EvidenceError("delegation signature invalid against issuer manifest chain")
    if not _verify_doc_signature(receipt, executor_chain):
        raise EvidenceError("receipt signature invalid against executor manifest chain")

    # 6. Historical delegation verification at receipt started_at
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

    # 7. Receipt binding through ephemeral pending store
    import tempfile
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

    # 8. Verify artifact digest
    artifact_bytes = artifact_path.read_bytes()
    expected_artifact_digest = rbody["artifacts"][0]["digest"]
    actual_artifact_digest = digest_bytes(artifact_bytes)
    if actual_artifact_digest != expected_artifact_digest:
        raise EvidenceError(
            f"artifact digest mismatch: {actual_artifact_digest} != {expected_artifact_digest}"
        )

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
        "artifact_digest": actual_artifact_digest,
    })


def _verify_doc_signature(doc: dict[str, Any], chain: list[dict[str, Any]]) -> bool:
    """Verify a document's Ed25519 signature against a manifest chain."""
    from . import crypto
    from .identity import resolve_key_for
    at = parse_timestamp(doc["issued_at"])
    public_raw = resolve_key_for(chain, doc["issuer"]["node_id"], doc["issuer"]["kid"], at)
    if public_raw is None:
        return False
    canon = canonical.canonical_bytes(canonical.strip_signature(doc))
    return crypto.verify_canonical(canon, doc["signature"]["value"], public_raw)

"""Normative verification procedure (§7.3).

``verify`` executes the authoritative ordered steps 1–11. A later step never
runs, and no capability handler, artifact consumer, or repository mutation may
occur, until every applicable earlier step has passed. A verification result is
NOT equivalent to authorization: steps 1–9 establish document validity and
binding; steps 10–11 establish local admission for a delegation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import canonical, crypto
from .documents import (
    KIND_DELEGATION,
    KIND_MANIFEST,
    KIND_RECEIPT,
    content_digest_of,
    parse_timestamp,
    validate_document,
)
from .identity import (
    ChainValidation,
    chain_manifest_freshness,
    resolve_key_for,
    resolve_key_in_manifest,
    validate_manifest_chain,
)
from .pending import PendingDelegationStore, PendingStoreError
from .replay import (
    ReplayAlreadyAdmitted,
    ReplayIntegrityViolation,
    ReplayRecord,
    ReplayStore,
)

__all__ = [
    "PinnedManifestTrustContext",
    "VerificationPolicy",
    "VerificationResult",
    "verify",
]

DEFAULT_CLOCK_SKEW_SECONDS = 60
DEFAULT_FRESHNESS_WINDOW = 3600


@dataclass(frozen=True)
class PinnedManifestTrustContext:
    """Locally supplied pinned manifest state (§7.3).

    Contains, for every trusted issuer: the ordered pinned manifest chain, the
    head sequence and digest, and the time the head was pinned/observed. No
    online manifest lookup is performed by the v0.2 core.
    """

    chain: list[dict[str, Any]]
    head_sequence: int
    head_digest: str
    pinned_at: datetime

    @classmethod
    def from_chain(cls, chain: list[dict[str, Any]], pinned_at: datetime | None = None) -> "PinnedManifestTrustContext":
        head = chain[-1]
        return cls(
            chain=list(chain),
            head_sequence=int(head["body"]["manifest_sequence"]),
            head_digest=content_digest_of(head),
            pinned_at=pinned_at or datetime.now(timezone.utc),
        )


@dataclass
class VerificationPolicy:
    """Local policy governing admission (§7.3 steps 10–11, §6 revocation)."""

    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS
    reject_stale: bool = False
    can_enforce_tokens: bool = True
    can_enforce_cost: bool = False
    allowed_external_effects: frozenset[str] = field(default_factory=lambda: frozenset({"none"}))
    allowed_actions: set[str] | None = None
    capability_targets: dict[str, str] = field(default_factory=dict)
    max_wall_seconds_cap: int | None = None
    max_output_bytes_cap: int | None = None


@dataclass
class VerificationResult:
    ok: bool = False
    step: int | None = None
    reason: str = ""
    freshness: str | None = None
    head_sequence: int | None = None
    head_digest: str | None = None
    resolved_kid: str | None = None
    delegation_digest: str | None = None
    replay_state: str | None = None
    terminal_receipt: dict[str, Any] | None = None
    admitted: bool = False
    deduplicated: bool = False

    @property
    def stale(self) -> bool:
        return self.freshness == "stale"


def _fail(step: int, reason: str, result: VerificationResult) -> VerificationResult:
    result.ok = False
    result.step = step
    result.reason = reason
    return result


def verify(
    document_bytes: bytes,
    *,
    expected_kind: str,
    local_node_id: str | None,
    trust_context: PinnedManifestTrustContext,
    local_policy: VerificationPolicy,
    now: datetime,
    replay_store: ReplayStore | None = None,
    pending_store: PendingDelegationStore | None = None,
) -> VerificationResult:
    """Verify a signed document per the authoritative §7.3 order.

    - ``expected_kind`` is the trusted kind; never inferred from input.
    - ``replay_store`` (receiver side) enables steps 9 and 11 for delegations.
    - ``pending_store`` (issuer side) is required to bind receipts (step 8).
    """
    result = VerificationResult()

    # -- Step 1: strict parse ------------------------------------------------
    try:
        doc = canonical.parse_strict(document_bytes)
    except canonical.CanonicalizationError as exc:
        return _fail(1, f"strict parse failed: {exc}", result)

    # -- Step 2: expected-kind schema validation ------------------------------
    try:
        validate_document(doc, expected_kind)
    except ValueError as exc:
        return _fail(2, f"schema validation failed: {exc}", result)

    body = doc["body"]
    issued_at = parse_timestamp(doc["issued_at"])

    # -- Step 3: audience binding (delegations only) ---------------------------
    if expected_kind == KIND_DELEGATION:
        if "target_node_id" in body:
            if local_node_id is None or body["target_node_id"] != local_node_id:
                return _fail(3, "delegation is not addressed to this node", result)
        elif "capability_target" in body:
            capability = body["capability_target"]["capability"]
            if local_policy.capability_targets.get(capability) != local_node_id:
                return _fail(
                    3,
                    f"capability-addressed target {capability!r} not matched by local policy",
                    result,
                )
        else:  # schema oneOf guarantees one branch; defensive
            return _fail(3, "delegation has no resolvable target", result)

    # -- Step 4: temporal and structural admission -----------------------------
    if expected_kind == KIND_DELEGATION:
        expires_at = parse_timestamp(body["expires_at"])
        deadline = parse_timestamp(body["deadline"])
        authority_expiry = parse_timestamp(body["authority"]["expiry"])
        if not (issued_at < expires_at):
            return _fail(4, "issued_at >= expires_at is rejected", result)
        if expires_at > deadline:
            return _fail(4, "expires_at > deadline is rejected", result)
        if authority_expiry < deadline:
            return _fail(4, "authority.expiry precedes deadline", result)
        if now > expires_at + _timedelta_seconds(local_policy.clock_skew_seconds):
            return _fail(4, "delegation expired before admission", result)
    elif expected_kind == KIND_RECEIPT:
        if body.get("started_at") is not None:
            if parse_timestamp(body["finished_at"]) < parse_timestamp(body["started_at"]):
                return _fail(4, "finished_at precedes started_at", result)
        if body["executor_node_id"] != doc["issuer"]["node_id"]:
            return _fail(4, "receipt issuer must be the executor", result)

    # -- Step 5: pinned manifest-chain validation and key resolution ------------
    chain_check: ChainValidation = validate_manifest_chain(
        trust_context.chain,
        head_sequence=trust_context.head_sequence,
        head_digest=trust_context.head_digest,
    )
    if not chain_check.ok:
        return _fail(5, f"pinned manifest chain invalid: {chain_check.reason}", result)
    public_raw = _resolve_document_key(
        doc, expected_kind, trust_context.chain, issued_at
    )
    if public_raw is None:
        return _fail(
            5,
            f"issuer.kid {doc['issuer']['kid']} not active in pinned chain for "
            f"{doc['issuer']['node_id']} at {doc['issued_at']}",
            result,
        )
    result.resolved_kid = doc["issuer"]["kid"]

    # -- Step 6: revocation and trust-context freshness -------------------------
    window = chain_manifest_freshness(trust_context.chain)
    is_stale = trust_context.pinned_at + _timedelta_seconds(window) < now
    result.freshness = "stale" if is_stale else "fresh"
    result.head_sequence = chain_check.head_sequence
    result.head_digest = chain_check.head_digest
    if is_stale:
        if local_policy.reject_stale:
            return _fail(6, "pinned manifest context is stale", result)
        result.reason = "stale pinned manifest context (qualified result)"

    # -- Step 7: core signature verification ------------------------------------
    canonical_bytes = canonical.canonical_bytes(canonical.strip_signature(doc))
    if not crypto.verify_canonical(canonical_bytes, doc["signature"]["value"], public_raw):
        return _fail(7, "core signature verification failed", result)
    result.delegation_digest = content_digest_of(doc)

    # -- Step 8: document binding (receipts, issuer side) ------------------------
    if expected_kind == KIND_RECEIPT:
        if pending_store is None:
            return _fail(8, "receipt verification requires a pending_store", result)
        task_id, attempt_id = body["task_id"], body["attempt_id"]
        digest, executor = body["delegation_digest"], body["executor_node_id"]
        record = pending_store.get_outstanding(task_id, attempt_id)
        if record is None:
            return _fail(8, f"receipt references unknown delegation {task_id}/{attempt_id}", result)
        if record.state != "outstanding":
            return _fail(8, f"delegation {task_id}/{attempt_id} is already {record.state}", result)
        if record.delegation_digest != digest:
            return _fail(8, f"receipt digest {digest} does not match outstanding {record.delegation_digest}", result)
        if executor != record.target_node_id:
            return _fail(8, f"receipt executor {executor} != target {record.target_node_id}", result)
        try:
            closed = pending_store.accept_terminal(doc)
        except PendingStoreError as exc:
            return _fail(8, f"receipt acceptance failed: {exc}", result)
        result.terminal_receipt = closed.terminal_receipt
        result.ok = True
        result.admitted = True
        return result

    # -- Step 9: replay lookup and integrity comparison (delegations) ------------
    if expected_kind == KIND_DELEGATION and replay_store is not None:
        issuer_node_id = body["issuer_node_id"]
        attempt_id = body["attempt_id"]
        digest = result.delegation_digest
        existing: ReplayRecord | None = replay_store.get(issuer_node_id, attempt_id)
        if existing is not None:
            if existing.delegation_digest != digest:
                return _fail(
                    9,
                    f"attempt {attempt_id} replay digest mismatch: "
                    f"{existing.delegation_digest} != {digest}",
                    result,
                )
            result.ok = True
            result.replay_state = existing.state
            result.terminal_receipt = existing.receipt
            result.deduplicated = True
            result.admitted = False
            return result

    # -- Step 10: local authority and budget evaluation --------------------------
    if expected_kind == KIND_DELEGATION:
        admission = _evaluate_authority_and_budget(body, local_policy)
        if admission is not None:
            return _fail(10, admission, result)

    # -- Step 11: atomic admission (delegations) ---------------------------------
    if expected_kind == KIND_DELEGATION and replay_store is not None:
        try:
            replay_store.create(body["issuer_node_id"], body["attempt_id"], result.delegation_digest)
        except ReplayAlreadyAdmitted as exc:
            existing = exc.record
            if existing.delegation_digest != result.delegation_digest:
                return _fail(9, "concurrent replay digest mismatch", result)
            result.ok = True
            result.replay_state = existing.state
            result.terminal_receipt = existing.receipt
            result.deduplicated = True
            return result
        except ReplayIntegrityViolation as exc:
            return _fail(9, str(exc), result)
        result.replay_state = "pending"
        result.admitted = True

    result.ok = True
    return result


def _resolve_document_key(
    doc: dict[str, Any],
    expected_kind: str,
    chain: list[dict[str, Any]],
    at: datetime,
) -> bytes | None:
    """Resolve the document's issuer key within the pinned chain.

    A node manifest is signed by a key valid in the state BEFORE the manifest
    (the previous manifest; the genesis signs itself as a possession proof), so
    its own signature is checked against that prior context. Delegations and
    receipts resolve against the newest manifest at their ``issued_at``.
    """
    if expected_kind == KIND_MANIFEST:
        doc_digest = content_digest_of(doc)
        for index, manifest in enumerate(chain):
            if content_digest_of(manifest) == doc_digest:
                context = chain[index - 1] if index > 0 else chain[index]
                return resolve_key_in_manifest(context, doc["issuer"]["kid"], at)
        context = chain[0] if chain else doc
        return resolve_key_in_manifest(context, doc["issuer"]["kid"], at)
    return resolve_key_for(chain, doc["issuer"]["node_id"], doc["issuer"]["kid"], at)


def _evaluate_authority_and_budget(body: dict[str, Any], policy: VerificationPolicy) -> str | None:
    """Return an error message when authority/budget cannot be enforced; else None."""
    authority = body["authority"]
    capability = body["capability"]
    if capability not in authority["actions"]:
        return f"capability {capability!r} not authorized by authority.actions"
    if policy.allowed_actions is not None:
        for action in authority["actions"]:
            if action not in policy.allowed_actions:
                return f"action {action!r} not permitted by local policy"
    external = authority.get("external_effect_scope", {}).get("allowed_effects", [])
    for effect in external:
        if effect not in policy.allowed_external_effects:
            return f"external effect {effect!r} not permitted by local policy"

    budget = body["budget"]
    if not budget:
        return "unbounded budget is not enforceable"
    if "max_wall_seconds" in budget:
        ceiling = int(budget["max_wall_seconds"])
        if policy.max_wall_seconds_cap is not None and ceiling > policy.max_wall_seconds_cap:
            return f"max_wall_seconds {ceiling} exceeds local cap {policy.max_wall_seconds_cap}"
    if "max_tokens" in budget and not policy.can_enforce_tokens:
        return "max_tokens ceiling cannot be measured or enforced locally"
    if "max_cost_usd" in budget and not policy.can_enforce_cost:
        return "max_cost_usd ceiling cannot be measured or enforced locally"
    if "max_output_bytes" in budget:
        ceiling = int(budget["max_output_bytes"])
        if policy.max_output_bytes_cap is not None and ceiling > policy.max_output_bytes_cap:
            return f"max_output_bytes {ceiling} exceeds local cap {policy.max_output_bytes_cap}"
    return None


def _timedelta_seconds(seconds: int) -> Any:
    from datetime import timedelta

    return timedelta(seconds=seconds)

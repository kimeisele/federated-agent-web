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
    datetime_to_ns,
    parse_timestamp_ns,
    validate_document,
)
from .identity import (
    ChainValidation,
    KeyResolution,
    chain_manifest_freshness,
    resolve_key_detailed,
    resolve_key_in_manifest_detailed,
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
    reason_code: str | None = None
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


def _fail(step: int, reason: str, result: VerificationResult, *, code: str) -> VerificationResult:
    result.ok = False
    result.step = step
    result.reason = reason
    result.reason_code = code
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
        if isinstance(exc, canonical.DuplicateMemberError):
            code = "parse.duplicate_member"
        elif isinstance(exc, canonical.InvalidUnicodeError):
            code = "parse.invalid_unicode"
        elif isinstance(exc, canonical.UnsupportedNumberError):
            code = "canonicalization.number_out_of_domain"
        else:
            code = "parse.invalid_json"
        return _fail(1, f"strict parse failed: {exc}", result, code=code)

    # -- Step 2: expected-kind schema validation ------------------------------
    if isinstance(doc, dict) and "kind" in doc:
        if doc["kind"] != expected_kind:
            return _fail(
                2,
                f"document kind {doc['kind']!r} does not match expected {expected_kind!r}",
                result,
                code="document.kind_mismatch",
            )
    try:
        validate_document(doc, expected_kind)
    except ValueError as exc:
        return _fail(2, f"schema validation failed: {exc}", result, code="schema.invalid")

    body = doc["body"]
    issued_at = parse_timestamp_ns(doc["issued_at"])

    # -- Step 3: audience binding (delegations only) ---------------------------
    if expected_kind == KIND_DELEGATION:
        if "target_node_id" in body:
            if local_node_id is None or body["target_node_id"] != local_node_id:
                return _fail(3, "delegation is not addressed to this node", result, code="audience.mismatch")
        elif "capability_target" in body:
            capability = body["capability_target"]["capability"]
            if local_policy.capability_targets.get(capability) != local_node_id:
                return _fail(
                    3,
                    f"capability-addressed target {capability!r} not matched by local policy",
                    result,
                    code="audience.mismatch",
                )
        else:  # schema oneOf guarantees one branch; defensive
            return _fail(3, "delegation has no resolvable target", result, code="audience.mismatch")

    # -- Step 4: temporal and structural admission -----------------------------
    if expected_kind == KIND_DELEGATION:
        expires_at = parse_timestamp_ns(body["expires_at"])
        deadline = parse_timestamp_ns(body["deadline"])
        authority_expiry = parse_timestamp_ns(body["authority"]["expiry"])
        if not (issued_at < expires_at):
            return _fail(4, "issued_at >= expires_at is rejected", result, code="temporal.invalid")
        if expires_at > deadline:
            return _fail(4, "expires_at > deadline is rejected", result, code="temporal.invalid")
        if authority_expiry < deadline:
            return _fail(4, "authority.expiry precedes deadline", result, code="temporal.invalid")
        skew_ns = local_policy.clock_skew_seconds * 1_000_000_000
        if datetime_to_ns(now) > expires_at + skew_ns:
            return _fail(4, "delegation expired before admission", result, code="temporal.invalid")
    elif expected_kind == KIND_RECEIPT:
        if body.get("started_at") is not None:
            if parse_timestamp_ns(body["finished_at"]) < parse_timestamp_ns(body["started_at"]):
                return _fail(4, "finished_at precedes started_at", result, code="temporal.invalid")
        if body["executor_node_id"] != doc["issuer"]["node_id"]:
            return _fail(4, "receipt issuer must be the executor", result, code="receipt.wrong_issuer")

    # -- Step 5: pinned manifest-chain validation and key resolution ------------
    chain_check: ChainValidation = validate_manifest_chain(
        trust_context.chain,
        head_sequence=trust_context.head_sequence,
        head_digest=trust_context.head_digest,
    )
    if not chain_check.ok:
        return _fail(5, f"pinned manifest chain invalid: {chain_check.reason}", result, code="trust.invalid_chain")
    resolution: KeyResolution = _resolve_document_key(
        doc, expected_kind, trust_context.chain, issued_at
    )
    if not resolution.ok:
        return _fail(
            5,
            f"issuer.kid {doc['issuer']['kid']} for {doc['issuer']['node_id']} at "
            f"{doc['issued_at']}: {resolution.reason}",
            result,
            code=resolution.code or "trust.unknown_key",
        )
    public_raw = resolution.public_raw
    result.resolved_kid = doc["issuer"]["kid"]

    # -- Step 6: revocation and trust-context freshness -------------------------
    window = chain_manifest_freshness(trust_context.chain)
    is_stale = datetime_to_ns(trust_context.pinned_at) + window * 1_000_000_000 < datetime_to_ns(now)
    result.freshness = "stale" if is_stale else "fresh"
    result.head_sequence = chain_check.head_sequence
    result.head_digest = chain_check.head_digest
    if is_stale:
        if local_policy.reject_stale:
            return _fail(6, "pinned manifest context is stale", result, code="trust.stale")
        result.reason = "stale pinned manifest context (qualified result)"

    # -- Step 7: core signature verification ------------------------------------
    canonical_bytes = canonical.canonical_bytes(canonical.strip_signature(doc))
    if not crypto.verify_canonical(canonical_bytes, doc["signature"]["value"], public_raw):
        return _fail(7, "core signature verification failed", result, code="signature.invalid")
    result.delegation_digest = content_digest_of(doc)

    # -- Step 8: document binding (receipts, issuer side) ------------------------
    if expected_kind == KIND_RECEIPT:
        if pending_store is None:
            return _fail(8, "receipt verification requires a pending_store", result, code="receipt.no_pending_store")
        task_id, attempt_id = body["task_id"], body["attempt_id"]
        digest, executor = body["delegation_digest"], body["executor_node_id"]
        record = pending_store.get_outstanding(task_id, attempt_id)
        if record is None:
            return _fail(8, f"receipt references unknown delegation {task_id}/{attempt_id}", result, code="receipt.no_pending_delegation")
        if record.state != "outstanding":
            return _fail(8, f"delegation {task_id}/{attempt_id} is already {record.state}", result, code="receipt.already_terminal")
        if record.delegation_digest != digest:
            return _fail(8, f"receipt digest {digest} does not match outstanding {record.delegation_digest}", result, code="binding.mismatch")
        if executor != record.target_node_id:
            return _fail(8, f"receipt executor {executor} != target {record.target_node_id}", result, code="receipt.wrong_executor")
        try:
            closed = pending_store.accept_terminal(doc)
        except PendingStoreError as exc:
            return _fail(8, f"receipt acceptance failed: {exc}", result, code="binding.mismatch")
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
                    code="replay.digest_conflict",
                )
            result.ok = True
            result.replay_state = existing.state
            result.terminal_receipt = existing.receipt
            result.deduplicated = True
            result.admitted = False
            return result

    # -- Step 10: local authority and budget evaluation --------------------------
    if expected_kind == KIND_DELEGATION:
        rejection = _evaluate_authority_and_budget(body, local_policy)
        if rejection is not None:
            return _fail(10, rejection.reason, result, code=rejection.code)

    # -- Step 11: atomic admission (delegations) ---------------------------------
    if expected_kind == KIND_DELEGATION and replay_store is not None:
        try:
            replay_store.create(body["issuer_node_id"], body["attempt_id"], result.delegation_digest)
        except ReplayAlreadyAdmitted as exc:
            existing = exc.record
            if existing.delegation_digest != result.delegation_digest:
                return _fail(9, "concurrent replay digest mismatch", result, code="replay.digest_conflict")
            result.ok = True
            result.replay_state = existing.state
            result.terminal_receipt = existing.receipt
            result.deduplicated = True
            return result
        except ReplayIntegrityViolation as exc:
            return _fail(9, str(exc), result, code="replay.digest_conflict")
        result.replay_state = "pending"
        result.admitted = True

    result.ok = True
    return result


def _resolve_document_key(
    doc: dict[str, Any],
    expected_kind: str,
    chain: list[dict[str, Any]],
    at_ns: int,
) -> KeyResolution:
    """Resolve the document's issuer key within the pinned chain (detailed).

    A node manifest is signed by a key valid in the state BEFORE the manifest
    (the previous manifest; the genesis signs itself as a possession proof), so
    its own signature is checked against that prior context. Delegations and
    receipts resolve against the newest manifest at their ``issued_at``.
    Returns a ``KeyResolution`` distinguishing unknown from not-valid keys.
    """
    if expected_kind == KIND_MANIFEST:
        doc_digest = content_digest_of(doc)
        for index, manifest in enumerate(chain):
            if content_digest_of(manifest) == doc_digest:
                context = chain[index - 1] if index > 0 else chain[index]
                return resolve_key_in_manifest_detailed(context, doc["issuer"]["kid"], at_ns)
        context = chain[0] if chain else doc
        return resolve_key_in_manifest_detailed(context, doc["issuer"]["kid"], at_ns)
    return resolve_key_detailed(chain, doc["issuer"]["node_id"], doc["issuer"]["kid"], at_ns)


@dataclass(frozen=True)
class AdmissionRejection:
    """Structured authority/budget rejection — code set at detection point."""
    code: str
    reason: str


def _evaluate_authority_and_budget(body: dict[str, Any], policy: VerificationPolicy) -> AdmissionRejection | None:
    """Return an error message when authority/budget cannot be enforced; else None."""
    authority = body["authority"]
    capability = body["capability"]
    if capability not in authority["actions"]:
        return AdmissionRejection("authority.action_denied", f"capability {capability!r} not authorized by authority.actions")
    if policy.allowed_actions is not None:
        for action in authority["actions"]:
            if action not in policy.allowed_actions:
                return AdmissionRejection("authority.action_denied", f"action {action!r} not permitted by local policy")
    external = authority.get("external_effect_scope", {}).get("allowed_effects", [])
    for effect in external:
        if effect not in policy.allowed_external_effects:
            return AdmissionRejection("authority.external_effect_denied", f"external effect {effect!r} not permitted by local policy")

    budget = body["budget"]
    if not budget:
        return AdmissionRejection("budget.unenforceable", "unbounded budget is not enforceable")
    if "max_wall_seconds" in budget:
        ceiling = int(budget["max_wall_seconds"])
        if policy.max_wall_seconds_cap is not None and ceiling > policy.max_wall_seconds_cap:
            return AdmissionRejection("budget.unenforceable", f"max_wall_seconds {ceiling} exceeds local cap {policy.max_wall_seconds_cap}")
    if "max_tokens" in budget and not policy.can_enforce_tokens:
        return AdmissionRejection("budget.unenforceable", "max_tokens ceiling cannot be measured or enforced locally")
    if "max_cost_usd" in budget and not policy.can_enforce_cost:
        return AdmissionRejection("budget.unenforceable", "max_cost_usd ceiling cannot be measured or enforced locally")
    if "max_output_bytes" in budget:
        ceiling = int(budget["max_output_bytes"])
        if policy.max_output_bytes_cap is not None and ceiling > policy.max_output_bytes_cap:
            return AdmissionRejection("budget.unenforceable", f"max_output_bytes {ceiling} exceeds local cap {policy.max_output_bytes_cap}")
    return None

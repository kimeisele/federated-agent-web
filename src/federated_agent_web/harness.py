"""Language-neutral conformance harness — Harness Implementation #1.

Implements the process contract documented in ``docs/HARNESS.md``: read
exactly one JSON request from stdin, verify the supplied document with the
existing FAW reference machinery, write exactly one JSON result to stdout,
and exit 0 for both protocol accept and protocol reject. Operational
failures exit non-zero and never encode a protocol rejection.

This harness reuses the existing parser/verifier/schema/trust logic
(``verify``, ``PinnedManifestTrustContext``, ``VerificationPolicy``). It is
not a second verifier: it contains no signature, schema, trust, temporal,
audience, authority, or budget logic. It never inspects expected outcomes,
never reads conformance fixtures, and operates only on its stdin request.

Run as: ``python -m federated_agent_web.harness``
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from typing import Any

from . import canonical
from .documents import KIND_DELEGATION, KIND_MANIFEST, KIND_RECEIPT
from .pending import PendingDelegation, PendingStoreError, STATE_OUTSTANDING, STATE_TERMINAL
from .verify import PinnedManifestTrustContext, VerificationPolicy, verify

HARNESS_VERSION = "1"
PROTOCOL_VERSION = "0.2"

# The exact thirteen stable rejection categories of the interoperability
# profile. A rejection code outside this set is a contract mismatch and is
# NEVER silently remapped into a stable category.
STABLE_CATEGORIES = frozenset(
    {
        "parse.invalid_json",
        "parse.duplicate_member",
        "parse.invalid_unicode",
        "canonicalization.number_out_of_domain",
        "schema.invalid",
        "document.kind_mismatch",
        "audience.mismatch",
        "temporal.invalid",
        "trust.invalid_chain",
        "trust.unknown_key",
        "trust.key_not_valid",
        "signature.invalid",
        "binding.mismatch",
    }
)

EXPECTED_KINDS = {KIND_DELEGATION, KIND_MANIFEST, KIND_RECEIPT}
PENDING_FIELDS = ("task_id", "attempt_id", "delegation_digest", "executor_node_id", "status")


class HarnessOperationalError(Exception):
    """Operational/harness failure (exit non-zero; never a protocol reject)."""


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HarnessOperationalError(f"invalid timestamp {value!r}: {exc}") from exc
    if parsed.tzinfo is None:
        raise HarnessOperationalError(f"timestamp {value!r} is not timezone-aware")
    return parsed


class InMemoryPendingStore:
    """Tiny in-memory semantic pending adapter for a single invocation.

    Adapts the language-neutral pending record to the ``verify()``
    pending-store interface. Contains only minimal state/storage behavior:
    no signature, schema, trust, temporal, audience, authority, or budget
    logic; all protocol verification stays in ``verify()``. One harness
    process handles exactly one request and exits, so no persistence,
    locking, or durable filesystem state is required.
    """

    def __init__(self, pending: dict[str, Any]) -> None:
        self._record = PendingDelegation(
            task_id=pending["task_id"],
            attempt_id=pending["attempt_id"],
            delegation_digest=pending["delegation_digest"],
            delegation={},  # full delegation bytes are not part of the contract
            issuer_node_id="",  # not part of the language-neutral context
            target_node_id=pending["executor_node_id"],
            issued_at="",
            expires_at="",
            deadline="",
            state=pending.get("status", STATE_OUTSTANDING),
        )

    def get_outstanding(self, task_id: str, attempt_id: str) -> PendingDelegation | None:
        record = self._record
        if record.task_id != task_id or record.attempt_id != attempt_id:
            return None
        return record

    def accept_terminal(self, receipt: dict[str, Any]) -> PendingDelegation:
        record = self._record
        body = receipt["body"]
        if record.state != STATE_OUTSTANDING:
            raise PendingStoreError(
                f"delegation {record.task_id}/{record.attempt_id} is already {record.state}"
            )
        if record.delegation_digest != body.get("delegation_digest"):
            raise PendingStoreError(
                "receipt digest does not match the outstanding delegation record"
            )
        if record.target_node_id != body.get("executor_node_id"):
            raise PendingStoreError("receipt executor != outstanding delegation target")
        record.state = STATE_TERMINAL
        record.terminal_receipt = receipt
        return record


def _validate_envelope(req: dict[str, Any]) -> None:
    if req.get("harness_version") != HARNESS_VERSION:
        raise HarnessOperationalError(
            f"unsupported harness_version {req.get('harness_version')!r} (expected {HARNESS_VERSION!r})"
        )
    if req.get("protocol_version") != PROTOCOL_VERSION:
        raise HarnessOperationalError(
            f"unsupported protocol_version {req.get('protocol_version')!r} (expected {PROTOCOL_VERSION!r})"
        )
    if not isinstance(req.get("request_id"), str) or not req["request_id"]:
        raise HarnessOperationalError("request_id must be a non-empty string")
    if req.get("expected_kind") not in EXPECTED_KINDS:
        raise HarnessOperationalError(
            f"unsupported expected_kind {req.get('expected_kind')!r}"
        )
    for key in ("now", "pinned_at"):
        if not isinstance(req.get(key), str):
            raise HarnessOperationalError(f"{key} must be a timestamp string")
    chain = req.get("trust_chain")
    if not isinstance(chain, list):
        raise HarnessOperationalError("trust_chain must be an array")
    for entry in chain:
        if not isinstance(entry, dict) or not isinstance(entry.get("bytes_b64"), str):
            raise HarnessOperationalError("trust_chain entries must be {\"bytes_b64\": ...}")
    if req.get("local_node_id") is not None and not isinstance(req["local_node_id"], str):
        raise HarnessOperationalError("local_node_id must be a string or null")
    if not isinstance(req.get("local_policy"), dict):
        raise HarnessOperationalError("local_policy must be an object")
    pending = req.get("pending")
    if pending is not None:
        if not isinstance(pending, dict):
            raise HarnessOperationalError("pending must be an object or null")
        missing = [f for f in PENDING_FIELDS if f not in pending]
        if missing:
            raise HarnessOperationalError(f"pending record missing fields: {missing}")


def _decode_b64(value: str, what: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise HarnessOperationalError(f"invalid base64 in {what}: {exc}") from exc


def _build_policy(local_policy: dict[str, Any]) -> VerificationPolicy:
    allowed_actions = local_policy.get("allowed_actions")
    effects = local_policy.get("allowed_external_effects")
    return VerificationPolicy(
        clock_skew_seconds=int(local_policy.get("clock_skew_seconds", 60)),
        reject_stale=bool(local_policy.get("reject_stale", False)),
        can_enforce_tokens=bool(local_policy.get("can_enforce_tokens", True)),
        can_enforce_cost=bool(local_policy.get("can_enforce_cost", False)),
        allowed_external_effects=frozenset(effects) if effects is not None else frozenset({"none"}),
        allowed_actions=set(allowed_actions) if allowed_actions is not None else None,
        capability_targets=dict(local_policy.get("capability_targets") or {}),
        max_wall_seconds_cap=local_policy.get("max_wall_seconds_cap"),
        max_output_bytes_cap=local_policy.get("max_output_bytes_cap"),
    )


def _handle_request(raw: bytes) -> str:
    try:
        req = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HarnessOperationalError(f"malformed request envelope: {exc}") from exc
    if not isinstance(req, dict):
        raise HarnessOperationalError("request envelope must be a JSON object")
    _validate_envelope(req)

    document_bytes = _decode_b64(req["document_bytes_b64"], "document_bytes_b64")
    chain: list[dict[str, Any]] = []
    for entry in req["trust_chain"]:
        raw_manifest = _decode_b64(entry["bytes_b64"], "trust_chain bytes")
        try:
            chain.append(canonical.parse_strict(raw_manifest))
        except canonical.CanonicalizationError as exc:
            raise HarnessOperationalError(f"trust chain manifest does not strict-parse: {exc}") from exc

    now = _parse_timestamp(req["now"])
    pinned_at = _parse_timestamp(req["pinned_at"])
    trust_context = PinnedManifestTrustContext.from_chain(chain, pinned_at=pinned_at)
    policy = _build_policy(req["local_policy"])

    pending_store: InMemoryPendingStore | None = None
    if req["pending"] is not None:
        pending_store = InMemoryPendingStore(req["pending"])

    result = verify(
        document_bytes,
        expected_kind=req["expected_kind"],
        local_node_id=req["local_node_id"],
        trust_context=trust_context,
        local_policy=policy,
        now=now,
        pending_store=pending_store,
    )

    if result.ok:
        return json.dumps(
            {
                "harness_version": HARNESS_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "request_id": req["request_id"],
                "verdict": "accept",
            }
        )
    code = result.reason_code
    if code not in STABLE_CATEGORIES:
        # Contract mismatch: never silently remap an internal reason code.
        raise HarnessOperationalError(
            f"reference rejection code {code!r} is outside the thirteen stable categories "
            "(contract/profile mismatch)"
        )
    return json.dumps(
        {
            "harness_version": HARNESS_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": req["request_id"],
            "verdict": "reject",
            "category": code,
        }
    )


def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        result_json = _handle_request(raw)
    except HarnessOperationalError as exc:
        print(f"harness: operational failure: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # internal crash — operational, never a protocol verdict
        print(f"harness: internal error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(result_json + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

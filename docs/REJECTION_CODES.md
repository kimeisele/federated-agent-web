# Verification Rejection Codes

Codes are selected directly by authoritative verification branches.
They are never inferred from the human-readable reason text.

Stable, machine-readable reason codes for every semantically distinct
failure returned by `VerificationResult`. These are reference-implementation
API identifiers — not normative wire fields.

## Code inventory

| Code | Kind | Step | Meaning | Retry? | Operator action |
|---|---|---|---|---|---|
| `parse.duplicate_key` | any | 1 | Duplicate object member in JSON input | No | Fix document |
| `parse.invalid` | any | 1 | Malformed JSON, invalid UTF-8, NaN/Infinity, negative zero, out-of-domain integer, lone surrogate | No | Fix document |
| `document.wrong_kind` | any | 2 | Document `kind` differs from expected | No | Use correct document |
| `schema.invalid` | any | 2 | Document fails its normative schema (missing field, wrong type, unknown member, invalid identifier pattern) | No | Fix document |
| `delegation.wrong_audience` | delegation | 3 | Target node differs from local node, or capability-addressed target not matched by policy | No | Address to the correct node |
| `time.ordering` | delegation/receipt | 4 | Temporal invariant violated (issued ≥ expires, expires > deadline, authority.expiry < deadline, started > finished) | No | Fix delegation/receipt |
| `time.expired` | delegation | 4 | Delegation's `expires_at` has passed | No | Issue new delegation |
| `receipt.wrong_issuer` | receipt | 4 | Receipt envelope issuer is not the declared executor | No | Receipt must be signed by executor |
| `trust.chain_invalid` | any | 5 | Pinned manifest chain fails validation (broken digest link, wrong sequence, invalid signature in chain) | No | Fix manifest chain |
| `trust.key_unresolved` | any | 5 | Issuer's `kid` not found active in the pinned chain at document issuance time | No | Check issuer manifest chain |
| `trust.stale` | any | 6 | Pinned manifest head is older than its declared freshness window | Maybe | Update pinned manifest |
| `signature.invalid` | any | 7 | Ed25519 signature over JCS(document minus signature) does not verify | No | Document was tampered or signed with wrong key |
| `receipt.no_pending_store` | receipt | 8 | Verifier has no `PendingDelegationStore` — cannot bind receipt | No | Provide pending store for receipt verification |
| `receipt.no_pending_delegation` | receipt | 8 | Receipt references an unknown or non-outstanding delegation | No | Register delegation before verifying receipt |
| `receipt.already_terminal` | receipt | 8 | The pending delegation is already closed | No | Deduplicate at issuer side |
| `receipt.digest_mismatch` | receipt | 8 | Receipt `delegation_digest` does not match the outstanding delegation | No | Check receipt binding |
| `receipt.wrong_executor` | receipt | 8 | Receipt `executor_node_id` is not the delegation's concrete target | No | Receipt must come from the executor |
| `receipt.binding_mismatch` | receipt | 8 | Atomic pending-store closure failed | No | Check receipt and pending state |
| `replay.digest_conflict` | delegation | 9 | Same attempt ID, different delegation digest — integrity violation | No | Do not reuse attempt IDs with different content |
| `authority.action_denied` | delegation | 10 | Requested capability or action not permitted by authority or local policy | No | Fix authority or policy |
| `authority.external_effect_denied` | delegation | 10 | Declared external effect not permitted by local policy | No | Restrict external effects |
| `budget.unenforceable` | delegation | 10 | Budget ceiling cannot be measured or enforced, or budget is unbounded | No | Remove unenforceable ceilings |

## Usage

Callers must read `VerificationResult.reason_code` — never derive the code
by parsing the human-readable `reason` field. Successful verification
returns `reason_code=None`.

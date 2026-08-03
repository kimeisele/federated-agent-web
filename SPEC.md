# FAW Specification (v0.2)

This file condenses the normative contract implemented in this repository.
The governing build specification is
[`docs/federated-agent-web-build-spec-v0.2.md`](docs/federated-agent-web-build-spec-v0.2.md);
where this summary and the governing spec disagree, the governing spec wins.

## 1. Document model

All normative documents are JSON objects:

```json
{
  "kind": "...",
  "spec_version": "0.2",
  "id": "...",
  "issued_at": "2026-08-03T12:00:00Z",
  "issuer": { "node_id": "urn:faw:...", "kid": "sha256:..." },
  "body": {},
  "signature": { "alg": "Ed25519", "value": "base64url-without-padding" }
}
```

Invariants:

- `node_id` is stable and never derived from the active key.
- `issuer.kid` is the sole key identifier; `sha256:<hex>` over the raw 32-byte
  public key. It must resolve to an active key in the issuer's pinned manifest
  chain at the document's `issued_at`. The `signature` object contains only
  `alg` and `value`.
- Timestamps are UTC RFC 3339 with `Z`. Opaque IDs are UUIDv4 (UUIDv7 where
  available). Monetary ceilings are decimal strings, never binary floats.
- Signatures and key encodings are base64url without padding.

### Identifier grammar (ASCII only, schema-enforced)

| Field | Pattern |
|---|---|
| `node_id` | `^urn:faw:[a-z0-9](?:[a-z0-9._-]{0,62})$` |
| `capability` | `^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$` |
| `kid`, digests | `^sha256:[0-9a-f]{64}$` |
| UUID (`id`, `task_id`, `attempt_id`, `receipt_id`) | `^[0-9a-f]{8}-[0-9a-f]{4}-[47][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` |
| generic token | `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` |

### Closed schemas

Every object in the three normative schemas (`schemas/`) sets
`additionalProperties: false`. Each schema declares its `kind` as a `const`
and `spec_version` as an enum containing only `"0.2"`. Forward extension
requires a `spec_version` bump; a future `extensions` object is explicitly
non-authority-bearing and never admitted by v0.2.

## 2. Canonicalization, digest, and signature

- Canonicalization is RFC 8785 (JCS), via the maintained `rfc8785` package.
- The parser rejects duplicate object members (at parse time), NaN/Infinity,
  negative zero, out-of-domain integers, invalid Unicode, and lone surrogates.
- Signing input: `JCS(document minus top-level signature)`; the signature is
  over those canonical UTF-8 bytes directly.
- Content digest: `sha256:<hex>` of `SHA-256(JCS(document without signature))`.
- Artifact digest: `sha256:<hex>` of `SHA-256(exact raw artifact bytes)`.
- `json.dumps(sort_keys=True)` is not a conforming substitute.

## 3. Verification procedure (authoritative order)

`verify(document_bytes, *, expected_kind, local_node_id, trust_context,
local_policy, now, replay_store=None, pending_store=None)` executes steps 1–11
in order; a later step never runs after an earlier failure, and no handler,
artifact consumer, or external effect occurs before every applicable step
passes:

1. **Strict parse** — JCS parse with duplicate-member and number/Unicode rejection.
2. **Expected-kind schema validation** — schema selected from the trusted
   `expected_kind` parameter; never inferred from input.
3. **Audience binding** (delegations) — `local_node_id` must be the addressed
   `target_node_id`, or the capability-addressed target must match under
   explicit local policy. Fail-closed.
4. **Temporal and structural admission** (delegations) — `body.issued_at ==
   envelope issued_at`; `issued_at < expires_at ≤ deadline`;
   `authority.expiry ≥ deadline`; `now ≤ expires_at + clock skew`.
5. **Pinned manifest-chain validation and key resolution** — monotonic
   sequence, digest links, and signatures, from the locally approved anchor;
   resolve `issuer.kid` to an active key at `issued_at`.
6. **Revocation and trust-context freshness** — the context is classified
   `fresh`/`stale` against `manifest_freshness_window_seconds`; the head
   sequence and digest are exposed; a stale context never silently produces an
   unqualified pass (policy may reject it).
7. **Core signature verification** — Ed25519 over JCS(document minus signature).
8. **Document binding** (receipts, issuer side) — against the
   `PendingDelegationStore`: outstanding, digest match, executor equals
   concrete target; then atomic terminal close.
9. **Replay lookup and integrity comparison** (delegations) — keyed
   `(issuer_node_id, attempt_id)`; matching digest deduplicates (stored
   terminal receipt or current state returned), differing digest is an
   integrity violation.
10. **Local authority and budget evaluation** — capability authorized by
    `authority.actions`, actions/external effects permitted by policy, every
    budget ceiling enforceable and within local caps; an unbounded budget is
    rejected.
11. **Atomic admission** — the replay record `(issuer_node_id, attempt_id,
    delegation_digest, state)` is created atomically (O_EXCL) before any
    handler runs: at-most-once handler admission per attempt within the
    persisted replay window.

A verification result is not authorization: steps 1–9 establish validity and
binding; steps 10–11 establish local admission for a delegation.

## 4. Node manifest

Published at `/.well-known/faw-node.json` (repository copy at
`.well-known/faw-node.json`). `/.well-known/agent-card.json` remains reserved
for a genuine A2A Agent Card; the legacy `agent-federation.json` is not the
FAW manifest and must never be aliased to the FAW path.

Body fields: `node_id`, `display_name`, `manifest_sequence`,
`previous_manifest_digest` (null for genesis), `manifest_freshness_window_seconds`,
`capabilities`, `endpoints`, `keys`, `authorization_policy`, `cost_class`,
`rate_limits`, `status`.

Key entries: `kid`, `alg: "Ed25519"`, `public_key` (base64url), `status`
(`active`/`retired`/`revoked`), `valid_from`, optional `valid_until`,
`revoked_at`, `replaces`.

Rotation: sequence increases; the new manifest links the previous digest; the
previous active key signs the manifest introducing the replacement; the old
key becomes `retired` (or overlaps while active). Revocation is published in a
later manifest signed by a still-valid key. `node_id` never changes.

## 5. Delegation

Body fields: `task_id`, `attempt_id`, `issuer_node_id`, `target_node_id` (or
`capability_target`), `capability`, `input` (`inline` data or immutable
`refs` with digests), `authority`, `budget`, `deadline`, `expected_output`,
`expires_at`. The delegation's `issued_at` is the envelope `issued_at` (§6);
it is not duplicated in the body.

- `task_id` is stable across retries; a retry uses a new `attempt_id`.
- `authority` is structured: `actions`, `filesystem_scope`, `network_scope`,
  `external_effect_scope`, `expiry`. An executor rejects anything it cannot
  prove is inside the declared authority.
- `budget` carries enforceable ceilings: `max_wall_seconds`, `max_tokens`,
  `max_cost_usd` (decimal string), `max_output_bytes`. An unenforceable
  ceiling is rejected before execution; an unbounded budget is rejected.
- `expires_at` governs admission; `deadline` governs execution. Once admitted,
  the executor must emit a `timed_out` receipt no later than `deadline`.

## 6. Receipt

Exactly one accepted terminal receipt per attempt:
`succeeded | failed | rejected | timed_out`.

Body fields: `receipt_id`, `task_id`, `attempt_id`, `delegation_digest`,
`executor_node_id`, `status`, `started_at`, `finished_at`, `artifacts`
(media type, digest over raw bytes, size, location), `usage`, optional
`failure`, `evidence`.

Issuer-side acceptance: the receipt must refer to an outstanding delegation
issued by this node, match `task_id`/`attempt_id`/`delegation_digest`, come
from the concrete `target_node_id`, and atomically close the pending record
(the first valid terminal receipt wins; a second is rejected).

## 7. Transport interface

```python
send(document: bytes, destination: str) -> TransportSendResult
poll() -> list[TransportEnvelope]
ack(transport_message_id: str) -> None
nack(transport_message_id: str, reason: str) -> None
```

- Transports never interpret document content as authority.
- Acknowledgements are per message; partial delivery failure preserves every
  unacknowledged message; adapters expose durable message IDs; duplicate
  transport delivery is expected and safe (core replay deduplicates).
- The shipped adapter is a fully offline loopback/filesystem transport
  (append-only writes, atomic rename, durable UUID message IDs). The
  `nadi_compat` compatibility boundary is specified in
  [`docs/ADAPTER_NADI.md`](docs/ADAPTER_NADI.md) and is not part of the v0.2
  acceptance gate.

## 8. CLI

```text
faw keygen
faw manifest init
faw manifest verify <file>
faw delegation verify <file>
faw receipt verify <file>
faw conformance <node-path-or-url>
faw demo
```

`faw demo` runs fully offline: two ephemeral identities → manifests → one
signed delegation → filesystem delivery → verification → deterministic
execution (`hash_file` capability) → signed receipt → issuer-side acceptance;
prints task ID, attempt ID, delegation digest, receipt digest, and artifact
digest; exits non-zero if any invariant fails.

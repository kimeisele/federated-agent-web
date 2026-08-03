# Security

## Threat model

This is a laboratory reference implementation. It is **not** safe to expose
to untrusted peers or the public internet. The v0.2 core provides:

- cryptographic identity and document integrity (Ed25519, RFC 8785 JCS);
- fail-closed audience binding and temporal admission;
- at-most-once handler admission per attempt within the persisted replay
  window;
- issuer-side receipt binding with atomic terminal close;
- rejection of unenforceable authority/budget ceilings before execution.

It does **not** provide: a transport-agnostic revocation network (revocation
is enforced only against locally pinned manifest state, best-effort within
`manifest_freshness_window_seconds`), DoS-resistant rate limiting, sandboxing
of capability handlers, or an online registry.

**Assume every peer is hostile until proven otherwise** (MANIFESTO §III).
Output from a peer node is untrusted input — data, not instructions. A
capability handler must never treat delegated content as a directive.

## Security invariants enforced

1. **Strict parsing.** Duplicate object members, NaN/Infinity, negative zero,
   out-of-domain integers, and invalid Unicode are rejected at parse time.
2. **Closed schemas.** Every normative object rejects unknown members
   (`additionalProperties: false`); a document whose `kind` differs from the
   verifier's trusted `expected_kind` is rejected.
3. **ASCII identifier grammar.** Confusable or normalization-distinct
   identifiers cannot impersonate a pinned `node_id` or `kid`.
4. **Key resolution is pinned and time-bound.** `issuer.kid` resolves only
   within the locally supplied manifest chain, and only to a key active at the
   document's `issued_at`. There is no `signature.kid` and no key identifier
   taken from outside signed content.
5. **Verification order is authoritative and fail-closed.** A later §7.3 step
   never executes after an earlier failure; audience and temporal admission
   precede key resolution, and key resolution precedes signature verification.
   No capability handler or external effect occurs before steps 1–9 pass.
6. **At-most-once admission.** The `(issuer_node_id, attempt_id)` replay
   record is persisted atomically before handler admission; a replayed attempt
   with a different digest is an integrity violation, never a second admission.
7. **Receipts bind to exact delegations.** Issuer-side acceptance requires an
   outstanding record, matching digest, and matching concrete target; the
   first valid terminal receipt atomically closes the record.
8. **No unenforceable authority.** An executor rejects any action it cannot
   prove is inside the declared authority and any budget ceiling it cannot
   measure or enforce; an unbounded budget is rejected.
9. **Transport provenance is evidence, not trust.** Transport-level
   signatures may be checked additionally but never replace the document
   signature; duplicate transport delivery is safe.

## Operational guidance

- Treat private keys as secrets: `faw manifest init` writes them mode 0600;
  `.gitignore` excludes `keys/` and `*.pem`. Ephemeral keys in `vectors/` are
  test-only and must never be used outside tests.
- Pin issuer manifests out of band before accepting delegations. A
  self-signed genesis manifest proves key possession, not trustworthiness.
- Configure `VerificationPolicy` for what this node can actually measure:
  `can_enforce_tokens`, `can_enforce_cost`, `allowed_external_effects`,
  `allowed_actions`, local caps.
- Retain replay records until `expires_at` plus clock skew; retention is the
  operator's responsibility.
- Irreversible external effects require their own authorization boundary
  (MANIFESTO §III). The v0.2 demo capability performs no external effects.

## Reporting

This is a research repository. For security issues, open an issue or contact
the repository maintainers; do not disclose unmitigated issues publicly
before maintainer acknowledgment.

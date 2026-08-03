# Nadi compatibility adapter — boundary specification

Status: **specified, not implemented.** Implementation is optional after the
offline core acceptance gate passes and only if the task budget permits. The
v0.2 conformance suite must not depend on this adapter.

Purpose: allow a FAW-conforming node to exchange signed documents with the
existing NADI GitHub-backed relay (`kimeisele/steward-federation`) without
weakening any core invariant.

## Boundary contract

A future `nadi_compat` transport adapter (in `transports/nadi.py`) MUST:

1. **Wrap complete signed FAW documents.** The adapter carries opaque FAW
   document bytes as NADI message payloads. It MUST NOT split, reformat, or
   re-canonicalize them; the FAW document is the transport payload, verbatim.
2. **Verify before dispatch.** Inbound payloads are parsed and verified with
   the core `verify()` procedure (expected kind per destination surface)
   BEFORE any handler is invoked. The adapter must not offer a
   "dispatch without verification" path.
3. **Never derive stable identity from the active key.** The FAW `node_id`
   is carried in the message metadata; the adapter must not synthesize a
   node identity from the signing key (nadi-kit's `_derive_node_id` behavior
   is explicitly rejected).
4. **Preserve failed/unacknowledged messages individually.** Mirror the
   filesystem adapter's per-message `ack`/`nack` semantics. A failed
   multi-target push must not clear the outbox (nadi-kit's `clear_outbox`
   behavior is explicitly rejected).
5. **Make hub location configurable.** `HUB_REPO` must be a constructor
   parameter, never a module constant pointing at `kimeisele/steward-federation`.
6. **Remain marked experimental.** The adapter sets an explicit
   `experimental: true` capability flag and logs a warning on construction.
7. **Use mocked/local transport in tests.** No test may contact the real
   GitHub relay; tests inject a stub `NadiHubRelay` (an in-memory or local
   file mailbox).
8. **Not claim the GitHub mailbox relay is a production bus.** Adapter
   documentation must state that the relay is a lab-scale compatibility
   surface that can accumulate backlog.

## Explicit non-goals

- No migration of existing NADI nodes.
- No production hardening of the relay.
- No use of NADI message semantics as authority: capability, authority,
  budget, deadline, and external-effect evaluation come exclusively from the
  verified FAW delegation.

## Test plan (when implemented)

- round-trip: signed FAW delegation through a stub relay, verified at the
  receiver, receipt returned and accepted;
- duplicate delivery through the relay is deduplicated by the core replay
  store (at-most-once);
- a malformed or unverified payload never reaches a handler;
- outbox retention on partial push failure;
- experimental flag present; tests run offline.

Do not copy `nadi_kit.py` wholesale: reuse only the small mechanics named in
`docs/REUSE_REPORT.md` (atomic writes, Ed25519 via `cryptography`), and keep
the adapter thin.

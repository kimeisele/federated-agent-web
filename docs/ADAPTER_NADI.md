# Nadi compatibility adapter — boundary specification

Status: **experimental, non-normative, transport-only.** Specified, not
implemented. Implementation is a later v0.4 issue and PR. The v0.4 shared
transport conformance suite runs against the filesystem adapter; the Nadi
adapter is added by registering its harness.

Purpose: allow a FAW-conforming node to exchange signed documents through the
existing NADI GitHub-backed relay (`kimeisele/steward-federation`) without
weakening any core invariant.

## Relay wrapper

The adapter carries one wrapper as the NADI operation:

```text
faw.document
```

The NADI payload is:

```json
{
  "message_id": "UUID",
  "source_node_id": "urn:faw:...",
  "destination_node_id": "urn:faw:...",
  "media_type": "application/faw+json",
  "encoding": "base64url",
  "document": "<base64url without padding>",
  "document_sha256": "sha256:<hex>",
  "created_at": "<UTC RFC3339>",
  "experimental": true
}
```

Clarifications:

- this wrapper is **not** a normative FAW signed document;
- wrapper fields are **untrusted transport metadata**;
- `document_sha256` detects transport corruption only; it is not a substitute
  for the FAW content digest or the Ed25519 signature;
- decoding `document` must reproduce the exact original bytes;
- no parsing, formatting, canonicalization, or mutation may occur in transit;
- `source_node_id` and `destination_node_id` are routing hints; issuer,
  target, authority, and validity are determined from the verified embedded
  document;
- unknown wrapper members fail closed in the adapter;
- malformed base64, wrong media type, or digest mismatch produces `nack`,
  never handler dispatch.

The wrapper is not added to any normative schema. `spec_version` is not
bumped.

## Boundary contract

A future `nadi_compat` transport adapter (in `transports/nadi.py`) MUST:

1. **Wrap complete signed FAW documents.** The adapter carries opaque FAW
   document bytes as the `document` field. It MUST NOT split, reformat, or
   re-canonicalize them; the FAW document is the transport payload, verbatim.
2. **Verify before dispatch.** Inbound payloads are parsed and verified with
   the core `verify()` procedure (expected kind per destination surface)
   BEFORE any handler is invoked. The adapter must not offer a
   "dispatch without verification" path.
3. **Never derive stable identity from the active key.** The FAW `node_id`
   is carried as `source_node_id` routing metadata; the adapter must not
   synthesize a node identity from the signing key (nadi-kit's
   `_derive_node_id` behavior is explicitly rejected).
4. **Preserve failed/unacknowledged messages individually.** Mirror the
   filesystem adapter's per-message `ack`/`nack` semantics. A failed
   multi-target push must not clear the outbox (nadi-kit's `clear_outbox`
   behavior is explicitly rejected).
5. **Make hub location configurable.** The GitHub owner/repo must be a
   constructor parameter, never a module constant pointing at
   `kimeisele/steward-federation`.
6. **Remain marked experimental.** The adapter sets an explicit
   `experimental: true` capability flag and logs a warning on construction.
7. **Use mocked/local transport in tests.** No test may contact the real
   GitHub relay; tests inject a stub relay (an in-memory or local file
   mailbox).
8. **Not claim the GitHub mailbox relay is a production bus.** Adapter
   documentation must state that the relay is a lab-scale compatibility
   surface that can accumulate backlog.
9. **Suppress already-acknowledged remote messages** when the GitHub mailbox
   is read again; repeated remote mailbox contents are expected and safe.
10. **Own local durable state** (`outbox/`, `inbox/`, `failed/`,
    `acknowledged/`): stage one exact document before remote publication;
    remove only the specifically confirmed published message; retain every
    failed or unconfirmed message.

## Thin backend boundary

The future implementation may use a small injected backend concept:

```python
class NadiRelayBackend(Protocol):
    def publish(self, envelopes: list[RelayEnvelope]) -> list[RelayPublishResult]: ...
    def fetch(self, destination: str) -> list[RelayEnvelope]: ...
```

Every `RelayPublishResult` must be tied to one exact `message_id`. An
aggregate integer such as `3 messages pushed` is insufficient because it
cannot identify which messages succeeded. This protocol is documented here
only; it is not added to application source in this slice.

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
- acknowledged messages suppressed on mailbox re-read;
- experimental flag present; tests run offline.

Do not copy `nadi_kit.py` wholesale: reuse only the small mechanics named in
`docs/REUSE_REPORT.md` (atomic writes, Ed25519 via `cryptography`), and keep
the adapter thin.

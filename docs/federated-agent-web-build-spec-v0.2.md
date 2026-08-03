# Federated Agent Web — Reference Implementation Build Specification v0.2

**Status:** Draft for final sign-off  
**Target repository:** `kimeisele/federated-agent-web`  
**Implementation language:** Python 3.11+  
**Primary input:** `MANIFESTO.md` — *The Federated Agentic Web Begins Here*, Draft 3  
**Purpose:** Build the smallest credible, transport-agnostic reference implementation of the manifesto's delegation contract.

---

## 1. Decision

Create one new, neutral front-door repository named `federated-agent-web`.

This is **not** a restart from zero and **not** another world/runtime/control-plane repository. It extracts a small interoperable core from the existing federation work while keeping the current repositories in their proper roles:

- `steward-protocol` remains a source of identity, capability, and communication semantics.
- `steward-federation` / `nadi-kit` is an experimental transport implementation and compatibility source.
- `agent-template` is the source for onboarding, descriptors, validation, and acceptance-node patterns.
- `agent-internet` remains discovery, commons, routing/trust projection, and public/operator surface — not the canonical message bus.
- `federation-recon` and `federation-map` remain evidence/observability consumers, not protocol dependencies.

The new repository defines the stable contract above all transports:

1. node discovery;
2. signed delegation;
3. bounded authority and budget;
4. independently verifiable terminal receipt;
5. transport adapters beneath that contract.

**Delegation is the protocol. Nadi, GitHub, filesystem, HTTP, A2A, queues, and future mechanisms are adapters.**

---

## 2. Why a new repository is justified

The current system already proves that repository-backed nodes, descriptors, signed messages, setup workflows, acceptance nodes, and federation observations can be built. It does not yet provide a neutral, independently implementable contract that an external node can adopt without joining infrastructure owned by `kimeisele`.

The new repository exists to remove that coupling. It must not require:

- access to `kimeisele/steward-federation`;
- a `FEDERATION_PAT` scoped to a central hub;
- Agent City membership;
- Mahamantra/Nadi terminology;
- any particular forge;
- any particular model, agent runtime, or orchestration framework.

Existing internal terminology may appear in adapter documentation, but the normative core uses neutral names.

---

## 3. Verified repository basis

The implementation agent must inspect the current default branches before copying code. The following are known starting points, not blind copy targets:

### `kimeisele/agent-internet`

- `docs/adrs/0002-commons-shell-not-second-substrate.md`
- Decision already recorded: `agent-internet` owns onboarding, discovery, routing/trust surfaces, adapters, and projections; it does not replace Nadi or become a second universal message bus.

### `kimeisele/steward-federation`

- `nadi_kit.py`
- `pyproject.toml`
- Useful existing mechanics: Ed25519 key generation, signed outbound messages, payload hashes, TTL, correlation IDs, atomic file inbox/outbox, and a small CLI.
- Known limitations that must not enter the core unchanged:
  - transport is coupled to `kimeisele/steward-federation` through `HUB_REPO`;
  - GitHub API mailbox updates generate transport commits;
  - inbound `NadiNode.receive()` / `process_inbox()` dispatches messages without a core-level signature verification gate;
  - partial multi-target push can clear the whole local outbox after only some targets succeeded;
  - node identity is derived from the current public key, which complicates stable identity across key rotation;
  - canonicalization is `json.dumps(..., sort_keys=True)`, not RFC 8785 JCS.

### `kimeisele/agent-template`

- `.well-known/agent-federation.json`
- `.well-known/agent.json`
- `scripts/setup_node.py`
- `scripts/quickstart.py`
- `scripts/nadi_send.py`
- `.github/workflows/heartbeat.yml`
- Reusable patterns: setup flow, descriptor generation, status checks, optional federation integration, governance checks, and acceptance-node testing.
- Live default-branch check completed: the template currently publishes the legacy files `.well-known/agent-federation.json` and `.well-known/agent.json`. Neither is the FAW node manifest.
- Required correction: do not rename or alias the legacy descriptor to the A2A path or to the FAW path. `/.well-known/agent-card.json` is reserved for an actual A2A Agent Card projection. The neutral FAW manifest uses `/.well-known/faw-node.json`.

### `kimeisele/federation-map`

- Current live map is useful evidence that the lab communicates.
- It also demonstrates that the existing GitHub-backed relay can accumulate a backlog and must not be presented as a production-scale bus.

The implementation report must name every copied or adapted source file and explain what was retained, changed, or rejected.

---

## 4. Scope of v0.2

### Required

1. Import the final manifesto as `MANIFESTO.md` without silently rewriting it.
2. Publish three normative JSON Schemas:
   - `node-manifest.schema.json`
   - `delegation.schema.json`
   - `receipt.schema.json`
3. Implement canonicalization, digesting, signing, and verification.
4. Implement stable node identity with rotatable keys.
5. Implement delegation replay protection and idempotent attempt handling.
6. Implement receipt binding to the exact delegation.
7. Implement a transport interface.
8. Implement a fully offline loopback/filesystem adapter.
9. Specify the compatibility boundary for a later `nadi_compat` adapter; implementation is optional after the core acceptance tests pass.
10. Implement a deterministic two-node end-to-end demo.
11. Implement conformance and verification CLI commands.
12. Provide golden test vectors suitable for a second independent implementation.
13. Provide a read-only, permission-bounded `skill.md` for agent onboarding.
14. Produce an implementation report with test results and remaining limitations.

### Explicitly not required

- production HTTP service;
- central registry;
- reputation scoring;
- Agent City or Agent World integration;
- economic/token system;
- governance system;
- autonomous PR generation against unrelated repositories;
- Moltbook posting;
- production hardening of the GitHub relay;
- A2A server implementation;
- migration of existing nodes.

---

## 5. Repository layout

```text
federated-agent-web/
├── MANIFESTO.md
├── README.md
├── SPEC.md
├── SECURITY.md
├── CONFORMANCE.md
├── skill.md
├── pyproject.toml
├── schemas/
│   ├── node-manifest.schema.json
│   ├── delegation.schema.json
│   └── receipt.schema.json
├── src/federated_agent_web/
│   ├── __init__.py
│   ├── canonical.py
│   ├── crypto.py
│   ├── identity.py
│   ├── documents.py
│   ├── verify.py
│   ├── replay.py
│   ├── pending.py
│   ├── cli.py
│   └── transports/
│       ├── base.py
│       └── filesystem.py
├── examples/
│   ├── node_a/
│   ├── node_b/
│   └── run_demo.py
├── vectors/
│   ├── canonicalization/
│   ├── signatures/
│   ├── delegations/
│   └── receipts/
├── tests/
└── docs/
    ├── REUSE_REPORT.md
    ├── IMPLEMENTATION_REPORT.md
    └── ADAPTER_NADI.md
```

Do not add services, databases, containers, web frameworks, or plugin systems unless a required acceptance test cannot be met without them.

---

## 6. Normative document model

All normative documents use JSON objects and contain:

```json
{
  "kind": "...",
  "spec_version": "0.2",
  "id": "...",
  "issued_at": "...",
  "issuer": {
    "node_id": "urn:faw:...",
    "kid": "sha256:..."
  },
  "body": {},
  "signature": {
    "alg": "Ed25519",
    "value": "base64url-without-padding"
  }
}
```

The exact schema may specialize fields, but these invariants apply:

- `node_id` is stable and **must not** be derived from the currently active key.
- `issuer.kid` is the sole key identifier carried by a signed document. It identifies a specific public key and is derived as `sha256:<hex>` over the raw 32-byte Ed25519 public key. The resolved key MUST be an active key in the pinned manifest chain for `issuer.node_id` at the relevant document time.
- the top-level `signature` object contains only `alg` and `value`; it does not repeat `kid`.
- timestamps are UTC RFC 3339 strings with `Z`.
- opaque document identifiers use UUIDv7 when available; UUIDv4 is acceptable when UUIDv7 is unavailable.
- monetary ceilings are decimal strings, never binary floating-point JSON numbers.
- signatures use base64url without padding.

### Identifier grammar

Normative identifiers are ASCII-only and schemas MUST enforce these patterns:

- `node_id`: `^urn:faw:[a-z0-9](?:[a-z0-9._-]{0,62})$`
- `capability`: `^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$`
- `kid` and SHA-256 digest strings: `^sha256:[0-9a-f]{64}$`
- top-level `id`, `task_id`, `attempt_id`, and `receipt_id`: `^[0-9a-f]{8}-[0-9a-f]{4}-[47][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`
- fields such as `issuer_node_id`, `target_node_id`, and `executor_node_id` use the `node_id` grammar.
- every other schema property named `id` or ending in `_id` MUST declare an explicit ASCII `pattern` appropriate to its semantic type; no identifier field may remain an unconstrained JSON string. Where no stronger semantic form exists, use the generic token grammar `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`.

JCS preserves Unicode exactly as supplied. Without an ASCII identifier grammar, confusable or normalization-distinct strings would remain distinct signed identifiers while appearing equivalent to an operator, enabling homoglyph impersonation of a pinned `node_id`.

### Closed schemas and versioned extension

Every object in the three normative schemas MUST set `additionalProperties: false`, including at minimum the document envelope, `issuer`, `body`, `authority`, `budget`, `signature`, artifact entries, and node-manifest key entries. An ignored unknown member that narrows or extends authority semantics would be a silent semantic downgrade under a valid signature.

Each schema MUST declare its document `kind` using JSON Schema `const` and `spec_version` using an `enum` containing only `"0.2"`.

Forward extension occurs through a `spec_version` bump. A future version may define a namespaced `extensions` object, but that object is explicitly non-authority-bearing and MUST NOT affect admission, target selection, authority, budget, deadline, or external-effect evaluation. No `extensions` member is accepted by the v0.2 schemas.

## 7. Canonicalization, digest, and signature

### 7.1 Canonicalization

Use RFC 8785 JSON Canonicalization Scheme (JCS), including its verified errata.

Use a maintained implementation rather than inventing a serializer. For Python, use `rfc8785` and lock the resolved version in the project lockfile.

The implementation must:

- reject duplicate object member names at parse time;
- reject NaN and Infinity;
- reject negative zero input;
- reject invalid Unicode/lone surrogates;
- preserve Unicode string data as supplied — JCS does not perform Unicode normalization;
- sort properties and serialize numbers exactly as JCS requires.

`json.dumps(sort_keys=True)` is not a conforming substitute.

> **Non-normative implementer note:** duplicate detection must occur in the strict JSON parser through an object-pairs hook or equivalent mechanism. A JCS implementation operating on an already-parsed mapping cannot detect duplicate names that the parser silently collapsed.

### 7.2 Signing input

To sign a document:

1. remove the top-level `signature` member;
2. canonicalize the remaining object with JCS;
3. sign the resulting canonical UTF-8 bytes directly with Ed25519;
4. place an `alg: "Ed25519"` and the base64url-without-padding signature value in the top-level `signature` member.

The document content digest is:

```text
sha256:<lowercase-hex-of-SHA-256(JCS(document_without_signature))>
```

The signature is over the canonical bytes, not over a pretty-printed file, the hexadecimal digest string, or an implementation-specific dictionary representation.

Each schema includes a `kind` declared as `const` and a `spec_version` declared as the supported-version `enum` in the signed content. The verification API takes the expected document kind as an explicit parameter and MUST NOT infer it from untrusted input.

### 7.3 Normative verification procedure

The verification API has the conceptual form:

```python
verify(
    document_bytes: bytes,
    *,
    expected_kind: str,
    local_node_id: str | None,
    trust_context: PinnedManifestTrustContext,
    local_policy: VerificationPolicy,
    now: datetime,
    replay_store: ReplayStore | None = None,
    pending_store: PendingDelegationStore | None = None,
) -> VerificationResult
```

The `PinnedManifestTrustContext` is supplied by local policy and contains, for every trusted issuer needed by the verification, the ordered pinned manifest chain, its head sequence and digest, and the time at which that head was pinned or observed. No online manifest lookup is performed by the v0.2 core.

The following order is authoritative. A later step MUST NOT execute, and no capability handler, artifact consumer, repository mutation, or other external effect may occur, until every applicable earlier step has passed:

1. **Strict parse.** Parse the raw bytes with duplicate-member detection and the rejection rules in §7.1. Preserve the parsed structure used for schema validation and JCS; do not parse through a permissive intermediate representation.
2. **Expected-kind schema validation.** Select the schema from the trusted `expected_kind` parameter. Validate the complete document with that schema, including `kind` `const`, `spec_version` `enum`, ASCII identifier patterns, and `additionalProperties: false`. Reject if the document's `kind` differs from `expected_kind` or if the version is unsupported.
3. **Audience binding.** For a delegation, assert that `local_node_id` is the addressed `target_node_id`, or that the capability-addressed target matches this node under explicit local policy. A document failing this step MUST never reach later steps. This fail-closed check may inspect unverified content only to reject; it can never cause acceptance or dispatch.
4. **Temporal and structural admission.** For a delegation, enforce `issued_at < expires_at ≤ deadline`, `authority.expiry ≥ deadline`, and reject when `now > expires_at` after the configured clock-skew policy. Validate all other document-specific timestamp and cross-field invariants before cryptographic acceptance.
5. **Pinned manifest-chain validation and key resolution.** Validate the locally supplied chain's monotonic sequence, digest links, and every manifest signature according to §8, starting from the locally approved trust anchor. Resolve `issuer.kid` only within the chain for `issuer.node_id`. The resolved Ed25519 key MUST be active and valid for the document's issuance time. No `signature.kid` exists and no key identifier may be taken from outside the signed content and supplied trust context.
6. **Revocation and trust-context freshness.** Enforce retired/revoked status against the supplied pinned manifest state. Revocation checking is therefore best-effort within the latest manifest's declared `manifest_freshness_window_seconds`; it is not an online freshness guarantee. The verifier MUST classify the trust context as `fresh` or `stale`, MUST expose the pinned head sequence and digest in `VerificationResult`, and SHOULD record the manifest sequence and digest in downstream evidence. A stale context may be rejected by local policy or returned as a qualified verification result, but it MUST NOT silently produce an unqualified pass.
7. **Core signature verification.** Remove `signature`, JCS-canonicalize the remaining object, and verify the Ed25519 signature with the key resolved in step 5. A transport-level signature may be checked additionally but never replaces this document signature.
8. **Document binding.** For a receipt, use the issuer-side `PendingDelegationStore` from §11. The issuer MUST match `delegation_digest` against a delegation it issued and still holds outstanding, MUST verify `task_id` and `attempt_id` against that record, and MUST verify `executor_node_id` equals that delegation's `target_node_id`. Unknown, non-outstanding, mismatched, or already-terminal delegations are rejected.
9. **Replay lookup and integrity comparison.** For a delegation, query the receiver replay store by `(issuer_node_id, attempt_id)`. If no record exists, continue. If one exists and its stored `delegation_digest` matches, deduplicate and return the stored terminal receipt or current non-terminal state. If the digest differs, reject as an integrity violation.
10. **Local authority and budget evaluation.** Evaluate capability authorization, resource scope, external-effect scope, enforceable budget, and local policy. Reject before execution if any requested action or ceiling cannot be proven enforceable.
11. **Atomic admission.** For a newly accepted delegation, atomically create the replay record containing `(issuer_node_id, attempt_id)`, `delegation_digest`, and the initial attempt state before admitting the document to a capability handler. Only after this persistence succeeds may execution begin.

A verification result is not equivalent to authorization: steps 1–9 establish document validity and binding; steps 10–11 establish local admission for a delegation.

## 8. Node manifest

The normative FAW node manifest is published at:

```text
/.well-known/faw-node.json
```

A repository copy may also exist at:

```text
.well-known/faw-node.json
```

Do not place the custom FAW manifest at `/.well-known/agent-card.json`. That path remains reserved for a document conforming to the A2A Agent Card schema. A node that later exposes A2A may publish a separate genuine A2A projection there, referencing the FAW manifest as related metadata under the rules of that A2A version.

The template's legacy `.well-known/agent-federation.json` is not the FAW manifest and MUST NOT be renamed, copied, redirected, or aliased to the reserved FAW path.

Required body fields:

- `node_id`
- `display_name`
- `manifest_sequence`
- `previous_manifest_digest` (`null` for genesis)
- `manifest_freshness_window_seconds`
- `capabilities`
- `endpoints`
- `keys`
- `authorization_policy`
- `cost_class`
- `rate_limits`
- `status`

`manifest_freshness_window_seconds` is a positive integer declaring how long a verifier may treat a locally pinned observation of this manifest head as fresh for best-effort revocation checking. It is not a promise that no revocation occurred during that window.

Each key entry includes:

- `kid`
- `alg: Ed25519`
- raw public key encoded as base64url without padding
- `status`: `active`, `retired`, or `revoked`
- `valid_from`
- optional `valid_until`
- optional `revoked_at`
- optional `replaces`

A self-signed genesis manifest proves possession of the key, not trustworthiness. Trust is established by local policy, pinning, signed history, or other out-of-band evidence.

Verification of an issuer uses a pinned manifest chain supplied through the trust context in §7.3. Revocation is enforced only against that supplied state, with freshness classified against the declared window. The core performs no online lookup in v0.2. Verifiers SHOULD include the manifest head sequence and digest used when recording evidence.

### Key rotation

A normal rotation is valid when:

1. manifest sequence increases monotonically;
2. the new manifest references the previous manifest digest;
3. the previous active key signs the manifest that introduces the replacement key;
4. the old key becomes `retired` or remains active during an overlap window.

A revocation is published in a later manifest and signed by a still-valid key. If no valid key remains, recovery is out of scope for automatic trust and requires a new locally approved trust anchor.

## 9. Delegation contract

Required fields:

- `task_id`: stable across retries and attempts;
- `attempt_id`: unique per execution attempt;
- `issuer_node_id`;
- `target_node_id` or an explicit capability-addressed target;
- `capability`;
- `input` or immutable input references with digests;
- `authority`;
- `budget`;
- `deadline`;
- `expected_output`;
- `issued_at`;
- `expires_at`;
- signature.

A capability-addressed discovery or selection step may precede issuance. For any delegation expected to produce an issuer-verifiable receipt, the issuer MUST resolve and record a concrete `target_node_id` before registering the delegation as outstanding.

### Attempt identity and replay protection

Do not add a second random nonce merely because the word “nonce” sounds safer. A globally unique `attempt_id` already serves as the replay token when used correctly.

The receiver must persist a replay record keyed by:

```text
(issuer_node_id, attempt_id)
```

The record stores `delegation_digest` as a compared value and is retained until at least `expires_at` plus a configurable clock-skew window.

Rules:

- atomically persist the attempt state before admitting it to a handler;
- when the key exists and the stored digest matches, deduplicate and return the previously stored terminal receipt when available, or the existing non-terminal attempt state while execution is still in progress;
- when the key exists and the digest differs, reject as an integrity violation;
- the same attempt must never be admitted to a handler twice within the replay window;
- a retry after a failed or timed-out attempt uses the same `task_id` and a new `attempt_id`;
- after a crash leaves an attempt in `executing`, do not automatically repeat an irreversible effect. Recovery requires capability-specific idempotency/reconciliation or a new attempt.

The protocol therefore claims at-most-once handler admission per attempt within the persisted replay window, not magical exactly-once delivery or exactly-once external effects.

### Admission expiry and execution deadline

`expires_at` governs admission. The verifier rejects a delegation when `now > expires_at`, subject only to the configured clock-skew policy.

`deadline` governs execution. Once admitted, the executor MUST terminate execution and emit a `timed_out` terminal receipt no later than `deadline`.

The following ordering is mandatory and is enforced semantically in addition to schema shape validation:

```text
issued_at < expires_at ≤ deadline
```

The delegation's `authority.expiry` MUST be greater than or equal to `deadline`. A delegation violating either ordering is rejected before handler admission.

### Authority

Authority must be structured, not implied by natural-language prompt content. It minimally declares:

- allowed actions;
- resource scope;
- filesystem scope;
- network scope;
- external-effect scope;
- expiry.

An executor must reject an action it cannot prove is inside the declared authority.

### Budget

The budget object carries enforceable ceilings such as:

- `max_wall_seconds`;
- `max_tokens`;
- `max_cost_usd` as a decimal string;
- optional `max_output_bytes`.

If a caller provides a ceiling that the executor cannot measure or enforce, the executor rejects the task before execution rather than silently ignoring it.

### Expected output

Expected output declares the output kind, media type, optional JSON Schema, required artifact names, and whether a repository mutation is expected. A free-form prompt alone is not an output contract.

## 10. Receipt contract

Every delegation attempt ends in exactly one accepted terminal receipt:

- `succeeded`
- `failed`
- `rejected`
- `timed_out`

Required fields:

- `receipt_id`;
- `task_id`;
- `attempt_id`;
- `delegation_digest`;
- `executor_node_id`;
- `status`;
- `started_at` when execution began;
- `finished_at`;
- `artifacts` with media type, digest, size, and location/reference;
- measured `usage`;
- optional structured `failure` object;
- `evidence` references;
- executor signature.

The receipt's `delegation_digest` is `sha256:<hex>` over the JCS-canonicalized delegation without its top-level `signature` member. The delegation's issuer signature must also be verified independently.

Every artifact digest is:

```text
sha256:<lowercase-hex-of-SHA-256(raw artifact bytes)>
```

Artifact digests are over the exact raw bytes represented by the artifact entry, not over JCS, decoded text, normalized line endings, archive members, or a location string.

Receipt acceptance is issuer-side and stateful. Using the persistent pending-delegation store defined in §11, the issuer:

- MUST match `delegation_digest` against a delegation it issued and still holds outstanding;
- MUST match `task_id` and `attempt_id` against that delegation;
- MUST verify `executor_node_id` equals that delegation's concrete `target_node_id`;
- MUST reject a receipt for an unknown, non-outstanding, already-terminal, or mismatched delegation;
- MUST mark the pending delegation terminal atomically when accepting its first valid terminal receipt.

A successful repository mutation must include evidence binding the resulting commit or pull request to `task_id`, `attempt_id`, and `delegation_digest`.

## 11. Transport interface

The core transport API must operate on complete signed documents and preserve delivery state per document.

Minimum abstract operations:

```python
send(document: bytes, destination: str) -> TransportSendResult
poll() -> list[TransportEnvelope]
ack(transport_message_id: str) -> None
nack(transport_message_id: str, reason: str) -> None
```

Requirements:

- transport never interprets prompt content as authority;
- core verification happens before dispatch;
- acknowledgements are per message, not “clear the whole outbox”;
- partial delivery failure preserves every unacknowledged message;
- adapters expose durable message IDs;
- duplicate transport delivery is expected and safe;
- transport provenance is evidence, not automatic trust.

### Issuer-side pending-delegation store

Receipt verification uses a persistent issuer-side store implemented in `pending.py`; persistence is not left implicitly to the caller.

Minimum conceptual operations:

```python
register_outstanding(delegation: dict, delegation_digest: str) -> None
get_outstanding(task_id: str, attempt_id: str) -> PendingDelegation | None
accept_terminal(receipt: dict) -> PendingDelegation
```

Requirements:

- registration occurs before the delegation is handed to a transport;
- the store retains the exact signed delegation or an immutable canonical representation sufficient to reproduce and compare `delegation_digest`;
- the record includes issuer node, concrete target node, task ID, attempt ID, issued time, expiry, deadline, and terminal state;
- `accept_terminal` atomically verifies that the record is still outstanding and marks it terminal;
- an unknown, non-outstanding, already-terminal, digest-mismatched, or non-target-executor receipt is rejected;
- caller applications may choose the storage backend, but the reference implementation provides a durable filesystem implementation used by the demo and tests.

### Filesystem adapter

The required reference adapter uses append-only directories and atomic rename. It must support the complete demo without network access.

### Nadi compatibility adapter

A later `nadi_compat` adapter may reuse small mechanics from `nadi-kit`, but it is not required for the core v0.2 acceptance gate. Before implementation, `docs/ADAPTER_NADI.md` must define how the adapter will:

- wrap complete signed FAW documents;
- verify the FAW document before handler dispatch;
- avoid deriving stable node identity from the active key;
- preserve failed/unacknowledged messages individually;
- make hub location configurable;
- remain marked experimental;
- use mocked/local transport in tests;
- not claim the GitHub mailbox relay is a production bus.

Implement this adapter only after the offline core is green and only if the task budget still permits it. Do not copy `nadi_kit.py` wholesale.

## 12. CLI

Provide one CLI executable, for example `faw`:

```text
faw keygen
faw manifest init
faw manifest verify <file>
faw delegation verify <file>
faw receipt verify <file>
faw conformance <node-path-or-url>
faw demo
```

`faw demo` must run offline and:

1. create two ephemeral node identities;
2. generate and verify both manifests;
3. create one signed delegation from node A to node B;
4. deliver it through the filesystem adapter;
5. verify it before execution;
6. execute a deterministic, harmless capability;
7. emit a signed terminal receipt;
8. deliver and verify the receipt at node A;
9. print task ID, attempt ID, delegation digest, receipt digest, and artifact digest;
10. exit non-zero if any invariant fails.

The printed artifact digest and every artifact digest in the receipt MUST use `sha256:<hex>` over the artifact's exact raw bytes as defined in §10.

The demo capability should be deterministic, such as hashing an input file or transforming a small JSON document. Do not use an LLM in the conformance path.

---

## 13. Conformance tests

The build is not complete unless all tests pass offline. Every normative MUST introduced by this specification requires an executable test or a documented static schema assertion.

Required tests:

### Schema, kind, identifiers, and paths

- every normative object rejects unknown members under `additionalProperties: false`;
- a document whose `kind` differs from the verifier's trusted `expected_kind` is rejected;
- an unsupported `spec_version` is rejected;
- `node_id`, capability, key ID, digest, and UUID fields violating their ASCII grammar are rejected;
- the FAW manifest is discovered at `/.well-known/faw-node.json` or its repository-copy equivalent;
- the legacy `agent-federation.json` is not accepted as a substitute or alias for the FAW manifest.

### Canonicalization

- nested object key order;
- Unicode preservation;
- invalid/lone surrogate rejection;
- number vectors including exponent forms;
- NaN/Infinity rejection;
- negative-zero rejection;
- duplicate-member rejection at parse time.

### Signatures and manifest trust

- valid signature passes;
- one-byte content mutation fails;
- changed `kind`, authority, budget, deadline, or expected output fails;
- unknown key fails;
- an `issuer.kid` not active in the issuer's pinned chain fails;
- revoked key fails against the supplied pinned manifest state;
- key rotation continuity passes;
- broken manifest chain fails;
- verification against a stale pinned manifest context reports `stale`, including the head sequence and digest, rather than silently returning an unqualified pass.

### Delegation and receipt

- receipt binds to exact delegation digest;
- receipt with wrong task or attempt fails;
- a relayed delegation addressed to another node is rejected before key resolution or handler admission;
- capability-addressed admission succeeds only under an explicit matching local policy;
- receipt from a non-target executor is rejected;
- receipt referencing an unknown or non-outstanding delegation is rejected;
- a second terminal receipt for an already-terminal pending record is rejected;
- expired delegation is rejected before execution;
- `issued_at >= expires_at` is rejected;
- `expires_at > deadline` is rejected;
- delegation whose `authority.expiry` precedes `deadline` is rejected at admission;
- an admitted execution that reaches `deadline` terminates and emits `timed_out` no later than the deadline;
- insufficient authority is rejected;
- unenforceable budget is rejected;
- duplicate delivery causes at-most-one handler admission and returns the stored receipt when terminal;
- reused `(issuer_node_id, attempt_id)` with the same digest deduplicates;
- reused `(issuer_node_id, attempt_id)` with a different digest is rejected as an integrity violation;
- retry with same task ID and new attempt ID is allowed.

### Transport and state

- pending delegation is durably registered before transport send;
- duplicate delivery is safe;
- partial multi-target failure retains failed messages;
- ack removes only the acknowledged message;
- malformed or unverified input never reaches a handler;
- receipt acceptance atomically closes only the matching outstanding record;
- offline demo completes end to end.

### Golden vectors

Write static JSON and expected-byte fixtures so that a non-Python implementation can reproduce:

- canonical bytes;
- content digests;
- public keys and key IDs;
- signatures;
- delegation digest;
- receipt digest;
- artifact digest over raw fixture bytes.

Ephemeral test keys must be clearly marked and never used outside tests.

## 14. Agent-readable onboarding file

The repository includes `skill.md` for discovery on agent platforms, but it is not an instruction override and not an installer.

It must explicitly declare:

- purpose;
- version and repository commit/tag expected;
- files it may read;
- files it may propose changing;
- network access required: none for local evaluation;
- external writes allowed: none;
- secrets required: none;
- output: a local patch/diff plus conformance report;
- refusal conditions.

The default join workflow is:

1. inspect the repository read-only;
2. verify the referenced release/commit and hashes;
3. generate a proposed node manifest locally;
4. run conformance locally;
5. present a diff and report;
6. perform no push, registration, message send, or post without separate bounded authorization.

Signing or hashing `skill.md` improves provenance but does not make its instructions safe. Permission scope and semantic review remain required.

`skill.md` ships with the repository and is written last, after the normative core and conformance report are stable. It is intentionally not a blocking §17 acceptance criterion. An agent-facing entry point that explicitly refuses fetch-and-execute behavior is nevertheless the project's clearest publication differentiator; omitting it from a published repository would invite the onboarding pattern the manifesto rejects.

---

## 15. Build and publication authority

The implementation has two distinct phases.

### Phase A — mandatory local build

The coding agent may:

- create the complete repository working tree locally;
- generate test-only keys;
- run tests and the offline demo;
- produce reports and exact publish commands.

It must not:

- create a remote repository;
- push commits;
- publish keys or manifests;
- post to Moltbook;
- contact external nodes;
- modify existing repositories.

### Phase B — optional authorized publication

Publication is permitted when the invoking task explicitly authorizes the irreversible effects and names their scope, including:

- exact GitHub owner/repository;
- repository visibility;
- allowed branches/tags;
- whether repository creation is allowed;
- whether push is allowed;
- whether a draft PR or direct default-branch initialization is allowed.

The actor granting or enforcing this authorization may be a human, an agent, or repository policy. A human click is not architecturally required. The requirement is an explicit, bounded authorization boundary.

Moltbook outreach is **not** part of the initial build or repository publication. After independent review, a separate bounded outreach task may recruit one pilot node. Broad promotion that describes the system as a working federation must wait for a successful external-node handshake.

---

## 16. Required implementation report

`docs/IMPLEMENTATION_REPORT.md` must include:

1. exact files created;
2. dependency list and rationale;
3. test commands and complete results;
4. offline demo transcript;
5. security invariants enforced;
6. known limitations;
7. every source reused from existing repositories;
8. divergences from current `nadi-kit` and why;
9. whether Phase B was authorized and performed;
10. exact remote URLs and commit IDs if publication occurred.

No claim such as “secure,” “production-ready,” “decentralized,” or “interoperable” may appear without a corresponding acceptance test or external proof.

---

## 17. Definition of done

v0.2 is done when:

- all required core files exist; `skill.md` is excluded from this blocking gate as stated below;
- schemas validate their examples;
- JCS and Ed25519 golden vectors pass;
- unverified input cannot reach a handler;
- replay tests prove at-most-once handler admission per attempt within the receiver's persisted replay window;
- partial transport failure cannot erase undelivered messages;
- key rotation works without changing stable `node_id`;
- `faw demo` completes fully offline;
- a second implementation can reproduce the vectors without importing the Python package;
- the implementation report is complete;
- an independent reviewer finds no unresolved critical issue.

`skill.md` is a required repository deliverable but is written last and does not block the v0.2 acceptance gate.

The first public milestone after v0.2 is not “more nodes under the same account.” It is:

> One independently controlled external node implements or consumes the contract, accepts a real bounded delegation, and returns a signed receipt that the reference implementation verifies.

Moltbook may be used earlier to recruit that pilot, but only after independent review and with honest “pilot wanted” language. Only after the proof should the project be promoted there as a working federation rather than a laboratory prototype.

---

## 18. Instructions to the independent reviewer

Review this specification against:

1. the manifesto's trust, authority, cost, consent, and delegation principles;
2. the actual default-branch code in the named repositories;
3. RFC 8785 including verified errata;
4. Ed25519 usage and key-lifecycle correctness;
5. replay/idempotency semantics;
6. receipt binding;
7. transport failure semantics;
8. whether the scope is still minimal enough for one focused implementation slice.

Classify findings only as:

- **BLOCKER** — would make signatures, authorization, replay protection, receipts, or transport correctness unsound;
- **REQUIRED** — needed for the stated v0.2 acceptance criteria;
- **OPTIONAL** — useful later but not required for v0.2.

Do not expand the project into registry, governance, reputation, payments, production A2A hosting, Agent City integration, or a new message bus unless a concrete v0.2 invariant cannot otherwise be satisfied.

## Changelog

### v0.2 — adjudicated change order

- **[1] Replay key:** changed receiver replay indexing from `(issuer_node_id, attempt_id, delegation_digest)` to `(issuer_node_id, attempt_id)`, with `delegation_digest` stored and compared. Matching content deduplicates; mismatched content is an integrity violation. This changes replay-state representation, not signed document bytes or document digests.
- **[2] Audience and receipt binding:** inserted fail-closed target validation immediately after schema validation. Added issuer-side checks that a receipt refers to an outstanding delegation issued by the verifier and that `executor_node_id` equals its concrete `target_node_id`. No signed document shape changed.
- **[3] Single authoritative key ID:** removed `signature.kid`; `issuer.kid` is now the sole signed key-resolution field and must resolve to an active key in the issuer's pinned manifest chain. This changes the outer serialized `signature` object, but it does **not** change JCS signing input, content digests, signature bytes, or existing golden cryptographic values because the entire `signature` member is removed before canonicalization.
- **[4] Revocation freshness:** made a pinned manifest chain, head sequence/digest, and pin-observation time explicit verification inputs. Added `manifest_freshness_window_seconds` to node manifests and required fresh/stale reporting. The new manifest field is inside signed content and therefore changes node-manifest canonical bytes, digests, signatures, and node-manifest golden vectors.
- **[5] Domain separation:** required schema `kind` as `const`, `spec_version` as an enum containing `0.2`, and an explicit trusted `expected_kind` verification parameter. Existing conforming fields remain in the signing input; the schema and verification behavior changed, while canonical bytes change only where `spec_version` moves from `0.1` to `0.2`.
- **[7] ASCII identifier grammar:** added explicit patterns for node IDs, capabilities, key/digest IDs, and UUID document IDs to prevent Unicode-confusable identity. Conforming document bytes are otherwise unchanged; previously accepted non-ASCII identifiers now fail schema validation.
- **[8] Admission and execution time:** defined `expires_at` as the admission boundary and `deadline` as the execution boundary, requiring `issued_at < expires_at ≤ deadline` and `authority.expiry ≥ deadline`. No new field was added, but invalid field combinations now fail before admission.
- **[9] Strict-parser note:** added a non-normative warning that duplicate-name detection must happen during parsing because post-parse JCS cannot recover collapsed duplicates. No protocol bytes changed.
- **[N1] Artifact digest:** defined artifact digests as `sha256:<hex>` over exact raw artifact bytes and added a golden fixture. This standardizes digest meaning; document bytes change only if an implementation previously emitted a different artifact digest value.
- **[10] Agent onboarding:** retained `skill.md` in scope and layout, declared it written last, and excluded it from the blocking definition-of-done gate. No normative signed bytes changed.
- **[P3] Reserved manifest path:** renamed the FAW path from `/.well-known/federated-agent.json` to `/.well-known/faw-node.json`; retained the A2A reservation and explicitly prohibited aliasing the template's legacy `agent-federation.json`. This is a discovery-path change only and does not alter signed manifest bytes unless the endpoint/path is itself included in a manifest field.
- **[C1] Issuer pending store:** added `pending.py` and a durable issuer-side outstanding-delegation store used by receipt verification. This adds local state and API requirements, not signed fields.
- **[C2] Closed schemas and extension path:** required `additionalProperties: false` throughout normative objects and specified that forward extension requires a version bump; any future `extensions` object is non-authority-bearing. Conforming v0.2 bytes are unchanged; documents with unknown members are now rejected.
- **[C3] Unified verification procedure:** rewrote §7.3 as one authoritative ordered list with explicit inputs, gating rules, audience validation, pinned trust context, receipt binding, replay comparison, authorization, and atomic admission. No document format changed beyond the separately listed findings.
- **[C4] Conformance coverage:** added executable tests for every new MUST, including wrong-audience relay, non-target executor, unknown/non-outstanding receipt, replay digest mismatch, unknown members, invalid ASCII identifiers, authority/deadline mismatch, and stale pinned-manifest reporting. Test fixtures and reports change; protocol bytes change only where a separately listed schema or field change requires new vectors.


# FAW v0.2 second-implementation brief

This brief is part of the implementer kit. It is written to be sufficient
without inspecting any file outside the kit — in particular without any
Python source, test file, or implementation report. It restates normative
content and adds scoping that the specification leaves to the implementer.

## Purpose

Build a **verifier-focused** second implementation of the FAW v0.2 contract
from the kit's normative sources alone: the governing specification
(`docs/federated-agent-web-build-spec-v0.2.md`), the three JSON Schemas
(`schemas/`), the interoperability profile
(`docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md`), the language-neutral
conformance package (`conformance/v0.2/**`), and the golden vectors
(`vectors/`). The implementation's job is to parse, canonicalize, verify
signatures, validate documents, and emit machine-readable accept/reject
results — not to run a node.

## Normative source priority

Where documents disagree:

1. `docs/federated-agent-web-build-spec-v0.2.md` — the governing
   specification. It is authoritative wherever it states MUST/MUST NOT.
2. `schemas/**` — machine-readable constraints; where a schema and the
   governing prose conflict, the contradiction is an erratum, not a license
   to pick one silently.
3. `docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md` — normative for v0.5
   cross-language conformance only; resolves previously unspecified v0.2
   boundaries (JSON number domain, timestamp instant semantics, UUID
   opacity, half-open key-validity interval, stale-context classification,
   the thirteen semantic rejection categories, and the planned
   negative/positive matrix). It never overrides items 1 or 2.
4. `SPEC.md` — a condensed summary; conflicts lose to the governing
   specification.
5. `conformance/v0.2/**` and `vectors/**` — conformance fixtures:
   reproducible data, not normative text. The conformance package's
   `manifest.json` is the machine-readable contract for the N01–N15 /
   P01–P05 fixtures (per-fixture records, explicit `pinned_at` and the
   freshness rule `fresh iff pinned_at + freshness_window >= now`, head
   sequence/digest from the ordered trust chain, language-neutral
   `local_policy` with no implicit defaults, semantic pending context, byte
   identity). A fixture that contradicts the governing specification is an
   erratum.
6. This brief — scoping and implementation guidance, never a source of new
   normative requirements.

## Required verifier scope

The implementation must independently implement:

1. strict UTF-8 JSON parsing;
2. duplicate object-member rejection;
3. number-domain rejection required by FAW/JCS;
4. schema validation for the three normative document kinds
   (node manifest, delegation, receipt);
5. RFC 8785 JCS canonicalization;
6. removal of the top-level signature member for signing/verification
   input;
7. SHA-256 content and artifact digests;
8. `kid` derivation from raw Ed25519 public-key bytes;
9. base64url without padding;
10. Ed25519 signature verification;
11. manifest-chain continuity and key resolution;
12. delegation structural, audience and temporal checks;
13. receipt signature and delegation-binding checks;
14. machine-readable rejection output.

## Explicit non-goals

Do not implement:

- transport adapters;
- node runtime;
- key storage;
- a daemon;
- an executor;
- a replay database;
- a pending-store implementation;
- GitHub integration;
- Nadi;
- capability execution;
- network access.

Where the full reference performs local stateful admission (pending-record
binding and replay deduplication), the verifier uses explicit input fixtures
representing the required pending or trust state. Every such boundary is
documented in the verification-order section below rather than guessed.

## Verification order

The order below is reproduced from the governing specification §7.3, with
local state boundaries annotated. A later step must not run, and no external
effect may occur, until every applicable earlier step has passed.

1. **Strict parse.** Parse the raw bytes with duplicate-member detection and
   the §7.1 rejection rules. Preserve the parsed structure for schema
   validation and canonicalization; do not round-trip through a permissive
   intermediate representation.
2. **Expected-kind schema validation.** Select the schema from the trusted
   expected-kind parameter — never infer the kind from untrusted input.
   Validate the complete document including the `kind` const, the
   `spec_version` enum, ASCII identifier patterns, and
   `additionalProperties: false`. Reject wrong kind or unsupported version.
3. **Audience binding** (delegations). The addressed target must be the
   local node, or a capability-addressed target must match under explicit
   local policy. Fail closed; never reach later steps on failure.
4. **Temporal and structural admission** (delegations). Enforce
   `issued_at < expires_at <= deadline` and `authority.expiry >= deadline`;
   reject when `now > expires_at` after the configured clock-skew policy.
   Validate cross-field invariants before cryptographic acceptance.
5. **Pinned manifest-chain validation and key resolution.** Validate the
   supplied chain's monotonic sequence, digest links, and every manifest
   signature from the locally approved anchor. Resolve the issuer's key
   identifier only within the chain for the issuer node. The resolved key
   must be active and valid at the document's issuance time. No key
   identifier may be taken from outside the signed content and supplied
   trust context.
6. **Revocation and trust-context freshness.** Enforce retired/revoked
   status against the supplied pinned manifest state. Classify the context
   as `fresh` or `stale` against the declared freshness window, expose the
   pinned head sequence and digest, and never silently return an unqualified
   pass for a stale context.
7. **Core signature verification.** Remove the top-level `signature`
   member, canonicalize the remainder, and verify the Ed25519 signature with
   the key resolved in step 5.
8. **Document binding** (receipts). Against pending-state input fixtures:
   the receipt's delegation digest must match an outstanding delegation
   issued by this node; `task_id` and `attempt_id` must match; the executor
   must equal the concrete target. Unknown, non-outstanding, mismatched, or
   already-terminal delegations are rejected.
9. **Replay lookup and integrity comparison** (delegations). Against
   replay-state input fixtures keyed by `(issuer_node_id, attempt_id)`: no
   record → continue; matching digest → deduplicate and return the stored
   terminal receipt or current state; differing digest → reject as an
   integrity violation.
10. **Local authority and budget evaluation.** Evaluate capability
    authorization, resource scope, external-effect scope, enforceable
    budget, and local policy; reject before execution anything that cannot
    be proven enforceable. (Executor-local; verifier-only builds evaluate
    this only when policy fixtures are supplied.)
11. **Atomic admission.** Create the replay record atomically before
    admitting to any handler. (Executor-local; verifier-only builds verify
    the invariant against supplied fixture state.)

For verifier-only operation, steps 8–11 use explicit fixtures and the
result marks which steps were evaluated from fixtures.

## Strict JSON requirements

- Reject duplicate object member names **at parse time**; a serializer
  operating on an already-parsed mapping cannot detect duplicates the parser
  collapsed, so the parser itself must reject them.
- Reject NaN and Infinity.
- Reject negative zero input (`-0`).
- Reject invalid UTF-8 and lone surrogates.
- Preserve Unicode exactly as supplied; JCS performs no normalization.
- Reject out-of-domain numbers (see the ambiguity inventory for the exact
  domain question).

## RFC 8785 canonicalization

Implement RFC 8785 (JCS) with its verified errata: sort object properties,
serialize numbers per JCS (including exponent forms and decimal
representation), escape strings per JCS, and emit the canonical UTF-8 bytes.
`json.dumps(sort_keys=True)` and equivalent naive sort-then-dump are not
conforming substitutes. The canonicalization vectors in
`vectors/canonicalization/` pin the expected bytes for nested objects,
strings, Unicode, and numbers.

## Digest rules

- Content digest: `sha256:<hex>` of SHA-256 over
  `JCS(document without the top-level signature member)`.
- Artifact digest: `sha256:<hex>` of SHA-256 over the exact raw artifact
  bytes.
- All digest strings match `^sha256:[0-9a-f]{64}$`.

## Ed25519 and kid derivation

- Public keys are raw 32-byte Ed25519 keys, encoded base64url without
  padding.
- `kid` = `sha256:<hex>` of SHA-256 over the raw 32-byte public key.
- Signatures are Ed25519 over the canonical bytes; values are base64url
  without padding.
- The vector key material is TEST-ONLY public fixture data (see the vector
  notes); it is not a deployment identity.

## Manifest-chain verification

- Sequences increase monotonically; each manifest links the previous
  manifest digest; the previous active key signs the manifest introducing
  its replacement; the old key becomes `retired` or overlaps while active.
- Revocation is published in a later manifest signed by a still-valid key.
- The node id never changes.
- Trust anchors are pinned locally; no online lookup occurs in v0.2.

## Delegation verification

Structural checks (schema), audience binding, temporal admission
(`issued_at < expires_at <= deadline`, `authority.expiry >= deadline`,
`now <= expires_at + skew`), issuer chain and key resolution, signature, and
replay integrity are applied in the authoritative order above. A delegation
that fails any step is rejected with a stable reason category.

## Receipt verification

- Exactly one accepted terminal receipt per attempt; status is one of
  `succeeded | failed | rejected | timed_out`.
- The receipt must bind to the exact delegation digest, match
  `task_id`/`attempt_id`, come from the concrete target executor, and close
  an outstanding pending record.
- A second terminal receipt for an already-terminal record is rejected.

## Golden Vector inventory

All files under `vectors/` and `conformance/v0.2/` are conformance
fixtures. Reproduction rules for `vectors/` are given in
`vectors/README.md` (part of the kit). The current inventory:

| Directory | Files |
|---|---|
| `canonicalization/` | `nested.json`, `nested.canonical.hex`, `strings.json`, `strings.canonical.hex`, `unicode.json`, `unicode.canonical.hex`, `numbers.json`, `numbers.canonical.hex` |
| `signatures/` | `message.json`, `message-canonical.hex`, `content-digest`, `keypair.json` (TEST-ONLY key) |
| `delegations/` | `delegation.json` (fully signed), `delegation-digest`, `issuer-manifest.json` |
| `receipts/` | `artifact-input.bin`, `artifact.bin`, `artifact-digest`, `receipt.json` (signed), `receipt-digest`, `executor-manifest.json` |
| root | `README.md` (reproduction guide and key hygiene) |

Reproduction required per vector: canonical bytes (vs `*.canonical.hex`),
content digests (vs `*-digest` files), artifact digest (over raw bytes),
public-key `kid` (vs manifest/keypair entries), and Ed25519 signature
verification.

## Conformance package (language-neutral fixtures)

The `conformance/v0.2/**` package is the intended negative/positive fixture
source for cross-language conformance. `conformance/v0.2/manifest.json` is
the machine-readable contract: every fixture record carries id, expect
(accept/reject), expected_kind, expected_category (null for accept), bytes,
sha256, size_bytes, fixed `now`, explicit `pinned_at`, the ordered
`trust_chain` array (head = last manifest; head sequence/digest derived from
it), language-neutral `local_policy` (all admission-policy fields, no
implicit defaults), semantic pending context for receipts (never a Python
store layout), and for every reject: the exact accepted `source` and
`mutation`. Freshness is `fresh iff pinned_at + freshness_window >= now`
(`stale iff < now`; the equality boundary classifies as fresh). The
package must be consumable from the raw fixtures and its manifest alone,
without any Python source or test.

The frozen interoperability profile's historical phrasing ("planned
matrix", "fixtures not yet created", "future fixture package") describes
repository status at the time that profile was authored; it is NOT an
instruction to create another vector package. The matrix semantics remain
normative, and `conformance/v0.2/**` — N01–N15 and P01–P05 with their
records — is the committed realization; no implementer may infer that
negative fixtures are still missing.

Both sanctioned TEST-ONLY key files — `vectors/signatures/keypair.json` and
`conformance/v0.2/context/test-only-keys.json` — are public reproducibility
fixtures only: they grant no authority, are never deployment identities,
and are never production credentials.

## Required CLI surface

The following CLI is the proposed implementer contract for the Go verifier.
It is an implementer contract, not an FAW v0.2 protocol change.

```text
faw-verifier-go verify-manifest <document> --trust <chain>
faw-verifier-go verify-delegation <document> --trust <chain> --local-node <id> --now <timestamp>
faw-verifier-go verify-receipt <document> --trust <chain> --pending <fixture>
faw-verifier-go vectors verify <kit-directory>
faw-verifier-go fixtures emit <output-directory>
faw-verifier-go conformance-report <kit-directory>
```

- `verify-manifest` / `verify-delegation` / `verify-receipt` verify one
  document file against a pinned manifest chain.
- `vectors verify <kit-directory>` reproduces every vector and reports
  pass/fail per vector.
- `fixtures emit <output-directory>` runs the conformance fixture emitter
  (deterministic, test-only signing).
- `conformance-report <kit-directory>` runs the layered interoperability
  suite from `docs/V0_5_INTEROPERABILITY_PLAN.md` and emits a
  machine-readable report. It MUST NOT require Python or the FAW reference
  repository to run: Layers 1, 2 and 4 are reported directly from the
  delivered kit; Layer 3 emitter/evidence generation runs from Go; the
  Layer 3 reference-verification result is reported as externally
  supplied/post-build evidence or clearly marked pending until the separate
  reference-side evaluation is performed.

## Machine-readable result format

Every verification operation emits one JSON object on stdout:

```json
{
  "implementation": "faw-verifier-go",
  "implementation_version": "0.1.0",
  "faw_spec_version": "0.2",
  "kit_reference_material_commit": "<sha>",
  "kit_manifest_sha256": "sha256:<hex>",
  "kit_archive_sha256": "sha256:<hex>",
  "operation": "verify-delegation",
  "ok": true,
  "reason_code": null,
  "details": {}
}
```

- `kit_reference_material_commit` is the frozen FAW reference-material commit
  recorded in the kit manifest.
- `kit_manifest_sha256` identifies the exact external manifest bytes;
  `kit_archive_sha256` identifies the complete delivered archive. Both are
  defined by the kit build output.
- `operation` is one of `verify-manifest`, `verify-delegation`,
  `verify-receipt`, `vectors`, `fixtures`, `conformance-report`.
- On rejection, `ok` is `false` and `reason_code` is one of the exact
  thirteen stable semantic rejection categories pinned by
  `docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md`: `parse.invalid_json`,
  `parse.duplicate_member`, `parse.invalid_unicode`,
  `canonicalization.number_out_of_domain`, `schema.invalid`,
  `document.kind_mismatch`, `audience.mismatch`, `temporal.invalid`,
  `trust.invalid_chain`, `trust.unknown_key`, `trust.key_not_valid`,
  `signature.invalid`, `binding.mismatch`. The taxonomy is NOT
  implementation-defined; these categories are the cross-language
  conformance categories and are used consistently everywhere in this kit.
- `details` carries structured context (e.g. resolved head sequence and
  digest, evaluated fixture boundaries).
- Exact equality with reference diagnostic wording is not required; the
  cross-language comparison contract is accept-versus-reject plus a stable
  semantic rejection category.

The implementation report must additionally record
`kit_build_head_sha` — the repository state the kit was assembled from. The
verifier may receive that value from its implementation metadata or
conformance configuration; it is not an FAW protocol field.

## Bidirectional interoperability

Four layers, defined in `docs/V0_5_INTEROPERABILITY_PLAN.md`:

1. vector reproduction;
2. reference-to-Go verification of committed fixtures;
3. Go-to-reference verification of emitted TEST-ONLY fixtures — a
   post-build evaluation across the repository boundary: the Go side
   deterministically emits fixtures and records bytes/digests/evidence; a
   separate reference-side evaluator/operator verifies them with the Python
   reference post-build; that result is recorded as interoperability
   evidence;
4. negative interoperability over the committed `conformance/v0.2/**`
   package (N01–N15, P01–P05) with the exact profile categories per fixture.

The acceptance comparison is `accept versus reject` and the stable semantic
rejection category. Internal structure and call stacks are not compared.

The second implementation MUST NOT import, vendor, clone, inspect, invoke,
or depend on the Python reference implementation or its tests. Layer 3's
reference-side verification is performed only by the separate
reference-side evaluator/operator, never by the Go implementation.

## Erratum reporting

Ambiguities and contradictions are reported as spec erratum issues against
the governing specification. A contradiction (two normative statements that
cannot both hold) is an erratum immediately. A question the specification
does not decide is listed in the ambiguity inventory below and resolved by a
documented implementation choice or an additional vector, never by copying
reference behavior.

## Independence declaration

The implementation report must disclose (ADR 0002):

- whether an AI agent was used;
- which exact input kit (manifest digest) it received;
- whether it had network access;
- whether it had any access to the reference repository;
- whether its operator or repository is controlled by the FAW maintainer.

AI use does not automatically invalidate independence. Access to the Python
implementation or its tests invalidates the clean-room claim. No delivered
clean-room instruction directs the second implementation to access or
execute the Python reference repository; Layer 3 reference verification is
performed only by the separate reference-side evaluator/operator as
post-build, externally recorded evidence.

The ADR 0002 citation above is provenance/history only. Every operative
requirement is fully stated in this brief and the interoperability plan;
ADR access is NOT required, and the second implementation does not need and
must not fetch the ADR or the reference repository. This rule also covers
ADR identifiers visible in frozen material — for example the
interoperability profile's ADR 0003 status reference: such identifiers are
provenance/history and are not implementation inputs.

---

## Known questions — resolved and remaining

Most questions the brief previously listed as open are now resolved by the
merged interoperability profile and the committed conformance package.

The following are **resolved** (by `docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md`
and/or committed fixtures; no implementation may treat them as open):

1. **Duplicate-key detection before schema validation** — resolved:
   parse-time rejection is normative; negative case N01 (`parse.duplicate_member`)
   is committed in `conformance/v0.2/negative/`.
2. **Fractional-second equality** — resolved: parsed UTC instants with
   nanosecond precision compare equal across lexical fraction encodings
   (profile §2; P03); `.5Z` == `.50Z` == `.500000000Z`.
3. **JCS number domain** — resolved: finite IEEE-754 binary64 only; safe
   integers ±9007199254740991 (profile §1; P01/P02, N05/N06).
4. **Unicode / lone-surrogate behavior** — resolved: invalid UTF-8 and
   escaped lone surrogates are rejected (profile §6; N03/N04).
5. **UUIDv7 time semantics** — resolved: IDs are opaque after syntax
   validation; no semantics from the embedded timestamp (profile §3; P05).
6. **Manifest key-validity boundaries** — resolved: half-open interval
   `valid_from <= issued_at < valid_until` (profile §4; P04, N11).
9. **Semantic rejection categories** — resolved: the exact thirteen
   categories are pinned by the profile (§6) and used consistently; they are
   not implementation-defined.
10. **Negative fixture coverage** — resolved: the language-neutral package
    `conformance/v0.2/**` is committed (N01–N15, P01–P05, source fixtures,
    per-fixture records); negative coverage is no longer missing.

The following remain **non-normative implementation choices** (the spec
permits either; the verifier documents its choice):

7. **Stale trust context: reject versus qualified pass** — the spec permits
   either a policy rejection or a qualified result and forbids only silent
   unqualified passes; the verifier's reject-vs-qualified behavior is a
   disclosed local-policy choice. Fresh/stale classification itself is fixed
   by the profile (§5).
8. **Receipt verification with pending state supplied as fixture data** —
   the verifier-only build substitutes semantic fixture state; the result
   envelope marks which steps were evaluated from fixtures.

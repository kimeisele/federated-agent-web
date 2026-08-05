# FAW v0.2 second-implementation brief

This brief is part of the implementer kit. It is written to be sufficient
without inspecting any file outside the kit — in particular without any
Python source, test file, or implementation report. It restates normative
content and adds scoping that the specification leaves to the implementer.

## Purpose

Build a **verifier-focused** second implementation of the FAW v0.2 contract
from the kit's normative sources alone: the governing specification
(`docs/federated-agent-web-build-spec-v0.2.md`), the three JSON Schemas
(`schemas/`), and the golden vectors (`vectors/`). The implementation's job
is to parse, canonicalize, verify signatures, validate documents, and emit
machine-readable accept/reject results — not to run a node.

## Normative source priority

Where documents disagree:

1. `docs/federated-agent-web-build-spec-v0.2.md` — the governing
   specification. It is authoritative wherever it states MUST/MUST NOT.
2. `schemas/**` — machine-readable constraints; where a schema and the
   governing prose conflict, the contradiction is an erratum, not a license
   to pick one silently.
3. `SPEC.md` — a condensed summary; conflicts lose to the governing
   specification.
4. `vectors/**` — conformance fixtures: reproducible data, not normative
   text. A vector that contradicts the governing specification is an
   erratum.
5. This brief — scoping and implementation guidance, never a source of new
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

All files under `vectors/` are conformance fixtures. Reproduction rules are
given in `vectors/README.md` (part of the kit). The current inventory:

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
verification. All vectors are positive fixtures; negative boundary coverage
is an open item (see the ambiguity inventory).

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
- `conformance-report <kit-directory>` runs the full layered
  interoperability suite from `docs/V0_5_INTEROPERABILITY_PLAN.md` and emits
  a machine-readable report.

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
- On rejection, `ok` is `false` and `reason_code` is a stable
  machine-readable code defined by the second implementation from the
  semantic rejection categories (see the ambiguity inventory).
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
3. Go-to-reference verification of emitted test-only fixtures;
4. negative interoperability over a shared mutation set.

The acceptance comparison is `accept versus reject` and the stable semantic
rejection category. Internal structure and call stacks are not compared.

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
implementation or its tests invalidates the clean-room claim.

---

## Known questions to be resolved by implementation

Classification legend:

```text
clear from normative text
needs additional vector
needs spec clarification
non-normative implementation choice
```

1. **Exact duplicate-key detection requirements before schema validation.**
   The governing spec requires duplicate-member rejection at parse time and
   a strict-parse step before schema validation; the current positive
   vectors contain no duplicate-member case.
   Classification: clear from normative text (parse-time, before schema
   validation); needs additional vector (no committed negative fixture).

2. **Accepted RFC 3339 fractional-second forms.** The schemas allow
   `ss(.f{1,9})?Z` — whole seconds or 1–9 fractional digits, `Z` only. The
   equality semantics of distinct fraction encodings (e.g. `.5` vs `.50` vs
   none) for temporal comparison are not pinned.
   Classification: schema form is clear from normative text; instant
   equality of distinct encodings needs spec clarification.

3. **JCS number limits and representation.** The spec rejects NaN,
   Infinity, negative zero, and out-of-domain numbers and requires exact JCS
   serialization; the current number vector pins exponent forms and
   `2^53 - 1`. The exact accepted integer domain (and the treatment of
   numbers outside IEEE double precision) is not stated in the governing
   spec.
   Classification: needs spec clarification (domain bound); the current
   vector set is a baseline, needs additional vector (boundary integers).

4. **Unicode and lone-surrogate behavior.** The spec rejects invalid
   Unicode and lone surrogates and preserves strings as supplied without
   normalization. The unicode vector covers preservation; a lone-surrogate
   negative fixture is not committed.
   Classification: clear from normative text; needs additional vector
   (escaped lone surrogate and ill-formed UTF-8 negatives).

5. **UUIDv4 versus UUIDv7 schema behavior.** The identifier grammar
   `[47]...-[89ab]...` accepts both v4 and v7. The governing spec says
   v7 "when available", v4 acceptable. Whether a v7 timestamp component is
   semantically checked against the document time is unspecified.
   Classification: version-digit acceptance is clear from normative text;
   v7 time semantics needs spec clarification.

6. **Manifest key-validity boundaries at exact timestamps.** Keys declare
   `valid_from` and optional `valid_until`; resolution requires the key be
   "active and valid for the document's issuance time". Inclusive/exclusive
   semantics exactly at the boundary timestamps are not stated.
   Classification: needs spec clarification (boundary semantics); needs
   additional vector (key valid exactly at issued_at).

7. **Stale trust context: reject versus qualified pass.** The spec permits
   either a policy rejection or a qualified result for a stale context, and
   forbids only silent unqualified passes. Which behavior the verifier
   chooses is local policy.
   Classification: clear from normative text (both allowed);
   non-normative implementation choice (verifier documents its choice and
   always exposes head sequence and digest).

8. **Receipt verification when pending state is supplied as fixture data.**
   The spec defines receipt binding against an issuer-side pending store;
   the verifier-only build substitutes fixture state. The result envelope
   marks which steps were evaluated from fixtures.
   Classification: non-normative implementation choice (documented
   boundary); no spec change required.

9. **Semantic rejection categories required for cross-language
   comparison.** The governing spec does not define a reason-code taxonomy;
   the reference's rejection taxonomy is an implementation artifact, not
   normative. Cross-language comparison needs a stable category set.
   Classification: needs spec clarification (a normative taxonomy, or an
   explicit statement that categories are implementation-defined); until
   then the categories are a documented implementation choice.

10. **Whether current positive vectors are sufficient for every required
    negative boundary.** The governing spec §13 requires negative coverage
    (NaN/Infinity, negative zero, duplicate members, lone surrogates, wrong
    kind, unknown key, one-byte mutation, digest mismatch, expired
    delegation, stale context); the committed vectors are all positive.
    Classification: needs additional vector (a negative fixture set is
    required before the second implementation can prove negative
    interoperability without consulting the reference).

### Candidate future errata

Listed separately from resolved questions; none is opened in this slice
because none is a demonstrated contradiction of two normative statements:

- **Rejection taxonomy normativity** — either promote the semantic
  rejection categories into the spec or state they are implementation-defined
  (item 9).
- **Number domain** — pin the accepted integer range in the spec (item 3).
- **Key-validity boundary semantics** — state inclusive/exclusive behavior
  at exact `valid_from`/`valid_until` instants (item 6).

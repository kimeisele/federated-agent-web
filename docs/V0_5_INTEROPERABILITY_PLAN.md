# v0.5 interoperability plan

The v0.5 gate requires that the second implementation verify the reference
implementation's documents and vice versa. This plan defines the evidence in
four layers so the claim is testable, bounded, and reproducible. It is part
of the implementer kit (a required input) and references only kit inputs.

The acceptance comparison at every layer is:

```text
accept versus reject
and stable semantic rejection category
```

Exact human-readable wording, internal implementation structure, call
stacks, and diagnostics need not match.

## Settled cross-language contract

The cross-language contract is settled and committed:

- `docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md` — normative for v0.5
  cross-language conformance only; it pins the JSON number domain, parsed-
  instant timestamp equality, UUID opacity, the half-open key-validity
  interval, stale-context classification, and the exact thirteen stable
  semantic rejection categories:
  `parse.invalid_json`, `parse.duplicate_member`, `parse.invalid_unicode`,
  `canonicalization.number_out_of_domain`, `schema.invalid`,
  `document.kind_mismatch`, `audience.mismatch`, `temporal.invalid`,
  `trust.invalid_chain`, `trust.unknown_key`, `trust.key_not_valid`,
  `signature.invalid`, `binding.mismatch`.
- `conformance/v0.2/**` — the committed language-neutral realization of the
  profile's planned matrix: negative cases N01–N15 and positive boundary
  cases P01–P05, with machine-readable fixture records
  (`conformance/v0.2/manifest.json`: expected kind, accept/reject, exact
  category, `pinned_at`/freshness rule, language-neutral `local_policy`,
  semantic pending context, byte identity, mutation provenance).

The profile's historical phrasing ("planned matrix", "fixtures not yet
created", "future fixture package") describes the repository status at the
time that frozen profile was authored; it is not an instruction to create
another vector package. The matrix semantics remain normative, and
`conformance/v0.2/**` is their committed realization: no implementer may
infer that negative fixtures are still missing.

The thirteen categories are the cross-language conformance categories for
every layer below; they are not chosen by the second implementation.

## Layer 1 — Vector reproduction

The second implementation reproduces, for every committed vector in
`vectors/`:

- canonical bytes (compare against `*.canonical.hex`);
- content digests (compare against `*-digest` files);
- artifact digests (SHA-256 over exact raw artifact bytes);
- public-key `kid` (SHA-256 over raw 32-byte public key);
- Ed25519 signature verification.

Pass means every vector reproduces with byte-exact equality. This layer is
the entry gate: no higher layer runs until Layer 1 passes.

## Layer 2 — Reference-to-Go verification

The second implementation verifies the committed reference fixtures:

- `vectors/signatures/message.json` (signed message, canonical bytes,
  content digest);
- `vectors/delegations/issuer-manifest.json` (genesis manifest),
  `delegation.json` (fully signed delegation);
- `vectors/receipts/executor-manifest.json` (genesis manifest),
  `receipt.json` (terminal signed receipt), plus
  `artifact-input.bin` / `artifact.bin` / `artifact-digest` binding.

No Python code or package import is involved. The trust chain for each
document comes from the committed manifest fixtures in the kit.

## Layer 3 — Go-to-reference verification (repository-boundary split)

Layer 3 is a post-build bidirectional interoperability evaluation across
the repository boundary. The Go implementation MUST NOT import, vendor,
clone, inspect, invoke, or depend on the Python reference implementation or
its tests (ADR 0002 independence declaration).

The Go side:

- the second implementation's deterministic **conformance fixture emitter**
  creates new TEST-ONLY fixtures:
  - a genesis manifest (with a freshly generated ephemeral TEST-ONLY key);
  - a delegation signed by that manifest's key;
  - a terminal receipt bound to the delegation digest;
  - artifact digest metadata;
- the Go-side report records the emitted fixture bytes/digests and the
  evidence required for external verification.

The reference side (post-build, separate):

- a separate reference-side evaluator/operator verifies those emitted
  fixtures using the Python reference implementation through its public
  verification interface;
- that reference-side result is then recorded as interoperability evidence
  in the implementation report.

Constraints:

- no fixture may reuse a deployment private key;
- emitter output must be deterministic for a given seed/key so the evidence
  is reproducible;
- the emitter is not a node, transport, executor, or production signing
  service (ADR 0002);
- the Go implementation never runs or reads the Python reference.

## Layer 4 — Negative interoperability

Both implementations must agree on rejection for the committed
language-neutral negative package `conformance/v0.2/negative/**`
(N01–N15), derived from the committed positive/source fixtures (P01–P05 and
`source/**`), with the exact rejection category per fixture as recorded in
`conformance/v0.2/manifest.json` and pinned by the interoperability
profile. Positive boundary cases P01–P05 are part of the same committed
package.

Agreement means: both reject, and both report the same stable semantic
rejection category from the exact thirteen profile categories. The negative
package is committed; it is not created by either implementation.

## Evidence and reporting

The `faw-verifier-go conformance-report <kit-directory>` command emits a
machine-readable report. It MUST NOT require Python or the FAW reference
repository to run:

- Layers 1, 2 and 4 are reported directly from the delivered kit;
- Layer 3 emitter/evidence generation runs from Go;
- the Layer 3 reference-verification result is reported as externally
  supplied/post-build evidence, or that sub-step is clearly marked pending
  until the separate reference-side evaluation is performed.

The implementation report records the kit provenance — the frozen
reference-material commit, the manifest digest, the archive digest, and the
kit build HEAD — the Layer 3 reference-side verification evidence, the
negative-interoperability matrix (N01–N15), and the independence declaration
required by ADR 0002.

## Gate

v0.5 is complete only when Layers 1–4 pass — including the Layer 3
reference-side verification of the Go-emitted fixtures — and the
implementation is Class A (external) or the evidence is explicitly labeled
Class B (maintainer-controlled) without claiming external independence.

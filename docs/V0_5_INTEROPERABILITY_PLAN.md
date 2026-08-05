# v0.5 interoperability plan

The v0.5 gate requires that the second implementation verify the reference
implementation's documents and vice versa. This plan defines the evidence in
four layers so the claim is testable, bounded, and reproducible. It is part
of the implementer kit and references only kit inputs.

The acceptance comparison at every layer is:

```text
accept versus reject
and stable semantic rejection category
```

Exact human-readable wording, internal implementation structure, call
stacks, and diagnostics need not match.

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

## Layer 3 — Go-to-reference verification

The second implementation's deterministic **conformance fixture emitter**
creates new test-only fixtures:

- a genesis manifest (with a freshly generated ephemeral TEST-ONLY key);
- a delegation signed by that manifest's key;
- a terminal receipt bound to the delegation digest;
- artifact digest metadata.

The Python reference verifies every emitted document through its public
verification interface, and the emitter's output is recorded in the
implementation report.

Constraints:

- no fixture may reuse a deployment private key;
- emitter output must be deterministic for a given seed/key so the evidence
  is reproducible;
- the emitter is not a node, transport, executor, or production signing
  service (ADR 0002).

## Layer 4 — Negative interoperability

Both implementations must agree on rejection for a small shared set of
mutations applied to committed positive fixtures:

- duplicate JSON member;
- unknown object member;
- wrong kind;
- wrong audience;
- expired delegation;
- unknown key ID;
- one-byte signature mutation;
- receipt delegation-digest mismatch.

Agreement means: both reject, and both report the same stable semantic
rejection category. The category set is defined by the second
implementation's machine-readable taxonomy and recorded in the
implementation report until the specification pins one (see the implementer
brief's ambiguity inventory, item 9).

Because the committed vectors are currently all positive, Layer 4 needs an
additional negative fixture set. This is tracked in the ambiguity inventory
(item 10); the negative set is derived from the specification's §13
requirements, never from reference test code.

## Evidence and reporting

The `faw-verifier-go conformance-report <kit-directory>` command emits a
machine-readable report covering all four layers. The implementation report
records the kit provenance — the frozen reference-material commit, the
manifest digest, the archive digest, and the kit build HEAD — the emitter
verification results from the reference, the negative-interoperability
matrix, and the independence declaration required by ADR 0002.

## Gate

v0.5 is complete only when Layers 1–4 pass and the implementation is
Class A (external) or the evidence is explicitly labeled Class B
(maintainer-controlled) without claiming external independence.

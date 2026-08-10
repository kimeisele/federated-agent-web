# Spec sufficiency audit — FAW v0.2 minimal input, Rust implementation (L0 → L1)

## 1. Purpose

Falsification-oriented audit of whether the minimal public FAW v0.2 protocol
material exposes enough semantics for a fresh Rust implementation to complete
L0 → L1 against the published vectors. Executed per control issue
[#41](https://github.com/kimeisele/federated-agent-web/issues/41)
(slice 1). This is NOT Class A evidence, NOT an independent-implementation
claim, and NOT proof that the specification is sufficient.

## 2. Method

Two logically separate roles. A fresh Rust Implementer received only the
exact allowlisted input files (with SHA-256 identities), the L0 observable
contract, the narrowed delegation-only L1 observable contract, and generic
environment information — implemented L0, then L1, offline, recording every
assumption/guess in a running log. An Auditor (who knew the audit hypotheses)
observed without coaching, then evaluated the hypotheses against the
observable behavior and the Implementer's log.

## 3. Exact input set

Pinned FAW commit: `255301717fefe37a130996790378ebcef7f0a477` (current `main`
at issue creation). 20 files, byte-verified before use (SHA-256):

| Path | SHA-256 |
|---|---|
| SPEC.md | 825b97f4917a0068db297eb4fb9cf09d3c3a8092d282b5f5e010962aaa05a90f |
| docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md | 17c6a5585ba1c5f63dff45a1783256a13db19308c488f519adf8a39798f0af48 |
| vectors/canonicalization/nested.json | 6cd6dbac012537e0684f5adf357a2b3fea3c07abd8cc6fbae6d149859badab64 |
| vectors/canonicalization/nested.canonical.hex | db3beadbc6a3e0faba74fd31d77c8e70697992ff5953903d0b2f03792e5aadb5 |
| vectors/canonicalization/strings.json | 88f77b69874ca1502f22f9b7c9bb8c88b31e2c3f83fa6fa019beff50afc41547 |
| vectors/canonicalization/strings.canonical.hex | d5384c254e94c4d8131b7b921d5c06f379eec8fe0c18ce1db834231abe1b8df9 |
| vectors/canonicalization/unicode.json | 0b132f96c216c0f375a58026a82e00d055cd13f6bc863b59715a74cb511006d9 |
| vectors/canonicalization/unicode.canonical.hex | ac2e52029051a01c9f6b37e4ae687ab8b43791eaca9d7e334b28cc867fcd2ef3 |
| vectors/canonicalization/numbers.json | 8b8c18b1c5caf1e43766ab9e8a56bbae748b360e936cc1959729f525ed4b9e83 |
| vectors/canonicalization/numbers.canonical.hex | 783a54fbb5fccd62c1263b7164d82da8a609a76d5daca79084847062705e12ec |
| conformance/v0.2/negative/N01.json | 1000ee2f91ce33b78f0eaa8d576d3647c45cb53fe091b5e6bcfc801c7eda7625 |
| conformance/v0.2/negative/N02.json | ea9d53791e1bd57dda732b32862611b9de25cc9506191ffd7be3c6fbe8ec0420 |
| conformance/v0.2/negative/N03.json | 9330bc8235f40984aa62ba9972c665089a3c0eca32c57b6a2e39cb7372a3ca67 |
| conformance/v0.2/negative/N04.json | e3e2b6ebb5b6451651f30724d1708a543389c28275a4ce2a9775f4ab192f97de |
| conformance/v0.2/negative/N05.json | 1064c2cd2212f3e1ed1f626ddf8eca1d0048ad901fe671b29eff9bfd94bf6a2b |
| conformance/v0.2/negative/N06.json | 1e194f1959c4065c61f25cd7ccb1016b00f4e52989b8d9d473c255423109641a |
| schemas/delegation.schema.json | 2c83d4fd716c914425fb019732dd30c35232f5a28d728354ca09d645a785b020 |
| vectors/delegations/delegation.json | f2b78d45d41c73aa170d9e7657cd2be814a9b84584058561106542c9a11a179f |
| vectors/delegations/delegation-digest | 289bf7bfe9b4c040185ae7c067550490d3196e37df3518183e645e9b4cd1d540 |
| vectors/delegations/issuer-manifest.json | aff51e1706b9576a47de6174c30018059b6f7576975ae8b0ed2fd14b6bdd6e53 |

## 4. Isolation and contamination limitations

- Workspace: `/private/tmp/faw-suff-ws` (outside the FAW repository); contains
  only the 20 allowlisted inputs, the Rust project, and the task text.
- Network disabled during implementation and test execution: all cargo
  operations ran with `CARGO_NET_OFFLINE=true` and `--offline` (any download
  attempt hard-fails); no web search.
- No FAW repository clone, no Python implementation/tests, no Go verifier
  inside the workspace. Dependencies frozen by preflight (see §5).
- **Isolation breach (process violation), disclosed:** the Implementer's own
  log documents that during implementation it executed external runtimes
  outside the workspace — Python (stdlib and the `cryptography` library) for
  cross-checks, Node for differential testing (100k random doubles, 4000
  random JSON documents), and it resolved one ambiguity by deferring to the
  behavior of the Python `rfc8785` package named in SPEC.md §2. This violates
  the isolation contract of #41. Consequences: (a) the PASS direction is not
  meaningful — this audit cannot certify "no gap found" under sterile
  conditions; (b) the confirmed gaps in §8 are nevertheless grounded in the
  material itself (two plausible readings, internal tension, vector
  non-discrimination) and were verified independently by the Auditor, not
  taken from the Implementer's resolution.
- **Contamination limitation (mandatory):** the implementation may have been
  performed by a model whose training data could already contain public FAW
  material; instruction-level isolation cannot prove absence of prior
  exposure. A PASS would mean only that no gap was found by this audit and
  would not prove that the specification is sufficient or that the
  implementation had no prior exposure through model training. This audit
  did not reach a PASS.

## 5. Rust environment

- `rustc 1.88.0 (6b00bc388 2025-06-23)`, `cargo 1.88.0 (873a06493 2025-05-10)`.
- Offline dependency preflight (environment-only, before code): local Cargo
  registry/cache inspected; the generic crypto crates `sha2`/`ed25519-dalek`
  are NOT cached, but the `openssl` crate (0.10.81, cached) links the
  locally installed system OpenSSL 3.6.3 (`/usr/local/opt/openssl@3`, which
  provides SHA-256 and Ed25519 via EVP), and `base64` 0.22.1 is cached.
- Frozen dependencies (declared before implementation, no download after
  start): `openssl = "=0.10.81"`, `base64 = "=0.22.1"` — generic
  implementation infrastructure (cryptography, encoding); nothing
  FAW-specific. Everything else is Rust standard library or hand-written
  implementation code (strict JSON parser, JCS canonicalizer, JSON Schema
  subset validator).
- Offline build validated with a skeleton before implementation: `cargo build
  --offline` compiles both crates from the local cache.

## 6. L0 result

All 4 canonicalization vectors reproduce byte-identical canonical bytes
(`nested`, `strings`, `unicode`, `numbers`; the numbers vector pins
`1e+30` with plus sign and `1e-7` without exponent zero-padding). All 6
negative fixtures are rejected with exactly the profile §6 category:
N01 `parse.duplicate_member`, N02 `parse.invalid_json`, N03/N04
`parse.invalid_unicode`, N05/N06 `canonicalization.number_out_of_domain`.
Reproduced independently by the Auditor (binary re-run, exit 0).

## 7. L1 result

Delegation-only (the 9 steps of #41): schema validation of
`delegation.json` against `schemas/delegation.schema.json` passes; content
digest `sha256:da4c4286…717d66d` matches `delegation-digest`
byte-for-byte; `issuer.kid` resolves to the manifest key entry
(`sha256:b5a55a3e…5aec2`, `alg=Ed25519`, `status=active`); the kid derived
from the raw 32-byte public key matches; the Ed25519 signature verifies over
JCS(delegation minus signature). All 5 L1 checks PASS (verified by Auditor
re-run, exit 0). No L2/L3 semantics (audience, temporal, chain, freshness,
replay, pending, authority/budget) were implemented.

## 8. Confirmed specification gaps

### GAP 1 — Object member-name sort order is ambiguous and vector-undetectable

- **Normative location:** SPEC.md §2 ("sort object properties and serialize
  numbers exactly as JCS requires") and SPEC.md §2's naming of "the
  maintained `rfc8785` package"; `docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md`
  §1/§6 (JCS semantics).
- **What the Implementer had to guess:** whether member names are ordered by
  Unicode code points or by UTF-16 code units (RFC 8785 §3.2.3 requires
  UTF-16 code units; the named Python `rfc8785` package orders by code
  points). The two orders differ for astral keys (U+10000+) alongside BMP
  keys in U+E000–U+FFFF (e.g. code points: U+E000 before U+1F680; UTF-16
  units: 0xD83D < 0xE000, so U+1F680 first).
- **Competing plausible readings:** (a) "exactly as JCS requires" → RFC 8785
  UTF-16 code-unit order; (b) "the maintained rfc8785 package" → the package's
  actual (code-point) order.
- **Observable evidence:** every committed vector key is ASCII, so all four
  canonicalization vectors pass under either ordering; the produced
  implementation demonstrably sorts by code points (plain string comparison)
  while matching every committed vector. No provided vector discriminates.
- **Proposed wording-level clarification:** state the ordering base
  explicitly in SPEC.md §2 / the profile ("member names are ordered by their
  UTF-16 code-unit sequences" or by code points — exactly one, named) and
  state that it is a canonicalization input, not a display choice.
- **Boundary vector for the later fix:** an object whose keys are `"\uE000"`
  and `"\u{1F680}"` (canonical hex pins whichever order is normative).

### GAP 2 — "Integer-valued JSON numbers" is ambiguous and internally in tension with the vectors

- **Normative location:** profile §1 ("For integer-valued JSON numbers, the
  inclusive safe-integer interval is required: −9007199254740991 through
  9007199254740991"); vectors/canonicalization/numbers.json (contains the
  accepted `1e+30`).
- **What the Implementer had to guess:** whether the safe-integer domain
  applies to a number's mathematical value (in which case `1e+30`, being an
  integer, would have to be rejected — contradicting the accepted positive
  vector) or to the lexical form (integer literals only). The Implementer
  had to infer the lexical reading to avoid the contradiction.
- **Competing plausible readings:** "integer-valued JSON numbers" = numbers
  whose exact decimal value is an integer (value-based) vs numbers written in
  pure integer form without `.`/`e`/`E` (lexical).
- **Proposed wording-level clarification:** define "integer-valued" precisely
  and state the treatment of exponent/decimal lexical forms (e.g. "the
  safe-integer interval applies to numbers whose exact decimal value is an
  integer and that are written without an exponent; exponent forms such as
  1e+30 are admitted as binary64 values" — or the opposite, exactly one).
- **Boundary vectors for the later fix:** positive/negative pairs
  `9007199254740991.0` / `9.007199254740991e15` (accept) and
  `9007199254740992.0` / `9.007199254740992e15` (reject), in both decimal and
  exponent lexical forms.

### GAP 3 — The JCS number serialization algorithm is not contained in the material; boundary cases are unpinned

- **Normative location:** SPEC.md §2/§7.1 ("serialize numbers exactly as JCS
  requires"; "Canonicalization is RFC 8785 (JCS)"); profile §1 (domain only).
  The serialization algorithm itself (shortest round-trip representation,
  tie-breaking convention, decimal↔exponent thresholds, exponent format) is
  delegated to the external RFC 8785, whose text is not part of the allowed
  material and is unreachable offline.
- **What the Implementer had to guess:** the tie-breaking convention for
  shortest-round-trip digits (round-half-even per ECMAScript vs round-half-up
  in Rust's std), the decimal/exponent thresholds (e.g. 1e-6 vs 1e-7,
  1e20 vs 1e21), and the exponent sign/format. The four committed vector
  numbers (0.1, 42, 9007199254740991, 1e+30, 1e-7) pin neither a tie case
  nor the thresholds.
- **Competing plausible readings:** e.g. for a tie like 900719925474099.25,
  "900719925474099.2" (ES, round-half-even) vs "900719925474099.3" (Rust,
  half-up) — both are shortest round-trip strings, and the material does not
  choose.
- **Proposed wording-level clarification:** pin the algorithm (reference the
  ECMAScript `Number::toString` semantics including tie-breaking, or include
  the serialization rules in the material) and state the decimal/exponent
  thresholds.
- **Boundary vectors for the later fix:** a tie case (e.g.
  `900719925474099.25` with its canonical hex), and threshold cases (e.g.
  `1e-6`, `1e-7`, `1e20`, `1e21` with canonical hex).

## 9. Non-gaps / hypotheses not reproduced

- Duplicate object members: the requirement to reject them at parse time is
  discoverable (SPEC.md §2; profile §6 matrix N01). Implemented without
  coaching; N01 rejected correctly. Not a gap.
- Unicode / lone surrogates: discoverable (SPEC.md §2; N03/N04). Escaped
  `\ud800` rejected as `parse.invalid_unicode`; the unicode vector reproduces.
  Not a gap.
- NaN / Infinity / -Infinity classification: the profile §6 mapping notes
  assign non-finite tokens to `canonicalization.number_out_of_domain`;
  discoverable. Not a gap (no committed fixture exercises the tokens).
- Raw-byte parsing boundary: SPEC.md §2 and the profile require
  parse-time duplicate detection; the hand-written parser operates on raw
  bytes by construction. Not a gap. (Note: an implementer relying on a
  normal in-memory JSON library would fail N01; the requirement is stated in
  the material.)
- Negative zero: the profile rejects "negative zero"; the exact lexical form
  set (-0 vs -0.0 vs -0e5) is unpinned but both readings reject with the
  same category. Noted, not classified as a gap.
- kid derivation, digest spelling, schema subset, category strings: all
  discoverable from the material (SPEC.md §1/§2/§6, schemas, profile §6).
  Not gaps.

## 10. Proposed clarifications

See the per-gap proposals in §8 (GAP 1: pin the member-name ordering base;
GAP 2: define "integer-valued" and the treatment of exponent forms; GAP 3:
pin the number serialization algorithm, tie-breaking, and thresholds). All
were formulated from the allowlisted material and Unicode/ECMAScript
reasoning only; no Python or Go implementation was inspected to formulate
them.

## 11. Limitations

- The isolation breach described in §4 means the Implementer's results cannot
  support a clean PASS claim; the confirmed gaps stand on the material
  analysis and Auditor verification.
- The audit covers the narrowed delegation-only L1 and the canonicalization
  domain only; L2/L3 semantics were not audited.
- Model-training contamination cannot be excluded (see §4).
- The gap set is specific to the allowlisted minimal material; the full
  governing build specification (not in the allowlist) was not audited here.

## 12. Final result

`SPEC SUFFICIENCY: GAPS FOUND`

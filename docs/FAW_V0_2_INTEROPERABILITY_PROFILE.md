# FAW v0.2 Interoperability Profile (v0.5 cross-language conformance)

**Status:** normative for v0.5 cross-language conformance only (ADR 0003).

This profile pins the verification semantics that two independent
implementations must agree on before any negative-vector generation or any
second-language implementation begins. It records decisions, not code: it
contains no schema, no vector fixtures, and no implementation.

## Scope and authority

This profile is normative for **v0.5 cross-language conformance** only. It
clarifies previously unspecified v0.2 boundaries without changing:

* wire format;
* schemas;
* protocol version;
* signature input;
* transport interface.

Where a rule in this profile would contradict the governing v0.2
specification or the normative schemas, the contradiction is reported as an
erratum rather than silently overridden. No rule in this document does so:
every pin below either restates governing normative text or fills a boundary
the governing text leaves open, as identified in the implementer brief's
ambiguity inventory.

### Relationship to the implementer brief ambiguity inventory

| Brief item | Question | Resolution |
|---|---|---|
| 1 | duplicate-key detection before schema validation | N01; clear from normative text (§7.1, §7.3 step 1) |
| 2 | fractional-second equality | §2 Timestamp semantics |
| 3 | JCS number domain | §1 JSON number domain |
| 4 | Unicode / lone-surrogate behavior | N03, N04; clear from normative text (§7.1) |
| 5 | UUIDv7 time semantics | §3 UUID semantics |
| 6 | key-validity boundary instants | §4 Manifest-key validity interval |
| 7 | stale context reject vs qualified pass | §5 Stale trust context |
| 8 | pending state as fixture data | unchanged; verifier-only boundary already documented in the brief |
| 9 | semantic rejection categories | §6 Stable semantic rejection categories |
| 10 | negative boundary coverage | planned matrix below; fixtures not yet created |

## Source priority for cross-language conformance

Where documents disagree during v0.5 cross-language conformance:

1. `docs/federated-agent-web-build-spec-v0.2.md` — the governing
   specification;
2. normative schemas (`schemas/**`);
3. **this interoperability profile** — for previously unspecified
   boundaries only;
4. `SPEC.md` — condensed summary;
5. Golden Vectors (`vectors/**`) — conformance fixtures, not normative text.

This profile never overrides items 1 or 2; it resolves what they leave open.

## 1. JSON number domain

The accepted JSON number domain is:

```text
finite IEEE-754 binary64 values only
```

The following are rejected:

```text
NaN
Infinity
-Infinity
negative zero
```

For integer-valued JSON numbers, the inclusive safe-integer interval is
required:

```text
-9007199254740991  through  9007199254740991
```

(i.e. `-(2^53 - 1)` through `2^53 - 1`). A value outside this interval must
not be silently rounded before verification: it is rejected, never
approximated.

Fields requiring a larger exact numeric domain must use an explicitly
defined string representation. Existing decimal monetary ceilings remain
strings (`budget.max_cost_usd` and any equivalent decimal-string ceiling);
this pin does not convert them to JSON numbers.

No schema changes accompany this decision.

## 2. Timestamp semantics

The schema lexical form is unchanged:

```text
UTC
Z suffix
zero or 1–9 fractional digits
```

Pattern matching alone is not sufficient: the string must denote a valid
calendar instant. A string that matches the pattern but names an impossible
date or time (for example month 13, hour 25, or a fractional-second form
outside the accepted grammar) is not a valid timestamp.

Semantic comparisons operate on the parsed UTC instant with nanosecond
precision. These represent the same instant and MUST compare equal:

```text
2026-01-01T00:00:00.5Z
2026-01-01T00:00:00.50Z
2026-01-01T00:00:00.500000000Z
```

The original signed JSON timestamp is never rewritten before signature
verification or JCS canonicalization: the bytes that were signed are the
bytes that are verified. No non-`Z` offsets are added, normalized, or
emitted.

## 3. UUID semantics

Schema acceptance of UUIDv4 and UUIDv7 is unchanged (version digit `4` or
`7`, variant digit `[89ab]`).

After syntax validation, document IDs are opaque. Implementations must not
derive from the embedded timestamp of a UUIDv7:

* trust;
* ordering;
* expiry;
* issuance time.

A UUIDv7's embedded timestamp does not need to match `issued_at`. Signed
timestamp and sequence fields (`issued_at`, `expires_at`, `deadline`,
`manifest_sequence`, `valid_from`, `valid_until`) define protocol timing and
order.

## 4. Manifest-key validity interval

The temporal validity rule for a resolved key is:

```text
valid_from <= document.issued_at < valid_until
```

when `valid_until` exists. Therefore:

```text
issued_at == valid_from
→ valid

issued_at == valid_until
→ invalid
```

Absence of `valid_until` means no temporal upper bound from the key entry
(rotation and revocation are governed by the manifest chain).

This rule does not override key status (`active`/`retired`/`revoked`),
revocation, manifest-chain validation, trust freshness, node identity, or
`kid` binding. It only pins the inclusive/exclusive boundary semantics of
the key's own validity window.

## 5. Stale trust context

The governing rule is kept:

* a stale context may be rejected by local policy;
* or returned as a qualified result;
* it must never silently produce an unqualified pass.

For cross-language conformance:

```text
fresh/stale classification must agree
```

The classification of a pinned manifest head against its declared
`manifest_freshness_window_seconds` is a semantic fact both implementations
must compute identically for the same inputs.

Local policy may differ between reject and qualified pass when that policy
is disclosed. Identical stale-policy behavior is not required.

## 6. Stable semantic rejection categories

The interoperability categories for v0.5 cross-language conformance are
exactly:

```text
parse.invalid_json
parse.duplicate_member
parse.invalid_unicode
canonicalization.number_out_of_domain
schema.invalid
document.kind_mismatch
audience.mismatch
temporal.invalid
trust.invalid_chain
trust.unknown_key
trust.key_not_valid
signature.invalid
binding.mismatch
```

These are conformance-result categories. They are not new FAW document
fields.

Exact exception names, human-readable messages, stack traces, or Python
implementation details do not need to match. Reference-implementation
internal rejection-code names are implementation artifacts and are not
copied merely to force equality.

Mapping notes for the strict-parse rejections of the governing spec §7.1:

* malformed JSON syntax → `parse.invalid_json`;
* duplicate object member names → `parse.duplicate_member`;
* invalid UTF-8 and escaped lone surrogates → `parse.invalid_unicode`;
* non-finite values (`NaN`, `Infinity`, `-Infinity`), negative zero, and
  integers outside the safe-integer interval →
  `canonicalization.number_out_of_domain`.

## Planned negative-vector matrix

The next vector slice is documented here; the fixtures are NOT created in
this slice. The matrix below is the exact acceptance contract for the future
fixture package.

Required rejection cases:

```text
N01 duplicate object member
     → parse.duplicate_member

N02 malformed JSON
     → parse.invalid_json

N03 invalid UTF-8
     → parse.invalid_unicode

N04 escaped lone surrogate
     → parse.invalid_unicode

N05 integer +9007199254740992
     → canonicalization.number_out_of_domain

N06 integer -9007199254740992
     → canonicalization.number_out_of_domain

N07 unknown object member
     → schema.invalid

N08 wrong expected document kind
     → document.kind_mismatch

N09 wrong delegation audience
     → audience.mismatch

N10 expired delegation
     → temporal.invalid

N11 document issued exactly at key valid_until
     → trust.key_not_valid

N12 unknown issuer kid
     → trust.unknown_key

N13 broken manifest previous-digest link
     → trust.invalid_chain

N14 one-byte Ed25519 signature mutation
     → signature.invalid

N15 receipt delegation-digest mismatch
     → binding.mismatch
```

Required positive boundary cases:

```text
P01 +9007199254740991 accepted

P02 -9007199254740991 accepted

P03 equivalent fractional timestamps compare equal

P04 document issued exactly at key valid_from accepted

P05 valid UUIDv7 remains acceptable even when its embedded timestamp differs
    from issued_at
```

### Future fixture-package requirements

The future fixture package must contain, per fixture:

* raw fixture bytes;
* expected kind;
* trust / pending / local-node context;
* expected accept/reject;
* expected rejection category;
* positive-source provenance for mutations (the exact positive fixture each
  negative is derived from, and the precise byte-level mutation applied).

The package must not depend on Python test files. It is consumed by any
implementation, in any language, from the raw fixtures and their metadata
alone.

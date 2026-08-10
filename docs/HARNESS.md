# FAW v0.2 language-neutral conformance harness contract

Control issue: [kimeisele/federated-agent-web#43](https://github.com/kimeisele/federated-agent-web/issues/43).

This document defines the smallest language-neutral process contract that
lets an arbitrary verifier implementation be tested against the existing FAW
v0.2 conformance package (`conformance/v0.2/manifest.json` and its referenced
package files), without filesystem assumptions or language-specific
integration. It defines only the process/interface boundary. It does NOT
change the FAW v0.2 wire format, schemas, signature input, canonicalization
semantics, trust semantics, rejection-category semantics, or existing
vectors. The interoperability profile and the conformance package remain
authoritative.

## Process model

One harness invocation processes exactly ONE verification request:

1. the runner starts `<cmd>`;
2. the runner writes exactly one JSON request to the harness stdin;
3. the harness writes exactly one JSON result to stdout;
4. the process exits.

No daemon, socket, HTTP server, Docker requirement, plugin framework, or
persistent session. stderr may carry diagnostics; stdout is reserved
exclusively for the single result JSON object.

## Request envelope

The runner writes exactly one JSON object to stdin:

```json
{
  "harness_version": "1",
  "protocol_version": "0.2",
  "request_id": "<opaque runner-generated id>",
  "document_bytes_b64": "<base64 of the exact raw document bytes>",
  "expected_kind": "faw-delegation",
  "now": "2026-01-01T00:00:00Z",
  "pinned_at": "2026-01-01T00:00:00Z",
  "trust_chain": [
    { "bytes_b64": "<base64 of the raw manifest bytes>" }
  ],
  "local_node_id": "urn:faw:...",
  "local_policy": {
    "clock_skew_seconds": 60,
    "reject_stale": false,
    "can_enforce_tokens": true,
    "can_enforce_cost": false,
    "allowed_external_effects": ["none"],
    "allowed_actions": null,
    "capability_targets": {},
    "max_wall_seconds_cap": null,
    "max_output_bytes_cap": null
  },
  "pending": null
}
```

Field semantics:

- `harness_version` / `protocol_version`: versioned contract identifiers
  (currently `"1"` and `"0.2"`). Unsupported values are operational errors.
- `request_id`: opaque, runner-generated; it carries no fixture
  classification and must be echoed back unchanged.
- `document_bytes_b64`: the exact raw document bytes, base64-encoded. Raw
  bytes are always transported inline as base64 — never as decoded JSON
  values — so malformed JSON, invalid UTF-8, duplicate members and other
  byte-level cases survive exactly.
- `expected_kind`: `faw-delegation` | `faw-receipt` | `faw-node-manifest`.
- `now`, `pinned_at`: RFC 3339 UTC timestamps (the pinned observation time
  and verification time; deterministic freshness classification requires
  both; the harness must not substitute wall-clock values).
- `trust_chain`: ordered pinned manifest chain, each manifest's exact raw
  bytes base64-encoded, genesis first.
- `local_node_id`: the verifying node, or `null` where not applicable.
- `local_policy`: the complete admission policy (all fields; consumers
  construct their policy from these values alone, with no implicit
  defaults).
- `pending`: the minimal semantic issuer-side pending record for a receipt
  binding, or `null`:

```json
{
  "task_id": "...",
  "attempt_id": "...",
  "delegation_digest": "...",
  "executor_node_id": "...",
  "status": "outstanding"
}
```

`pending` never carries `delegation_source`, source fixture paths, full
delegation bytes, issuer metadata, or `issued_at`/`expires_at`/`deadline`
merely to satisfy a private store representation.

## No answer leakage

The request never exposes the expected answer. It never contains: expected
verdict, expected rejection category, `expect`, `expected_category`,
mutation descriptions, source fixture identity, fixture paths, or fixture
IDs (P01/N01-style). The harness must derive its verdict from the supplied
document and context alone.

## Result envelope

The harness writes exactly one JSON object to stdout:

Accept:

```json
{
  "harness_version": "1",
  "protocol_version": "0.2",
  "request_id": "<same opaque id>",
  "verdict": "accept"
}
```

Reject:

```json
{
  "harness_version": "1",
  "protocol_version": "0.2",
  "request_id": "<same opaque id>",
  "verdict": "reject",
  "category": "signature.invalid"
}
```

## Result invariants

- `verdict` is only `"accept"` or `"reject"`.
- `"accept"` MUST NOT contain `category`.
- `"reject"` MUST contain exactly one `category`.
- A reject `category` MUST be one of the exact thirteen stable categories:

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

- Result `request_id` must equal request `request_id`.
- Request/result `harness_version` and `protocol_version` must agree.
- No implementation-specific exception names or internal error strings are
  part of the contract. If a reference implementation produces an internal
  rejection code outside the thirteen, the harness reports an operational
  failure; it never silently remaps an internal code into a stable category.

## Exit-code semantics

- **Exit 0:** the harness successfully processed the request and produced one
  syntactically and semantically valid result JSON. A protocol rejection
  still exits 0 (`verdict:"reject"` with a valid category is a successful
  harness execution).
- **Exit non-zero:** operational/harness failure only (malformed request;
  unsupported harness/protocol version; invalid base64; internal crash;
  inability to process the invocation as a harness operation). A non-zero
  exit MUST NOT be interpreted as a FAW protocol rejection.

## stdout / stderr

- On exit 0: stdout is exactly one JSON result object plus an optional final
  newline; no logging or banners on stdout.
- stderr: free-form diagnostics allowed; never used to determine the
  protocol verdict.
- On non-zero exit: the runner records the exit code and stderr as
  operational-failure evidence.

## Harness command safety

The runner executes the harness without a shell (`shell=False`); the
`--harness` command line is parsed with `shlex.split()` (POSIX quoting only
— no shell evaluation, no pipes/redirection/environment expansion) into an
argv list. Operators needing complex invocations must wrap them in a script
file and pass the script.

## Runner (reference)

`faw conformance run --harness "<cmd>"` (with optional `--timeout` and
`--manifest`) drives any conforming harness over P01–P05 and N01–N15 from
the committed `conformance/v0.2/manifest.json` (runner-side truth). For each
fixture the runner verifies byte identity of the referenced fixture document
AND every referenced trust-chain file (SHA-256 and size from the manifest's
`files` map; any missing/unreadable/mismatched reference is a HARNESS
OPERATIONAL FAILURE and the harness is never invoked), builds a non-leaking
inline request with an opaque `request_id`, invokes the harness, validates
the result envelope and invariants, and compares the verdict/category
against the manifest expectation. Failures are distinguished as
`CONFORMANCE FAILURE` (valid result contradicting the manifest) vs
`HARNESS OPERATIONAL FAILURE` (malformed result, invariant violation,
timeout, non-zero exit, invalid envelope, unverified byte context). Expected
answer data stays runner-side only.

## Reference harness

`python -m federated_agent_web.harness` is Harness Implementation #1: a thin
adapter over the existing FAW reference verification machinery
(`verify`, `PinnedManifestTrustContext`, `VerificationPolicy`), reading one
request from stdin and writing one result to stdout. It contains no
duplicate verifier logic and never inspects expected outcomes or reads
conformance fixtures.

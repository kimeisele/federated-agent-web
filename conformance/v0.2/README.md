# FAW v0.2 conformance vector package (language-neutral)

Positive/negative fixtures implementing the planned matrix of the merged
interoperability profile (N01–N15, P01–P05). Consumable from `manifest.json`
and the raw fixture bytes alone; no Python sources or tests are required.

## Verification procedure per fixture

1. read the exact bytes of `bytes`;
2. parse strictly (JCS rules: duplicate members, number domain, UTF-8/lone surrogates);
3. validate against the schema for `expected_kind`;
4. for delegations: audience binding, then temporal admission;
5. validate the ordered `trust_chain` manifests; resolve the issuer kid;
6. check trust-context freshness;
7. verify the Ed25519 signature over JCS(document minus signature);
8. receipts: bind against the semantic `pending` context;
9. delegations: replay/authority/budget admission (local policy).

`expect` is `accept` (category null) or `reject` (exact `expected_category`).
`now`, `pinned_at`, `local_node_id`, `local_policy`, and `pending` are
verification inputs recorded per fixture.

## Trust freshness (language-neutral)

The ordered `trust_chain` array supplies the pinned manifests. Head = the
last manifest; `head_sequence` = `head.body.manifest_sequence`; `head_digest`
= `sha256:<hex>` of JCS(head without the top-level signature). Freshness is
Freshness rule: `fresh` iff `pinned_at` +
`head.body.manifest_freshness_window_seconds` >= `now`; `stale` iff `<`
`now`. The equality boundary (`pinned_at + window == now`) classifies as
fresh, matching the reference implementation (`stale iff pinned_at +
window < now`). `pinned_at` is explicit per fixture and never chosen by
the consumer. A stale context may be rejected or returned qualified by
local policy, never silently accepted.

## Local policy (language-neutral)

`local_policy` encodes every admission-policy input required to reproduce
verification: `clock_skew_seconds`, `reject_stale`, `can_enforce_tokens`,
`can_enforce_cost`, `allowed_external_effects`, `allowed_actions`,
`capability_targets`, `max_wall_seconds_cap`, `max_output_bytes_cap`.
Consumers construct their policy from these values alone; no implicit
defaults are assumed.

Byte identity is machine-checkable via `files[].sha256` and `size_bytes`.
All keys are TEST-ONLY (`context/test-only-keys.json`); they grant no
authority and are never used outside this package.

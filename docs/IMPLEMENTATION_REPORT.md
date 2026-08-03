# Implementation Report — FAW v0.2 reference implementation

Status: **complete** (all §17 definition-of-done items met except the
independent-review item, see §7 below). Build date: 2026-08-03.

Remote: `https://github.com/kimeisele/federated-agent-web` (public, `main`)
Pushed at build time: `8545e10b23012e68fec6c0e730c45330fcea957e`

Publication history (all on `main`, in order):

| Commit | Content |
|---|---|
| `303d315ba9563a03a277ed018f3e00eeb52e7fe1` | Core, schemas, CLI, demo, vectors, tests (initial publish) |
| `8545e10b23012e68fec6c0e730c45330fcea957e` | Implementation report; executor filesystem-scope + concrete-target registration hardening |
| `abcb4c88c96cb3c4d3e1451d8ddb2407bc4fd5a1` | Normative schemas bundled into wheels |

## 1. Files created

```text
MANIFESTO.md                       imported verbatim from docs/MANIFESTO.md (Draft 3)
README.md, SPEC.md, SECURITY.md, CONFORMANCE.md, skill.md
pyproject.toml, requirements.txt, .gitignore
schemas/node-manifest.schema.json  normative, closed, kind const, spec_version enum ["0.2"]
schemas/delegation.schema.json     same
schemas/receipt.schema.json        same
src/federated_agent_web/
  __init__.py  canonical.py  crypto.py  identity.py  documents.py
  verify.py    replay.py     pending.py demo.py       cli.py
  transports/__init__.py transports/base.py transports/filesystem.py
examples/run_demo.py  examples/node_a/README.md  examples/node_b/README.md
vectors/  (canonicalization/ signatures/ delegations/ receipts/ + README.md)
tools/gen_vectors.py
tests/ conftest.py + test_canonical.py test_schemas.py test_signatures.py
       test_delegation.py test_transport.py test_vectors.py
docs/federated-agent-web-build-spec-v0.2.md (working copy of the governing spec)
docs/REUSE_REPORT.md  docs/IMPLEMENTATION_REPORT.md  docs/ADAPTER_NADI.md
```

## 2. Dependencies and rationale

| Dependency | Version (locked) | Rationale |
|---|---|---|
| `rfc8785` | 0.1.4 | RFC 8785 JCS canonicalization. The spec mandates a maintained JCS implementation, not a hand-rolled serializer. Locked exactly; `json.dumps(sort_keys=True)` is not a substitute. |
| `cryptography` | 46.0.6 | Ed25519 sign/verify (same library the existing federation work uses). |
| `jsonschema` | 4.26.0 | Normative schema validation (`Draft202012Validator`). |
| `pytest` | 8.0.0 | Test runner (dev). |

No services, databases, containers, or web frameworks. The only runtime
"storage" is the filesystem (append-only dirs + atomic rename) for the
reference transports/stores.

## 3. Test commands and results

```bash
pip install -r requirements.txt && pip install -e .
python -m pytest -q
```

Result (all offline, deterministic, no network):

```text
119 passed in 1.43s
```

Coverage map is in `CONFORMANCE.md`; every §13 requirement group has
executable tests (schemas/kinds/identifiers/paths, canonicalization,
signatures and manifest trust, delegation and receipt, transport and state,
golden vectors). Golden vectors are reproducible from static fixtures without
importing the package (`vectors/README.md`).

## 4. Offline demo transcript

`faw demo` (see `examples/run_demo.py`), exit code 0:

```text
manifest: two genesis manifests generated and verified
delegation: sha256:57aa55ddf6aefa85fdc757218c8222dd604a14d248c88570b78f3c0784f84e9d
admission: attempt 6b7dcd6b-e878-4be6-8a00-9600efe9113d admitted at urn:faw:b9c43f44bbb6f4bd
task_id:        558437cf-3677-47ad-8de3-93b76fdf5a0e
attempt_id:     6b7dcd6b-e878-4be6-8a00-9600efe9113d
delegation_digest: sha256:57aa55ddf6aefa85fdc757218c8222dd604a14d248c88570b78f3c0784f84e9d
receipt_digest: sha256:21cfc27c91a6a0e2c8ef81f1016790e0294debf35432d1852028ddf8da1e1aed
artifact_digest: sha256:3f669ad85867ccdb0d96920ff44bfd4b1490a022da206dbae843a210ed49a895
receipt status: succeeded
demo: OK
```

The artifact digest is `sha256:<hex>` over the exact raw artifact bytes as
defined in §10; the demo asserts this against the artifact file.

## 5. Security invariants enforced

See `SECURITY.md`. Summary: strict JCS parsing (duplicates, NaN/Infinity,
negative zero, out-of-domain integers, lone surrogates rejected at parse);
closed schemas; ASCII identifier grammar; pinned, time-bound key resolution
without any `signature.kid`; authoritative fail-closed §7.3 order (audience
and temporal admission precede key resolution; signature after resolution;
replay and authority before admission); at-most-once admission via atomic
O_EXCL replay records; issuer-side receipt binding with atomic terminal
close; unenforceable authority/budget rejected; executor enforces
`filesystem_scope` and the deadline; transport provenance is evidence, not
trust.

## 6. Known limitations

- **Revocation is best-effort** against locally pinned manifest state, within
  `manifest_freshness_window_seconds`; there is no online revocation network.
- **No sandboxing of capability handlers**: the shipped `hash_file`
  capability is deterministic and external-effect-free; a production node
  must add its own sandboxing.
- **Single-process concurrency**: replay/pending stores use O_EXCL create and
  an advisory lock for receipt acceptance; heavy multi-process concurrency on
  one store is untested.
- **Retention is the operator's job**: replay records should be retained
  until `expires_at` + clock skew; the reference stores do not garbage-collect.
- **Demo executor** checks input digest and declared filesystem scope but is
  not a general sandbox.
- **No A2A server, no HTTP service, no central registry, no governance, no
  token/economic system** — all explicitly out of scope for v0.2.
- Claimed properties are exactly those with acceptance tests; no stronger
  claims ("secure", "production-ready", "decentralized", "interoperable") are
  made.

## 7. Independent review

An automated independent review was attempted three times during the build
(two `reviewer` agents and a `scout` agent, the final attempt on a
"deepseek pro" reviewer configuration) and every attempt was blocked by the
provider's monthly usage limit (`429 GoUsageLimitError`, resets
~2026-08-04 06:00 UTC). No alternative model backend was available either.
Instead, a manual adversarial review pass was performed over the §18
categories. Two findings were found and fixed before publication:

1. The demo executor did not enforce the delegation's declared
   `filesystem_scope`; it now rejects inputs outside the declared read paths
   (test: `test_executor_rejects_input_outside_filesystem_scope`).
2. `PendingDelegationStore.register_outstanding` accepted
   capability-addressed delegations without a resolved concrete target,
   contrary to §9; it now requires `target_node_id` before registration
   (test: `test_capability_addressed_registration_requires_concrete_target`).

**The §17 "independent reviewer finds no unresolved critical issue"
criterion is NOT yet formally satisfied.** A fresh independent review should
run against the published commit `8545e10` before the project is promoted
beyond "laboratory prototype" status. This is the only open §17 item.

## 8. Reused sources

Every inspected source and its disposition is documented in
`docs/REUSE_REPORT.md`: `kimeisele/steward-federation` (nadi-kit mechanics
retained as patterns: Ed25519 via `cryptography`, atomic tmp+rename writes,
TTL/correlation concepts; its sort_keys canonicalization, key-derived node
identity, hub coupling, whole-outbox clear, and unverified dispatch
rejected), `kimeisele/agent-template` (setup-flow pattern only; legacy
descriptors explicitly not aliased to the FAW path), `kimeisele/agent-internet`
(ADR 0002 as role division), `kimeisele/federation-map` (evidence of relay
backlog; not a dependency). No file was copied wholesale; the core is a fresh
implementation.

## 9. Divergences from current nadi-kit

| nadi-kit | FAW v0.2 |
|---|---|
| `json.dumps(sort_keys=True)` canonicalization | RFC 8785 JCS via `rfc8785` with strict parsing |
| node id derived from public key | stable `urn:faw:` id, key rotation independent of identity |
| hub-coupled GitHub mailbox transport | transport-agnostic contract; offline filesystem adapter; `nadi_compat` specified, not implemented |
| whole-outbox clear on partial push | per-message ack; partial failure preserves unacknowledged messages |
| inbound dispatch without core signature gate | authoritative 11-step verification before any handler |
| no receipt binding state | issuer-side pending-delegation store with atomic terminal close |
| no replay protection | `(issuer_node_id, attempt_id)` replay store, digest-compared, at-most-once admission |

## 10. Phase B (publication)

- Authorized: yes — the invoking task explicitly authorized push/creation
  ("i authorize the push etc") on 2026-08-03; scope recorded here.
- Repository created: `kimeisele/federated-agent-web` (public).
- Branch pushed: `main` (direct default-branch initialization; no draft PR).
- Remote URL: `https://github.com/kimeisele/federated-agent-web`
- Pushed commit: `8545e10b23012e68fec6c0e730c45330fcea957e`
- Moltbook outreach: **not performed**; a separate bounded task is required
  before any pilot recruitment.

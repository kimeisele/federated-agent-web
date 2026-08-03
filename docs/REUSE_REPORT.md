# Reuse Report

This report names every source file inspected from the existing federation
repositories and states what was retained, changed, or rejected. The v0.2
core was written fresh against the build specification; the existing
repositories were inspected on their default branches (shallow clones at
build time) and used as *compatibility and mechanics references*, not as
copy targets.

## Inspected repositories

| Repository | Inspected on | Key files |
|---|---|---|
| `kimeisele/steward-federation` | `main` | `nadi_kit.py` (978 lines), `pyproject.toml` |
| `kimeisele/agent-template` | default branch | `scripts/setup_node.py`, `.well-known/agent-federation.json`, `.well-known/agent.json`, `.github/workflows/heartbeat.yml` |
| `kimeisele/agent-internet` | default branch | `docs/adrs/0002-commons-shell-not-second-substrate.md` |
| `kimeisele/federation-map` | default branch | live map (evidence of GitHub-backed relay backlog) |

## steward-federation (`nadi_kit.py`)

### Retained (as patterns, not code)

| Mechanism | Where reused in FAW core | Divergence |
|---|---|---|
| Ed25519 via the `cryptography` package | `src/federated_agent_web/crypto.py` | Same underlying library; FAW signs JCS canonical bytes directly, not a hex digest string. |
| Atomic mailbox writes (temp file + `os.replace`) | `transports/filesystem.py`, `replay.py`, `pending.py` | Same atomic-rename discipline, per-message instead of per-file-batch. |
| TTL / correlation identifiers | `expires_at`/`deadline`/`attempt_id` | Reinterpreted as the admission/execution boundary split (§9). |

### Rejected, with reasons

| nadi-kit feature | Reason for rejection |
|---|---|
| `_derive_node_id(public_key)` (node identity from the active key) | §6 requires a stable `node_id` never derived from the active key; rotation must not change identity. FAW uses `urn:faw:` identifiers assigned out of band. |
| `_sign_message` using `json.dumps(canonical, sort_keys=True)` | Not RFC 8785 JCS (§7.1 explicitly forbids it as a substitute). FAW uses the maintained `rfc8785` package over a strict parser. |
| `HUB_REPO` coupling + `NadiHubRelay` GitHub API mailbox | Transport is coupled to `kimeisele/steward-federation`; the v0.2 core is transport-agnostic and offline. The GitHub relay can accumulate a backlog (evidenced by `federation-map`) and is not a production bus. |
| `clear_outbox()` (whole-outbox clear) | §11 requires per-message acknowledgements; partial delivery failure must preserve every unacknowledged message. |
| Inbound dispatch without a core-level signature gate | §7.3 makes verification order authoritative; no handler runs before steps 1–9 pass. |
| `nadi_kit.py` as a single vendored module | The FAW core is a small package with separated concerns (canonical/crypto/identity/documents/verify/stores/transports). |
| Mahamantra/Nadi terminology in the normative core | §2: the normative core uses neutral names; legacy terms appear only in adapter docs. |

## agent-template

### Retained (as patterns)

- Setup flow structure — identity generation before connectivity — is
  reflected in `faw manifest init` (non-interactive, no governance wiring).
- Status/descriptor generation patterns inform the CLI's manifest commands.

### Rejected, with reasons

- `scripts/setup_node.py` itself (interactive wizard, governance checks,
  Agent City phase-2 connectivity): out of v0.2 scope; onboarding and
  governance are not part of the delegation contract.
- The legacy descriptors `.well-known/agent-federation.json` and
  `.well-known/agent.json`: §8/P3 — neither is the FAW node manifest. They
  are never renamed, aliased, or redirected to the reserved FAW path
  `/.well-known/faw-node.json`; `/.well-known/agent-card.json` remains
  reserved for a genuine A2A Agent Card projection.
- `.github/workflows/heartbeat.yml` (GitHub-specific liveness): the v0.2 core
  is forge-agnostic; no CI workflow is part of the core.

## agent-internet

- ADR 0002 ("commons shell, not a second substrate") is accepted as the
  role-division basis: `agent-internet` keeps discovery/commons/routing
  surfaces; the FAW repository is the neutral contract, not a message bus.
- No code copied.

## federation-map

- Used as evidence only: the live map shows the lab communicates, and that a
  GitHub-backed relay accumulates backlog. The FAW core therefore defines
  per-message durability and at-most-once admission instead of relying on a
  hub mailbox.
- No code copied.

## New files (not derived from any inspected source)

`MANIFESTO.md` (imported verbatim from `docs/MANIFESTO.md`), the three
normative schemas, `canonical.py`, `crypto.py`, `identity.py`,
`documents.py`, `verify.py` (the §7.3 procedure), `replay.py`, `pending.py`,
`transports/`, `cli.py`, `demo.py`, `examples/`, `tests/`, `tools/`,
`vectors/`, and the documentation set — all written fresh against the build
specification.

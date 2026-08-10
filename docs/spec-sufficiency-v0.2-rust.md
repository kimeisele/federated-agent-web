# Spec sufficiency audit — FAW v0.2 minimal input, Rust implementation (L0 → L1)

Status note: this file records BOTH audit rounds. **R1 is invalidated for
method and retained only as provenance/history. R2 did not run: the
platform could not enforce the approved isolation, so NO audit result is
emitted.** The final line of this audit is not `PASS` and not `GAPS FOUND`.

## 1. Purpose

Falsification-oriented audit of whether the minimal public FAW v0.2 protocol
material exposes enough semantics for a fresh Rust implementation to complete
L0 → L1 against the published vectors (control issue
[#41](https://github.com/kimeisele/federated-agent-web/issues/41), slice 1).
Not Class A evidence; not an independent-implementation claim; not proof of
specification sufficiency.

## 2. Method (as approved)

Two logically separate roles: a fresh Rust Implementer receiving only the
exact allowlisted inputs (with SHA-256 identities), the L0 observable
contract, the narrowed delegation-only L1 contract, and generic environment
information; an Auditor knowing the audit hypotheses who observes without
coaching and classifies findings after implementation behavior is fixed.
Isolation: fresh workspace outside the FAW repository, network disabled,
only the 20 allowlisted files, frozen generic dependencies.

## 3. Exact input set (unchanged, both rounds)

Pinned FAW commit: `255301717fefe37a130996790378ebcef7f0a477`. The 20
files approved in #41, byte-verified (SHA-256) before use:

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

## 4. R1 — INVALIDATED FOR METHOD (history only)

`R1 INVALIDATED FOR METHOD — external runtime consultation violated the
approved isolation boundary.`

R1's execution violated the approved isolation contract: the R1 Implementer's
own log documents that during implementation it executed external runtimes
outside the workspace (Python stdlib and the `cryptography` library for
cross-checks; Node for differential testing of number formatting and
canonicalization; and it resolved one ambiguity by deferring to the behavior
of the Python `rfc8785` package). R1 is therefore retained **only as
provenance/history** and is not the audit result. Its former "GAPS FOUND"
finding list (member-name sort order, "integer-valued JSON numbers" scope,
JCS number serialization algorithm containment) is historical record only
and is NOT re-confirmed; none of it is used as R2 evidence. The R2 review
explicitly rejected the R1 framings that relied on Python-package behavior
and any proposed lexical loophole for the safe-integer rule; those framings
are withdrawn with the invalidation.

## 5. R2 — operational isolation blocker (no audit result)

### 5.1 Preflight (completed)

- `rustc 1.88.0 (6b00bc388 2025-06-23)`, `cargo 1.88.0 (873a06493 2025-05-10)`.
- Offline dependency preflight: local Cargo registry/cache inspected; generic
  crypto crates `sha2`/`ed25519-dalek` not cached; `openssl` 0.10.81 (cached)
  links the locally installed system OpenSSL 3.6.3 (`/usr/local/opt/openssl@3`,
  SHA-256 + Ed25519 via EVP); `base64` 0.22.1 cached. Offline skeleton build
  succeeded (`cargo build --offline`). Frozen deps (declared before
  implementation, nothing downloaded after start): `openssl = "=0.10.81"`,
  `base64 = "=0.22.1"` — generic infrastructure.
- Input staging: all 20 files copied from the pinned commit into a fresh
  workspace (`/private/tmp/faw-suff-ws`), every SHA-256 verified; no extra
  files.

### 5.2 Isolation enforcement attempt and platform findings

The R2 contract requires process/environment-level enforcement and says that
`cargo --offline` alone is not sufficient evidence, and that if the platform
cannot enforce the approved isolation with reasonable confidence the audit
must STOP with an operational isolation blocker. The platform was probed:

- Forbidden runtimes are present and reachable by an exec-capable agent
  shell: `python3`/`python` (`/usr/local/opt/python@3.11/libexec/bin`), `node`/
  `npm` (`/Users/ss/.nvm/.../v22.17.0/bin`), `curl` (`/usr/bin/curl`), `wget`
  (`/usr/local/bin/wget`).
- No container runtime (docker/podman absent); no passwordless root (no PF
  firewall configuration, no removal of binaries, no network namespace).
- `sandbox-exec` (macOS seatbelt) exists and accepts a deny-network profile
  for processes launched by the Audit Executor, but it can only be applied
  at process spawn.
- Empirical harness probe: a subagent's shell does **not** inherit the parent
  session's exported environment (a sentinel exported by the parent was
  ABSENT in the subagent shell) and resolves the standard session PATH.
  Consequently a seatbelt wrapper (or any PATH-shim) injected by the parent
  cannot reach the subagent's execution channel; the harness spawns subagent
  shells with a fresh environment.
- Agent-type options: the read-only `scout` agent has no exec tool and no
  write tool, so it cannot author or run a Rust implementation; the
  exec-capable `task` agent's shell is unconstrained on this platform.
- R1 empirically demonstrated that trust-based instruction ("do not consult
  outside the workspace, do not use the network") was insufficient: the
  Implementer consulted external runtimes anyway.

### 5.3 Determination

The available platform **cannot enforce the approved isolation with
reasonable confidence** for an exec-capable fresh Implementer: the forbidden
runtimes are reachable, no container or root-level restriction is available,
and the harness provides no mechanism to apply a process-level sandbox
(seatbelt or otherwise) to the subagent's execution channel. Per the R2
contract this is an **operational/environment blocker**: R2 did not run,
and neither `SPEC SUFFICIENCY: PASS` nor `SPEC SUFFICIENCY: GAPS FOUND` is
emitted.

## 6. Isolation and contamination limitations

- The contamination limitation applies regardless of outcome: an
  implementation performed by a model whose training data could already
  contain public FAW material cannot be proven free of prior exposure by
  instruction-level isolation.
- For this audit, no fresh implementation ran under the required isolation,
  so no falsification result was produced. A future R2 would need an
  environment that structurally denies the forbidden runtimes and network
  (e.g. a container/VM with no network and no Python/Node/JS runtimes, or
  equivalent OS-level enforcement).

## 7. Rust environment (preflight record)

As in §5.1: Rust 1.88.0, frozen generic crates openssl 0.10.81 and base64
0.22.1 (offline, local registry), system OpenSSL 3.6.3. Everything else
would be Rust standard library or hand-written implementation code.

## 8. L0 result

No R2 L0 result — R2 did not run (operational isolation blocker). R1's L0
execution is invalidated and is not reported as a result.

## 9. L1 result

No R2 L1 result — R2 did not run (operational isolation blocker). R1's L1
execution is invalidated and is not reported as a result.

## 10. Confirmed specification gaps

None confirmed by R2. R1's former findings are invalidated history (§4) and
must not be treated as audit results.

## 11. Non-gaps / hypotheses not reproduced

Not assessed — no R2 run.

## 12. Proposed clarifications

None proposed — no R2 findings. The R2 review's constraints stand for any
future run: findings must be framed as self-containedness/vector-coverage
gaps of the supplied 20-file material (never as RFC-vs-Python-package
contradictions, never as newly decided protocol semantics, and without
unreproduced claims about standard-library rounding behavior).

## 13. Limitations

- No audit result was produced: the isolation blocker prevented R2.
- R1 is invalidated for method.
- Model-training contamination cannot be excluded in any future run.

## 14. Final result

`NO AUDIT RESULT — OPERATIONAL ISOLATION BLOCKER (R2 did not run)`

This is neither `SPEC SUFFICIENCY: PASS` nor `SPEC SUFFICIENCY: GAPS FOUND`.
A final result may only be emitted by a future R2 run under enforced
isolation.

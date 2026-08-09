# v0.5 clean-room protocol

This document fixes the exact allowed and forbidden inputs for the second
implementation and the machine checks that enforce them. It is the contract
behind `interop/v0.2/INPUT_MANIFEST.json` and
`scripts/build_v0_5_implementer_kit.py`.

The frozen reference-material commit is:

```text
2d3edbc49192fd5910389c17c1653d0913fa6434
```

It identifies the commit from which the normative specification, schemas,
security guidance, the interoperability profile, and the Golden Vector and
conformance-package material were selected. It does not claim that every
kit-administration file existed at that commit.

## 1. Purpose

A second implementation is only credible as clean-room evidence if its
authors received a bounded, reproducible, hash-verified input set and no
implementation details from the reference repository. This protocol fixes
that input set, forbids everything else, and makes the boundary
machine-checkable so the claim does not depend on memory or goodwill.

## 2. Implementer kit allowlist

The kit may contain only these paths:

```text
SPEC.md
docs/federated-agent-web-build-spec-v0.2.md
docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md
docs/V0_5_INTEROPERABILITY_PLAN.md
schemas/**
conformance/v0.2/**
vectors/**
SECURITY.md
LICENSE
docs/V0_5_IMPLEMENTER_BRIEF.md
interop/v0.2/INPUT_MANIFEST.json
```

`LICENSE` is a required kit member: the repository publishes the MIT
License (Copyright (c) 2026 Kim Eisele) at `LICENSE`, and the manifest lists
it. Omission of `LICENSE` from the manifest or the repository is a build
failure; the builder never silently skips it. License terms are never read
from packaging metadata during kit construction.

## 3. Classification of allowed inputs

| Path | Classification | Reason |
|---|---|---|
| `docs/federated-agent-web-build-spec-v0.2.md` | normative (governing) | source of normative requirements; where it conflicts with any summary, it wins |
| `docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md` | normative (cross-language) | normative for v0.5 cross-language conformance only; clarifies previously unspecified v0.2 boundaries without changing wire format, schemas, protocol version, signature input, or transport |
| `SPEC.md` | normative summary | condensed contract; conflicts lose to the governing specification |
| `schemas/**` | normative (machine-readable) | JSON Schema constraints for the three normative document kinds |
| `conformance/v0.2/**` | conformance fixtures | language-neutral positive/negative vector package (N01–N15, P01–P05) with per-fixture records, `pinned_at`/freshness rule, language-neutral `local_policy`, and semantic pending context; byte identity machine-checkable via its own `manifest.json` |
| `vectors/**` | conformance fixtures | static golden data: canonical bytes, digests, key material, signed documents |
| `LICENSE` | license | terms governing copying, modification and redistribution of the kit materials |
| `SECURITY.md` | non-normative guidance | disclosure and hygiene policy; no normative document semantics |
| `docs/V0_5_IMPLEMENTER_BRIEF.md` | non-normative guidance | scope, verification order, CLI and result-format contract |
| `docs/V0_5_INTEROPERABILITY_PLAN.md` | non-normative guidance (required) | interoperability/evidence plan: the four-layer evidence model and the Layer 3 repository-boundary split; a required kit input — omission fails the build |
| `interop/v0.2/INPUT_MANIFEST.json` | kit manifest | self-describing hash manifest; its own digest is defined externally by the build output |

The documented finite classification set used by the manifest is:
`normative`, `normative-summary`, `conformance-fixture`,
`non-normative-guidance`, `license`. The builder rejects any other value.

## 4. Forbidden inputs

The kit MUST NOT contain:

```text
src/**
tests/**
examples/**
.github/**
scripts/**
pyproject.toml
docs/IMPLEMENTATION_REPORT.md
docs/REUSE_REPORT.md
docs/TRACEABILITY_V0_2.json
docs/TRANSPORT_CONFORMANCE.md
docs/NADI_LIVE_REHEARSAL.md
docs/ADAPTER_NADI.md
```

It must also exclude, by construction:

- Python filenames (`.py`, `.pyc`, `.pyo`);
- Python bytecode and `__pycache__`;
- coverage output (`.coverage`, `coverage.xml`, HTML reports);
- test node IDs and any pytest/node inventory;
- reference rejection-code implementation details;
- reference package imports or module paths;
- Git history and `.git/`;
- local absolute or relative paths (including `..` traversal);
- credentials of any kind;
- private keys other than the two sanctioned TEST-ONLY public fixture
  files, exactly:
  - `vectors/signatures/keypair.json` (existing);
  - `conformance/v0.2/context/test-only-keys.json` (added with the
    conformance package).

Both sanctioned files are public TEST-ONLY reproducibility fixtures only:
they grant no authority, are never deployment identities, and are never
production credentials. They are published so an independent implementer
can reproduce signatures and conformance fixtures deterministically. All
other private/deployment key material remains forbidden; the machine checks
prove the allowlist is exactly these two files and that no other kit member
containing private key material is accepted.

## 5. Forbidden implementation paths

The future second implementation must not live inside
`federated-agent-web`, not under `implementations/go/`, `go/`, or
`second-implementation/`. See ADR 0002.

## 6. Machine enforcement

`scripts/build_v0_5_implementer_kit.py` enforces:

- manifest structure is valid (required members present, unknown members
  rejected, exact reference-material commit and archive name, normalized
  unique forbidden prefixes, exact file-entry shape, documented
  classification set);
- every listed file is allowlisted;
- every listed file is a regular non-symlink file;
- every listed file matches its exact byte size and SHA-256;
- every existing file under `conformance/v0.2/`, `schemas/` and `vectors/`
  is listed, including `conformance/v0.2/manifest.json` (the package
  manifest's inability to hash itself applies only to its own internal
  `files` map);
- every required fixed file, including `LICENSE`, is listed;
- only listed files plus the manifest enter the archive;
- the completed archive is scanned again for unsafe or forbidden members;
- deterministic archive construction: sorted members, normalized POSIX
  separators, uid/gid zero, empty owner/group names, fixed permissions,
  mtime zero, deterministic gzip header (mtime zero);
- no network operation and no subprocess.

The build is content-hermetic with respect to the allowlist and the
byte-exact manifest. Files outside the allowlist do not affect or enter the
archive. The builder does not claim to prove that the complete Git worktree
is clean; it resolves and reports the current Git `HEAD` as provenance and
fails if `HEAD` cannot be determined.

`tests/test_v0_5_implementer_kit.py` proves each of these checks, that two
builds are byte-identical, that archive extraction cannot escape the
destination directory, and that non-allowlisted contamination never enters
the archive.

## 6a. Cross-language contract and Layer 3 independence

The settled cross-language contract is the frozen interoperability profile
(`docs/FAW_V0_2_INTEROPERABILITY_PROFILE.md`) plus the committed
`conformance/v0.2/**` package (N01–N15 / P01–P05 with per-fixture records).
The profile's historical phrasing ("planned matrix", "fixtures not yet
created", "future fixture package") describes the repository status at the
time that frozen profile was authored; it is NOT an instruction to create
another vector package. The matrix semantics remain normative; the committed
realization is `conformance/v0.2/**`, and no implementer may infer that
negative fixtures are still missing.

The exact thirteen stable semantic rejection categories are fixed by the
profile and used consistently across the kit; they are not chosen by the
second implementation.

The second implementation MUST NOT import, vendor, clone, inspect, invoke,
or depend on the Python reference implementation or its tests. Layer 3 is a
post-build bidirectional interoperability evaluation across the repository
boundary: the Go side deterministically emits TEST-ONLY fixtures and records
bytes/digests/evidence; a separate reference-side evaluator/operator
verifies those fixtures with the Python reference post-build, and that
result is recorded as interoperability evidence. `faw-verifier-go
conformance-report` runs without Python: Layers 1, 2 and 4 from the kit,
Layer 3 emitter/evidence from Go, and the Layer 3 reference-verification
result as externally supplied/post-build evidence or clearly marked
pending. The v0.5 gate still requires that reference verification to pass
before bidirectional interoperability may be claimed complete. No delivered
clean-room instruction directs the second implementation to access or
execute the Python reference repository.

## 7. Provenance and kit digest

The archive is named:

```text
faw-v0.2-implementer-kit.tar.gz
```

Every build reports four-part provenance; the printed build output is the
provenance attestation:

```text
reference_material_commit   the frozen FAW material commit
build_head_sha              the repository state the kit was assembled from
manifest_sha256             the exact external manifest bytes
archive_sha256              the complete delivered archive
```

The manifest cannot hash itself recursively: `manifest_sha256` and
`archive_sha256` are defined externally by the build output, and the
`manifest_sha256` value is the authoritative external identity of the
manifest bytes.

## 8. Handling of discovered deviations

- Any normative contradiction found during implementation becomes a spec
  erratum issue; ambiguous-but-not-contradictory questions are listed in the
  implementer brief's ambiguity inventory.
- No deviation is resolved by reading Python source and copying behavior.

# v0.5 clean-room protocol

This document fixes the exact allowed and forbidden inputs for the second
implementation and the machine checks that enforce them. It is the contract
behind `interop/v0.2/INPUT_MANIFEST.json` and
`scripts/build_v0_5_implementer_kit.py`.

The recorded source commit of the reference material is:

```text
bb85221c894473adfd17dceb2c7d3685d9e266ea
```

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
schemas/**
vectors/**
SECURITY.md
LICENSE
docs/V0_5_IMPLEMENTER_BRIEF.md
interop/v0.2/INPUT_MANIFEST.json
```

`LICENSE` is reserved in this allowlist. The repository at the recorded
source commit ships no license file; `pyproject.toml` declares MIT metadata.
Until a license file is published, the kit omits `LICENSE` and this absence
is recorded as a candidate erratum. The manifest does not list it.

## 3. Classification of allowed inputs

| Path | Classification | Reason |
|---|---|---|
| `docs/federated-agent-web-build-spec-v0.2.md` | normative (governing) | source of normative requirements; where it conflicts with any summary, it wins |
| `SPEC.md` | normative summary | condensed contract; conflicts lose to the governing specification |
| `schemas/**` | normative (machine-readable) | JSON Schema constraints for the three normative document kinds |
| `vectors/**` | conformance fixtures | static golden data: canonical bytes, digests, key material, signed documents |
| `SECURITY.md` | non-normative guidance | disclosure and hygiene policy; no normative document semantics |
| `docs/V0_5_IMPLEMENTER_BRIEF.md` | non-normative guidance | scope, verification order, CLI and result-format contract |
| `interop/v0.2/INPUT_MANIFEST.json` | kit manifest | self-describing hash manifest; its own digest is defined externally by the build output |

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
- private keys other than fixtures explicitly labelled TEST-ONLY in the
  published Golden Vectors.

The TEST-ONLY vector key material (`vectors/signatures/keypair.json` and the
manifest key entries under `vectors/delegations/` and `vectors/receipts/`)
is public fixture material. It is published so an independent implementer can
reproduce signatures. It is not a deployment identity, grants no authority,
and must never be used outside tests.

## 5. Forbidden implementation paths

The future second implementation must not live inside
`federated-agent-web`, not under `implementations/go/`, `go/`, or
`second-implementation/`. See ADR 0002.

## 6. Machine enforcement

`scripts/build_v0_5_implementer_kit.py` enforces:

- execution from a clean repository whose allowlisted content matches the
  manifest byte-for-byte at the recorded source commit;
- manifest structural validity: sorted unique paths, no traversal, no
  absolute paths, no symlinks, no file outside the allowlist, no
  forbidden prefix;
- exact byte size and SHA-256 for every listed file;
- deterministic archive construction: sorted members, normalized POSIX
  separators, uid/gid zero, empty owner/group names, fixed permissions,
  mtime zero, deterministic gzip header (mtime zero);
- a final scan of the produced archive for forbidden prefixes and Python
  filenames;
- no network operation.

`tests/test_v0_5_implementer_kit.py` proves each of these checks, that two
builds are byte-identical, and that archive extraction cannot escape the
destination directory.

## 7. Kit digest

The archive is named:

```text
faw-v0.2-implementer-kit.tar.gz
```

Its SHA-256 is not stored inside the manifest (the manifest cannot hash
itself recursively). It is defined externally: the build command prints the
archive digest, and that printed value is the authoritative kit digest to
record in the future implementation report.

## 8. Handling of discovered deviations

- The missing `LICENSE` file is a recorded deviation from the allowlist
  draft and a candidate erratum.
- Any normative contradiction found during implementation becomes a spec
  erratum issue; ambiguous-but-not-contradictory questions are listed in the
  implementer brief's ambiguity inventory.
- No deviation is resolved by reading Python source and copying behavior.

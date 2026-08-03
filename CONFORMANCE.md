# Conformance

The build specification (v0.2, §13) requires executable tests for every
normative MUST. This file maps the requirement groups to their tests and
documents the claims the suite actually proves. Everything runs offline.

## Running the suite

```bash
pip install -e . && python -m pip install --group test
python -m pytest -q
```

All tests are offline and deterministic; no network access is used. Ephemeral
keys live only in test fixtures and `vectors/` and are clearly marked
TEST-ONLY.

## Coverage map

| §13 requirement group | Test module | Notes |
|---|---|---|
| Schema, kind, identifiers, paths | `test_schemas.py` | closed objects, `kind` const, `spec_version` enum, ASCII grammars, `/.well-known/faw-node.json` discovery, legacy `agent-federation.json` rejected |
| Canonicalization | `test_canonical.py` | nested key order, Unicode preservation, lone-surrogate rejection, exponent numbers, NaN/Infinity, negative zero, duplicate members |
| Signatures and manifest trust | `test_signatures.py` | valid pass, one-byte mutation fails, changed kind/authority/budget/deadline/expected output fails, unknown key, inactive `kid`, revoked key, rotation continuity, broken chain, stale context reports `stale` + head sequence/digest |
| Delegation and receipt | `test_delegation.py` | digest binding, wrong task/attempt, relayed-to-other-node rejected before key resolution, capability-addressed under policy, non-target executor, unknown/non-outstanding receipt, second terminal rejected, expired, `issued_at >= expires_at`, `expires_at > deadline`, `authority.expiry < deadline`, `timed_out` at deadline, insufficient authority, unenforceable budget, duplicate delivery at-most-once, same-digest dedup, digest-mismatch integrity violation, retry with new attempt allowed |
| Transport and state | `test_transport.py` | durable registration before send, duplicate delivery safe, partial multi-target failure retains messages, ack removes only acked message, malformed input never reaches a handler, atomic receipt close, offline demo end-to-end |
| Golden vectors | `test_vectors.py` | canonical bytes, content digests, public keys/kids, signatures, delegation digest, receipt digest, artifact digest — reproducible without importing the package |

## What this suite does NOT prove

- Production-grade transport reliability (the filesystem adapter is a
  reference loopback).
- Online revocation freshness (v0.2 revocation is best-effort against locally
  pinned state).
- Sandboxing of arbitrary capability handlers (the demo capability is
  deterministic and external-effect-free).
- "Secure", "production-ready", "decentralized", or "interoperable" in any
  stronger sense than the listed acceptance tests demonstrate.

## Definition of done (v0.2, §17)

- all required core files exist;
- schemas validate their examples;
- JCS and Ed25519 golden vectors pass;
- unverified input cannot reach a handler;
- replay tests prove at-most-once handler admission per attempt within the
  persisted replay window;
- partial transport failure cannot erase undelivered messages;
- key rotation works without changing stable `node_id`;
- `faw demo` completes fully offline;
- a second implementation can reproduce the vectors without importing the
  Python package;
- the implementation report is complete;
- an independent reviewer finds no unresolved critical issue.

See `docs/IMPLEMENTATION_REPORT.md` for test results and the independent
review.

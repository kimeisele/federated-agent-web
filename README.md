# Federated Agent Web

**Transport-agnostic delegation contract — reference implementation (v0.2).**

This repository is the smallest credible, transport-agnostic reference
implementation of the delegation contract described in
[`MANIFESTO.md`](MANIFESTO.md). Delegation is the protocol; Nadi, GitHub,
filesystem, HTTP, A2A, queues, and future mechanisms are adapters beneath it.

- **Node discovery** — signed node manifests at `/.well-known/faw-node.json`
- **Signed delegation** — Ed25519-signed, JCS-canonicalized documents
- **Bounded authority and budget** — enforceable ceilings, deadline, expiry
- **Independently verifiable terminal receipt** — bound to the exact delegation
- **Transport adapters** — loopback/filesystem shipped; `nadi_compat` specified

## Status

Reference implementation, laboratory prototype. Not "production-ready",
not "secure", not "decentralized" — see [`CONFORMANCE.md`](CONFORMANCE.md) for
exactly what is demonstrated and [`SECURITY.md`](SECURITY.md) for the threat
model. Do not run against untrusted peers.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

faw demo                 # fully offline two-node demo
faw conformance <node-path-or-url>   # verify a node manifest
faw manifest init --name "My Node" --capabilities hash_file --out ./my-node
```

Run the conformance suite offline:

```bash
pytest -q
```

### Operational one-shot node

> **experimental / laboratory reference implementation**

Persist a node, then run it as a one-shot executor or issuer against the
filesystem transport:

```bash
# Create and persist a node identity
faw manifest init --name "My Node" --capabilities hash_file --out ./my-node

# Run once as executor: process one delegated task and exit
faw node run-once \
  --identity ./my-node \
  --trust ./peer-node \
  --transport-root ./transport \
  --state-dir ./state \
  --work-dir ./work \
  --role executor

# Run once as issuer: accept one terminal receipt and exit
faw node run-once \
  --identity ./my-node \
  --trust ./peer-node \
  --transport-root ./transport \
  --state-dir ./state \
  --work-dir ./work \
  --role issuer
```

Each invocation processes at most one inbound envelope and exits. State is
persisted across restarts (replay store, pending-delegation store).
`hash_file` is the only executable capability. No daemon, no HTTP server,
no endless loop.


## Repository layout

```text
MANIFESTO.md            the manifesto (Draft 3), imported verbatim
SPEC.md                 normative contract summary (v0.2)
CONFORMANCE.md          what is proven, and how
SECURITY.md             threat model and security invariants
skill.md                agent-readable onboarding file (read-only by default)
schemas/                three normative JSON Schemas (closed, v0.2)
src/federated_agent_web/
  canonical.py          strict RFC 8785 (JCS) parse + canonicalization
  crypto.py             Ed25519 signing/verification, kid derivation
  identity.py           stable node identity, rotatable keys, manifest chains
  documents.py          envelope, schema validation, document builders
  verify.py             the authoritative 11-step verification procedure
  replay.py             receiver-side at-most-once admission store
  pending.py            issuer-side outstanding-delegation store
  cli.py                the `faw` CLI
  transports/           transport interface + filesystem adapter
examples/run_demo.py    offline two-node demo entry point
vectors/                golden vectors for independent implementations
docs/                   reuse report, implementation report, Nadi adapter spec
```

## The contract in one paragraph

A node publishes a **signed manifest** declaring capabilities, endpoints,
keys, cost class, and rate limits. A caller issues a **signed delegation**
carrying a stable `task_id`, a unique `attempt_id`, a named capability,
structured authority, an enforceable budget, and a deadline. The receiver
verifies the document in a fixed order (strict JCS parse → schema → audience →
temporal → pinned manifest chain → freshness → signature → replay → authority →
atomic admission) and admits each attempt **at most once**. Every attempt ends
in exactly one **terminal receipt** (`succeeded` / `failed` / `rejected` /
`timed_out`) signed by the executor and bound to the delegation digest; the
issuer accepts it only against its own outstanding-delegation store.

## License

MIT. See [`SECURITY.md`](SECURITY.md) before exposing any node to a network.

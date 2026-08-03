# Transport Conformance

## Purpose

One adapter-neutral transport contract suite proves that any FAW transport
adapter satisfies the same delivery properties. The suite lives in
`tests/transport_contract.py` (harness + case registration) and
`tests/test_transport_contract.py` (shared property tests).

Shared property tests never branch on the adapter name. Adapter-specific
setup and fault injection live entirely in the harness objects
(`tests/transport_contract.py`).

## Command

```bash
python -m pytest -q tests/test_transport_contract.py
```

## Current matrix

| Transport | Shared contract suite | Live network evidence |
|---|---:|---:|
| Filesystem | pass | not applicable |
| Nadi/GitHub | pass against stub | not performed |

The suite currently registers two cases (`filesystem_transport_case` and
`nadi_stub_transport_case`); the same 10 properties run against both —
20 passing parameterized cases, no shared test changes required.

## Shared properties

1. exact document bytes round trip;
2. envelope routing (destination, source, stable message ID);
3. distinct message IDs;
4. per-message acknowledgement;
5. per-message negative acknowledgement with failed evidence;
6. partial destination failure (exact-message-ID retention);
7. duplicate delivery with at-most-once replay admission;
8. malformed input never reaches a handler;
9. signed delegation/receipt round trip through the core;
10. no transport authority (source metadata never changes document validity).

## Future v0.4 gate

The eventual v0.4 evidence sequence:

1. shared suite passes against the filesystem adapter;
2. the unchanged shared suite passes against a stubbed Nadi relay;
3. a controlled live GitHub rehearsal between `kimeisele` and
   `federation-operator` provides relay-conformance evidence;
4. no claim that the controlled rehearsal is an independent external pilot.

v0.4 is not marked complete by this slice; the Nadi adapter is a later issue
and PR.

## Simulated relay conformance versus live GitHub evidence

Nadi-specific acceptance items (distinct relay address, differing local
FAW/relay identity, missing-route retention, wrong-destination quarantine,
relay-source independence, no credentials in manifests) are proven by the
Nadi adapter's own focused tests; they do not become shared filesystem
properties.

Stubbed-relay conformance proves the adapter's behavior against the
`Transport` contract. Live GitHub evidence proves only that the real relay
mailbox can carry FAW documents for a controlled rehearsal. Neither is proof
of an independent external pilot, a second implementation, or production
readiness.

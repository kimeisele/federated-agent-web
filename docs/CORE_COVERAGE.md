# Core Coverage

Branch-and-statement coverage for the FAW security-critical core modules.
Measured with `pytest-cov`; CI enforces a no-regression floor.

## Measurement command

```bash
python -m pytest -q \
  --cov=federated_agent_web.canonical \
  --cov=federated_agent_web.verify \
  --cov=federated_agent_web.replay \
  --cov=federated_agent_web.pending \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=90
```

## In-scope modules

| Module | Role |
|---|---|
| `canonical.py` | Strict JCS parsing, canonicalization, content digests |
| `verify.py` | Authoritative 11-step verification procedure |
| `replay.py` | At-most-once admission, replay deduplication |
| `pending.py` | Issuer-side outstanding-delegation store, receipt binding |

## Coverage (v0.3, measured 2026-08-03)

| Module | Statements | Branches | Aggregate |
|---|---|---|---|
| `canonical.py` | 98% | 100% | |
| `verify.py` | 91% | 92% | |
| `replay.py` | 88% | 85% | |
| `pending.py` | 86% | 72% | |
| **Aggregate** | **91%** | **88%** | **91%** |

Enforced floor: **90%**

## Meaningful uncovered branches

| Module | Lines | Reason |
|---|---|---|
| `canonical.py` | 129 | `canonical_bytes` wraps rfc8785 IntegerDomainError — triggered only when a parsed integer survives the strict-parse domain check (defensive; cannot be reached from valid JSON) |
| `pending.py` | 82-87 | `register_outstanding` issuer_node_id mismatch — already guarded by cross-field checks in `build_document` |
| `pending.py` | 157-158 | `accept_terminal` lock context — exercised by atomic close paths through `verify()`; direct lock-acquire path tested indirectly |
| `replay.py` | 78-83 | `get()` file-read path — exercised by all admit/attach_terminal flows |
| `replay.py` | 123, 129 | `mark_executing`/`attach_terminal` state transitions — covered by `test_mark_executing_*` and `test_attach_terminal_*` |
| `verify.py` | 179 | Delegation with replay store present but not hitting the dedup path — exercised by fresh-admission tests |
| `verify.py` | 295-305 | Step 11 concurrent-replay AlreadyAdmitted with matching digest — covered by `test_concurrent_admission_dup_deduplicates` |

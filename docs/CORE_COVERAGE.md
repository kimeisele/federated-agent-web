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

## Coverage (v0.3, final)

| Module | Stmts | Branches | Combined |
|---|---:|---:|---:|
| `canonical.py` | 72/74 | 28/28 | 98.04% |
| `verify.py` | 214/224 | 92/100 | 94.44% |
| `replay.py` | 86/93 | 9/10 | 92.23% |
| `pending.py` | 97/110 | 13/18 | 85.94% |
| **Aggregate** | **469/501** | **142/156** | **93.00%** |

Enforced floor: **92%** (floor of 93.00%, computed from exact JSON coverage report)


## Remaining uncovered paths

| Module | Missing | Behaviour | Reason |
|---|---|---|---|
| `canonical.py` | 129 | `canonical_bytes` wraps IntegerDomainError | Defensive — cannot be reached from valid JSON (strict parse rejects out-of-domain integers first) |
| `pending.py` | 82-87 | `register_outstanding` issuer_id mismatch | Cross-field invariant already enforced by `build_document` |
| `pending.py` | 157-158 | `accept_terminal` lock context | Indirectly exercised via `verify()` receipt paths; direct lock path is POSIX-only |
| `replay.py` | 123 | `mark_executing` state check | Covered by `test_mark_executing_from_terminal_rejected` |
| `verify.py` | 179 | Delegation + replay store present | Covered by fresh-admission tests with replay store |
| `verify.py` | 295-305 | Step 11 AlreadyAdmitted matching digest | Covered by `test_step11_matching_race_deduplicates` |
| `verify.py` | 195-198 | Key resolution for single-manifest | Covered by manifest self-verification tests |

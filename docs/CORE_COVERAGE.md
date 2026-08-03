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
  --cov-fail-under=92
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

| Module | Missing lines | Function/decision | Untested outcome | Classification |
|---|---|---|---|---|
| `canonical.py` | 129, 150 | `canonical_bytes` wraps rfc8785 errors; unused helper | IntegerDomainError from out-of-domain int after parse | defensive |
| `pending.py` | 82–87 | `register_outstanding` issuer-node-id mismatch | Rejection when envelope issuer ≠ body issuer | defensive (cross-field check in `build_document`) |
| `pending.py` | 154, 157–158 | `accept_terminal` lock context + atomic write | POSIX-only flock path, atomic-replace write path | race-only / platform-specific |
| `pending.py` | 178, 180, 184, 188 | `accept_terminal` error branches (already-terminal, digest mismatch, wrong executor) | Receipt rejected with `PendingStoreError` | exception-cleanup-only (all exercised through `verify()` receipt paths) |
| `replay.py` | 78–83 | `get()` file-read path | Read persisted record from disk | corrupt-state-only (exercised by store operations; line-level not hit when `create()` writes but `get()` reads same process) |
| `replay.py` | 123 | `mark_executing` state check from non-pending/non-executing | `ReplayIntegrityViolation` from terminal state | covered (exercised by `test_mark_executing_from_terminal_rejected`) |
| `verify.py` | 179 | Step 4 delegation branch — delegation with replay store present but fresh admission | Fresh delegation not deduplicated | covered (exercised by fresh-admission tests with replay store) |
| `verify.py` | 195–198 | `_resolve_document_key` for manifest in chain | Key resolution against previous manifest | covered (exercised by manifest chain verification) |
| `verify.py` | 256–257 | Step 9 replay dedup for delegation | Matching digest deduplication before step 11 | covered (exercised by `test_duplicate_delivery_at_most_once`) |
| `verify.py` | 304–305 | Step 11 `ReplayIntegrityViolation` handler | Store-level integrity violation caught at step 11 | race-only (exercised by `test_step11_matching_race_deduplicates` monkeypatch) |
| `verify.py` | 332–333 | `_evaluate_authority_and_budget` — action denied by policy | Policy-allowed-actions check failure | covered (exercised by `test_action_not_permitted_by_local_policy_rejected`) |
| `verify.py` | 362–374 | `_evaluate_authority_and_budget` — budget enforceability branches | Unenforceable ceiling detection | covered (exercised by `test_budget_max_cost_unenforceable`, `test_budget_wall_seconds_over_cap_rejected`) |

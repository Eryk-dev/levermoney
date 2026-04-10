# Quickstart: Daily Cash Reconciliation

**Date**: 2026-04-10

## Context

Read these documents IN ORDER before writing any code:

1. `new levermoney/SDD.md` — Problem statement, root causes, test plan
2. `.specify/memory/constitution.md` — 6 non-negotiable principles
3. `specs/001-daily-cash-reconciliation/spec.md` — Feature spec with acceptance scenarios
4. `specs/001-daily-cash-reconciliation/plan.md` — Implementation plan
5. `specs/001-daily-cash-reconciliation/research.md` — Technical decisions
6. `docs/CONHECIMENTO_MERCADO_LIVRE.md` — Domain knowledge (ML/MP APIs)
7. `CLAUDE.md` — Full system documentation

## Prerequisites

```bash
source venv/bin/activate
python3 -m pytest -q  # Must pass (574+ tests, 0 failures)
```

## Test Data

Real production data at `testes/data/`:
- Payments fetched live from ML API via `ml_api.search_payments()` (session-cached in fixtures)
- `extratos/extrato janeiro 141Air.csv` — 690 extrato lines (golden ref)
- `extratos/extrato fevereiro 141Air.csv` — 592 extrato lines (golden ref)

## Execution Order

**CRITICAL: Follow this exact order. Do NOT skip steps.**

### Step 1: Write failing tests

Create `testes/integration/test_cash_reconciliation.py` with all 6
test suites from the spec. They MUST fail initially (proving bugs exist).

```bash
python3 -m pytest testes/integration/test_cash_reconciliation.py -v
# Expected: FAILURES (bugs not yet fixed)
```

### Step 2: Fix money_release_date BRT (1 line)

File: `app/services/processor.py`, line ~303

```python
# FROM:
money_release_date = (payment.get("money_release_date") or date_approved_raw)[:10]

# TO:
money_release_date = _to_brt_date(payment.get("money_release_date") or date_approved_raw)
```

```bash
python3 -m pytest -q  # Full suite must still pass
python3 -m pytest testes/integration/test_cash_reconciliation.py -v
# Expected: BRT date tests now PASS, others still fail
```

### Step 3: Fix sign inversion

File: `app/services/expense_classifier.py`

Fix `_is_incoming_transfer()` and direction mapping based on test
results from Step 1.

```bash
python3 -m pytest -q
python3 -m pytest testes/integration/test_cash_reconciliation.py -v
# Expected: Sign tests now PASS
```

### Step 4: Fix unconditional skips

File: `app/services/extrato_ingester.py`, lines ~100-102

Convert skip rules to capture rules for items with cash impact.

```bash
python3 -m pytest -q
python3 -m pytest testes/integration/test_cash_reconciliation.py -v
# Expected: Coverage tests now PASS
```

### Step 5: Daily reconciliation MUST pass

```bash
python3 -m pytest testes/integration/test_cash_reconciliation.py::TestDailyReconciliation -v
# Expected: ALL days pass (divergence < R$0.05)
```

### Step 6: Validate with February data

```bash
python3 -m pytest testes/integration/test_cash_reconciliation.py -v -k "fev"
# Expected: February data also passes
```

## Success Criterion

```
For EVERY day of January AND February 2026:
  abs(CA_daily_total - extrato_daily_total) < R$0.05
```

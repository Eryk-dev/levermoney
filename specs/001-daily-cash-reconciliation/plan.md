# Implementation Plan: Daily Cash Reconciliation

**Branch**: `001-daily-cash-reconciliation` | **Date**: 2026-04-10 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/001-daily-cash-reconciliation/spec.md`

## Summary

Fix the Lever Money conciliation system so that daily cash totals in
Conta Azul match the ML extrato to the cent. Three root causes have been
identified: (1) money_release_date not converted to BRT, (2) sign
inversion in non-order payment classification, (3) extrato lines silently
skipped. The approach is test-first: write new reconciliation tests using
real production data, prove each bug exists, fix one at a time, measure
divergence reduction after each fix.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: FastAPI 0.115.6, httpx 0.28.1, supabase-py 2.11.0, pydantic-settings 2.7.1
**Storage**: Supabase (PostgreSQL) via supabase-py SDK
**Testing**: pytest (574+ tests, real production data in testes/data/)
**Target Platform**: Linux server (Docker multi-stage)
**Project Type**: Web service (API + background jobs)
**Performance Goals**: Daily reconciliation divergence < R$0.05/day
**Constraints**: All dates in BRT, charges_details only for fees, no silent data loss
**Scale/Scope**: ~900 payments/month/seller, ~700 extrato lines/month/seller, 4+ sellers

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Cash Reconciliation First | PASS | This feature's sole purpose |
| II. Prove Before Building | PASS | Test-first approach: 6 new test suites using real data |
| III. Extrato is Source of Truth | PASS | All assertions compare against extrato CSV |
| IV. One Fix, One Measurement | PASS | Execution order: one fix per commit, divergence measured |
| V. No Silent Data Loss | PASS | US-4 specifically audits and eliminates silent skips |
| VI. Dates in BRT, Always | PASS | US-2 fixes money_release_date BRT conversion |

All gates pass. No violations.

## Project Structure

### Documentation (this feature)

```text
specs/001-daily-cash-reconciliation/
├── plan.md              # This file
├── research.md          # Phase 0: technical research
├── data-model.md        # Phase 1: entity model
├── quickstart.md        # Phase 1: getting started
└── tasks.md             # Phase 2: task breakdown (via /speckit.tasks)
```

### Source Code (repository root)

```text
app/
├── services/
│   ├── processor.py             # FIX: money_release_date BRT (US-2)
│   ├── expense_classifier.py    # FIX: sign inversion (US-3)
│   ├── extrato_ingester.py      # FIX: unconditional skips (US-4)
│   ├── money.py                 # Existing: unified sign convention
│   ├── event_ledger.py          # Existing: source of truth
│   └── release_report_validator.py  # VALIDATE: fee adjustments (US-6)
├── routers/
│   └── baixas.py                # Existing: settlement creation
└── models/

testes/
├── data/
│   └── extratos/                            # Extratos are golden ref (static CSV)
│       ├── extrato janeiro 141Air.csv       # 690 lines (golden ref)
│       └── extrato fevereiro 141Air.csv     # 592 lines (golden ref)
├── integration/
│   └── test_cash_reconciliation.py  # NEW: daily cash reconciliation tests
└── unit/
    ├── test_brt_dates.py            # NEW: BRT conversion tests
    └── test_sign_correctness.py     # NEW: sign direction tests
```

**Structure Decision**: Existing monorepo structure. New test files only.
No new services, routers, or modules needed. Fixes are in existing files.

## Execution Strategy

### Phase Order (Constitution: One Fix, One Measurement)

```
1. Write ALL new tests first (they MUST fail — proving bugs exist)
2. Fix money_release_date BRT (US-2) → re-run → measure
3. Fix sign inversion (US-3) → re-run → measure
4. Fix unconditional skips (US-4) → re-run → measure
5. Validate fee adjustments (US-6) → re-run → measure
6. Run daily reconciliation test (US-5) → MUST pass
```

### Test Architecture

All new tests share a common pattern:

```
1. Fetch payments live from ML API via ml_api.search_payments() (session-cached)
2. Load real extrato CSV from testes/data/extratos/
3. Parse both into structured data
4. Run the comparison logic
5. Assert specific measurable outcomes
```

No mocks of external APIs. No synthetic data. Real payments, real extrato.

### Shared Test Fixtures

A single conftest or helper module that:
- Parses the extrato CSV (semicolon-delimited, BR number format)
- Fetches payments live from ML API (cached in-memory per session)
- Provides lookup by payment_id / reference_id
- Converts extrato amounts from BR format ("1.234,56") to float

### Files Modified (estimated)

| File | Change | Lines |
|------|--------|-------|
| `app/services/processor.py` | BRT convert money_release_date (1 line) | ~1 |
| `app/services/expense_classifier.py` | Fix sign for deposits/transfers | ~10-20 |
| `app/services/extrato_ingester.py` | Convert unconditional skips to captures | ~10-15 |
| `testes/integration/test_cash_reconciliation.py` | NEW: all 6 test suites | ~300-400 |
| `testes/unit/test_brt_dates.py` | NEW: BRT conversion edge cases | ~50 |
| `testes/unit/test_sign_correctness.py` | NEW: sign direction validation | ~80 |

### Risks

| Risk | Mitigation |
|------|-----------|
| "Unconditional skip" items actually have zero cash impact | US-4 test validates each skip against extrato — if cash impact exists, test fails |
| Sign fix introduces new sign bugs elsewhere | Unit tests for every direction+type combination |
| BRT fix shifts existing correct baixas to wrong day | Test validates ALL pairs, not just edge cases |
| Fee adjustments don't close the gap fully | US-6 test measures residual gap after adjustments |
| Extrato CSV format varies between months | Tests run on both Jan and Feb data |

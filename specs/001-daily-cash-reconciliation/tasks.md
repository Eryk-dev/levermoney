# Tasks: Daily Cash Reconciliation

**Input**: Design documents from `specs/001-daily-cash-reconciliation/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, quickstart.md

**Tests**: Tests are MANDATORY per Constitution Principle II (Prove Before Building).

**Organization**: Tasks are grouped by user story. Each story is independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1-US6)
- Include exact file paths in descriptions

## Path Conventions

- Backend: `app/services/`, `app/routers/`
- Tests: `testes/unit/`, `testes/integration/`
- Test data: `testes/data/` (extratos CSV only — payments are fetched live from ML API)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Test helpers and fixtures for all reconciliation tests.

- [X] T001 Create extrato CSV parser helper at `testes/helpers/extrato_parser.py` — parse semicolon-delimited CSV with BR number format (`1.234,56` → float), return list of dicts with fields: date, transaction_type, reference_id, amount, balance
- [X] T002 Create reconciliation conftest at `testes/integration/conftest_cash.py` — fixtures that fetch payments LIVE from ML API via `ml_api.search_payments()` for Jan and Feb 2026 (seller 141air), and load extrato CSVs from `testes/data/extratos/` using the parser from T001. Cache results in-memory per test session (`@pytest.fixture(scope="session")`). Provide lookup helpers: `payments_by_id`, `extrato_by_ref_id`, `extrato_liberacoes`, `extrato_by_date`
- [X] T003 [P] Create `_extract_processor_charges()` test helper at `testes/helpers/charge_extractor.py` — standalone function that replicates processor.py's fee/shipping extraction from charges_details without importing processor.py (tests must be independent of code under test)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Ensure existing test suite still passes before any changes.

- [X] T004 Run `python3 -m pytest -q` and confirm 574+ tests pass with 0 failures. Document current test count as baseline.

---

## Phase 3: Per-Payment Net Validation (US1, P1)

**Goal**: Prove that for every payment with a "Liberacao de dinheiro" in the extrato, `transaction_amount - mp_fee - shipping_cost` equals the extrato net amount.

**Independent Test**: Run `python3 -m pytest testes/integration/test_cash_reconciliation.py::TestPerPaymentNet -v`

- [X] T005 [US1] Write test `TestPerPaymentNet::test_single_payment_net_matches_extrato` in `testes/integration/test_cash_reconciliation.py` — pick one approved payment from Jan API data, calculate net using charge_extractor, find matching extrato liberacao by reference_id, assert `abs(calculated_net - extrato_amount) < 0.02`
- [X] T006 [US1] Write test `TestPerPaymentNet::test_all_payments_net_match_extrato_jan` — for ALL matched payment+extrato pairs in January, assert per-payment net matches within R$0.02. Report count of matches, mismatches, and total divergence.
- [X] T007 [US1] Write test `TestPerPaymentNet::test_financing_fee_excluded` — find a payment with financing_fee in charges_details, assert it is excluded from mp_fee, assert net still matches extrato.
- [X] T008 [P] [US1] Write test `TestPerPaymentNet::test_all_payments_net_match_extrato_fev` — same as T006 but using February 2026 data.

---

## Phase 4: Money Release Date BRT Alignment (US2, P1)

**Goal**: Prove money_release_date needs BRT conversion and fix it.

**Independent Test**: Run `python3 -m pytest testes/integration/test_cash_reconciliation.py::TestBrtDates -v`

- [X] T009 [US2] Write test `TestBrtDates::test_brt_converted_date_matches_extrato` in `testes/integration/test_cash_reconciliation.py` — for each matched pair, assert `_to_brt_date(money_release_date) == extrato_date`
- [X] T010 [US2] Write test `TestBrtDates::test_truncated_date_diverges` — for each matched pair, compare `money_release_date[:10]` (current behavior) vs extrato_date. Count divergences. Assert at least 1 divergence exists (proving the bug).
- [X] T011 [US2] Write unit test `TestBrtConversion::test_late_night_utc4_crosses_midnight` in `testes/unit/test_brt_dates.py` — assert `_to_brt_date("2026-01-20T23:00:00.000-04:00") == "2026-01-21"` and `"2026-01-20T23:00:00.000-04:00"[:10] == "2026-01-20"` (proving 1-day offset)
- [X] T012 [US2] Fix `money_release_date` in `app/services/processor.py` line ~303 — change `(payment.get("money_release_date") or date_approved_raw)[:10]` to `_to_brt_date(payment.get("money_release_date") or date_approved_raw)`
- [X] T013 [US2] Run `python3 -m pytest -q` — all existing tests must still pass. Run T009 — must now pass.

---

## Phase 5: Sign Correctness for Non-Order Payments (US3, P1)

**Goal**: Prove and fix sign inversion in deposits/transfers.

**Independent Test**: Run `python3 -m pytest testes/integration/test_cash_reconciliation.py::TestSignCorrectness -v`

- [X] T014 [US3] Write test `TestSignCorrectness::test_extrato_credits_are_positive` in `testes/integration/test_cash_reconciliation.py` — for each non-order extrato line with positive amount (credit), verify the system's classification produces a positive signed_amount
- [X] T015 [US3] Write test `TestSignCorrectness::test_extrato_debits_are_negative` — for each non-order extrato line with negative amount (debit), verify the system's classification produces a negative signed_amount
- [X] T016 [US3] Write test `TestSignCorrectness::test_deposit_sign_is_positive` — find "Entrada de dinheiro" lines in extrato, assert they classify as positive
- [X] T017 [US3] Write test `TestSignCorrectness::test_transfer_intra_sign_matches_extrato` — find transfer lines, assert sign matches extrato direction
- [X] T018 [US3] Fix sign inversion in `app/services/expense_classifier.py` — based on test results from T014-T017, correct `_is_incoming_transfer()` and/or direction mapping so all signs match extrato
- [X] T019 [US3] Run `python3 -m pytest -q` — all existing tests must still pass. Run T014-T017 — must now pass.

---

## Phase 6: Full Extrato Coverage (US4, P2)

**Goal**: Ensure every extrato line with cash impact is accounted for.

**Independent Test**: Run `python3 -m pytest testes/integration/test_cash_reconciliation.py::TestExtratoCoverage -v`

- [X] T020 [US4] Write test `TestExtratoCoverage::test_all_lines_classified` in `testes/integration/test_cash_reconciliation.py` — parse full January extrato (690 lines), for each line check if it's: (a) matched to a payment, (b) classifiable by extrato_ingester rules, or (c) unconditional skip. Report uncovered lines.
- [X] T021 [US4] Write test `TestExtratoCoverage::test_unconditional_skips_have_zero_cash_impact` — for each "unconditional skip" rule (transferencia pix, pix enviado, pagamento de conta), find matching lines in extrato and assert their net cash impact on the seller is zero. If non-zero, the test MUST fail.
- [X] T022 [US4] Write test `TestExtratoCoverage::test_no_cash_impact_lines_missing` — sum all extrato lines, sum all classified lines (payments + expenses + justified skips). Assert difference < R$0.05.
- [X] T023 [US4] Fix unconditional skips in `app/services/extrato_ingester.py` lines ~100-102 — for any skip rule where T021 fails (non-zero cash impact), convert to a capture rule with appropriate expense_type and direction
- [X] T024 [US4] Run `python3 -m pytest -q` — all tests pass. Run T020-T022 — must now pass.

---

## Phase 7: Daily Cash Reconciliation (US5, P2)

**Goal**: The FINAL test. Daily totals in CA must match extrato.

**Independent Test**: Run `python3 -m pytest testes/integration/test_cash_reconciliation.py::TestDailyReconciliation -v`

- [X] T025 [US5] Write test `TestDailyReconciliation::test_daily_totals_match_jan` in `testes/integration/test_cash_reconciliation.py` — for each day of January 2026, compute: CA side = sum of (receita - comissao - frete + expenses) per payment released that day (using BRT money_release_date); extrato side = sum of all extrato lines for that day. Assert `abs(CA - extrato) < R$0.05` per day.
- [X] T026 [P] [US5] Write test `TestDailyReconciliation::test_daily_totals_match_fev` — same as T025 but for February 2026.
- [X] T027 [US5] Write test `TestDailyReconciliation::test_cumulative_divergence_under_threshold` — sum divergence across all days of both months. Assert cumulative `abs(total_divergence) < R$5.00` (down from R$103k).
- [X] T028 [US5] If T025-T027 fail, investigate and fix remaining divergences. Each fix is a separate commit with measured impact per Constitution Principle IV.

---

## Phase 8: Fee Validation (US6, P3)

**Goal**: Detect and correct fee discrepancies between processor and release report.

**Independent Test**: Run `python3 -m pytest testes/integration/test_cash_reconciliation.py::TestFeeValidation -v`

- [X] T029 [US6] Write test `TestFeeValidation::test_processor_fees_vs_release_report` in `testes/integration/test_cash_reconciliation.py` — compare processor-calculated fees (from charges_details) against release report CSV MP_FEE_AMOUNT for each payment. Report count and total of divergences >= R$0.01.
- [X] T030 [US6] Write test `TestFeeValidation::test_adjustment_entries_close_gap` — verify that after adjustment entries are created (via release_report_validator), the fee gap per payment is < R$0.01.
- [X] T031 [US6] Verify `release_report_validator.py` runs in nightly pipeline. Check `app/main.py` lifespan for step 3 (`validate_release_fees_all_sellers`). If not running, fix the pipeline configuration.

---

## Phase 9: Polish & Validation

**Purpose**: Final validation and documentation.

- [X] T032 Run full test suite: `python3 -m pytest -q` — all tests pass (574+ existing + new reconciliation tests)
- [X] T033 Run reconciliation tests on BOTH months: `python3 -m pytest testes/integration/test_cash_reconciliation.py -v` — all pass
- [ ] T034 Update `new levermoney/SDD.md` with final test results: measured divergence per day, total divergence, list of fixes applied with R$ impact

---

## Dependencies

```
Phase 1 (Setup) ──────────────────────> ALL subsequent phases
Phase 2 (Baseline) ───────────────────> ALL subsequent phases
Phase 3 (US1: Per-Payment Net) ───────> Phase 7 (US5: Daily Reconciliation)
Phase 4 (US2: BRT Dates) ────────────> Phase 7 (US5: Daily Reconciliation)
Phase 5 (US3: Sign Correctness) ─────> Phase 7 (US5: Daily Reconciliation)
Phase 6 (US4: Extrato Coverage) ─────> Phase 7 (US5: Daily Reconciliation)
Phase 7 (US5: Daily Reconciliation) ──> Phase 8 (US6: Fee Validation)
Phase 8 (US6: Fee Validation) ────────> Phase 9 (Polish)
```

Phases 3, 4, 5, 6 can run in PARALLEL after Phase 2 completes.
Phase 7 BLOCKS until Phases 3-6 all pass.

## Parallel Execution Examples

**After Phase 2 baseline passes:**

```
Agent A: Phase 3 (US1 — per-payment net tests)
Agent B: Phase 4 (US2 — BRT date tests + fix)
Agent C: Phase 5 (US3 — sign correctness tests + fix)
Agent D: Phase 6 (US4 — extrato coverage tests + fix)
```

**After all P1 stories pass:**

```
Single agent: Phase 7 (US5 — daily reconciliation, the integration test)
```

## Implementation Strategy

**MVP (minimum viable)**: Phases 1-2-3 only. If per-payment net doesn't
match, stop and fix before anything else.

**Full delivery**: All 9 phases in order. Phase 7 (daily reconciliation)
is the acceptance gate — if it passes, the system works.

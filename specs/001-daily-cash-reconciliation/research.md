# Research: Daily Cash Reconciliation

**Date**: 2026-04-10
**Feature**: [spec.md](spec.md)

No NEEDS CLARIFICATION items in Technical Context. All decisions are
informed by existing codebase analysis and production data.

---

## R1: money_release_date Timezone Handling

**Decision**: Apply `_to_brt_date()` to money_release_date, same as
date_approved.

**Rationale**: ML API returns all dates in UTC-4. The extrato CSV uses
BRT (UTC-3). A release at 23:00 UTC-4 is 00:00 BRT the next day. The
current code truncates without conversion (`[:10]`), causing a 1-day
offset for late-night releases. `date_approved` already uses
`_to_brt_date()` correctly — money_release_date must do the same.

**Alternatives considered**:
- Keep truncation, adjust extrato parser instead → Rejected: extrato
  dates are BRT and correct; the system must match them.
- Store both UTC-4 and BRT dates → Rejected: unnecessary complexity.
  Only BRT is used for financial attribution.

**Location**: `app/services/processor.py`, line ~303.

---

## R2: Non-Order Payment Sign Convention

**Decision**: Validate every non-order classification against extrato
credit/debit direction. Fix `_is_incoming_transfer()` and direction
mapping where they produce inverted signs.

**Rationale**: Production audit measured R$106k divergence from a single
R$53k deposit with inverted sign. The unified `money.py` sign function
is correct; the bug is in the *direction input* passed to it.

**Alternatives considered**:
- Add a sign override table by expense_type → Rejected: treats symptoms.
  The classification logic itself must be correct.
- Remove `_is_incoming_transfer()` and always use extrato direction
  → Rejected: the classifier runs on Payment API data, not extrato
  data. It must infer direction correctly from payment fields.

**Location**: `app/services/expense_classifier.py`, `_is_incoming_transfer()`.

---

## R3: Unconditional Skip Evaluation

**Decision**: Audit all "unconditional skip" rules against real extrato
data. Convert to capture rules if cash impact exists.

**Rationale**: Three rules currently skip without capture:
- `transferencia pix` (40+ lines/month)
- `pix enviado` (1+ lines/month)
- `pagamento de conta` (19+ lines/month)

These may or may not have net cash impact on the seller's account.
The test will determine this empirically by checking if these lines
appear in the extrato with non-zero amounts.

**Alternatives considered**:
- Capture all unconditionally → Rejected: some truly are internal (e.g.,
  transfers between sub-accounts net to zero). Must validate first.
- Keep skipping and add manual reconciliation → Rejected: violates
  Constitution Principle V (No Silent Data Loss).

**Location**: `app/services/extrato_ingester.py`, lines ~100-102.

---

## R4: Extrato CSV Parsing

**Decision**: Build a shared test helper that parses the extrato CSV
format (semicolon-delimited, BR numbers) into structured data.

**Rationale**: Multiple tests need to parse the same extrato files.
The format is: `DD-MM-YYYY;TRANSACTION_TYPE;REFERENCE_ID;AMOUNT;BALANCE`
with amounts in BR format (`1.234,56`). A reusable parser avoids
duplication and ensures consistent parsing.

**Alternatives considered**:
- Use pandas → Rejected: unnecessary dependency for test utilities.
  Standard csv module with locale-aware parsing suffices.
- Reuse existing `extrato_ingester.py` parser → Considered: may work
  but tests should be independent of the code under test.

**Location**: New file `testes/helpers/extrato_parser.py` or inline
in conftest.

---

## R5: Fee Validation Approach

**Decision**: Compare processor fees (from event_ledger) against release
report fees (from CSV) per payment. Create adjustment entries for
divergences >= R$0.01.

**Rationale**: `release_report_validator.py` already does this. The
research question was whether it runs reliably in the nightly pipeline.
Review of `app/main.py` lifespan confirms it's step 3 of the nightly
pipeline (`validate_release_fees_all_sellers`). The risk is that
release report generation (async, polling) may timeout silently.

**Alternatives considered**:
- Skip fee validation, accept small divergences → Rejected: centavos
  accumulate to reais over a month.
- Inline fee adjustment into processor.py → Rejected: processor runs
  at approval time, release report is only available later.

**Location**: `app/services/release_report_validator.py` (existing).

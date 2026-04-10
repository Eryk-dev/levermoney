# Feature Specification: Daily Cash Reconciliation

**Feature Branch**: `001-daily-cash-reconciliation`
**Created**: 2026-04-10
**Status**: Draft
**Input**: User description: "Conciliacao diaria de caixa — valores baixados no CA devem bater centavo por centavo com o extrato do Mercado Livre/Mercado Pago para cada dia do mes."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Per-Payment Net Validation (Priority: P1)

As an operator, I want to verify that for every individual payment that
appears as "Liberacao de dinheiro" in the ML extrato, the system's
calculated net (receita - comissao - frete) matches the extrato amount
exactly, so I can trust that the base calculation is correct.

**Why this priority**: If the per-payment calculation is wrong, nothing
else can be right. This is the foundation — every downstream
reconciliation depends on individual payment math being correct.

**Independent Test**: Can be fully tested with live ML API payments and
real extrato CSV. For each matched pair (payment + extrato line), assert
that `transaction_amount - mp_fee - shipping_cost_seller` equals the
extrato net credit amount within R$0.02.

**Acceptance Scenarios**:

1. **Given** 879 real payments from January 2026 (141air) and the
   corresponding extrato CSV with 289 "Liberacao de dinheiro" lines,
   **When** each payment's net is calculated using charges_details,
   **Then** every matched pair has `abs(calculated_net - extrato_net) < R$0.02`.

2. **Given** a payment with charges including financing_fee,
   **When** the net is calculated,
   **Then** financing_fee is excluded and the net still matches the extrato.

3. **Given** 585 real payments from February 2026 and its extrato CSV,
   **When** the same validation runs,
   **Then** all matched pairs reconcile within R$0.02.

---

### User Story 2 - Money Release Date BRT Alignment (Priority: P1)

As an operator, I want the money release date used for baixas to be
converted to BRT (matching the extrato), so that settlements land on the
correct day in Conta Azul.

**Why this priority**: If baixas land on the wrong day, daily totals
will never match even if amounts are correct. This is a known bug that
affects every payment released near midnight UTC-4.

**Independent Test**: For each payment+extrato pair, compare the
BRT-converted money_release_date against the extrato DATE column. Count
how many diverge when using the current truncation (no BRT conversion)
versus the corrected approach.

**Acceptance Scenarios**:

1. **Given** all matched payment+extrato pairs from January 2026,
   **When** money_release_date is converted to BRT via `_to_brt_date()`,
   **Then** the resulting date matches the extrato DATE column for every pair.

2. **Given** the same pairs,
   **When** money_release_date is truncated without BRT conversion
   (current behavior),
   **Then** at least some pairs show a 1-day offset — proving the bug exists.

3. **Given** the BRT fix is applied to processor.py,
   **When** baixas are simulated for January 2026,
   **Then** each baixa's data_pagamento matches the extrato date for that payment.

---

### User Story 3 - Sign Correctness for Non-Order Payments (Priority: P1)

As an operator, I want deposits and incoming transfers to have positive
signs and outgoing payments to have negative signs, so that non-order
items contribute the correct amount to the daily total.

**Why this priority**: A single deposit with inverted sign (e.g., R$53k
positive recorded as negative) causes R$106k of divergence. This has been
measured in production.

**Independent Test**: For each non-order payment from the ML API, classify
it and compare the resulting sign against the extrato's credit/debit
direction. Every credit in the extrato must be positive in the system,
every debit must be negative.

**Acceptance Scenarios**:

1. **Given** the January 2026 extrato for 141air with its non-order lines,
   **When** each line's sign is determined by the expense_classifier,
   **Then** the sign matches the extrato (NET_CREDIT = positive,
   NET_DEBIT = negative) for every line.

2. **Given** a deposit (e.g., "Entrada de dinheiro" with positive amount
   in extrato),
   **When** classified by the system,
   **Then** the signed_amount is positive.

3. **Given** a transfer classified as incoming by `_is_incoming_transfer()`,
   **When** it appears as a credit in the extrato,
   **Then** the system's sign is positive.

---

### User Story 4 - Full Extrato Coverage (Priority: P2)

As an operator, I want every line in the ML extrato to be accounted for
by the system (either as a processed payment, a captured expense, or a
justified skip), so that no money movement is invisible.

**Why this priority**: Gaps in coverage directly cause daily totals to
diverge. Items that are silently skipped are missing from the CA total.

**Independent Test**: Parse the full extrato CSV. For each line, verify
it falls into one of: (a) matched to a processed payment, (b) captured
as an expense event, (c) classified as zero-cash-impact internal
movement. Report any uncovered lines with their amounts.

**Acceptance Scenarios**:

1. **Given** the January 2026 extrato (690 lines),
   **When** each line is checked against the system's classification rules,
   **Then** 100% of lines are accounted for (zero uncovered lines with
   non-zero cash impact).

2. **Given** lines currently marked as "unconditional skip" (transferencia
   PIX, pagamento de conta, pix enviado),
   **When** their cash impact is evaluated,
   **Then** either they have zero net impact (justify the skip) or they
   are reclassified as captured expenses.

---

### User Story 5 - Daily Cash Reconciliation (Priority: P2)

As an operator, I want to sum all settlements (baixas) and expense
entries for a given day in the CA, compare against the sum of all extrato
lines for that same day, and see zero divergence — so I can close the
books with confidence.

**Why this priority**: This is the ultimate acceptance test. Everything
else (per-payment math, BRT dates, sign correctness, coverage) feeds
into this. If this passes, the system works.

**Independent Test**: For each day of January 2026, compute the simulated
CA total (sum of all receitas, comissoes, fretes, estornos, and expense
items released that day) and compare against the extrato total for that
day. The difference must be less than R$0.05.

**Acceptance Scenarios**:

1. **Given** all 879 payments and 690 extrato lines from January 2026,
   **When** the daily reconciliation is computed for each of the 31 days,
   **Then** `abs(CA_total - extrato_total) < R$0.05` for every day.

2. **Given** all 585 payments and 592 extrato lines from February 2026,
   **When** the same daily reconciliation runs,
   **Then** `abs(CA_total - extrato_total) < R$0.05` for every day.

3. **Given** a day with mixed sales, refunds, DIFAL, and PIX transfers,
   **When** the daily total is computed,
   **Then** no category of extrato line is missing from the CA side.

---

### User Story 6 - Fee Validation Against Release Report (Priority: P3)

As an operator, I want the system to detect when fees calculated from
charges_details differ from fees in the ML release report, and create
adjustment entries to correct the difference.

**Why this priority**: Fee discrepancies are typically small (centavos to
a few reais per payment) but accumulate over a month. This is a
refinement after the main reconciliation works.

**Independent Test**: Compare processor-calculated fees against release
report MP_FEE_AMOUNT for each payment. Report total divergence and
verify that adjustment entries close the gap.

**Acceptance Scenarios**:

1. **Given** the January 2026 release report and processor fees,
   **When** fees are compared per payment,
   **Then** any divergence >= R$0.01 generates an adjustment entry.

2. **Given** adjustment entries are created,
   **When** the daily reconciliation re-runs including adjustments,
   **Then** the fee gap is closed to within R$0.01.

---

### Edge Cases

- What happens when a payment is released at 23:59 UTC-4 (crosses
  midnight into next day BRT)?
- How does the system handle a refund that occurs before the original
  payment is released (money_release_status still "pending")?
- What happens when the ML API silently drops payments from search
  results (batch release bug, measured on 2026-01-26)?
- How are kit splits (refunded/by_admin) handled when the original
  payment was never synced?
- What happens when a single payment_id generates multiple extrato lines
  (e.g., dispute lifecycle: debit + credit + refund)?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST calculate per-payment net as
  `transaction_amount - mp_fee - shipping_cost_seller` where fees come
  exclusively from `charges_details[from=collector]`, excluding
  financing_fee.

- **FR-002**: System MUST convert `money_release_date` to BRT using the
  same `_to_brt_date()` function used for `date_approved`, before using
  it as `data_vencimento` in CA parcelas.

- **FR-003**: System MUST assign correct signs to non-order payments:
  credits in the extrato MUST be positive, debits MUST be negative.
  The `_is_incoming_transfer()` function MUST correctly identify the
  direction for all transfer types.

- **FR-004**: System MUST capture every extrato line with non-zero cash
  impact. Lines currently classified as "unconditional skip" MUST be
  validated: if they have cash impact on the seller's account, they
  MUST be reclassified as captured expenses.

- **FR-005**: System MUST produce a daily cash reconciliation that sums
  all CA entries (baixas + expense entries) for a given day and compares
  against the extrato total for that day, with tolerance < R$0.05.

- **FR-006**: System MUST validate processor fees against the ML release
  report and create adjustment entries when divergence >= R$0.01.

- **FR-007**: All new tests MUST use real production data from
  live ML API data (payments fetched via `ml_api.search_payments()`) and
  extrato CSVs from `testes/data/extratos/` for seller 141air (Jan/Feb 2026).

### Key Entities

- **Payment**: An ML transaction with order_id, transaction_amount,
  charges_details, money_release_date, and net_received_amount.
  Generates up to 3 CA entries (receita, comissao, frete).

- **Extrato Line**: A row in the ML account_statement CSV representing a
  real cash movement (credit or debit) on a specific date. Source of
  truth for cash reconciliation.

- **Non-Order Payment**: An ML payment without order_id (DARF,
  subscription, PIX transfer, deposit, etc.). Classified into
  mp_expenses and exported via XLSX for manual CA import.

- **Baixa**: Settlement of a CA parcela on a specific date. The date
  MUST match the BRT-converted money_release_date.

- **Daily Reconciliation**: The comparison between sum of all CA
  movements for a day vs sum of all extrato lines for that day.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 100% of matched payment+extrato pairs,
  `abs(calculated_net - extrato_net) < R$0.02` (per-payment accuracy).

- **SC-002**: For 100% of matched pairs, BRT-converted
  money_release_date equals the extrato DATE column (date alignment).

- **SC-003**: For 100% of non-order extrato lines, the system's sign
  matches the extrato credit/debit direction (sign correctness).

- **SC-004**: 100% of extrato lines with non-zero cash impact are
  accounted for by the system (full coverage).

- **SC-005**: For every day of January and February 2026,
  `abs(CA_daily_total - extrato_daily_total) < R$0.05`
  (daily reconciliation).

- **SC-006**: Divergence reduction from current ~R$103k cumulative
  to < R$5 cumulative over 2 months (system-level accuracy).

## Assumptions

- ML API access for seller 141air is available and returns payments for
  Jan/Feb 2026. Extrato CSVs at `testes/data/extratos/` are representative
  of production behavior.
- The ML API `charges_details` field is complete and accurate for
  payments from 2026 onward (legacy payments without charges_details
  use fallback calculation).
- The extrato CSV format (semicolon-delimited, BR number format) is
  stable and consistent across months.
- Non-order items that the user uploads via XLSX to CA use the same
  date as the extrato line — the system generates the XLSX with the
  correct date.
- "Unconditional skip" items (transferencia PIX, pagamento de conta,
  pix enviado) may need to be re-evaluated: if the user's MP account
  uses these for business purposes, they have cash impact and must be
  captured.

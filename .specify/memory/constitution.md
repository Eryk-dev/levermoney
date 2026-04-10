<!--
  Sync Impact Report
  Version change: 0.0.0 → 1.0.0 (initial ratification)
  Added sections: 6 Core Principles, Financial Constraints, Development Workflow, Governance
  Removed sections: none (first version)
  Templates requiring updates: none (templates are generic, no constitution-specific references)
  Follow-up TODOs: none
-->

# Lever Money Conciliador Constitution

## Core Principles

### I. Cash Reconciliation First (NON-NEGOTIABLE)

Every feature, refactor, and architectural decision MUST serve one goal:
the values settled in Conta Azul (filtered by data de baixa) MUST match
the ML extrato for that same day, to the cent.

No feature ships without proving daily cash reconciliation. If a change
breaks reconciliation, it is reverted — no exceptions.

Acceptance criterion: `abs(soma_CA(dia) - soma_extrato(dia)) < R$0.05`
for every day of the month, using real production data.

### II. Prove Before Building

No code change without a test that proves the problem exists first.
No fix without a test that proves the fix works.

- Write the failing test BEFORE the fix
- Use real data (879 payments Jan/2026, 585 payments Feb/2026, real extratos)
- Synthetic/mock data is acceptable only for unit tests of isolated logic
- Integration tests MUST use the real payment cache and extrato CSVs
  from `testes/data/`

### III. Extrato is Source of Truth for Cash

The ML extrato (account_statement / release_report) is the authoritative
record of what money actually moved. The system's job is to replicate
that reality in Conta Azul.

- Every line in the extrato MUST be accounted for in the system
- No line may be silently skipped without explicit, documented justification
- If the system calculates a value that differs from the extrato,
  the system is wrong until proven otherwise
- `charges_details` is source of truth for fee breakdown per payment
- `date_approved` converted to BRT is source of truth for competencia
- `money_release_date` converted to BRT is source of truth for cash date

### IV. One Fix, One Measurement

Changes MUST be atomic and measurable:

- Fix ONE bug at a time
- Run the daily reconciliation test after EACH fix
- Measure the divergence before and after (in R$)
- If divergence does not decrease, the fix is wrong or incomplete
- Never batch multiple fixes in one commit — each fix gets its own
  commit with the measured impact in the commit message

### V. No Silent Data Loss

The system MUST NOT discard financial data without explicit tracking:

- Every extrato line MUST either: (a) match a processed payment,
  (b) be captured as an expense event, or (c) be explicitly classified
  as an internal movement with zero cash impact
- "Unconditional skip" rules (returning None) MUST be justified:
  the skipped item MUST have zero net cash impact on the seller's account
- If an item has cash impact and is skipped, the reconciliation WILL fail
  and the skip rule MUST be converted to a capture rule

### VI. Dates in BRT, Always

All dates used for financial attribution MUST be in BRT (UTC-3):

- `date_approved` → competencia (DRE) — already uses `_to_brt_date()`
- `money_release_date` → cash date (baixa) — MUST use `_to_brt_date()`
- Extrato CSV dates are in BRT — system dates MUST match
- Any date truncation without timezone conversion is a bug

## Financial Constraints

These are domain rules that MUST NOT be violated. They come from months
of production experience with the ML/MP APIs and Conta Azul integration.

1. **Fee source:** `charges_details` only. Never `fee_details`.
2. **financing_fee:** Net-neutral. MUST be excluded from comissao. Always.
3. **Competencia date:** `_to_brt_date(date_approved)`. Never `date_created`.
4. **CA API is async:** Returns `protocolo`, never `id`.
5. **Baixa date:** MUST NOT be in the future. CA returns 400.
6. **Rate limit:** 600 req/min global for CA API. All calls via rate_limiter.
7. **Token refresh:** CA refresh MUST use asyncio.Lock. Concurrent refresh
   causes `invalid_grant`.
8. **by_admin refunds:** SKIP if never synced (kit split). Process if synced.
9. **marketplace_shipment:** Always skip. Not a sale.
10. **collector_id present:** Always skip. Seller is buying, not selling.

## Development Workflow

### Test Data

Real data lives at `testes/data/`:
- `cache_jan2026/141air_payments.json` — 879 payments
- `cache_fev2026/141air_payments.json` — 585 payments
- `extratos/extrato janeiro 141Air.csv` — 690 lines
- `extratos/extrato fevereiro 141Air.csv` — 592 lines

These files are the golden reference. Tests MUST use them.

### Test Execution

```bash
python3 -m pytest                          # full suite
python3 -m pytest testes/unit/             # unit only
python3 -m pytest testes/integration/      # integration only
```

All tests MUST pass before any commit. Zero tolerance for test failures.

### Commit Convention

```
fix: [scope] - description (divergence: R$X → R$Y)
feat: [scope] - description
```

Include measured divergence impact when fixing reconciliation bugs.

### Key Files (Read Before Modifying)

| File | What it does |
|------|-------------|
| `CLAUDE.md` | Full system documentation |
| `docs/CONHECIMENTO_MERCADO_LIVRE.md` | Domain knowledge (gotchas, formulas) |
| `new levermoney/SDD.md` | Problem statement, test plan, fix plan |
| `app/services/processor.py` | Payment → CA entries |
| `app/services/expense_classifier.py` | Non-order payment classification |
| `app/services/extrato_ingester.py` | Extrato line ingestion |
| `app/services/money.py` | Unified sign convention |
| `app/routers/baixas.py` | Settlement (baixa) creation |

## Governance

This constitution supersedes all other development practices for the
Lever Money project. Any PR or code change MUST comply with these
principles.

**Amendment process:**
1. Propose change with rationale
2. Validate that change does not break daily reconciliation
3. Update this file with new version
4. Commit with message: `docs: amend constitution to vX.Y.Z`

**Compliance review:**
Every code review MUST verify:
- Does this change maintain or improve daily cash reconciliation?
- Are dates converted to BRT?
- Is the extrato source of truth respected?
- Are there new silent skips without justification?
- Is there a test proving the change works with real data?

**Version**: 1.0.0 | **Ratified**: 2026-04-10 | **Last Amended**: 2026-04-10

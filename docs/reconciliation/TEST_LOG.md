# Test Log — Histórico de runs de pytest

**Append-only.** Cada run anexa 1 bloco. Gerado automaticamente por `scripts/run_tests.sh`.

**Regra:** `passed` só sobe OU permanece. Se cair → investigar ANTES de commitar.

---

## 2026-04-16 — baseline pos-cleanup (manual)
**Commit:** 6270cd8 + edits locais (processor, daily_sync, backfill, testes)
**Trigger:** manual

| Métrica | Valor |
|---|---|
| Total | 460 passed, 2 skipped |
| Duração | 1.58s |
| Tags @reconciliation | 0 (ainda não implementado — T-002) |
| Tags @eval_harness | 0 (ainda não implementado — T-001) |
| Tags @money_sign | 0 (ainda não implementado — T-010) |
| Tags @classifier | 0 (ainda não implementado — T-020) |
| Tags @invariants | 0 (ainda não implementado — T-040) |
| Tags @golden | 0 (ainda não implementado — T-030) |

**Observações:** Primeira run após remoção do `test_cash_*` e criação da spec 002. Nenhum teste da nova spec existe ainda — próximo passo é T-001 (eval harness) e T-002 (red test da reconciliação).

---

## Template (gerado automaticamente)
```
## YYYY-MM-DD HH:MM — <contexto curto>
**Commit:** <sha curto>
**Trigger:** manual | ci | pre-commit

| Métrica | Valor | Δ vs anterior |
|---|---|---|
| Total | NNN passed, N failed, N skipped | +N / 0 / 0 |
| Duração | N.NNs | +N.NNs |
| Tags @reconciliation | N/N passed | +N |

**Observações:** o que mudou, se algum teste novo foi adicionado, se algo quebrou, etc.
```

## 2026-04-16 15:01 — -q
**Commit:** 6270cd8
**Trigger:** manual
**Exit code:** 0

| Métrica | Valor |
|---|---|
| Summary |  460 passed, 2 skipped, 12 warnings in 1.54s  |
| Tag | — |


## 2026-04-16 15:09 — -m reconciliation
**Commit:** 6270cd8
**Trigger:** manual
**Exit code:** 1

| Métrica | Valor |
|---|---|
| Summary |  5 failed, 465 deselected, 2 warnings in 2.08s  |
| Tag | @reconciliation |


## 2026-04-16 15:09 — -m eval_harness
**Commit:** 6270cd8
**Trigger:** manual
**Exit code:** 0

| Métrica | Valor |
|---|---|
| Summary |  3 passed, 467 deselected, 2 warnings in 2.55s  |
| Tag | @eval_harness |


## 2026-04-16 15:09 — full suite
**Commit:** 6270cd8
**Trigger:** manual
**Exit code:** 0

| Métrica | Valor |
|---|---|
| Summary |  460 passed, 2 skipped, 8 deselected, 12 warnings in 1.00s  |
| Tag | — |


## 2026-04-16 15:23 — full suite
**Commit:** 6270cd8
**Trigger:** manual
**Exit code:** 0

| Métrica | Valor |
|---|---|
| Summary |  496 passed, 2 skipped, 8 deselected, 12 warnings in 1.79s  |
| Tag | — |


## 2026-04-16 15:23 — -m reconciliation
**Commit:** 6270cd8
**Trigger:** manual
**Exit code:** 1

| Métrica | Valor |
|---|---|
| Summary |  5 failed, 501 deselected, 2 warnings in 2.33s  |
| Tag | @reconciliation |


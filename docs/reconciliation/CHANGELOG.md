# Changelog — Reconciliação

**Append-only.** Cronológico. Anota O QUE foi construído/mudado, não POR QUÊ (isso é DECISIONS.md) nem COMO (isso é git log).

---

## 2026-04-16
### Adicionado
- `specs/002-extrato-reconciliation/spec.md` — invariantes I-1 a I-8, contratos de ID, tolerâncias
- `specs/002-extrato-reconciliation/contracts/reconciliation.yml` — parâmetros travados (tolerances, coverage targets, sign convention, id_mapping, classifier_coverage, stale rule)
- `specs/002-extrato-reconciliation/plan.md` — estratégia SDD + TDD em 6 fases
- `specs/002-extrato-reconciliation/tasks.md` — 11 tasks T-001 a T-051 com red tests definidos
- `docs/reconciliation/DECISIONS.md` — ADR-0001 a ADR-0004
- `docs/reconciliation/ERRORS.md` — ERR-0001 a ERR-0004 (4 bugs descobertos no baseline)
- `docs/reconciliation/RUNS.md` — baseline de reconciliação 141air jan/2026 (56,04% créd / 85,68% déb)
- `docs/reconciliation/TEST_LOG.md` — baseline pytest (460 passed)

### Removido
- `specs/001-daily-cash-reconciliation/` (speckit antigo substituído)
- `testes/integration/test_cash_brt_dates.py`
- `testes/integration/test_cash_coverage.py`
- `testes/integration/test_cash_daily.py`
- `testes/integration/test_cash_fees.py`
- `testes/integration/test_cash_reconciliation.py`
- `testes/integration/test_cash_sign.py`
- `testes/integration/conftest_cash.py`
- `testes/reconcilia_extrato.py` (prototype descartado, vai ser reescrito em scripts/run_reconciliation.py conforme T-001)

### Modificado (contexto: chegada na spec 002)
- `app/services/processor.py` — passa a processar `refunded/by_admin` (kit split) e `cancelled` como receita + estorno (matches ML "Vendas brutas")
- `app/services/daily_sync.py` — `cancelled` passa para processor, só `rejected` é skip
- `app/routers/backfill.py` — whitelist de status adiciona `cancelled`
- `testes/integration/test_processor_integration.py` — 3 testes reescritos refletindo novo comportamento
- `testes/unit/test_daily_sync_unit.py` — `test_skips_rejected_processes_cancelled`

Backfill executado em 141air jan/2026: 26 payments extras processados (+R$ 12.530,58 em sale_approved).

## 2026-04-16 (continuação) — T-010 a T-020 (FASE 2 + FASE 3)

### Adicionado
- `testes/unit/test_expense_classifier_sign.py` (T-010, ERR-0001) — 12 testes assertam sign convention para `deposit`/`transfer_intra`/`transferencia_pix_in`/`entrada_dinheiro`/`transfer_pix`/`pix_enviado`. Inclui parametric invariant lock.
- `testes/integration/test_stale_mp_expenses.py` (T-011, ERR-0002) — 12 testes para detecção pura de `expense_captured` events stale (I-8 invariant).
- `testes/integration/test_cash_movement_per_event.py` (T-012, ERR-0003) — 7 testes confirmam que release/refund groups produzem CashMovements separados nas suas datas próprias.
- `testes/unit/test_category_mapping.py` (T-013, ERR-0004) — 4 testes lockando contrato canônico `bill_payment` ↔ `pagamento_conta`, `transfer_intra` ↔ `transferencia_pix_in`. Idempotência verificada.
- `testes/unit/test_classifier_coverage.py` (T-020) — property test que carrega os 8 CSVs de extrato e exige que nenhum `TRANSACTION_TYPE` real caia no fallback `("other", "expense", None)`.

### Modificado
- `app/services/expense_classifier.py::_is_incoming_transfer` (ERR-0001 fix) — agora também aceita `collector_id` top-level (formato real da API MP, antes só lia `collector.id` aninhado), fold `transferencia_pix_in`/`entrada_dinheiro` na lista incoming, e default incoming quando collector/payer ausentes.
- `app/services/extrato_ingester.py` — adicionados `STALE_EXPENSE_TYPES` e `find_stale_expense_events()` (helper puro para o I-8 invariant).
- `app/services/reconciliation.py`:
  - `events_to_payment_movements()` (ERR-0003 fix) — agora emite 2 grupos por payment: release group (sale + fee + shipping + subsidy na money_release_date) e refund group (refund + refund_fee + refund_shipping na refund event_date).
  - `filter_stale_mp_expenses()` (ERR-0002 fix) — exclui mp_expenses stale antes de construir movements.
  - `_expense_type_to_category` (ERR-0004 + extra) — adicionado mapping `transfer_intra` → `transferencia_pix_in`.

### Runs (via wrappers)
- `run_reconciliation.sh 141air 2026-01`:
  - Antes: 56.04% créd / 85.68% déb / 158 orphan ext / 39 orphan sis
  - Depois: 69.29% créd (+13.25 pp) / 86.77% déb (+1.09 pp) / 53 orphan ext (-105) / 17 orphan sis (-22)
- `run_tests.sh` (full): 460 passed → 496 passed (+36 novos testes)

### Não atingido (próxima iteração)
- Gate 99,5% ainda longe; faltam ajustes de ref_id mapping para PIX/intra-MP (ref_id do extrato ≠ payment_id da API), e backfill de payments faltantes para liberacao/pagamento_qr órfãos.
- T-030 golden snapshots, T-040 ambient invariants e T-050 scale-out continuam pendentes.

---

## 2026-04-16 (continuação) — T-001 + T-002

### Adicionado
- `app/services/reconciliation.py` — engine puro (load extrato/events/expenses + match + compute metrics). Emite `ReconciliationMetrics` dataclass. 420 linhas.
- `scripts/run_reconciliation.py` — CLI thin: `python3 scripts/run_reconciliation.py <seller> <period>` → JSON stdout.
- `testes/e2e/__init__.py` + `testes/e2e/test_eval_harness_exists.py` (T-001) — 3 testes GREEN, todos marcados `@pytest.mark.integration @pytest.mark.e2e @pytest.mark.eval_harness`.
- `testes/e2e/test_reconciliation_141air_jan.py` (T-002) — 5 testes RED (esperado): cobertura créd/déb/orphans/daily_diff comparados contra `contract.yml`. Mensagens de erro mostram gap e top categorias.

### Modificado
- `pyproject.toml` — adicionados markers: `e2e`, `eval_harness`, `reconciliation`, `money_sign`, `stale_data`, `architecture`, `classifier`, `golden`, `invariants`, `tooling`.

### Runs automáticos hoje (via wrappers)
- `run_reconciliation.sh 141air 2026-01` → 56.04% créd, 85.68% déb, 158 orphan ext, 39 orphan sis, daily_diff_max R$ 54.479,07 (dia 2026-01-26 por conta do R$ 53k do ERR-0001)
- `run_tests.sh -m reconciliation` → 5 failed (esperado, RED design)
- `run_tests.sh -m eval_harness` → 3 passed (GREEN)
- `run_tests.sh` (full) → 460 passed, 2 skipped, 8 deselected (os 3 eval_harness + 5 reconciliation são integration-only, não quebram o default)

---

## 2026-04-16 (continuação) — ERR-0014..0018 (extensão para fev/2026)

### Adicionado
- `docs/reconciliation/ERRORS.md` — ERR-0014 a ERR-0018 (5 bugs novos surgidos ao reconciliar 141air fev/2026).
- `docs/reconciliation/dre_141air_2026-02.json` — DRE simulado de fev (receita líquida R$ 116.770,17; resultado operacional R$ 93.655,05).
- `scripts/cleanup_and_reingest_feb.py` — one-shot: apaga rows `:pe:N` stale (pix_enviado mal classificado) + re-ingere Fev CSV.
- `scripts/cleanup_feb_stale.py` — one-shot: apaga linha `143104571692:lc` mal classificada (sinal invertido) + re-ingere Fev CSV.
- `scripts/debug_orphans.py` — helper: dump de orphan extrato/sistema/amount_diff com candidatos sys por ref, consumindo o mesmo pipeline de `reconcile()`.
- `scripts/check_feb_mp_expenses.py`, `scripts/check_specific_refs.py` — diagnostic scripts usados durante iteração (podem ser descartados depois).
- `testes/e2e/test_reconciliation_141air_fev.py` — gate e2e espelhando o de jan (5 asserts contra `reconciliation.yml`).

### Modificado
- `app/services/extrato_ingester.py`:
  - Nova constante `_COMPLEMENTARY_EXPENSE_TYPES` (dispute/reversal family); step (c) do ingester agora só pula ref com expense prévio quando o tipo atual NÃO é complementar (ERR-0016).
  - Nova constante `_SIGN_DRIVEN_EXPENSE_TYPES = {liberacao_cancelada, dinheiro_recebido_cancelado}`; direction é sobrescrito pelo sinal do CSV (ERR-0018).
  - Nova regra `("reembolso de pix enviado", "reembolso_pix_enviado", "income", None)` antes de `("pix enviado", ...)` (ERR-0017).
  - Nova regra `("dinheiro recebido cancelado", "dinheiro_recebido_cancelado", "income", None)` antes de `("dinheiro recebido", _CHECK_PAYMENTS, ...)`.
  - Abreviações adicionadas: `rpe` (reembolso_pix_enviado), `dcc` (dinheiro_recebido_cancelado).
  - Templates de descrição adicionados para os dois novos tipos.

- `app/services/reconciliation.py`:
  - `align_refund_created_with_extrato()` ganha **case 4** (ERR-0014): pids com release+refund events cujo único footprint no extrato é `entrada_dinheiro` (bpp_refunded / Programa de Proteção Mercado Envios Full) têm release + refund movements suprimidos — o `mp_expense entrada_dinheiro` carrega o caixa real.
  - `filter_stale_mp_expenses()` ganha parâmetro opcional `extrato_pids: set[str]`. Rows com `expense_type == "cashback"` passam por 3 cases (ERR-0015):
    1. Duplicata de mp_expense não-cashback no mesmo (ref, amount) → drop.
    2. Ref não aparece no extrato → drop (MP-internal cashback sem cash real).
    3. Caso contrário, mantém (matcher pass 2 faz ref+amount).
  - Call site em `reconcile()` passa `extrato_pids_str` ao filter.

- `scripts/simulate_dre.py` — call site atualizado: `filter_stale_mp_expenses(expenses, approved, extrato_pids_str)`.

### Runs
- `run_reconciliation.sh 141air 2026-02`:
  - Baseline (pré-fix): 83,18% créd / 95,27% déb / 19 orphan ext / 3 orphan sis / daily_diff_max R$ 18.574,41.
  - Final: **100,00% créd / 100,00% déb / 0 orphan ext / 0 orphan sis / daily_diff_max R$ 0,00** (592/592 linhas, 0/28 dias divergentes).
- `run_reconciliation.sh 141air 2026-01` (regressão): **100,00% / 100,00% / 0 / 0 / R$ 0,00** (690/690 linhas).
- `pytest testes/e2e/test_reconciliation_141air_jan.py -m "integration or e2e or reconciliation"` → 5/5 pass.
- Suite de reconciliação completa (50 testes integration+unit) → all green.

### Cleanup efetuado no DB
- 1 row `146365338433:pe:2` (pix_enviado duplicado por missclassificação) deletada via `scripts/cleanup_and_reingest_feb.py`.
- 1 row `143104571692:lc` (liberacao_cancelada com direction=expense quando CSV dizia +) deletada via `scripts/cleanup_feb_stale.py`.
- Re-ingestão criou 27 rows na primeira passada + 2 rows na segunda (liberacao_cancelada com sinal correto + dinheiro_recebido_cancelado).

---

## Template
```
## YYYY-MM-DD
### Adicionado
- path/arquivo.py — descrição curta
### Removido
- path/arquivo.py — descrição curta
### Modificado
- path/arquivo.py — descrição curta
```

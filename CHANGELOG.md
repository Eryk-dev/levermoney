# Changelog

Todas as mudancas relevantes do LeverMoney Conciliador.

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

---

# ============================================================================
#  LEVERMONEY V3 — UNIFIED EVENT LEDGER
#  Arquitetura completamente nova: mp_expenses deprecada.
#  Todas as despesas, caixa e lifecycle vivem no event ledger (payment_events).
# ============================================================================

## [3.0.0] — 2026-03-13

### Resumo

**Major release.** O sistema de despesas (`mp_expenses`) foi completamente migrado para o
Event Ledger (`payment_events`). A tabela `mp_expenses` foi renomeada para
`mp_expenses_deprecated` e substituida por uma view de compatibilidade.

Todas as escritas, leituras, dedup e exports agora usam exclusivamente o event ledger.
520 testes passando. Zero referencias a `mp_expenses` em `app/`.

### O que mudou (visao geral)

O LeverMoney v2 tinha **dois sistemas paralelos**:
- `payment_events` (event ledger) para pagamentos de vendas
- `mp_expenses` (tabela mutavel) para despesas/non-order payments

O LeverMoney v3 **unifica tudo** no event ledger:
- Pagamentos de vendas: 16 event types (inalterado)
- Despesas (non-order): 4 event types (expense_captured/classified/reviewed/exported)
- Caixa (extrato): 6 event types (cash_release/expense/income/transfer_out/transfer_in/internal)
- **Total: 26 event types** em uma unica tabela append-only

### Etapas da migracao (executadas sequencialmente)

| Etapa | Branch | Descricao |
|-------|--------|-----------|
| 1 | ralph/upload-extrato | Upload de extrato CSV via admin + dashboard tab |
| 2 | ralph/cash-events | 6 cash event types + ingestion script + cash reconciliation |
| 3 | ralph/dual-write | Dual-write: despesas gravadas em mp_expenses E event ledger |
| 4 | ralph/migrar-leituras | Feature flag + todos consumers leem do ledger |
| 5 | ralph/deprecar-mp-expenses | Stop dual-write, remove refs, migration 009 |

### Breaking Changes

- **`mp_expenses` e agora uma VIEW**, nao uma tabela. A view le de `payment_events`
  WHERE `event_type = 'expense_captured'` e mapeia metadata para colunas esperadas.
  A tabela original foi renomeada para `mp_expenses_deprecated` (backup).
- **Feature flag `expenses_source` removida** de `app/config.py`. Nao ha mais toggle —
  ledger e a unica fonte.
- **Dual-write removido.** `expense_classifier.py`, `extrato_ingester.py`,
  `release_report_sync.py` e `export.py` agora escrevem APENAS no event ledger
  via `record_expense_event()`.
- **Dedup de despesas migrado.** `daily_sync.py` e `onboarding_backfill.py` usam
  `payment_events WHERE event_type = 'expense_captured'` em vez de `mp_expenses`.

### Migrations novas

| Migration | Descricao |
|-----------|-----------|
| `007_extrato_uploads.sql` | Tabela `extrato_uploads` para uploads CSV |
| `008_unified_ledger.sql` | Coluna `reference_id` + indexes para cash events |
| `009_deprecate_mp_expenses.sql` | Rename table + create compatibility view |

### Novos event types (10)

| event_type | signed_amount | Origem | Descricao |
|-----------|---------------|--------|-----------|
| `expense_captured` | any | expense_classifier, extrato_ingester, release_report_sync | Despesa identificada |
| `expense_classified` | zero | expense_classifier | Auto-classificada (ca_category) |
| `expense_reviewed` | zero | expenses/crud.py | Revisada por humano |
| `expense_exported` | zero | expenses/export.py | Exportada em batch |
| `cash_release` | positive | extrato ingestion | Liberacao no extrato |
| `cash_expense` | negative | extrato ingestion | Despesa no extrato |
| `cash_income` | positive | extrato ingestion | Receita no extrato |
| `cash_transfer_out` | negative | extrato ingestion | Transferencia saida |
| `cash_transfer_in` | positive | extrato ingestion | Transferencia entrada |
| `cash_internal` | any | extrato ingestion | Movimento interno (reserva, saldo) |

### Novas funcoes no event_ledger.py

| Funcao | Descricao |
|--------|-----------|
| `record_expense_event()` | Grava expense lifecycle event com idempotency `{seller}:{pid}:{type}` |
| `record_cash_event()` | Grava cash event com idempotency `{seller}:{ref}:{type}:{date}:{abbrev}` |
| `derive_expense_status()` | Deriva status de despesa a partir de event_types |
| `get_pending_exports()` | Despesas com expense_captured sem expense_exported |
| `get_expense_list()` | Lista despesas com status derivado |
| `get_expense_stats()` | Estatisticas agregadas de despesas |
| `get_cash_summary()` | Soma de cash events por tipo e periodo |

### Arquivos modificados (Etapa 5 — deprecacao final)

- **`expense_classifier.py`** — removido upsert a `mp_expenses`. Apenas `record_expense_event()`
- **`extrato_ingester.py`** — removido insert a `mp_expenses`. Apenas `record_expense_event()`
- **`release_report_sync.py`** — removido insert a `mp_expenses`. Apenas `record_expense_event()`
- **`expenses/export.py`** — removido update de status em `mp_expenses`. Apenas `expense_exported` events
- **`expenses/crud.py`** — leitura via `get_expense_list()`/`get_expense_stats()` (sem feature flag)
- **`expenses/closing.py`** — leitura via event ledger
- **`financial_closing.py`** — `_compute_manual_lane()` le do event ledger
- **`extrato_coverage_checker.py`** — `_lookup_expense_ids()` le do event ledger
- **`daily_sync.py`** — dedup de despesas via `payment_events`
- **`onboarding_backfill.py`** — dedup de despesas via `payment_events`
- **`app/config.py`** — `expenses_source` removido

### Scripts de migracao

- **`testes/migrate_mp_expenses_to_ledger.py`** — migra dados historicos de
  `mp_expenses_deprecated` para event ledger. Idempotente. Cria expense_captured,
  expense_classified, expense_exported e expense_reviewed conforme status original.

### Testes

- 520 testes passando (~4.5s, offline)
- Novos test files: `test_cash_events.py`, `test_expense_classifier_dual_write.py`,
  `test_extrato_ingester_dual_write.py`, `test_release_report_dual_write.py`,
  `test_export_dual_write.py`, `test_expense_read_helpers.py`,
  `test_crud_export_ledger_mode.py`, `test_feature_flag_expenses_source.py`,
  `test_parity_expenses_source.py`

### Tabela completa de Event Types (v3 — 26 tipos)

| event_type | signed_amount | Origem |
|-----------|---------------|--------|
| `sale_approved` | +amount bruto | processor |
| `fee_charged` | -mp_fee | processor |
| `shipping_charged` | -shipping | processor |
| `subsidy_credited` | +subsidio | processor |
| `refund_created` | -refund | processor |
| `refund_fee` | +fee estornada | processor |
| `refund_shipping` | +frete estornado | processor |
| `partial_refund` | -refund parcial | processor |
| `ca_sync_completed` | 0 | ca_worker |
| `ca_sync_failed` | 0 | ca_worker |
| `money_released` | 0 | release_checker |
| `mediation_opened` | 0 | processor |
| `charged_back` | -amount | processor |
| `reimbursed` | +amount | processor |
| `adjustment_fee` | -diff fee | release_report_validator |
| `adjustment_shipping` | -diff shipping | release_report_validator |
| `expense_captured` | any | expense_classifier, extrato_ingester, release_report_sync |
| `expense_classified` | zero | expense_classifier |
| `expense_reviewed` | zero | expenses/crud |
| `expense_exported` | zero | expenses/export |
| `cash_release` | positive | extrato ingestion |
| `cash_expense` | negative | extrato ingestion |
| `cash_income` | positive | extrato ingestion |
| `cash_transfer_out` | negative | extrato ingestion |
| `cash_transfer_in` | positive | extrato ingestion |
| `cash_internal` | any | extrato ingestion |

### Como rodar a migracao de dados historicos

```bash
# 1. Aplicar migration 009 (via Supabase MCP ou SQL editor)
# 2. Rodar script de migracao de dados:
python3 testes/migrate_mp_expenses_to_ledger.py --seller 141air
python3 testes/migrate_mp_expenses_to_ledger.py --seller net-air
python3 testes/migrate_mp_expenses_to_ledger.py --seller netparts-sp
python3 testes/migrate_mp_expenses_to_ledger.py --seller easypeasy

# 3. Validar contagens:
# SELECT seller_slug, COUNT(*) FROM mp_expenses GROUP BY seller_slug
# deve retornar mesmos valores que antes (via view)
```

---

# ============================================================================
#  LEVERMONEY V2 — EVENT LEDGER (PAYMENTS)
#  Historico da v2: event ledger para pagamentos, testes, DRE, validacao.
# ============================================================================

## [2.4.0] — 2026-03-12

### Resumo
Fase 2.5 completa: validacao de cobertura total do extrato 141Air janeiro/2026.
690/690 linhas cobertas (100%), 0 gaps, R$ 0,00 gap amount.

### Validacao Extrato Completo
- **Baseline**: 535/690 (77.5%) — apenas order-based payments cobriam o extrato
- **Classifier**: 78 non-order payments classificados em mp_expenses (boletos, transfers, subscriptions, cashback)
- **Extrato ingester**: 235 linhas de gap ingeridas (disputas, DIFAL, faturas, liberacoes nao-sync, QR/PIX nao-sync)
- **Cobertura final**: 50 skips + 330 payment_events + 310 mp_expenses = 690 (100%)
- **3 amount updates**: IOF correction em subscriptions (valor extrato != valor API)

### Migracao
- `mp_expenses.payment_id`: bigint → text (suporte a composite keys como "123456:dd")

### Scripts de Validacao
- `testes/validate_full_coverage.py` — medicao de cobertura vs Supabase (READ-ONLY)
- `testes/classify_non_orders.py` — classifica non-order payments do cache → mp_expenses
- `testes/ingest_extrato_gaps.py` — ingere gaps do extrato CSV → mp_expenses

---

## [2.3.0] — 2026-03-12

### Resumo
Fase 2 completa: backfill real 141air janeiro/2026 executado com sucesso em producao.
439 payments processados, 0 erros, 1441 CA jobs completados. DRE gap = R$ 0,00 em
todas as linhas — match exato com valores de referencia offline.

### Validacao em Producao
- **Backfill 141air jan/2026**: 486 payments ML → 439 processaveis (47 filtrados)
- **Event ledger**: 1441 eventos gravados (438 sale_approved, 435 fee_charged, 362 shipping_charged, 77 refund_created, 75 refund_fee, 54 refund_shipping)
- **CA queue**: 1441 jobs → todos completed (438 receita, 435 comissao, 362 frete, 77 estorno, 75 estorno_taxa, 54 estorno_frete)
- **DRE validado**: receita bruta R$ 179.572,25, comissao R$ 23.085,97, frete R$ 8.946,37, devolucoes R$ 45.375,41, resultado operacional R$ 109.555,37
- **Release report sync**: 6 expenses ingeridos

### Known Issues
- 272/438 payments sem `ca_sync_completed` event (race condition no backfill concorrente com concurrency=5; nao afeta dados financeiros)

---

## [2.2.0] — 2026-03-12

### Resumo
Fase 1 completa: 84 testes de integracao cobrindo funcoes async do event ledger,
orquestracao do processor (state machine com 8 caminhos), ca_queue (enqueue idempotencia
e group completion), e daily_sync (sync window, dedup, filtros, status change detection).
366 testes passando, ~2.5s, offline.

### Added
- **`test_event_ledger_integration.py`** — 26 testes das funcoes async com mock DB:
  `record_event` (success, idempotent skip, DB error, validation), `get_events`,
  `get_balance` (com/sem date filter), `get_dre_summary` (paginacao), `get_processed_payment_ids`
  (boundary 1000 rows, 999 sem segunda pagina), `get_payment_statuses` (derive + paginacao).
- **`test_processor_integration.py`** — 25 testes de orquestracao com mock services:
  state machine (8 caminhos: approved, already processed, partially_refunded, refunded new/existing,
  charged_back+reimbursed, by_admin com/sem existing), filtros (6: no order, marketplace_shipment,
  collector_id, cancelled, missing CA config, seller not found), subsidio, partial refund
  idempotency (re-run skips processed), refund estornos, error handling (EventRecordError nao aborta).
- **`test_ca_queue_unit.py`** — 13 testes: enqueue success/idempotency/error propagation,
  convenience wrappers (prioridades, endpoints, idempotency keys), CaWorker._check_group_completion
  (all done → ca_sync_completed, dead → ca_sync_failed, pending → no action, EventRecordError caught).
- **`test_daily_sync_unit.py`** — 20 testes: `_parse_date_yyyy_mm_dd` (5 cases),
  `_compute_sync_window` (lookback, cursor extends, cursor within lookback, begin clamped, missing cursor),
  `sync_seller_payments` (dedup, skip cancelled, skip marketplace_shipment, skip collector,
  new order processing, status change reprocess, already synced skip, classifier mode,
  legacy mode defers, queued status reprocess).

---

## [2.1.0] — 2026-03-12

### Resumo
Estabilizacao completa do Event Ledger (Fase 0). Diagnostico com 4 agentes especializados
identificou 3 bugs criticos, 4 dividas arquiteturais — todos resolvidos. Queries diretas
eliminadas, adjustment events adicionados, float vs Decimal avaliado e resolvido.
282 testes passando.

### Fixed
- **Bug `len(payments)` em `queue.py:185`** — causava `NameError` em runtime no endpoint
  de reconciliacao. Corrigido para `len(events_by_pid)` → depois para `len(payment_statuses)`.
- **Status derivation inconsistente** — `daily_sync.py` checava `ca_sync_completed` antes
  de `ca_sync_failed` (order errada). Usava "pending" como fallback enquanto outros usavam
  "unknown". Agora todos usam `derive_payment_status()` centralizada.

### Changed
- **`event_ledger.record_event()` nao engole mais excecoes** — erros de DB agora levantam
  `EventRecordError`. Callers (`processor.py`, `ca_queue.py`, `release_checker.py`) capturam
  `EventRecordError` especificamente e logam em nivel `ERROR` (antes: `Exception` generico + `WARNING`).
- **`get_dre_summary()` agora paginado** — loop com page_limit=1000, elimina risco de
  truncamento silencioso pelo PostgREST para sellers com >1000 eventos/mes.
- **Status derivation centralizada** — nova funcao `derive_payment_status(event_types)`
  em `event_ledger.py`. Removidas 3 copias duplicadas em `daily_sync.py`,
  `financial_closing.py`, `queue.py`. Prioridade: error > refunded > synced > queued > unknown.
- **Queries diretas a `payment_events` eliminadas** — `financial_closing._compute_auto_lane()`
  e `queue.queue_reconciliation()` agora usam `event_ledger.get_payment_statuses()`.
  Ambos ficaram async-corretos e mais enxutos (~20 linhas removidas cada).
- **`release_report_validator.py`** agora grava eventos `adjustment_fee` / `adjustment_shipping`
  no ledger quando cria CA jobs de ajuste. Antes esses ajustes nao entravam no ledger.

### Added
- **`derive_payment_status()`** em `event_ledger.py` — funcao pura, mapeia set de
  event_types para status string.
- **`get_payment_statuses()`** em `event_ledger.py` — retorna `{payment_id: status}`
  para um seller, com filtro opcional por competencia_date. Paginado.
- **`EventRecordError`** em `event_ledger.py` — excecao especifica para falhas de escrita
  no ledger (distingue de idempotency skip que retorna `None`).
- **Event types `adjustment_fee` e `adjustment_shipping`** — negativos, gravados pelo
  release_report_validator com metadata (processor_fee vs release_fee).
- **19 novos testes** em `test_event_ledger.py`:
  - `TestDerivePaymentStatus` (14 testes) — prioridade de status, edge cases, full lifecycle
  - `TestEventRecordError` (2 testes) — tipo e mensagem
  - `TestValidateEvent` (+3 testes) — adjustment_fee/shipping validation

### Tabela de Event Types (atualizada)

| event_type | signed_amount | Origem |
|-----------|---------------|--------|
| `sale_approved` | +amount bruto | processor |
| `fee_charged` | -mp_fee | processor |
| `shipping_charged` | -shipping | processor |
| `subsidy_credited` | +subsidio | processor |
| `refund_created` | -refund | processor |
| `refund_fee` | +fee estornada | processor |
| `refund_shipping` | +frete estornado | processor |
| `partial_refund` | -refund parcial | processor |
| `ca_sync_completed` | 0 | ca_worker |
| `ca_sync_failed` | 0 | ca_worker |
| `money_released` | 0 | release_checker |
| `mediation_opened` | 0 | processor |
| `charged_back` | -amount | processor |
| `reimbursed` | +amount | processor |
| `adjustment_fee` | -diff fee | release_report_validator |
| `adjustment_shipping` | -diff shipping | release_report_validator |

---

## [2.0.0] — 2026-03-12

### Resumo
Migracao arquitetural: tabela `payments` deixa de ser fonte de dados. Toda leitura
e escrita de estado de pagamentos agora passa pelo **Event Ledger** (`payment_events`),
um log append-only e imutavel. Zero referencias a `db.table("payments")` restam em `app/`.
263 testes passando.

### Breaking Changes
- **`payments` table nao e mais escrita** — `_upsert_payment()` removida do processor.
  `ca_queue._check_group_completion()` nao atualiza mais `payments.status`.
  Consumidores que dependiam de `payments` devem usar `event_ledger`.
- **Status derivado de eventos** — nao existe mais campo `status` em payments.
  Status e derivado: `sale_approved` → queued, `ca_sync_completed` → synced,
  `refund_created` → refunded, `ca_sync_failed` → error.
- **`fee_adjusted` movido** — flag agora vive em `release_report_fees` (antes em `payments`).

### Added
- **`event_ledger.py`** — 3 novas funcoes de consulta:
  - `get_processed_payment_ids(seller, event_type)` — set paginado de payment_ids
  - `get_processed_payment_ids_in(seller, ids, event_type)` — batch lookup em chunks de 100
  - `get_payment_fees_from_events(seller, ids)` — reconstroi fee/shipping de eventos
- **`testes/test_event_ledger.py`** (40 testes) — pure functions do event ledger
- **`testes/test_event_ledger_backfill.py`** (25 testes) — validacao DRE via eventos jan+fev 2026
- **`testes/test_dre_reconciliation_fev2026.py`** (46 testes) — DRE fevereiro 2026

### Changed (12 arquivos migrados)
- **`processor.py`** — removida `_upsert_payment()`. `process_payment_webhook()` usa
  `event_ledger.get_events()` para checar estado existente. `sale_approved` metadata
  inclui `ml_status`, `status_detail`, `money_release_date` para status change detection.
- **`ca_queue.py`** — `_check_group_completion()` nao escreve mais em `payments`.
  Apenas grava eventos `ca_sync_completed` / `ca_sync_failed` via event_ledger.
- **`daily_sync.py`** — already-done set e status change detection leem de `payment_events`
  em vez de `payments`. Status derivado de event_types.
- **`financial_closing.py`** — `_compute_auto_lane()` le `payment_events` e agrupa
  por payment_id para derivar status (synced/queued/refunded/error).
- **`release_report_validator.py`** — fees via `event_ledger.get_payment_fees_from_events()`.
  `fee_adjusted` flag agora em `release_report_fees` table.
- **`onboarding_backfill.py`** — `_load_already_done()` agora async, usa
  `event_ledger.get_processed_payment_ids()`.
- **`backfill.py` (router)** — already-done via event_ledger. Missing-fees via eventos.
- **`extrato_coverage_checker.py`** — `_lookup_payment_ids()` agora async, usa event_ledger.
- **`extrato_ingester.py`** — `_batch_lookup_payment_ids()` e `_batch_lookup_refunded_payment_ids()`
  agora async, delegam para event_ledger.
- **`release_checker.py`** — `_preload()` le `payment_events` (money_released + sale_approved metadata).
  `_recheck_ml_api()` grava evento `money_released` em vez de atualizar `payments`.
- **`release_report_sync.py`** — `_lookup_existing_ids()` agora async, usa event_ledger.
- **`queue.py` (router)** — reconciliation view le `payment_events` para derivar status.

### Tabela de Event Types

| event_type | signed_amount | Origem |
|-----------|---------------|--------|
| `sale_approved` | +amount bruto | processor |
| `fee_charged` | -mp_fee | processor |
| `shipping_charged` | -shipping | processor |
| `subsidy_credited` | +subsidio | processor |
| `refund_created` | -refund | processor |
| `refund_fee` | +fee estornada | processor |
| `refund_shipping` | +frete estornado | processor |
| `partial_refund` | -refund parcial | processor |
| `ca_sync_completed` | 0 | ca_worker |
| `ca_sync_failed` | 0 | ca_worker |
| `money_released` | 0 | release_checker |
| `mediation_opened` | 0 | processor |
| `charged_back` | -amount | processor |
| `reimbursed` | +amount | processor |

---

## [1.1.0] — 2026-03-12

### Resumo
Reconstrucao da fundacao de testes e validacao. 152 testes automatizados offline
com dados reais (141air, Janeiro 2026). Correcao de categoria CA para vendas
mercadopago. Reorganizacao do diretorio `testes/`. Todos os valores financeiros
validados contra extrato ML.

### Added
- **Suite de testes pytest** — 152 testes offline, ~1.3s, sem Supabase/API
  - `testes/test_processor_unit.py` (31 testes) — matematica do processor:
    `_to_float`, `_to_brt_date`, `_extract_processor_charges`,
    `_compute_effective_net_amount`, estornos granulares, payload builders
  - `testes/test_extrato_classification.py` (73 testes) — parsing CSV,
    classificacao de linhas, smart skip, expense builder, cobertura de tipos,
    extratos reais com zero linhas nao classificadas
  - `testes/test_dre_reconciliation.py` (48 testes) — DRE completo com dados reais:
    receita (1.1.1 + 1.1.2), comissao, frete, devolucoes, estornos de taxa e frete,
    balanco por payment, consistencia DRE, match extrato 289/289, gap R$0,00,
    datas de competencia
  - `testes/conftest.py` — fixtures compartilhados: 7 payments reais, extrato CSV,
    seller config, `collect_ignore_glob` para excluir scripts standalone
- **`pyproject.toml`** — configuracao pytest (testpaths, markers, addopts)
- **`PLANO_FUNDACAO.md`** — plano persistente de reconciliacao entre sessoes
- **`CHANGELOG.md`** — este arquivo
- Categoria CA `venda_ecommerce` (1.1.2 Loja Propria) em `sellers.py`

### Changed
- **processor.py** — `_process_approved()` agora seleciona categoria CA baseada em
  `order.type`: `mercadolibre` → 1.1.1, `mercadopago` → 1.1.2. Descricao do
  lancamento tambem diferencia ("Venda ML" vs "Venda MP")
- **extrato_ingester.py** — 6 novas regras de classificacao para transacoes
  de fevereiro 2026: pix recebido, compra mercado livre, dinheiro reservado/retirado
  renda, aprovacao dinheiro express, dinheiro reservado (transferencias internas)
- **docs/TESTES.md** — reescrito com documentacao completa da suite de testes,
  DRE de referencia, tabela de regras validadas, explicacao do gap ML vs processor
- **CLAUDE.md** — referencia de testes atualizada
- **Diretorio `testes/` reorganizado:**
  - `testes/data/` — cache JSON + extratos CSV (antes na raiz)
  - `testes/standalone/` — scripts de teste que nao sao pytest
  - `testes/simulacoes/` — simulacoes offline (7 scripts)
  - `testes/utils/` — rebuild_cache.py
  - `testes/reports/` — relatorios gerados
  - Testes pytest permanecem na raiz de `testes/`
  - `conftest.py` atualizado: `collect_ignore_glob = ["standalone/**", "simulacoes/**", "utils/**"]`

### Valores de referencia validados (141air, Janeiro 2026)

```
879 payments no cache → 438 processaveis (apos filtros)

RECEITA BRUTA                              R$ 179.572,25
  1.1.1 Vendas ML (mercadolibre)           R$ 179.512,35
  1.1.2 Loja Propria (mercadopago)         R$      59,90
DEDUCOES
  1.2.1 Devolucoes (77)                   (R$  45.375,41)
OUTRAS RECEITAS
  1.3.4 Estornos de Taxas (75)             R$   5.948,66
  1.3.7 Estorno de Frete (54)              R$   1.442,21
RECEITA LIQUIDA                            R$ 141.587,71
DESPESAS VARIAVEIS
  2.8.2 Comissoes Marketplace (435)       (R$  23.085,97)
  2.9.4 Frete MercadoEnvios (362)         (R$   8.946,37)
RESULTADO OPERACIONAL                      R$ 109.555,37

Extrato: 690 linhas, 289/289 liberacoes match, gap = R$ 0,00
```

### Regras validadas por testes

| Regra | Detalhe |
|-------|---------|
| Competencia = `date_approved` BRT | 0 payments mudam de mes entre UTC-4 e BRT |
| Filtro `collector.id` | 6 payments excluidos (compras, nao vendas) |
| Filtro `marketplace_shipment` | 16 excluidos |
| Filtro `by_admin` | 2 excluidos (kit split) |
| `financing_fee` excluida | Net-neutral, nao gera despesa |
| Coupon `from=ml` excluido | ML paga, nao o seller |
| Frete nunca negativo | `max(0, shipping_collector - shipping_amount)` |
| Estorno taxa so em refund total | Parcial nao estorna |
| Devolucao capped em amount | `min(refund, transaction_amount)` |
| Balanco por payment | `amount - fee - ship = net` para 438 payments |

---

## [1.0.0] — 2026-03-06

### Resumo
Versao inicial do sistema completo em producao.

### Features em producao
- Pipeline completo: ML payment → processor → CA queue → Conta Azul
- Daily sync automatico (00:01 BRT, D-1 a D-3)
- Nightly pipeline com 9 etapas sequenciais
- Classificacao automatica de non-order payments (mp_expenses)
- Release report sync (payouts, cashback, shipping)
- Extrato ingester (DIFAL, faturas ML, disputes)
- Financial closing (auto + manual lanes)
- Onboarding self-service de sellers com backfill historico
- Dashboard React (faturamento, metas, admin panel)
- Expenses export ZIP + backup Google Drive assincrono
- Legacy daily export (XLSX para CA)
- pending_ca status para sellers sem config CA
- OAuth2 para ML e CA com token rotation
- Rate limiter para CA API (9 req/s, 540 req/min)

### Sellers ativos
- 141air, net-air, netparts-sp, easypeasy

---

## Como rodar testes

```bash
cd "/Volumes/SSD Eryk/LeverMoney"

# Todos os testes (520, ~4.5s)
python3 -m pytest

# Com output detalhado
python3 -m pytest -v --tb=long

# Apenas DRE reconciliation
python3 -m pytest testes/test_dre_reconciliation.py -v

# Apenas event ledger
python3 -m pytest testes/test_event_ledger.py -v

# Apenas cash events
python3 -m pytest testes/test_cash_events.py -v

# Apenas expense dual-write / ledger mode
python3 -m pytest testes/test_expense_classifier_dual_write.py testes/test_crud_export_ledger_mode.py -v
```

Testes usam dados reais (cache JSON + extratos CSV), nao mocks.
Rodam offline em ~4.5 segundos, sem necessidade de Supabase ou API.

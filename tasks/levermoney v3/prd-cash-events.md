# PRD: Etapa 2 — Cash Events no Event Ledger (Fase 1)

## 1. Introduction/Overview

Adicionar uma camada de caixa ao event ledger: cada linha do extrato CSV vira um evento `cash_*` imutavel em `payment_events`. Permite reconciliacao automatica: `sum(cash events) == extrato`.

**Spec tecnica completa:** `rfcs/plano-execucao/03-fase1-cash-events.md`
**Correcoes obrigatorias:** `rfcs/plano-execucao/02-correcoes-pre-implementacao.md`

## 2. Goals

- 690 cash events criados para 141Air jan/2026 (1 por linha do extrato)
- `sum(signed_amount)` dos cash events == R$ -3.385,83 (NET movement)
- Reconciliacao dia a dia com zero gaps
- Idempotencia total (re-run nao cria duplicatas)
- DRE por competencia inalterado (cash events NAO contaminam queries existentes)

## 3. User Stories

### US-001: Schema migration + novos event types

**Description:** Como sistema, preciso da coluna `reference_id` em `payment_events`, dos 6 novos event types `cash_*`, e da protecao de queries existentes contra contaminacao.

**Acceptance Criteria:**
- [ ] Arquivo `migrations/008_unified_ledger.sql` criado com: `reference_id TEXT`, backfill, `ml_payment_id DEFAULT 0`, indexes (`idx_pe_seller_ref`, `idx_pe_cash_events`)
- [ ] Migration aplicada no Supabase
- [ ] Verificacao: `SELECT reference_id FROM payment_events LIMIT 1` retorna valor (backfill funcionou)
- [ ] 6 novos event types adicionados ao `EVENT_TYPES` em `event_ledger.py`: `cash_release`, `cash_expense`, `cash_income`, `cash_transfer_out`, `cash_transfer_in`, `cash_internal`
- [ ] Sign `"any"` suportado em `validate_event()` (para `cash_internal`)
- [ ] `get_dre_summary()` filtrada: exclui eventos com prefixo `cash_` e `expense_`
- [ ] `get_payment_statuses()` filtrada: exclui eventos com prefixo `cash_` e `expense_`
- [ ] Parametro `reference_id: str | None = None` adicionado a `record_event()`
- [ ] `python3 -m pytest` passa (152+ testes, DRE inalterado)

### US-002: record_cash_event() + get_cash_summary()

**Description:** Como sistema, preciso de funcoes para gravar cash events com idempotency key correta (incluindo abbreviation) e consultar sumario de caixa por periodo.

**Acceptance Criteria:**
- [ ] Funcao `record_cash_event(seller_slug, reference_id, event_type, signed_amount, event_date, extrato_type, expense_type_abbrev, metadata)` criada em `event_ledger.py`
- [ ] Idempotency key: `{seller}:{ref_id}:{event_type}:{date}:{abbrev}` (5 partes)
- [ ] `ml_payment_id` preenchido via `int(reference_id)` (fallback 0 para nao-numerico)
- [ ] `competencia_date = event_date` (documentado no docstring)
- [ ] Funcao `get_cash_summary(seller_slug, date_from, date_to)` com paginacao `.range()`
- [ ] Retorna dict: `{"cash_release": 12345.67, "cash_expense": -1234.56, ...}`
- [ ] Testes criados em `testes/test_cash_events.py`:
  - `test_cash_event_types_exist` — 6 tipos no EVENT_TYPES
  - `test_validate_cash_any_sign` — cash_internal aceita + e -
  - `test_record_cash_event_idempotency_includes_date`
  - `test_record_cash_event_idempotency_includes_abbrev`
  - `test_no_collision_different_types_same_ref`
  - `test_get_dre_summary_excludes_cash`
  - `test_get_payment_statuses_excludes_cash`
  - `test_get_cash_summary`
  - `test_skip_mapping_not_all_internal`
- [ ] `python3 -m pytest` passa (todos os testes novos + 152 existentes)

### US-003: Script de ingesta do extrato para cash events

**Description:** Como operador, preciso de um script que le o extrato CSV e cria cash events no ledger, com mapeamento correto de tipos e validacao de invariante.

**Acceptance Criteria:**
- [ ] Script `testes/ingest_extrato_to_ledger.py` criado
- [ ] Aceita: `--seller 141air --month jan2026 [--dry-run]`
- [ ] Mapeamento `CASH_TYPE_MAP` com 17 entries (sem `deposito_avulso` ou `cashback`)
- [ ] Mapeamento `SKIP_TO_CASH_TYPE` com 13 entries (per-rule, NAO blanket cash_internal):
  - PIX enviado/transferencia PIX/pagamento de conta → `cash_transfer_out`
  - Compra mercado libre/livre/compra de → `cash_expense`
  - Transferencia de saldo/dinheiro reservado/renda → `cash_internal`
- [ ] `SKIP_ABBREV` com 13 entries (abbreviations para skips)
- [ ] Chaves com acentos tratadas: `"transferência enviada"` e `"transferência de saldo"`
- [ ] Dry-run: apenas conta e valida, nao grava
- [ ] Dry-run output mostra: total lines, contagem por cash event type, SUM
- [ ] Execucao real para 141air jan2026:
  - 690 cash events criados
  - Verificacao SQL: `SELECT COUNT(*) FROM payment_events WHERE seller_slug = '141air' AND event_type LIKE 'cash_%'` retorna 690
  - Verificacao SQL: `SELECT ROUND(SUM(signed_amount)::numeric, 2) FROM payment_events WHERE seller_slug = '141air' AND event_type LIKE 'cash_%'` retorna -3385.83
- [ ] Re-run idempotente: `"0 newly ingested, 690 already exist"`
- [ ] DRE verificacao: `SELECT event_type, ROUND(SUM(signed_amount)::numeric, 2) FROM payment_events WHERE seller_slug = '141air' AND event_type NOT LIKE 'cash_%' AND event_type NOT LIKE 'expense_%' AND competencia_date BETWEEN '2026-01-01' AND '2026-01-31' GROUP BY event_type` retorna mesmos valores de antes da ingesta
- [ ] `python3 -m pytest` passa

## 4. Functional Requirements

- FR-1: Cada linha do extrato CSV gera exatamente 1 cash event no ledger
- FR-2: Cash events usam `event_date` (data de caixa), nao competencia real
- FR-3: Sign do `signed_amount` segue o sinal do extrato (ja correto no CSV)
- FR-4: Idempotency key inclui abbreviation para prevenir colisao
- FR-5: `get_dre_summary()` NUNCA retorna cash events (filtro por prefixo)
- FR-6: `get_payment_statuses()` NUNCA retorna cash events
- FR-7: Nenhum evento existente (sale_approved, fee_charged etc.) e alterado

## 5. Non-Goals

- Nao modificar processor.py, daily_sync.py, ca_queue.py, ou mp_expenses
- Nao alterar event types existentes (16 tipos originais intocados)
- Nao criar logica de export/CA para cash events
- Nao fazer sum(signed_amount) sem filtro de event_type (risco de double-counting)

## 6. Technical Considerations

- Reference IDs do extrato MP sao SEMPRE numericos (payment_id, operation_id, withdrawal_id). O `ml_payment_id=0` e rarissimo.
- A colisao de idempotency key sem abbreviation e real: "dinheiro retido" e "debito por divida" podem ter mesmo ref_id, mesmo dia, mesmo cash event type (`cash_expense`).
- O `competencia_date` para cash events NAO e a competencia real — e a data de caixa. DRE queries devem excluir cash events.

## 7. Success Metrics

- 690 cash events, sum == R$ -3.385,83
- Zero gaps dia a dia
- 0 duplicatas em re-run
- 152+ testes existentes + 9 novos = 161+ passando
- DRE inalterado

## 8. Open Questions

- Nenhuma (todas as questoes de design foram resolvidas na RFC-002 e auditoria)

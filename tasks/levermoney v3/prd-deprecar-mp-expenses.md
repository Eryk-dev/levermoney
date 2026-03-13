# PRD: Etapa 5 — Deprecar mp_expenses (Fase 4)

## 1. Introduction/Overview

Eliminar `mp_expenses` como tabela ativa. Parar dual-write, migrar dados historicos, remover todas as referencias no codigo. O event ledger se torna a unica fonte de verdade para despesas.

**Spec tecnica completa:** `rfcs/plano-execucao/06-fase4-deprecar-mp-expenses.md`

**Pre-requisito:** Etapa 4 completa e estavel em producao por 2+ meses.

## 2. Goals

- `grep -r "mp_expenses" app/` retorna ZERO resultados
- Dados historicos migrados para o ledger
- Tabela `mp_expenses` renomeada para `mp_expenses_deprecated` com view de compatibilidade
- Codigo simplificado (sem branches old/new, sem feature flag)

## 3. User Stories

### US-001: Parar dual-write nos services produtores

**Description:** Como sistema, preciso que `expense_classifier.py`, `extrato_ingester.py` e `release_report_sync.py` PAREM de escrever em mp_expenses e gravem APENAS no event ledger.

**Acceptance Criteria:**
- [ ] `expense_classifier.py`: removido upsert em mp_expenses. Apenas `record_expense_event()`.
- [ ] `extrato_ingester.py`: removido upsert em mp_expenses (todos os pontos de insercao). Apenas `record_expense_event()`.
- [ ] `release_report_sync.py`: removido insert em mp_expenses para payouts/cashback/shipping. Apenas `record_expense_event()`.
- [ ] `expenses/export.py`: removido update de status em mp_expenses. Apenas `expense_exported` events.
- [ ] Daily sync para 141air funciona: novas despesas aparecem no ledger, NAO em mp_expenses
- [ ] Export ZIP funciona: le do ledger (Etapa 4 ja migrou)
- [ ] `python3 -m pytest` passa

### US-002: Migrar dados historicos

**Description:** Como sistema, preciso que todos os registros existentes em mp_expenses tenham correspondentes no event ledger.

**Acceptance Criteria:**
- [ ] Script `testes/migrate_mp_expenses_to_ledger.py` criado
- [ ] Para cada mp_expense sem `expense_captured` correspondente:
  - Grava `expense_captured` com signed_amount e metadata completo
  - Se `auto_categorized`: grava `expense_classified`
  - Se `status == 'exported'`: grava `expense_exported` com `batch_id: "legacy_migration"`
  - Se `status == 'manually_categorized'`: grava `expense_reviewed`
- [ ] Script e idempotente (re-run nao cria duplicatas)
- [ ] Validacao SQL por seller:
  ```sql
  SELECT seller_slug, COUNT(*) FROM mp_expenses GROUP BY seller_slug;
  SELECT seller_slug, COUNT(*) FROM payment_events WHERE event_type = 'expense_captured' GROUP BY seller_slug;
  -- Devem ser iguais
  ```
- [ ] `python3 -m pytest` passa

### US-003: Migrar dedup e remover todas as referencias

**Description:** Como sistema, preciso que `daily_sync.py` e `onboarding_backfill.py` usem o ledger para dedup, e que ZERO referencias a mp_expenses restem no codigo.

**Acceptance Criteria:**
- [ ] `daily_sync.py`: dedup de expenses usa `payment_events WHERE event_type = 'expense_captured'` em vez de `mp_expenses`
- [ ] `onboarding_backfill.py`: mesmo padrao de dedup
- [ ] Feature flag `expenses_source` removido de `config.py`
- [ ] Branches old/new removidos de todos os consumers
- [ ] `grep -r "mp_expenses" app/ --include="*.py"` retorna ZERO resultados
- [ ] `python3 -m pytest` passa

### US-004: Migration SQL — deprecar tabela

**Description:** Como DBA, preciso deprecar a tabela mp_expenses de forma segura com view de compatibilidade.

**Acceptance Criteria:**
- [ ] `migrations/009_deprecate_mp_expenses.sql` criado
- [ ] Tabela renomeada: `ALTER TABLE mp_expenses RENAME TO mp_expenses_deprecated`
- [ ] View criada: `CREATE VIEW mp_expenses AS SELECT ... FROM payment_events WHERE event_type = 'expense_captured'`
  - View mapeia campos do metadata para colunas esperadas
  - View deriva status via subqueries EXISTS (expense_exported, expense_reviewed, expense_classified)
- [ ] Migration aplicada no Supabase
- [ ] Verificacao: `SELECT COUNT(*) FROM mp_expenses` retorna dados via view
- [ ] Apos 1 mes sem erros: considerar `DROP TABLE mp_expenses_deprecated` (manual, nao automatico)

## 4. Functional Requirements

- FR-1: ZERO escrita em mp_expenses apos Etapa 5 completa
- FR-2: Dados historicos 100% migrados (contagem por seller bate)
- FR-3: View de compatibilidade captura queries que escaparam
- FR-4: Todos os fluxos existentes continuam funcionando: daily_sync, export, closing, onboarding

## 5. Non-Goals

- Nao criar novo dashboard de reconciliacao
- Nao alterar processor.py ou o fluxo de vendas
- Nao remover tabelas expense_batches/expense_batch_items (sao de controle, nao de dados)

## 6. Technical Considerations

- A view de compatibilidade usa subqueries EXISTS que podem ser lentas em tabelas grandes. Monitorar performance. Se necessario, criar materialized view com refresh periodico.
- O `daily_sync.py` busca TODOS os payment_ids de expenses para dedup. Com o ledger, precisa buscar reference_ids de expense_captured. O index `idx_pe_expense_events` ajuda.
- `onboarding_backfill.py` faz o mesmo padrao de dedup. Deve ser atualizado em conjunto com daily_sync.

## 7. Success Metrics

- `grep -r "mp_expenses" app/` retorna ZERO
- Contagens de mp_expenses_deprecated == expense_captured por seller
- Todos os fluxos funcionando sem erros por 1+ mes
- 161+ testes passando
- DRE inalterado

## 8. Open Questions

- Quando fazer o DROP TABLE definitivo? (sugestao: 1 mes apos migration 009 sem erros)
- Manter expense_batches/expense_batch_items como esta ou migrar para ledger? (sugestao: manter — sao de controle)

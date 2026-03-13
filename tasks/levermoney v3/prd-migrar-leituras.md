# PRD: Etapa 4 — Migrar Leituras para Event Ledger (Fase 3)

## 1. Introduction/Overview

Todos os consumers que hoje leem de `mp_expenses` passam a ler do event ledger. A tabela `mp_expenses` continua recebendo dual-write mas nenhum consumer le dela. Feature flag permite rollback instantaneo.

**Spec tecnica completa:** `rfcs/plano-execucao/05-fase3-migrar-leituras.md`

## 2. Goals

- Consumers migrados: financial_closing, extrato_coverage_checker, expenses/crud, expenses/export, expenses/closing
- Feature flag `expenses_source` para rollback seguro
- Respostas identicas: old (mp_expenses) vs new (ledger)
- Deploy gradual com validacao

## 3. User Stories

### US-001: Helpers de leitura no event_ledger.py

**Description:** Como sistema, preciso de funcoes que reconstroem a informacao de mp_expenses a partir do event ledger.

**Acceptance Criteria:**
- [ ] `get_pending_exports(seller_slug, date_from, date_to, status_filter)` criada
  - Retorna expenses com `expense_captured` mas SEM `expense_exported`
  - Enriquece com metadata de `expense_classified` (ca_category)
  - Retorna mesmos campos que mp_expenses para compatibilidade com `_build_xlsx()`
- [ ] `get_expense_list(seller_slug, status, expense_type, direction, date_from, date_to, limit, offset)` criada
  - Deriva status via `derive_expense_status()` para cada reference_id
  - Suporta filtro por status derivado
- [ ] `get_expense_stats(seller_slug, date_from, date_to, status_filter)` criada
  - Retorna mesma shape que endpoint atual: `total`, `total_amount`, `by_type`, `by_direction`, `by_status`, `pending_review_count`, `auto_categorized_count`
- [ ] Testes unitarios para cada helper
- [ ] `python3 -m pytest` passa

### US-002: Feature flag + migracao dos consumers

**Description:** Como sistema, preciso de um feature flag para alternar entre mp_expenses e ledger, e migrar todos os consumers.

**Acceptance Criteria:**
- [ ] `expenses_source: str = "mp_expenses"` adicionado em `app/config.py` (valores: `"mp_expenses"` | `"ledger"`)
- [ ] `financial_closing.py` `_compute_manual_lane()`: usa ledger quando `expenses_source == "ledger"`
- [ ] `extrato_coverage_checker.py` `_lookup_expense_ids()`: usa `expense_captured` em payment_events quando flag ativo
- [ ] `expenses/crud.py`: todos os endpoints (list, review, stats, pending-summary) usam ledger quando flag ativo
- [ ] `expenses/export.py`: export usa `get_pending_exports()` quando flag ativo; marcacao de exported grava `expense_exported` events
- [ ] Com `expenses_source=mp_expenses`: comportamento identico ao atual
- [ ] Com `expenses_source=ledger`: mesmos dados, mesmos totais
- [ ] `python3 -m pytest` passa

### US-003: Validacao de paridade old vs new

**Description:** Como operador, preciso validar que as respostas do ledger sao identicas as de mp_expenses antes de migrar.

**Acceptance Criteria:**
- [ ] Script ou teste que compara para 141air jan/2026:
  - `list_expenses()` retorna mesma contagem e mesmos payment_ids
  - `expense_stats()` retorna mesmos totais e contadores
  - `export_expenses()` gera ZIP com mesmo conteudo XLSX (mesmas linhas, mesmos valores)
  - `financial_closing()` manual lane retorna mesmos dias e contadores
- [ ] Zero diferencas documentadas (ou diferencas explicadas e aceitas)
- [ ] DRE inalterado
- [ ] Deploy com `expenses_source=ledger` em producao por 2+ semanas sem erros

## 4. Functional Requirements

- FR-1: Feature flag `expenses_source` controla TODAS as leituras de expenses
- FR-2: Review de expense (PATCH) grava `expense_reviewed` event no ledger (nao altera mp_expenses quando usando ledger)
- FR-3: O XLSX gerado pelo export deve ter mesmos campos e valores independente da source
- FR-4: Rollback instantaneo: mudar flag de `ledger` para `mp_expenses` restaura comportamento anterior

## 5. Non-Goals

- Nao parar dual-write (isso e Etapa 5)
- Nao deletar mp_expenses
- Nao remover feature flag (manter para rollback)

## 6. Technical Considerations

- O metadata do `expense_captured` DEVE conter todos os campos que `_build_xlsx()` espera (ver lista na spec). Se faltar algum campo, o XLSX sai incompleto. Validar completude ANTES de migrar.
- Performance: derivar status via GROUP BY no SQL e mais lento que WHERE status = X. O index `idx_pe_expense_events` ajuda.
- `_compute_manual_lane()` precisa virar async (ja e chamada de contexto async).

## 7. Success Metrics

- Respostas identicas old vs new para 141air
- Zero erros em producao com `expenses_source=ledger` por 2+ semanas
- 161+ testes passando

## 8. Open Questions

- Nenhuma

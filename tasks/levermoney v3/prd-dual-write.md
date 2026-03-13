# PRD: Etapa 3 — Expense Lifecycle + Dual-Write (Fase 2)

## 1. Introduction/Overview

Cada despesa/receita non-order grava no event ledger ALEM de em `mp_expenses`. Ao final, todo registro em `mp_expenses` tem um `expense_captured` correspondente no ledger. Isso prepara a eliminacao futura da tabela mutavel.

**Spec tecnica completa:** `rfcs/plano-execucao/04-fase2-dual-write.md`

## 2. Goals

- Todo novo registro em `mp_expenses` gera `expense_captured` no ledger
- Auto-classificacoes geram `expense_classified` adicional
- Exports geram `expense_exported`
- `COUNT(mp_expenses) == COUNT(expense_captured)` para cada seller
- Nenhuma mudanca visivel para o usuario final (mp_expenses continua como fonte de leitura)

## 3. User Stories

### US-001: Novos event types + record_expense_event()

**Description:** Como sistema, preciso de 4 novos event types para o ciclo de vida de despesas e uma funcao helper para grava-los.

**Acceptance Criteria:**
- [ ] 4 novos event types adicionados ao `EVENT_TYPES` em `event_ledger.py`:
  - `expense_captured` (sign: `"any"`) — despesa/receita identificada
  - `expense_classified` (sign: `"zero"`) — classificada automaticamente
  - `expense_reviewed` (sign: `"zero"`) — revisada por humano
  - `expense_exported` (sign: `"zero"`) — exportada em batch
- [ ] Funcao `record_expense_event(seller_slug, payment_id, event_type, signed_amount, competencia_date, expense_type, metadata)` criada
- [ ] Idempotency key: `{seller}:{payment_id}:{event_type}` (3 partes)
- [ ] Funcao `derive_expense_status(event_types: set[str]) -> str` criada
- [ ] `get_dre_summary()` ja exclui `expense_*` events (feito na Etapa 2)
- [ ] Testes unitarios para `record_expense_event` e `derive_expense_status`
- [ ] `python3 -m pytest` passa (161+ testes)

### US-002: Dual-write nos 3 services produtores

**Description:** Como sistema, preciso que `expense_classifier.py`, `extrato_ingester.py` e `release_report_sync.py` gravem no ledger alem de mp_expenses.

**Acceptance Criteria:**
- [ ] `expense_classifier.py`: apos upsert em mp_expenses, grava `expense_captured` no ledger. Se `auto_categorized`, grava `expense_classified` tambem.
- [ ] `extrato_ingester.py`: apos insert em mp_expenses (bloco "d. Insert new"), grava `expense_captured`. Se `auto_categorized`, grava `expense_classified`.
- [ ] `release_report_sync.py`: apos insert de payouts/cashback/shipping em mp_expenses, grava `expense_captured`.
- [ ] Nenhum dos 3 services falha se o ledger retornar conflito de idempotency (ON CONFLICT DO NOTHING)
- [ ] Rodar daily_sync para 141air → novas despesas aparecem em AMBAS as tabelas
- [ ] Verificacao SQL:
  ```sql
  SELECT COUNT(*) FROM mp_expenses WHERE seller_slug = '141air';
  SELECT COUNT(*) FROM payment_events WHERE seller_slug = '141air' AND event_type = 'expense_captured';
  -- Devem ser iguais
  ```
- [ ] `python3 -m pytest` passa

### US-003: Dual-write no export

**Description:** Como sistema, preciso que o export de despesas grave `expense_exported` no ledger apos marcar rows como exported em mp_expenses.

**Acceptance Criteria:**
- [ ] `app/routers/expenses/export.py`: apos marcar rows como "exported" em mp_expenses, gravar `expense_exported` para cada row no ledger
- [ ] Metadata inclui `batch_id` do export
- [ ] Export ZIP funciona normalmente (ainda le de mp_expenses)
- [ ] Verificacao: apos export, `COUNT(expense_exported WHERE batch_id = 'exp_xxx') == numero de rows exportadas`
- [ ] `python3 -m pytest` passa

### US-004: Validacao com dados reais

**Description:** Como operador, preciso validar que o dual-write esta consistente para todos os sellers ativos.

**Acceptance Criteria:**
- [ ] Script de validacao (pode ser inline SQL ou script Python) que compara:
  - `COUNT(mp_expenses)` vs `COUNT(expense_captured)` por seller
  - `SUM(amount)` de mp_expenses vs `SUM(signed_amount)` de expense_captured por seller
- [ ] Para 141air: contagens e somas batem 100%
- [ ] DRE inalterado (cash events e expense events NAO aparecem no DRE)
- [ ] Export ZIP gera mesmo conteudo que antes (le de mp_expenses, nao do ledger)

## 4. Functional Requirements

- FR-1: Todo INSERT em mp_expenses deve ser acompanhado de `expense_captured` no ledger
- FR-2: O `signed_amount` do `expense_captured` respeita a convencao: positivo para income, negativo para expense
- FR-3: `expense_classified` grava `ca_category` no metadata
- FR-4: `expense_exported` grava `batch_id` no metadata
- FR-5: Falha no dual-write do ledger NAO deve impedir a gravacao em mp_expenses (log warning, nao exception)
- FR-6: DRE por competencia nao e afetado

## 5. Non-Goals

- Nao migrar leituras para o ledger (isso e Etapa 4)
- Nao remover escrita em mp_expenses
- Nao alterar o admin panel ou endpoints de listagem
- Nao criar UI para o expense lifecycle

## 6. Technical Considerations

- O metadata do `expense_captured` deve conter TODOS os campos necessarios para reconstruir a resposta de mp_expenses na Etapa 4: `expense_type`, `expense_direction`, `ca_category`, `auto_categorized`, `description`, `amount`, `date_created`, `date_approved`, `business_branch`, `operation_type`, `payment_method`, `external_reference`, `beneficiary_name`, `notes`. Sem isso, a Etapa 4 nao tera dados suficientes.
- O `signed_amount` do expense_captured pode ser positivo (income) ou negativo (expense), diferente do `amount` em mp_expenses que e sempre positivo.

## 7. Success Metrics

- `COUNT(mp_expenses) == COUNT(expense_captured)` por seller
- DRE inalterado
- Export ZIP identico
- 161+ testes passando

## 8. Open Questions

- Nenhuma

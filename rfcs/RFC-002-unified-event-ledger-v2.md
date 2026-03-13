# RFC-002: Unified Event Ledger — Especificacao Completa

**Status:** Approved (escopo definido, implementacao pendente)
**Autor:** Eryk + Claude
**Data:** 2026-03-13
**Supersedes:** RFC-001

---

## Motivacao

O sistema tem **duas fontes de verdade** para movimentos financeiros:

| Fonte | Tabela | O que armazena | Status model |
|-------|--------|---------------|--------------|
| Vendas com pedido | `payment_events` | Receita, comissao, frete, estorno | Event sourcing (append-only, status derivado) |
| Tudo sem pedido | `mp_expenses` | Boletos, SaaS, DIFAL, payouts, cashback | Snapshot mutavel (status column) |

Problemas praticos:
- **DRE** precisa cruzar duas tabelas
- **Reconciliacao com extrato** exige logica dupla (payment_events + mp_expenses)
- **Auditoria** fragmentada — nao ha um lugar unico para ver todo o fluxo de dinheiro
- **Inconsistencia de modelo**: vendas usam event sourcing imutavel, despesas usam status mutavel

---

## Decisoes Aprovadas

| # | Decisao | Resposta |
|---|---------|----------|
| 1 | Competencia + caixa | **Coexistem** no mesmo ledger, duas camadas |
| 2 | Granularidade caixa | **1 evento por linha do extrato** com NET amount |
| 3 | Lancamentos no CA | **Nao mudam** — CA continua recebendo receita/comissao/frete separados |
| 4 | Nascimento de despesas | Quando **classifier/ingester identifica** (pode vir da API ou do extrato) |
| 5 | mp_expenses | **Dual-write transitorio** → depois depreca |
| 6 | Release checker / baixas CA | **Mantem como esta** — extrato e validacao posterior |
| 7 | Payouts (sem competencia previa) | **Competencia = mesmo dia do caixa** |
| 8 | Skips (transfers internas) | **Viram eventos** para rastreio total |
| 9 | DRE | **Sempre por competencia** |
| 10 | Piloto | **141air jan/2026** → validar 100% → expandir |

---

## Arquitetura: Duas Camadas no Mesmo Ledger

### Camada 1: Competencia (DRE) — ja existe

Eventos que representam o FATO GERADOR financeiro. Data = `competencia_date` (date_approved BRT).

```
Venda 138199281600, competencia_date=2026-01-02:
  sale_approved     +4.600,00
  fee_charged         -497,71
  shipping_charged    -107,45
```

### Camada 2: Caixa (Extrato) — NOVA

Eventos que representam o MOVIMENTO DE DINHEIRO. Data = `event_date` (release_date do extrato).

```
Extrato linha, event_date=2026-01-05:
  cash_release      +3.994,84   ref=138199281600  (link com venda acima)
```

### Invariante de reconciliacao

```
Para todo seller S, para todo dia D:
  sum(signed_amount WHERE event_type LIKE 'cash_%' AND event_date = D AND seller = S)
  == sum(TRANSACTION_NET_AMOUNT do extrato CSV nesse dia para S)

Para todo seller S, para todo mes M:
  sum(signed_amount WHERE event_type LIKE 'cash_%' AND seller = S AND mes = M)
  == final_balance - initial_balance do extrato CSV
```

**Essa invariante e tautologica por construcao** — cada evento cash e criado a partir de uma linha do extrato, com o mesmo valor.

---

## Schema Changes

### Migration 008: Extend payment_events for unified ledger

```sql
-- Adiciona reference_id para entradas sem ml_payment_id numerico
-- (payouts, transfers, DIFAL usam ref_id textual do extrato)
ALTER TABLE payment_events ADD COLUMN reference_id TEXT;

-- Backfill: reference_id = ml_payment_id para eventos existentes
UPDATE payment_events SET reference_id = ml_payment_id::text
WHERE reference_id IS NULL;

-- Relaxa ml_payment_id: default 0 para entradas sem payment numerico
-- NAO torna nullable (queries existentes dependem de NOT NULL)
ALTER TABLE payment_events ALTER COLUMN ml_payment_id SET DEFAULT 0;

-- Index para lookup por reference_id (reconciliacao, dedup)
CREATE INDEX idx_pe_seller_ref ON payment_events (seller_slug, reference_id)
WHERE reference_id IS NOT NULL;

-- Index parcial para queries de caixa (cash events)
CREATE INDEX idx_pe_cash_events ON payment_events (seller_slug, event_date)
WHERE event_type LIKE 'cash_%';

-- Index parcial para queries de despesa lifecycle
CREATE INDEX idx_pe_expense_events ON payment_events (seller_slug, event_type)
WHERE event_type LIKE 'expense_%';
```

### Nota sobre ml_payment_id

`ml_payment_id` continua BIGINT NOT NULL. O reference_id do extrato do Mercado Pago e
**sempre um ID numerico** (payment_id, operation_id, withdrawal_id) — entao `ml_payment_id`
e preenchido normalmente para praticamente todos os cash events. O `reference_id TEXT` serve
como backup para casos excepcionais e para composite keys do extrato_ingester.

Queries existentes que usam `ml_payment_id` continuam funcionando sem modificacao.

---

## Novos Event Types

### Cash events (Camada 2 — reconciliacao com extrato)

| Event Type | Sign | Descricao | Exemplo no extrato |
|---|---|---|---|
| `cash_release` | positive | Liberacao de venda (NET) | "Liberacao de dinheiro" |
| `cash_expense` | negative | Despesa debitada | "DIFAL", "Pagamento de fatura" |
| `cash_income` | positive | Receita nao-venda | "Cashback", "Reembolso disputa" |
| `cash_transfer_out` | negative | Dinheiro saindo | "Transferencia PIX enviada", "Saque" |
| `cash_transfer_in` | positive | Dinheiro entrando | "Deposito", "PIX recebido" |
| `cash_internal` | any | Movimento interno (skip) | "Transferencia entre contas" |

### Expense lifecycle events (unificacao de mp_expenses)

| Event Type | Sign | Descricao |
|---|---|---|
| `expense_captured` | any | Despesa/receita identificada (valor com sinal) |
| `expense_classified` | zero | Classificada automaticamente (metadata: category, confidence) |
| `expense_reviewed` | zero | Revisada por humano (metadata: approved, reviewer) |
| `expense_exported` | zero | Exportada em batch (metadata: batch_id) |

### Validacao de sign atualizada

```python
EVENT_TYPES = {
    # ... existentes ...
    # Cash events
    "cash_release":      "positive",
    "cash_expense":      "negative",
    "cash_income":       "positive",
    "cash_transfer_out": "negative",
    "cash_transfer_in":  "positive",
    "cash_internal":     "any",       # NOVO: aceita qualquer sinal
    # Expense lifecycle
    "expense_captured":  "any",       # NOVO: receita (+) ou despesa (-)
    "expense_classified": "zero",
    "expense_reviewed":  "zero",
    "expense_exported":  "zero",
}
```

---

## Idempotency Keys

### Cash events

```
{seller}:{reference_id}:{event_type}:{event_date}:{abbrev}
Exemplo: 141air:138199281600:cash_release:2026-01-05:cr
Exemplo: 141air:2775052514:cash_expense:2026-01-21:df  (DIFAL)
```

A data na chave e necessaria porque o mesmo ref_id pode aparecer em dias diferentes.
A abbreviation (`abbrev`) previne colisao quando dois tipos de transacao diferentes geram
o mesmo cash event type para o mesmo ref_id no mesmo dia (ver `_EXPENSE_TYPE_ABBREV`
em `extrato_ingester.py` e `SKIP_ABBREV` em `03-fase1-cash-events.md`).

### Expense lifecycle events

```
{seller}:{payment_id_or_composite}:{event_type}
Exemplo: 141air:12345678:expense_captured
Exemplo: 141air:12345678:df:expense_captured  (composite key com abbreviation)
```

---

## Implementacao em 4 Fases

### Fase 1: Schema + Cash Events (reconciliacao)

**Objetivo:** Cada linha do extrato vira um evento cash_ no ledger. Validar que sum(cash) == extrato.

1. Criar migration 008 (schema changes acima)
2. Adicionar novos event types ao `EVENT_TYPES` dict em `event_ledger.py`
3. Adicionar funcao `record_cash_event()` com idempotency key incluindo data
4. Criar script `testes/ingest_extrato_to_ledger.py` que:
   - Le extrato CSV (Account Statement)
   - Para cada linha: classifica, cria cash event com valor e metadata
   - Valida invariante: sum(cash events) == final_balance - initial_balance
5. Rodar para 141air jan/2026 → validar 100%

**NAO muda:** processor.py, daily_sync.py, expense_classifier.py, mp_expenses, export.
**NAO quebra:** DRE, baixas, CA jobs, nenhum fluxo existente.

### Fase 2: Expense Lifecycle (dual-write)

**Objetivo:** Cada mp_expense tambem gera eventos no ledger. Validar consistencia.

1. Modificar `expense_classifier.py`: apos gravar em mp_expenses, TAMBEM grava `expense_captured`
2. Modificar `extrato_ingester.py`: apos gravar em mp_expenses, TAMBEM grava `expense_captured`
3. Modificar `release_report_sync.py`: apos gravar em mp_expenses, TAMBEM grava `expense_captured`
4. Script de validacao: todo registro em mp_expenses tem expense_captured correspondente

**NAO muda:** export, admin panel, financial_closing (continuam lendo mp_expenses).

### Fase 3: Migrar Leituras

**Objetivo:** Consumers passam a ler do ledger em vez de mp_expenses.

1. `financial_closing.py`: lane manual le do ledger (expense_captured + expense_exported)
2. `extrato_coverage_checker.py`: verifica cobertura contra cash events no ledger
3. Router `expenses/crud.py`: lista despesas do ledger
4. Router `expenses/export.py`: exporta do ledger (expense_captured sem expense_exported)

### Fase 4: Deprecar mp_expenses

**Objetivo:** mp_expenses vira read-only, depois view, depois apaga.

1. Parar de gravar em mp_expenses (remover dual-write)
2. Migrar dados historicos: cada mp_expense existente → expense_captured event
3. mp_expenses vira view materializada (ou apaga)

---

## Riscos e Mitigacoes

### R1: Double-counting (CRITICO)

**Risco:** sum(*) sem filtro inclui competencia + caixa → valores dobrados.

**Mitigacao:**
- Prefixos claros: `cash_` para caixa, `expense_` para lifecycle
- Helper functions obrigatorias: `get_dre_summary()` filtra por competencia, `get_cash_summary()` filtra por caixa
- Testes automatizados que verificam que sum(competencia) != sum(caixa) != sum(*)
- Documentacao explicita: "NUNCA fazer sum(*) sem filtrar event_type"

### R2: ml_payment_id para non-payments (risco BAIXO)

**Risco original:** Entries sem payment_id numerico precisariam ml_payment_id = 0.

**Realidade:** O reference_id do extrato do MP e SEMPRE um ID numerico (payment_id, operation_id,
withdrawal_id). Portanto ml_payment_id e preenchido normalmente para quase todos os cash events.
O `reference_id TEXT` existe como backup para casos excepcionais.

**Risco residual:** Minimo. Manter `reference_id` como campo auxiliar, nao primario.

### R3: competencia_date ambiguo

**Risco:** Para cash events, competencia_date = event_date (release_date), nao competencia real.

**Mitigacao:**
- Documentar explicitamente no event_ledger.py
- Para cash events: competencia_date = event_date (sao o mesmo)
- DRE query filtra por event types de competencia (sale_approved, etc.) → nao afetado

### R4: Export mais complexo

**Risco:** Derivar "pendente de export" via ausencia de expense_exported e mais lento que WHERE status.

**Mitigacao:**
- Manter mp_expenses para export durante Fase 2 (dual-write)
- So migrar export na Fase 3, quando a logica estiver validada
- Helper function `get_pending_exports()` encapsula a query complexa

### R5: Escopo grande para dev solo

**Risco:** Tocar 10+ arquivos, reescrever testes, risco de regressao.

**Mitigacao:**
- Fases incrementais com validacao entre cada uma
- Fase 1 e isolada (nao muda nada existente)
- Dual-write permite rollback (mp_expenses continua funcionando)
- 366 testes existentes rodam em cada fase como rede de seguranca

---

## Validacao com Dados Reais (141Air Janeiro 2026)

### Extrato de referencia

```
INITIAL_BALANCE: R$ 4.476,23
CREDITS:         R$ 207.185,69
DEBITS:          R$ -210.571,52
FINAL_BALANCE:   R$ 1.090,40
NET MOVEMENT:    R$ -3.385,83  (final - initial)
TOTAL LINES:     690
```

### Distribuicao esperada dos cash events

| Cash Event Type | Qtd | Origem |
|---|---|---|
| cash_release | ~330 | Liberacoes de venda (link com payment_events) |
| cash_expense | ~110 | DIFAL, faturas ML, debitos, compras MP |
| cash_income | ~80 | Cashback, reembolsos, creditos |
| cash_transfer_out | ~115 | PIX enviados, saques, pagamentos de conta, transferencias |
| cash_transfer_in | ~40 | Depositos, PIX recebidos |
| cash_internal | ~15 | Transferencias de saldo internas MP, reservas de renda |
| **Total** | **690** | **1:1 com extrato** |

### Checklist de validacao

- [ ] 690 cash events criados (1 por linha do extrato)
- [ ] sum(signed_amount de TODOS os cash events) == R$ -3.385,83
- [ ] Para cada dia D: sum(cash events do dia) == sum(extrato do dia)
- [ ] Para cash_release: reference_id existe em payment_events como sale_approved
- [ ] Zero duplicatas (idempotency keys unicas)
- [ ] DRE por competencia NAO muda (testes existentes passam)
- [ ] 366 testes pytest continuam passando

---

## Arquivos Afetados por Fase

### Fase 1 (Cash Events — isolada)

| Arquivo | Mudanca |
|---|---|
| `migrations/008_unified_ledger.sql` | **NOVO** — schema changes |
| `app/services/event_ledger.py` | Novos event types, `record_cash_event()`, `get_cash_summary()` |
| `testes/ingest_extrato_to_ledger.py` | **NOVO** — ingesta extrato → cash events |
| `testes/test_event_ledger.py` | Novos testes para cash event types |

### Fase 2 (Expense Lifecycle — dual-write)

| Arquivo | Mudanca |
|---|---|
| `app/services/expense_classifier.py` | Adicionar escrita de expense_captured apos mp_expenses |
| `app/services/extrato_ingester.py` | Adicionar escrita de expense_captured apos mp_expenses |
| `app/services/release_report_sync.py` | Adicionar escrita de expense_captured apos mp_expenses |
| `app/services/event_ledger.py` | `derive_expense_status()`, `get_pending_exports()` |

### Fase 3 (Migrar Leituras)

| Arquivo | Mudanca |
|---|---|
| `app/services/financial_closing.py` | Lane manual le do ledger |
| `app/services/extrato_coverage_checker.py` | Verifica cash events em vez de release report |
| `app/routers/expenses/crud.py` | Lista do ledger |
| `app/routers/expenses/export.py` | Exporta do ledger |

### Fase 4 (Deprecar mp_expenses)

| Arquivo | Mudanca |
|---|---|
| Todos os acima | Remover dual-write, remover imports de mp_expenses |
| `migrations/008_deprecate_mp_expenses.sql` | Drop table ou create view |

---

## Referencias

- `rfcs/RFC-001-unified-event-ledger.md` — draft original (superseded)
- `app/services/event_ledger.py` — implementacao atual do ledger
- `app/services/expense_classifier.py` — classificador de despesas
- `app/services/extrato_ingester.py` — ingesta de linhas do extrato
- `docs/TABELAS.md` — schema payment_events e mp_expenses
- `migrations/006_payment_events.sql` — migration original do ledger
- `testes/data/extratos/extrato janeiro 141Air.csv` — extrato de referencia
- `PLANO_FUNDACAO.md` — contexto completo do projeto

---

*Criado: 2026-03-13 — Sessao #9*

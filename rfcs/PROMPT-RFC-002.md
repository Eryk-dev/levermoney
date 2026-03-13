# ⚠️ DEPRECATED — NAO USAR

> **Este arquivo contem bugs conhecidos identificados por auditoria externa.**
> **Usar em vez deste:** `rfcs/plano-execucao/03-fase1-cash-events.md` (versao corrigida)
>
> Bugs neste arquivo: skips mapeados como blanket cash_internal, idempotency key sem abbreviation,
> mapeamentos mortos (deposito_avulso, cashback), get_dre_summary desprotegida.
> Detalhes: `rfcs/plano-execucao/02-correcoes-pre-implementacao.md`

---

# Prompt: Implementar Unified Event Ledger — Fase 1 (Cash Events) [DEPRECATED]

> **Instrucao para LLM.** Este prompt e auto-contido. Implemente SOMENTE a Fase 1.
> Ao final, valide com dados reais. So prossiga para Fase 2 apos validacao completa.

---

## Contexto do Sistema

Sistema de conciliacao automatica entre **Mercado Livre/Mercado Pago** e **Conta Azul ERP**.

Para cada venda no ML, cria no CA:
- Receita (contas-a-receber) com valor bruto
- Despesa comissao (contas-a-pagar) com taxas ML
- Despesa frete (contas-a-pagar) com custo de envio
- Baixas quando dinheiro e liberado

Alem de vendas, o sistema rastreia despesas sem pedido (boletos, SaaS, DIFAL, cashback, payouts)
na tabela `mp_expenses`.

### Arquitetura atual (duas fontes de verdade)

```
Vendas (com order_id):
  ML Payments API → processor.py → payment_events (event ledger, append-only)
  Status derivado de eventos: sale_approved → queued, ca_sync_completed → synced, etc.

Despesas (sem order_id):
  ML Payments API → expense_classifier.py → mp_expenses (tabela mutavel, status column)
  Extrato CSV → extrato_ingester.py → mp_expenses
  Release Report CSV → release_report_sync.py → mp_expenses
```

### Tabela payment_events (schema atual)

```sql
id BIGSERIAL PRIMARY KEY,
seller_slug TEXT NOT NULL,
ml_payment_id BIGINT NOT NULL,       -- ID numerico do pagamento ML
ml_order_id BIGINT,
event_type TEXT NOT NULL,             -- sale_approved, fee_charged, etc.
signed_amount NUMERIC(12,2) NOT NULL, -- +receita, -despesa, 0=flag
competencia_date DATE NOT NULL,       -- data do fato gerador (date_approved BRT)
event_date DATE NOT NULL,             -- data do evento
created_at TIMESTAMPTZ DEFAULT NOW(),
source TEXT DEFAULT 'processor',
idempotency_key TEXT UNIQUE,
metadata JSONB
```

16 event types existentes: sale_approved, fee_charged, shipping_charged, subsidy_credited,
refund_created, refund_fee, refund_shipping, partial_refund, ca_sync_completed, ca_sync_failed,
money_released, mediation_opened, charged_back, reimbursed, adjustment_fee, adjustment_shipping.

---

## Objetivo da Fase 1

Adicionar uma **camada de caixa** ao ledger existente: cada linha do extrato (Account Statement CSV)
vira um evento `cash_*` no `payment_events`. Isso permite reconciliacao automatica:
**sum(cash events) == extrato**.

### O que muda

1. Schema: adicionar coluna `reference_id TEXT` + relaxar `ml_payment_id` default
2. Event types: 6 novos tipos `cash_*`
3. Helper: `record_cash_event()` com idempotency key incluindo data
4. Script: ingesta do extrato CSV → cash events no ledger
5. Testes: validar com 141air jan/2026

### O que NAO muda (CRITICO)

- **processor.py** — nao tocar
- **daily_sync.py** — nao tocar
- **expense_classifier.py** — nao tocar (Fase 2)
- **mp_expenses** — nao tocar (Fase 2)
- **CA jobs, baixas, export** — nao tocar
- **DRE por competencia** — deve continuar funcionando identico
- **366 testes pytest existentes** — todos devem continuar passando

---

## Passo 1: Migration SQL

Criar `migrations/007_unified_ledger.sql`:

```sql
-- RFC-002: Extend payment_events for unified ledger (cash events)

-- reference_id textual para entradas sem ml_payment_id numerico
ALTER TABLE payment_events ADD COLUMN IF NOT EXISTS reference_id TEXT;

-- Backfill reference_id para eventos existentes
UPDATE payment_events SET reference_id = ml_payment_id::text
WHERE reference_id IS NULL;

-- Default 0 para ml_payment_id (safety net — extrato MP usa IDs numericos,
-- entao ml_payment_id e preenchido normalmente na maioria dos casos)
ALTER TABLE payment_events ALTER COLUMN ml_payment_id SET DEFAULT 0;

-- Index para lookup por reference_id
CREATE INDEX IF NOT EXISTS idx_pe_seller_ref
ON payment_events (seller_slug, reference_id)
WHERE reference_id IS NOT NULL;

-- Index parcial para queries de caixa
CREATE INDEX IF NOT EXISTS idx_pe_cash_events
ON payment_events (seller_slug, event_date)
WHERE event_type LIKE 'cash_%';
```

Aplicar via Supabase MCP tool (`mcp__supabase__apply_migration`).

---

## Passo 2: Novos Event Types em event_ledger.py

Adicionar ao dicionario `EVENT_TYPES`:

```python
# Cash events (Camada 2 — reconciliacao extrato)
"cash_release":      "positive",    # Liberacao de venda (NET amount)
"cash_expense":      "negative",    # Despesa debitada (DIFAL, fatura, debito)
"cash_income":       "positive",    # Receita nao-venda (cashback, reembolso)
"cash_transfer_out": "negative",    # Dinheiro saindo (PIX, saque, payout)
"cash_transfer_in":  "positive",    # Dinheiro entrando (deposito, PIX recebido)
"cash_internal":     "any",         # Movimento interno sem impacto DRE
```

**IMPORTANTE:** Adicionar suporte para sign "any" no `validate_event()`:
```python
if expected == "any":
    pass  # aceita qualquer sinal
```

---

## Passo 3: Funcao record_cash_event()

Adicionar em `event_ledger.py`:

```python
async def record_cash_event(
    seller_slug: str,
    reference_id: str,
    event_type: str,
    signed_amount: float,
    event_date: str,
    extrato_type: str,
    metadata: dict | None = None,
) -> dict | None:
    """Record a cash flow event from an extrato line.

    Idempotency key includes date because the same reference_id
    can appear on different days (e.g., partial releases).
    """
    if not event_type.startswith("cash_"):
        raise ValueError(f"record_cash_event requires cash_* event type, got {event_type}")

    idem_key = f"{seller_slug}:{reference_id}:{event_type}:{event_date}"

    # reference_id do extrato MP e sempre numerico (payment_id, operation_id, etc.)
    try:
        ml_pid = int(reference_id)
    except (ValueError, TypeError):
        ml_pid = 0  # rarissimo — extrato MP usa IDs numericos

    full_metadata = {"extrato_type": extrato_type, "source": "account_statement"}
    if metadata:
        full_metadata.update(metadata)

    return await record_event(
        seller_slug=seller_slug,
        ml_payment_id=ml_pid,
        event_type=event_type,
        signed_amount=signed_amount,
        competencia_date=event_date,   # para cash events, competencia = caixa
        event_date=event_date,
        source="extrato",
        metadata=full_metadata,
        idempotency_key=idem_key,
    )
```

**Adicionar helper de query:**

```python
async def get_cash_summary(
    seller_slug: str,
    date_from: str,
    date_to: str,
) -> dict:
    """Aggregate cash events by type for a date range (event_date).

    Returns dict like: {"cash_release": 12345.67, "cash_expense": -1234.56, ...}
    """
    # Mesma logica de get_dre_summary, mas:
    # - Filtra WHERE event_type LIKE 'cash_%'
    # - Usa event_date em vez de competencia_date
```

**IMPORTANTE:** Tambem adicionar `reference_id` ao dict `row` em `record_event()`:
Na funcao `record_event()`, adicionar a linha:
```python
row = {
    ...
    "reference_id": reference_id if reference_id is not None else str(ml_payment_id),
    ...
}
```
Isso requer adicionar `reference_id: str | None = None` como parametro de `record_event()`.

---

## Passo 4: Script de Ingesta do Extrato

Criar `testes/ingest_extrato_to_ledger.py`:

```
python3 testes/ingest_extrato_to_ledger.py --seller 141air --month jan2026 [--dry-run]
```

**Fluxo:**

1. Ler extrato CSV de `testes/data/extratos/extrato janeiro 141Air.csv`
2. Parsear com `_parse_account_statement()` de `extrato_ingester.py`
3. Para cada transacao, classificar com `_classify_extrato_line()`
4. Mapear classificacao para cash event type:

```python
CASH_TYPE_MAP = {
    # expense_type retornado por _classify_extrato_line → cash event type
    None: "cash_internal",                    # skips internos
    "_CHECK_PAYMENTS": None,                  # resolver abaixo
    "difal": "cash_expense",
    "faturas_ml": "cash_expense",
    "debito_envio_ml": "cash_expense",
    "debito_divida_disputa": "cash_expense",
    "debito_troca": "cash_expense",
    "pagamento_cartao_credito": "cash_expense",
    "subscription": "cash_expense",
    "reembolso_disputa": "cash_income",
    "reembolso_generico": "cash_income",
    "entrada_dinheiro": "cash_income",
    "bonus_envio": "cash_income",
    "cashback": "cash_income",
    "dinheiro_retido": "cash_expense",
    "liberacao_cancelada": "cash_expense",
    "deposito_avulso": "cash_transfer_in",
    # _CHECK_PAYMENTS resolved:
    "liberacao_nao_sync": "cash_release",     # venda que ML API perdeu
    "qr_pix_nao_sync": "cash_income",
    "pix_nao_sync": "cash_transfer_in",
    "dinheiro_recebido": "cash_income",
}

# Para _CHECK_PAYMENTS que resolve para payment_events (ja coberto):
# O cash event type e cash_release (liberacao de venda conhecida)
```

5. Para `_CHECK_PAYMENTS`:
   - Se ref_id esta em payment_events (sale_approved) → `cash_release`
   - Se nao → resolver com `_resolve_check_payments()` e usar mapa acima

6. Para cada linha, gravar `record_cash_event()` com:
   - `reference_id` = ref_id do extrato
   - `signed_amount` = amount do extrato (ja com sinal correto)
   - `event_date` = data do extrato
   - `extrato_type` = transaction_type original do extrato (portugues)

7. **Modo dry-run:** nao gravar, apenas contar e validar

**Atencao com transfers (skips atuais):**
Linhas classificadas como `expense_type=None` (skips) TAMBEM viram eventos:
- `cash_internal` com o amount do extrato
- Sao necessarios para que sum(cash events) == extrato total

---

## Passo 5: Validacao

### 5a. Testes pytest existentes

```bash
python3 -m pytest
# DEVE: 366 passed (nenhum teste quebrado)
```

### 5b. Dry-run do script

```bash
python3 testes/ingest_extrato_to_ledger.py --seller 141air --month jan2026 --dry-run
```

Saida esperada:
```
Total lines: 690
cash_release: ~330
cash_expense: ~100
cash_income: ~80
cash_transfer_out: ~90
cash_transfer_in: ~40
cash_internal: ~50
SUM: -3385.83  (== final_balance - initial_balance)
```

### 5c. Ingesta real

```bash
python3 testes/ingest_extrato_to_ledger.py --seller 141air --month jan2026
```

### 5d. Validacao pos-ingesta

Query Supabase (via SQL ou script):

```sql
-- Total de cash events deve ser 690
SELECT COUNT(*) FROM payment_events
WHERE seller_slug = '141air' AND event_type LIKE 'cash_%';

-- Sum deve ser -3385.83 (net movement jan/2026)
SELECT ROUND(SUM(signed_amount)::numeric, 2) FROM payment_events
WHERE seller_slug = '141air' AND event_type LIKE 'cash_%';

-- Por dia: deve bater com extrato dia a dia
SELECT event_date, ROUND(SUM(signed_amount)::numeric, 2) as net
FROM payment_events
WHERE seller_slug = '141air' AND event_type LIKE 'cash_%'
GROUP BY event_date
ORDER BY event_date;

-- DRE por competencia NAO deve mudar
SELECT event_type, ROUND(SUM(signed_amount)::numeric, 2)
FROM payment_events
WHERE seller_slug = '141air'
  AND event_type NOT LIKE 'cash_%'
  AND event_type NOT LIKE 'expense_%'
  AND competencia_date BETWEEN '2026-01-01' AND '2026-01-31'
GROUP BY event_type;
```

### 5e. Idempotencia

Rodar o script novamente:
```bash
python3 testes/ingest_extrato_to_ledger.py --seller 141air --month jan2026
```
Deve reportar: "0 newly ingested, 690 already exist (idempotent skip)"

---

## Passo 6: Testes Novos

Criar `testes/test_cash_events.py` com:

1. **test_cash_event_types_exist** — todos os 6 tipos estao no EVENT_TYPES
2. **test_validate_cash_any_sign** — cash_internal aceita positivo e negativo
3. **test_record_cash_event_idempotency** — mesma chave nao duplica
4. **test_cash_event_idem_key_includes_date** — chave inclui data
5. **test_cash_event_reference_id** — reference_id e preenchido
6. **test_cash_event_ml_payment_id_numeric** — ref_id numerico preenche ml_payment_id
7. **test_cash_event_ml_payment_id_zero** — ref_id nao-numerico usa ml_payment_id=0
8. **test_get_cash_summary** — soma por tipo para date range

---

## Dados de Referencia (141Air Janeiro 2026)

```
Extrato: testes/data/extratos/extrato janeiro 141Air.csv

INITIAL_BALANCE:  4476.23
CREDITS:        207185.69
DEBITS:        -210571.52
FINAL_BALANCE:    1090.40
NET MOVEMENT:    -3385.83

Total lines: 690
  Skips atuais (expense_type=None): ~50  → cash_internal
  Payment_events (sale_approved): ~330    → cash_release
  MP_expenses (classifier+ingester): ~310 → cash_expense/income/transfer
```

---

## Arquivos para LER ANTES de comecar

Ler nesta ordem:

1. `rfcs/RFC-002-unified-event-ledger-v2.md` — a especificacao completa
2. `app/services/event_ledger.py` — implementacao atual do ledger
3. `migrations/006_payment_events.sql` — schema original
4. `app/services/extrato_ingester.py` linhas 88-200 — classificacao de linhas do extrato
5. `app/services/extrato_ingester.py` linhas 243-327 — parser do Account Statement CSV
6. `testes/ingest_extrato_gaps.py` — referencia de como o script de ingesta funciona
7. `testes/validate_full_coverage.py` — referencia de como buscar dados do Supabase

---

## NUNCA (guard rails)

- **NUNCA** modificar processor.py, daily_sync.py, ou ca_queue.py nesta fase
- **NUNCA** modificar a tabela mp_expenses nesta fase
- **NUNCA** modificar event types existentes (sale_approved, fee_charged, etc.)
- **NUNCA** fazer sum(signed_amount) sem filtrar por event_type — risco de double-counting
- **NUNCA** criar cash events para linhas que nao existem no extrato CSV
- **NUNCA** alterar idempotency keys de eventos existentes
- **NUNCA** tornar ml_payment_id nullable (usar default 0)
- **NUNCA** adicionar logic de export/CA nesta fase

---

## Criterio de Sucesso

A Fase 1 esta COMPLETA quando:

1. ✅ Migration 007 aplicada no Supabase
2. ✅ 6 novos event types no event_ledger.py
3. ✅ record_cash_event() funcionando com idempotency
4. ✅ get_cash_summary() retornando agregados por event_date
5. ✅ 690 cash events criados para 141air jan/2026
6. ✅ sum(cash events) == R$ -3.385,83
7. ✅ Reconciliacao dia a dia: zero gaps
8. ✅ Re-run idempotente (0 duplicatas)
9. ✅ 366 testes existentes passando
10. ✅ Novos testes cash events passando
11. ✅ DRE por competencia inalterado (mesmos valores de antes)

---

*Criado: 2026-03-13 — RFC-002 Fase 1*

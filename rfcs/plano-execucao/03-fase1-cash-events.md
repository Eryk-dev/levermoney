# Fase 1: Cash Events — Prompt de Implementacao (CORRIGIDO)

> Versao corrigida do PROMPT-RFC-002.md. Incorpora todas as correcoes da auditoria.
> Este prompt e auto-contido. Implemente SOMENTE a Fase 1.

---

## Contexto

Sistema de conciliacao ML/MP <-> Conta Azul ERP. FastAPI + Supabase.
Tabela `payment_events` e o event ledger append-only com 16 event types para vendas.
Tabela `mp_expenses` armazena despesas sem pedido (mutavel, com status column).

O objetivo e adicionar uma CAMADA DE CAIXA ao ledger: cada linha do extrato (Account Statement CSV do MP) vira um evento `cash_*`. Isso permite reconciliacao automatica: sum(cash events) == extrato.

---

## Invariante de Reconciliacao

```
Para todo seller S, para todo mes M:
  sum(signed_amount WHERE event_type LIKE 'cash_%' AND seller = S AND mes(event_date) = M)
  == final_balance - initial_balance do extrato CSV de S para M
```

Para 141Air Janeiro 2026: sum deve ser R$ -3.385,83.

---

## Passo 1: Migration SQL

Arquivo: `migrations/008_unified_ledger.sql`

```sql
ALTER TABLE payment_events ADD COLUMN IF NOT EXISTS reference_id TEXT;
UPDATE payment_events SET reference_id = ml_payment_id::text WHERE reference_id IS NULL;
ALTER TABLE payment_events ALTER COLUMN ml_payment_id SET DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_pe_seller_ref ON payment_events (seller_slug, reference_id) WHERE reference_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pe_cash_events ON payment_events (seller_slug, event_date) WHERE event_type LIKE 'cash_%';
```

---

## Passo 2: Novos Event Types

Adicionar ao `EVENT_TYPES` em `event_ledger.py`:

```python
# Cash events (reconciliacao extrato) — competencia_date = event_date (caixa, NAO competencia real)
# DRE queries MUST exclude these via prefix filter
"cash_release":      "positive",    # Liberacao de venda (NET)
"cash_expense":      "negative",    # Despesa debitada
"cash_income":       "positive",    # Receita nao-venda
"cash_transfer_out": "negative",    # Dinheiro saindo
"cash_transfer_in":  "positive",    # Dinheiro entrando
"cash_internal":     "any",         # Movimento interno MP (sem impacto externo)
```

Adicionar suporte para sign "any" em `validate_event()`:
```python
if expected == "any":
    pass  # aceita qualquer sinal (cash_internal pode ser + ou -)
```

---

## Passo 3: Proteger queries existentes (CORRECAO 1)

Em `get_dre_summary()` — filtrar cash/expense events:
```python
rows = result.data or []
rows = [r for r in rows
        if not r["event_type"].startswith("cash_")
        and not r["event_type"].startswith("expense_")]
```

Em `get_payment_statuses()` — mesmo filtro para evitar pid=0 pollution.

---

## Passo 4: record_cash_event() com idempotency corrigida (CORRECAO 3)

```python
async def record_cash_event(
    seller_slug: str,
    reference_id: str,
    event_type: str,
    signed_amount: float,
    event_date: str,
    extrato_type: str,
    expense_type_abbrev: str = "xx",
    metadata: dict | None = None,
) -> dict | None:
    """Record a cash flow event from an extrato line.

    Idempotency key: {seller}:{ref_id}:{event_type}:{date}:{abbrev}
    The abbreviation prevents collision when two different transaction types
    generate the same cash event type for the same ref_id on the same day.

    NOTE: competencia_date is set equal to event_date for cash events.
    It does NOT represent accrual competencia.
    """
    if not event_type.startswith("cash_"):
        raise ValueError(f"cash_* event type required, got {event_type}")

    idem_key = f"{seller_slug}:{reference_id}:{event_type}:{event_date}:{expense_type_abbrev}"

    try:
        ml_pid = int(reference_id)
    except (ValueError, TypeError):
        ml_pid = 0

    full_metadata = {"extrato_type": extrato_type, "source": "account_statement"}
    if metadata:
        full_metadata.update(metadata)

    return await record_event(
        seller_slug=seller_slug,
        ml_payment_id=ml_pid,
        event_type=event_type,
        signed_amount=signed_amount,
        competencia_date=event_date,
        event_date=event_date,
        source="extrato",
        metadata=full_metadata,
        idempotency_key=idem_key,
        reference_id=reference_id,
    )
```

Tambem adicionar `reference_id: str | None = None` como parametro opcional de `record_event()`, e incluir no dict `row`.

---

## Passo 5: Mapeamento CORRIGIDO de extrato -> cash events (CORRECOES 2 + 4)

```python
# Para linhas classificadas com expense_type (nao-None, nao-_CHECK_PAYMENTS)
CASH_TYPE_MAP: dict[str, str] = {
    "liberacao_cancelada":      "cash_expense",
    "reembolso_disputa":        "cash_income",
    "reembolso_generico":       "cash_income",
    "entrada_dinheiro":         "cash_income",
    "dinheiro_retido":          "cash_expense",
    "difal":                    "cash_expense",
    "faturas_ml":               "cash_expense",
    "debito_envio_ml":          "cash_expense",
    "debito_divida_disputa":    "cash_expense",
    "debito_troca":             "cash_expense",
    "bonus_envio":              "cash_income",
    "subscription":             "cash_expense",
    "pagamento_cartao_credito": "cash_expense",
    "emprestimo_mp":            "cash_income",
    "liberacao_nao_sync":       "cash_release",
    "qr_pix_nao_sync":          "cash_income",
    "dinheiro_recebido":        "cash_income",
    "pix_nao_sync":             "cash_transfer_in",
}

# Para _CHECK_PAYMENTS resolvidos (ref_id ESTA em payment_events):
# -> cash_release

# Para skips (expense_type=None) — mapeamento per-rule, NAO blanket cash_internal
SKIP_TO_CASH_TYPE: dict[str, str] = {
    "transferencia pix":         "cash_transfer_out",
    "pix enviado":               "cash_transfer_out",
    "pagamento de conta":        "cash_transfer_out",
    "compra mercado libre":      "cash_expense",
    "compra mercado livre":      "cash_expense",
    "transferencia enviada":     "cash_transfer_out",
    "transferência enviada":     "cash_transfer_out",
    "compra de ":                "cash_expense",
    "transferencia de saldo":    "cash_internal",
    "transferência de saldo":    "cash_internal",
    "dinheiro reservado renda":  "cash_internal",
    "dinheiro retirado renda":   "cash_internal",
    "dinheiro reservado":        "cash_internal",
}

SKIP_ABBREV: dict[str, str] = {
    "transferencia pix": "tp",
    "pix enviado": "pe",
    "pagamento de conta": "pg",
    "compra mercado libre": "cm",
    "compra mercado livre": "cm",
    "transferencia enviada": "te",
    "transferência enviada": "te",
    "compra de ": "cd",
    "transferencia de saldo": "ts",
    "transferência de saldo": "ts",
    "dinheiro reservado renda": "rr",
    "dinheiro retirado renda": "xr",
    "dinheiro reservado": "rv",
}
```

---

## Passo 6: Script de ingesta

Criar `testes/ingest_extrato_to_ledger.py`:
```
python3 testes/ingest_extrato_to_ledger.py --seller 141air --month jan2026 [--dry-run]
```

Fluxo:
1. Ler extrato CSV
2. Parsear com `_parse_account_statement()`
3. Para cada transacao:
   a. Classificar com `_classify_extrato_line(tx_type)` -> expense_type, direction
   b. Determinar cash event type:
      - Se expense_type is None -> lookup em SKIP_TO_CASH_TYPE usando tx_type normalizado
      - Se expense_type == _CHECK_PAYMENTS -> verificar payment_events:
        - Se ref_id em payment_events -> cash_release
        - Se nao -> resolver com _resolve_check_payments -> lookup em CASH_TYPE_MAP
      - Senao -> lookup em CASH_TYPE_MAP
   c. Determinar abbreviation:
      - Se skip -> SKIP_ABBREV[normalized_tx_type]
      - Se classified -> _EXPENSE_TYPE_ABBREV[expense_type]
      - Se _CHECK_PAYMENTS resolved -> _EXPENSE_TYPE_ABBREV[fallback_type]
      - Se _CHECK_PAYMENTS em payment_events -> "cr" (cash release)
   d. Gravar record_cash_event() com idem key incluindo abbrev
4. Validar: sum(signed_amount) == final_balance - initial_balance

---

## Passo 7: get_cash_summary()

```python
async def get_cash_summary(
    seller_slug: str,
    date_from: str,
    date_to: str,
) -> dict:
    """Aggregate cash events by type for a date range (event_date)."""
    db = get_db()
    summary: dict[str, float] = {}
    page_start = 0
    page_limit = 1000
    while True:
        result = db.table(TABLE).select("event_type, signed_amount").eq(
            "seller_slug", seller_slug
        ).gte("event_date", date_from).lte(
            "event_date", date_to
        ).range(page_start, page_start + page_limit - 1).execute()

        rows = result.data or []
        for row in rows:
            et = row["event_type"]
            if not et.startswith("cash_"):
                continue
            summary[et] = round(summary.get(et, 0) + float(row["signed_amount"]), 2)

        if len(rows) < page_limit:
            break
        page_start += page_limit

    return summary
```

---

## Passo 8: Testes

Criar `testes/test_cash_events.py`:
1. test_cash_event_types_exist — 6 tipos no EVENT_TYPES
2. test_validate_cash_any_sign — cash_internal aceita + e -
3. test_record_cash_event_idempotency_includes_date — chave inclui data
4. test_record_cash_event_idempotency_includes_abbrev — chave inclui abbreviation
5. test_no_collision_different_types_same_ref — dois expense types com mesmo ref_id/dia nao colidem
6. test_get_dre_summary_excludes_cash — get_dre_summary() nao retorna cash events
7. test_get_payment_statuses_excludes_cash — get_payment_statuses() nao retorna cash events
8. test_get_cash_summary — soma correta por tipo
9. test_skip_mapping_not_all_internal — verifica que PIX enviado NAO e cash_internal

---

## Validacao Final (141Air Janeiro 2026)

```
690 cash events criados (1 por linha do extrato)
sum(signed_amount) == R$ -3.385,83
Reconciliacao dia a dia: zero gaps
Re-run idempotente: 0 duplicatas
366 testes existentes + novos testes: todos passando
DRE por competencia: inalterado
```

---

## NUNCA

- NUNCA modificar processor.py, daily_sync.py, ca_queue.py, ou mp_expenses nesta fase
- NUNCA fazer sum(signed_amount) sem filtrar por event_type prefix
- NUNCA mapear TODOS os skips como cash_internal (usar SKIP_TO_CASH_TYPE)
- NUNCA usar idempotency key sem abbreviation (risco de colisao)
- NUNCA criar cash events para dados que nao vieram do extrato CSV real

---

## Arquivos para LER antes de comecar

1. `rfcs/RFC-002-unified-event-ledger-v2.md`
2. `rfcs/plano-execucao/02-correcoes-pre-implementacao.md`
3. `app/services/event_ledger.py`
4. `app/services/extrato_ingester.py` linhas 60-210 (regras de classificacao)
5. `app/services/extrato_ingester.py` linhas 243-327 (parser CSV)
6. `testes/ingest_extrato_gaps.py` (referencia de script de ingesta)
7. `migrations/006_payment_events.sql` (schema original)

---
*Versao corrigida: 2026-03-13 — Incorpora auditoria externa*

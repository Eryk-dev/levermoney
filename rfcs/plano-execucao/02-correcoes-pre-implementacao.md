# Correcoes Pre-Implementacao — RFC-002

> Todas as correcoes abaixo DEVEM ser aplicadas antes de implementar qualquer fase do event ledger.
> Baseadas na auditoria externa dos documentos RFC-002 e PROMPT-RFC-002.

---

## CORRECAO 1: Proteger get_dre_summary() contra contaminacao

**Problema:** `get_dre_summary()` em `event_ledger.py` (linha 350) faz query por `competencia_date` sem filtrar event types. Com cash events usando `competencia_date = event_date`, a funcao retornaria cash_release, cash_expense etc. junto com sale_approved, fee_charged.

**Correcao:** Adicionar filtro explicito em `get_dre_summary()`:

```python
# ANTES (buggy com unified ledger):
result = db.table(TABLE).select("event_type, signed_amount").eq(
    "seller_slug", seller_slug
).gte("competencia_date", date_from).lte("competencia_date", date_to)

# DEPOIS (safe):
result = db.table(TABLE).select("event_type, signed_amount").eq(
    "seller_slug", seller_slug
).gte("competencia_date", date_from).lte("competencia_date", date_to)

# Filtrar cash/expense lifecycle events (nao sao DRE)
rows = [r for r in (result.data or [])
        if not r["event_type"].startswith("cash_")
        and not r["event_type"].startswith("expense_")]
```

Alternativa (mais performante): usar `.not_.like("event_type", "cash_%")` na query Supabase, mas precisa testar se PostgREST suporta NOT LIKE encadeado.

**Tambem aplicar em:** `get_payment_statuses()` — filtrar cash/expense events para evitar pid=0 pollution.

**Quando:** Fase 1, antes de criar qualquer cash event.

---

## CORRECAO 2: Mapeamento de skips para cash event types (NAO usar None -> cash_internal)

**Problema:** O PROMPT mapeia todos os skips (expense_type=None) como `cash_internal`. Mas muitos skips sao movimentos reais de dinheiro (PIX enviado, pagamento de conta, compras). So 4 sao realmente internos.

**Correcao:** Criar mapeamento per-rule em vez de blanket None -> cash_internal.

O mapeamento correto, baseado nas regras em `extrato_ingester.py` linhas 88-149:

```python
# Mapeamento de TODOS os skips (expense_type=None) para cash event types
SKIP_TO_CASH_TYPE: dict[str, str] = {
    # Movimentos reais de dinheiro (NAO sao internos)
    "transferencia pix":         "cash_transfer_out",
    "pix enviado":               "cash_transfer_out",
    "pagamento de conta":        "cash_transfer_out",
    "compra mercado libre":      "cash_expense",
    "compra mercado livre":      "cash_expense",
    "transferencia enviada":     "cash_transfer_out",
    "transferência enviada":     "cash_transfer_out",
    "compra de ":                "cash_expense",
    # Movimentos internos MP (sem impacto externo)
    "transferencia de saldo":    "cash_internal",
    "transferência de saldo":    "cash_internal",
    "dinheiro reservado renda":  "cash_internal",
    "dinheiro retirado renda":   "cash_internal",
    "dinheiro reservado":        "cash_internal",
}
```

**Implementacao:** Na funcao de ingesta de cash events, quando `_classify_extrato_line()` retorna `expense_type=None`, fazer lookup no `SKIP_TO_CASH_TYPE` usando o `transaction_type` normalizado do extrato. Se nao encontrar match, default para `cash_internal` com log de warning.

**Quando:** Fase 1, no script de ingesta.

---

## CORRECAO 3: Idempotency key deve incluir abbreviation do extrato_type

**Problema:** A chave `{seller}:{ref_id}:{event_type}:{date}` pode colidir quando dois tipos de transacao diferentes geram o mesmo cash event type para o mesmo ref_id no mesmo dia.

Exemplo real:
- "dinheiro retido" (ref=12345, 2026-01-15) -> cash_expense
- "debito por divida" (ref=12345, 2026-01-15) -> cash_expense
- Ambos geram chave `141air:12345:cash_expense:2026-01-15` -> segundo e descartado

**Correcao:** Incluir abbreviation do tipo original na chave:

```python
# Chave de idempotencia para cash events:
# {seller}:{ref_id}:{event_type}:{date}:{abbrev}
# Exemplo: 141air:12345:cash_expense:2026-01-15:dr (dinheiro_retido)
# Exemplo: 141air:12345:cash_expense:2026-01-15:dd (debito_divida_disputa)

def _cash_idem_key(seller: str, ref_id: str, event_type: str, date: str, expense_type: str) -> str:
    abbrev = _EXPENSE_TYPE_ABBREV.get(expense_type, "xx")
    return f"{seller}:{ref_id}:{event_type}:{date}:{abbrev}"
```

Para skips (expense_type=None), usar abbreviation derivada do SKIP_TO_CASH_TYPE:
```python
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

**Quando:** Fase 1, na funcao `record_cash_event()`.

---

## CORRECAO 4: Limpar CASH_TYPE_MAP — remover mapeamentos mortos

**Problema:** O CASH_TYPE_MAP no prompt contem tipos que NAO sao gerados por `_classify_extrato_line()`:
- `deposito_avulso` — nao existe nas regras de classificacao do extrato (docstring diz "Dinheiro recebido" -> deposito_avulso, mas a regra real mapeia "dinheiro recebido" -> _CHECK_PAYMENTS -> fallback `dinheiro_recebido`)
- `cashback` — vem do release_report_sync, nao do extrato classifier

**Correcao:** Remover mapeamentos que nunca serao ativados pela classificacao do extrato. O CASH_TYPE_MAP corrigido deve conter SOMENTE tipos presentes em `EXTRATO_CLASSIFICATION_RULES` (linhas 88-149 do extrato_ingester.py):

```python
CASH_TYPE_MAP = {
    # Tipos gerados por _classify_extrato_line()
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
    # Fallback types (de _CHECK_PAYMENTS_FALLBACK)
    "liberacao_nao_sync":       "cash_release",
    "qr_pix_nao_sync":          "cash_income",
    "dinheiro_recebido":        "cash_income",
    "pix_nao_sync":             "cash_transfer_in",
}

# _CHECK_PAYMENTS resolvido para payment em payment_events:
# -> cash_release (liberacao de venda conhecida)
```

**REMOVIDOS** (nao gerados pelo extrato classifier):
- ~~`deposito_avulso`~~ (tipo morto: docstring desatualizada, regra real usa `dinheiro_recebido` via fallback)
- ~~`cashback`~~ (vem do release_report_sync, nao do extrato)

**Quando:** Fase 1, no script de ingesta.

---

## CORRECAO 5: Documentar semantica de competencia_date para cash events

**Problema:** Para cash events, `competencia_date = event_date` (sao o mesmo valor). O campo "competencia_date" perde o significado original ("data do fato gerador por competencia"). Isso pode causar confusao.

**Correcao:** Adicionar docstring explicita em `record_cash_event()`:

```python
async def record_cash_event(...):
    """Record a cash flow event from an extrato line.

    NOTE: For cash events, competencia_date is set equal to event_date
    (the release date from the bank statement). It does NOT represent
    accrual-basis competencia. DRE queries MUST filter by event_type
    prefixes to avoid mixing competencia and cash events.
    """
```

E adicionar comentario no `EVENT_TYPES` dict:
```python
# Cash events — competencia_date = event_date (caixa, nao competencia real)
# DRE queries MUST exclude these (filter: event_type NOT LIKE 'cash_%')
```

**Quando:** Fase 1.

---

## Checklist de Correcoes

- [ ] C1: Filtrar cash/expense events em get_dre_summary()
- [ ] C1b: Filtrar cash/expense events em get_payment_statuses()
- [ ] C2: Criar SKIP_TO_CASH_TYPE mapeamento per-rule
- [ ] C3: Incluir abbreviation na idempotency key de cash events
- [ ] C4: Remover mapeamentos mortos do CASH_TYPE_MAP
- [ ] C5: Documentar semantica de competencia_date para cash events

---
*Criado: 2026-03-13 -- Baseado na auditoria externa*

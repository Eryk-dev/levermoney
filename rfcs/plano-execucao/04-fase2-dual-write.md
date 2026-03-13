# Fase 2: Expense Lifecycle — Dual-Write

> Pre-requisito: Fase 1 completa e validada (690 cash events, sum == R$ -3.385,83).
> Esta fase NAO remove mp_expenses. Adiciona escrita paralela no event ledger.

---

## Objetivo

Cada despesa/receita non-order gera eventos no event ledger ALEM de gravar em mp_expenses.
Ao final, todo registro em mp_expenses tem um `expense_captured` correspondente no ledger.

---

## Novos Event Types

Adicionar ao `EVENT_TYPES` em `event_ledger.py`:

```python
# Expense lifecycle events (unificacao mp_expenses)
"expense_captured":   "any",    # Despesa/receita identificada (valor com sinal)
"expense_classified": "zero",   # Classificada automaticamente (metadata: category)
"expense_reviewed":   "zero",   # Revisada por humano (metadata: approved)
"expense_exported":   "zero",   # Exportada em batch (metadata: batch_id)
```

---

## Funcao record_expense_event()

```python
async def record_expense_event(
    seller_slug: str,
    payment_id: str,           # plain ou composite (ex: "12345:df")
    event_type: str,
    signed_amount: float,
    competencia_date: str,
    expense_type: str,
    metadata: dict | None = None,
) -> dict | None:
    """Record an expense lifecycle event.

    Idempotency key: {seller}:{payment_id}:{event_type}
    """
    idem_key = f"{seller_slug}:{payment_id}:{event_type}"

    try:
        ml_pid = int(payment_id.split(":")[0])
    except (ValueError, TypeError):
        ml_pid = 0

    full_metadata = {"expense_type": expense_type}
    if metadata:
        full_metadata.update(metadata)

    return await record_event(
        seller_slug=seller_slug,
        ml_payment_id=ml_pid,
        event_type=event_type,
        signed_amount=signed_amount,
        competencia_date=competencia_date,
        event_date=competencia_date,
        source="expense_lifecycle",
        metadata=full_metadata,
        idempotency_key=idem_key,
        reference_id=payment_id,
    )
```

---

## Arquivos a Modificar

### 1. `app/services/expense_classifier.py`

Na funcao `classify_non_order_payment()`, APOS o upsert em mp_expenses, adicionar:

```python
# Dual-write: gravar expense_captured no event ledger
from app.services.event_ledger import record_expense_event

if inserted_row:
    amount = float(inserted_row.get("amount", 0))
    signed = amount if direction == "income" else -abs(amount)
    date_str = (inserted_row.get("date_approved") or inserted_row.get("date_created") or "")[:10]
    await record_expense_event(
        seller_slug=seller_slug,
        payment_id=str(payment_id),
        event_type="expense_captured",
        signed_amount=signed,
        competencia_date=date_str,
        expense_type=expense_type,
    )
    if inserted_row.get("auto_categorized"):
        await record_expense_event(
            seller_slug=seller_slug,
            payment_id=str(payment_id),
            event_type="expense_classified",
            signed_amount=0,
            competencia_date=date_str,
            expense_type=expense_type,
            metadata={"ca_category": inserted_row.get("ca_category")},
        )
```

### 2. `app/services/extrato_ingester.py`

Na funcao `ingest_extrato_for_seller()`, APOS inserir em mp_expenses (bloco "d. Insert new"),
adicionar dual-write identico ao do classifier.

### 3. `app/services/release_report_sync.py`

Na funcao `sync_release_report()`, APOS inserir em mp_expenses (payouts, cashback, shipping),
adicionar dual-write.

### 4. `app/routers/expenses/export.py`

APOS marcar rows como "exported" em mp_expenses, gravar `expense_exported` no ledger:

```python
for row in rows:
    await record_expense_event(
        seller_slug=seller_slug,
        payment_id=str(row["payment_id"]),
        event_type="expense_exported",
        signed_amount=0,
        competencia_date=(row.get("date_approved") or row.get("date_created") or "")[:10],
        expense_type=row.get("expense_type", "unknown"),
        metadata={"batch_id": batch_id},
    )
```

---

## Validacao

1. Rodar classifier + extrato ingester para 141air jan/2026
2. Verificar: todo `mp_expenses` tem `expense_captured` no ledger

```sql
-- Contagem mp_expenses
SELECT COUNT(*) FROM mp_expenses WHERE seller_slug = '141air';

-- Contagem expense_captured no ledger
SELECT COUNT(*) FROM payment_events
WHERE seller_slug = '141air' AND event_type = 'expense_captured';

-- Devem ser iguais
```

3. 366+ testes pytest passando
4. DRE inalterado (get_dre_summary exclui expense_* events)
5. Export ZIP funciona normalmente (ainda le de mp_expenses)

---

## O que NAO muda

- mp_expenses continua sendo a fonte para export, admin panel, financial closing
- CA jobs e baixas nao mudam
- Nenhum consumer le do ledger para expenses ainda (isso e Fase 3)

---

## Derivacao de Status (para referencia futura, Fase 3)

```python
def derive_expense_status(event_types: set[str]) -> str:
    if "expense_exported" in event_types:
        return "exported"
    if "expense_reviewed" in event_types:
        return "reviewed"
    if "expense_classified" in event_types:
        return "auto_categorized"
    if "expense_captured" in event_types:
        return "pending_review"
    return "unknown"
```

---

*Fase 2 — Dual-write transitorio. NAO remove mp_expenses.*

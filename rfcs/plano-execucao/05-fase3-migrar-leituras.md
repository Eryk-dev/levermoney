# Fase 3: Migrar Leituras — mp_expenses → Event Ledger

> Pre-requisito: Fase 2 completa e validada (dual-write estavel por 2+ semanas).
> Apos esta fase, mp_expenses continua recebendo escritas (dual-write), mas NENHUM consumer le dela.

---

## Objetivo

Todos os consumers que hoje leem de `mp_expenses` passam a ler do event ledger (`payment_events`).
A tabela `mp_expenses` continua existindo e recebendo dual-write, mas e irrelevante para o fluxo.

---

## Helpers Novos no event_ledger.py

### get_pending_exports()

```python
async def get_pending_exports(
    seller_slug: str,
    date_from: str | None = None,
    date_to: str | None = None,
    status_filter: list[str] | None = None,
) -> list[dict]:
    """Return expense events pending export.

    Logic:
    - Select all expense_captured events for seller
    - LEFT JOIN with expense_exported events (same reference_id)
    - Return only those WITHOUT a matching expense_exported
    - Enrich with metadata from expense_classified (ca_category, etc.)
    """
```

### get_expense_list()

```python
async def get_expense_list(
    seller_slug: str,
    status: str | None = None,
    expense_type: str | None = None,
    direction: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List expenses from ledger with filters.

    Derives status via derive_expense_status() for each unique reference_id.
    Reconstructs the same response shape as mp_expenses for backward compatibility.
    """
```

### get_expense_stats()

```python
async def get_expense_stats(
    seller_slug: str,
    date_from: str | None = None,
    date_to: str | None = None,
    status_filter: list[str] | None = None,
) -> dict:
    """Compute expense stats from ledger.

    Groups expense_captured events by expense_type, direction, derived status.
    Returns same shape as current expense_stats endpoint.
    """
```

### derive_expense_status() (ja definido na Fase 2)

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

## Arquivos a Modificar

### 1. `app/services/financial_closing.py`

**Funcao:** `_compute_manual_lane()`

**Hoje:** Le de `mp_expenses` diretamente (linha 91):
```python
q = db.table("mp_expenses").select(
    "payment_id, amount, expense_direction, status, date_created, date_approved"
).eq("seller_slug", seller_slug)
```

**Depois:** Le do event ledger:
```python
from app.services.event_ledger import get_expense_list, derive_expense_status

# Buscar expense_captured events com metadados
events = await get_expense_events_for_closing(seller_slug, date_from, date_to)

# Para cada expense, derivar status a partir dos eventos
for expense in events:
    expense["status"] = derive_expense_status(expense["event_types"])
```

**Impacto:**
- `_compute_manual_lane()` precisa virar `async` (ja e chamada de contexto async)
- A logica de `_signed_amount()` nao muda (usa expense_direction do metadata)
- `imported_by_day` continua lendo de `expense_batches` (nao muda — batches sao de controle, nao de dados)
- `_batch_tables_available()` nao muda

**Cuidados:**
- O `date_created` em mp_expenses filtra por ISO timestamp com fuso. No ledger, `competencia_date` e DATE. Ajustar filtro.
- O campo `date_approved` em mp_expenses e usado para agrupar por dia. No ledger, vem do metadata.

### 2. `app/services/extrato_coverage_checker.py`

**Funcao:** `_lookup_expense_ids()`

**Hoje:** Busca em `mp_expenses` (linha 54):
```python
result = db.table("mp_expenses").select("payment_id").eq(
    "seller_slug", seller_slug
).in_("payment_id", chunk).execute()
```

**Depois:** Busca expense_captured events no ledger:
```python
async def _lookup_expense_ids(seller_slug: str, source_ids: list[int]) -> set[int]:
    """Look up which source IDs exist as expense_captured in payment_events."""
    from app.services import event_ledger
    db = get_db()
    found: set[int] = set()
    for i in range(0, len(source_ids), 100):
        chunk = source_ids[i:i + 100]
        result = db.table("payment_events").select("reference_id").eq(
            "seller_slug", seller_slug
        ).eq("event_type", "expense_captured").in_(
            "reference_id", [str(x) for x in chunk]
        ).execute()
        for r in (result.data or []):
            try:
                found.add(int(r["reference_id"]))
            except (ValueError, TypeError):
                pass
    return found
```

**Impacto:**
- Funcao precisa virar `async` (chamada de contexto async)
- Assinatura muda: remover `db` parameter (usar `get_db()` internamente)
- Caller `check_extrato_coverage()` ja e async, ajustar chamada

### 3. `app/routers/expenses/crud.py`

**Endpoints afetados:**
- `GET /{seller_slug}` (list_expenses)
- `PATCH /review/{seller_slug}/{expense_id}` (review_expense)
- `GET /{seller_slug}/pending-summary` (pending_review_summary)
- `GET /{seller_slug}/stats` (expense_stats)

**list_expenses — Depois:**
```python
from app.services.event_ledger import get_expense_list

@router.get("/{seller_slug}", dependencies=[Depends(require_admin)])
async def list_expenses(seller_slug, status, expense_type, direction, date_from, date_to, limit, offset):
    expenses = await get_expense_list(
        seller_slug, status, expense_type, direction, date_from, date_to, limit, offset
    )
    return {"seller": seller_slug, "count": len(expenses), "offset": offset, "data": expenses}
```

**review_expense — Depois:**
```python
from app.services.event_ledger import record_expense_event

@router.patch("/review/{seller_slug}/{expense_id}", dependencies=[Depends(require_admin)])
async def review_expense(seller_slug, expense_id, req):
    # expense_id aqui e o reference_id (payment_id) no ledger
    # Gravar expense_reviewed event
    await record_expense_event(
        seller_slug=seller_slug,
        payment_id=str(expense_id),
        event_type="expense_reviewed",
        signed_amount=0,
        competencia_date=...,  # buscar do expense_captured original
        expense_type=...,      # buscar do expense_captured original
        metadata={"approved": True, "reviewer": "admin", **req.model_dump(exclude_none=True)},
    )
```

**Nota sobre review:** No modelo mutavel, review atualiza o status IN PLACE. No ledger, review
adiciona um NOVO evento. O status e derivado. Isso e melhor porque mantem o historico.

**expense_stats — Depois:**
```python
from app.services.event_ledger import get_expense_stats

@router.get("/{seller_slug}/stats", dependencies=[Depends(require_admin)])
async def expense_stats(seller_slug, date_from, date_to, status_filter):
    stats = await get_expense_stats(seller_slug, date_from, date_to,
        [s.strip() for s in status_filter.split(",")] if status_filter else None)
    return stats
```

### 4. `app/routers/expenses/export.py`

**Endpoint afetado:** `GET /{seller_slug}/export` (export_expenses)

**Hoje:** Queries `mp_expenses` diretamente (linha 136):
```python
q = db.table("mp_expenses").select("*").eq("seller_slug", seller_slug)
```

**Depois:**
```python
from app.services.event_ledger import get_pending_exports, record_expense_event

@router.get("/{seller_slug}/export", dependencies=[Depends(require_admin)])
async def export_expenses(seller_slug, ...):
    # Buscar expenses pendentes do ledger
    rows = await get_pending_exports(seller_slug, date_from, date_to,
        [s.strip() for s in status_filter.split(",")] if status_filter else None)

    # Build ZIP (mesma logica de XLSX)
    ...

    # Marcar como exported: gravar expense_exported events
    if mark_exported and rows:
        batch_id = f"exp_{uuid4().hex[:24]}"
        for row in rows:
            await record_expense_event(
                seller_slug=seller_slug,
                payment_id=row["reference_id"],
                event_type="expense_exported",
                signed_amount=0,
                competencia_date=row["competencia_date"],
                expense_type=row.get("expense_type", "unknown"),
                metadata={"batch_id": batch_id},
            )
```

**Cuidados:**
- O format do `rows` retornado por `get_pending_exports()` deve ter os mesmos campos
  que `_build_xlsx()` espera: `amount`, `expense_direction`, `ca_category`, `description`,
  `payment_id`, `date_approved`, `date_created`, `auto_categorized`, `external_reference`,
  `raw_payment`, `notes`
- O `get_pending_exports()` reconstroi esses campos a partir do metadata do expense_captured
- Batch tables (`expense_batches`, `expense_batch_items`) continuam funcionando — sao de controle
- GDrive backup nao muda

### 5. `app/routers/expenses/closing.py`

**Endpoint afetado:** Closing status per seller.

**Verificar:** Se le de mp_expenses diretamente ou usa `financial_closing.py`.
Se usa `financial_closing.py`, nao precisa mudar (ja coberto no item 1).

---

## Compatibilidade de Dados

O evento `expense_captured` no ledger deve conter metadata suficiente para reconstruir
a resposta que mp_expenses dava. Campos criticos no metadata:

```python
metadata = {
    "expense_type": "difal",           # tipo original
    "expense_direction": "expense",     # expense, income, transfer
    "ca_category": "1.4.1 ...",        # categoria CA (se auto_categorized)
    "auto_categorized": True,          # flag
    "description": "Débito por ...",   # descricao original
    "amount": 11.04,                   # valor absoluto (sinal em signed_amount)
    "date_created": "2026-01-21T...",  # data original
    "date_approved": "2026-01-21T...", # data de aprovacao
    "business_branch": "...",          # filial
    "operation_type": "...",           # tipo de operacao ML
    "payment_method": "...",           # metodo de pagamento
    "external_reference": "...",       # referencia externa
    "beneficiary_name": "...",         # nome do beneficiario
    "notes": "...",                    # notas
}
```

**IMPORTANTE:** Esse metadata e gravado na Fase 2 (dual-write). Se a Fase 2 nao gravar
todos esses campos, a Fase 3 nao tera dados suficientes. Revisar o dual-write da Fase 2
antes de comecar a Fase 3.

---

## Validacao

1. Para cada endpoint de expenses, comparar resposta old (mp_expenses) vs new (ledger):
   - Mesma contagem de rows
   - Mesmos valores de amount
   - Mesmos status derivados

2. Financial closing: comparar resultado old vs new para 141air jan/2026

3. Extrato coverage: comparar resultado old vs new

4. Export ZIP: gerar com old e new, comparar conteudo dos XLSX

5. 366+ testes pytest passando

6. Deploy com feature flag: `EXPENSES_SOURCE=ledger` vs `EXPENSES_SOURCE=mp_expenses`

---

## Feature Flag (Recomendado)

Para migrar com seguranca, usar feature flag:

```python
# app/config.py
expenses_source: str = "mp_expenses"  # "mp_expenses" | "ledger"
```

Isso permite:
- Deploy com `expenses_source=mp_expenses` (comportamento atual)
- Validar em staging com `expenses_source=ledger`
- Rollback instantaneo se algo quebrar

Cada consumer verifica:
```python
if settings.expenses_source == "ledger":
    rows = await get_expense_list(...)
else:
    rows = db.table("mp_expenses").select(...).execute().data
```

Quando tudo estiver validado por 2+ semanas, remover o flag e o codigo old.

---

## O que NAO muda

- `expense_classifier.py` — continua escrevendo em mp_expenses + ledger (dual-write)
- `extrato_ingester.py` — continua escrevendo em mp_expenses + ledger (dual-write)
- `release_report_sync.py` — continua escrevendo em mp_expenses + ledger (dual-write)
- `expense_batches` e `expense_batch_items` — tabelas de controle, nao de dados. Continuam.
- Processor, daily_sync, ca_queue — nao tocados

---

## Riscos

### R1: Metadata incompleto do expense_captured

Se a Fase 2 nao gravou todos os campos necessarios no metadata, os helpers da Fase 3
retornam dados incompletos. Mitigacao: validar completude do metadata ANTES de comecar Fase 3.

### R2: Performance de queries derivadas

Derivar status de `set(event_types)` para cada expense requer GROUP BY no SQL. Pode ser
mais lento que um simples WHERE status = 'pending_review'. Mitigacao: index parcial
`idx_pe_expense_events` (criado na migration 008) + cache em helpers.

### R3: Export requer todos os campos do XLSX

`_build_xlsx()` espera ~10 campos especificos. Todos devem estar no metadata do
expense_captured. Se algum faltar, o XLSX sai incompleto. Mitigacao: criar teste que
compara output do export old vs new antes de migrar.

---

*Fase 3 — Migrar leituras de mp_expenses para event ledger. mp_expenses continua recebendo dual-write.*

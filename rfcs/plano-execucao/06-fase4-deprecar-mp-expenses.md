# Fase 4: Deprecar mp_expenses

> Pre-requisito: Fase 3 completa e estavel em producao por 2+ meses.
> Apos esta fase, mp_expenses deixa de existir como tabela mutavel.

---

## Objetivo

Eliminar a tabela `mp_expenses` como fonte de dados ativa. Todo o ciclo de vida de despesas
(captura, classificacao, revisao, export) vive exclusivamente no event ledger.

---

## Etapas

### Etapa 1: Parar de Escrever em mp_expenses (remover dual-write)

#### 1a. `app/services/expense_classifier.py`

Remover o bloco de upsert em mp_expenses. Manter APENAS a escrita no ledger.

**Antes (Fase 2):**
```python
# Upsert em mp_expenses
db.table("mp_expenses").upsert(row).execute()

# Dual-write no ledger
await record_expense_event(...)
```

**Depois (Fase 4):**
```python
# APENAS ledger
await record_expense_event(
    seller_slug=seller_slug,
    payment_id=str(payment_id),
    event_type="expense_captured",
    signed_amount=signed,
    competencia_date=date_str,
    expense_type=expense_type,
    metadata={...todos os campos...},
)
if auto_categorized:
    await record_expense_event(
        ...,
        event_type="expense_classified",
        ...
    )
```

#### 1b. `app/services/extrato_ingester.py`

Mesmo padrao: remover upsert em mp_expenses, manter apenas ledger.

**Cuidado extra:** `ingest_extrato_for_seller()` e uma funcao grande (~360 linhas).
O bloco de insercao em mp_expenses esta espalhado em multiplos pontos:
- Bloco "d. Insert new" (~linha 950)
- Bloco de update de status (~linha 1000)
- Bloco de dedup (~linha 900)

Todos devem ser removidos. A dedup usa o ledger (idempotency_key).

#### 1c. `app/services/release_report_sync.py`

Remover insercao em mp_expenses para payouts, cashback, shipping.

#### 1d. `app/routers/expenses/export.py`

Remover o bloco que marca rows como "exported" em mp_expenses:
```python
# REMOVER:
db.table("mp_expenses").update({
    "status": "exported",
    "exported_at": now,
}).in_("id", chunk).execute()
```

O export agora grava `expense_exported` events no ledger (ja feito na Fase 3).

---

### Etapa 2: Migrar Dados Historicos

Todos os registros em mp_expenses que NAO tem expense_captured correspondente no ledger
precisam ser migrados.

**Script:** `testes/migrate_mp_expenses_to_ledger.py`

```python
"""One-time migration: mp_expenses → expense_captured events in ledger."""

async def migrate():
    db = get_db()

    # 1. Buscar todos mp_expenses
    expenses = paginate(db.table("mp_expenses").select("*").order("date_created"))

    # 2. Para cada expense, verificar se ja tem expense_captured
    for exp in expenses:
        idem_key = f"{exp['seller_slug']}:{exp['payment_id']}:expense_captured"
        existing = db.table("payment_events").select("id").eq(
            "idempotency_key", idem_key
        ).execute()

        if existing.data:
            continue  # ja migrado

        # 3. Gravar expense_captured
        signed = _compute_signed(exp)
        await record_expense_event(
            seller_slug=exp["seller_slug"],
            payment_id=str(exp["payment_id"]),
            event_type="expense_captured",
            signed_amount=signed,
            competencia_date=_extract_date(exp),
            expense_type=exp.get("expense_type", "unknown"),
            metadata=_build_metadata(exp),
        )

        # 4. Se auto_categorized, gravar expense_classified
        if exp.get("auto_categorized"):
            await record_expense_event(
                ...,
                event_type="expense_classified",
                metadata={"ca_category": exp.get("ca_category")},
            )

        # 5. Se exported, gravar expense_exported
        if exp.get("status") == "exported":
            await record_expense_event(
                ...,
                event_type="expense_exported",
                metadata={"batch_id": "legacy_migration", "exported_at": exp.get("exported_at")},
            )

        # 6. Se manually_categorized, gravar expense_reviewed
        if exp.get("status") == "manually_categorized":
            await record_expense_event(
                ...,
                event_type="expense_reviewed",
                metadata={"source": "legacy_migration"},
            )

    print(f"Migrated {count} expenses to ledger")
```

**Validacao pos-migracao:**
```sql
-- Contagem mp_expenses por seller
SELECT seller_slug, COUNT(*) FROM mp_expenses GROUP BY seller_slug;

-- Contagem expense_captured por seller
SELECT seller_slug, COUNT(*) FROM payment_events
WHERE event_type = 'expense_captured'
GROUP BY seller_slug;

-- Devem ser iguais
```

---

### Etapa 3: Remover Imports e Referencias a mp_expenses

Buscar em todo o codebase:

```bash
grep -r "mp_expenses" app/ --include="*.py" -l
```

Arquivos esperados (apos Fase 3):
- `app/services/expense_classifier.py` — remover upsert (Etapa 1a)
- `app/services/extrato_ingester.py` — remover upsert (Etapa 1b)
- `app/services/release_report_sync.py` — remover upsert (Etapa 1c)
- `app/services/financial_closing.py` — ja migrado na Fase 3
- `app/services/extrato_coverage_checker.py` — ja migrado na Fase 3
- `app/routers/expenses/crud.py` — ja migrado na Fase 3
- `app/routers/expenses/export.py` — ja migrado na Fase 3
- `app/services/daily_sync.py` — usa mp_expenses para dedup (`already_done_expenses`). Migrar para ledger.
- `app/services/onboarding_backfill.py` — usa mp_expenses para dedup. Migrar para ledger.

**daily_sync.py dedup:**

Hoje:
```python
done_expenses = set()
result = db.table("mp_expenses").select("payment_id").eq("seller_slug", seller_slug)
for r in result.data:
    done_expenses.add(int(r["payment_id"]))
```

Depois:
```python
done_expenses = set()
result = db.table("payment_events").select("reference_id").eq(
    "seller_slug", seller_slug
).eq("event_type", "expense_captured")
for r in result.data:
    try:
        done_expenses.add(int(r["reference_id"]))
    except (ValueError, TypeError):
        pass
```

**onboarding_backfill.py dedup:** Mesmo padrao.

---

### Etapa 4: Migration SQL — Deprecar Tabela

Criar `migrations/008_deprecate_mp_expenses.sql`:

**Opcao A: Rename + View (conservadora)**

```sql
-- Rename para backup
ALTER TABLE mp_expenses RENAME TO mp_expenses_deprecated;

-- View para compatibilidade (queries que escaparam)
CREATE VIEW mp_expenses AS
SELECT
    pe.id,
    pe.seller_slug,
    pe.reference_id AS payment_id,
    ABS(pe.signed_amount) AS amount,
    pe.metadata->>'expense_type' AS expense_type,
    pe.metadata->>'expense_direction' AS expense_direction,
    pe.metadata->>'ca_category' AS ca_category,
    (pe.metadata->>'auto_categorized')::boolean AS auto_categorized,
    pe.metadata->>'description' AS description,
    pe.metadata->>'business_branch' AS business_branch,
    pe.metadata->>'operation_type' AS operation_type,
    pe.metadata->>'payment_method' AS payment_method,
    pe.metadata->>'external_reference' AS external_reference,
    pe.metadata->>'beneficiary_name' AS beneficiary_name,
    pe.metadata->>'notes' AS notes,
    pe.competencia_date::text AS date_approved,
    pe.competencia_date::text AS date_created,
    -- Derive status from events
    CASE
        WHEN EXISTS (SELECT 1 FROM payment_events pe2
            WHERE pe2.seller_slug = pe.seller_slug
            AND pe2.reference_id = pe.reference_id
            AND pe2.event_type = 'expense_exported') THEN 'exported'
        WHEN EXISTS (SELECT 1 FROM payment_events pe2
            WHERE pe2.seller_slug = pe.seller_slug
            AND pe2.reference_id = pe.reference_id
            AND pe2.event_type = 'expense_reviewed') THEN 'manually_categorized'
        WHEN EXISTS (SELECT 1 FROM payment_events pe2
            WHERE pe2.seller_slug = pe.seller_slug
            AND pe2.reference_id = pe.reference_id
            AND pe2.event_type = 'expense_classified') THEN 'auto_categorized'
        ELSE 'pending_review'
    END AS status,
    pe.created_at
FROM payment_events pe
WHERE pe.event_type = 'expense_captured';
```

**Opcao B: Drop (agressiva)**

```sql
-- Apenas se 100% certo que nenhuma query le mp_expenses
DROP TABLE IF EXISTS mp_expenses;
```

**Recomendacao:** Opcao A primeiro. Depois de 1 mes sem erros, trocar para Opcao B.

---

## Remocao de Codigo

Apos a Etapa 3, remover:

1. **Feature flag** `expenses_source` de `app/config.py` (se adicionado na Fase 3)
2. **Codigo old** dos consumers (branches `else: mp_expenses`)
3. **Testes** que referenciam mp_expenses diretamente
4. **Documentacao**: atualizar CLAUDE.md, docs/TABELAS.md, docs/CODE_MAP.md

---

## Checklist Final

- [ ] expense_classifier.py NAO escreve em mp_expenses
- [ ] extrato_ingester.py NAO escreve em mp_expenses
- [ ] release_report_sync.py NAO escreve em mp_expenses
- [ ] daily_sync.py dedup usa payment_events (expense_captured)
- [ ] onboarding_backfill.py dedup usa payment_events (expense_captured)
- [ ] financial_closing.py le do ledger (Fase 3)
- [ ] extrato_coverage_checker.py le do ledger (Fase 3)
- [ ] expenses/crud.py le do ledger (Fase 3)
- [ ] expenses/export.py le do ledger (Fase 3)
- [ ] Dados historicos migrados (script Etapa 2)
- [ ] `grep -r "mp_expenses" app/` retorna ZERO resultados
- [ ] Migration 008 aplicada (rename + view)
- [ ] 366+ testes passando
- [ ] DRE inalterado
- [ ] Export ZIP funciona com dados do ledger

---

## Riscos

### R1: Dados historicos incompletos

Se algum mp_expense nao foi migrado na Etapa 2, o closing e export mostram dados
incompletos. Mitigacao: script de validacao que compara contagens old vs new por seller+mes.

### R2: Performance da view

A view `mp_expenses` (Opcao A) usa subqueries EXISTS que podem ser lentas em tabelas grandes.
Mitigacao: usar como safety net apenas, nao como query principal. Se precisar, criar
materialized view com refresh periodico.

### R3: Queries escaparam

Alguma query direta em mp_expenses pode nao ter sido identificada no grep.
Mitigacao: monitorar logs de erro apos o deploy. A view (Opcao A) captura essas queries
e retorna dados corretos, dando tempo para corrigir.

---

## Timeline Recomendada

```
Fase 3 completa ──────────────────── Semana 0
Observar estabilidade ─────────────── Semana 1-8 (2 meses)
Etapa 1: Parar dual-write ─────────── Semana 9
Etapa 2: Migrar dados historicos ──── Semana 9
Etapa 3: Remover imports ──────────── Semana 10
Etapa 4: Migration 008 ───────────── Semana 11
Observar view (Opcao A) ──────────── Semana 11-15
Drop table (Opcao B) ─────────────── Semana 16+
```

---

*Fase 4 — Deprecar mp_expenses. Evento e a unica fonte de verdade.*

# RFC-001: Unified Event Ledger para todos os movimentos financeiros

**Status:** Draft
**Autor:** Eryk
**Data:** 2026-03-12
**Prioridade:** Futura (sem prazo definido)

---

## Motivacao

Hoje o sistema tem **duas fontes de verdade** para movimentos financeiros:

| Fonte | O que armazena |
|-------|---------------|
| `payment_events` | Vendas com pedido (receita, comissao, frete, estorno) |
| `mp_expenses` | Tudo sem pedido (boletos, SaaS, cashback, payouts, DIFAL, etc.) |

Isso gera problemas praticos:
- **DRE** precisa cruzar duas tabelas
- **Reconciliacao com extrato** exige logica dupla
- **Auditoria** fica fragmentada — nao ha um lugar unico pra ver todo o fluxo de dinheiro
- **Status de despesas** e um campo mutavel em `mp_expenses`, enquanto vendas derivam status de eventos imutaveis

---

## Proposta

Estender o `payment_events` (event ledger) para registrar **todos** os movimentos financeiros, incluindo pagamentos sem pedido, payouts, e itens de release report/extrato.

### Novos event types (proposta inicial)

```
expense_captured      (+/- valor)   # pagamento nao-order capturado da API ML
expense_classified    (0)           # classificado automaticamente (metadata: category, confidence)
expense_reviewed      (0)           # revisado por humano (metadata: approved, reviewer)
expense_exported      (0)           # exportado em batch (metadata: batch_id)
payout_recorded       (- valor)     # saque bancario (do release report)
shipping_credit       (+ valor)     # credito de frete (do release report)
```

### Derivacao de status (mesma logica das vendas)

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

### Fluxo proposto

```
ML API (pagamento sem order)
    -> expense_captured (grava valor no ledger)
    -> expense_classified (classificador automatico)
    -> expense_reviewed (humano aprova/rejeita)
    -> expense_exported (batch export para CA)
```

---

## Beneficios

1. **DRE de uma query so** — `SELECT event_type, SUM(signed_amount) FROM payment_events WHERE competencia_date BETWEEN ... GROUP BY event_type`
2. **Auditoria completa** — todo movimento financeiro tem timestamp, source, metadata
3. **Reconciliacao simplificada** — extrato bancario vs event ledger, sem cruzar tabelas
4. **Consistencia arquitetural** — um padrao unico (append-only events) pra tudo

---

## Questoes em aberto

Antes de implementar, precisa definir:

1. **Itens sem `ml_payment_id`**: payouts do release report nao tem payment_id. Usar um ID sintetico? Ou adicionar campo opcional?
2. **Automacao CA**: despesas continuam sendo export em lote (ZIP) ou passam a ser jobs individuais no `ca_jobs`?
3. **Revisao humana**: todo pagamento nao-order precisa revisao, ou classificacoes de alta confianca podem ir direto?
4. **Migracao**: dados historicos do `mp_expenses` sao migrados pro ledger ou so novos registros?
5. **Destino do `mp_expenses`**: vira view materializada, tabela de staging, ou desaparece?
6. **Multi-marketplace**: se amanha entrar Shopee/Amazon, o ledger ja deve suportar?

---

## Impacto estimado

### Arquivos afetados
- `event_ledger.py` — novos event types, novo derive_expense_status
- `expense_classifier.py` — gravar eventos em vez de inserir em mp_expenses
- `release_report_sync.py` — gravar eventos para payouts/cashback/shipping
- `extrato_ingester.py` — gravar eventos para DIFAL/faturas/disputes
- `daily_sync.py` — adaptar fluxo non-order
- `financial_closing.py` — simplificar (uma fonte so)
- Routers de expenses — ler do ledger em vez de mp_expenses
- Dashboard admin — adaptar queries

### Risco
Medio-alto. Toca em quase todos os services. Recomenda-se implementar de forma incremental:
1. Primeiro: gravar eventos em paralelo (dual-write) sem remover mp_expenses
2. Validar que ledger e mp_expenses estao consistentes
3. Migrar leituras pro ledger
4. Deprecar mp_expenses

---

## Referencias

- `app/services/event_ledger.py` — implementacao atual
- `app/services/expense_classifier.py` — classificador de despesas
- `docs/TABELAS.md` — schema mp_expenses e payment_events
- Conversa de 2026-03-12 sobre unificacao do ledger

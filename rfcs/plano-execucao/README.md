# Plano de Execucao — Unified Event Ledger + Upload Extrato

## Ordem de Execucao

Duas trilhas paralelas que podem ser implementadas nesta ordem:

### Track A: Upload de Extrato (feature pratica — resolver AGORA)
1. `01-upload-extrato.md` — Endpoint admin + tela para upload do extrato CSV do Mercado Pago. Roda a logica existente do extrato_ingester. Novas despesas aparecem em mp_expenses. Export ZIP existente funciona.

### Track B: Unified Event Ledger (arquitetura — implementar apos Track A)
2. `02-correcoes-pre-implementacao.md` — Todas as correcoes identificadas pela auditoria antes de implementar
3. `03-fase1-cash-events.md` — Prompt corrigido: schema + cash events + validacao 141air jan/2026
4. `04-fase2-dual-write.md` — Expense lifecycle events + dual-write com mp_expenses
5. `05-fase3-migrar-leituras.md` — Financial closing, coverage checker, export leem do ledger
6. `06-fase4-deprecar-mp-expenses.md` — mp_expenses vira read-only, depois depreca

## Dependencias

```
Track A (upload extrato)
    |
    +-- independente, pode ser feito AGORA
    |
Track B (event ledger)
    |
    +-- Fase 1 (cash events) <-- depende de Track A estar funcionando para ter dados
    +-- Fase 2 (dual-write) <-- depende de Fase 1 validada
    +-- Fase 3 (migrar leituras) <-- depende de Fase 2 estavel
    +-- Fase 4 (deprecar mp_expenses) <-- depende de Fase 3 em producao por 2+ meses
```

## Arquitetura Alvo

```
HOJE:
  Vendas   -> payment_events (event sourcing)
  Despesas -> mp_expenses (status mutavel)
  Extrato  -> scripts manuais CLI

FUTURO:
  Vendas   -> payment_events: sale_approved, fee_charged, etc. (competencia)
  Despesas -> payment_events: expense_captured, expense_classified, etc. (competencia)
  Extrato  -> payment_events: cash_release, cash_expense, etc. (caixa)
  Upload   -> Admin panel: upload CSV -> ingesta automatica
  mp_expenses -> deprecada
```

## Validacao

Piloto: 141Air Janeiro 2026
- 690 linhas no extrato
- R$ 4.476,23 saldo inicial -> R$ 1.090,40 saldo final
- NET movement: R$ -3.385,83
- 366 testes pytest existentes devem passar em todas as fases

## Referencias

- `rfcs/RFC-002-unified-event-ledger-v2.md` — RFC completa
- `rfcs/PROMPT-RFC-002.md` — Prompt original (contem erros — corrigidos nesta pasta)
- `PLANO_FUNDACAO.md` — Contexto do projeto
- `app/services/event_ledger.py` — Ledger atual
- `app/services/extrato_ingester.py` — Classificacao do extrato

---
*Criado: 2026-03-13*

# PRD Master: LeverMoney v3 — Unified Event Ledger + Upload Extrato

## 1. Introduction/Overview

LeverMoney v2 tem duas fontes de verdade para movimentos financeiros: `payment_events` (vendas, event sourcing imutavel) e `mp_expenses` (despesas, tabela mutavel com status column). Isso fragmenta DRE, reconciliacao, auditoria e export.

O v3 unifica tudo em um unico event ledger append-only, adiciona uma camada de caixa para reconciliacao automatica com o extrato bancario, e cria um fluxo operacional de upload de extrato pelo admin panel.

## 2. Goals

- Unificar todas as movimentacoes financeiras em uma unica tabela imutavel (`payment_events`)
- Permitir reconciliacao automatica: `sum(cash events) == extrato CSV`
- Eliminar a tabela mutavel `mp_expenses` (fonte de bugs e inconsistencias)
- Criar fluxo operacional de upload de extrato via admin panel
- Manter 100% backward compatibility durante a transicao (dual-write)
- Validar com dados reais 141Air janeiro 2026 em cada etapa

## 3. Etapas (PRDs Detalhados)

| Etapa | PRD | Dependencia | Descricao |
|-------|-----|-------------|-----------|
| 1 | `prd-upload-extrato.md` | Nenhuma | Upload extrato CSV no admin panel |
| 2 | `prd-cash-events.md` | Etapa 1 | Cash events no ledger (camada de caixa) |
| 3 | `prd-dual-write.md` | Etapa 2 | Expense lifecycle + dual-write |
| 4 | `prd-migrar-leituras.md` | Etapa 3 | Consumers migram para ledger |
| 5 | `prd-deprecar-mp-expenses.md` | Etapa 4 | Eliminar mp_expenses |

## 4. Ordem de Execucao

```
Etapa 1 (upload extrato) ──→ Etapa 2 (cash events) ──→ Etapa 3 (dual-write)
                                                              │
                                                              ▼
                                                     Etapa 4 (migrar leituras)
                                                              │
                                                              ▼
                                                     Etapa 5 (deprecar mp_expenses)
```

## 5. Non-Goals (Out of Scope)

- Alterar processor.py, daily_sync.py, ou ca_queue.py (exceto Etapa 5)
- Mudar o fluxo de baixas ou CA jobs
- Criar dashboard de reconciliacao (futuro)
- Automatizar download do extrato via API ML (confirmado impossivel — API nao fornece Account Statement format)

## 6. Technical References

| Documento | Conteudo |
|-----------|----------|
| `rfcs/RFC-002-unified-event-ledger-v2.md` | Spec completa do ledger unificado |
| `rfcs/plano-execucao/README.md` | Plano de execucao com dependencias |
| `rfcs/plano-execucao/01-upload-extrato.md` | Spec upload extrato |
| `rfcs/plano-execucao/02-correcoes-pre-implementacao.md` | 5 correcoes da auditoria |
| `rfcs/plano-execucao/03-fase1-cash-events.md` | Spec corrigida Fase 1 |
| `rfcs/plano-execucao/04-fase2-dual-write.md` | Spec Fase 2 |
| `rfcs/plano-execucao/05-fase3-migrar-leituras.md` | Spec Fase 3 |
| `rfcs/plano-execucao/06-fase4-deprecar-mp-expenses.md` | Spec Fase 4 |

## 7. Success Metrics

- 690 cash events para 141Air jan/2026, sum == R$ -3.385,83
- Zero gaps na reconciliacao dia a dia
- mp_expenses eliminada ao final da Etapa 5
- 152+ testes pytest passando em todas as etapas
- DRE por competencia inalterado em todas as etapas

## 8. Pilot Data (141Air Janeiro 2026)

```
INITIAL_BALANCE:  R$ 4.476,23
CREDITS:          R$ 207.185,69
DEBITS:           R$ -210.571,52
FINAL_BALANCE:    R$ 1.090,40
NET MOVEMENT:     R$ -3.385,83
TOTAL LINES:      690
```

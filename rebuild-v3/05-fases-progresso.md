# 05 — As 7 fases e o progresso

Cada fase: corrige código real → re-roda harness → resíduo cai. O harness É a regressão.
Branch: `fix/conciliador-reconciliation`.

## Fase 0 — O Juiz ✅ FEITO
Reconciliação de valor diária (`testes/judge_caixa_jan2026.py` + harness). A fundação que
nunca existiu. Resultado: âncora ✓ 4/4 (10/10 com mar-mai), vendas 99,9%.

## Fase 1 — Re-ancorar valor no extrato 🟡 PARCIAL
- ✅ **net_diff<0 (taxa oculta) → despesa.** `processor.py` `_process_approved` bloco E.
  Antes só logava warning → CA com net maior que o liberado. Verificado: approved −134→−89.
- 🔴 **falta:** conserto do bug de formato do ingester (`_parse_account_statement` aceitar os
  3 layouts via conversor); SKIP de PIX/saída passar a ingerir com valor do extrato.

## Fase 2 — Unificar fonte + cobertura por valor 🟡 PARCIAL
- ✅ **coverage chave composta.** `_lookup_expense_ids` escaneia mp_expenses e casa pelo ID
  base (antes do `:`), corrige crash `int("123:df")` + miss silencioso. (Não verificável
  offline — precisa estado DB.)
- 🔴 **falta:** ingester e coverage_checker lerem a MESMA fonte; cobertura por VALOR não contagem.

## Fase 3 — Baixa extrato-dirigida + 3 datas 🟡 PARCIAL
- ✅ **data do estorno BRT** (não `datetime.now()`) em `_process_refunded` e
  `_process_partial_refund`. Verificado: sem regressão.
- 🔴 **falta (redesign maior):** baixa dirigida pelo extrato (data+valor reais); liberação
  parcial → N baixas; cancela-antes-liberar = não-evento; harness stateful cross-month exato.

## Fase 4 — Validação de fee + refund parcial 🟡 PARCIAL
- ✅ **guard de base do frete:** só ajusta quando `processor_shipping>0` (evita inflar despesa
  quando comprador paga o frete). `release_report_validator.py`.
- ✅ **estorno de devolução PARCIAL:** o router roteava `approved` → `_process_approved` e
  `_process_partial_refund` NUNCA era chamado → a parte devolvida não era estornada → líquido no
  CA maior que o extrato. Agora `approved` + `partially_*refunded` + refunds → estorna o parcial.
  Verificado: erro de valor real net-air −R$2.091 → +R$274; 141air +160 → +324 (ambos <0,1%).
- 🔴 **falta:** ajuste de fee bidirecional (release<processor → crédito); reset de `fee_adjusted`;
  estorno parcial preciso (usa refund.amount bruto; ML devolve comissão proporcional → fino só
  com baixa extrato-dirigida). Bidirecional precisa fixture do release report (não dá offline).

## Fase 5 — As duas pontes 🔴 NÃO INICIADA
Ponte caixa↔DRE (recebíveis a liberar) e ponte DRE↔painel ML (drivers nomeados, datada).
Resolve a dor "explicar a divergência com o painel ML". Precisa das decisões de negócio.

## Fase 6 — DRE D+1 em produção 🔴 NÃO INICIADA
Virar `testes/simulate_dre*.py` em relatório de produção (competência: receita por
date_approved, devolução por data do estorno, fees, despesas non-venda).

## Fase 7 — Cauda de classificação non-venda ✅ FEITO
`extrato_ingester` rules, verificado contra extratos reais (`test_rules` ALL PASS, juiz 0 bugs):
- reembolso de boleto/conta: era SKIP indevido (R$2.168 perdido) → income.
- pix recebido: era OTHER → income.
- compra Mercado Livre (PT): era OTHER → skip.
- "dinheiro recebido cancelado": sinal trocado (income) → expense.

## Commits (branch fix/conciliador-reconciliation)
```
9e310d3 feat(harness): FakeDB stateful p/ idempotência cross-month
af227e7 fix(conciliador): Fase 4 guard base do frete
6e2f313 fix(conciliador): Fase 2 (coverage chave composta) + Fase 3 (data estorno BRT)
4dc150b feat(conciliador): Fase 1 taxa oculta + recon date-aware no harness
92849fe feat(conciliador): harness real-code dry-run + judge + Fase 7 fixes
```
(+ commits de extratos mar-mai e [E] full caixa após este doc.)

## Resumo: o que falta pra "completamente funcional"
1. Fase 1 (ingester formato), Fase 2 (fonte única), Fase 3-full (baixa extrato-dirigida),
   Fase 4-full (fee bidirecional), Fase 5 (pontes), Fase 6 (DRE produção).
2. Harness stateful cross-month exato (precisa dado event-time).
3. Decisões de negócio (ver 08).
4. Cutover ao vivo (deploy + escrita habilitada = ambiente do usuário).

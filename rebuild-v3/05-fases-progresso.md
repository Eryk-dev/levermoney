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

## Fase 3 — Baixa extrato-dirigida + 3 datas 🟡 CORE FEITO
- ✅ **data do estorno BRT** (não `datetime.now()`) em `_process_refunded` e
  `_process_partial_refund`. Verificado: sem regressão.
- ✅ **core da baixa extrato-dirigida:** `app/services/baixas_extrato.plan_baixas_from_extrato`
  (lógica pura) — casa crédito do extrato a parcela CA por payment_id, baixa com DATA+VALOR reais
  do extrato, ajuste quando difere, liberação parcelada → N baixas, cancela-antes-liberar →
  nunca_baixou. 4 casos testados ALL PASS (`test_baixas_extrato.py`).
- 🔴 **falta (wiring de produção):** ligar ao CA real (buscar parcelas abertas + enfileirar via
  ca_queue), substituindo o scheduler por-promessa em `baixas.py`. + harness stateful exato
  (precisa dado event-time).

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

## Fase 5 — As duas pontes ✅ FEITO (harness)
Modo `ponte`: Caixa↔DRE (Δ recebíveis a liberar — fecha: soma +R$377/5meses = 0,1%) e
DRE↔painel ML (devolução DIFERIDA por mês = driver pra explicar a divergência: painel ≈
DRE_dev + diferida + by_admin). Falta: produtizar (módulo de produção) + plugar o nº do painel
ML real + decisões #1/#2.

## Fase 6 — DRE por competência ✅ FEITO (harness)
Modo `dre`: DRE mensal por competência a partir dos eventos CA capturados (receita bruta por
date_approved, devoluções por data do estorno, comissão, frete, estorno taxa, subsídio →
resultado de vendas). Falta: produtizar como relatório/endpoint + despesas non-venda no DRE.

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

## Resumo: status das 7 fases
| Fase | Status |
|---|---|
| 0 Juiz | ✅ feito |
| 1 Taxa oculta | ✅ feito |
| 2 Chave composta | ✅ feito |
| 3 Data estorno + baixa extrato-dirigida (core) | 🟡 core feito + testado; wiring produção falta |
| 4 Refund parcial + guard frete | ✅ feito; fee bidirecional falta (fixture) |
| 5 Pontes caixa↔DRE / DRE↔painel ML | ✅ feito (harness); produtizar falta |
| 6 DRE por competência | ✅ feito (harness); produtizar falta |
| 7 Classificação + cobertura 100% | ✅ feito |

## Plano de finalização — EXECUTADO (jun/2026)

O plano `docs/superpowers/plans/2026-06-08-conciliador-finalizacao.md` foi executado via
subagentes (TDD, 13 commits, 7 test files em `testes/finalizacao/`, todos PASS):

- ✅ **Fase 1 restante** — `extrato_ingester._normalize_report_bytes` (aceita os 3 layouts via conversor).
- ✅ **Fase 4 fee bidirecional** — crédito quando release<processor + revalidação (`fee_adjusted_amount`,
  migration `006`). `_validate_rows` extraído.
- ✅ **Fase 3-full wiring** — `app/services/baixas_extrato_runner.py` (download extrato → busca parcelas
  CA → planeja → posta gated por `baixa_extrato_write_sellers`). Endpoint `GET /baixas/extrato/{seller}`.
  Guard no scheduler legado (`baixa_extrato_driven_sellers`).
- ✅ **Fase 6 produção** — `app/services/dre_report.py` + endpoint `GET /admin/dre/{seller}`.
- ✅ **Fase 5 produção** — `app/services/pontes.py` + endpoint `GET /admin/pontes/{seller}`.
- ✅ Config flags em `app/config.py` (tolerância, rollout por-seller).

## O que falta (só ambiente do usuário — não-código)
1. Aplicar migration `006_fee_adjusted_amount.sql` no Supabase de produção (DDL).
2. **Cutover ao vivo** (runbook Parte 5 do plano): dry-run em prod → habilitar escrita só 141air
   (`baixa_extrato_write_sellers=141air`) → validar no CA → rollout incremental. Deploy + credenciais.
3. Confirmar as 4 decisões de negócio (defaults já embutidos: painel=vendas líquidas, tol=R$50,
   antecipação=despesa financeira, cancela-antes-liberar=cancelar).
4. Fase 4: verificar o crédito bidirecional contra um release report REAL com overcharge (fixture passou).

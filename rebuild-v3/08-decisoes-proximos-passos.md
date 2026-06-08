# 08 — Decisões pendentes + próximos passos

## Decisões de negócio (do usuário — travam Fase 4/5)

1. **Qual métrica do painel ML é a régua?** (GMV / vendas brutas / vendas líquidas /
   faturamento / valor a receber). Define a ponte DRE↔painel ML. Comparar a métrica errada
   *cria* divergência sozinho.
2. **Tolerância de resíduo por seller?** (centavos? R$? %?) Define quando o portão "fecha".
3. **Antecipação** (ML libera adiantado com desconto): quem absorve a taxa de antecipação?
   (despesa financeira do seller?)
4. **Cancela-antes-de-liberar:** confirmar tratar como NÃO-EVENTO (cancelar as contas, não
   estornar bruto). Hoje o processor pode inflar receita+devolução por um fato que nunca tocou caixa.

## FEITO nesta rodada (todas as 7 fases têm artefato verificado em dry-run)
- ✅ Juiz + harness real-code (modos: timeline, dre, ponte).
- ✅ Baseline jan-mai nos 2 sellers; cobertura 100%; erro de valor real ~0,1%.
- ✅ Fase 3-full **core** (`baixas_extrato.plan_baixas_from_extrato` + 4 testes).
- ✅ Harness `timeline` (processa cada payment 1x na união dedupada — evita double-count).
- ✅ Fase 6 DRE por competência (modo `dre`).
- ✅ Fase 5 pontes (modo `ponte`).

## Próximos passos (ordem de dependência)

### Autônomos (não precisam do usuário)
- **A. Fase 1 (ingester formato):** `_parse_account_statement` aceitar os 3 layouts (chamar o
  conversor de `legacy/daily_export`). → destrava o ingester que pode estar lendo 0 linhas.
- **B. Produtizar Fase 3-full:** ligar `baixas_extrato` ao CA real (buscar parcelas abertas +
  enfileirar baixa via ca_queue), substituindo o scheduler por-promessa em `baixas.py`.
- **C. Produtizar Fase 6/5:** DRE e pontes como serviço/endpoint (hoje só no harness).
- **D. Harness stateful cross-month exato:** precisa de dado event-time (status histórico) OU
  janela maior (incluir dezembro) p/ eliminar boundary.

### Precisam do usuário
- **E. Fase 4 fee bidirecional** + reset `fee_adjusted`. Precisa fixture do release report.
- **F. Plugar o nº do painel ML** real na ponte DRE↔ML (decisão #1: qual métrica).
- **G. Cutover ao vivo:** deploy + escrita habilitada no CA = ambiente/credenciais do usuário.

## Critério de "completamente funcional"

7 fases no código real + harness fechando jan-mai (resíduo < tolerância por seller/mês,
explicado) + as 2 pontes fechando + doc do cutover ao vivo.

## Riscos / limites conhecidos
- Caches snapshot → cross-month exato limitado offline (precisa event-time).
- Fase 2/4 não verificáveis 100% offline (precisam estado DB / fixture release report).
- Só 141air tem config CA em prod (resto pending_ca) — em prod precisam ser configurados.
- mar/abr/mai só existem pra 141air + net-air (foco do rebuild).

## Mapa de arquivos (código desta rodada)
```
testes/judge_caixa_jan2026.py            # Juiz Fase 0 (importa regras REAIS do ingester)
testes/harness/dryrun.py                 # core: FakeDB stateful, captura, patches
testes/harness/run.py                    # CLI: modos <meses> | timeline | dre | ponte
testes/harness/test_rules.py             # teste regras Fase 7 (ALL PASS)
testes/harness/test_baixas_extrato.py    # teste baixa extrato-dirigida Fase 3-full (ALL PASS)
testes/harness/fetch_all.py              # fetch payments via API read-only (month-aware)
app/services/processor.py                # Fase 1 (taxa oculta), 3 (data estorno), 4 (refund parcial)
app/services/extrato_ingester.py         # Fase 7 (regras: reembolso/pix/cancel/Renda/Mercado Crédito)
app/services/extrato_coverage_checker.py # Fase 2 (chave composta)
app/services/release_report_validator.py # Fase 4 (guard frete)
app/services/baixas_extrato.py           # Fase 3-full core (baixa extrato-dirigida, lógica pura)
docs/superpowers/specs/2026-06-08-conciliador-harness-fases-design.md  # spec
```

## Modos do harness (como rodar)
```
python3 -m testes.harness.run <slug> <mes[,mes]>   # reconcilia mês(es): [A]âncora [C]vendas [D]caixa [E]full
python3 -m testes.harness.run <slug> timeline      # processa cada payment 1x + resíduo de valor + caixa/mês
python3 -m testes.harness.run <slug> dre           # DRE por competência mensal
python3 -m testes.harness.run <slug> ponte         # pontes caixa↔DRE e DRE↔painel ML
python3 -m testes.harness.test_rules               # regras de classificação
python3 -m testes.harness.test_baixas_extrato      # baixa extrato-dirigida
python3 testes/judge_caixa_jan2026.py              # âncora + buckets (jan, 4 sellers)
```
slug = 141air | net-air. Meses com dado: jan,fev,mar,abr,mai (141air + net-air).

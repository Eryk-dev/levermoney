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

## Próximos passos (ordem de dependência)

### Autônomos (não precisam do usuário)
- **A. Refinar [E] full caixa** no harness: dedup estorno×refund-debit (replicar a regra do
  ingester real), comparar vendas pelo lifecycle correto. → métrica de "bate/não bate" confiável.
- **B. Rodar jan-mai nos 2 sellers** com os caches fetchados → baseline completo 5 meses.
- **C. Fase 1 (ingester formato):** fazer `_parse_account_statement` aceitar os 3 layouts
  (chamar o conversor de `legacy/daily_export`). → destrava o ingester que pode estar lendo 0 linhas.
- **D. Fase 3-full (baixa extrato-dirigida):** redesenhar a baixa pra usar data+valor reais do
  extrato. → fecha o caixa diário de verdade + as 3 datas certas no CA.
- **E. Harness stateful cross-month exato:** precisa de dado event-time (status histórico) OU
  processar a união de payments uma vez por timeline. Limite atual: caches são snapshot.
- **F. Fase 6 (DRE D+1):** virar `simulate_dre*.py` em relatório de produção.

### Precisam do usuário
- **G. Fase 4-full:** fee bidirecional + reset fee_adjusted + estorno proporcional. Precisa de
  decisão #2 (tolerância) + fixture do release report pra verificar.
- **H. Fase 5 (pontes):** precisa das decisões #1 e #2.
- **I. Cutover ao vivo:** deploy + escrita habilitada no CA = ambiente/credenciais do usuário.

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
testes/judge_caixa_jan2026.py          # Juiz Fase 0
testes/harness/dryrun.py               # core: FakeDB, captura, patches
testes/harness/run.py                  # CLI: roda + reconcilia ([A][C][D][E])
testes/harness/test_rules.py           # teste regras Fase 7
testes/harness/fetch_all.py            # fetch payments via API (read-only)
app/services/processor.py              # Fase 1 (taxa oculta), Fase 3 (data estorno)
app/services/extrato_ingester.py       # Fase 7 (regras de classificação)
app/services/extrato_coverage_checker.py # Fase 2 (chave composta)
app/services/release_report_validator.py # Fase 4 (guard frete)
docs/superpowers/specs/2026-06-08-conciliador-harness-fases-design.md  # spec
```

# Conciliador — Harness de teste real-code + correção das 7 fases

Data: 2026-06-08
Status: aprovado (via `/goal pode rodar todas as fases` + aprovações incrementais)

## Objetivo

Tornar o conciliador ML/MP → Conta Azul **completamente funcional**: caixa fecha 100% ao
centavo contra o extrato, vendas/comissão/frete/estorno lançados corretamente, 3 datas
(competência/vencimento/baixa) certas, DRE por competência em D+1, e toda divergência
(caixa↔DRE, DRE↔painel ML) explicada. Provado por um harness que roda o CÓDIGO REAL do app
contra dados reais (jan–mai/2026), em **dry-run** (zero escrita na Conta Azul).

O dashboard de vendas/metas está fora de escopo (já funciona).

## Princípios de arquitetura

1. **Dois livros separados.** Caixa (ancorado no extrato, regime de caixa, fecha ao centavo/dia)
   e DRE (competência, fecha D+1). Ligados pela ponte `Caixa = DRE ± recebíveis a liberar`.
2. **Uma engine só.** Re-ancorar o fluxo novo no extrato; aposentar o legacy engine (evita
   double-count e indecisão arquitetural).
3. **Baixa dirigida pelo extrato.** Baixa = data+valor REAIS da linha de crédito do extrato,
   não a promessa (`money_release_date`). Resolve liberação parcial e cancela-antes-de-liberar.
4. **Portões anti-acúmulo.** Dia não fecha se `Σ lançado != Σ extrato`. Resíduo é explícito,
   nunca absorvido.

## Harness (real-code dry-run) — `testes/harness/`

- Runner por (seller × mês). Chama as funções REAIS: `processor.process_payment_webhook`,
  `_process_approved/_process_refunded/_process_partial_refund`,
  `expense_classifier.classify_non_order_payment`, regras de `extrato_ingester`,
  `release_report_validator`.
- **Captura, não escreve:** monkeypatch de `ca_queue.enqueue_*` → ledger em memória.
  `FakeDB` captura `_upsert_payment` e retorna vazio nos selects. `ml_api.get_order` → None.
- **Segurança (triplo cinto):** ca_queue/ca_api write patchados pra falhar hard; Supabase só
  `.select()`; nenhum CaWorker. Garante zero escrita na Conta Azul.
- Reconcilia o ledger capturado vs extrato: âncora, recon de vendas, caixa diário, cobertura,
  fila de exceção. Saída: relatório por mês + pass/fail vs tolerância.

## Plano de dados

| Mês | Extrato UI CSV | Payments | Status |
|---|---|---|---|
| jan, fev | presente (4 sellers) | 141air cache + resto via ML API read-only | roda já |
| mar, abr, mai | **falta — pedir ao usuário** | via ML API read-only | bloqueado no extrato |

Extrato = download manual do MP (formato `RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;
TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE`). Sellers com config CA: só `141air` (resto `pending_ca`).

## As 7 fases (cada uma: corrige código real → re-roda harness → resíduo cai)

- **Fase 0 — Juiz** (FEITO): reconciliação de valor diária. `testes/judge_caixa_jan2026.py`.
  Resultado jan: âncora ✓ 4/4; vendas 99,97% (R$33/R$96.881); risco no bucket SKIP (~R$57k
  PIX-saída fora do cache); cauda manual ~13% (categoria, não buraco); bug reembolso R$2k + sinais.
- **Fase 1 — Re-ancorar valor no extrato.** Conserta bug de formato do ingester; SKIP de
  PIX/saída passa a ingerir com valor do extrato; `net_diff<0` vira despesa.
  → verifica: ingester processa N>0 linhas; soma ingerida == movimento do extrato.
- **Fase 2 — Unificar fonte + cobertura por valor.** ingester e coverage_checker mesma fonte;
  remove `int(payment_id)` que quebra chave composta.
  → verifica: cobertura por valor, nenhuma linha fora do denominador.
- **Fase 3 — Baixa extrato-dirigida + 3 datas.** baixa = data+valor do extrato; estorno usa
  data do estorno BRT (não `now()`); liberação parcial → N baixas; cancela-antes-liberar = não-evento.
  → verifica: fluxo de caixa do CA == extrato; estorno no mês certo.
- **Fase 4 — Validação de fee bidirecional.** ajusta nos dois sentidos; corrige base do frete
  (líquido vs bruto); `fee_adjusted` reabre com report novo.
  → verifica: fee no CA converge pro release report final.
- **Fase 5 — Duas pontes.** Caixa↔DRE (recebíveis a liberar) e DRE↔painel ML (drivers nomeados, datada).
  → verifica: ambas fecham, resíduo < tolerância.
- **Fase 6 — DRE D+1 em produção.** vira `simulate_dre*.py` em relatório de produção.
  → verifica: DRE assinável em D+1.
- **Fase 7 — Cauda de classificação non-venda.** pix recebido, sinal/ordenação, double-count
  de disputa, typo de locale.
  → verifica: % valor em pending_review < meta.

## Definição de "completamente funcional"

7 fases no código real + harness fechando jan–mai (resíduo < tolerância por seller/mês,
explicado) + doc do cutover ao vivo (precisa das credenciais/deploy do usuário).

## Decisões de negócio (do usuário, não do dev)

1. Qual métrica do painel ML é a régua. 2. Tolerância de resíduo por seller. 3. Antecipação:
quem absorve a taxa. 4. Cancela-antes-liberar: cancelar mesmo.

## Fora de escopo / precisa do usuário

- Extratos mar/abr/mai. Cutover ao vivo (deploy + credenciais). Escrita real na Conta Azul.

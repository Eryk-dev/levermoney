# 09 — Convergência (branch `convergencia-v3`)

> Rodada de jun/2026 (dia 9). Descoberta: existiam DOIS esforços paralelos que nunca se
> conheceram. Este doc registra a convergência — o que foi portado, os números, e o que
> falta pro cutover.

## A descoberta

- **`origin/main` ("o outro chat") = O QUE RODA EM PRODUÇÃO.** Rearquitetura "Unified
  Event Ledger" v3.0.0: `payment_events` append-only é a fonte-da-verdade, tabela
  `payments` morta (write parou 13/mar), `mp_expenses` deprecada, refund unificado
  (US-010, job `estorno_frete`), upload de extrato no admin, ~520 testes, juiz spec-002.
  Provado em prod: payment_events 55k rows com write no dia, ca_jobs fluxo completo ativo.
- **Local (`rebuild-v3-local`)** = a rodada destes docs (harness, gabarito, baixa
  extrato-dirigida, DRE/pontes, fee bidirecional) sobre a arquitetura ANTIGA.
- **O "100%" do spec-002 NÃO é prova de valor:** mede presença de contrapartida com
  passes de alinhamento que SOBRESCREVEM o valor do sistema com o do extrato (ERR-0019
  absorveu o R$6,46 do pid 148949991586 que o baixa_100 local expõe como ajuste);
  nunca compara com o Conta Azul (fora de escopo declarado); offline-only.
- **Merge textual: inviável** (4 arquivos-núcleo, migração 006 colide). Caminho:
  **portar peça a peça sobre origin/main**, gabarito como régua em cada passo.

## Baseline do processor REMOTO (gabarito, jan-mai, nunca medido antes)

| ERRO REAL | local main | remoto (base) | convergencia-v3 (final) |
|---|--:|--:|--:|
| 141air | 3.989,62 (30) | 3.221,73 (25) | **3.221,73 (25)** |
| net-air | 65.858,76 (495) | 60.120,18 (508) | **57.286,81 (421)** |

- O refund unificado do remoto (US-010, sem caps, estorno_frete separado) **VENCEU o
  fix1 local pela régua** → mantido. Decisão empírica, não opinião.
- Taxa oculta portada (bloco E WAL): net-air −R$2.833.
- 3 "bugs Fase 7" no remoto eram FALSO ALARME: `_SIGN_DRIVEN_EXPENSE_TYPES` (sinal do
  CSV manda em reversões, ERR-0025) + dedup de compra_ml vs classifier resolvem MELHOR
  que as regras locais. test_rules atualizado pro pipeline real.

## O que foi portado/construído (commits da branch)

| Commit | O quê |
|---|---|
| `7bfc56a` | Fundação: harness/judge/gabarito/baixas_extrato/docs sobre origin/main |
| `f1745f2` | Harness adaptado ao event_ledger (FakeDB payment_events stateful) + baseline |
| `29de305` | Taxa oculta → despesa (WAL) + fee bidirecional + guard frete; adjustment_* "any" |
| `a1c50d1` | ca_api: CRUD de baixas (listar/buscar/atualizar/deletar, versao lock) |
| `81f6191` | **TRIO extrato-dirigido** + runner + flags + endpoint /baixas/extrato |
| `42c5fc0` | **Juiz P1** (caixa_judge): âncora + saldo absoluto + caixa/dia; /admin/caixa-judge |
| `06e4357` | **Retrofit histórico**: plan/apply PATCH/DELETE; CaWorker patch/delete; /admin/baixas-retrofit |
| `75b265c` | DRE por competência + pontes do ledger; /admin/dre, /admin/pontes |
| (final) | Suite convergência + test_rules sign-driven + este doc |

## O TRIO (o coração do fechamento ao centavo)

`plan_baixas_trio`: crédito de liberação liquida o GRUPO da venda (receita bruto,
comissão, frete, taxa oculta, subsídio) proporcionalmente por tranche, na data do
crédito. **Invariante: Σ(baixas assinadas do dia) + ajustes == Σ(extrato do dia) EXATO.**

Prova com dados reais (processor remoto, extratos jan-mai):
```
141air:  1.061 liberações → 2.951 baixas             DIFF R$0,00 · 0 dias divergentes
net-air: 16.345 liberações → 42.988 baixas, 10 ajustes DIFF R$0,00 · 0 dias divergentes
```

## Semântica importante do ledger (não esquecer)

- Eventos de refund têm `competencia_date` = mês da VENDA e `event_date` = ESTORNO real.
  → DRE contábil bucketa devolução (E estorno_taxa/frete) por `event_date`;
  → ponte da devolução diferida sai da MESMA row, sem join (saiu_do_mes/entrou_no_mes).
- `adjustment_fee/shipping` agora são bidirecionais ("any"): negativo = ML cobrou mais
  (despesa de ajuste); positivo = ML cobrou menos (crédito contas-a-receber).
- Baixa job idempotency default `{seller}:{parcela}:baixa` COLIDE com liberação
  parcelada → runner usa `{seller}:{parcela}:baixa:{data}:{centavos}`.
- CaWorker agora suporta PATCH/DELETE (antes, non-POST caía em GET).

## Bateria (estado final da branch)

- pytest unit+integration+finalizacao: **532 passed**, 8 failed PRÉ-EXISTENTES da base
  (test_expense_classifier_sign×4 + test_extrato_classification×4 — dívida do remoto,
  não desta rodada; ver fails_base).
- test_baixas_trio 7/7 · test_baixas_extrato 4/4 · test_rules 7/7 · gabarito idêntico.

## Cutover (o que falta — ambiente/decisão do usuário)

1. Deploy da branch (merge na main após validação).
2. `baixa_extrato_driven_sellers=141air` (desliga promessa) +
   `baixa_extrato_write_sellers=141air` (liga escrita) no .env.
3. Retrofit piloto: `GET /admin/baixas-retrofit/141air?data_de=...&limit=1` → validar
   1 PATCH no CA real (caveat: baixa com `id_reconciliacao` = manual) → lote.
4. Juiz: `GET /admin/caixa-judge/141air` diário; meta = saldo_diff → 0 e dias 100% fecha.
5. Lane non-venda: decidir auto-post no CA vs import manual disciplinado (P2).
6. As 4 decisões de negócio (doc 08) + configurar CA dos 9 sellers restantes.

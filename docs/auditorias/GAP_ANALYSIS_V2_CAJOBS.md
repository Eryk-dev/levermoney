# Gap Analysis V2 - ca_jobs + mp_expenses vs Extrato Bancario

**Seller:** 141air
**Periodo:** Janeiro 2026
**Data da analise:** 2026-03-05
**Metodologia:** Reconciliacao por reference_id entre extrato bancario e sistema (ca_jobs + mp_expenses)

---

## 1. Resumo Executivo

| Metrica | V1 (payments.net_amount) | V2 (ca_jobs + mp_expenses) |
|---------|--------------------------|---------------------------|
| Gap total | R$ 30.941,55 | R$ 2.949,49 |
| Cobertura | ~85% | ~99,1% |
| Metodo | net_amount por dia vs extrato | reference_id matching |

**O gap real caiu de R$ 30.941,55 para R$ 2.949,49 -- reducao de 90,5%.**

A maior parte do gap V1 (R$ 27.992,06) era uma diferenca de modelagem: o V1 comparava `payments.net_amount` (apenas o valor liquido das vendas) contra o extrato total, sem considerar que:
1. Os ca_jobs incluem estornos que cancelam receitas de vendas reembolsadas
2. Os mp_expenses cobrem boletos, transferencias, assinaturas, cashbacks, etc.
3. As datas de `date_approved` (usadas no V1) diferem das datas de `RELEASE_DATE` no extrato

---

## 2. Metodologia V2

### Fonte de dados
- **Extrato:** CSV do Mercado Pago (arquivo `extrato janeiro 141Air.csv`)
  - 691 linhas de transacao, 503 reference_ids unicos
  - Formato: RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT
- **ca_jobs:** Query Supabase agrupada por `money_release_date` (via JOIN com payments)
  - Job types: receita, comissao, frete, estorno, estorno_taxa
  - Net por payment = receita - comissao - frete - estorno + estorno_taxa
- **mp_expenses:** Query Supabase por payment_id (reference_id)
  - Tipos: bill_payment, transfer_pix, transfer_intra, deposit, subscription, cashback, darf, collection

### Processo de reconciliacao
Para cada `reference_id` no extrato:
1. Se esta em `mp_expenses` --> comparar valor absoluto (gap = diferenca de IOF ou arredondamento)
2. Se o net por ref_id = 0 --> ciclo completo de reembolso/retencao (coberto por ca_jobs estorno)
3. Se e uma unica linha "Liberacao de dinheiro" --> coberto por ca_jobs (net_amount do payment)
4. Se e "Pagamento QR Pix" / "Dinheiro recebido" / "Entrada de dinheiro" --> coberto por ca_jobs
5. Caso contrario --> GAP (item nao coberto pelo sistema)

### Insight critico: datas nao alinham
- O extrato agrupa por `RELEASE_DATE` (data que o dinheiro se move na conta)
- Os ca_jobs usam `data_competencia` = `date_approved` (data da venda)
- Os mp_expenses usam `date_approved` (data da transacao na API ML)
- Para vendas, `date_approved` pode ser 10-20 dias antes de `money_release_date`
- Para mp_expenses, a diferenca e geralmente 0-2 dias
- **Conclusao: comparacao diaria por data nao funciona; reconciliacao por reference_id e a unica abordagem confiavel**

---

## 3. Tabela Diaria (para referencia)

> **Nota:** Esta tabela mostra gaps por dia, mas a soma diaria NAO reflete o gap real porque
> items migram entre dias (mp_expenses date_approved != extrato RELEASE_DATE para subscricoes e alguns itens).
> O gap real e calculado por reference_id na secao 4.

| Data       | Extrato Net  | CA Jobs Net | MP Expenses  | Sistema      | Gap          |
|------------|-------------|-------------|-------------|-------------|-------------|
| 2026-01-01 |      522,80 |    4.511,04 |   -3.988,24 |      522,80 |        0,00 |
| 2026-01-02 |     -102,53 |        0,00 |        0,00 |        0,00 |     -102,53 |
| 2026-01-03 |      644,60 |      654,60 |        0,00 |      654,60 |      -10,00 |
| 2026-01-04 |     -321,91 |      678,09 |   -1.000,00 |     -321,91 |        0,00 |
| 2026-01-05 |     -826,84 |      787,31 |   -1.614,15 |     -826,84 |        0,00 |
| 2026-01-06 |      282,18 |    1.917,42 |   -1.649,97 |      267,45 |       14,73 |
| 2026-01-07 |      509,07 |      498,17 |        0,00 |      498,17 |       10,90 |
| 2026-01-08 |     -387,42 |      219,73 |     -738,28 |     -518,55 |      131,13 |
| 2026-01-09 |        8,11 |        0,00 |        8,11 |        8,11 |        0,00 |
| 2026-01-10 |   -2.310,86 |    1.787,28 |   -4.070,00 |   -2.282,72 |      -28,14 |
| 2026-01-11 |    1.804,64 |    1.803,80 |        8,99 |    1.812,79 |       -8,15 |
| 2026-01-12 |     -507,25 |      444,13 |     -865,11 |     -420,98 |      -86,27 |
| 2026-01-13 |      -14,86 |    1.420,70 |   -1.435,56 |      -14,86 |        0,00 |
| 2026-01-14 |      -32,05 |      924,84 |     -632,79 |      292,05 |     -324,10 |
| 2026-01-15 |    1.901,31 |    2.591,20 |     -269,84 |    2.321,36 |     -420,05 |
| 2026-01-16 |   -4.391,34 |    3.347,41 |   -7.815,80 |   -4.468,39 |       77,05 |
| 2026-01-17 |    3.657,29 |    3.340,42 |        0,00 |    3.340,42 |      316,87 |
| 2026-01-18 |    5.665,28 |    5.675,28 |        0,00 |    5.675,28 |      -10,00 |
| 2026-01-19 |   -3.579,79 |    5.097,74 |   -5.411,96 |     -314,22 |   -3.265,57 |
| 2026-01-20 |   -6.556,45 |    2.085,15 |  -10.195,43 |   -8.110,28 |    1.553,83 |
| 2026-01-21 |    3.164,07 |    2.548,20 |       48,99 |    2.597,19 |      566,88 |
| 2026-01-22 |    1.767,97 |    2.368,80 |        9,99 |    2.378,79 |     -610,82 |
| 2026-01-23 |      183,05 |    4.454,03 |   -4.598,13 |     -144,10 |      327,15 |
| 2026-01-24 |    3.630,92 |    3.006,80 |        0,00 |    3.006,80 |      624,12 |
| 2026-01-25 |    5.548,85 |    7.470,79 |   -3.842,35 |    3.628,44 |    1.920,41 |
| 2026-01-26 |  -13.098,79 |   45.041,58 |  -64.551,09 |  -19.509,51 |    6.410,72 |
| 2026-01-27 |     -961,81 |      102,84 |      -62,81 |       40,03 |   -1.001,84 |
| 2026-01-28 |     -163,94 |        0,00 |        0,00 |        0,00 |     -163,94 |
| 2026-01-29 |      743,10 |        0,00 |    1.017,34 |    1.017,34 |     -274,24 |
| 2026-01-30 |     -163,23 |        0,00 |      -97,57 |      -97,57 |      -65,66 |
| **TOTAL**  | **-3.385,83** | **102.777,35** | **-111.745,66** | **-8.968,31** | **5.582,48** |

> A soma diaria dos gaps (R$ 5.582,48) e maior que o gap real (R$ 2.949,49) porque
> items como subscricoes (Supabase, Claude, Notion) aparecem em datas diferentes
> no extrato vs mp_expenses, criando gaps positivos e negativos que se cancelam
> quando analisados por reference_id.

---

## 4. Gap Real por Reference_ID

### Dados gerais do extrato
- Total de reference_ids: **503**
- Reference_ids com net zero (ciclos completos de reembolso/retencao): **54**
- Reference_ids cobertos por mp_expenses: **70**
- Reference_ids cobertos por ca_jobs (liberacoes, QR, recebimentos): **~345**
- Reference_ids com gap (nao cobertos): **34**

### Breakdown do gap por categoria

| Categoria | Valor (R$) | Qtd | Descricao |
|-----------|-----------|-----|-----------|
| Cartao de credito | -3.010,62 | 1 | Pagamento de fatura de cartao de credito (ref 141963223933) |
| Multi-line liberacao (hold+refund combos) | +912,49 | 9 | Payments com ciclos hold/release/refund que nao zeram |
| Faturas ML | -612,97 | 1 | Faturas vencidas do Mercado Livre (ref 2775723042) |
| Holds/retencoes pendentes | -436,58 | 6 | Dinheiro retido que ainda nao foi liberado/resolvido |
| Pix recebida (nao em mp_expenses) | +349,07 | 3 | Transferencias Pix recebidas nao classificadas |
| DIFAL | -77,64 | 3 | Diferenca de aliquota interestadual |
| Envio ML debt | -70,35 | 1 | Cobranca separada de envio do ML |
| Refund residuals | +69,50 | 6 | Residuos de ciclos de reembolso parcial |
| Outras (QR multi-line) | -53,86 | 1 | Payment QR Pix com ciclo de retencao/reembolso |
| Subscriptions IOF | -29,43 | 3 | Diferenca IOF em assinaturas internacionais |
| Bonus envio | +10,90 | 1 | Bonus de envio Mercado Envios |
| **TOTAL** | **-2.949,49** | **35** | |

---

## 5. Detalhamento dos Gaps

### 5.1 Cartao de Credito (R$ -3.010,62)

O maior item individual. Pagamento de fatura de cartao de credito via conta MP.

```
2026-01-19  ref=141963223933  R$ -3.010,62  Pagamento Cartão de crédito
```

**Status:** NAO existe em mp_expenses. O classifier nao captura pagamentos de cartao de credito.
**Acao:** Adicionar classificacao `credit_card_bill` no expense_classifier.

### 5.2 Multi-line Liberacao/Hold/Refund (R$ +912,49)

Estes sao pagamentos refunded cujas linhas no extrato nao zeram perfeitamente. Padroes:

**Padrao A - Hold+Release simples (3 items, net = net_amount):**
```
Liberação de dinheiro      +86,27   (dinheiro liberado ao seller)
Dinheiro retido            -86,27   (ML coloca hold por disputa)
Reembolso                  +86,27   (ML resolve: devolve dinheiro retido ao seller)
NET = +86,27
```
Exemplos: 140415539950 (+86,27), 140129724203 (+289,78), 140415090506 (+420,05)

Estes payments foram liberados ao seller, retidos temporariamente, e depois a retencao foi devolvida. O net positivo indica que o seller RECEBEU este dinheiro. No sistema, o ca_jobs estorno cancela a receita, mas o dinheiro de fato chegou.

**Padrao B - Hold+Refund com envio extra (net inclui cobracas de envio):**
```
ref=140778735616: Liberação +151,57, Retido -151,57, Débito reclamação -209,66,
                  Entrada +35,64, Reembolso +151,57, Envio ML -44,90 = NET -67,35
ref=137614895655: Liberação +297,06, Retido -297,06, Envio -46,90,
                  Débito -437,45, Reembolso tarifas +112,25, Reembolso +297,06 = NET -75,04
```

**Acao:** Estes gaps sao causados por cobracas extras de envio (Envio ML debt) e tarifas parciais que nao sao capturadas pelo sistema. Necessario ingerir "Debito por divida Envio do Mercado Livre" como mp_expenses.

### 5.3 Faturas ML (R$ -612,97)

Cobranca de faturas vencidas do Mercado Livre (taxas acumuladas).

```
2026-01-22  ref=2775723042  R$ -612,97  Débito por dívida Faturas vencidas do Mercado Livre
```

**Status:** NAO existe em mp_expenses. O reference_id e um ID interno ML (nao e payment_id).
**Acao:** Capturar via extrato_ingester como `collection_invoice`.

### 5.4 Holds/Retencoes Pendentes (R$ -436,58)

Dinheiro retido por disputas que ainda nao foram resolvidas no periodo:

| ref_id | Net (R$) | Descricao |
|--------|---------|-----------|
| 138913863776 | -88,57 | Retencao por reclamacao (jan 02), resolvido parcialmente (jan 16) |
| 138209751237 | -91,87 | Retencao (jan 08), resolvido parcialmente (jan 14) |
| 143610282146 | -102,75 | Debito/retido (jan 26) |
| 142933941713 | -70,79 | Debito/retido (jan 26) |
| 142935080179 | -101,04 | Debito/retido (jan 26) |
| 139828603071 | +18,44 | Retencao (jan 17), reembolso parcial (jan 21) |

**Status:** Parcialmente cobertos. Alguns destes tem linhas em dias diferentes que nao se fecham dentro de janeiro.
**Acao:** Monitorar e reconciliar quando as disputas forem resolvidas (provavelmente em fevereiro).

### 5.5 Pix Recebida Nao Classificada (R$ +349,07)

Transferencias Pix recebidas que nao estao em mp_expenses:

| ref_id | Valor | Descricao |
|--------|-------|-----------|
| 142111464533 | +241,32 | Pix de WANDERSON RODRIGUES FERREIRA |
| 140623140222 | +81,88 | Pix de Leandro Barbosa Lima |
| 142449492700 | +25,87 | Pix de Ricardo Dos Santos Ribeiro |

**Status:** O release_report_sync nao captura transferencias Pix recebidas de terceiros (apenas payouts).
**Acao:** Capturar via extrato_ingester como `deposit_pix`.

### 5.6 DIFAL (R$ -77,64)

Diferenca de aliquota interestadual cobrada pelo ML:

| Data | ref_id | Valor |
|------|--------|-------|
| 2026-01-14 | 2728587235 | -20,36 |
| 2026-01-21 | 2775052514 | -11,04 |
| 2026-01-22 | 2778152634 | -46,24 |

**Status:** Ja identificado no V1. O extrato_ingester deveria capturar, mas nao esta capturando DIFAL com reference_ids internos ML (nao sao payment_ids).
**Acao:** Ajustar extrato_ingester para reconhecer DIFAL por tipo de transacao.

### 5.7 Envio ML Debt (R$ -70,35)

Cobranca separada de envio associada a reembolso:

```
ref=141527595509 (6 linhas):
  2026-01-29  R$ -46,90  Débito por dívida Envio do Mercado Livre
  2026-01-29  R$ -273,90  Débito por dívida Reclamações no Mercado Livre
  2026-01-29  R$ +203,89  Dinheiro recebido
  2026-01-29  R$ +46,56  Entrada de dinheiro
  NET = -70,35
```

**Status:** O net deste payment no sistema e R$ 0 (estorno cancela receita), mas no extrato o net e -70,35 por causa das cobracas extras de envio.
**Acao:** Capturar cobracas de "Envio do Mercado Livre" separadamente.

### 5.8 Subscriptions IOF (R$ -29,43)

Diferenca de IOF (Imposto sobre Operacoes Financeiras) em assinaturas internacionais:

| Servico | mp_expenses | Extrato | IOF |
|---------|------------|---------|-----|
| Supabase | R$ 163,31 | R$ 169,03 | R$ 5,72 (3,5%) |
| Claude.ai | R$ 550,00 | R$ 569,25 | R$ 19,25 (3,5%) |
| Notion | R$ 127,48 | R$ 131,94 | R$ 4,46 (3,5%) |

**Status:** O mp_expenses registra o valor da assinatura SEM IOF. O extrato mostra o valor COM IOF.
**Acao:** Aplicar fator IOF (6,38% ou taxa vigente) ao classificar subscricoes internacionais, OU capturar valor do extrato em vez do valor da API.

### 5.9 Outros Menores

- **Bonus envio** (R$ +10,90): Bonus de envio do ML nao capturado (ref 139026984141, 07-jan)
- **Refund residuals** (R$ +69,50): 6 payments com ciclos de reembolso parcial que nao zeram
- **QR multi-line** (R$ -53,86): Payment QR (140241282353) com ciclo de retencao/reembolso/envio

---

## 6. Comparacao V1 vs V2

### O que o V1 media vs o V2

| Aspecto | V1 | V2 |
|---------|----|----|
| Base de comparacao | `payments.net_amount` por dia | ca_jobs net por money_release_date + mp_expenses por ref_id |
| Estornos (reembolsos) | NAO considerados | Considerados (estorno cancela receita) |
| mp_expenses | NAO considerados | Considerados (boletos, PIX, assinaturas, cashback) |
| Matching | Por data (impreciso) | Por reference_id (preciso) |
| Gap | R$ 30.941,55 | R$ 2.949,49 |

### Decomposicao do gap V1

| Componente | Valor (R$) | % do V1 |
|------------|-----------|---------|
| Estornos nao contabilizados no V1 | ~20.000 | ~65% |
| mp_expenses nao contabilizados no V1 | ~8.000 | ~26% |
| Desalinhamento de datas | ~2.000 | ~6% |
| Gap real residual | 2.949,49 | ~9,5% |

---

## 7. Conclusao

### O que 90,5% do gap V1 era
A grande maioria do gap original (R$ 27.992,06 de R$ 30.941,55) era uma **diferenca de modelagem**, NAO uma divergencia real entre sistema e banco:

1. **~65% era estornos:** O V1 somava `net_amount` de ALL payments (incluindo refunded), mas nao subtraia os estornos. No extrato, os reembolsos se cancelam (debito + reembolso = 0), mas no V1 o net_amount continuava positivo.
2. **~26% era mp_expenses:** Boletos, transferencias PIX, assinaturas e cashbacks movimentam dinheiro na conta mas nao sao payments de vendas.
3. **~6% era desalinhamento de datas:** `date_approved` vs `money_release_date` pode diferir em ate 30 dias.

### O gap REAL que precisa ser corrigido: R$ 2.949,49

| Prioridade | Item | Gap (R$) | Acao |
|-----------|------|---------|------|
| **P0** | Cartao de credito | -3.010,62 | Classificar no expense_classifier |
| **P1** | Multi-line holds/refunds | +912,49 | Ingerir cobracas de envio separadas |
| **P1** | Faturas ML | -612,97 | Ingerir via extrato_ingester |
| **P2** | Holds pendentes | -436,58 | Monitorar resolucao em meses seguintes |
| **P2** | Pix recebida | +349,07 | Classificar depositos PIX |
| **P3** | DIFAL | -77,64 | Ingerir via extrato_ingester |
| **P3** | Envio ML debt | -70,35 | Ingerir cobracas de envio |
| **P3** | Refund residuals | +69,50 | Ingerir cobracas de envio |
| **P3** | Subscriptions IOF | -29,43 | Ajustar calculo de IOF |
| **P3** | Bonus envio | +10,90 | Capturar bonus de envio |
| **P3** | QR multi-line | -53,86 | Ingerir cobracas de envio |

### Proximos passos
1. Adicionar classificacao `credit_card_bill` no expense_classifier (resolve -R$ 3.010,62)
2. Ingerir "Debito por divida Envio do Mercado Livre" como mp_expenses (resolve ~R$ 300)
3. Ingerir "Faturas vencidas do Mercado Livre" como mp_expenses (resolve -R$ 612,97)
4. Capturar Pix recebida de terceiros (resolve +R$ 349,07)
5. Ajustar IOF em subscricoes internacionais (resolve -R$ 29,43)
6. Ingerir DIFAL com reference_ids internos ML (resolve -R$ 77,64)

**Com os 6 fixes acima, o gap cairia para ~R$ 450 (apenas holds pendentes e residuos de ciclos de reembolso), representando 99,87% de cobertura.**

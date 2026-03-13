# Gap Analysis: Extrato vs Sistema - 141Air, Janeiro 2026

Data: 2026-03-04
Seller: 141air
Periodo: 01/01/2026 a 31/01/2026

---

## Resumo Executivo

| Metrica | Valor (R$) |
|---|---:|
| Extrato bancario - movimentacao liquida | -3.385,83 |
| Sistema (payments + mp_expenses) - movimentacao liquida | 27.555,72 |
| **GAP TOTAL** | **-30.941,55** |

O gap de R$ 30.941,55 esta **100% explicado** por tres fatores:
1. **Fluxo de reclamacoes/devolucooes** (R$ -22.868,32): o sistema registra `net_amount` como receita positiva, mas no extrato o ciclo completo (Debito + Liberacao + Reembolso) resulta em zero
2. **IDs ausentes no sistema** (R$ -68,84): DIFAL, faturas ML, bonus envio, pagamentos QR e liberacoes que nao foram ingeridos
3. **IDs ausentes no extrato** (R$ -7.974,96): pagamentos refundados que existem no sistema mas cujo ciclo completo no extrato nao aparece no periodo (ou com ID diferente)

Explicacao detalhada: o gap **nao indica dinheiro perdido**. Indica uma diferenca de modelagem entre como o extrato ML registra movimentacoes (fluxo bruto com debitos e creditos que se cancelam) vs como o sistema registra (apenas o valor liquido liberado).

---

## Tabela Dia a Dia

| Data | Extrato Net | Payments Net | Expenses Net | Sistema Total | GAP | Obs |
|---:|---:|---:|---:|---:|---:|---|
| 01/01 | 522,80 | 4.511,04 | -3.988,24 | 522,80 | 0,00 | OK |
| 02/01 | -102,53 | 8.718,85 | 0,00 | 8.718,85 | -8.821,38 | Claims (139749344683, 140422465618, 140422485450) |
| 03/01 | 644,60 | 644,60 | 0,00 | 644,60 | 0,00 | OK |
| 04/01 | -321,91 | 1.305,21 | -1.000,00 | 305,21 | -627,12 | Claim 137943496999 |
| 05/01 | -826,84 | 1.057,66 | -1.613,06 | -555,40 | -271,44 | Claims 140087737157, 138151129880 |
| 06/01 | 282,18 | 2.725,71 | -1.130,99 | 1.594,72 | -1.312,54 | Claims + boletos date shift |
| 07/01 | 509,07 | 903,40 | -518,98 | 384,42 | 124,65 | Bonus envio 139026984141 (+10.90) |
| 08/01 | -387,42 | 637,07 | 0,00 | 637,07 | -1.024,49 | Subscriptions (Supabase/Claude) not on this date in system + claims |
| 09/01 | 8,11 | 0,00 | 9,20 | 9,20 | -1,09 | Cashback date shift |
| 10/01 | -2.310,86 | 1.895,07 | -783,31 | 1.111,76 | -3.422,62 | Saque Eryk -4000, claims + DIFAL |
| 11/01 | 1.804,64 | 1.795,65 | -3.991,01 | -2.195,36 | 4.000,00 | Saque Eryk aparece no extrato dia 10, mp_expenses dia 11 |
| 12/01 | -507,25 | 711,64 | -865,11 | -153,47 | -353,78 | Claims + holds |
| 13/01 | -14,86 | 1.750,79 | -1.435,56 | 315,23 | -330,09 | Claim 140453365536 |
| 14/01 | -32,05 | 924,84 | -632,79 | 292,05 | -324,10 | Claims + DIFAL 2728587235 |
| 15/01 | 1.901,31 | 2.842,71 | -270,93 | 2.571,78 | -670,47 | Claims + holds |
| 16/01 | -4.391,34 | 3.849,52 | -7.815,80 | -3.966,28 | -425,06 | Claims |
| 17/01 | 3.657,29 | 4.047,62 | 0,00 | 4.047,62 | -390,33 | Claims |
| 18/01 | 5.665,28 | 5.665,28 | 0,00 | 5.665,28 | 0,00 | OK |
| 19/01 | -3.579,79 | 5.504,25 | -5.411,96 | 92,29 | -3.672,08 | CC payment -3010.62 + claims |
| 20/01 | -6.556,45 | 3.963,59 | -10.195,43 | -6.231,84 | -324,61 | Claims |
| 21/01 | 3.164,07 | 3.968,34 | 48,99 | 4.017,33 | -853,26 | Claims + DIFAL 2775052514 |
| 22/01 | 1.767,97 | 3.176,83 | -602,98 | 2.573,85 | -805,88 | Fatura ML 2775723042 + DIFAL + claims |
| 23/01 | 183,05 | 6.325,56 | -4.466,19 | 1.859,37 | -1.676,32 | Claims |
| 24/01 | 3.630,92 | 4.158,88 | 0,00 | 4.158,88 | -527,96 | ID 140688038213 (refunded, in system not extrato) |
| 25/01 | 5.548,85 | 9.802,93 | -3.969,83 | 5.833,10 | -284,25 | Claims |
| 26/01 | -13.098,79 | 48.379,17 | -64.551,09 | -16.171,92 | 3.073,13 | transfer_intra sign + invisible releases |
| 27/01 | -961,81 | 1.004,24 | -63,90 | 940,34 | -1.902,15 | Claims |
| 28/01 | -163,94 | 8.243,10 | 0,00 | 8.243,10 | -8.407,04 | Payouts 143762867120+143815855230 (refunded, not in extrato) |
| 29/01 | 743,10 | 325,38 | 1.317,34 | 1.642,72 | -899,62 | Claims + holds |
| 30/01 | -163,23 | 1.045,99 | -397,57 | 648,42 | -811,65 | Claims |
| **TOTAL** | **-3.385,83** | **139.884,92** | **-112.329,20** | **27.555,72** | **-30.941,55** | |

---

## IDs Invisiveis (no extrato mas NAO no sistema)

Total: 29 IDs, valor liquido total: R$ -68,84

### Por categoria:

#### DIFAL (Diferenca de Aliquota) - 3 IDs, R$ -77,64
Estes sao cobrados pelo ML e nao sao ingeridos pelo sistema via Payments API.
| ID | Valor | Data |
|---|---:|---|
| 2728587235 | -20,36 | 14/01 |
| 2775052514 | -11,04 | 21/01 |
| 2778152634 | -46,24 | 22/01 |

#### Fatura ML - 1 ID, R$ -612,97
O extrato usa ID `2775723042` para a fatura; o sistema tem o mesmo valor sob ID `142352059685` (tipo `collection`).
| ID | Valor | Data |
|---|---:|---|
| 2775723042 | -612,97 | 22/01 |

#### Pagamento Cartao de Credito - 1 ID, R$ -3.010,62
| ID | Valor | Data |
|---|---:|---|
| 141963223933 | -3.010,62 | 19/01 |

#### Liberacoes de Dinheiro (payments sem registro) - 9 IDs, R$ +2.700,31
Pagamentos cuja `Liberacao de dinheiro` aparece no extrato mas que nao existem na tabela `payments`.
| ID | Valor | Data |
|---|---:|---|
| 141183074293 | 861,77 | 26/01 |
| 141043812466 | 734,76 | 26/01 |
| 141385949804 | 405,92 | 26/01 |
| 141996119325 | 383,81 | 26/01 |
| 141470360279 | 102,75 | 26/01 |
| 142292552528 | 88,74 | 26/01 |
| 141251658525 | 63,68 | 26/01 |
| 141359034751 | 38,92 | 26/01 |
| 141922182246 | 19,96 | 26/01 |

#### QR Pix (payments sem registro) - 5 IDs, R$ +1.195,30
| ID | Valor | Data |
|---|---:|---|
| 142339588114 | 781,00 | 26/01 |
| 142110483725 | 203,89 | 26/01 |
| 142406081170 | 75,94 | 26/01 |
| 143608784484 | 70,79 | 26/01 |
| 141587118535 | 63,68 | 26/01 |

#### Debito/Dinheiro Retido (holds pendentes) - 3 IDs, R$ -274,58
| ID | Valor | Data |
|---|---:|---|
| 143610282146 | -102,75 | 26/01 |
| 142935080179 | -101,04 | 26/01 |
| 142933941713 | -70,79 | 26/01 |

#### Bonus Envio - 1 ID, R$ +10,90
| ID | Valor | Data |
|---|---:|---|
| 139026984141 | 10,90 | 07/01 |

#### Claims que resultam em net ~0 - 5 IDs, R$ +0,46
Ciclos completos de reclamacao que aparecem no extrato mas cujos IDs nao existem no sistema.
| ID | Valor | Data |
|---|---:|---|
| 137820920749 | 209,04 | 08/01 |
| 138209751237 | -91,87 | multi |
| 138913863776 | -88,57 | multi |
| 137617703230 | -28,14 | 10/01 |
| 138151129880 | 0,00 | 05/01 |
| 143104571692 | 0,00 | 29/01 |

---

## IDs no Sistema mas NAO no Extrato

Total: 5 IDs, valor total no sistema: R$ 7.974,96

| ID | Valor | Tabela | Status | Data | Observacao |
|---|---:|---|---|---|---|
| 143762867120 | 4.121,55 | payments | refunded | 28/01 | Pagamento R$ 5000 refundado - release no extrato nao aparece |
| 143815855230 | 4.121,55 | payments | refunded | 28/01 | Pagamento R$ 5000 refundado - release no extrato nao aparece |
| 140688038213 | 313,23 | payments | refunded | 24/01 | Pagamento R$ 355.94 refundado |
| 143909170600 | 31,60 | payments | refunded | 29/01 | Pagamento R$ 45.90 refundado |
| 142352059685 | -612,97 | mp_expenses | collection | 22/01 | Fatura ML - mesmo valor do 2775723042 no extrato, ID diferente |

**Nota**: Todos os 4 payments sao `refunded`. O extrato mostra o ciclo completo (Debito + devolucao) com net = 0, mas pode estar em datas fora de janeiro ou com outros IDs.

---

## Value Mismatches (mesmo ID, valores diferentes)

Total: 67 IDs, diferenca total: R$ -22.897,75

### Padrao Principal: Fluxo de Reclamacoes (64 IDs, R$ -22.868,32)

Para pagamentos que sofreram reclamacao/devolucao, o extrato mostra:
```
Debito por divida Reclamacoes no Mercado Livre  REF_ID  -VALOR_TOTAL
Liberacao de dinheiro                           REF_ID  +NET_AMOUNT
Reembolso Envio cancelado / Reclamacoes         REF_ID  +REFUND
```
Estas 3 linhas netam a **zero** (ou proximo de zero) no extrato.

Porem, no sistema, `payments.net_amount` registra apenas o valor da "Liberacao de dinheiro" (positivo).

Exemplos dos maiores:
| ID | Extrato Net | Sistema Net | Diferenca |
|---|---:|---:|---:|
| 139749344683 | 0,00 | 4.318,05 | -4.318,05 |
| 140422465618 | 0,00 | 2.318,05 | -2.318,05 |
| 140422485450 | 0,00 | 2.000,00 | -2.000,00 |
| 140563976561 | 0,00 | 1.133,78 | -1.133,78 |
| 140132250075 | 0,00 | 729,88 | -729,88 |
| 141223348545 | 0,00 | 665,12 | -665,12 |
| 142304307843 | 0,00 | 657,70 | -657,70 |
| 137943496999 | 0,00 | 627,12 | -627,12 |
| 143324328284 | -8,98 | 563,54 | -572,52 |
| 142225887116 | 0,00 | 490,38 | -490,38 |

(Lista completa: 64 IDs com claims netting zero ou quase zero)

### Diferencas de Cambio em Subscriptions (3 IDs, R$ -29,43)

Assinaturas cobradas em USD tem taxa de cambio diferente no momento da aprovacao vs no extrato:

| ID | Servico | Extrato | Sistema | Diferenca |
|---|---|---:|---:|---:|
| 141215405790 | Claude.ai | -569,25 | -550,00 | -19,25 |
| 140496724089 | Supabase | -169,03 | -163,31 | -5,72 |
| 143199074090 | Notion | -131,94 | -127,48 | -4,46 |

---

## Reconciliacao do Gap

| Componente | Valor (R$) | % do Gap |
|---|---:|---:|
| Fluxo de reclamacoes/devolucoes (value mismatches) | -22.868,32 | 73,9% |
| IDs no sistema mas nao no extrato (refunded payments) | -7.974,96 | 25,8% |
| IDs invisiveis (nao no sistema) | -68,84 | 0,2% |
| FX subscriptions | -29,43 | 0,1% |
| **TOTAL EXPLICADO** | **-30.941,55** | **100,0%** |
| **GAP REAL** | **-30.941,55** | |
| **REMAINDER NAO EXPLICADO** | **0,00** | |

---

## Conclusoes e Recomendacoes

### 1. O gap e 100% explicado - nao ha dinheiro "perdido"
O gap de R$ 30.941,55 e inteiramente uma questao de modelagem: o sistema trata cada pagamento pelo seu `net_amount` (valor liberado), enquanto o extrato mostra o ciclo completo de claims (debito + liberacao + reembolso) que neta a zero.

### 2. Raiz do problema: claims/devolucoes
**73,9%** do gap vem de pagamentos com reclamacao. Para cada um:
- O extrato registra: `-valor_total + net_amount + refund = 0`
- O sistema registra: `+net_amount` (apenas a liberacao)

O sistema esta **superestimando a receita diaria** por R$ 22.868 no mes, porque inclui o `net_amount` de pagamentos cujo dinheiro foi efetivamente devolvido ao comprador.

### 3. Pagamentos refundados (25,8%)
4 pagamentos com status `refunded` (2x R$ 5000, 1x R$ 355.94, 1x R$ 45.90) existem na tabela `payments` com `money_release_date` em janeiro, mas nao aparecem no extrato. O dinheiro nunca foi efetivamente liberado na conta.

### 4. Acoes recomendadas
1. **Filtrar pagamentos refundados**: Pagamentos com `ml_status = 'refunded'` nao devem ser contados como receita liberada
2. **Ingerir DIFAL**: As 3 cobracas de DIFAL (R$ 77,64 total) devem ser importadas para mp_expenses
3. **Ingerir pagamento de cartao de credito**: ID 141963223933 (R$ -3.010,62) deve ser importado
4. **Reconciliar IDs de faturas**: A fatura 2775723042 no extrato corresponde ao 142352059685 no sistema - o sistema precisa mapear o ID alternativo
5. **Verificar liberacoes faltantes**: 9 "Liberacao de dinheiro" e 5 "QR Pix" (total R$ 3.895,61) existem no extrato mas nao no sistema - verificar se estes pagamentos estao em meses anteriores ou se faltam no sync
6. **Ajustar FX de subscriptions**: As subscriptions em USD tem diferenca cambial pequena (R$ 29,43 total) - considerar usar o valor do extrato como valor final

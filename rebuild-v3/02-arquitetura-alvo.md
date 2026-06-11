# 02 — Arquitetura-alvo

## Princípio 1: DOIS livros, não um

A confusão que afundou o projeto foi misturar caixa e competência num motor só.

```
Livro CAIXA   → ancorado no EXTRATO, regime de caixa, fecha ao centavo TODO DIA
Livro DRE     → ancorado nos FATOS (venda/estorno), regime de competência, fecha D+1
Ponte         → Caixa = DRE ± recebíveis a liberar ± retido/disputa
```

A ponte é a prova matemática: quando fecha, a diferença entre caixa e DRE é 100% explicada
por dinheiro que o ML ainda não liberou — não por buraco.

## Princípio 2: UMA engine só

Re-ancorar o fluxo novo no extrato; **aposentar o legacy engine**. Hoje coexistem (legacy XLSX
vs classifier V3 → mp_expenses) e nenhuma venceu → risco de double-count. O legacy ancorava no
extrato (conceito certo) mas era frágil (string-matching, 4 CSVs auxiliares, tolerância R$0,10).
Decisão: portar "extrato é a verdade" pro fluxo novo, aposentar o legacy de vez.

## Princípio 3: BAIXA dirigida pelo extrato (não pela promessa)

Hoje a baixa usa `data_pagamento = data_vencimento = money_release_date` (a PROMESSA do ML) e
`valor = nao_pago` da parcela. Por isso o fluxo de caixa do CA nunca bate com o banco.

```
HOJE:  scheduler chuta data/valor a partir da promessa do ML
CERTO: quando uma linha de crédito aparece no EXTRATO →
       acha a parcela no CA → baixa com a DATA e o VALOR reais do extrato
```

Com isso a baixa É o evento real de caixa → fluxo de caixa do CA == banco por construção.
Resolve de graça: liberação parcelada (1 recebível → N créditos → N baixas) e cancela-antes-de-
liberar (sem crédito, parcela nunca baixa → juiz aponta "a receber que nunca virou caixa").

## As 3 datas no Conta Azul (pra ver tudo nativo no CA)

| Data CA | Deve ser | Hoje | Status |
|---|---|---|---|
| **Competência** (DRE) | data da venda confirmada | `_to_brt_date(date_approved)` | ✅ certo |
| **Competência do ESTORNO** | data real do estorno BRT | era `datetime.now()` | ✅ **corrigido (Fase 3)** |
| **Vencimento** (a receber/pagar) | quando o ML promete liberar | `money_release_date` | 🟡 ok |
| **Baixa** (fluxo de caixa) | data+valor REAIS do extrato | usa a promessa | 🔴 **falta (Fase 3-full)** |

Com as 3 certas, os relatórios NATIVOS do CA ficam corretos: DRE (competência), Contas a
receber/pagar (vencimento), Fluxo de caixa (baixa == banco). O sistema externo vira só MOTOR
(alimenta o CA) + JUIZ (reconcilia); a visualização acontece dentro do CA.

## Lançamento em DUAS fases (resolve a defasagem do fee)

```
Fase provisória (dia da venda):  lança no CA com charges_details (estimativa)
Fase final (release report sai): reconcilia contra MP_FEE/SHIPPING_FEE → ajusta o delta
```
O `release_report_validator` é a fase 2 (precisa virar bidirecional — Fase 4).

## Os 4 PORTÕES (impedem acúmulo)

```
P1 diário:  Σ lançado no dia == Σ extrato do dia (== PARTIAL_BALANCE)  → senão dia NÃO fecha
P2 diário:  fila pending_review vazia
P3 mensal:  todo dia do mês passou P1+P2
P4 mensal:  ponte Caixa = DRE ± recebíveis fecha  → senão mês NÃO assina
```

## Fonte-da-verdade por relatório (o pulo do gato)

Cada relatório é a verdade de um CAMPO diferente. Juntar por chave e pegar cada número da
fonte autoritativa:

| Relatório | É a verdade de… |
|---|---|
| **Extrato CSV** (account_statement / bank_report) | **caixa**: valor líquido real, saldo corrido (`PARTIAL_BALANCE`), data do crédito |
| **Release report** (reserve-release) | **fees finais** (MP_FEE, SHIPPING_FEE, FINANCING_FEE), payouts, cashback, créditos de frete |
| **Payments API** (`charges_details`) | a **venda** no momento: bruto, status, order_id, `date_approved`, fee *estimado* |
| **VENDAS** | **quem paga o frete** (shipping<0 = vendedor), título, pack_id |
| **Pós-venda** | **devoluções e mediações** (data do estorno) |
| **Settlement** (DINHEIRO) | **previsão** (dinheiro ainda não liberado) |

O erro do fluxo novo: pegou valor da Payments API e *torceu* pra bater com o extrato. O certo:
extrato manda no caixa, release report manda no fee, API manda no fato da venda.

## As duas pontes (a tranquilidade na reunião)

**Ponte caixa↔DRE:** `Caixa do mês = DRE do mês ± Δrecebíveis a liberar ± retido/disputa`.

**Ponte DRE↔painel ML:** explica cada centavo da divergência com o painel. Drivers conhecidos
(todos consultáveis pelo sistema):

| Driver | Por que diverge |
|---|---|
| Competência de devolução | painel conta no mês da venda; DRE no mês do estorno |
| by_admin / kit-split | ML conta como devolução; você pula (splits cobrem a receita) |
| Subsídio ML (1.3.7) | net > calculado → receita de subsídio |
| financing_fee | net-neutral, excluído da comissão |
| Timezone / borda de dia | ML API UTC-4 vs reports BRT |
| Arredondamento | relatórios ML não amarram ao centavo (<R$200/seller) |

Formato do entregável (ex NET-AIR jan, números reais da doc):
```
Painel ML — devoluções jan        R$ 159.991
(−) by_admin / kit-split          −  4.752
(−) estornos só ocorridos em fev  − 62.103   → entram no DRE de fev
= devolução esperada no DRE jan   R$ 93.136
  devolução efetiva no DRE jan    R$ 93.136
Resíduo não explicado             R$      0   ✅
```
"Bater" não é igualar o painel (impossível e contabilmente errado) — é **toda diferença ter
nome e valor**.

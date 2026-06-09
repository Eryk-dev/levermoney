# 06 — Modelo de reconciliação contábil

O coração do sistema. Como o caixa e o DRE fecham, as identidades, e as sutilezas que pegam.

## A âncora do caixa

O extrato do MP traz, no topo: `INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE`, e cada linha
traz `PARTIAL_BALANCE` (saldo corrido). Isso dá o **saldo absoluto todo dia**.

```
Identidade de integridade:  INITIAL_BALANCE + Σ(TRANSACTION_NET_AMOUNT) == FINAL_BALANCE
Identidade por linha:       saldo_anterior + net == PARTIAL_BALANCE
```
Provado: bate ao centavo nos 10 extratos (jan-mai × 141air + net-air), com continuidade
(FINAL de um mês == INITIAL do próximo). **O extrato é uma fonte de verdade interna consistente.**

Por isso o caixa NÃO acumula drift: você ancora no saldo ABSOLUTO, não em deltas. Ou o dia
fecha contra `PARTIAL_BALANCE`, ou a divergência aparece naquele dia (não acumula escondida).

## Caixa vs DRE (dois regimes)

```
CAIXA = quando o dinheiro se move (regime de caixa). Âncora = extrato. Fecha ao centavo/dia.
DRE   = quando o fato econômico ocorre (competência). Âncora = venda(date_approved)/estorno.
PONTE = Caixa do mês = DRE do mês ± Δrecebíveis a liberar ± retido/disputa
```

## A chave de join (funciona pra venda)

`REFERENCE_ID` da linha "Liberação de dinheiro" no extrato **== `payment.id`** da Payments API.
Confirmado: 289/289 casadas em 141air jan. O medo de "sem chave" era só pra non-venda
(PIX/boleto = texto livre), NÃO pra venda.

## Decomposição do caixa por linha do extrato

```
FINAL - INITIAL  =  Σ vendas (liberação + refund/disputa de venda)
                 +  Σ non-venda classificado (DIFAL, faturas, bônus, etc.)
                 +  Σ skip (PIX/boleto/transfer — coberto pelo classifier da API non-order)
                 +  Σ OTHER (não coberto — DEVE ser 0)
```
"Tudo bater" decompõe em:
1. **Vendas:** Σ CA (eventos date-aware) == Σ extrato sale-lines → resíduo → 0.
2. **Non-venda:** OTHER == 0 (cobertura 100%). Valor casa por construção (ingester usa o valor
   do extrato). ✅ Atingido após Fase 7 (0 linhas OTHER nas amostras).
3. **Skip:** coberto pela Payments API non-order (classify → mp_expenses). ⚠️ risco: PIX-saída
   que o ingester pula e a API não tem (ver Fase 1).

## Sutilezas que PEGAM (documentar pra não reintroduzir)

### 1. Status snapshot vs cash date (cross-month refund)
Um `bpp_refunded` libera em jan (+ no extrato) e estorna em fev (− no extrato). O processor,
vendo o status FINAL (refunded), cria receita(jan) + estorno(fev). Se você somar o lifecycle
ignorando data, dá ~0 — mas no caixa de JAN só entrou a liberação (+). **Recon tem que ser
date-aware:** receita conta em jan (vencimento=money_release_date jan), estorno conta em fev
(vencimento=data do estorno). Aí cada mês bate. (Seção [D] do harness faz isso.)

### 2. Double-count estorno × refund-debit
Um refund de venda aparece DUAS vezes: (a) processor cria estorno no CA; (b) o ingester
classifica a linha de débito do refund no extrato (dinheiro_retido/reembolso). Se ambos
contarem, double. O ingester real tem dedup (pula `debito_divida_disputa` se o payment já
consta refunded — extrato_ingester.py:696-707), mas o harness [E] ainda não replica isso →
o número de "vendas" em [E] não é confiável. **Refino pendente.**

### 3. net_received é pré-refund em partially_refunded
O `net_received_amount` do MP não reflete o caixa efetivo em refund parcial. `processor.
_compute_effective_net_amount` ajusta o campo armazenado, mas os lançamentos no CA não
acompanham (comissão/frete não revertidos proporcionalmente). Exposição: refunded −1.840 em
141air jan. (Fase 4.)

### 4. Refund parcial não reverte taxa
`_process_refunded` só cria estorno de taxa se devolução TOTAL (estorno_receita >= amount).
Em parcial, comissão/frete já lançados ficam sem contrapartida → resíduo no caixa.

## Per-payment net (como o processor calcula)
```
comissao = Σ charges_details[type=fee, from=collector, name!=financing_fee].amounts.original
frete_seller = max(0, Σ shipping[from=collector] - shipping_amount_buyer)
reconciled_net = amount - comissao - frete_seller
net_diff = net_received_amount - reconciled_net
  net_diff > 0 → Subsídio ML (receita 1.3.7)
  net_diff < 0 → Taxa oculta (despesa comissão — Fase 1, corrigido)
```

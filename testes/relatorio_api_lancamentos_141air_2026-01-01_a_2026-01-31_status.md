# Comparativo API Nova x Extrato MP - 141air - 01/01 a 31/01/2026 (Resumo por Status)

## Resumo
- Payment IDs no extrato: **505**
- IDs de venda (API): **429**
- IDs non-sale/legado: **76**
- IDs não resolvidos no endpoint de payments: **31**
- Total extrato do período: **R$ -3.385,83**
- Vendas (API) no extrato: **R$ 112.324,74**
- Vendas (API) líquido lançado: **R$ 145.158,84**
- Diferença vendas (extrato - lançado): **R$ -32.834,10**

## Fechamento por Status de Payment
- `approved`: ids=347 | bruto=R$ 129.656,12 | comissão=R$ 16.488,96 | frete=R$ 6.789,73 | líquido_lançado=R$ 106.377,43 | extrato=R$ 106.242,79 | diff=R$ -134,64
- `charged_back`: ids=1 | bruto=R$ 113,06 | comissão=R$ 13,57 | frete=R$ 16,41 | líquido_lançado=R$ 83,08 | extrato=R$ 83,08 | diff=R$ 0,00
- `in_mediation`: ids=3 | bruto=R$ 2.877,58 | comissão=R$ 367,90 | frete=R$ 155,80 | líquido_lançado=R$ 2.353,88 | extrato=R$ 2.353,88 | diff=R$ 0,00
- `refunded`: ids=78 | bruto=R$ 43.675,06 | comissão=R$ 5.740,99 | frete=R$ 1.589,62 | líquido_lançado=R$ 36.344,45 | extrato=R$ 3.644,99 | diff=R$ -32.699,46

## Recorte Operacional (status liquidados)
- Considera `approved` + `charged_back/reimbursed`.
- Extrato: **R$ 106.325,87**
- Líquido lançado API: **R$ 106.460,51**
- Diferença: **R$ -134,64**
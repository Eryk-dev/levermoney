# Comparativo API Nova x Extrato MP - 141air - 01/01 a 31/01/2026

## Escopo
- Seller: `141air`
- Periodo: `2026-01-01` a `2026-01-31`
- Fonte extrato: `testes/extrato 01.01 a 31.01 141Air.csv` (account_statement)
- Regra de lancamento API: `charges_details` direto (`fee` sem `financing_fee`, `shipping` menos `shipping_amount`).

## Resumo Geral
- Linhas do extrato: **706**
- `payment_id` unicos no extrato: **505**
- IDs de venda (API nova): **429**
- IDs non-sale/externos (legado): **76**
- IDs nao encontrados no endpoint de payments: **31**
- Total extrato no periodo: **R$ -3.385,83**

## Vendas (API Nova)
- Soma extrato dos IDs de venda: **R$ 112.324,74**
- Soma liquida esperada pelos lancamentos da API: **R$ 145.158,84**
- Diferenca vendas (extrato - API): **R$ -32.834,10**

## Non-Sale / Legado
- Soma extrato dos IDs nao-venda: **R$ -115.710,57**

## Fechamento de Saldo
- Saldo inicial (arquivo): **R$ 0,00**
- Saldo final (arquivo): **R$ -3.385,83**
- Saldo final pelo modelo (vendas API + non-sale extrato): **R$ 29.448,27**
- Diferenca final (arquivo - modelo): **R$ -32.834,10**

## Recorte Operacional (status liquidados)
- Considera `approved` + `charged_back/reimbursed`.
- Extrato: **R$ 106.325,87**
- API: **R$ 106.460,51**
- Diferenca: **R$ -134,64**

## Top Divergencias por Payment ID (venda)
- `139749344683`: extrato_id=R$ 0,00 | api_liquido=R$ 4.318,05 | diff=R$ -4.318,05 | linhas=3 | status=refunded
- `143762867120`: extrato_id=R$ 0,00 | api_liquido=R$ 4.121,55 | diff=R$ -4.121,55 | linhas=2 | status=refunded
- `143815855230`: extrato_id=R$ 0,00 | api_liquido=R$ 4.121,55 | diff=R$ -4.121,55 | linhas=2 | status=refunded
- `140422465618`: extrato_id=R$ 0,00 | api_liquido=R$ 2.318,05 | diff=R$ -2.318,05 | linhas=3 | status=refunded
- `140422485450`: extrato_id=R$ 0,00 | api_liquido=R$ 2.000,00 | diff=R$ -2.000,00 | linhas=2 | status=refunded
- `140563976561`: extrato_id=R$ 0,00 | api_liquido=R$ 1.133,78 | diff=R$ -1.133,78 | linhas=2 | status=refunded
- `140132250075`: extrato_id=R$ -0,00 | api_liquido=R$ 729,88 | diff=R$ -729,88 | linhas=3 | status=refunded
- `141223348545`: extrato_id=R$ 0,00 | api_liquido=R$ 665,12 | diff=R$ -665,12 | linhas=3 | status=refunded
- `142304307843`: extrato_id=R$ 0,00 | api_liquido=R$ 657,70 | diff=R$ -657,70 | linhas=3 | status=refunded
- `137943496999`: extrato_id=R$ -0,00 | api_liquido=R$ 627,12 | diff=R$ -627,12 | linhas=3 | status=refunded
- `143324328284`: extrato_id=R$ -8,98 | api_liquido=R$ 563,54 | diff=R$ -572,52 | linhas=4 | status=refunded
- `137617703230`: extrato_id=R$ -28,14 | api_liquido=R$ 527,71 | diff=R$ -555,85 | linhas=3 | status=refunded
- `142225887116`: extrato_id=R$ 0,00 | api_liquido=R$ 490,38 | diff=R$ -490,38 | linhas=3 | status=refunded
- `140545137977`: extrato_id=R$ 0,00 | api_liquido=R$ 434,45 | diff=R$ -434,45 | linhas=5 | status=refunded
- `141199931294`: extrato_id=R$ 0,00 | api_liquido=R$ 417,34 | diff=R$ -417,34 | linhas=3 | status=refunded
- `142100582011`: extrato_id=R$ 0,00 | api_liquido=R$ 407,75 | diff=R$ -407,75 | linhas=2 | status=refunded
- `141009085184`: extrato_id=R$ 0,00 | api_liquido=R$ 405,23 | diff=R$ -405,23 | linhas=3 | status=refunded
- `143415634883`: extrato_id=R$ 0,00 | api_liquido=R$ 397,36 | diff=R$ -397,36 | linhas=3 | status=refunded
- `137614895655`: extrato_id=R$ -75,04 | api_liquido=R$ 297,06 | diff=R$ -372,10 | linhas=6 | status=refunded
- `139930660133`: extrato_id=R$ -0,00 | api_liquido=R$ 352,52 | diff=R$ -352,52 | linhas=3 | status=refunded
- `140453365536`: extrato_id=R$ -0,00 | api_liquido=R$ 330,09 | diff=R$ -330,09 | linhas=3 | status=refunded
- `142841935136`: extrato_id=R$ 0,00 | api_liquido=R$ 318,87 | diff=R$ -318,87 | linhas=3 | status=refunded
- `140494466821`: extrato_id=R$ 0,00 | api_liquido=R$ 316,40 | diff=R$ -316,40 | linhas=3 | status=refunded
- `140688038213`: extrato_id=R$ 0,00 | api_liquido=R$ 313,23 | diff=R$ -313,23 | linhas=2 | status=refunded
- `141527595509`: extrato_id=R$ -70,35 | api_liquido=R$ 203,89 | diff=R$ -274,24 | linhas=4 | status=refunded
- `140087737157`: extrato_id=R$ 0,00 | api_liquido=R$ 270,35 | diff=R$ -270,35 | linhas=3 | status=refunded
- `140240998217`: extrato_id=R$ 272,42 | api_liquido=R$ 541,69 | diff=R$ -269,27 | linhas=5 | status=refunded
- `142389527043`: extrato_id=R$ 0,00 | api_liquido=R$ 248,47 | diff=R$ -248,47 | linhas=3 | status=refunded
- `142952947024`: extrato_id=R$ 0,00 | api_liquido=R$ 248,47 | diff=R$ -248,47 | linhas=3 | status=refunded
- `140898783536`: extrato_id=R$ 0,00 | api_liquido=R$ 240,41 | diff=R$ -240,41 | linhas=5 | status=refunded
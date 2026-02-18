# Comparativo API Nova x Extrato MP - 141air - 01/02 a 15/02/2026

## Escopo
- Seller: `141air`
- Periodo: `2026-02-01` a `2026-02-15`
- Fonte extrato: `testes/extrato 01.02 a 15.02 141Air.csv`
- Regra de lancamento API: `charges_details` direto (`fee` sem `financing_fee`, `shipping` menos `shipping_amount`).
- Comparacao por `payment_id`: soma de movimentos no extrato por ID vs liquido esperado da API para IDs de venda.

## Resumo Geral
- Linhas do extrato: **314**
- `payment_id` unicos no extrato: **227**
- IDs de venda (API nova): **177**
- IDs non-sale/externos (legado): **50**
- IDs nao encontrados no endpoint de payments (tratados como nao-venda): **39**
- Total extrato no periodo: **R$ 451,76**

## Vendas (API Nova)
- Soma extrato dos IDs de venda: **R$ 25.684,03**
- Soma liquida esperada pelos lancamentos da API: **R$ 47.518,50**
- Diferenca vendas (extrato - API): **R$ -21.834,47**
- Receita bruta total lancada: **R$ 58.332,71**
- Despesa comissao total lancada: **R$ 7.334,92**
- Despesa frete total lancada: **R$ 3.479,29**

## Non-Sale / Legado
- Soma extrato dos IDs nao-venda: **R$ -25.232,27**

## Fechamento de Saldo
- Saldo inicial (arquivo): **R$ 1.090,40**
- Saldo final (arquivo): **R$ 1.542,16**
- Saldo final calculado pelo extrato (inicial + movimentos): **R$ 1.542,16**
- Saldo final pelo modelo (vendas API + non-sale extrato): **R$ 23.376,63**
- Diferenca final (arquivo - modelo): **R$ -21.834,47**

## Top Divergencias por Payment ID (venda)
- `142959458860`: extrato_id=R$ -5.034,60 | api_liquido=R$ 2.517,30 | diff=R$ -7.551,90 | linhas=7 | status=refunded
- `140395321666`: extrato_id=R$ -1.369,76 | api_liquido=R$ 1.369,76 | diff=R$ -2.739,52 | linhas=1 | status=in_mediation
- `141747614296`: extrato_id=R$ -1.438,40 | api_liquido=R$ 719,20 | diff=R$ -2.157,60 | linhas=6 | status=refunded
- `141427378366`: extrato_id=R$ -720,32 | api_liquido=R$ 634,52 | diff=R$ -1.354,84 | linhas=2 | status=in_mediation
- `140563976561`: extrato_id=R$ -0,00 | api_liquido=R$ 1.133,78 | diff=R$ -1.133,78 | linhas=3 | status=refunded
- `141385949804`: extrato_id=R$ -486,77 | api_liquido=R$ 405,92 | diff=R$ -892,69 | linhas=5 | status=refunded
- `140668502871`: extrato_id=R$ -349,60 | api_liquido=R$ 349,60 | diff=R$ -699,20 | linhas=1 | status=in_mediation
- `142181590693`: extrato_id=R$ -230,87 | api_liquido=R$ 230,87 | diff=R$ -461,74 | linhas=4 | status=refunded
- `145533986422`: extrato_id=R$ 0,00 | api_liquido=R$ 437,58 | diff=R$ -437,58 | linhas=3 | status=refunded
- `143559496714`: extrato_id=R$ 0,00 | api_liquido=R$ 415,70 | diff=R$ -415,70 | linhas=4 | status=refunded
- `142100582011`: extrato_id=R$ 0,00 | api_liquido=R$ 407,75 | diff=R$ -407,75 | linhas=3 | status=refunded
- `143565916122`: extrato_id=R$ -67,35 | api_liquido=R$ 255,52 | diff=R$ -322,87 | linhas=6 | status=refunded
- `140806214821`: extrato_id=R$ -203,86 | api_liquido=R$ 93,95 | diff=R$ -297,81 | linhas=4 | status=refunded
- `143104571692`: extrato_id=R$ -78,34 | api_liquido=R$ 203,89 | diff=R$ -282,23 | linhas=4 | status=refunded
- `144620291521`: extrato_id=R$ -46,90 | api_liquido=R$ 193,96 | diff=R$ -240,86 | linhas=1 | status=in_mediation
- `139380061139`: extrato_id=R$ -0,00 | api_liquido=R$ 229,49 | diff=R$ -229,49 | linhas=3 | status=refunded
- `143196577333`: extrato_id=R$ 0,00 | api_liquido=R$ 228,29 | diff=R$ -228,29 | linhas=3 | status=refunded
- `143527361181`: extrato_id=R$ 0,00 | api_liquido=R$ 226,51 | diff=R$ -226,51 | linhas=2 | status=in_mediation
- `144260916454`: extrato_id=R$ -0,00 | api_liquido=R$ 216,65 | diff=R$ -216,65 | linhas=5 | status=refunded
- `141085645218`: extrato_id=R$ -97,47 | api_liquido=R$ 97,47 | diff=R$ -194,94 | linhas=4 | status=refunded
- `143236034402`: extrato_id=R$ 0,00 | api_liquido=R$ 192,46 | diff=R$ -192,46 | linhas=3 | status=refunded
- `144575400964`: extrato_id=R$ 112,75 | api_liquido=R$ 303,08 | diff=R$ -190,33 | linhas=4 | status=refunded
- `142961208182`: extrato_id=R$ 0,00 | api_liquido=R$ 185,02 | diff=R$ -185,02 | linhas=3 | status=refunded
- `141979194794`: extrato_id=R$ -69,86 | api_liquido=R$ 96,99 | diff=R$ -166,85 | linhas=6 | status=refunded
- `143308563139`: extrato_id=R$ 0,00 | api_liquido=R$ 117,48 | diff=R$ -117,48 | linhas=4 | status=refunded
- `144500605642`: extrato_id=R$ 0,00 | api_liquido=R$ 109,35 | diff=R$ -109,35 | linhas=2 | status=in_mediation
- `141693604662`: extrato_id=R$ -13,96 | api_liquido=R$ 77,26 | diff=R$ -91,22 | linhas=3 | status=refunded
- `144170413604`: extrato_id=R$ 0,00 | api_liquido=R$ 75,81 | diff=R$ -75,81 | linhas=4 | status=refunded
- `144499689691`: extrato_id=R$ -0,00 | api_liquido=R$ 60,92 | diff=R$ -60,92 | linhas=3 | status=refunded
- `145508379302`: extrato_id=R$ 0,00 | api_liquido=R$ 60,92 | diff=R$ -60,92 | linhas=5 | status=refunded

## Observacao Importante
- IDs nao encontrados no endpoint `payments/{id}` foram tratados como nao-venda/externos (fluxo legado).
- Isso e esperado para parte das movimentacoes de extrato (transferencias, reservas, referencias nao mapeadas como payment).

## Arquivos Gerados
- `testes/relatorio_api_lancamentos_141air_2026-02-01_a_2026-02-15.md`
- `testes/relatorio_api_lancamentos_141air_2026-02-01_a_2026-02-15.csv`
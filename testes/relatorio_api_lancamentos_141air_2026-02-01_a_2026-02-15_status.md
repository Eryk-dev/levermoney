# Comparativo API Nova x Extrato MP - 141air - 01/02 a 15/02/2026 (Resumo por Status)

## Resumo
- Payment IDs no extrato: **227**
- IDs de venda (API): **177**
- IDs non-sale/legado: **50**
- IDs não resolvidos no endpoint de payments: **39**
- Total extrato do período: **R$ 451,76**
- Vendas (API) no extrato: **R$ 25.684,03**
- Vendas (API) líquido lançado: **R$ 47.518,50**
- Diferença vendas (extrato - lançado): **R$ -21.834,47**

## Fechamento por Status de Payment
- `approved`: ids=140 | bruto=R$ 43.464,41 | comissão=R$ 5.494,46 | frete=R$ 2.683,35 | líquido_lançado=R$ 35.286,60 | extrato=R$ 35.286,60 | diff=R$ 0,00
- `charged_back`: ids=1 | bruto=R$ 115,80 | comissão=R$ 13,90 | frete=R$ 13,96 | líquido_lançado=R$ 87,94 | extrato=R$ 87,94 | diff=R$ 0,00
- `in_mediation`: ids=6 | bruto=R$ 3.542,70 | comissão=R$ 440,34 | frete=R$ 218,66 | líquido_lançado=R$ 2.883,70 | extrato=R$ -2.486,58 | diff=R$ -5.370,28
- `refunded`: ids=30 | bruto=R$ 11.209,80 | comissão=R$ 1.386,22 | frete=R$ 563,32 | líquido_lançado=R$ 9.260,26 | extrato=R$ -7.203,93 | diff=R$ -16.464,19

## Recorte Operacional (status liquidados)
- Considera `approved` + `charged_back/reimbursed` (movimento que fecha como liberação de venda).
- Extrato: **R$ 35.374,54**
- Líquido lançado API: **R$ 35.374,54**
- Diferença: **R$ 0,00**

## Observação de Conciliação
- A divergência total do período está concentrada em `refunded` e `in_mediation`, que misturam retenções, débitos de disputa e estornos no extrato.
- Extrato non-sale/legado no período: **R$ -25.232,27**

## Top Divergências (IDs de venda)
- `142959458860`: status=refunded/bpp_refunded | extrato=R$ -5.034,60 | liquido_lancado=R$ 2.517,30 | diff=R$ -7.551,90
- `140395321666`: status=in_mediation/pending | extrato=R$ -1.369,76 | liquido_lancado=R$ 1.369,76 | diff=R$ -2.739,52
- `141747614296`: status=refunded/bpp_refunded | extrato=R$ -1.438,40 | liquido_lancado=R$ 719,20 | diff=R$ -2.157,60
- `141427378366`: status=in_mediation/pending | extrato=R$ -720,32 | liquido_lancado=R$ 634,52 | diff=R$ -1.354,84
- `140563976561`: status=refunded/bpp_refunded | extrato=R$ -0,00 | liquido_lancado=R$ 1.133,78 | diff=R$ -1.133,78
- `141385949804`: status=refunded/bpp_refunded | extrato=R$ -486,77 | liquido_lancado=R$ 405,92 | diff=R$ -892,69
- `140668502871`: status=in_mediation/pending | extrato=R$ -349,60 | liquido_lancado=R$ 349,60 | diff=R$ -699,20
- `142181590693`: status=refunded/bpp_refunded | extrato=R$ -230,87 | liquido_lancado=R$ 230,87 | diff=R$ -461,74
- `145533986422`: status=refunded/bpp_refunded | extrato=R$ 0,00 | liquido_lancado=R$ 437,58 | diff=R$ -437,58
- `143559496714`: status=refunded/bpp_refunded | extrato=R$ 0,00 | liquido_lancado=R$ 415,70 | diff=R$ -415,70
- `142100582011`: status=refunded/bpp_refunded | extrato=R$ 0,00 | liquido_lancado=R$ 407,75 | diff=R$ -407,75
- `143565916122`: status=refunded/bpp_refunded | extrato=R$ -67,35 | liquido_lancado=R$ 255,52 | diff=R$ -322,87
- `140806214821`: status=refunded/bpp_refunded | extrato=R$ -203,86 | liquido_lancado=R$ 93,95 | diff=R$ -297,81
- `143104571692`: status=refunded/bpp_refunded | extrato=R$ -78,34 | liquido_lancado=R$ 203,89 | diff=R$ -282,23
- `144620291521`: status=in_mediation/pending | extrato=R$ -46,90 | liquido_lancado=R$ 193,96 | diff=R$ -240,86
- `139380061139`: status=refunded/bpp_refunded | extrato=R$ -0,00 | liquido_lancado=R$ 229,49 | diff=R$ -229,49
- `143196577333`: status=refunded/bpp_refunded | extrato=R$ 0,00 | liquido_lancado=R$ 228,29 | diff=R$ -228,29
- `143527361181`: status=in_mediation/pending | extrato=R$ 0,00 | liquido_lancado=R$ 226,51 | diff=R$ -226,51
- `144260916454`: status=refunded/bpp_refunded | extrato=R$ -0,00 | liquido_lancado=R$ 216,65 | diff=R$ -216,65
- `141085645218`: status=refunded/bpp_refunded | extrato=R$ -97,47 | liquido_lancado=R$ 97,47 | diff=R$ -194,94

## Arquivos
- `testes/relatorio_api_lancamentos_141air_2026-02-01_a_2026-02-15.csv`
- `testes/relatorio_api_lancamentos_141air_2026-02-01_a_2026-02-15.md`
- `testes/relatorio_api_lancamentos_141air_2026-02-01_a_2026-02-15_status.csv`
- `testes/relatorio_api_lancamentos_141air_2026-02-01_a_2026-02-15_status.md`
# Relatorio de Lancamentos da API Nova - 141air - 2026-02-15

## Escopo
- Seller: `141air`
- Data conciliada (competencia/liberacao): `2026-02-15`
- Saldo inicial informado: `R$ 9.687,61`
- Fonte extrato: `testes/extrato 01.02 a 15.02 141Air.csv`
- Regra de calculo: charges_details direto (fee sem financing_fee, shipping menos shipping_amount).

## Resumo do Dia
- Linhas no extrato (15/02): **15**
- Total extrato do dia: **R$ -8.145,45**
- Linhas tratadas como venda/API nova: **14**
- Linhas non-sale (legado): **1**
- Total extrato das vendas: **R$ 4.007,61**
- Total liquido calculado pelos lancamentos da API: **R$ 4.007,61**
- Receita bruta lancada (contas a receber): **R$ 4.775,73**
- Despesa comissao lancada (contas a pagar): **R$ 561,58**
- Despesa frete lancada (contas a pagar): **R$ 206,54**
- Saldo final pelo extrato: **R$ 1.542,16**
- Saldo final pelo modelo de lancamentos + non-sale extrato: **R$ 1.542,16**
- Diferenca final: **R$ 0,00**

## Baixas (simulacao da madrugada de 16/02)
- Pagamentos de venda aptos a baixa (released e release_date <= 2026-02-15): **14** de 14

## Divergencias Extrato x Liquido Calculado
- Nenhuma divergencia no dia 15/02 para pagamentos de venda.

## Detalhe por Movimentacao (Extrato 15/02)
| ID | Tipo Extrato | API trata como venda? | Bruto | Comissao | Frete | Liquido lancamento | Valor extrato | Diff | Status DB atual | Acao se rodar agora |
|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 143277354070 | Liberação de dinheiro  | sim | R$ 25,00 | R$ 1,12 | R$ 0,00 | R$ 23,88 | R$ 23,88 | R$ 0,00 | - | launch |
| 142596223717 | Pagamento com Código QR Pix Vinícius Gabriel Brixius | sim | R$ 297,80 | R$ 26,80 | R$ 0,00 | R$ 271,00 | R$ 271,00 | R$ 0,00 | - | launch |
| 143939314942 | Liberação de dinheiro  | sim | R$ 141,64 | R$ 24,08 | R$ 17,16 | R$ 100,40 | R$ 100,40 | R$ 0,00 | - | launch |
| 144574485192 | Pagamento com Código QR Pix FLAVIO GUEDES DONATO ME | sim | R$ 276,35 | R$ 24,61 | R$ 23,45 | R$ 228,29 | R$ 228,29 | R$ 0,00 | synced | skip_already_synced |
| 144553021998 | Liberação de dinheiro  | sim | R$ 35,90 | R$ 10,81 | R$ 0,00 | R$ 25,09 | R$ 25,09 | R$ 0,00 | synced | skip_already_synced |
| 144899316468 | Pagamento com Código QR Pix Sivaldo da Silva Santos | sim | R$ 160,90 | R$ 19,31 | R$ 19,30 | R$ 122,29 | R$ 122,29 | R$ 0,00 | synced | skip_already_synced |
| 144846013476 | Liberação de dinheiro  | sim | R$ 1.957,90 | R$ 234,95 | R$ 28,45 | R$ 1.694,50 | R$ 1.694,50 | R$ 0,00 | synced | skip_already_synced |
| 145701403811 | Dinheiro reservado Renda | nao | R$ 12.153,06 | R$ 0,00 | R$ 0,00 | R$ 12.153,06 | R$ -12.153,06 | R$ 0,00 | - | skip_non_sale_daily_sync_legacy |
| 144359445042 | Liberação de dinheiro  | sim | R$ 56,90 | R$ 16,14 | R$ 0,00 | R$ 40,76 | R$ 40,76 | R$ 0,00 | synced | skip_already_synced |
| 144297217627 | Dinheiro recebido  | sim | R$ 238,67 | R$ 21,26 | R$ 23,45 | R$ 193,96 | R$ 193,96 | R$ 0,00 | synced | skip_already_synced |
| 144197811337 | Dinheiro recebido  | sim | R$ 43,50 | R$ 10,44 | R$ 12,87 | R$ 20,19 | R$ 20,19 | R$ 0,00 | synced | skip_already_synced |
| 144197841299 | Pagamento com Código QR Pix ANDRE LOPES DA SILVA | sim | R$ 43,49 | R$ 0,00 | R$ 0,00 | R$ 43,49 | R$ 43,49 | R$ 0,00 | synced | skip_already_synced |
| 144612647582 | Pagamento com Código QR Pix ELTON JONHN FOSCH | sim | R$ 1.130,90 | R$ 135,71 | R$ 44,45 | R$ 950,74 | R$ 950,74 | R$ 0,00 | synced | skip_already_synced |
| 144141627247 | Pagamento com Código QR Pix JM CENTRO AUTOMOTIVO | sim | R$ 118,90 | R$ 14,27 | R$ 13,96 | R$ 90,67 | R$ 90,67 | R$ 0,00 | synced | skip_already_synced |
| 143988623955 | Dinheiro recebido  | sim | R$ 247,88 | R$ 22,08 | R$ 23,45 | R$ 202,35 | R$ 202,35 | R$ 0,00 | synced | skip_already_synced |

## Arquivos Gerados
- `testes/relatorio_api_lancamentos_141air_2026-02-15.md`
- `testes/relatorio_api_lancamentos_141air_2026-02-15.csv`
# DRE por Competência (Amostra) - 141Air - 01/02 a 15/02/2026

Regra usada:
- Competência da venda = `date_approved` (BRT).
- Vencimento/baixa (`money_release_date`) não altera competência.
- Comissão/frete calculados por `charges_details` (sem `financing_fee`, frete líquido do seller).
- `refunded`/`charged_back` (não reimbursed) entram como devolução na data do refund.

## Estrutura dos lançamentos no Conta Azul (competência)
- Receita de venda ML (contas a receber)
- Despesa comissão ML (contas a pagar)
- Despesa frete MercadoEnvios (contas a pagar)
- Devoluções/cancelamentos (estorno de receita, quando houver refund)
- Estorno de taxas ML (quando devolução total)

## DRE (competência do período)
- Receita Bruta: **R$ 57.379,26**
- (-) Devoluções e Cancelamentos: **R$ 1.593,81**
- Receita Líquida: **R$ 55.785,45**
- (-) Comissão ML: **R$ 7.109,26**
- (-) Frete MercadoEnvios: **R$ 3.488,58**
- (+) Estorno de Taxas: **R$ 293,29**
- Resultado Variável (antes de custos fixos/impostos): **R$ 45.480,90**
- Margem Variável: **81.53%**

## Contexto de vencimento/liberação (não afeta competência)
- Vendas ainda a liberar após 15/02: **144** | valor líquido **R$ 34.224,28**
- Vendas já liberadas até 15/02: **59** | valor líquido **R$ 12.557,14**

## Arquivos
- `testes/dre_competencia_amostra_141air_2026-02-01_a_2026-02-15.md`
- `testes/dre_competencia_diario_141air_2026-02-01_a_2026-02-15.csv`
- `testes/lancamentos_amostra_conta_azul_141air_2026-02-01_a_2026-02-15.csv`
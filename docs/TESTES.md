> Extraido do CLAUDE.md principal. Ver CLAUDE.md para visao geral.

## 18. Testes: Simulacao com Dados Reais

O sistema nao possui testes unitarios. A validacao e feita por **simulacao com dados reais do ML**, sem gravar nada no Conta Azul nem alterar o Supabase (tabela payments/ca_jobs).

### 18.1 Ferramenta: `simulate_backfill.py`

Script standalone que replica a logica do `processor.py` + `backfill.py` localmente.

**O que faz:**
1. Conecta ao Supabase para obter config do seller (tokens ML, IDs CA)
2. Busca payments no ML via API (mesmo endpoint do backfill)
3. Para cada payment, busca order e shipping costs (mesmo fluxo do processor)
4. Aplica toda a logica de classificacao e calculo
5. Gera relatorio no terminal + arquivo JSON detalhado

**O que NAO faz:** NAO grava no Supabase, NAO enfileira no CA, NAO chama CA API.

### 18.2 Como Rodar

```bash
# 1. Editar constantes no topo do script
SELLER_SLUG = "netparts-sp"   # slug do seller
BEGIN_DATE = "2026-02-01"      # YYYY-MM-DD
END_DATE = "2026-02-01"        # YYYY-MM-DD

# 2. Executar
cd "lever money claude"
python3 simulate_backfill.py
```

**Sellers disponiveis:**

| Slug | Nome | ML User ID |
|------|------|------------|
| `141air` | 141AIR | 1963376627 |
| `net-air` | NET AIR | 421259712 |
| `netparts-sp` | NETPARTS SP | 1092904133 |

**Output:** relatorio no terminal + `simulate_report_{seller}_{data}.json`

### 18.3 Checklist de Validacao

**Filtros:**
- [ ] Payments sem `order_id` → classificar em `mp_expenses` (modo `classifier`) ou deferir para legado (modo `legacy`)
- [ ] `marketplace_shipment` → SKIP
- [ ] Payments com `collector_id` → SKIP (compra, nao venda)
- [ ] Status `cancelled`/`rejected` → SKIP

**Vendas aprovadas:**
- [ ] 1 receita (contas-a-receber) com valor bruto = `transaction_amount`
- [ ] Comissao = soma de `charges_details[type=fee, from=collector]` sem `financing_fee`
- [ ] Frete seller = `max(0, shipping_collector - shipping_amount)` (sem fallback por shipment_costs)
- [ ] Competencia = `_to_brt_date(date_approved)` (NAO date_created)
- [ ] Vencimento = `money_release_date`
- [ ] Conferencia: `receita - comissao - frete ≈ net`

**Caixa diario (fechamento exato com extrato):**
- [ ] Fonte = `account_statement` (`release_report` / `bank_report`)
- [ ] `baixa_api_dia` = soma `net_api` de vendas liquidadas com `money_release_date = dia`
- [ ] `ajustes_legado_dia` = demais linhas do extrato do dia (`refund`, `mediation`, `reserve`, non-sale)
- [ ] `extrato_total_dia - (baixa_api_dia + ajustes_legado_dia) = 0`

**Devolucoes:**
- [ ] Gera receita original + despesas + estorno receita + estorno taxa
- [ ] Estorno nao excede `transaction_amount`
- [ ] Estorno taxa so em refund total
- [ ] `charged_back` + `reimbursed` → APPROVED (sem estorno)
- [ ] `charged_back` sem `reimbursed` → REFUNDED

**Categorias CA esperadas:**

| Tipo | Categoria |
|------|-----------|
| Receita venda | 1.1.1 MercadoLibre |
| Comissao | 2.8.2 Comissoes Marketplace |
| Frete seller | 2.9.4 MercadoEnvios |
| Devolucao | 1.2.1 Devolucoes e Cancelamentos |
| Estorno taxa | 1.3.4 Estornos de Taxas |

### 18.4 Fluxo Completo: Simulacao → Producao

```
1. simulate_backfill.py (analise offline, sem side effects)
       ↓
2. Verificar checklist (secao 18.3)
       ↓
3. Conferir com relatorio CSV do ML (mesmos totais?)
       ↓
4. GET /backfill/{seller}?begin_date=...&end_date=...&dry_run=true
       ↓
5. Comparar dry_run com simulacao
       ↓
6. GET /backfill/{seller}?begin_date=...&end_date=...&dry_run=false
       ↓
7. GET /queue/status (monitorar fila)
       ↓
8. Verificar lancamentos no CA
```

### 18.5 Resultado de Referencia

**NETPARTS SP — 01/02/2026** (testado 2026-02-13):

```
87 payments | 74 approved | 4 refunded | 8 skipped | 1 pending

Categorias CA:
  1.1.1 MercadoLibre (Receita):      R$ 10.075,29
  2.8.2 Comissoes Marketplace:        R$  1.633,67
  2.9.4 MercadoEnvios:                R$    725,77
  1.2.1 Devolucoes e Cancelamentos:   R$    644,36
  1.3.4 Estornos de Taxas:            R$    155,48

Aprovadas: receita=9.430,93 | comissao=1.530,61 | frete=673,35 | net=7.261,95
```

**141AIR — 01/01/2026 a 31/01/2026 (account_statement):**
```
Extrato total: R$ -3.385,83
Comparativo caixa diario exato (API baixas + ajustes legado): diff = R$ 0,00

Observacao:
- Comparativo mensal por payment_id pode divergir em refunded/in_mediation.
- Comparativo diario por caixa (regra 11.3b) e o criterio oficial para bater com extrato.
```

### 18.6 Estrutura do JSON de Saida

```json
{
  "payment_id": 143670186451,
  "ml_status": "approved",
  "order_id": 2000006829820543,
  "amount": 259.39,
  "net": 196.01,
  "action": "APPROVED",          // APPROVED | REFUNDED | CHARGED_BACK | SKIP | PENDING
  "skip_reason": null,           // preenchido quando action=SKIP
  "shipping_seller": 23.45,
  "comissao": 39.93,
  "competencia": "2026-02-01",
  "money_release_date": "2026-02-17",
  "item_title": "Ventilador Interno...",
  "ca_entries": [
    {
      "tipo": "RECEITA (contas-a-receber)",
      "categoria_id": "78f42170-...",
      "categoria_nome": "1.1.1 MercadoLibre (Receita)",
      "valor": 259.39,
      "descricao": "Venda ML #2000006829820543 - Ventilador...",
      "data_competencia": "2026-02-01",
      "data_vencimento": "2026-02-17"
    }
  ]
}
```

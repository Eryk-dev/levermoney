> Extraido do CLAUDE.md principal. Ver CLAUDE.md para visao geral.

## 11. Regras de Negocio CRITICAS

### 11.1 Comissao ML
```
comissao_ml = SUM(charges_details[type=fee, accounts.from=collector, name!=financing_fee])
frete_seller = max(0, SUM(charges_details[type=shipping, accounts.from=collector]) - shipping_amount)
liquido_calculado = amount - comissao_ml - frete_seller
```
**NAO usar** `fee_details` (incompleto). **USAR** `charges_details` como fonte de verdade.
`financing_fee` e net-neutral e deve ser excluido da comissao contabil.

### 11.1b Subsidio ML (net > calculado)
Quando `net_received_amount` for maior que `amount - fee - frete`, o diff vira
receita de subsidio (`Subsidio ML - Payment {id}`, categoria 1.3.7).

### 11.1c Backfill de Fees Faltantes
`/backfill/{seller}` pode reprocessar payments ja finalizados quando
`processor_fee`/`processor_shipping` estiverem nulos (`reprocess_missing_fees=true`).

### 11.2 financing_fee e NET-NEUTRAL
`financing_fee` = `financing_transfer` (pass-through). **NAO** gera despesa no CA. Ja esta descontado do net.

### 11.3 Datas
- **competencia** = `_to_brt_date(date_approved)` — quando o pagamento foi confirmado
- ML API retorna UTC-4, reports ML usam BRT (UTC-3)
- **vencimento/baixa** = `money_release_date`

### 11.3b Caixa x Competencia (regra operacional)
- **Competencia (DRE):** reconhece venda em `date_approved` (BRT), independente da baixa.
- **Caixa diario:** compara contra `account_statement` por dia (nao por payment_id mensal).
- **Baixa API do dia:** soma `net_api` de vendas liquidadas (`approved` + `charged_back/reimbursed`) com `money_release_date = dia`.
- **Ajustes legado do dia:** todas as demais linhas do extrato (`refund`, `mediation`, `reserve_for_dispute`, `shipping`, `payout`, non-sale).
- Regra de fechamento diario:
  `extrato_total_dia = baixa_api_dia + ajustes_legado_dia`
- Comparar apenas por `payment_id` agregado no mes pode gerar divergencia artificial em `refunded`/`in_mediation`.

### 11.4 Filtros de Skip (payments que NAO sao vendas)
- Sem `order_id` → **V3:** classificado em `mp_expenses` via `expense_classifier.py` (NAO mais skip)
- `description == "marketplace_shipment"` → frete pago pelo comprador (payment separado)
- `collector.id is not None` → compra (o seller e o comprador, nao vendedor)
- `status == "refunded"` + `status_detail == "by_admin"` + NAO synced → skip (kit split, novos payments serao processados separadamente)
- `operation_type == "partition_transfer"` → skip (movimentacao interna MP)
- `operation_type == "payment_addition"` → skip (frete adicional vinculado a order)

### 11.4b Order 404 Fallback
Se `get_order()` retorna erro (ex: 404), o processor continua com `order=None`. Descricao usa titulo vazio: `"Venda ML #order_id - "`. Shipping fallback via API tambem e ignorado. Nao e fatal.

### 11.5 Charged Back
- `charged_back` + `status_detail == "reimbursed"` → tratar como approved (ML cobriu)
- `charged_back` sem reimbursed → tratar como refunded (receita + estorno)
- `transaction_amount_refunded` pode ser 0 em chargebacks → fallback para `amount`

### 11.5b Refund by_admin (Kit Split)

Quando o ML separa um pacote em etiquetas diferentes, o payment original e cancelado com
`status=refunded`, `status_detail=by_admin`. Novos payments sao criados para cada pacote split.

**Comportamento ML:**
- ML cancela payment original (refunded/by_admin)
- ML cria 2+ payments novos (approved) com novos pack_ids
- Pack_ids novos sao sequenciais ao original (ex: ...577151 → ...577155)
- Valor total dos splits = valor original
- ML dashboard NAO conta o original como "venda" se o split ocorreu no mesmo dia
- O by_admin pode resultar em 2 pacotes menores (split real) OU 1 pacote com mesmo valor (reagrupamento)

**Regra no processor:**
- `by_admin` + payment NAO synced → **SKIP** (backfill: novos payments cobrem a receita)
- `by_admin` + payment JA synced → **processar como refund normal** (webhook: precisa estornar receita existente)
- Status no Supabase: `skipped_non_sale` quando skip

**Por que NAO processar by_admin como refund normal:**
1. Infla receita bruta no DRE (receita + estorno = net zero, mas brutos inflados)
2. Infla devolucoes no DRE (categoria 1.2.1 cresce desnecessariamente)
3. Diverge do painel ML que exclui by_admin da contagem de vendas

**Exemplo real (easy-utilidades, fev 2026):**
- Pack 2000011402350827: Filtro Iveco R$519,80 → split em 2x R$259,90 (packs ...9506463 e ...9506461)
- Pack 2000011463574971: Sombrinha R$88,56 → split em 2x R$44,28 (packs ...836317 e ...836319)
- Pack 2000011463577151: Sombrinha R$88,56 → reagrupado em pack ...577155 (mesmo R$88,56)
- Pack 2000011512599281: Refil Filtro R$290,94 → split em 2un+1un R$96,98 (packs ...573285 e ...573287)

**Impacto medido:** R$987,86 de inflacao no DRE da easy-utilidades (7 by_admin em 12 dias).
Apos correcao, receita bruta alinha com painel ML (R$78.234 vs R$79.222 anterior).

### 11.6 CA API v2
- Respostas sao **async**: retornam `{"protocolo": "...", "status": "PENDING"}`, NAO `{"id": "..."}`
- Busca de parcelas e **GET** com params (nao POST com body)
- **Obrigatorio** incluir `valor_liquido` em `detalhe_valor` (senao 400)

### 11.6b Baixas: POR QUE Separadas do Processor
A CA API retorna 400 se `data_pagamento > hoje` ("A data do pagamento deve ser igual ou anterior a data atual"). Quando `money_release_date` e futuro, a baixa NAO pode ser feita na hora de criar a receita/despesa. Por isso despesas/receitas sao criadas SEM baixa (ficam EM_ABERTO) e o scheduler diario `/baixas/processar/{seller}` cria baixas so para parcelas com vencimento <= hoje.

### 11.7 CA OAuth2 / Cognito
- **Token rotation habilitado** no user pool do CA
- **DEVE usar** `https://auth.contaazul.com/oauth2/token` (NAO o endpoint direto do Cognito IDP)
- OAuth2 endpoint retorna NOVO refresh token a cada refresh → tokens vivem indefinidamente se renovados
- Background refresh a cada 30 min mantem tokens vivos

### 11.8 ML CSV vs API
- CSV do ML usa `pack_id` como "N. de venda", **NAO** `order_id` da payments API

### 11.8b Fonte de Extrato (Account Statement)
- Para conciliacao de caixa, usar **account_statement** via:
  - `GET /v1/account/release_report/list` + `GET /v1/account/release_report/{file_name}`
  - (alias equivalente em algumas contas: `bank_report`)
- Arquivo normalmente vem como `reserve-release-...csv`.
- `settlement_report` nao deve ser usado como fonte primaria para fechamento diario de caixa.

---

## 11.9 Exemplo Numerico Completo (venda real)
```
Payment 144370799868 (approved):
  transaction_amount:      284.74  (valor bruto)
  net_received_amount:     235.85
  shipping (collector):     23.45  (charges_details type=shipping, from=collector)
  comissao = 284.74 - 235.85 - 23.45 = 25.44

  → Receita CA:   R$284.74  (contas-a-receber, cat 1.1.1, venc=money_release_date)
  → Comissao CA:  R$ 25.44  (contas-a-pagar, cat 2.8.2)
  → Frete CA:     R$ 23.45  (contas-a-pagar, cat 2.9.4)
  → Baixas:       criadas pelo scheduler quando money_release_date <= hoje
```

---

## 11.10 Historico de Correcoes (guardrails)

Bugs ja corrigidos — NAO reintroduzir:

| # | Bug | Correcao |
|---|-----|----------|
| 1 | `buscar_parcelas_pagar` usava POST | Corrigido para GET com params |
| 3 | `in_mediation` nao era processado | Adicionado ao branch de approved |
| 4 | Payments sem order_id processados | Filtro: skip se sem order_id (non-sale) |
| 5 | `_process_refunded` reprocessava | Check: se existing status=refunded → skip |
| 7 | Refund de payment nunca synced | `_process_refunded` cria receita original primeiro |
| 8 | Estorno > transaction_amount | `estorno = min(refunded, amount)` |
| 9 | Chamadas CA diretas sem rate limit | Migrado para ca_queue + rate_limiter global |
| 10a | Competencia usava date_created | Corrigido para `_to_brt_date(date_approved)` — alinha com XLSX ML. PIX/boleto com delay ficam no dia correto |
| 10b | marketplace_shipment processado | Filtro: skip se description="marketplace_shipment" |
| 11 | charged_back nao tratado | Branch: reimbursed→approved, outros→refunded |
| 12 | charged_back refund=0, estorno zerado | Fallback: `refunded or amount` |
| 13 | charged_back+reimbursed gerava estorno | Check: reimbursed → tratar como approved, sem estorno |
| 14 | by_admin inflava DRE (receita+estorno desnecessarios) | Skip se by_admin + nao synced. Novos payments split cobrem a receita |

### 11.11 Classificacao de Pagamentos Non-Order (V3)

Payments sem `order_id` sao classificados pelo `expense_classifier.py`:

**Arvore de decisao:**

| Condicao | Tipo | Direcao | Categoria | Auto |
|----------|------|---------|-----------|------|
| `partition_transfer` + `am-to-pot` | `savings_pot` | transfer | - | Nao |
| `partition_transfer` (outros) | SKIP | - | - | - |
| `payment_addition` | SKIP | - | - | - |
| `money_transfer` + Cashback | `cashback` | income | 1.3.4 | Sim |
| `money_transfer` + Intra MP | `transfer_intra` | transfer | - | Nao |
| `money_transfer` + outro | `transfer_pix` | transfer | - | Nao |
| Bill Payment + DARF | `darf` | expense | 2.2.7 | Sim |
| Bill Payment (outros) | `bill_payment` | expense | - | Nao |
| Virtual + Claude/Anthropic | `subscription` | expense | 2.6.5 | Sim |
| Virtual + Supabase | `subscription` | expense | 2.6.4 | Sim |
| Virtual + Notion | `subscription` | expense | 2.6.1 | Sim |
| Virtual (outros) | `subscription` | expense | 2.6.1 | Sim |
| Collections | `collection` | expense | 2.8.2 | Sim |
| PIX sem branch | `deposit` | transfer | - | Nao |
| Nenhum match | `other` | expense | - | Nao |

**Auto-rules extensiveis** em `AUTO_RULES` no topo de `expense_classifier.py`.

### 11.12 XLSX Export (Despesas MP)

`GET /expenses/{seller}/export` retorna ZIP com XLSX organizados por dia:

```
EMPRESA/
├── 2026-02-15/
│   ├── PAGAMENTO_CONTAS.xlsx    # expense + income
│   └── TRANSFERENCIAS.xlsx       # transfer
├── manifest.csv
└── manifest_pagamentos.csv
```

**PAGAMENTO_CONTAS.xlsx:** boletos, DARF, SaaS, cobrancas, cashback (direction=expense|income)
**TRANSFERENCIAS.xlsx:** PIX, transferencias intra MP (direction=transfer)

| Coluna | Descricao |
|--------|-----------|
| Data de Competencia | `date_approved` em BRT (DD/MM/YYYY) |
| Data de Vencimento | igual competencia |
| Data de Pagamento | igual competencia |
| Valor | negativo (despesas/transfer), positivo (receitas) |
| Categoria | preenchida se auto, vazia se manual |
| Descricao | template por tipo |
| Cliente/Fornecedor | "MERCADO PAGO" (despesas) / "MERCADO LIVRE" (receitas) |
| CNPJ/CPF | 10573521000191 (MP) / 03007331000141 (ML) |
| Centro de Custo | `seller.dashboard_empresa` |
| Observacoes | payment_id + external_reference + notas |

### 11.13 Competencia de Devolucoes: DRE vs Painel ML

O painel ML e nosso DRE usam **criterios de competencia diferentes** para devolucoes, gerando divergencia esperada.

**Painel ML:** conta TODAS as devolucoes de vendas do mes, **independente de quando o estorno ocorreu**.
Exemplo: venda aprovada em janeiro, devolvida em fevereiro → ML conta como devolucao de janeiro.

**Nosso DRE:** conta devolucoes pela **data do estorno** (`date_last_updated` do refund em BRT).
Exemplo: venda aprovada em janeiro, devolvida em fevereiro → estorno entra no DRE de fevereiro.

**Consequencia:** nosso DRE de um mes mostra MENOS devolucoes que o painel ML, porque parte dos estornos so ocorre no mes seguinte.

**Formula de reconciliacao:**
```
Painel ML (devol+cancel de vendas jan) ≈ DRE jan (estornos em jan) + DRE fev (estornos em fev de vendas jan) + by_admin
```

**Referencia Janeiro 2026 (validado 2026-02-20):**

| Seller | Estorno total | + by_admin (≈ ML) | DRE jan | Diferido p/ DRE fev |
|--------|-------------:|------------------:|--------:|--------------------:|
| 141AIR | R$ 42.687 | R$ 43.043 | R$ 32.900 | R$ 9.787 |
| NET-AIR | R$ 155.239 | R$ 159.991 | R$ 93.136 | R$ 62.103 |
| NETPARTS-SP | R$ 107.485 | R$ 108.672 | R$ 65.334 | R$ 42.151 |
| EASY-UTIL | R$ 14.609 | R$ 15.029 | R$ 10.528 | R$ 4.081 |

**Notas:**
- `by_admin` (kit split) e contado pelo ML como devolucao, mas nos pulamos (novos payments split cobrem a receita — ver 11.5b)
- `cancelled`/`rejected` NAO entram como devolucao em nenhum dos dois (nunca foram vendas aprovadas)
- Diferenca residual (< R$ 200 por seller) vem de by_admin parciais e arredondamentos
- Este comportamento e **correto e intencional** — nao e bug

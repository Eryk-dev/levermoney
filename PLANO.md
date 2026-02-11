# API Conciliador V2 - Plano de Desenvolvimento

**Versão do plano:** 1.6
**Última atualização:** 2026-02-11

---

## 1. Problema Central

O vendedor do Mercado Livre enfrenta:

1. **Sem controle de prazos** - Dinheiro fica retido por dias/semanas sem visibilidade
2. **Cancelamentos invisíveis** - Devoluções, chargebacks e reclamações impactam o saldo sem rastreio claro
3. **Zero previsibilidade** - Não sabe quanto vai receber nem quando
4. **Competência errada** - Receita registrada na data da liberação, não da venda
5. **Trabalho manual** - Baixar 5 CSVs, processar, importar no Conta Azul
6. **Precisão limitada** - CSV tem tolerância de R$ 0,10, difícil rastrear venda a venda

---

## 2. Solução: Sincronização ML/MP <-> Conta Azul via APIs

### Conceito

Integração **100% automática** e **em tempo real** entre Mercado Livre/Mercado Pago e Conta Azul, usando as APIs oficiais de ambos os lados. Sem CSV, sem importação manual, sem esforço humano.

### Conta de Trânsito "MP Retido"

Para cada empresa, criar 2 contas no Conta Azul que representam o fluxo real do dinheiro:

```
VENDA                    LIBERAÇÃO                  SAQUE
  │                          │                        │
  ▼                          ▼                        ▼
┌──────────┐  liberação  ┌──────────┐   PIX/TED   ┌──────────┐
│ MP RETIDO│────────────▶│MP DISPON.│────────────▶│  BANCO   │
│ (virtual)│             │ (real)   │             │  (real)  │
└──────────┘             └──────────┘             └──────────┘
  Saldo =                  Saldo =                  Saldo =
  quanto falta             quanto tem               quanto tem
  liberar                  no MP                    no banco
```

| Conta no Conta Azul | O que representa |
|---------------------|------------------|
| **MP Retido - [Empresa]** | Dinheiro retido pelo ML aguardando liberação |
| **Mercado Pago - [Empresa]** | Saldo disponível na conta MP |
| **Banco - [Empresa]** | Saldo no banco (já existe) |

---

## 3. Lançamentos Contábeis por Evento

### 3.1 Venda aprovada

Competência = **data da venda**. Conta = **MP Retido**.

```
RECEITA:
  Conta: MP Retido
  Categoria: 1.1.1 MercadoLibre (ou 1.1.2 Loja, 1.1.5 Balcão)
  Valor: +R$ 100,00 (transaction_amount EXATO da API)

COMISSÃO:
  Conta: MP Retido
  Categoria: 2.8.2 Comissões de Marketplace
  Valor: -R$ 12,00 (fee_details EXATO da API)

FRETE (se vendedor paga):
  Conta: MP Retido
  Categoria: 2.9.4 MercadoEnvios
  Valor: -R$ 6,00 (shipping_fee EXATO da API)

→ Saldo MP Retido: +R$ 82,00
```

### 3.2 Dinheiro liberado

Data = **money_release_date**. Transferência entre contas (não é receita/despesa).

```
TRANSFERÊNCIA:
  DE: MP Retido
  PARA: MP Disponível
  Valor: R$ 82,00 (net_received_amount EXATO)

→ MP Retido: -R$ 82,00
→ MP Disponível: +R$ 82,00
```

### 3.3 Cancelamento / Devolução

Competência = **data do cancelamento**.

```
ESTORNO RECEITA:
  Conta: MP Retido
  Categoria: 1.2.1 Devoluções e Cancelamentos
  Valor: -R$ 100,00

ESTORNO COMISSÃO:
  Conta: MP Retido
  Categoria: 1.3.4 Estornos de Taxas
  Valor: +R$ 12,00

ESTORNO FRETE:
  Conta: MP Retido
  Categoria: 1.3.7 Estorno de Frete
  Valor: +R$ 6,00

→ MP Retido: -R$ 82,00 (zera a operação)
```

### 3.4 Saque (MP para Banco)

```
TRANSFERÊNCIA:
  DE: MP Disponível
  PARA: Banco
  Valor: R$ 82,00
  Data: data do PIX/TED
```

### 3.5 Pagamento de contas (settlement negativo)

O MP permite pagar boletos/contas usando saldo disponível. Isso aparece como `SETTLEMENT` com valor **negativo** nos relatórios. A API retorna como um pagamento com `operation_type = "regular_payment"` e `transaction_amount` negativo.

**Competência = data do pagamento da conta.**

```
DESPESA:
  Conta: MP Disponível
  Categoria: (categoria da despesa correspondente - ex: 2.3.1 Fornecedores)
  Valor: -R$ 500,00 (saída do saldo MP)
  Descrição: "Pgto conta via MP - Boleto XYZ"

→ MP Disponível: -R$ 500,00 (saldo diminui)
```

**Na prática:** O dinheiro sai de "MP Disponível" sem ir pro banco. É como se fosse um pagamento direto pelo MP.

**Fluxo contábil:**
```
MP Disponível (R$ 5.000) → Pagamento conta R$ 500 → MP Disponível (R$ 4.500)
                                                       └─ Conta a Pagar "Fornecedor X" = PAGO
```

### 3.6 Antecipação de recebíveis

O vendedor pode solicitar antecipação de valores ainda retidos pelo ML/MP, recebendo antes da `money_release_date` original. O MP cobra uma **taxa de antecipação** sobre o valor antecipado.

**Competência = data da antecipação solicitada.**

```
ANTECIPAÇÃO:
  Transferência: MP Retido → MP Disponível
  Valor: R$ 82,00 (valor líquido original)
  Data: data da antecipação (NÃO a money_release_date original)

TAXA DE ANTECIPAÇÃO:
  Conta: MP Disponível
  Categoria: 2.11.9 Antecipação de Recebíveis (já existe no CA)
  Valor: -R$ 3,28 (taxa cobrada pelo MP)

→ MP Retido: -R$ 82,00 (libera antecipadamente)
→ MP Disponível: +R$ 78,72 (valor menos taxa de antecipação)
```

**Detecção via API:** Quando o `money_release_date` de um pagamento é alterado para uma data anterior à original, indica antecipação. A diferença entre `net_received_amount` original e o valor efetivamente recebido é a taxa de antecipação.

**Fluxo contábil:**
```
Antes da antecipação:
  MP Retido: R$ 82,00 (liberação prevista: 22/01)

Após antecipação em 16/01:
  MP Retido: R$ 0,00
  MP Disponível: +R$ 78,72
  Taxa antecipação: -R$ 3,28 (despesa financeira)
```

---

## 4. Arquitetura Técnica

### 4.1 Visão geral

```
┌─────────────────────────────────────────────────────────────────────┐
│                  MERCADO LIVRE / MERCADO PAGO                        │
│                                                                      │
│  Webhooks:                        APIs de consulta:                  │
│  • payment (venda/refund/CB)      • GET /v1/payments/{id}            │
│  • orders_v2 (pedido)             • GET /orders/{id}                 │
│  • shipments (envio)              • GET /shipments/{id}/costs        │
│  • chargebacks (contestação)      • GET /v1/payments/search          │
│  • claims (reclamação)                                               │
└──────────────┬──────────────────────────────────┬────────────────────┘
               │ webhook                          │ consulta
               ▼                                  │
┌──────────────────────────────────────────────────────────────────────┐
│                      API CONCILIADOR V2                               │
│                      (FastAPI + Supabase)                             │
│                                                                      │
│  ETAPA 1: CAPTURA                                                    │
│  • Recebe webhook do ML/MP                                           │
│  • Busca detalhes completos via API (payment + order + shipping)     │
│  • Salva dados brutos no Supabase                                    │
│                                                                      │
│  ETAPA 2: CLASSIFICAÇÃO                                              │
│  • Identifica tipo (venda, refund, chargeback, transferência)        │
│  • Determina origem (ML, Loja Própria, Balcão/POS)                   │
│  • Calcula breakdown (receita, comissão, frete)                      │
│  • Gera lançamentos contábeis                                        │
│                                                                      │
│  ETAPA 3: SINCRONIZAÇÃO COM CONTA AZUL                               │
│  • POST contas-a-receber (receitas) via API                          │
│  • POST contas-a-pagar (despesas) via API                            │
│  • Associa à conta financeira correta (MP Retido / MP Disponível)    │
│  • Marca como sincronizado no Supabase                               │
│                                                                      │
│  ETAPA 4: RECONCILIAÇÃO (periódica)                                  │
│  • Compara ML/MP vs Conta Azul transação a transação                 │
│  • Detecta divergências                                              │
│  • Auto-corrige ou gera alerta                                       │
└──────────────┬───────────────────────────────────┬───────────────────┘
               │ API REST                          │ leitura/escrita
               ▼                                   ▼
┌────────────────────────────┐   ┌─────────────────────────────────────┐
│       CONTA AZUL           │   │          SUPABASE                    │
│       (via API v2)         │   │                                     │
│                            │   │  Tabelas:                           │
│  Contas Financeiras:       │   │  • accounts (empresas/CNPJs)        │
│  • MP Retido - Empresa X   │   │  • ml_payments (dados brutos ML)    │
│  • Mercado Pago - Empresa X│   │  • ml_orders (pedidos + itens)      │
│  • Banco - Empresa X       │   │  • transactions (lançamentos)       │
│                            │   │  • ca_sync_log (log sincronização)  │
│  Lançamentos:              │   │  • reconciliation (conferência)     │
│  • Contas a Receber        │   │  • tokens (ML + CA criptog.)        │
│  • Contas a Pagar          │   │                                     │
│  • Categorias corretas     │   │  Views:                             │
│  • Centros de custo        │   │  • v_saldo_retido                   │
│                            │   │  • v_previsao_liberacoes            │
└────────────────────────────┘   │  • v_divergencias                   │
                                 │  • v_resumo_diario                  │
                                 └─────────────────────────────────────┘
```

### 4.2 Fluxo de uma venda (tempo real)

```
1. Comprador paga no ML
   └─▶ Webhook "payment" chega na API V2
       └─▶ Busca GET /v1/payments/{id}
           ├── money_release_date = 2025-01-22
           ├── transaction_amount = R$ 100,00
           ├── net_received_amount = R$ 82,00
           └── fee_details = [comissão R$ 12, frete R$ 6]
       └─▶ Busca GET /orders/{order_id}
           ├── order_items = ["Produto X"]
           └── shipping.cost = R$ 15,00
       └─▶ Salva no Supabase
       └─▶ POST no Conta Azul:
           ├── contas-a-receber: +R$ 100 (1.1.1 MercadoLibre)
           ├── contas-a-pagar: -R$ 12 (2.8.2 Comissões)
           └── contas-a-pagar: -R$ 6 (2.9.4 MercadoEnvios)
       └─▶ Tudo na conta "MP Retido"

2. Produto entregue
   └─▶ Webhook "shipments"
       └─▶ Atualiza status no Supabase

3. Dinheiro liberado (money_release_date)
   └─▶ Webhook "payment" (status atualizado)
       └─▶ Gera transferência: MP Retido → MP Disponível

4. Cancelamento/devolução
   └─▶ Webhook "payment" (status = "refunded")
       └─▶ Gera estornos na MP Retido via API Conta Azul
```

---

## 5. APIs Utilizadas

### 5.1 Mercado Livre / Mercado Pago (ENTRADA)

| API | Endpoint | Dados obtidos |
|-----|----------|---------------|
| **Pagamento** | `GET /v1/payments/{id}` | Valor, taxas, líquido, money_release_date, status |
| **Busca pagamentos** | `GET /v1/payments/search` | Filtro por período, status, referência |
| **Pedido** | `GET /orders/{id}` | Itens, frete, comprador, status do pedido |
| **Envio** | `GET /shipments/{id}` | Status de entrega, tracking |
| **Custo frete** | `GET /shipments/{id}/costs` | Breakdown: quem pagou quanto |
| **Histórico envio** | `GET /shipments/{id}/history` | Linha do tempo do envio |

**Webhooks recebidos:**

| Topic | Quando dispara | Ação no sistema |
|-------|---------------|-----------------|
| `payment` | Venda aprovada, refund, chargeback | Cria/atualiza lançamentos |
| `orders_v2` | Pedido atualizado | Atualiza dados do pedido |
| `shipments` | Envio/entrega | Atualiza status de entrega |
| `chargebacks` | Contestação aberta/resolvida | Cria lançamento de chargeback |
| `topic_claims_integration_wh` | Reclamação/mediação | Alerta + bloqueia liberação |

**Campos-chave do payment:**

```json
{
  "id": 12345678901,
  "status": "approved",
  "date_created": "2025-01-15T10:30:00.000Z",
  "date_approved": "2025-01-15T10:30:05.000Z",
  "money_release_date": "2025-01-22T10:30:00.000Z",
  "transaction_amount": 100.00,
  "fee_details": [...],
  "transaction_details": {
    "net_received_amount": 82.00,
    "total_paid_amount": 115.00,
    "installment_amount": 115.00
  },
  "payment_method_id": "pix",
  "external_reference": "MLB123456789"
}
```

### 5.2 Conta Azul (SAIDA)

Base URL: `https://api-v2.contaazul.com`

| API | Endpoint | Uso |
|-----|----------|-----|
| **Contas a Receber** | `POST /v1/financeiro/eventos-financeiros/contas-a-receber` | Criar receitas |
| **Contas a Pagar** | `POST /v1/financeiro/eventos-financeiros/contas-a-pagar` | Criar despesas |
| **Contas Financeiras** | `GET /v1/conta-financeira` | Listar contas (MP Retido, MP Disp.) |
| **Centros de Custo** | `GET/POST /v1/centro-de-custo` | Listar/criar centros de custo |

**Exemplo de lançamento (contas a receber):**

```json
{
  "data_competencia": "2025-01-15",
  "valor": 100.00,
  "descricao": "Venda ML #ORDER_ID - Produto X",
  "observacao": "Payment ID: 12345678901 | Liberação: 2025-01-22",
  "contato": "UUID_MERCADO_LIVRE",
  "conta_financeira": "UUID_CONTA_MP_RETIDO",
  "rateio": [
    {
      "id_categoria": "UUID_CAT_1.1.1_MERCADOLIBRE",
      "valor": 100.00,
      "rateio_centro_custo": [
        {
          "id_centro_custo": "UUID_CENTRO_CUSTO",
          "valor": 100.00
        }
      ]
    }
  ],
  "condicao_pagamento": {
    "parcelas": [
      {
        "descricao": "Venda ML #ORDER_ID",
        "data_vencimento": "2025-01-22",
        "conta_financeira": "UUID_CONTA_MP_RETIDO",
        "detalhe_valor": {
          "valor_bruto": 100.00,
          "valor_liquido": 100.00,
          "taxa": 0,
          "desconto": 0
        }
      }
    ]
  }
}
```

**Autenticação Conta Azul:**
- OAuth2 com Bearer Token
- Refresh Token automático
- Rate limit: 600 req/min, 10 req/seg por conta ERP

### 5.3 MCP Server Conta Azul (Desenvolvimento/Testes)

Existe um MCP Server local (`/Volumes/SSD Eryk/financeiro/contaazul-mcp-server/`) que permite interagir com a API do Conta Azul diretamente via Claude Code. Útil para explorar dados, testar endpoints e validar estrutura antes de codificar.

**24 tools disponíveis:**

| Módulo | Tool | Endpoint |
|--------|------|----------|
| **Auth** | `contaazul_auth_status` | Verifica status do token |
| | `contaazul_renovar_token` | Renova access_token via refresh_token |
| | `contaazul_trocar_code` | Troca authorization_code por tokens |
| **Financeiro** | `contaazul_listar_centros_custo` | GET centros de custo (paginado, filtros) |
| | `contaazul_criar_centro_custo` | POST novo centro de custo |
| | `contaazul_listar_parcelas_evento` | GET parcelas de um evento financeiro |
| | `contaazul_buscar_parcela` | GET parcela por ID |
| | `contaazul_atualizar_parcela` | PATCH parcela (vencimento, valor, conta) |
| | `contaazul_listar_categorias` | GET categorias (receita/despesa, paginado) |
| | `contaazul_listar_categorias_dre` | GET categorias DRE |
| | `contaazul_listar_contas_financeiras` | GET contas (corrente, cartão, poupança, etc.) |
| | `contaazul_buscar_saldo_conta` | GET saldo de uma conta financeira |
| | `contaazul_criar_conta_receber` | POST evento de conta a receber |
| | `contaazul_buscar_contas_receber` | GET parcelas de receitas (filtros por data/status) |
| | `contaazul_criar_conta_pagar` | POST evento de conta a pagar |
| | `contaazul_buscar_contas_pagar` | GET parcelas de despesas (filtros por data/status) |
| **Baixas** | `contaazul_criar_baixa` | POST baixa (pagamento) em parcela |
| | `contaazul_listar_baixas` | GET baixas de uma parcela |
| | `contaazul_buscar_baixa` | GET baixa por ID |
| | `contaazul_atualizar_baixa` | PATCH baixa (valor, data, conta) |
| | `contaazul_deletar_baixa` | DELETE baixa |
| **Cobranças** | `contaazul_criar_cobranca` | POST cobrança (boleto/PIX/link) |
| | `contaazul_buscar_cobranca` | GET cobrança por ID |
| | `contaazul_deletar_cobranca` | DELETE cobrança |

**Endpoints da API Conta Azul descobertos via MCP:**

```
Base: https://api-v2.contaazul.com

Financeiro:
  POST /v1/financeiro/eventos-financeiros/contas-a-receber
  POST /v1/financeiro/eventos-financeiros/contas-a-pagar
  GET  /v1/financeiro/eventos-financeiros/contas-a-receber/parcelas (busca com filtros)
  GET  /v1/financeiro/eventos-financeiros/contas-a-pagar/parcelas (busca com filtros)
  GET  /v1/financeiro/eventos-financeiros/parcelas/{id}
  PATCH /v1/financeiro/eventos-financeiros/parcelas/{id}

Baixas:
  POST   /v1/financeiro/eventos-financeiros/parcelas/{id}/baixa
  GET    /v1/financeiro/eventos-financeiros/parcelas/{id}/baixa
  GET    /v1/financeiro/eventos-financeiros/parcelas/baixa/{id}
  PATCH  /v1/financeiro/eventos-financeiros/parcelas/baixa/{id}
  DELETE /v1/financeiro/eventos-financeiros/parcelas/baixa/{id}

Cobranças:
  POST   /v1/financeiro/eventos-financeiros/contas-a-receber/gerar-cobranca
  GET    /v1/financeiro/eventos-financeiros/contas-a-receber/cobranca/{id}
  DELETE /v1/financeiro/eventos-financeiros/contas-a-receber/cobranca/{id}

Categorias:
  GET /v1/categorias (paginado, filtros tipo/busca)
  GET /v1/categorias-dre

Contas Financeiras:
  GET /v1/conta-financeira (paginado, filtros tipo/nome/status)
  GET /v1/conta-financeira/{id}/saldo

Centros de Custo:
  GET  /v1/centro-de-custo (paginado, filtros)
  POST /v1/centro-de-custo
```

---

## 6. Garantia de 100% de Precisão

### Camada 1: Captura individual (valores exatos)

Cada pagamento capturado via API com campos exatos:
- `transaction_amount` = bruto exato
- `fee_details` = taxas exatas
- `net_received_amount` = líquido exato
- `money_release_date` = data exata

Sem arredondamento. Sem estimativa. Valores EXATOS do ML/MP.

### Camada 2: Sincronização atômica com Conta Azul

Cada transação gera lançamentos via API com valores exatos:

| Evento | Ação no Conta Azul | Valor |
|--------|-------------------|-------|
| Venda aprovada | POST contas-a-receber | `transaction_amount` (exato) |
| Comissão | POST contas-a-pagar | `fee_details.amount` (exato) |
| Frete vendedor | POST contas-a-pagar | `shipping_fee` (exato) |
| Devolução | POST contas-a-pagar | `refund_amount` (exato) |
| Estorno taxa | POST contas-a-receber | `fee_refund` (exato) |
| Liberação | Transferência entre contas | `net_received` (exato) |
| Pgto conta via MP | POST contas-a-pagar + baixa | valor do boleto/conta (exato) |
| Antecipação | Transferência + POST contas-a-pagar | `net_received` + taxa antecipação |

### Camada 3: Reconciliação automática

Periodicamente (diária ou semanal):

```
1. Puxa TODOS os pagamentos do ML/MP via GET /v1/payments/search
2. Puxa TODOS os lançamentos do Conta Azul via API
3. Cruza payment_id <-> lançamento no Conta Azul
4. Para cada transação verifica:
   ├── Valor bate?
   ├── Categoria correta?
   ├── Conta financeira correta?
   └── Data de competência correta?
5. Resultado:
   ├── OK: 347 transações sincronizadas
   ├── ALERTA: 2 divergências encontradas
   └── ERRO: 0 faltando no Conta Azul
```

---

## 7. Stack Tecnológico

| Componente | Tecnologia | Justificativa |
|-----------|------------|---------------|
| **Backend** | FastAPI (Python) | Mesma stack da V1, performance, async |
| **Banco de dados** | Supabase (PostgreSQL) | Já no plano de contas, views, auth |
| **Hospedagem** | VPS/Cloud | Servidor sempre online para webhooks |
| **Autenticação ML** | OAuth2 | Padrão do ML/MP |
| **Autenticação CA** | OAuth2 | Padrão do Conta Azul |
| **Fila de eventos** | Supabase Realtime ou Redis | Para processar webhooks sem perder |
| **Cron/Scheduler** | APScheduler ou pg_cron | Para reconciliação periódica |

---

## 8. Banco de Dados (Supabase)

### Tabelas principais

```sql
-- Empresas configuradas
accounts (
  id, nome, cnpj,
  ml_seller_id, ml_access_token, ml_refresh_token,
  ca_access_token, ca_refresh_token,
  ca_conta_retido_uuid, ca_conta_disponivel_uuid,
  ca_contato_ml_uuid, ca_centro_custo_uuid,
  created_at, updated_at
)

-- Pagamentos do ML/MP (dados brutos)
ml_payments (
  id, account_id, payment_id,
  status, date_created, date_approved, money_release_date,
  transaction_amount, net_received_amount,
  fee_amount, shipping_fee, financing_fee,
  payment_method, external_reference, order_id,
  raw_json, created_at, updated_at
)

-- Pedidos do ML
ml_orders (
  id, account_id, order_id,
  status, items_json, shipping_id, shipping_cost, shipping_status,
  buyer_nickname, raw_json,
  created_at, updated_at
)

-- Lançamentos contábeis gerados
transactions (
  id, account_id, payment_id,
  tipo (receita/despesa/transferencia),
  categoria, valor, data_competencia, data_pagamento,
  conta_financeira (retido/disponivel),
  descricao, observacao,
  ca_synced (boolean), ca_sync_id, ca_synced_at,
  created_at
)

-- Log de sincronização com Conta Azul
ca_sync_log (
  id, transaction_id, account_id,
  endpoint, request_json, response_json,
  status (success/error), error_message,
  created_at
)

-- Reconciliação
reconciliation (
  id, account_id, periodo_inicio, periodo_fim,
  total_ml, total_ca, diferenca,
  transacoes_ok, transacoes_divergentes, transacoes_faltantes,
  detalhes_json,
  created_at
)
```

### Views

```sql
-- Saldo retido por empresa
v_saldo_retido AS
  SELECT account_id, SUM(valor) as saldo
  FROM transactions
  WHERE conta_financeira = 'retido' AND tipo != 'transferencia'
  GROUP BY account_id

-- Previsão de liberações futuras
v_previsao_liberacoes AS
  SELECT account_id, money_release_date, payment_id,
         net_received_amount, status
  FROM ml_payments
  WHERE status = 'approved'
    AND money_release_date > NOW()
  ORDER BY money_release_date

-- Divergências
v_divergencias AS
  SELECT * FROM transactions
  WHERE ca_synced = false
     OR id IN (SELECT transaction_id FROM ca_sync_log WHERE status = 'error')
```

---

## 9. Implementação - Roadmap

### Decisões tomadas (2026-02-11):
- **Hosting:** VPS própria do Eryk (já tem servidor com IP fixo)
- **Banco:** Supabase (já usa)
- **ML App:** Precisa criar no developers.mercadolivre.com.br
- **Seller piloto:** 141AIR
- **Conta Azul:** OAuth já funciona via MCP server

### SPRINT 1 - MVP Funcional (2026-02-11)

**Objetivo:** 141AIR recebendo webhooks, processando vendas e lançando no Conta Azul.

#### Bloco A - Setup (manual, Eryk)
- [ ] Criar App no ML Developers (https://developers.mercadolivre.com.br)
  - Redirect URI: `https://{VPS_DOMAIN}/auth/ml/callback`
  - Topics: payments, orders_v2, shipments, claims
  - Anotar: APP_ID e SECRET_KEY
- [ ] Criar conta "MP Retido - 141AIR" no Conta Azul (Financeiro > Contas > Nova > tipo OUTROS)
  - Anotar o UUID gerado
- [ ] Configurar DNS/HTTPS no VPS (se ainda não tem)

#### Bloco B - Projeto FastAPI + Supabase (dev)
- [ ] Criar projeto FastAPI com estrutura:
  ```
  apiconciliador-v2/
  ├── app/
  │   ├── main.py              # FastAPI app
  │   ├── config.py            # Settings/env vars
  │   ├── routers/
  │   │   ├── webhooks.py      # POST /webhooks/{seller}
  │   │   ├── auth_ml.py       # OAuth ML
  │   │   └── health.py        # GET /health
  │   ├── services/
  │   │   ├── ml_api.py        # Client ML/MP APIs
  │   │   ├── ca_api.py        # Client Conta Azul API
  │   │   └── processor.py     # Lógica de processamento
  │   ├── models/
  │   │   └── sellers.py       # Config por seller
  │   └── db/
  │       └── supabase.py      # Client Supabase
  ├── .env                     # Secrets
  ├── requirements.txt
  └── Dockerfile
  ```
- [ ] Migration Supabase: tabelas sellers, payments, sync_log
- [ ] OAuth2 ML: /auth/ml/connect, /auth/ml/callback, refresh automático
- [ ] Webhook receiver: POST /webhooks/{seller_slug}
  - Validar assinatura HMAC
  - Responder <500ms
  - Salvar raw no Supabase
  - Processar async

#### Bloco C - Pipeline Venda → Conta Azul (dev)
- [ ] Ao receber webhook payment (status=approved):
  1. GET /v1/payments/{id} → valores, taxas, money_release_date
  2. GET /orders/{order_id} → itens, descrição
  3. GET /shipments/{id}/costs → frete vendedor
  4. Salvar no Supabase (ml_payments)
  5. POST contas-a-receber no CA (receita bruta)
  6. POST contas-a-pagar no CA (comissão + frete)
  7. Marcar como sincronizado
- [ ] Ao receber webhook payment (status=refunded):
  1. Criar estornos no CA
- [ ] Idempotência: não duplicar se payment_id já existe

#### Bloco D - Conectar 141AIR (Eryk + dev)
- [ ] Eryk autoriza 141AIR no app ML via OAuth
- [ ] Configurar webhooks do ML apontando para VPS
- [ ] Testar com venda real
- [ ] Verificar lançamento aparecendo no Conta Azul

### SPRINT 2 - Liberações + Estornos (próximos dias)

- [ ] Cron diário: Released Money Report → detectar liberações
- [ ] Transferência MP Retido → MP Disponível no CA
- [ ] Detectar saques → transferência MP Disponível → Banco
- [ ] Chargebacks e claims (alertas)
- [ ] Antecipação de recebíveis

### SPRINT 3 - Todos os Sellers (semana seguinte)

- [ ] Conectar os 7 sellers restantes via OAuth
- [ ] Criar as 7 contas MP Retido restantes no CA
- [ ] Testar cada seller individualmente

### SPRINT 4 - Reconciliação + Dashboard

- [ ] Cron semanal: cruzar ML/MP vs CA
- [ ] Relatório de divergências
- [ ] Dashboard básico de status
- [ ] Alertas por email/webhook

---

## 10. Comparação V1 vs V2

| Aspecto | V1 (CSV atual) | V2 (API + API) |
|---------|----------------|----------------|
| **Fonte de dados** | 5 CSVs baixados manualmente | APIs em tempo real (webhook) |
| **Destino** | Arquivos XLSX para importar | API do Conta Azul direto |
| **Precisão** | ~99% (tolerância R$ 0,10) | **100%** (valores exatos) |
| **Frequência** | Mensal/semanal | **Tempo real** |
| **Esforço humano** | Baixar 5 CSVs + importar | **Zero** |
| **Reconciliação** | Manual | **Automática** |
| **Previsibilidade** | Limitada | **Total** (money_release_date) |
| **Rastreio venda a venda** | Difícil (CSVs consolidados) | **Individual** (payment_id) |
| **Cancelamentos** | Só descobre depois | **Instantâneo** (webhook) |
| **Infraestrutura** | API stateless, sem banco | Servidor + Supabase |

---

## 11. Requisitos de Infraestrutura

### APIs externas

| Serviço | Requisito |
|---------|-----------|
| **ML/MP** | App registrada + OAuth2 + Access Token por CNPJ |
| **Conta Azul** | App no Portal de Desenvolvedores + OAuth2 + Plano com API |

### Servidor

| Requisito | Motivo |
|-----------|--------|
| Sempre online | Receber webhooks 24/7 |
| HTTPS com certificado | ML exige webhook HTTPS |
| IP fixo ou domínio | Configurar URL do webhook |

### Conta Azul

| Requisito | Motivo |
|-----------|--------|
| Plano que suporta API | Nem todos os planos têm acesso |
| Contas "MP Retido" criadas | Uma por empresa/CNPJ |
| Categorias mapeadas | UUIDs das categorias do plano de contas |
| Contato "MERCADO LIVRE" criado | UUID para associar lançamentos |

---

## 12. Mapeamento Real do Conta Azul (extraído via API em 2026-02-11)

### 12.1 Centros de Custo (21 ativos)

Cada empresa tem 2 centros: VARIÁVEL (custos ligados à venda) e FIXO (custos fixos).

| Código | Nome | ID |
|--------|------|----|
| CC001.1 | NETAIR - VARIÁVEL | `ea62b7c0-be2f-11f0-b53b-c7780e8df70d` |
| CC001.2 | NETAIR - FIXO | `c6154906-f78b-11f0-8a55-a7d4cde47b3d` |
| CC002.1 | NETPARTS - VARIÁVEL | `f2157c8c-be2f-11f0-a5d1-1720678d9ade` |
| CC002.2 | NETPARTS - FIXO | `03277ef4-f78c-11f0-8753-2b9bd3fd63e1` |
| CC003.1 | 141AIR - VARIÁVEL | `f7c214a6-be2f-11f0-8080-ab23c683d2a1` |
| CC003.2 | 141AIR - FIXO | `4d48054e-f78c-11f0-8f17-5ff741445e86` |
| CC004.1 | EASYPEASY - VARIÁVEL | `fe22bdbe-be2f-11f0-b3a4-432a9ff21e7c` |
| CC004.2 | EASYPEASY - FIXO | `6dc7bb5c-f78c-11f0-a7a2-c78ab116c047` |
| CC005.1 | UNIQUE - VARIÁVEL | `04ddd1de-be30-11f0-818a-c3572b802fb1` |
| CC005.2 | UNIQUE - FIXO | `860e8a10-f78c-11f0-b44a-8f2fdcca866d` |
| CC006.1 | BELLATOR - VARIÁVEL | `0a76ea72-be30-11f0-8cde-2fd643481fb8` |
| CC006.2 | BELLATOR - FIXO | `934151a4-f78c-11f0-8982-0b89fc62071d` |
| CC007.1 | LEVER TALENTS - VARIÁVEL | `0e6a5916-be30-11f0-bf2d-57ebcdeb1fa2` |
| CC007.2 | LEVER TALENTS - FIXO | `a4dcf080-f78c-11f0-8ee5-93cc6499e829` |
| CC008.1 | GRUPO - VARIÁVEL | `194153f8-be30-11f0-9aaf-5b7578694f27` |
| CC008.2 | GRUPO - FIXO | `bf7e882c-f78c-11f0-aee1-3f1d8fcb8536` |
| CC009.1 | VICTOR - VARIÁVEL | `428ad31c-d4f9-11f0-9583-efbbf33ae1cb` |
| CC009.2 | VICTOR - FIXO | `cf9ddda2-f78c-11f0-974e-2375f5da6713` |
| CC010.1 | EASYPEASY SP - VARIÁVEL | `dfe621a6-f179-11f0-9309-2798c0988889` |
| CC010.2 | EASYPEASY SP - FIXO | `e0f88b24-f78c-11f0-930e-d3ca7ab74fa4` |
| - | UNIQUE EU | `99af6286-cb15-11f0-8448-b3a5ea48e821` |

### 12.2 Contas Financeiras - Mercado Pago (ativas)

Contas MP que receberão lançamentos automatizados. Cada uma precisará de uma conta "MP Retido" correspondente.

| Empresa | Conta MP (Disponível) | ID | Tipo |
|---------|----------------------|-----|------|
| **141AIR** | 141AIR - MP | `f0e9908c-2735-4843-8d29-10b8b27f7ff8` | CC |
| | 141AIR - MP - CC | `b05b810d-5b71-4ab5-acaa-bb72d0597b30` | Cartão |
| | 141 - MP LEVER TALENTS | `db9af0d3-2d4a-4510-b34e-1262d8e19bad` | Investimento |
| **BELLATOR** | BELLATOR - MP | `5d9d1e55-a73f-45a7-8672-22b97cbd107f` | CC |
| **EASYPEASY** | EASYPEASY - MP | `c8939e1a-525b-49d1-9dc8-5bd0a4ebb966` | CC |
| | EASYPEASY - MP - CC | `b2d81173-35a5-4da8-9359-4c800c81fdd5` | Cartão |
| | EASYPEASY - MP FILIAL | `56a04907-c2cb-43ab-8719-9568fcec9389` | CC |
| **NETAIR** | NETAIR - MP | `00900d7c-af0d-44fb-8741-0891d458a415` | CC |
| | NETAIR - MP CC | `857b0d0e-5209-4e05-ad96-f5b029df8d6d` | Cartão |
| **NETPARTS** | NETPARTS - MP | `dbe7d162-8d84-40f7-923d-47a741daa4b5` | CC |
| **UNIQUEBOX** | UNIQUEBOX - MP | `b773dcdf-ca7b-43b3-9a9c-85c6b999bcc0` | CC |
| | UNIQUEBOX - MP - CC | `b2700e19-4bb2-4089-a88c-ecabdeea110f` | Cartão |
| **UNIQUE** | UNIQUE - MP | `11d76e3b-3613-4678-b43f-bfbf3c0cbd6e` | CC |
| | UNIQUE - MP \| UNIQUEKIDS | `cf762c11-9913-463a-803c-3fdaaf248bc3` | CC |
| **VICTOR** | VICTOR - MP | `6f92ea09-d6ef-4fc3-8ca2-e6d4eae18df4` | CC |
| | VICTOR - MP COFRINHO | `ed233541-23b6-43f9-b417-11a66c87b72f` | Investimento |

**Conta transitória existente:** TRANSITÓRIA (`00db1dac-ad6d-4635-af52-10f744e43bde`) - tipo OUTROS

> **AÇÃO NECESSÁRIA:** Criar contas "MP Retido - [Empresa]" para cada empresa que vende no ML. São ~8 empresas com conta MP ativa (141AIR, BELLATOR, EASYPEASY, NETAIR, NETPARTS, UNIQUEBOX, UNIQUE, VICTOR).

### 12.3 Contas Financeiras - Bancos (ativas)

| Empresa | Bancos |
|---------|--------|
| **141AIR** | Sicredi (CC + CC cartão + Poupança + Investimento + Cooperativa) |
| **BELLATOR** | Banco do Brasil, Sicredi (CC + Cartão + Cooperativa) |
| **EASYPEASY** | Itaú, Sicredi (CC + Cartão + Poupança + Investimento + Cooperativa) |
| **NETAIR** | Asaas, Banco do Brasil (CC + Cartão + Investimento + OuroCap), Itaú, Sicredi (CC + Cartão + Poupança + Investimento + Cooperativa) |
| **UNIQUE** | Asaas, Banco do Brasil (CC + Cartão), C6 (CC + Cartão), Santander, Sicredi (CC + Cartão + Investimento) |
| **VICTOR** | (apenas MP) |

### 12.4 Categorias Relevantes para V2 (com UUIDs)

#### RECEITAS usadas no fluxo ML/MP:

| Código | Nome | ID | Uso no V2 |
|--------|------|----|-----------|
| 1.1.1 | MercadoLibre | `78f42170-23f7-41dc-80cd-7886c78fc397` | Vendas ML |
| 1.1.2 | Loja Própria (E-commerce) | `00c0bd64-e37b-4def-a69d-acf01353f0d3` | Vendas loja própria |
| 1.1.4 | Marketplace (Outros) | `d889b6f7-2ee4-433d-9b68-e15a70819db2` | Vendas Shopee/Amazon |
| 1.1.5 | Vendas Diretas/Balcão | `0ff3cac8-fc5f-443d-8829-36ae22458411` | Vendas POS/Balcão |
| 1.3.1 | Receita de Frete Cobrado | `d00a7c0d-a04c-462b-bed6-6945d84bee6d` | Frete cobrado do comprador |
| 1.3.4 | Descontos e Estornos de Taxas | `c4cc890c-126a-48da-8e47-da7c1492620d` | Estorno de comissão ML |
| 1.3.7 | Estorno de Frete sobre Vendas | `2c0ef767-4983-4c4e-bfec-119f05708cd4` | Estorno frete em cancelamento |

#### DESPESAS usadas no fluxo ML/MP:

| Código | Nome | ID | Uso no V2 |
|--------|------|----|-----------|
| 1.2.1 | Devoluções e Cancelamentos | `713ee216-8abe-4bcd-bc54-34421cb62a06` | Cancelamento/devolução |
| 2.8.2 | Comissões de Marketplace | `699d6072-031a-47bf-9aeb-563d1c2e8a41` | Comissão ML por venda |
| 2.9.4 | MercadoEnvios | `6ccbf8ed-e174-4da0-ac8d-0ed1b387cb32` | Frete MercadoEnvios |
| 2.9.10 | Frete Full | `27c8de66-cbb2-4778-94a5-b0de4405ae68` | Frete Fulfillment ML |
| 2.11.8 | Tarifas de Pagamento (MP, PagSeg) | `d77aa9d6-dd63-4d67-a622-64c3a05780a5` | Taxas MP diversas |
| 2.11.9 | Antecipação de Recebíveis | `7e9efb50-6039-4238-b844-a10507c42ff2` | Taxa de antecipação |
| 2.7.4 | Anúncios MercadoLibre | `e3dc7ffe-fad0-4abf-844a-e87379c0d7a8` | Product Ads ML |
| 2.7.4 | Mercado Livre Ads | `a9eb69ea-a6fb-4c1a-8cc5-d55fa9b2db3e` | Mercado Ads |

### 12.5 Categorias Completas

#### RECEITAS (22 categorias)

```
1.1 VENDAS
  1.1.1 MercadoLibre
  1.1.2 Loja Própria (E-commerce)
  1.1.3 Vendas B2B (Atacado)
  1.1.4 Marketplace (Outros - Shopee, Amazon, Magalu)
  1.1.5 Vendas Diretas/Balcão
  1.1.6 Serviços (Instalação, Consultoria)

1.3 RECEITAS OPERACIONAIS
  1.3.1 Receita de Frete Cobrado
  1.3.2 Juros Recebidos
  1.3.3 Rendimento de Aplicações
  1.3.4 Descontos e Estornos de Taxas e Tarifas
  1.3.5 Receitas Intercompany
  1.3.6 Reversão de Provisões
  1.3.7 Estorno de Frete sobre Vendas
  1.3.8 A Classificar (receita operacional)
  1.3.9 Rendimentos CC
  2.1.8 Estorno de Compra de Mercadoria

1.4 RECEITAS NÃO OPERACIONAIS
  1.4.1 Venda de Ativo Imobilizado
  1.4.2 Outras Receitas Eventuais
  1.4.3 A Classificar (receita não operacional)
  1.4.4 Empréstimo Entre Sócios - Entrada
  1.4.5 Recebimento de Empréstimos e Financiamentos

3.2 DISTRIBUIÇÃO
  3.2.1 Estorno de Dividendos (+)
```

#### DESPESAS (141 categorias)

```
1.2 DEDUÇÕES DA RECEITA
  1.2.1 Devoluções e Cancelamentos
  1.2.3 Abatimentos

2.1 CMV (CUSTO DA MERCADORIA VENDIDA)
  2.1.1 Compra de Mercadorias
  2.1.2 Frete sobre Compras (Entrada)
  2.1.3 Seguro de Transporte - Entrada
  2.1.4 Embalagens
  2.1.5 Material de Empacotamento
  2.1.7 Compra de Insumos

2.2 IMPOSTOS SOBRE VENDAS
  2.2.1 ICMS
  2.2.2 ICMS-ST (Substituição Tributária)
  2.2.3 DIFAL (Diferencial de Alíquota)
  2.2.4 PIS
  2.2.5 COFINS
  2.2.6 ISS (Serviços)
  2.2.7 Simples Nacional

2.3 IMPOSTOS GERAIS
  2.3.1 IPTU
  2.3.2 IPVA e Licenciamento
  2.3.3 Outros Impostos

2.4 PESSOAL
  2.4.1 Salários e Ordenados (CLT)
  2.4.2 Pró-Labore (Sócios)
  2.4.3 Prestadores de Serviço (PJ)
  2.4.4 Horas Extras
  2.4.5 Férias e 13º Salário
  2.4.6 Aviso Prévio e Rescisões
  2.4.7 FGTS
  2.4.8 INSS Patronal
  2.4.9 Rateio de Funcionários Compartilhados
  2.4.10 Vale Transporte (VT) e Vale Refeição (VR)
  2.4.11 Plano de Saúde
  2.4.12 Seguro de Vida
  2.4.13 Treinamentos e Capacitação
  2.4.14 Uniformes e EPIs
  2.4.15 INSS
  2.4.16 Despesas com Influencer

2.5 INFRAESTRUTURA
  2.5.1 Aluguel
  2.5.2 Condomínio
  2.5.3 Energia Elétrica
  2.5.4 Água e Esgoto
  2.5.5 Telefone e Internet
  2.5.6 Correios (Administrativo)
  2.5.7 Material de Escritório
  2.5.8 Material de Limpeza
  2.5.9 Manutenção e Reparos
  2.5.10 Segurança e Vigilância
  2.5.11 Seguros

2.6 TECNOLOGIA
  2.6.1 Software e Licenças
  2.6.2 ERP
  2.6.3 Software de Automação
  2.6.4 Banco de Dados (Supabase)
  2.6.5 APIs e Integrações
  2.6.6 Hospedagem de Sites
  2.6.7 Cloud Computing
  2.6.8 Desenvolvimento de Software
  2.6.9 Domínios e SSL
  2.6.10 Manutenção de Sistemas

2.7 MARKETING
  2.7.1 Google Ads
  2.7.2 Meta Ads
  2.7.3 Marketing em Marketplace
  2.7.3 Tiktok Ads
  2.7.4 Anúncios MercadoLibre
  2.7.4 Mercado Livre Ads
  2.7.5 SEO e Conteúdo
  2.7.6 Design e Criação
  2.7.7 Fotografia de Produtos
  2.7.8 Agência de Marketing
  2.7.9 Brindes e Materiais Promocionais

2.8 VENDAS / COMERCIAL
  2.8.1 Comissões de Vendedores
  2.8.2 Comissões de Marketplace
  2.8.3 Bonificações e Prêmios
  2.8.4 Viagens e Representação
  2.8.5 Participação em Feiras
  2.8.6 Amostras e Demonstrações
  2.8.7 Reembolso Diversos (vendas)

2.9 LOGÍSTICA
  2.9.1 Frete sobre Vendas (Saída)
  2.9.2 Correios (Envios)
  2.9.3 Transportadoras
  2.9.4 MercadoEnvios
  2.9.5 Entrega Local
  2.9.7 Seguro de Transporte - Saída
  2.9.8 Armazenagem
  2.9.9 Picking e Packing
  2.9.10 Frete Full

2.10 JURÍDICO E COMPLIANCE
  2.10.1 Honorários Contábeis
  2.10.2 Honorários Advocatícios
  2.10.3 Taxas e Contribuições
  2.10.4 Certidões e Registros
  2.10.5 Multas e Juros (Dedução Legal)
  2.10.6 Despesas Cartorárias
  2.10.7 Anuidades Profissionais

2.11 DESPESAS FINANCEIRAS
  2.11.1 Juros sobre Empréstimos
  2.11.2 Taxa de Administração de Consórcio
  2.11.3 Encargos De IRRF
  2.11.4 IOF
  2.11.5 Pagamento de Parcelas de Consórcio
  2.11.6 Tarifas Bancárias
  2.11.7 Tarifas de Cartão de Crédito
  2.11.8 Tarifas de Pagamento (Mercado Pago, PagSeguro)
  2.11.9 Antecipação de Recebíveis
  2.11.10 Variação Cambial Passiva
  2.11.11 Juros de Mora
  2.11.12 Juros sobre Financiamentos
  2.11.13 Juros do Rotativo do Cartão de Crédito
  2.11.14 Juros Sobre Cheque Especial
  2.11.15 Taxa de Boleto
  2.11.16 Descontos Concedidos (Financeiros)
  2.11.17 Multas (despesa financeira)
  2.11.18 Juros (despesa financeira)

2.12 VEÍCULOS
  2.12.1 Combustível
  2.12.2 Manutenção de Veículos
  2.12.3 Seguro de Veículos
  2.12.4 Estacionamento e Pedágios

2.13 TRIBUTÁRIO (IR/CSLL)
  2.13.1 IRPJ (Imposto de Renda PJ)
  2.13.2 CSLL (Contribuição Social)
  2.13.3 Adicional de IRPJ

2.14 OUTRAS DESPESAS OPERACIONAIS
  2.14.1 Despesas com Viagens
  2.14.2 Alimentação e Refeições
  2.14.3 Despesas Médicas e Exames
  2.14.4 Doações e Patrocínios
  2.14.5 Perdas com Inadimplência
  2.14.6 Quebras e Perdas de Estoque
  2.14.7 Perdas em Garantia
  2.14.8 Despesas Eventuais
  2.14.9 Despesas de Exercícios Anteriores a nov/2025
  2.14.10 A Classificar (despesa operacional)
  2.14.11 IRRF
  2.14.12 Assinaturas
  2.14.13 Pagamento de Empréstimos e Financiamentos
  2.14.14 Água, Copa e Cozinha

2.15 INVESTIMENTOS E EXPANSÃO
  2.15.1 Pesquisa e Desenvolvimento
  2.15.2 Expansão de Negócios
  2.15.3 Consultoria Empresarial
  2.15.4 Compra de Ativo Imobilizado
  2.15.5 Aporte de capital

2.16 INTERCOMPANY
  2.16.1 Serviços Contratados Intercompany
  2.16.2 Aluguel Pago Intercompany
  2.16.3 Rateio de Custos Compartilhados
  2.16.4 Royalties e Licenciamento Interno

2.17 NÃO OPERACIONAIS
  2.17.1 Empréstimo Entre Sócios - Saída
  2.17.2 A Classificar (despesa não operacional)

3.1 DISTRIBUIÇÃO DE LUCROS
  3.1.1 Pagamento de dividendos
```

### 12.6 Sellers ML Confirmados (8 sellers = 8 integrações)

| # | Seller ML | Conta MP Disponível (CA) | Centro Custo Variável | MP Retido (A CRIAR) |
|---|-----------|--------------------------|----------------------|---------------------|
| 1 | **NETAIR** | NETAIR - MP (`00900d7c`) | CC001.1 NETAIR - VAR | MP Retido - NETAIR |
| 2 | **NETPARTS** | NETPARTS - MP (`dbe7d162`) | CC002.1 NETPARTS - VAR | MP Retido - NETPARTS |
| 3 | **141AIR** | 141AIR - MP (`f0e9908c`) | CC003.1 141AIR - VAR | MP Retido - 141AIR |
| 4 | **EASYPEASY** | EASYPEASY - MP (`c8939e1a`) | CC004.1 EASYPEASY - VAR | MP Retido - EASYPEASY |
| 5 | **EASYPEASY SP** | EASYPEASY - MP FILIAL (`56a04907`) | CC010.1 EASYPEASY SP - VAR | MP Retido - EASYPEASY SP |
| 6 | **UNIQUE** | UNIQUE - MP (`11d76e3b`) | CC005.1 UNIQUE - VAR | MP Retido - UNIQUE |
| 7 | **UNIQUEKIDS** | UNIQUE - MP \| UNIQUEKIDS (`cf762c11`) | CC005.1 UNIQUE - VAR | MP Retido - UNIQUEKIDS |
| 8 | **BELLATOR** | BELLATOR - MP (`5d9d1e55`) | CC006.1 BELLATOR - VAR | MP Retido - BELLATOR |

> **VICTOR** usa MP como meio de pagamento mas **NÃO vende no ML**. Não entra no fluxo V2.

> **AÇÃO:** Criar 8 contas financeiras "MP Retido - [Seller]" no Conta Azul (tipo OUTROS, banco Mercado Pago).

---

## 13. Operacionalização Completa - Mapeamento Evento → API → Conta Azul

> Documento de referência detalhado das APIs: `REFERENCIA-APIs-ML-MP.md` (mesmo diretório)

### 13.1 Webhooks a configurar (por seller)

Cada seller (8 contas MP) precisa ter os seguintes webhooks configurados apontando para a API V2:

**No Mercado Pago (dashboard do desenvolvedor):**
- `payment` → Venda, refund, chargeback, mudança de status
- `topic_chargebacks_wh` → Contestações de cartão
- `topic_claims_integration_wh` → Reclamações e mediações
- `stop_delivery_op_wh` → Alerta de fraude (**SEM RETRIES - crítico**)

**No Mercado Livre (centro do desenvolvedor):**
- `orders_v2` → Pedidos (criação, cancelamento)
- `shipments` → Envios (entrega, devolução)
- `claims` → Reclamações

**URL padrão:** `https://api-v2.dominio.com/webhooks/{seller_slug}`

### 13.2 Mapeamento: Webhook → APIs consultadas → Lançamento no CA

#### EVENTO 1: Venda Aprovada
```
Gatilho:   webhook "payment" com action = "payment.created" ou "payment.updated"
Condição:  payment.status == "approved"

APIs consultadas:
  1. GET /v1/payments/{data.id}             → valores, taxas, money_release_date
  2. GET /orders/{payment.order.id}         → itens, descrição, pack_id
  3. GET /shipments/{order.shipping.id}     → tipo envio, status
  4. GET /shipments/{id}/costs              → breakdown de frete (quem paga quanto)

Lançamentos no Conta Azul:
  ┌─────────────────────────────────────────────────────────────────────┐
  │ RECEITA (contas-a-receber)                                         │
  │   data_competencia: payment.date_approved                          │
  │   valor: payment.transaction_amount                                │
  │   categoria: 1.1.1 MercadoLibre                                    │
  │   conta_financeira: MP Retido - {seller}                           │
  │   centro_custo: {seller} - VARIÁVEL                                │
  │   data_vencimento: payment.money_release_date                      │
  │   descricao: "Venda ML #{order_id} - {item_title}"                │
  │   observacao: "Payment: {payment_id} | Liberação: {release_date}" │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────────────────┐
  │ DESPESA - COMISSÃO (contas-a-pagar)                                │
  │   data_competencia: payment.date_approved                          │
  │   valor: fee_details[type=mercadopago_fee].amount                  │
  │   categoria: 2.8.2 Comissões de Marketplace                       │
  │   conta_financeira: MP Retido - {seller}                           │
  │   centro_custo: {seller} - VARIÁVEL                                │
  │   data_vencimento: payment.date_approved (deduzido na hora)        │
  │   baixa automática: sim (já pago pelo MP)                          │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────────────────┐
  │ DESPESA - FRETE (contas-a-pagar) [se senders[].cost > 0]          │
  │   data_competencia: payment.date_approved                          │
  │   valor: senders[0].cost (de /shipments/{id}/costs)                │
  │   categoria: 2.9.4 MercadoEnvios                                  │
  │   conta_financeira: MP Retido - {seller}                           │
  │   centro_custo: {seller} - VARIÁVEL                                │
  │   baixa automática: sim                                            │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────────────────┐
  │ DESPESA - PARCELAMENTO (contas-a-pagar) [se financing_fee > 0]    │
  │   data_competencia: payment.date_approved                          │
  │   valor: fee_details[type=financing_fee].amount                    │
  │   categoria: 2.8.2 Comissões de Marketplace (ou subcategoria)      │
  │   conta_financeira: MP Retido - {seller}                           │
  │   baixa automática: sim                                            │
  └─────────────────────────────────────────────────────────────────────┘

Validação: transaction_amount - TODAS as taxas == net_received_amount
```

#### EVENTO 2: Dinheiro Liberado (Release)
```
Gatilho:   NÃO TEM WEBHOOK DEDICADO!

Detecção (3 métodos, usar todos):
  A. Poll periódico: GET /v1/payments/{id} → money_release_status == "released"
  B. Released Money Report diário: RECORD_TYPE = "release", DESCRIPTION = "payment"
  C. Verificar se date atual >= money_release_date dos pagamentos pendentes

Lançamento no Conta Azul:
  ┌─────────────────────────────────────────────────────────────────────┐
  │ TRANSFERÊNCIA (baixa na parcela + novo lançamento)                 │
  │   DE: MP Retido - {seller}                                         │
  │   PARA: Mercado Pago - {seller}                                    │
  │   valor: payment.transaction_details.net_received_amount           │
  │   data: money_release_date efetivo (do report ou do payment)       │
  │                                                                     │
  │   Mecânica CA:                                                      │
  │   1. Criar baixa na parcela da receita (conta = MP Retido)         │
  │   2. Criar conta-a-receber com vencimento=hoje na MP Disponível    │
  │   3. Criar baixa automática (já recebido na MP Disponível)        │
  └─────────────────────────────────────────────────────────────────────┘
```

#### EVENTO 3: Saque (MP Disponível → Banco)
```
Gatilho:   NÃO TEM WEBHOOK!

Detecção:
  A. Released Money Report com frequency.type = "withdrawal" (gera report a cada saque)
  B. Account Balance Report: TRANSACTION_TYPE = "WITHDRAWAL"
  C. Poll periódico dos saldos das contas MP

Lançamento no Conta Azul:
  ┌─────────────────────────────────────────────────────────────────────┐
  │ TRANSFERÊNCIA                                                       │
  │   DE: Mercado Pago - {seller}                                       │
  │   PARA: Banco - {seller}                                            │
  │   valor: WITHDRAWAL amount do relatório                             │
  │   data: data do saque                                               │
  └─────────────────────────────────────────────────────────────────────┘
```

#### EVENTO 4: Cancelamento / Devolução Total
```
Gatilho:   webhook "payment" com payment.status == "refunded"

APIs consultadas:
  1. GET /v1/payments/{data.id}  → refunds[], transaction_amount_refunded

Lançamentos no Conta Azul (ESTORNOS):
  ┌─────────────────────────────────────────────────────────────────────┐
  │ ESTORNO RECEITA (contas-a-pagar ou estorno de contas-a-receber)    │
  │   data_competencia: refund.date_created                            │
  │   valor: -transaction_amount                                       │
  │   categoria: 1.2.1 Devoluções e Cancelamentos                     │
  │   conta_financeira: MP Retido - {seller}                           │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────────────────┐
  │ ESTORNO COMISSÃO (contas-a-receber ou estorno de contas-a-pagar)   │
  │   valor: +fee_details[mercadopago_fee].amount (devolvido pelo MP) │
  │   categoria: 1.3.4 Estornos de Taxas                              │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────────────────┐
  │ ESTORNO FRETE (se aplicável)                                       │
  │   valor: +shipping_fee (devolvido pelo MP)                         │
  │   categoria: 1.3.7 Estorno de Frete                               │
  └─────────────────────────────────────────────────────────────────────┘

Nota: Devolução PARCIAL → status permanece "approved" com status_detail =
      "partially_refunded". Estornar proporcionalmente.
```

#### EVENTO 5: Chargeback
```
Gatilho:   webhook "topic_chargebacks_wh" + "payment" (status: charged_back)

APIs consultadas:
  1. GET /v1/payments/{payment_id}          → status, status_detail
  2. GET /v1/chargebacks/{chargeback_id}    → coverage_applied, amount

Fase 1 - Chargeback aberto (status_detail = "in_process"):
  ┌─────────────────────────────────────────────────────────────────────┐
  │ BLOQUEIO DE FUNDOS                                                  │
  │   Marcar no Supabase: payment.money_release_blocked = true         │
  │   Se dinheiro já foi liberado: registrar débito em MP Disponível   │
  │   Gerar alerta para o vendedor                                     │
  └─────────────────────────────────────────────────────────────────────┘

Fase 2 - Resolução:
  Se vendedor PERDE (status_detail = "settled"):
    → Mesmos estornos do EVENTO 4 (como se fosse devolução)

  Se vendedor GANHA (status_detail = "reimbursed"):
    → Desbloquear liberação, prosseguir com EVENTO 2 normalmente
    → Se já tinha registrado débito: estornar o débito

Nota: Análise pode levar ATÉ 6 MESES (coverage_applied fica null)
```

#### EVENTO 6: Reclamação/Mediação
```
Gatilho:   webhook "topic_claims_integration_wh"

APIs consultadas:
  1. GET /post-purchase/v1/claims/{claim_id}   → type, stage, status, resolution
  2. GET /v1/payments/{payment_id}              → status atual

Quando aberta:
  → Registrar no Supabase como claim ativa
  → Se payment.status muda para in_mediation: bloquear liberação
  → Gerar alerta

Quando resolvida (status = "closed"):
  Se refund ao comprador (resolution.reason = "payment_refunded"):
    → Webhook payment subsequente vai disparar EVENTO 4

  Se vendedor ganha:
    → Desbloquear liberação, EVENTO 2 procede normalmente
```

#### EVENTO 7: Antecipação de Recebíveis
```
Gatilho:   NÃO TEM WEBHOOK!

Detecção:
  A. Released Money Report: DESCRIPTION = "fee_release_in_advance"
  B. Poll payment: money_release_date mudou para data anterior
  C. Released Money Report: payment liberado antes do money_release_date original

Lançamentos no Conta Azul:
  ┌─────────────────────────────────────────────────────────────────────┐
  │ TRANSFERÊNCIA ANTECIPADA                                            │
  │   DE: MP Retido - {seller}                                          │
  │   PARA: Mercado Pago - {seller}                                     │
  │   valor: net_received_amount                                        │
  │   data: data efetiva da antecipação                                 │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────────────────┐
  │ DESPESA - TAXA DE ANTECIPAÇÃO                                       │
  │   valor: fee_release_in_advance amount (do relatório)               │
  │   categoria: 2.11.9 Antecipação de Recebíveis                     │
  │   conta_financeira: Mercado Pago - {seller}                         │
  │   baixa automática: sim (já deduzido pelo MP)                       │
  └─────────────────────────────────────────────────────────────────────┘
```

#### EVENTO 8: Pagamento de Conta via MP
```
Gatilho:   NÃO TEM WEBHOOK!

Detecção:
  A. Account Balance Report: TRANSACTION_TYPE = "SETTLEMENT" com valor negativo
  B. Released Money Report: débito não associado a refund/chargeback

Lançamento no Conta Azul:
  ┌─────────────────────────────────────────────────────────────────────┐
  │ DESPESA (contas-a-pagar)                                            │
  │   data_competencia: data do pagamento                               │
  │   valor: valor do boleto/conta                                      │
  │   categoria: (depende do tipo de conta - classificação manual)      │
  │   conta_financeira: Mercado Pago - {seller}                         │
  │   baixa automática: sim (já saiu do MP)                             │
  └─────────────────────────────────────────────────────────────────────┘
```

### 13.3 Reconciliação Periódica

```
┌──────────────────────────────────────────────────────────────────┐
│ CRON DIÁRIO (madrugada)                                          │
│                                                                   │
│ Para cada seller (8x):                                           │
│   1. Gerar Released Money Report do dia anterior                 │
│      POST /v1/account/release_report                             │
│      body: { begin_date, end_date }                              │
│                                                                   │
│   2. Baixar e parsear o relatório                                │
│      GET /v1/account/release_report/{file_name}                  │
│                                                                   │
│   3. Para cada linha do relatório:                               │
│      a. Buscar SOURCE_ID no Supabase (ml_payments)               │
│      b. Verificar se lançamento correspondente existe no CA      │
│      c. Se DESCRIPTION = "payment" e não tem transferência:      │
│         → Criar transferência MP Retido → MP Disponível          │
│      d. Se DESCRIPTION = "payout" e não tem transferência:       │
│         → Criar transferência MP Disponível → Banco              │
│      e. Se DESCRIPTION = "refund" e não tem estorno:             │
│         → Criar estornos no CA                                   │
│      f. Se DESCRIPTION = "fee_release_in_advance":               │
│         → Registrar taxa de antecipação                          │
│                                                                   │
│   4. Gerar relatório de divergências                             │
│      → Transações no MP sem lançamento no CA                     │
│      → Lançamentos no CA sem transação no MP                     │
│      → Valores que não batem                                     │
│                                                                   │
│ CRON SEMANAL:                                                     │
│   1. Gerar Account Balance Report (saldo completo)               │
│   2. Comparar saldo MP Retido no CA vs calculado do relatório   │
│   3. Comparar saldo MP Disponível no CA vs relatório             │
│   4. Gerar alerta se diferença > R$ 0,01                        │
└──────────────────────────────────────────────────────────────────┘
```

### 13.4 Eventos SEM Webhook (resumo)

| Evento | Detecção | Frequência recomendada |
|--------|----------|----------------------|
| Dinheiro liberado | Released Money Report | Diário |
| Saque para banco | Report com frequency=withdrawal | Automático por saque |
| Antecipação | Released Money Report (fee_release_in_advance) | Diário |
| Pgto conta via MP | Account Balance Report | Diário |
| Missed notifications | GET /missed_feeds (ML) | A cada 30 min |
| Payments perdidos | GET /v1/payments/search | A cada 60 min |

### 13.5 Campos-chave extraídos por API

| API | Campo | Uso no V2 |
|-----|-------|-----------|
| `GET /v1/payments/{id}` | `transaction_amount` | Valor bruto da RECEITA |
| | `fee_details[].amount` | Valor de cada DESPESA (comissão, frete, parcelamento) |
| | `transaction_details.net_received_amount` | Valor da TRANSFERÊNCIA (liberação) |
| | `money_release_date` | Data vencimento da transferência |
| | `date_approved` | Data competência dos lançamentos |
| | `status` / `status_detail` | Determina tipo do evento |
| | `collector_id` | Identifica o seller |
| | `external_reference` | Cruza com order_id |
| `GET /orders/{id}` | `order_items[].title` | Descrição do lançamento |
| | `pack_id` | Agrupa orders do mesmo carrinho |
| `GET /shipments/{id}/costs` | `senders[].cost` | Valor frete pago pelo vendedor |
| | `receiver.cost` | Valor frete pago pelo comprador |
| Released Money Report | `SOURCE_ID` | Cruza com payment_id |
| | `DESCRIPTION` | Tipo do evento financeiro |
| | `NET_CREDIT_AMOUNT` | Valor creditado |
| | `NET_DEBIT_AMOUNT` | Valor debitado |

---

## 14. Discussões pendentes (atualizado)

- [x] ~~Quantas empresas/CNPJs usam o sistema?~~ → **8 sellers ML ativos** (ver 12.6)
- [ ] Já tem App registrada no ML Developers?
- [x] ~~Já tem App registrada no Conta Azul Developers?~~ → Sim (MCP server funcionando com OAuth2)
- [x] ~~Já tem Access Token / OAuth configurado para ambos?~~ → CA sim (via MCP), ML pendente
- [ ] Qual plano do Conta Azul? (precisa suportar API) → Funciona! API v2 ativa
- [ ] Onde hospedar? (VPS atual? Cloud?)
- [ ] Já usa Supabase?
- [ ] Precisa de dashboard web ou só a integração basta?
- [ ] A API V1 (CSV) continua como fallback/conferência?
- [ ] **CRIAR contas "MP Retido" para cada seller** (8-10 contas novas)
- [x] ~~Quais empresas vendem no ML?~~ → NETAIR, NETPARTS, 141AIR, EASYPEASY, EASYPEASY SP, UNIQUE 1, UNIQUE 2, BELLATOR
- [x] ~~EASYPEASY tem 2 contas MP - são sellers separados?~~ → **SIM** (EASYPEASY + EASYPEASY SP FILIAL)
- [x] ~~UNIQUE tem 2 contas MP - são sellers separados?~~ → **SIM** (UNIQUE + UNIQUEKIDS = 2 sellers)
- [ ] VICTOR usa MP mas **NÃO vende no ML** (só meio de pagamento)
- [x] ~~Pesquisa completa das APIs ML/MP~~ → Seção 13 + REFERENCIA-APIs-ML-MP.md
- [ ] **IMPORTANTE:** 4 eventos SEM webhook (liberação, saque, antecipação, pgto contas) → usar Reports API + polling
- [ ] Configurar Released Money Report com frequency=withdrawal para cada seller
- [ ] Definir frequência do cron de reconciliação (recomendado: diário)
- [ ] **ALERTA FRAUDE:** webhook `stop_delivery_op_wh` não tem retry - implementar redundância

---

## 15. Referências

### Mercado Pago - APIs
- [Get Payment](https://www.mercadopago.com.ar/developers/en/reference/payments/_payments_id/get)
- [Search Payments](https://www.mercadopago.com.ar/developers/en/reference/payments/_payments_search/get)
- [Create Refund](https://www.mercadopago.com.br/developers/en/reference/chargebacks/_payments_id_refunds/post)
- [Get Chargeback](https://www.mercadopago.com.br/developers/en/reference/chargebacks/_chargebacks_id/get)
- [Search Claims](https://www.mercadopago.com.ar/developers/en/reference/claims/search-claims/get)

### Mercado Pago - Notificações e Relatórios
- [Webhooks](https://www.mercadopago.com.br/developers/en/docs/your-integrations/notifications/webhooks)
- [Released Money Report - Fields](https://www.mercadopago.com.ar/developers/en/docs/nuvemshop/additional-content/reports/released-money/report-fields)
- [Released Money Report - API](https://www.mercadopago.com.ar/developers/en/docs/reports/released-money/api)
- [Account Balance Report](https://www.mercadopago.com.br/developers/en/docs/reports/account-money/introduction)
- [Chargeback Notifications](https://www.mercadopago.com.ar/developers/en/docs/checkout-pro/chargebacks/notifications)
- [Transaction Status](https://www.mercadopago.com.ar/developers/en/docs/checkout-api-orders/payment-management/status/transaction-status)

### Mercado Livre
- [Order Management](https://developers.mercadolivre.com.br/en_us/order-management)
- [Shipments](https://developers.mercadolivre.com.br/en_us/shipment-handling)
- [Shipping Costs](https://developers.mercadolibre.com.ar/en_us/management-of-shippin-fees)
- [Packs Management](https://developers.mercadolibre.com.ar/en_us/about-our-api/packs-management)
- [Claims / Returns](https://developers.mercadolibre.com.ar/en_us/working-with-claims)
- [Notifications](https://developers.mercadolivre.com.br/en_us/products-receive-notifications)
- [Fulfillment](https://developers.mercadolivre.com.br/en_us/fulfillment)
- [Billing Reports](https://developers.mercadolivre.com.br/en_us/billing-reports)

### Conta Azul
- [Portal de Desenvolvedores](https://developers.contaazul.com/)
- [API Financeira](https://developers.contaazul.com/docs/financial-apis-openapi)
- [Criar Contas a Pagar](https://developers.contaazul.com/docs/financial-apis-openapi/v1/createpayablefinancialevent)
- [Contas Financeiras](https://developers.contaazul.com/docs/financial-apis-openapi/v1/searchfinancialaccounts)
- [Centros de Custo](https://developers.contaazul.com/docs/financial-apis-openapi/v1/searchcostcenters)

### Documentos Internos
- [REFERENCIA-APIs-ML-MP.md](./REFERENCIA-APIs-ML-MP.md) - Referência técnica completa das APIs ML/MP

---

*Documento vivo - atualizado conforme definições*

# Referência Completa - APIs Mercado Livre / Mercado Pago

**Para uso do API Conciliador V2**
**Gerado em:** 2026-02-11
**Baseado em:** Pesquisa extensiva da documentação oficial ML/MP

---

## 1. GET /v1/payments/{id} - Campos do Pagamento

### 1.1 Campos de Identificação

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | number | ID único do pagamento |
| `collector_id` | number | ID do vendedor/recebedor |
| `operation_type` | string | Tipo: `regular_payment`, `money_transfer`, `recurring_payment`, `pos_payment` |
| `external_reference` | string | Referência customizada |
| `description` | string | Descrição do pagamento |
| `currency_id` | string | Moeda (BRL) |
| `live_mode` | boolean | Produção (true) ou sandbox (false) |

### 1.2 Campos de Data (CRÍTICOS para conciliação)

| Campo | Tipo | Descrição | Uso no V2 |
|-------|------|-----------|-----------|
| `date_created` | datetime | Quando o pagamento foi criado | **data_competencia** da receita |
| `date_approved` | datetime/null | Quando aprovado (null se pendente) | Gatilho para criar lançamento |
| `date_last_updated` | datetime | Última modificação | Detectar mudanças |
| `money_release_date` | datetime/null | Quando o dinheiro será/foi liberado | **data_vencimento** da transferência MP Retido → MP Disponível |
| `date_of_expiration` | datetime/null | Expiração de pagamentos pendentes | Cleanup de boletos não pagos |

### 1.3 Status do Pagamento

| Status | Descrição | Ação no V2 |
|--------|-----------|------------|
| `pending` | Aguardando pagamento (boleto gerado) | Nenhuma (aguardar) |
| `approved` | **Aprovado e creditado** | **Criar lançamentos no CA** |
| `authorized` | Autorizado mas não capturado | Nenhuma (aguardar captura) |
| `in_process` | Em análise pelo MP | Nenhuma (aguardar) |
| `in_mediation` | **Disputa/reclamação aberta** | **Bloquear liberação, criar alerta** |
| `rejected` | Rejeitado | Nenhuma (comprador pode tentar de novo) |
| `cancelled` | Cancelado/expirado | **Estornar lançamentos se havia approved antes** |
| `refunded` | **Reembolsado totalmente** | **Criar estornos no CA** |
| `charged_back` | **Chargeback de cartão** | **Criar lançamento de chargeback no CA** |

### 1.4 status_detail por status

**approved:**
- `accredited` - Aprovado e creditado
- `partially_refunded` - Tem pelo menos um estorno parcial

**charged_back:**
- `in_process` - Chargeback aberto, fundos retidos
- `settled` - Decidido contra o vendedor, dinheiro deduzido
- `reimbursed` - Decidido a favor do vendedor, dinheiro devolvido

**refunded:**
- `refunded` - Estorno total pelo vendedor
- `by_admin` - Estorno pelo MP/suporte

**cancelled:**
- `expired` - Expirado após ~30 dias pendente
- `by_collector` - Cancelado pelo vendedor
- `by_payer` - Cancelado pelo comprador

### 1.5 Campos de Valor (CRÍTICOS para conciliação)

| Campo | Tipo | Descrição | Uso no V2 |
|-------|------|-----------|-----------|
| `transaction_amount` | float | **Valor bruto** cobrado do comprador | Valor da RECEITA no CA |
| `transaction_amount_refunded` | float | Total reembolsado | Valor do ESTORNO no CA |
| `coupon_amount` | float | Desconto de cupom | Deduzir do bruto |
| `shipping_amount` | float | Frete incluído | Informativo |
| `installments` | number | Nº de parcelas | Informativo |

### 1.6 transaction_details (Líquido)

```json
{
  "transaction_details": {
    "net_received_amount": 82.00,
    "total_paid_amount": 100.00,
    "overpaid_amount": 0,
    "installment_amount": 100.00
  }
}
```

| Sub-campo | Descrição | Uso no V2 |
|-----------|-----------|-----------|
| `net_received_amount` | **VALOR LÍQUIDO que o vendedor recebe** | Valor da transferência MP Retido → MP Disponível |
| `total_paid_amount` | Total pago pelo comprador (com financiamento) | Informativo |
| `overpaid_amount` | Excesso pago (raro, tipicamente 0) | Conferência |

**Fórmula:** `net_received_amount = transaction_amount - todas_as_taxas`

### 1.7 fee_details (Taxas)

```json
{
  "fee_details": [
    {
      "type": "mercadopago_fee",
      "amount": 12.00,
      "fee_payer": "collector"
    },
    {
      "type": "financing_fee",
      "amount": 6.00,
      "fee_payer": "collector"
    }
  ]
}
```

| type | Descrição | Categoria no CA |
|------|-----------|-----------------|
| `mercadopago_fee` | Comissão ML/MP (inclui IVA) | 2.8.2 Comissões de Marketplace |
| `financing_fee` | Custo de parcelamento sem juros | 2.11.9 Antecipação de Recebíveis (ou 2.8.2) |
| `shipping_fee` | Custo de frete | 2.9.4 MercadoEnvios |
| `coupon_fee` | Desconto de cupom | (deduzir da receita) |
| `application_fee` | Comissão de marketplace/integrador | 2.8.2 Comissões de Marketplace |
| `discount_fee` | Desconto absorvido pelo vendedor | (deduzir da receita) |

**fee_payer:** `"collector"` (vendedor absorve) ou `"payer"` (comprador absorve)

### 1.8 Método de Pagamento

| payment_type_id | Descrição | Exemplos no Brasil |
|-----------------|-----------|-------------------|
| `credit_card` | Cartão de crédito | visa, master, amex, hipercard, elo |
| `debit_card` | Cartão de débito | visa, master |
| `bank_transfer` | Transferência/PIX | pix |
| `ticket` | Boleto | bolbradesco |
| `account_money` | Saldo MP | account_money |
| `digital_currency` | Mercado Crédito | - |
| `prepaid_card` | Cartão pré-pago | - |

### 1.9 money_release_date e money_release_status

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `money_release_date` | datetime/null | Data prevista de liberação |
| `money_release_status` | string/null | `"released"`, `"pending"` |
| `money_release_schema` | string/null | Esquema de liberação (D+14, D+30, etc.) |

**IMPORTANTE:** `money_release_date` pode mudar se:
- Reclamação/disputa aberta → liberação bloqueada/adiada
- Chargeback → liberação revertida
- Antecipação solicitada → liberação antecipada
- Entrega não confirmada no ML → liberação adiada

### 1.10 refunds (dentro do payment)

```json
{
  "refunds": [
    {
      "id": 123456789,
      "payment_id": 987654321,
      "amount": 50.00,
      "date_created": "2025-01-15T10:30:00.000-04:00",
      "status": "approved",
      "amount_refunded_to_payer": 50.00
    }
  ]
}
```

- **Refund total:** status muda para `refunded`, `transaction_amount_refunded = transaction_amount`
- **Refund parcial:** status **permanece** `approved`, `status_detail = "partially_refunded"`
- Múltiplos refunds parciais são cumulativos
- Limite: ~180 dias para cartão

---

## 2. GET /v1/payments/search - Busca de Pagamentos

### Parâmetros

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `sort` | string | `date_approved`, `date_created`, `date_last_updated`, `money_release_date` |
| `criteria` | string | `asc` ou `desc` |
| `external_reference` | string | Filtrar por referência |
| `status` | string | Filtrar por status |
| `range` | string | Campo de data: `date_created`, `date_approved`, `money_release_date` |
| `begin_date` | string | Início (formato: `yyyy-MM-dd'T'HH:mm:ss.SSSZ`) |
| `end_date` | string | Fim |
| `offset` | number | Paginação (máx recomendado: 10.000) |
| `limit` | number | Resultados por página |

**Limitações:**
- Máximo 12 meses de histórico
- Offset > 10.000 pode falhar
- Rate limit: evitar polling agressivo, usar webhooks

---

## 3. Webhooks ML/MP - Configuração Completa

### 3.1 Topics do Mercado Pago

| Topic | Quando dispara | Ação no V2 |
|-------|---------------|------------|
| `payment` | Pagamento criado, status muda (approved, refunded, charged_back) | **PRINCIPAL** - cria/atualiza lançamentos |
| `topic_chargebacks_wh` | Chargeback aberto, status muda, fundos bloqueados/liberados | **Criar lançamento de chargeback** |
| `topic_claims_integration_wh` | Reclamação aberta, status muda, mediação | **Alerta + bloquear liberação** |
| `topic_merchant_order_wh` | Merchant order criada/fechada/expirada | Informativo |
| `stop_delivery_op_wh` | **Alerta de fraude** (SEM RETRIES!) | **Cancelar envio imediatamente** |

### 3.2 Topics do Mercado Livre

| Topic | Quando dispara | Ação no V2 |
|-------|---------------|------------|
| `orders_v2` | Pedido criado, confirmado, pago, cancelado | Atualizar dados do pedido |
| `payments` | Pagamento criado em pedido ML | Redundante com MP `payment` |
| `shipments` | Envio criado, status muda (shipped, delivered) | Atualizar status entrega |
| `claims` | Reclamação aberta no ML | Complementar ao MP claims |

### 3.3 Formato do Payload (MP)

```json
{
  "id": 12345,
  "live_mode": true,
  "type": "payment",
  "date_created": "2025-01-15T10:04:58.396-04:00",
  "user_id": 44444,
  "api_version": "v1",
  "action": "payment.created",
  "data": {
    "id": "999999999"
  }
}
```

**IMPORTANTE:** O webhook é LEVE - contém apenas o ID. Deve-se fazer GET para obter os dados completos.

### 3.4 Validação de Assinatura (HMAC-SHA256)

```python
import hmac, hashlib

def validate_webhook(request):
    x_signature = request.headers.get('x-signature')
    x_request_id = request.headers.get('x-request-id')

    parts = {}
    for part in x_signature.split(','):
        key, value = part.split('=', 1)
        parts[key] = value

    ts = parts['ts']
    received_hash = parts['v1']
    data_id = request.json()['data']['id']

    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    computed_hash = hmac.new(
        SECRET_KEY.encode('utf-8'),
        manifest.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(computed_hash, received_hash)
```

### 3.5 Política de Retry

**Mercado Pago:**
| Tentativa | Delay |
|-----------|-------|
| 1 | Imediato |
| 2 | 15 min |
| 3 | 30 min |
| 4 | 6 horas |
| 5 | 48 horas |
| 6-8 | 96 horas cada |

- Timeout: 22 segundos para retornar HTTP 200/201
- **EXCEÇÃO:** `stop_delivery_op_wh` → **ZERO retries** (perder = perder para sempre)

**Mercado Livre:**
- Timeout: **500 milissegundos** para retornar HTTP 200
- Retry: até 1 hora com backoff exponencial
- Se falhar consistentemente: ML **desativa** a inscrição do tópico

### 3.6 Missed Feeds (ML)

```
GET https://api.mercadolibre.com/missed_feeds?app_id={APP_ID}&topic=orders_v2
```

Retorna notificações que foram enviadas mas nunca confirmadas com HTTP 200.

---

## 4. Orders API (Mercado Livre)

### 4.1 GET /orders/{order_id}

**Campos principais:**

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | number | ID do pedido |
| `status` | string | confirmed, payment_required, paid, cancelled, invalid |
| `total_amount` | float | Total dos produtos (sem frete) |
| `paid_amount` | float | Valor efetivamente pago |
| `shipping_cost` | float | Custo de frete |
| `pack_id` | number/null | ID do pack (carrinho) se multi-item |
| `tags` | array | Tags: `paid`, `delivered`, `pack_order`, `fraud_risk_detected` |
| `cancel_detail` | object | Detalhes de cancelamento (quando cancelado) |

### 4.2 payments[] dentro do Order

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | number | **ID do pagamento MP** (para GET /v1/payments/{id}) |
| `transaction_amount` | float | Custo do produto |
| `total_paid_amount` | float | Total incluindo frete/taxas |
| `shipping_cost` | float | Parte de frete do pagamento |
| `marketplace_fee` | float | **Taxa do ML deduzida do vendedor** |
| `status` | string | approved, pending, rejected, refunded |
| `payment_type` | string | credit_card, debit_card, account_money, ticket |
| `installments` | int | Nº parcelas |
| `date_approved` | datetime | Quando aprovado |

### 4.3 Status do Pedido

| Status | Descrição | Ação no V2 |
|--------|-----------|------------|
| `confirmed` | Pedido criado | Nenhuma |
| `payment_required` | Aguardando pagamento | Nenhuma |
| `paid` | **Pagamento aprovado** | Lançamentos via webhook payment |
| `cancelled` | Cancelado | Estornos se havia lançamentos |
| `invalid` | Fraude detectada | Cancelar + alerta |

### 4.4 Packs (Multi-Item/Carrinho)

```
GET /packs/{PACK_ID}
```

- 1 pack = N orders + 1 shipment
- Cada order tem seus próprios payments
- Todos os orders do pack compartilham o mesmo shipping_id
- O comprador faz 1 pagamento, ML distribui proporcionalmente

---

## 5. Shipments API (Mercado Livre)

### 5.1 GET /shipments/{shipment_id}

**Header obrigatório:** `x-format-new: true`

**Campos principais:**

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | number | ID do envio |
| `status` | string | pending, handling, ready_to_ship, shipped, delivered, not_delivered, cancelled |
| `substatus` | string | Detalhe do status |
| `tracking_number` | string | Código de rastreio |
| `logistic.mode` | string | me1, me2, custom |
| `logistic.type` | string | drop_off, xd_drop_off, cross_docking, self_service, fulfillment |
| `lead_time.cost` | float | Custo do frete |
| `lead_time.cost_type` | string | `free`, `charged`, `partially_free` |

### 5.2 GET /shipments/{shipment_id}/costs (CRÍTICO)

```json
{
  "gross_amount": 24.55,
  "receiver": {
    "user_id": 74425755,
    "cost": 0,
    "compensation": 0,
    "discounts": [
      { "rate": 1, "type": "loyal", "promoted_amount": 4.07 }
    ]
  },
  "senders": [{
    "user_id": 81387353,
    "cost": 8.19,
    "compensation": 0,
    "discounts": [
      { "rate": 0.6, "type": "mandatory", "promoted_amount": 12.29 }
    ]
  }]
}
```

**Cenários de frete:**
- **Comprador paga:** `receiver.cost = gross_amount`, `senders[].cost = 0`
- **Frete grátis (mandatório):** `receiver.cost = 0`, `senders[].cost = parcial`, ML subsidia o resto
- **Frete grátis (vendedor):** `receiver.cost = 0`, `senders[].cost = total`

**Tipos de desconto:**
- `mandatory` - Subsídio ML (frete grátis obrigatório por preço)
- `loyal` - Desconto Mercado Pontos
- `optional` - Frete grátis escolhido pelo vendedor
- `promotional` - Campanha promocional

### 5.3 Tipos de Envio (Mercado Envios)

| Tipo | logistic.type | Descrição |
|------|---------------|-----------|
| **ME2 Drop Off** | `drop_off` | Vendedor leva ao correio |
| **ME2 Places** | `xd_drop_off` | Vendedor leva a ponto parceiro |
| **ME2 Coleta** | `cross_docking` | ML coleta no vendedor |
| **ME2 Flex** | `self_service` | Entrega no mesmo dia pelo vendedor |
| **ME2 Full** | `fulfillment` | ML armazena e envia (FBM) |
| **ME1** | (mode=me1) | Vendedor gerencia próprio frete |

---

## 6. Chargebacks API

### 6.1 GET /v1/chargebacks/{id}

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | string | ID do chargeback |
| `payments` | array | IDs de pagamento associados |
| `amount` | number | Valor do chargeback |
| `coverage_applied` | boolean/null | `true` = vendedor protegido, `false` = vendedor perde, `null` = em análise |
| `coverage_elegible` | boolean | Se qualifica para proteção |
| `documentation_required` | boolean | Se precisa enviar evidência |
| `documentation_status` | string | `not_supplied`, `review_pending` |
| `date_documentation_deadline` | datetime | Prazo para enviar evidência |

### 6.2 Ciclo de Vida do Chargeback

```
Pagamento approved → Chargeback aberto → payment.status = "charged_back"
                                          status_detail = "in_process"
                                          Fundos bloqueados
                     ↓
                Vendedor envia evidência (POST /v1/chargebacks/{id}/documentation)
                     ↓
                Banco resolve (até 6 meses!)
                     ├── Vendedor ganha: status_detail = "reimbursed", fundos liberados
                     └── Vendedor perde: status_detail = "settled", fundos deduzidos
```

---

## 7. Claims API (Reclamações/Mediações)

### 7.1 Endpoints

| Endpoint | Descrição |
|----------|-----------|
| `GET /post-purchase/v1/claims/search` | Buscar reclamações |
| `GET /post-purchase/v1/claims/{claim_id}` | Detalhes da reclamação |

### 7.2 Tipos de Reclamação

| type | Descrição | Impacto Financeiro |
|------|-----------|-------------------|
| `mediations` | Disputa comprador/vendedor | Fundos retidos até resolução |
| `cancel_purchase` | Comprador quer cancelar | Reembolso se aprovado |
| `return` | Comprador quer devolver | Reembolso após devolução |

### 7.3 Estágios

- `claim` → `dispute` → `recontact`

### 7.4 Status

- `opened` - Ativo
- `closed` - Resolvido (ver resolution.reason para saber se refund ou não)

### 7.5 Impacto Financeiro

- Quando aberta: `money_release_date` pode ser adiada
- Payment pode mudar para `in_mediation`
- Se resolvida pro comprador: payment muda para `refunded`
- Se resolvida pro vendedor: fundos liberados normalmente

---

## 8. Reports API (Relatórios)

### 8.1 Released Money Report (Dinheiro Liberado)

**O relatório DEFINITIVO para reconciliação.** Mostra cada movimento que impactou o saldo disponível.

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `POST /v1/account/release_report/config` | POST | Configurar |
| `POST /v1/account/release_report` | POST | Gerar (body: begin_date, end_date) |
| `GET /v1/account/release_report/list` | GET | Listar relatórios |
| `GET /v1/account/release_report/{file_name}` | GET | Baixar |
| `POST /v1/account/release_report/schedule` | POST | Agendar |

**RECORD_TYPE:**
- `initial_available_balance` - Saldo inicial
- `release` - Dinheiro liberado de uma cobrança
- `total` - Total líquido
- `available_balance` - Saldo antes/depois de saque

**DESCRIPTION (eventos financeiros):**

| Valor | Descrição | Ação no V2 |
|-------|-----------|------------|
| `payment` | Pagamento liberado | Transferência MP Retido → MP Disponível |
| `refund` | Reembolso | Estorno |
| `chargeback` | Contestação | Lançamento de chargeback |
| `dispute` | Disputa | Bloqueio/liberação |
| `mediation` | Mediação | Bloqueio/liberação |
| `payout` | **Saque para banco** | Transferência MP Disponível → Banco |
| `fee_release_in_advance` | **Taxa de antecipação** | Despesa: 2.11.9 Antecipação |
| `shipping` | Taxa de frete | Despesa: 2.9.4 MercadoEnvios |
| `shipping_cancel` | Cancelamento de frete | Estorno de frete |
| `tax_withdholding` | Retenção fiscal | Despesa fiscal |
| `reserve_for_refund` | Reserva para reembolso | Bloqueio |
| `credit_payment` | Parcela de empréstimo | Despesa financeira |

**Campos financeiros do relatório:**

| Campo | Descrição |
|-------|-----------|
| `SOURCE_ID` | **ID do pagamento** (para cruzar com GET /v1/payments/{id}) |
| `EXTERNAL_REFERENCE` | Referência externa |
| `GROSS_AMOUNT` | Valor bruto |
| `MP_FEE_AMOUNT` | Comissão ML/MP (inclui IVA) |
| `FINANCING_FEE_AMOUNT` | Custo de parcelamento |
| `SHIPPING_FEE_AMOUNT` | Custo de frete |
| `TAXES_AMOUNT` | Impostos retidos |
| `COUPON_AMOUNT` | Desconto de cupom |
| `NET_CREDIT_AMOUNT` | **Líquido creditado** |
| `NET_DEBIT_AMOUNT` | **Líquido debitado** |
| `BALANCE_AMOUNT` | Saldo após operação |

**Configuração de frequência:**
```json
{
  "frequency": {
    "hour": 3,
    "type": "daily"     // "daily", "weekly", "monthly", "withdrawal"
  }
}
```

**type = "withdrawal"**: Gera relatório automaticamente a cada saque → perfeito para detectar transferências MP → Banco.

### 8.2 Account Balance Report (Relatório de Saldo)

Mostra TODAS operações (disponível + indisponível).

| Endpoint | Método |
|----------|--------|
| `POST /v1/account/settlement_report/config` | Configurar |
| `POST /v1/account/settlement_report` | Gerar |
| `GET /v1/account/settlement_report/list` | Listar |
| `GET /v1/account/settlement_report/{file_name}` | Baixar |

**TRANSACTION_TYPE:**
- `SETTLEMENT` - Pagamento aprovado
- `REFUND` - Reembolso
- `CHARGEBACK` - Contestação
- `DISPUTE` - Reclamação
- `WITHDRAWAL` - **Transferência para banco**
- `WITHDRAWAL_CANCEL` - Transferência cancelada
- `PAYOUT` - Saque em dinheiro

### 8.3 Limitações dos Relatórios

- Mínimo: 1 dia de dados
- Máximo: 60 dias por relatório
- Histórico: últimos 12 meses
- Formatos: CSV, XLSX
- Timezone: GMT-4

---

## 9. Detecção de Eventos SEM Webhook

| Evento | Como detectar |
|--------|--------------|
| **Dinheiro liberado** | NÃO tem webhook! Usar: poll GET /v1/payments/{id} (money_release_status) ou Released Money Report |
| **Saque para banco** | NÃO tem webhook! Usar: Released Money Report com frequency.type = "withdrawal" ou Account Balance Report |
| **Antecipação** | NÃO tem webhook! Usar: Released Money Report (DESCRIPTION = "fee_release_in_advance") + mudança no money_release_date |
| **Pagamento de conta via MP** | NÃO tem webhook! Usar: Account Balance Report (TRANSACTION_TYPE = "SETTLEMENT" negativo) |

---

## 10. Sequência de Eventos - Ciclo Completo de uma Venda

```
T+0   Comprador paga             → webhook payment (status: pending/approved)
T+0   Pedido atualizado          → webhook orders_v2 (status: paid)
T+1   Preparação do envio        → webhook shipments (status: handling)
T+2   Etiqueta gerada            → webhook shipments (status: ready_to_ship)
T+3   Pacote coletado            → webhook shipments (status: shipped)
T+7   Pacote entregue            → webhook shipments (status: delivered)
T+14  Dinheiro liberado          → SEM WEBHOOK (poll payment ou report)
T+15  Vendedor faz saque         → SEM WEBHOOK (report com frequency=withdrawal)
```

### Cenário: Cancelamento

```
T+0   Pagamento approved         → webhook payment → cria lançamentos CA
T+X   Comprador cancela          → webhook orders_v2 (status: cancelled)
T+X   Pagamento refunded         → webhook payment (status: refunded) → cria estornos CA
```

### Cenário: Chargeback

```
T+0   Pagamento approved         → webhook payment → cria lançamentos CA
T+30  Chargeback aberto          → webhook topic_chargebacks_wh → alerta + bloqueia
T+30  Payment status muda        → webhook payment (status: charged_back)
T+60  Banco resolve              → webhook topic_chargebacks_wh
      Se vendedor ganha:         → fundos liberados, payment volta a approved
      Se vendedor perde:         → fundos deduzidos permanentemente
```

### Cenário: Reclamação/Mediação

```
T+0   Venda normal + entrega
T+X   Comprador abre reclamação  → webhook topic_claims_integration_wh
T+X   Money release bloqueado    → payment em in_mediation
T+X   Resolução                  → webhook topic_claims_integration_wh
      Se refund:                 → webhook payment (status: refunded) → estornos CA
      Se vendedor ganha:         → liberação normal procede
```

---

## 11. Arquitetura Recomendada para Webhooks

```
ML/MP ──webhook──▶ [Webhook Receiver]  ◀── Responder em <500ms com HTTP 200
                         │
                    enqueue (fila)
                         │
                    [Worker Process]
                    1. Validar assinatura HMAC
                    2. GET recurso completo via API
                    3. Deduplicar (data.id + action)
                    4. Processar (classificar, calcular)
                    5. Salvar no Supabase
                    6. Sincronizar com Conta Azul
```

**Reconciliação periódica (cron):**
- A cada 15-60 min: GET /v1/payments/search (pegar eventos perdidos)
- A cada 15-60 min: GET /missed_feeds (ML)
- Diário: Gerar Released Money Report e cruzar com Supabase
- Semanal: Gerar Account Balance Report para auditoria completa

---

*Documento de referência técnica - não editável manualmente*

# API Conciliador V2/V3 — Documentação Completa

**Versão:** 2.2.0
**Base URL:** `https://conciliador.levermoney.com.br` (produção) ou `http://localhost:8000` (desenvolvimento)
**Protocolo:** REST API com JSON
**Autenticação:** OAuth2 + X-Admin-Token (header)

---

## Índice

1. [Visão Geral](#visão-geral)
2. [Autenticação](#autenticação)
3. [Endpoints — Webhooks](#31-webhooks)
4. [Endpoints — Backfill](#32-backfill)
5. [Endpoints — Baixas](#33-baixas)
6. [Endpoints — Auth ML](#34-auth-ml)
7. [Endpoints — Auth CA](#35-auth-ca)
8. [Endpoints — Admin](#36-admin)
9. [Endpoints — Dashboard](#37-dashboard)
10. [Endpoints — Expenses](#38-expenses)
11. [Endpoints — Queue](#39-queue)
12. [Endpoints — Health/Debug](#310-healthdebug)
13. [Códigos de Erro](#códigos-de-erro)
14. [Exemplos curl](#exemplos-curl)

---

## Visão Geral

A API Conciliador automatiza a sincronização de vendas entre **Mercado Livre/Mercado Pago** e **Conta Azul ERP**.

**Funcionalidades principais:**
- Sincronização de payments ML/MP → receitas, despesas e baixas automáticas no CA
- Dashboard de faturamento em tempo real
- Gestão de sellers (onboarding, aprovação, ativação)
- Processamento de despesas non-order (boletos, SaaS, cashback)
- Fila persistente com retry automático
- Webhooks (recepção e log apenas; processamento via daily sync)
- Reconciliação de caixa com account_statement

**Pipeline automatizado (Nightly Pipeline):**
1. Sync diário de payments (D-1 a D-3)
2. Validação de fees contra release report
3. Ingestão de gaps do account_statement
4. Processamento de baixas
5. Legacy export (opcional)
6. Verificação de cobertura do extrato
7. Fechamento financeiro diário

---

## Autenticação

### OAuth2 — Mercado Livre

**Fluxo:** Authorization Code

```
1. GET /auth/ml/connect?seller=141air
   → Redireciona para https://auth.mercadolivre.com.br/authorization

2. Usuário autoriza no ML
   → ML redireciona para callback com code

3. GET /auth/ml/callback?code=...&state=141air
   → Troca code por tokens
   → Salva tokens no Supabase
   → Resposta: {"status": "success", "seller": "141air", ...}
```

**Variáveis obrigatórias em .env:**
- `ML_APP_ID`: App ID do app ML
- `ML_SECRET_KEY`: Secret key do app ML
- `ML_REDIRECT_URI`: URL de callback (ex: `https://conciliador.levermoney.com.br/auth/ml/callback`)

**Self-service install:** GET `/auth/ml/install` → novo seller é criado automaticamente com status `pending_approval`.

### OAuth2 — Conta Azul

**Fluxo:** Authorization Code com token rotation

```
1. GET /auth/ca/connect
   → Redireciona para https://auth.contaazul.com/login

2. Usuário autoriza no CA (Cognito)
   → CA redireciona para callback com code

3. GET /auth/ca/callback?code=...
   → Troca code por tokens
   → Salva tokens globais (tabela ca_tokens)
   → Resposta: HTML com confirmação
```

**Variáveis obrigatórias em .env:**
- `CA_CLIENT_ID`: Client ID do app CA (Cognito)
- `CA_CLIENT_SECRET`: Client secret do app CA
- `CA_ACCESS_TOKEN` / `CA_REFRESH_TOKEN`: Tokens bootstrap (ativação manual opcional)

**Status dos tokens:**
GET `/auth/ca/status` → retorna `{"connected": true, "access_token_valid": true, ...}`

### X-Admin-Token (Header)

**Proteção:** Endpoints `/admin/*` e `/expenses/*` requerem `X-Admin-Token` válido (session token de 24h).

**Fluxo de login:**
```bash
POST /admin/login
{
  "password": "sua_senha_aqui"
}
→ {"token": "token_urlsafe_base64"}

# Usar em requisições subsequentes:
curl -H "X-Admin-Token: token_urlsafe_base64" https://...
```

**First-time setup:** Primeira requisição com qualquer senha define a senha admin no banco.

---

## 3.1 Webhooks

### POST /webhooks/ml

Endpoint global para receber webhooks do Mercado Livre/Mercado Pago.

**Descrição:**
Recebe eventos de payment, order e outros topics do ML/MP. A requisição é processada em < 500ms (async). O processamento real de payments ocorre via daily sync automático às 00:01 BRT (D-1 a D-3), não por webhook.

**Url configurada no app ML:**
`https://conciliador.levermoney.com.br/webhooks/ml`

**Request:**
```json
{
  "topic": "payment" | "order" | ...,
  "action": "payment.updated" | "order.updated" | ...,
  "resource": "/payments/144359445042",
  "user_id": 1963376627,
  "data": {
    "id": 144359445042
  }
}
```

**Response:** `{"status": "ok"}`

**Status codes:**
| Code | Descrição |
|------|-----------|
| 200 | OK — evento recebido e logado |
| 400 | JSON inválido |
| 202 | Seller não identificado (webhook ainda é logado como "unmatched") |

**Notas importantes:**
- Seller é identificado pelo `user_id` do ML
- Se seller não encontrado, evento é salvo com status `"unmatched"` em `webhook_events`
- Assinatura HMAC-SHA256 é validada (header `x-signature`), mas aceita sem validação no MVP
- Processamento real: use `/backfill/{seller}?dry_run=false` para processar histórico

---

## 3.2 Backfill

### GET /backfill/{seller_slug}

Puxa payments do ML de um período e processa no CA (receitas, despesas, baixas).

**Parâmetros (Query):**

| Parâmetro | Tipo | Obrigatório | Default | Descrição |
|-----------|------|------------|---------|-----------|
| `begin_date` | string | Sim | - | Data início `YYYY-MM-DD` |
| `end_date` | string | Sim | - | Data fim `YYYY-MM-DD` |
| `dry_run` | boolean | Não | `true` | Se `true`, apenas lista sem processar |
| `max_process` | integer | Não | 0 | Máximo de payments a processar (0 = todos) |
| `concurrency` | integer | Não | 10 | Payments em paralelo (1-20) |
| `reprocess_missing_fees` | boolean | Não | `true` | Reprocessa payments já finalizados com fees nulos |

**Exemplo:**
```bash
# Dry run (listar)
curl "https://conciliador.levermoney.com.br/backfill/141air?begin_date=2026-02-01&end_date=2026-02-15&dry_run=true"

# Processar
curl "https://conciliador.levermoney.com.br/backfill/141air?begin_date=2026-02-01&end_date=2026-02-15&dry_run=false&concurrency=10"
```

**Response (dry_run=true):**
```json
{
  "mode": "dry_run",
  "seller": "141air",
  "period": "2026-02-01 to 2026-02-15",
  "total_payments": 87,
  "total_amount": 10750.33,
  "by_status": {
    "approved": 74,
    "refunded": 4,
    "rejected": 3,
    "in_mediation": 6
  },
  "already_done": 50,
  "already_done_missing_fees": 3,
  "reprocess_missing_fees": true,
  "to_process_new": 34,
  "to_reprocess_missing_fees": 3,
  "to_process": 37,
  "concurrency": 10,
  "sample": [
    {
      "id": 144359445042,
      "status": "approved",
      "amount": 284.74,
      "date": "2026-02-15T10:30:42",
      "order_id": 2000006829820543,
      "net": 196.01
    }
  ]
}
```

**Response (dry_run=false):**
```json
{
  "mode": "process",
  "seller": "141air",
  "period": "2026-02-01 to 2026-02-15",
  "total_found": 87,
  "already_done": 50,
  "already_done_missing_fees": 3,
  "to_process_new": 34,
  "to_reprocess_missing_fees": 3,
  "processed": 37,
  "errors": 0,
  "remaining": 0,
  "results": [
    {"id": 144359445042, "status": "ok"},
    {"id": 144359445043, "status": "ok"}
  ]
}
```

**Status codes:**
| Code | Descrição |
|------|-----------|
| 200 | OK |
| 404 | Seller não encontrado |
| 429 | Rate limit (CA) |
| 502 | Erro na comunicação com ML/CA |

**Filtros aplicados:**
- Status: `approved`, `refunded`, `in_mediation`, `charged_back`
- Apenas com `order_id` (não non-order payments)
- Não marketplace_shipment (frete pago pelo comprador)
- Não compras (collector_id = null)

**Idempotência:** Payments já processados são pulados; `reprocess_missing_fees=true` permite reprocessar com fees nulos.

---

## 3.3 Baixas

### GET /baixas/processar/{seller_slug}

Busca parcelas abertas (EM_ABERTO/ATRASADO) no CA com vencimento <= data_ate e cria baixa para cada uma.

**Parâmetros (Query):**

| Parâmetro | Tipo | Obrigatório | Default | Descrição |
|-----------|------|------------|---------|-----------|
| `dry_run` | boolean | Não | `true` | Se `true`, apenas lista sem criar baixas |
| `verify_release` | boolean | Não | `true` | Verifica `money_release_status` no ML antes da baixa |
| `data_ate` | string | Não | Hoje | Data limite `YYYY-MM-DD` |
| `lookback_days` | integer | Não | 90 | Dias para trás na busca |

**Exemplo:**
```bash
# Dry run
curl "https://conciliador.levermoney.com.br/baixas/processar/141air?dry_run=true"

# Processar (com verificação de liberação)
curl "https://conciliador.levermoney.com.br/baixas/processar/141air?dry_run=false&verify_release=true"
```

**Response (dry_run=true):**
```json
{
  "mode": "dry_run",
  "seller": "141air",
  "data_de": "2025-11-21",
  "data_ate": "2026-02-20",
  "conta_bancaria": "fea5f1de-fd23-4462-9b43-0a2c6ae4df04",
  "verify_release": true,
  "parcelas_pagar": {
    "total": 12,
    "itens": [
      {
        "id": "parcela-123",
        "descricao": "Comissão ML - Payment 144359445042",
        "data_vencimento": "2026-02-15",
        "total": 39.93,
        "nao_pago": 39.93,
        "status": "EM_ABERTO",
        "release_status": "released"
      }
    ]
  },
  "parcelas_receber": {
    "total": 8,
    "itens": []
  },
  "skipped_pagar": {
    "total": 2,
    "motivo": "money_release_status != released",
    "itens": [
      {
        "id": "parcela-124",
        "descricao": "Receita ML - Payment 144359445043",
        "data_vencimento": "2026-02-28",
        "total": 284.74,
        "nao_pago": 284.74,
        "status": "EM_ABERTO",
        "release_status": "pending"
      }
    ]
  },
  "skipped_receber": {
    "total": 0,
    "motivo": "money_release_status != released",
    "itens": []
  }
}
```

**Response (dry_run=false):**
```json
{
  "mode": "process",
  "seller": "141air",
  "data_de": "2025-11-21",
  "data_ate": "2026-02-20",
  "verify_release": true,
  "pagar": {
    "total": 12,
    "queued": 12,
    "errors": 0,
    "results": [
      {
        "id": "parcela-123",
        "tipo": "pagar",
        "descricao": "Comissão ML - Payment 144359445042",
        "valor": 39.93,
        "data_vencimento": "2026-02-15",
        "status": "queued"
      }
    ]
  },
  "receber": {
    "total": 8,
    "queued": 8,
    "errors": 0,
    "results": []
  },
  "skipped_pagar": {
    "total": 2,
    "motivo": "money_release_status != released",
    "itens": []
  },
  "skipped_receber": {
    "total": 0,
    "motivo": "money_release_status != released",
    "itens": []
  }
}
```

**Status codes:**
| Code | Descrição |
|------|-----------|
| 200 | OK |
| 404 | Seller não encontrado |
| 502 | Erro na comunicação com CA/ML |

**Verificação de liberação (`verify_release=true`):**
- Consulta `money_release_status` do ML para cada payment
- Se "pending", parcela é pulada (reportada em `skipped_*`)
- Se "released" ou indeterminado, baixa é criada
- Release status vem do cache Supabase (raw_payment) ou re-fetch do ML API

---

## 3.4 Auth ML

### GET /auth/ml/connect?seller={seller_slug}

Redireciona um seller autorizado para OAuth2 do ML.

**Parâmetros (Query):**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|------------|-----------|
| `seller` | string | Sim | Slug do seller (ex: "141air") |

**Response:**
HTTP 302 → Redireciona para Mercado Livre auth URL

**Status codes:**
| Code | Descrição |
|------|-----------|
| 302 | Redirecionamento OK |
| 404 | Seller não encontrado |
| 403 | Seller não aprovado (status deve ser pending_approval, approved ou active) |

---

### GET /auth/ml/install

Self-service install flow — novo seller é criado automaticamente.

**Request:** (sem parâmetros)

**Response:**
HTTP 302 → Redireciona para Mercado Livre auth URL (estado = new_install)

**Fluxo:**
1. Usuário clica em `/auth/ml/install`
2. Autoriza no ML
3. Callback automático cria seller com status `pending_approval`
4. Página de sucesso com confirmação

---

### GET /auth/ml/callback

Callback do OAuth2 do ML. Troca authorization code por tokens.

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `code` | string | Authorization code do ML |
| `state` | string | Slug do seller ou "_new_install" (self-service) |

**Response:**
```json
{
  "status": "success",
  "seller": "141air",
  "ml_user_id": 1963376627,
  "message": "Seller 141air connected! Token expires at 2026-02-20T10:30:42+00:00"
}
```

**Status codes:**
| Code | Descrição |
|------|-----------|
| 200 | OK — tokens salvos |
| 400 | State (seller) não informado |
| 502 | Falha no exchange de código ou fetch de user info |

---

## 3.5 Auth CA

### GET /auth/ca/connect

Redireciona para o formulário de login da Conta Azul (Cognito).

**Request:** (sem parâmetros)

**Response:**
HTTP 302 → Redireciona para `https://auth.contaazul.com/login`

---

### GET /auth/ca/callback

Callback do OAuth2 do CA. Troca authorization code por tokens globais.

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `code` | string | Authorization code do CA (Cognito) |
| `state` | string | Estado (ex: "ca_connect") |

**Response:**
HTML com página de sucesso

**Status codes:**
| Code | Descrição |
|------|-----------|
| 200 | OK — tokens salvos globalmente |
| 500 | CA_CLIENT_SECRET não configurado |
| 502 | Falha no exchange de código |

---

### GET /auth/ca/status

Verifica o status do token CA (global, única instância por API).

**Request:** (sem parâmetros)

**Response:**
```json
{
  "connected": true,
  "access_token_valid": true,
  "expires_in_seconds": 1847,
  "has_refresh_token": true,
  "message": "OK"
}
```

**Status codes:**
| Code | Descrição |
|------|-----------|
| 200 | OK |

---

## 3.6 Admin

### POST /admin/login

Autentica com senha admin e retorna session token (24h).

**Request:**
```json
{
  "password": "sua_senha_aqui"
}
```

**Response:**
```json
{
  "token": "token_urlsafe_base64"
}
```

**Status codes:**
| Code | Descrição |
|------|-----------|
| 200 | OK |
| 401 | Senha incorreta |

**Notas:** Primeira requisição com qualquer senha define a senha. Sessions são em memória (24h de duração).

---

### GET /admin/sellers

Lista todos os sellers.

**Headers:** `X-Admin-Token: {token}`

**Response:**
```json
[
  {
    "id": "seller-uuid",
    "slug": "141air",
    "name": "141 AIR",
    "email": "contato@141air.com.br",
    "active": true,
    "onboarding_status": "active",
    "integration_mode": "dashboard_ca",
    "ml_user_id": 1963376627,
    "ml_access_token": "...",
    "ml_refresh_token": "...",
    "ml_token_expires_at": "2026-02-20T10:30:42Z",
    "ca_conta_bancaria": "fea5f1de-...",
    "ca_centro_custo_variavel": "f7c214a6-...",
    "dashboard_empresa": "141 AIR",
    "dashboard_grupo": "FROTA",
    "dashboard_segmento": "VEICULOS",
    "ca_start_date": "2026-01-01",
    "ca_backfill_status": "completed",
    "ca_backfill_started_at": "2026-01-15T00:00:00Z",
    "ca_backfill_completed_at": "2026-01-17T15:30:00Z",
    "approved_at": "2026-01-01T00:00:00Z",
    "created_at": "2026-01-01T00:00:00Z"
  }
]
```

---

### GET /admin/sellers/pending

Lista sellers com status `pending_approval`.

**Headers:** `X-Admin-Token: {token}`

**Response:** Array de sellers (mesmo formato que GET /admin/sellers)

---

### POST /admin/sellers/{seller_id}/approve

Aprova um seller (pending_approval → approved) e cria revenue_line + 12 goals vazias.

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "dashboard_empresa": "141 AIR",
  "dashboard_grupo": "FROTA",
  "dashboard_segmento": "VEICULOS",
  "ca_conta_bancaria": "fea5f1de-fd23-4462-9b43-0a2c6ae4df04",
  "ca_centro_custo_variavel": "f7c214a6-be2f-11f0-8080-ab23c683d2a1",
  "ca_contato_ml": "b247cccb-38a2-4851-bf0e-700c53036c2c",
  "ml_app_id": "app-id-opcional",
  "ml_secret_key": "secret-opcional"
}
```

**Response:**
```json
{
  "id": "seller-uuid",
  "slug": "141air",
  "name": "141 AIR",
  "onboarding_status": "approved",
  "dashboard_empresa": "141 AIR",
  "ca_conta_bancaria": "fea5f1de-...",
  "created_at": "2026-01-01T00:00:00Z"
}
```

**Status codes:**
| Code | Descrição |
|------|-----------|
| 200 | OK |
| 401 | Token inválido |
| 404 | Seller não encontrado |

---

### POST /admin/sellers/{seller_id}/reject

Rejeita um seller (pending_approval → suspended).

**Headers:** `X-Admin-Token: {token}`

**Response:** Seller atualizado com `onboarding_status = "suspended"`

---

### PATCH /admin/sellers/{seller_id}

Atualiza campos de um seller.

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "name": "Novo nome",
  "dashboard_empresa": "NOVA EMPRESA",
  "ca_conta_bancaria": "novo-id-uuid"
}
```

**Response:** Seller atualizado

---

### GET /admin/revenue-lines

Lista todas as revenue lines.

**Headers:** `X-Admin-Token: {token}`

**Response:**
```json
[
  {
    "empresa": "141 AIR",
    "grupo": "FROTA",
    "segmento": "VEICULOS",
    "seller_id": "seller-uuid",
    "source": "ml",
    "active": true,
    "created_at": "2026-01-01T00:00:00Z"
  }
]
```

---

### POST /admin/revenue-lines

Cria uma revenue line.

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "empresa": "141 AIR",
  "grupo": "FROTA",
  "segmento": "VEICULOS",
  "source": "manual"
}
```

**Response:** Revenue line criada

---

### PATCH /admin/revenue-lines/{empresa}

Atualiza uma revenue line.

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "grupo": "NOVO GRUPO",
  "active": true
}
```

**Response:** Revenue line atualizada

---

### DELETE /admin/revenue-lines/{empresa}

Desativa uma revenue line (soft delete: `active = false`).

**Headers:** `X-Admin-Token: {token}`

**Response:** Revenue line com `active = false`

---

### GET /admin/goals?year=2026

Lista metas para um ano.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `year` | integer | 2026 | Ano |

**Response:**
```json
[
  {
    "empresa": "141 AIR",
    "grupo": "FROTA",
    "year": 2026,
    "month": 1,
    "valor": 50000.00
  }
]
```

---

### POST /admin/goals/bulk

Upsert (insert/update) metas em lote.

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "goals": [
    {
      "empresa": "141 AIR",
      "grupo": "FROTA",
      "year": 2026,
      "month": 1,
      "valor": 50000.00
    },
    {
      "empresa": "141 AIR",
      "grupo": "FROTA",
      "year": 2026,
      "month": 2,
      "valor": 55000.00
    }
  ]
}
```

**Response:**
```json
{
  "status": "ok",
  "count": 2
}
```

---

### POST /admin/sync/trigger

Dispara sync manual de faturamento (todos os sellers ativos).

**Headers:** `X-Admin-Token: {token}`

**Response:**
```json
{
  "last_sync": "2026-02-20T10:30:42Z",
  "results": [
    {
      "seller": "141air",
      "orders_count": 42,
      "novo_valor": 10750.33,
      "status": "ok"
    }
  ]
}
```

---

### GET /admin/sync/status

Retorna resultado do último sync de faturamento.

**Headers:** `X-Admin-Token: {token}`

**Response:** Mesmo formato que POST /admin/sync/trigger

---

### POST /admin/closing/trigger

Dispara financial closing manualmente.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `date_from` | string | D-1 | Data início `YYYY-MM-DD` |
| `date_to` | string | D-1 | Data fim `YYYY-MM-DD` |

**Response:**
```json
{
  "sellers_total": 5,
  "sellers_closed": 5,
  "sellers_open": 0,
  "date_from": "2026-02-19",
  "date_to": "2026-02-19",
  "results": [
    {
      "seller": "141air",
      "status": "closed",
      "auto_lane": {"ok": true, ...},
      "manual_lane": {"ok": true, ...}
    }
  ]
}
```

---

### GET /admin/closing/status

Retorna resultado do último financial closing.

**Headers:** `X-Admin-Token: {token}`

**Response:** Mesmo formato que POST /admin/closing/trigger

---

### GET /admin/closing/seller/{seller_slug}

Retorna closing detalhado para um seller.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `date_from` | string | D-1 | Data início |
| `date_to` | string | D-1 | Data fim |

**Response:**
```json
{
  "seller": "141air",
  "date_from": "2026-02-19",
  "date_to": "2026-02-19",
  "status": "closed",
  "auto_lane": {
    "ok": true,
    "payments_synced": 42,
    "payments_queued": 0,
    "payments_dead": 0,
    "jobs_completed": 125
  },
  "manual_lane": {
    "ok": true,
    "expenses_exported": 12,
    "expenses_imported": 12,
    "batches_imported": 2
  }
}
```

---

### POST /admin/release-report/sync

Sincroniza release report de um seller para mp_expenses.

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "seller": "141air",
  "begin_date": "2026-02-01",
  "end_date": "2026-02-15"
}
```

**Response:**
```json
{
  "seller": "141air",
  "period": "2026-02-01 to 2026-02-15",
  "rows_fetched": 143,
  "rows_classified": 135,
  "rows_skipped": 8,
  "expenses_created": 12,
  "errors": 0
}
```

---

### POST /admin/release-report/validate/{seller_slug}

Valida processor_fees contra release report e cria ajustes CA.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `begin_date` | string | Data início `YYYY-MM-DD` |
| `end_date` | string | Data fim `YYYY-MM-DD` |

**Response:**
```json
{
  "seller": "141air",
  "period": "2026-02-01 to 2026-02-15",
  "payments_validated": 74,
  "discrepancies_found": 3,
  "adjustments_created": 3,
  "total_adjustment_amount": 127.50,
  "details": [
    {
      "payment_id": 144359445042,
      "processor_fee": 25.44,
      "release_report_fee": 26.50,
      "difference": -1.06,
      "adjustment_created": true
    }
  ]
}
```

---

### POST /admin/release-report/validate-all

Valida fees para todos os sellers ativos (D-1 a D-{lookback_days}).

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `lookback_days` | integer | 3 | Dias para trás |

**Response:**
```json
{
  "count": 3,
  "total_adjustments": 8,
  "results": [
    {
      "seller": "141air",
      "adjustments_created": 3
    },
    {
      "seller": "netparts-sp",
      "adjustments_created": 5
    }
  ]
}
```

---

### GET /admin/release-report/validation-status

Retorna resultado da última validação de fees.

**Headers:** `X-Admin-Token: {token}`

**Response:** Mesmo formato que POST /admin/release-report/validate-all

---

### POST /admin/release-report/configure/{seller_slug}

Configura colunas do release report com fee breakdown.

**Headers:** `X-Admin-Token: {token}`

**Response:**
```json
{
  "status": "configured",
  "config": {
    "columns": ["id", "gross_amount", "mp_fee", "shipping_fee", ...]
  }
}
```

---

### GET /admin/release-report/config/{seller_slug}

Retorna configuração atual do release report.

**Headers:** `X-Admin-Token: {token}`

**Response:** Mesmo formato que POST /admin/release-report/configure/{seller_slug}

---

### GET /admin/extrato/coverage/{seller_slug}

Verifica cobertura do account_statement (extrato).

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `date_from` | string | Data início `YYYY-MM-DD` |
| `date_to` | string | Data fim `YYYY-MM-DD` |

**Response:**
```json
{
  "seller": "141air",
  "period": "2026-02-01 to 2026-02-15",
  "total_extrato_lines": 245,
  "covered_by_payments_api": 142,
  "covered_by_mp_expenses": 85,
  "covered_by_legacy": 18,
  "uncovered": 0,
  "coverage_pct": 100.0
}
```

---

### POST /admin/extrato/coverage-all

Verifica cobertura para todos os sellers ativos.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `lookback_days` | integer | 3 | Dias para trás |

**Response:**
```json
{
  "count": 3,
  "results": [
    {
      "seller": "141air",
      "coverage_pct": 100.0,
      "uncovered": 0
    }
  ]
}
```

---

### GET /admin/extrato/coverage-status

Retorna resultado da última verificação de cobertura.

**Headers:** `X-Admin-Token: {token}`

**Response:** Mesmo formato que POST /admin/extrato/coverage-all

---

### GET /admin/ca/contas-financeiras

Lista contas financeiras do CA.

**Headers:** `X-Admin-Token: {token}`

**Response:**
```json
[
  {
    "id": "fea5f1de-fd23-4462-9b43-0a2c6ae4df04",
    "nome": "Conta Corrente 141AIR",
    "tipo": "conta_corrente"
  }
]
```

---

### GET /admin/ca/centros-custo

Lista centros de custo do CA.

**Headers:** `X-Admin-Token: {token}`

**Response:**
```json
[
  {
    "id": "f7c214a6-be2f-11f0-8080-ab23c683d2a1",
    "descricao": "Centro de Custo 141AIR"
  }
]
```

---

### POST /admin/sellers/{slug}/activate

Ativa um seller com modo de integração (dashboard_only ou dashboard_ca).

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "integration_mode": "dashboard_ca",
  "name": "141 AIR",
  "dashboard_empresa": "141 AIR",
  "dashboard_grupo": "FROTA",
  "dashboard_segmento": "VEICULOS",
  "ca_conta_bancaria": "fea5f1de-...",
  "ca_centro_custo_variavel": "f7c214a6-...",
  "ca_start_date": "2026-01-01"
}
```

**Response:**
```json
{
  "status": "ok",
  "backfill_triggered": true
}
```

**Notas:**
- `dashboard_ca` requer CA config (conta, centro de custo, start_date)
- ca_start_date deve ser 1º de um mês (formato YYYY-MM-DD)
- Dispara onboarding backfill em background task

---

### POST /admin/sellers/{slug}/upgrade-to-ca

Migra seller de dashboard_only para dashboard_ca (adiciona CA config).

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "ca_conta_bancaria": "fea5f1de-...",
  "ca_centro_custo_variavel": "f7c214a6-...",
  "ca_start_date": "2026-01-01"
}
```

**Response:**
```json
{
  "status": "ok",
  "backfill_triggered": true
}
```

---

### GET /admin/sellers/{slug}/backfill-status

Retorna status e progresso do onboarding backfill.

**Headers:** `X-Admin-Token: {token}`

**Response:**
```json
{
  "seller": "141air",
  "status": "completed",
  "progress": {
    "payments_processed": 1234,
    "expenses_classified": 56,
    "errors": 0
  },
  "started_at": "2026-01-15T00:00:00Z",
  "completed_at": "2026-01-17T15:30:00Z"
}
```

---

### POST /admin/sellers/{slug}/backfill-retry

Re-dispara um onboarding backfill falho (idempotente).

**Headers:** `X-Admin-Token: {token}`

**Response:**
```json
{
  "status": "ok"
}
```

---

### GET /admin/onboarding/install-link

Retorna link para install OAuth ML (self-service).

**Headers:** `X-Admin-Token: {token}`

**Response:**
```json
{
  "url": "https://conciliador.levermoney.com.br/auth/ml/install"
}
```

---

### POST /admin/extrato/ingest/{seller_slug}

Ingere manualmente gaps do account_statement em mp_expenses.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `begin_date` | string | Data início `YYYY-MM-DD` |
| `end_date` | string | Data fim `YYYY-MM-DD` |

**Response:**
```json
{
  "seller": "141air",
  "period": "2026-02-01 to 2026-02-15",
  "extrato_lines_total": 78,
  "newly_ingested": 12,
  "already_covered": 66,
  "errors": 0
}
```

---

### POST /admin/extrato/ingest-all

Ingere gaps do account_statement para todos os sellers (D-1 a D-{lookback_days}).

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `lookback_days` | integer | 3 | Dias para trás |

**Response:**
```json
{
  "count": 3,
  "total_ingested": 45,
  "total_errors": 0,
  "results": [
    {"seller": "141air", "newly_ingested": 12},
    {"seller": "netparts-sp", "newly_ingested": 33}
  ]
}
```

---

### GET /admin/extrato/ingestion-status

Retorna resultado da última ingestão de extrato.

**Headers:** `X-Admin-Token: {token}`

**Response:** Mesmo formato que POST /admin/extrato/ingest-all

---

### POST /admin/legacy/daily/trigger

Dispara export legado manualmente (gera ZIP com XLSX).

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `seller_slug` | string | Se fornecido, roda apenas para este seller |
| `target_day` | string | `YYYY-MM-DD` (default: ontem BRT) |
| `upload` | boolean | Faz upload do ZIP (default: true) |

**Response (single seller):**
```json
{
  "mode": "single",
  "result": {
    "seller": "141air",
    "target_day": "2026-02-19",
    "ok": true,
    "file_path": "legacy_movimentos_141AIR_20260220_103042.zip",
    "upload_status": "ok"
  }
}
```

**Response (all sellers):**
```json
{
  "mode": "all",
  "count": 3,
  "ok": 3,
  "failed": 0,
  "results": [...]
}
```

---

### GET /admin/legacy/daily/status

Retorna status dos últimos exports legados.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `seller_slug` | string | Filter by seller (optional) |

**Response:**
```json
{
  "last_run": "2026-02-20T06:15:00Z",
  "sellers_total": 3,
  "sellers_ok": 3,
  "sellers_failed": 0,
  "results": [
    {
      "seller": "141air",
      "target_day": "2026-02-19",
      "ok": true,
      "file_path": "...",
      "file_size": 1024576,
      "run_at": "2026-02-20T06:15:00Z"
    }
  ]
}
```

---

## 3.7 Dashboard

### GET /dashboard/revenue-lines

Lista revenue lines ativas (público, sem autenticação).

**Response:**
```json
[
  {
    "empresa": "141 AIR",
    "grupo": "FROTA",
    "segmento": "VEICULOS",
    "seller_id": "seller-uuid",
    "source": "ml",
    "active": true,
    "created_at": "2026-01-01T00:00:00Z"
  }
]
```

---

### GET /dashboard/goals?year=2026

Lista metas para um ano (público, sem autenticação).

**Parâmetros (Query):**

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `year` | integer | 2026 | Ano |

**Response:**
```json
[
  {
    "empresa": "141 AIR",
    "grupo": "FROTA",
    "year": 2026,
    "month": 1,
    "valor": 50000.00
  }
]
```

---

### POST /dashboard/faturamento/entry

Upsert manual de entrada faturamento.

**Request:**
```json
{
  "empresa": "141 AIR",
  "date": "2026-02-20",
  "valor": 10750.33
}
```

**Response:**
```json
{
  "status": "ok"
}
```

---

### POST /dashboard/faturamento/delete

Delete manual de entrada faturamento.

**Request:**
```json
{
  "empresa": "141 AIR",
  "date": "2026-02-20"
}
```

**Response:**
```json
{
  "status": "ok"
}
```

---

## 3.8 Expenses

### GET /expenses/{seller_slug}

Lista mp_expenses de um seller com filtros opcionais.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `status` | string | Filter: `pending_review`, `auto_categorized`, `manually_categorized`, `exported` |
| `expense_type` | string | Filter: `bill_payment`, `subscription`, `transfer_pix`, etc. |
| `direction` | string | Filter: `expense`, `income`, `transfer` |
| `date_from` | string | Date range: `YYYY-MM-DD` |
| `date_to` | string | Date range: `YYYY-MM-DD` |
| `limit` | integer | Default: 100 (max: 500) |
| `offset` | integer | Default: 0 |

**Response:**
```json
{
  "seller": "141air",
  "count": 25,
  "offset": 0,
  "data": [
    {
      "id": 12345,
      "payment_id": "monthly-123456",
      "expense_type": "subscription",
      "expense_direction": "expense",
      "ca_category": "2.6.5",
      "auto_categorized": true,
      "amount": 199.00,
      "description": "Claude API subscription",
      "business_branch": "Tech",
      "operation_type": "service",
      "payment_method": "credit_card",
      "external_reference": "SUB-2026-02",
      "febraban_code": null,
      "date_created": "2026-02-20T10:30:42Z",
      "date_approved": "2026-02-20T10:30:42Z",
      "beneficiary_name": "Anthropic",
      "notes": "Auto-classificado",
      "status": "auto_categorized",
      "exported_at": null,
      "created_at": "2026-02-20T10:30:42Z"
    }
  ]
}
```

---

### PATCH /expenses/review/{seller_slug}/{expense_id}

Revisão manual e classificação de uma despesa (muda status para `manually_categorized`).

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "ca_category": "2.6.5",
  "description": "Novo texto",
  "notes": "Anotação manual",
  "beneficiary_name": "Novo beneficiário",
  "expense_type": "subscription",
  "expense_direction": "expense"
}
```

**Response:** Expense atualizado com `status = "manually_categorized"`

---

### GET /expenses/{seller_slug}/pending-summary

Resumo de despesas pendentes de revisão agrupadas por dia.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `date_from` | string | `YYYY-MM-DD` |
| `date_to` | string | `YYYY-MM-DD` |

**Response:**
```json
{
  "seller": "141air",
  "total_pending": 35,
  "by_day": [
    {
      "date": "2026-02-20",
      "count": 12,
      "amount_total": 2345.50,
      "payment_ids_sample": ["payment-123", "payment-124"]
    }
  ]
}
```

---

### GET /expenses/{seller_slug}/stats

Contadores de despesas por tipo, direção e status.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `date_from` | string | `YYYY-MM-DD` |
| `date_to` | string | `YYYY-MM-DD` |

**Response:**
```json
{
  "seller": "141air",
  "total": 125,
  "total_amount": 12450.75,
  "by_type": {
    "subscription": 34,
    "bill_payment": 12,
    "transfer_pix": 56,
    "cashback": 23
  },
  "by_direction": {
    "expense": 89,
    "income": 12,
    "transfer": 24
  },
  "by_status": {
    "auto_categorized": 98,
    "pending_review": 18,
    "manually_categorized": 6,
    "exported": 3
  }
}
```

---

### GET /expenses/{seller_slug}/export

Gera ZIP com XLSX (estrutura por dia e tipo).

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `date_from` | string | `YYYY-MM-DD` |
| `date_to` | string | `YYYY-MM-DD` |
| `status_filter` | string | Filter by status (default: não exported/imported) |
| `mark_exported` | boolean | Marcar como exported após gerar ZIP |

**Response:**
ZIP file com estrutura:
```
141AIR/
├── 2026-02-20/
│   ├── PAGAMENTO_CONTAS.xlsx
│   └── TRANSFERENCIAS.xlsx
├── 2026-02-21/
│   ├── PAGAMENTO_CONTAS.xlsx
│   └── TRANSFERENCIAS.xlsx
├── manifest.csv
└── manifest_pagamentos.csv
```

**Headers retornados:**
- `Content-Disposition: attachment; filename=despesas_141AIR_2026-02-01_2026-02-21.zip`
- `X-Export-Batch-Id: exp_abc123def456`

---

### GET /expenses/{seller_slug}/batches

Lista lotes de exportação.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `status` | string | Filter: `generated`, `exported`, `imported` |
| `limit` | integer | Default: 50 (max: 500) |

**Response:**
```json
{
  "seller": "141air",
  "count": 5,
  "data": [
    {
      "batch_id": "exp_abc123def456",
      "seller_slug": "141air",
      "company": "141 AIR",
      "status": "imported",
      "rows_count": 78,
      "amount_total_signed": -12450.75,
      "date_from": "2026-02-20",
      "date_to": "2026-02-21",
      "exported_at": "2026-02-20T15:30:00Z",
      "imported_at": "2026-02-20T16:15:00Z",
      "notes": "Importado com sucesso",
      "updated_at": "2026-02-20T16:15:00Z"
    }
  ]
}
```

---

### POST /expenses/{seller_slug}/batches/{batch_id}/confirm-import

Confirma importação de um lote no CA (status: `generated|exported` → `imported`).

**Headers:** `X-Admin-Token: {token}`

**Request:**
```json
{
  "imported_at": "2026-02-20T16:15:00Z",
  "notes": "Importado manualmente no CA"
}
```

**Response:**
```json
{
  "ok": true,
  "seller": "141air",
  "batch_id": "exp_abc123def456",
  "imported_rows": 78,
  "imported_at": "2026-02-20T16:15:00Z"
}
```

---

### GET /expenses/{seller_slug}/closing

Status de fechamento diário (cobertura por dia e status de export/import).

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Query):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `date_from` | string | `YYYY-MM-DD` |
| `date_to` | string | `YYYY-MM-DD` |
| `include_payment_ids` | boolean | Incluir listas completas de payment_ids |

**Response:**
```json
{
  "seller": "141air",
  "company": "141 AIR",
  "date_from": "2026-02-20",
  "date_to": "2026-02-21",
  "import_source": "batch_tables",
  "days": [
    {
      "date": "2026-02-20",
      "company": "141 AIR",
      "rows_total": 42,
      "rows_exported": 40,
      "rows_imported": 40,
      "rows_not_exported": 2,
      "rows_not_imported": 2,
      "amount_total_signed": -5625.50,
      "amount_exported_signed": -5500.00,
      "amount_imported_signed": -5500.00,
      "amount_diff_export_signed": -125.50,
      "amount_diff_import_signed": -125.50,
      "payment_ids_total": 38,
      "payment_ids_exported": 36,
      "payment_ids_imported": 36,
      "payment_ids_missing_export": 2,
      "payment_ids_missing_import": 2,
      "payment_ids_missing_export_sample": [12345, 12346],
      "payment_ids_missing_import_sample": [12345, 12346],
      "closed": false
    }
  ],
  "days_total": 2,
  "days_closed": 1,
  "days_open": 1,
  "all_closed": false
}
```

---

### POST /expenses/{seller_slug}/legacy-export

Bridge legado: roda reconciliação sobre arquivos MP e exporta XLSX.

**Headers:** `X-Admin-Token: {token}`

**Parâmetros (Multipart Form):**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `extrato` | File | Account statement (CSV ou ZIP) — obrigatório |
| `dinheiro` | File | Settlement report (CSV ou ZIP) — opcional |
| `vendas` | File | Collection report (CSV ou ZIP) — opcional |
| `pos_venda` | File | After-collection report (CSV ou ZIP) — opcional |
| `liberacoes` | File | Reserve-release report (CSV ou ZIP) — opcional |
| `centro_custo` | string | Override para nome do centro de custo — opcional |

**Response:**
ZIP file com estrutura:
```
CONTA_AZUL/
├── PAGAMENTO_CONTAS.xlsx
└── TRANSFERENCIAS.xlsx
RESUMO/
├── RESUMO_MOVIMENTOS.xlsx
└── RESUMO_DISCREPANCIAS.xlsx
OUTROS/
├── extrato_processado.csv
└── ...
```

**Headers retornados:**
- `Content-Disposition: attachment; filename=legacy_movimentos_141AIR_20260220_103042.zip`
- `X-Legacy-Centro-Custo: 141AIR`
- `X-Legacy-Pagamentos-Rows: 45`
- `X-Legacy-Transferencias-Rows: 12`

---

## 3.9 Queue

### GET /queue/status

Contagem de jobs por status na fila persistente.

**Response:**
```json
{
  "counts": {
    "pending": 12,
    "processing": 2,
    "completed": 1245,
    "failed": 3,
    "dead": 1
  }
}
```

---

### GET /queue/dead

Lista jobs em dead-letter (últimos 50).

**Response:**
```json
{
  "total": 1,
  "jobs": [
    {
      "id": "job-uuid",
      "seller_slug": "141air",
      "job_type": "receita",
      "ca_endpoint": "/v1/financeiro/eventos-financeiros/contas-a-receber",
      "ca_method": "POST",
      "group_id": "141air:144359445042",
      "status": "dead",
      "attempts": 3,
      "max_attempts": 3,
      "ca_response_status": 400,
      "ca_response_body": "{...}",
      "ca_protocolo": null,
      "last_error": "CA returned 400: invalid payload",
      "created_at": "2026-02-20T10:30:42Z",
      "updated_at": "2026-02-20T10:35:00Z"
    }
  ]
}
```

---

### POST /queue/retry/{job_id}

Retry manual de um job dead (volta para pending).

**Response:**
```json
{
  "ok": true,
  "job_id": "job-uuid"
}
```

---

### POST /queue/retry-all-dead

Retry de TODOS os jobs dead.

**Response:**
```json
{
  "ok": true,
  "retried": 5
}
```

---

### GET /queue/reconciliation/{seller_slug}

View operacional de reconciliação: payments vs jobs em aberto/erro.

**Parâmetros (Query):**

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `date_from` | string | - | `YYYY-MM-DD` |
| `date_to` | string | - | `YYYY-MM-DD` |
| `sample_limit` | integer | 200 | Max items (1-1000) |

**Response:**
```json
{
  "seller": "141air",
  "date_from": "2026-02-20",
  "date_to": "2026-02-21",
  "payments_total": 87,
  "payments_by_status": {
    "synced": 74,
    "queued": 6,
    "pending": 3,
    "refunded": 2,
    "skipped": 2
  },
  "payments_open_count": 9,
  "payments_open_sample": [144359445042, 144359445043, ...],
  "payments_with_error_count": 2,
  "payments_with_error_sample": [144359445100, 144359445101],
  "dead_job_payment_ids_count": 1,
  "dead_job_payment_ids_sample": [144359445100],
  "pending_job_payment_ids_count": 8,
  "pending_job_payment_ids_sample": [144359445042, 144359445043, ...],
  "not_fully_reconciled_count": 9,
  "not_fully_reconciled_sample": [144359445042, ...]
}
```

---

## 3.10 Health/Debug

### GET /health

Health check simples.

**Response:**
```json
{
  "status": "ok"
}
```

---

### GET /debug/ca-token

Testa refresh do token CA e retorna status.

**Response:**
```json
{
  "status": "ok",
  "token_prefix": "eyJhbGciOiJIUzI1Ni...",
  "token_len": 2048
}
```

---

### GET /debug/process-test

Testa processamento de 1 payment e retorna detalhes.

**Response:**
```json
{
  "db": "ok",
  "seller": {
    "found": true,
    "has_contato": true
  },
  "ml_payment": {
    "status": "approved",
    "amount": 284.74
  },
  "ca_token": "eyJhbGciOiJIUzI1Ni... (len=2048)",
  "payload": {...},
  "ca_status": 202,
  "ca_response": {
    "protocolo": "12345678",
    "status": "PENDING"
  }
}
```

---

### GET /debug/busca-parcela

Testa busca de parcelas no CA.

**Response:**
```json
{
  "status_code": 200,
  "response": {
    "dados": [
      {
        "id": "parcela-123",
        "descricao": "Comissão ML - Payment 144359445042",
        "total": 39.93,
        "nao_pago": 39.93,
        "status": "EM_ABERTO"
      }
    ]
  }
}
```

---

## Extrato Ingester — Classificação de Gaps

O módulo `extrato_ingester.py` ingere linhas do account_statement (release_report) que NÃO são cobertas pelos endpoints da Payments API ou pelas despesas classificadas automaticamente. Essas linhas são convertidas em registros `mp_expenses` para exportação XLSX.

### Gap Types Classificados

| Tipo | Código | Categoria CA | Direção | Status | Descrição |
|------|--------|--------------|---------|--------|-----------|
| **DIFAL** | `2.2.3` | Diferencial ICMS | expense | auto | Diferença de alíquota de ICMS por UF (estado) |
| **Faturas ML** | `2.8.2` | Comissões | expense | auto | Faturas vencidas do Mercado Livre não pagas |
| **Reembolso Disputa** | `1.3.4` | Estornos de Taxas | income | auto | Reembolso de reclamações/disputas do cliente |
| **Dinheiro Retido** | (pending review) | - | expense | manual | Dinheiro retido em disputa (não reconhecido como receita) |
| **Entrada Dinheiro** | (pending review) | - | income | manual | Entrada avulsa de crédito (necessário análise) |
| **Débito Envio ML** | `2.9.4` | MercadoEnvios | expense | auto | Cobrança retroativa de frete não cobrado |
| **Liberação Cancelada** | (pending review) | - | expense | manual | Reversão de liberação de dinheiro (interno MP) |
| **Reembolso Genérico** | `1.3.4` | Estornos de Taxas | income | auto | Reembolso genérico/arredondamento |
| **Depósito Avulso** | (pending review) | - | income | manual | Depósito avulso/aporte de dinheiro |
| **Débito Divida Disputa** | (pending review) | - | expense | manual | Debitação direta por reclamações no ML |
| **Débito Troca** | (pending review) | - | expense | manual | Debitação por troca de produto |
| **Bônus por Envio** | `1.3.7` | Estorno de Frete | income | auto | Bônus recebido pela execução de envios |

### Padrões de Classificação (Regras)

A classificação segue a ordem de prioridade abaixo (primeiro match vence):

**Skips (Já cobertos por outros pipelines):**
- "liberacao de dinheiro" (remoção = reversão interna)
- "transferencia pix" (PIX já coberto por API ou legacy)
- "pix enviado" (PIX enviado coberto por legacy)
- "pagamento de conta" (boleto coberto por expense_classifier)
- "pagamento com" (pagamento coberto por expense_classifier)
- "compra mercado libre" (compra no ML — não é receita)

**Income (Entradas de caixa):**
- "reembolso reclamacoes" / "reembolso reclamações" → `reembolso_disputa` (1.3.4)
- "reembolso envio cancelado" / "reembolso envío cancelado" → `reembolso_disputa` (1.3.4)
- "reembolso de tarifas" / "reembolso" → `reembolso_generico` (1.3.4)
- "entrada de dinheiro" → `entrada_dinheiro` (pending review)
- "dinheiro recebido" → `deposito_avulso` (pending review)
- "bônus por envio" / "bonus por envio" → `bonus_envio` (1.3.7)
- "transferencia recebida" / "transferência recebida" → `entrada_dinheiro` (pending review)

**Expenses (Saídas/Débitos):**
- "dinheiro retido" → `dinheiro_retido` (pending review)
- "diferenca da aliquota" / "difal" → `difal` (2.2.3)
- "faturas vencidas" → `faturas_ml` (2.8.2)
- "envio do mercado livre" → `debito_envio_ml` (2.9.4)
- "reclamacoes no mercado livre" / "reclamações no mercado livre" → `debito_divida_disputa` (pending review)
- "troca de produto" → `debito_troca` (pending review)
- "pagamento" (genérico, após regras específicas) → `subscription` (pending review)

### Deduplicação de Disputas

**Novo em v2.2.0:** O ingester detecta quando uma linha extrato do tipo `debito_divida_disputa` corresponde a um payment que já foi processado como `refunded` pelo processor.

**Comportamento:**
1. Processor cria estorno de receita (1.2.1 Devoluções) quando payment tem `ml_status = "refunded"`
2. Extrato também contém linha `debito_divida_disputa` para o mesmo payment_id
3. **Resultado:** Linha extrato é **SKIPPED** (status: `already_covered`)
4. **Efeito:** Evita double-counting da devolução no DRE

**Função:** `_batch_lookup_refunded_payment_ids(db, seller_slug, ref_ids)` busca payments com `ml_status = "refunded"` para deduplicação.

### Idempotência e Composite Keys

Quando a mesma `reference_id` aparece múltiplas vezes no extrato com tipos diferentes (ex: grupo de disputa com crédito + débito), o ingester gera `payment_id` composto:

```
payment_id = "{reference_id}:{expense_type_abbrev}"

Exemplos:
  "123456789:df"  (DIFAL)
  "123456789:dd"  (debito_divida_disputa)
  "123456789:rd"  (reembolso_disputa)
  "123456789:be"  (bonus_envio)
```

Isso permite rastreamento completo de todas as linhas sem colisões de chaves.

### Mapeamento de Categorias CA

| Código | UUID CA | Descrição |
|--------|---------|-----------|
| **1.3.4** | `c4cc890c-...` | Estornos de Taxas |
| **1.3.7** | `2c0ef767-...` | Estorno de Frete |
| **2.2.3** | `3b1acab2-9fd6-4fce-b9ac-d418c6355c5d` | DIFAL (Diferencial ICMS) |
| **2.8.2** | `699d6072-...` | Comissões Marketplace |
| **2.9.4** | `6ccbf8ed-...` | MercadoEnvios |

### Pipeline de Nightly

O ingester é acionado **após** o sync de payments (daily_sync) e **antes** da verificação de cobertura:

```
Nightly Pipeline (00:01 BRT):
  1. sync_all_sellers()                    ← Processa payments API
  2. validate_release_fees_all_sellers()   ← Valida fees
  3. ingest_extrato_all_sellers()          ← Ingere gaps (NEW)
  4. _run_baixas_all_sellers()             ← Cria baixas
  5. run_legacy_daily_for_all()            ← Export legado
  6. check_extrato_coverage_all_sellers()  ← Coverage = 100%
  7. _run_financial_closing()              ← Fechamento diário
```

### Endpoints de Ingestão Manual

Veja seção **3.6 Admin** para os endpoints:
- `POST /admin/extrato/ingest/{seller_slug}` — Ingere gaps manualmente
- `POST /admin/extrato/ingest-all` — Ingere para todos os sellers
- `GET /admin/extrato/ingestion-status` — Status da última ingestão

---

## Códigos de Erro

| Code | Descrição | Ação |
|------|-----------|------|
| 200 | OK | - |
| 201 | Created | - |
| 202 | Accepted (async) | Processo iniciado em background |
| 204 | No Content | - |
| 302 | Redirect | Siga o redirect |
| 400 | Bad Request | Revise parâmetros/payload |
| 401 | Unauthorized | Token inválido ou expirado |
| 403 | Forbidden | Sem permissão (seller status, etc.) |
| 404 | Not Found | Recurso não existe |
| 409 | Conflict | Recurso já existe ou estado inválido |
| 429 | Too Many Requests | Rate limit (tentar depois) |
| 500 | Internal Server Error | Erro do servidor (logar issue) |
| 502 | Bad Gateway | Erro na comunicação com ML/CA/DB |
| 503 | Service Unavailable | Serviço temporariamente indisponível |

---

## Exemplos curl

### 1. Login admin

```bash
curl -X POST https://conciliador.levermoney.com.br/admin/login \
  -H "Content-Type: application/json" \
  -d '{"password": "sua_senha"}'

# Retorna token para usar em requisições subsequentes
```

### 2. Listar sellers

```bash
curl https://conciliador.levermoney.com.br/admin/sellers \
  -H "X-Admin-Token: token_obtido_no_login"
```

### 3. Backfill (dry run)

```bash
curl "https://conciliador.levermoney.com.br/backfill/141air?begin_date=2026-02-01&end_date=2026-02-15&dry_run=true"
```

### 4. Backfill (processar)

```bash
curl "https://conciliador.levermoney.com.br/backfill/141air?begin_date=2026-02-01&end_date=2026-02-15&dry_run=false&concurrency=10"
```

### 5. Baixas (dry run)

```bash
curl "https://conciliador.levermoney.com.br/baixas/processar/141air?dry_run=true&verify_release=true"
```

### 6. Expenses export

```bash
curl "https://conciliador.levermoney.com.br/expenses/141air/export?date_from=2026-02-20&date_to=2026-02-21&mark_exported=true" \
  -H "X-Admin-Token: token_obtido_no_login" \
  -o despesas_141air.zip
```

### 7. Queue status

```bash
curl https://conciliador.levermoney.com.br/queue/status
```

### 8. Financial closing

```bash
curl -X POST "https://conciliador.levermoney.com.br/admin/closing/trigger?date_from=2026-02-19&date_to=2026-02-19" \
  -H "X-Admin-Token: token_obtido_no_login"
```

### 9. List revenue lines (público)

```bash
curl https://conciliador.levermoney.com.br/dashboard/revenue-lines
```

### 10. Get goals (público)

```bash
curl "https://conciliador.levermoney.com.br/dashboard/goals?year=2026"
```

---

## Competência de Devoluções: DRE vs Painel ML

O sistema e o painel do Mercado Livre usam critérios de competência diferentes para devoluções.
Isso gera divergência esperada que **não é bug**.

### Critério do Painel ML

O painel ML conta **todas** as devoluções de vendas do mês, independente de quando o estorno ocorreu.

Exemplo: venda aprovada em janeiro, devolvida em fevereiro → ML conta como devolução de janeiro.

### Critério do Nosso DRE (Competência da Devolução)

O DRE reconhece o estorno na **data em que o refund aconteceu** (`date_last_updated` do payment refundado, convertido para BRT).

Exemplo: venda aprovada em janeiro, devolvida em fevereiro → estorno entra no DRE de fevereiro.

### Consequência Prática

O DRE de um mês mostra **menos devoluções** que o painel ML, porque parte dos estornos de vendas daquele mês só ocorre nos meses seguintes.

### Fórmula de Reconciliação

```
Painel ML (devoluções de vendas do mês X)
  ≈ DRE mês X (estornos ocorridos em X)
  + DRE mês X+1 (estornos ocorridos em X+1 de vendas de X)
  + by_admin (kit splits — pulados pelo nosso sistema, ver seção 11.5b do CLAUDE.md)
```

### Referência Validada — Janeiro 2026

| Seller | Estorno total (todos os meses) | + by_admin (≈ Painel ML) | DRE jan (estornos em jan) | Diferido p/ DRE fev |
|--------|-------------------------------:|--------------------------:|--------------------------:|--------------------:|
| 141AIR | R$ 42.687 | R$ 43.043 | R$ 32.900 | R$ 9.787 |
| NET-AIR | R$ 155.239 | R$ 159.991 | R$ 93.136 | R$ 62.103 |
| NETPARTS-SP | R$ 107.485 | R$ 108.672 | R$ 65.334 | R$ 42.151 |
| EASY-UTIL | R$ 14.609 | R$ 15.029 | R$ 10.528 | R$ 4.081 |

### Notas

- **by_admin (kit split):** o ML conta como devolução, mas nosso sistema pula porque novos payments split já cobrem a receita
- **cancelled/rejected:** não entram como devolução em nenhum dos dois sistemas (nunca foram vendas aprovadas)
- **Diferença residual** (< R$ 200 por seller) vem de by_admin parciais e arredondamentos
- **Validado em:** 2026-02-20, dados de janeiro 2026

---

## Notas Importantes

### Autenticação
- **Webhooks:** Sem autenticação (webhook global, seller identificado por user_id)
- **OAuth2:** Fluxo automático (user redireciona, tokens salvos no servidor)
- **Admin:** X-Admin-Token header (session de 24h)
- **Dashboard:** Sem autenticação (endpoints públicos de leitura)

### Rate Limiting
- **CA API:** 9 req/s (burst), 540 req/min (guard)
- **ML API:** Respeitadas limits oficiais do ML (implementado automaticamente)
- Se receber 429: aguarde e tente depois

### Idempotência
- **Payments:** Upsert por `(seller_slug, ml_payment_id)` — seguro reprocessar
- **ca_jobs:** Unique constraint em `idempotency_key` — evita duplicação
- **Backfill:** Payments já processados são pulados automaticamente

### Datas
- **Formato:** `YYYY-MM-DD` para query params
- **Timezone:** BRT (UTC-3) para competência e vencimentos
- **API responses:** ISO 8601 com Z (UTC)

### Estrutura de Response
Todos os endpoints retornam **JSON**. Errors incluem `detail` com mensagem de erro:
```json
{
  "detail": "Seller not found"
}
```

---

**Última atualização:** 2026-02-20
**Versão da API:** 2.2.0

# API Conciliador V2/V3 - Lever Money

## Documentacao Completa da API

**Versao:** 2.2.0
**Dominio:** `conciliador.levermoney.com.br`
**Ultima atualizacao:** 2026-02-19

---

## Indice

1. [Visao Geral](#1-visao-geral)
2. [Arquitetura e Stack](#2-arquitetura-e-stack)
3. [Autenticacao e Autorizacao](#3-autenticacao-e-autorizacao)
4. [Variaveis de Ambiente](#4-variaveis-de-ambiente)
5. [Endpoints da API](#5-endpoints-da-api)
   - [5.1 Health e Debug](#51-health-e-debug)
   - [5.2 Webhooks](#52-webhooks)
   - [5.3 Backfill](#53-backfill)
   - [5.4 Baixas](#54-baixas)
   - [5.5 Auth ML (Mercado Livre)](#55-auth-ml-mercado-livre)
   - [5.6 Auth CA (Conta Azul)](#56-auth-ca-conta-azul)
   - [5.7 Admin](#57-admin)
   - [5.8 Dashboard](#58-dashboard)
   - [5.9 Queue](#59-queue)
   - [5.10 Expenses](#510-expenses)
6. [Modelos de Dados (Supabase)](#6-modelos-de-dados-supabase)
7. [Fluxos de Negocio](#7-fluxos-de-negocio)
   - [7.1 Payment -> Conta Azul](#71-payment---conta-azul)
   - [7.2 CaWorker (Fila Persistente)](#72-caworker-fila-persistente)
   - [7.3 Baixas (Liquidacao)](#73-baixas-liquidacao)
   - [7.4 Daily Sync](#74-daily-sync)
   - [7.5 Classificacao Non-Order (V3)](#75-classificacao-non-order-v3)
   - [7.6 Financial Closing](#76-financial-closing)
   - [7.7 Onboarding de Sellers](#77-onboarding-de-sellers)
8. [Background Tasks (Lifespan)](#8-background-tasks-lifespan)
9. [Regras de Negocio Criticas](#9-regras-de-negocio-criticas)
10. [Rate Limiting](#10-rate-limiting)
11. [Idempotencia e Resiliencia](#11-idempotencia-e-resiliencia)
12. [Categorias Conta Azul](#12-categorias-conta-azul)
13. [Deploy (Docker)](#13-deploy-docker)
14. [Dashboard (React SPA)](#14-dashboard-react-spa)
15. [Testes e Validacao](#15-testes-e-validacao)
16. [Historico de Correcoes](#16-historico-de-correcoes)

---

## 1. Visao Geral

Sistema de conciliacao automatica entre **Mercado Livre / Mercado Pago** e **Conta Azul ERP**. Para cada venda no ML, o sistema cria automaticamente no Conta Azul:

- **Receita** (contas-a-receber) com valor bruto da venda
- **Despesa comissao** (contas-a-pagar) com taxas ML/MP
- **Despesa frete** (contas-a-pagar) com custo MercadoEnvios
- **Baixas** automaticas quando o dinheiro e liberado pelo ML

### V3 - Classificacao de Pagamentos Non-Order

Pagamentos sem `order_id` (boletos, SaaS, cashback, transferencias) sao classificados automaticamente na tabela `mp_expenses` e exportados como XLSX para o financeiro importar no CA.

### Fonte de Caixa/Extrato

Usar **account_statement** (endpoints `release_report` / `bank_report`).
`settlement_report` NAO e a fonte oficial para fechamento diario de caixa.

### Mecanismo de Ingestao

Daily sync automatico as 00:01 BRT (D-1 a D-3). Webhooks continuam recebendo e logando, mas NAO processam payments diretamente -- o daily sync cuida de tudo.

### Funcionalidades Adicionais

- Dashboard de faturamento (React SPA)
- Sync periodico de faturamento
- Onboarding self-service de sellers
- Painel admin
- Financial closing (fechamento financeiro diario)
- Legacy daily export (ZIP com PAGAMENTO_CONTAS e TRANSFERENCIAS)
- Nightly pipeline (orquestracao sequencial de todos os processos)

---

## 2. Arquitetura e Stack

### Stack Tecnologico

| Camada | Tecnologia | Versao |
|--------|------------|--------|
| API | FastAPI | 0.115.6 |
| Python | Python | 3.12 |
| HTTP Client | httpx | 0.28.1 |
| Banco de Dados | Supabase (PostgreSQL) | via supabase-py 2.11.0 |
| Auth CA | OAuth2 / Cognito (token rotation) | - |
| Auth ML | OAuth2 / Mercado Pago | - |
| Settings | pydantic-settings | 2.7.1 |
| Planilhas | openpyxl | 3.1.5 |
| Criptografia | bcrypt | 4.2.1 |
| Dashboard | React 19 + Vite + TypeScript | SPA separada |
| Deploy | Docker multi-stage (Python + Nginx) | - |
| Dominio | conciliador.levermoney.com.br | - |

### Dependencias Python

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
python-dotenv==1.0.1
supabase==2.11.0
pydantic-settings==2.7.1
bcrypt==4.2.1
openpyxl==3.1.5
python-multipart==0.0.20
pandas==2.2.3
numpy==2.1.3
google-api-python-client==2.167.0
google-auth==2.40.3
```

### Estrutura do Projeto

```
lever money/
├── app/
│   ├── main.py                  # FastAPI app, lifespan, routers, SPA serve
│   ├── config.py                # Pydantic Settings (env vars)
│   ├── db/
│   │   └── supabase.py          # Singleton Supabase client
│   ├── models/
│   │   └── sellers.py           # CA categories, seller queries
│   ├── routers/
│   │   ├── webhooks.py          # POST /webhooks/ml
│   │   ├── backfill.py          # GET /backfill/{seller}
│   │   ├── baixas.py            # GET /baixas/processar/{seller}
│   │   ├── auth_ml.py           # OAuth ML (connect/callback/install)
│   │   ├── auth_ca.py           # OAuth CA (connect/callback/status)
│   │   ├── admin.py             # Admin CRUD (sellers, goals, sync)
│   │   ├── dashboard_api.py     # Dashboard read API (publico)
│   │   ├── expenses.py          # MP expenses (list, export, stats)
│   │   ├── queue.py             # Queue monitoring
│   │   └── health.py            # Health check + debug
│   ├── services/
│   │   ├── processor.py         # CORE: ML payment -> CA events
│   │   ├── ca_api.py            # CA HTTP client (retry, rate limit)
│   │   ├── ml_api.py            # ML/MP HTTP client (per-seller tokens)
│   │   ├── ca_queue.py          # Persistent job queue + CaWorker
│   │   ├── daily_sync.py        # Daily sync: backfill D-1..D-3
│   │   ├── expense_classifier.py# Classify non-order payments
│   │   ├── financial_closing.py # Fechamento financeiro
│   │   ├── faturamento_sync.py  # Polling ML orders -> faturamento
│   │   ├── release_checker.py   # Verifica money_release_status
│   │   ├── rate_limiter.py      # Token bucket (9 req/s)
│   │   ├── onboarding.py        # Seller signup/approve/activate
│   │   ├── onboarding_backfill.py # Backfill de ativacao (dashboard_ca)
│   │   ├── legacy_daily_export.py # Export legado diario
│   │   ├── legacy_bridge.py     # Bridge para formato legado
│   │   ├── legacy_engine.py     # Motor de reconciliacao legado
│   │   ├── release_report_sync.py # Sync release report -> mp_expenses
│   │   ├── release_report_validator.py # Valida fees e cria ajustes CA
│   │   ├── extrato_ingester.py  # Ingestao de lacunas do extrato -> mp_expenses
│   │   └── extrato_coverage_checker.py # Verifica cobertura total do extrato
│   └── static/
│       └── install.html         # Landing page self-service
├── dashboard/                   # React SPA (CLAUDE.md proprio)
├── Dockerfile                   # Multi-stage build
├── docker-compose.yml
├── requirements.txt
└── .env                         # Segredos (NAO commitar)
```

---

## 3. Autenticacao e Autorizacao

### 3.1 Admin API

Endpoints `/admin/*` e `/expenses/*` requerem o header `X-Admin-Token`.

**Obtencao do token:**

```
POST /admin/login
Content-Type: application/json

{
  "password": "sua_senha_admin"
}
```

**Resposta:**
```json
{
  "token": "AbCdEfGh1234567890..."
}
```

O token tem validade de **24 horas**. No primeiro login, a senha informada e armazenada como hash bcrypt na tabela `admin_config`.

**Uso do token:**
```
GET /admin/sellers
X-Admin-Token: AbCdEfGh1234567890...
```

**Erros:**
| Status | Detalhe |
|--------|---------|
| 401 | `Invalid or expired admin token` |
| 401 | `Invalid password` |
| 401 | `Session expired` |

### 3.2 OAuth Mercado Livre

Fluxo OAuth2 Authorization Code para conectar sellers ao ML.

**Fluxo padrao (seller existente):**
1. Admin cria seller no Supabase (ou via onboarding)
2. `GET /auth/ml/connect?seller={slug}` -> redirect para ML
3. Seller autoriza no ML
4. ML redireciona para `GET /auth/ml/callback?code=...&state={slug}`
5. API troca code por tokens e salva no Supabase
6. Seller ativado automaticamente

**Fluxo self-service (novo seller):**
1. Seller acessa `GET /auth/ml/install` -> redirect para ML
2. Seller autoriza no ML
3. ML redireciona para callback com `state=_new_install`
4. API cria seller com `pending_approval` e salva tokens
5. Admin aprova via `POST /admin/sellers/{id}/approve`

**Credenciais por seller:** Cada seller pode ter `ml_app_id` e `ml_secret_key` proprios, com fallback para as credenciais globais (`ML_APP_ID`, `ML_SECRET_KEY`).

**Token refresh:** Automatico. Quando o `access_token` expira, o servico faz refresh usando o `refresh_token` salvo no Supabase.

### 3.3 OAuth Conta Azul

Fluxo OAuth2 via AWS Cognito com token rotation habilitado.

**Fluxo:**
1. `GET /auth/ca/connect` -> redirect para `auth.contaazul.com/login`
2. Usuario faz login no CA
3. CA redireciona para `GET /auth/ca/callback?code=...`
4. API troca code por tokens (access + refresh)
5. Tokens salvos na tabela `ca_tokens` (id=1, single row)

**Endpoint de token:** Obrigatorio usar `https://auth.contaazul.com/oauth2/token` (NAO o endpoint direto do Cognito IDP).

**Token rotation:** A cada refresh, o CA retorna um NOVO refresh token. O servico salva ambos (access + refresh) a cada rotacao.

**Refresh proativo:** Background task a cada 30 minutos mantem tokens vivos.

**Lock de concorrencia:** `asyncio.Lock` previne race conditions no refresh simultaneo.

### 3.4 Endpoints Publicos (sem autenticacao)

| Endpoint | Descricao |
|----------|-----------|
| `GET /health` | Health check |
| `POST /webhooks/ml` | Receiver de webhooks ML/MP |
| `GET /dashboard/*` | API do dashboard de faturamento |
| `GET /backfill/{seller}` | Backfill de payments |
| `GET /baixas/processar/{seller}` | Processamento de baixas |
| `GET /queue/*` | Monitoramento da fila |
| `GET /auth/ml/*` | Fluxos OAuth ML |
| `GET /auth/ca/*` | Fluxos OAuth CA |
| `GET /debug/*` | Endpoints de debug |

---

## 4. Variaveis de Ambiente

Definidas via `.env` e lidas pelo `pydantic-settings` (classe `Settings` em `app/config.py`).

### Obrigatorias

| Variavel | Descricao | Exemplo |
|----------|-----------|---------|
| `ML_APP_ID` | App ID do Mercado Livre | `1234567890` |
| `ML_SECRET_KEY` | Secret key do app ML | `abc123...` |
| `ML_REDIRECT_URI` | URL de callback OAuth ML | `https://dominio/auth/ml/callback` |
| `CA_CLIENT_ID` | Cognito Client ID | `4dnledla42eblg...` |
| `CA_CLIENT_SECRET` | Cognito Client Secret | `abc123...` |
| `SUPABASE_URL` | URL do projeto Supabase | `https://xxx.supabase.co` |
| `SUPABASE_KEY` | Service role key do Supabase | `eyJ...` |

### Opcionais

| Variavel | Default | Descricao |
|----------|---------|-----------|
| `CA_ACCESS_TOKEN` | `""` | Token CA bootstrap |
| `CA_REFRESH_TOKEN` | `""` | Refresh token CA bootstrap |
| `BASE_URL` | `http://localhost:8000` | URL base para OAuth callbacks |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Origens CORS (comma-separated) |
| `SYNC_INTERVAL_MINUTES` | `5` | Intervalo sync faturamento |
| `DAILY_SYNC_NON_ORDER_MODE` | `classifier` | `classifier` ou `legacy` |
| `SELLER_ALLOWLIST` | `""` | Slugs permitidos (comma-separated, vazio = todos) |
| `EXPENSES_API_ENABLED` | `true` | Habilita router /expenses |
| `NIGHTLY_PIPELINE_ENABLED` | `false` | Habilita pipeline noturno unificado |
| `NIGHTLY_PIPELINE_HOUR_BRT` | `0` | Hora BRT do pipeline noturno |
| `NIGHTLY_PIPELINE_MINUTE_BRT` | `1` | Minuto BRT do pipeline noturno |
| `NIGHTLY_PIPELINE_LEGACY_WEEKDAYS` | `0,3` | Dias da semana para legacy export (0=Seg) |
| `LEGACY_DAILY_ENABLED` | `false` | Habilita scheduler legado |
| `LEGACY_DAILY_HOUR_BRT` | `6` | Hora BRT do export legado |
| `LEGACY_DAILY_MINUTE_BRT` | `15` | Minuto BRT do export legado |
| `LEGACY_DAILY_UPLOAD_MODE` | `http` | Modo de upload (`http` ou `gdrive`) |
| `LEGACY_DAILY_UPLOAD_URL` | `""` | URL para upload do ZIP legado |
| `LEGACY_DAILY_UPLOAD_TOKEN` | `""` | Bearer token para upload |

---

## 5. Endpoints da API

Base URL: `https://conciliador.levermoney.com.br`

### 5.1 Health e Debug

#### `GET /health`

Health check simples.

**Resposta 200:**
```json
{
  "status": "ok"
}
```

---

#### `GET /debug/ca-token`

Testa refresh do token Conta Azul.

**Resposta 200 (sucesso):**
```json
{
  "status": "ok",
  "token_prefix": "eyJraWQiOiJ3WFdkU...",
  "token_len": 1523
}
```

**Resposta 200 (erro):**
```json
{
  "status": "error",
  "error": "CA refresh token expirado...",
  "traceback": "..."
}
```

---

#### `GET /debug/process-test`

Testa processamento de 1 payment com dados reais do seller `141air`.

**Resposta 200:**
```json
{
  "db": "ok",
  "seller": {"found": true, "has_contato": true},
  "ml_payment": {"status": "approved", "amount": 284.74},
  "ca_token": "eyJ...",
  "payload": {...},
  "ca_status": 200,
  "ca_response": {"protocolo": "abc-123", "status": "PENDING"}
}
```

---

#### `GET /debug/busca-parcela`

Testa busca de parcelas abertas no Conta Azul.

**Resposta 200:**
```json
{
  "status_code": 200,
  "response": {"itens": [...], "itens_totais": 5}
}
```

---

### 5.2 Webhooks

#### `POST /webhooks/ml`

Receiver para webhooks do Mercado Livre / Mercado Pago. Loga o evento mas NAO processa payments (o daily sync cuida do processamento).

**URL configurada no app ML:** `https://conciliador.levermoney.com.br/webhooks/ml`

**Headers recebidos do ML:**
| Header | Descricao |
|--------|-----------|
| `x-signature` | HMAC-SHA256 assinatura (`ts=xxx,v1=xxx`) |
| `x-request-id` | ID da requisicao ML |

**Request Body (enviado pelo ML):**
```json
{
  "type": "payment",
  "action": "payment.created",
  "data": {
    "id": 144370799868
  },
  "user_id": 1963376627,
  "resource": "/v1/payments/144370799868"
}
```

**Resposta 200:**
```json
{
  "status": "ok"
}
```

**Comportamento:**
1. Valida assinatura HMAC-SHA256 (warning se invalida, aceita no MVP)
2. Identifica seller pelo `user_id`
3. Salva evento na tabela `webhook_events`
4. Para webhooks de payment: loga mas NAO processa (daily sync cuida)

**Resposta 400:**
```json
{
  "detail": "Invalid JSON"
}
```

---

### 5.3 Backfill

#### `GET /backfill/{seller_slug}`

Puxa payments do ML por periodo e processa no Conta Azul. Suporta modo dry_run para preview.

**Path Parameters:**
| Parametro | Tipo | Descricao |
|-----------|------|-----------|
| `seller_slug` | string | Slug do seller (ex: `141air`, `netparts-sp`) |

**Query Parameters:**
| Parametro | Tipo | Obrigatorio | Default | Descricao |
|-----------|------|-------------|---------|-----------|
| `begin_date` | string | Sim | - | Data inicio `YYYY-MM-DD` |
| `end_date` | string | Sim | - | Data fim `YYYY-MM-DD` |
| `dry_run` | bool | Nao | `true` | Se true, apenas lista sem processar |
| `max_process` | int | Nao | `0` | Maximo de payments a processar (0=todos) |
| `concurrency` | int | Nao | `10` | Payments processados em paralelo (1-20) |
| `reprocess_missing_fees` | bool | Nao | `true` | Reprocessa payments ja finalizados com `processor_fee`/`processor_shipping` nulos |

**Exemplo - Dry Run:**
```
GET /backfill/netparts-sp?begin_date=2026-02-01&end_date=2026-02-01&dry_run=true
```

**Resposta 200 (dry_run=true):**
```json
{
  "mode": "dry_run",
  "seller": "netparts-sp",
  "period": "2026-02-01 to 2026-02-01",
  "total_payments": 87,
  "total_amount": 12345.67,
  "by_status": {
    "approved": 74,
    "refunded": 4,
    "cancelled": 1,
    "pending": 8
  },
  "already_done": 50,
  "already_done_missing_fees": 8,
  "reprocess_missing_fees": true,
  "to_process_new": 16,
  "to_reprocess_missing_fees": 8,
  "to_process": 24,
  "concurrency": 10,
  "sample": [
    {
      "id": 143670186451,
      "status": "approved",
      "amount": 259.39,
      "date": "2026-02-01T10:30:00",
      "order_id": 2000006829820543,
      "net": 196.01
    }
  ]
}
```

**Exemplo - Processamento:**
```
GET /backfill/netparts-sp?begin_date=2026-02-01&end_date=2026-02-01&dry_run=false
```

**Resposta 200 (dry_run=false):**
```json
{
  "mode": "process",
  "seller": "netparts-sp",
  "period": "2026-02-01 to 2026-02-01",
  "total_found": 87,
  "already_done": 50,
  "already_done_missing_fees": 8,
  "to_process_new": 16,
  "to_reprocess_missing_fees": 8,
  "processed": 24,
  "errors": 0,
  "remaining": 0,
  "results": [
    {"id": 143670186451, "status": "ok"},
    {"id": 143670186452, "status": "ok"}
  ]
}
```

**Resposta 200 (seller nao encontrado):**
```json
{
  "error": "Seller xxx not found"
}
```

**Filtros aplicados automaticamente:**
- Payment deve ter `order_id` (non-order payments sao ignorados)
- Status deve ser `approved`, `refunded`, `in_mediation` ou `charged_back`
- Payments ja processados (synced, queued, refunded, skipped) sao ignorados, exceto quando `reprocess_missing_fees=true` e faltam campos de fee
- Payments com `description == "marketplace_shipment"` sao ignorados
- Payments com `collector.id` (compras, nao vendas) sao ignorados

---

### 5.4 Baixas

#### `GET /baixas/processar/{seller_slug}`

Busca parcelas abertas no Conta Azul com vencimento <= data_ate e cria baixas. Verifica `money_release_status` no ML antes de cada baixa.

**Path Parameters:**
| Parametro | Tipo | Descricao |
|-----------|------|-----------|
| `seller_slug` | string | Slug do seller |

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `dry_run` | bool | `true` | Se true, apenas lista sem criar baixas |
| `verify_release` | bool | `true` | Verifica `money_release_status` no ML |
| `data_ate` | string | hoje | Data limite `YYYY-MM-DD` |
| `lookback_days` | int | `90` | Dias para tras na busca de parcelas |

**Exemplo:**
```
GET /baixas/processar/141air?dry_run=true&verify_release=true
```

**Resposta 200 (dry_run=true):**
```json
{
  "mode": "dry_run",
  "seller": "141air",
  "data_de": "2025-11-20",
  "data_ate": "2026-02-18",
  "conta_bancaria": "fea5f1de-fd23-4462-9b43-0a2c6ae4df04",
  "verify_release": true,
  "parcelas_pagar": {
    "total": 15,
    "itens": [
      {
        "id": "abc-123",
        "descricao": "Comissao ML - Payment 144370799868",
        "data_vencimento": "2026-02-17",
        "total": 25.44,
        "nao_pago": 25.44,
        "status": "EM_ABERTO",
        "release_status": "released"
      }
    ]
  },
  "parcelas_receber": {
    "total": 15,
    "itens": [...]
  },
  "skipped_pagar": {
    "total": 3,
    "motivo": "money_release_status != released",
    "itens": [...]
  },
  "skipped_receber": {
    "total": 1,
    "motivo": "money_release_status != released",
    "itens": [...]
  }
}
```

**Resposta 200 (dry_run=false):**
```json
{
  "mode": "process",
  "seller": "141air",
  "data_de": "2025-11-20",
  "data_ate": "2026-02-18",
  "verify_release": true,
  "pagar": {
    "total": 15,
    "queued": 15,
    "errors": 0,
    "results": [
      {
        "id": "abc-123",
        "tipo": "pagar",
        "descricao": "Comissao ML - Payment 144370799868",
        "valor": 25.44,
        "data_vencimento": "2026-02-17",
        "status": "queued"
      }
    ]
  },
  "receber": {
    "total": 15,
    "queued": 15,
    "errors": 0,
    "results": [...]
  }
}
```

**Release status possivel:**
| Status | Significado | Acao |
|--------|-------------|------|
| `released` | Dinheiro liberado pelo ML | Processar baixa |
| `bypass` | Devolucao/estorno (nao precisa verificar) | Processar baixa |
| `unknown` | Nao encontrou payment correspondente | Processar baixa (conservador) |
| `pending` | Dinheiro ainda nao liberado | Pular baixa |

---

### 5.5 Auth ML (Mercado Livre)

#### `GET /auth/ml/connect`

Redireciona o seller para autorizar no Mercado Livre.

**Query Parameters:**
| Parametro | Tipo | Obrigatorio | Descricao |
|-----------|------|-------------|-----------|
| `seller` | string | Sim | Slug do seller existente |

**Exemplo:**
```
GET /auth/ml/connect?seller=141air
```

**Resposta 302:** Redirect para `https://auth.mercadolivre.com.br/authorization?...`

**Resposta 404:**
```json
{
  "detail": "Seller '141air' not found in database"
}
```

**Resposta 403:**
```json
{
  "detail": "Seller 'xxx' is not approved (status=suspended)"
}
```

---

#### `GET /auth/ml/install`

Self-service install flow. Redireciona para OAuth ML sem exigir seller pre-existente.

**Resposta 302:** Redirect para `https://auth.mercadolivre.com.br/authorization?...`

---

#### `GET /auth/ml/callback`

Callback do OAuth ML. Troca code por tokens e salva no Supabase.

**Query Parameters:**
| Parametro | Tipo | Descricao |
|-----------|------|-----------|
| `code` | string | Authorization code do ML |
| `state` | string | Slug do seller ou `_new_install` |

**Resposta 200 (seller existente):**
```json
{
  "status": "success",
  "seller": "141air",
  "ml_user_id": 1963376627,
  "message": "Seller 141air connected! Token expires at 2026-02-18T18:30:00+00:00"
}
```

**Resposta 200 (self-service install):** HTML page com confirmacao.

---

### 5.6 Auth CA (Conta Azul)

#### `GET /auth/ca/connect`

Redireciona para login no Conta Azul.

**Resposta 302:** Redirect para `https://auth.contaazul.com/login?...`

---

#### `GET /auth/ca/callback`

Callback do OAuth CA. Troca code por tokens e salva no Supabase.

**Query Parameters:**
| Parametro | Tipo | Descricao |
|-----------|------|-----------|
| `code` | string | Authorization code do CA |

**Resposta 200:** HTML page com confirmacao "Conta Azul conectada!"

---

#### `GET /auth/ca/status`

Verifica status dos tokens CA.

**Resposta 200 (conectado):**
```json
{
  "connected": true,
  "access_token_valid": true,
  "expires_in_seconds": 2847,
  "has_refresh_token": true,
  "message": "OK"
}
```

**Resposta 200 (desconectado):**
```json
{
  "connected": false,
  "message": "Nenhum token encontrado. Conecte via /auth/ca/connect"
}
```

---

### 5.7 Admin

Todos os endpoints requerem header `X-Admin-Token`. Obtenha via `POST /admin/login`.

#### `POST /admin/login`

Autentica com senha admin.

**Request Body:**
```json
{
  "password": "sua_senha"
}
```

**Resposta 200:**
```json
{
  "token": "AbCdEfGh1234567890_session_token..."
}
```

---

#### `GET /admin/sellers`

Lista todos os sellers cadastrados.

**Resposta 200:**
```json
[
  {
    "slug": "141air",
    "name": "141AIR",
    "active": true,
    "onboarding_status": "active",
    "ml_user_id": 1963376627,
    "dashboard_empresa": "141AIR",
    "dashboard_grupo": "NETAIR",
    "dashboard_segmento": "AR CONDICIONADO",
    "ca_conta_bancaria": "fea5f1de-...",
    "ca_centro_custo_variavel": "f7c214a6-...",
    "created_at": "2026-01-01T00:00:00"
  }
]
```

---

#### `GET /admin/sellers/pending`

Lista sellers aguardando aprovacao.

**Resposta 200:**
```json
[
  {
    "slug": "novo-seller",
    "name": "Novo Seller",
    "onboarding_status": "pending_approval",
    "ml_user_id": 123456
  }
]
```

---

#### `POST /admin/sellers/{seller_id}/approve`

Aprova seller com configuracao completa.

**Path Parameters:**
| Parametro | Tipo | Descricao |
|-----------|------|-----------|
| `seller_id` | string | ID do seller (UUID) |

**Request Body:**
```json
{
  "dashboard_empresa": "NOVO SELLER",
  "dashboard_grupo": "NETAIR",
  "dashboard_segmento": "AR CONDICIONADO",
  "ca_conta_bancaria": "fea5f1de-...",
  "ca_centro_custo_variavel": "f7c214a6-...",
  "ca_contato_ml": "b247cccb-...",
  "ml_app_id": "1234567890",
  "ml_secret_key": "abc123..."
}
```

| Campo | Tipo | Obrigatorio | Descricao |
|-------|------|-------------|-----------|
| `dashboard_empresa` | string | Sim | Nome da empresa no dashboard |
| `dashboard_grupo` | string | Nao | Grupo (default: "OUTROS") |
| `dashboard_segmento` | string | Nao | Segmento (default: "OUTROS") |
| `ca_conta_bancaria` | string | Nao | UUID da conta bancaria CA |
| `ca_centro_custo_variavel` | string | Nao | UUID do centro de custo CA |
| `ca_contato_ml` | string | Nao | UUID do contato ML no CA |
| `ml_app_id` | string | Nao | App ID ML do seller |
| `ml_secret_key` | string | Nao | Secret key ML do seller |

**Efeitos colaterais:**
- Cria `revenue_line` para o `dashboard_empresa`
- Cria 12 `goals` com valor=0 para o ano corrente (se nao existem)
- Se seller ja tem tokens ML, ativa automaticamente

---

#### `POST /admin/sellers/{seller_id}/reject`

Rejeita seller pendente (status -> `suspended`).

---

#### `PATCH /admin/sellers/{seller_id}`

Atualiza campos de um seller.

**Request Body (todos opcionais):**
```json
{
  "name": "Novo Nome",
  "dashboard_empresa": "EMPRESA",
  "dashboard_grupo": "GRUPO",
  "dashboard_segmento": "SEGMENTO",
  "ca_conta_bancaria": "uuid",
  "ca_centro_custo_variavel": "uuid",
  "ca_contato_ml": "uuid",
  "ml_app_id": "id",
  "ml_secret_key": "secret"
}
```

---

#### `GET /admin/revenue-lines`

Lista todas as revenue lines.

---

#### `POST /admin/revenue-lines`

Cria nova revenue line.

**Request Body:**
```json
{
  "empresa": "NOVA EMPRESA",
  "grupo": "NETAIR",
  "segmento": "AR CONDICIONADO",
  "source": "manual"
}
```

---

#### `PATCH /admin/revenue-lines/{empresa}`

Atualiza revenue line.

---

#### `DELETE /admin/revenue-lines/{empresa}`

Desativa revenue line (soft delete: `active=false`).

---

#### `GET /admin/goals`

Lista metas do ano.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `year` | int | `2026` | Ano das metas |

---

#### `POST /admin/goals/bulk`

Upsert de metas em lote.

**Request Body:**
```json
{
  "goals": [
    {
      "empresa": "141AIR",
      "grupo": "NETAIR",
      "year": 2026,
      "month": 1,
      "valor": 150000.00
    },
    {
      "empresa": "141AIR",
      "grupo": "NETAIR",
      "year": 2026,
      "month": 2,
      "valor": 160000.00
    }
  ]
}
```

---

#### `POST /admin/sync/trigger`

Trigger manual do sync de faturamento.

**Resposta 200:**
```json
{
  "results": [
    {
      "seller": "141air",
      "valor": 15234.50,
      "order_count": 42,
      "fraud_skipped": 0
    }
  ]
}
```

---

#### `GET /admin/sync/status`

Status do ultimo sync de faturamento.

**Resposta 200:**
```json
{
  "last_sync": "2026-02-18T03:01:00",
  "results": [...]
}
```

---

#### `POST /admin/legacy/daily/trigger`

Trigger manual do export legado diario.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `seller_slug` | string | null | Seller especifico (null = todos) |
| `target_day` | string | ontem BRT | Data `YYYY-MM-DD` |
| `upload` | bool | `true` | Upload do ZIP |

---

#### `GET /admin/legacy/daily/status`

Status dos ultimos exports legados.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `seller_slug` | string | null | Filtrar por seller |

---

#### `POST /admin/closing/trigger`

Trigger manual do financial closing.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `date_from` | string | ontem BRT | Data inicio `YYYY-MM-DD` |
| `date_to` | string | ontem BRT | Data fim `YYYY-MM-DD` |

---

#### `GET /admin/closing/status`

Resultado do ultimo financial closing.

Quando o coverage do extrato ja foi executado, o resumo inclui:
`extrato_coverage.ran_at`, `extrato_coverage.sellers_checked`, `extrato_coverage.sellers_100pct`.

---

#### `GET /admin/closing/seller/{seller_slug}`

Financial closing detalhado por seller.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `date_from` | string | null | Data inicio |
| `date_to` | string | null | Data fim |

**Resposta 200:**
```json
{
  "seller": "141air",
  "company": "141AIR",
  "auto": {
    "payments_total": 150,
    "payments_by_status": {"synced": 140, "queued": 5, "skipped": 5},
    "open_payment_ids_count": 5,
    "dead_job_payment_ids_count": 0,
    "unresolved_payment_ids_count": 5
  },
  "manual": {
    "import_source": "batch_tables",
    "days_total": 30,
    "days_closed": 28,
    "days_open": 2,
    "missing_import_payment_ids_count": 3
  },
  "closed": false
}
```

---

#### `POST /admin/release-report/sync`

Sync de release report para mp_expenses.

**Request Body:**
```json
{
  "seller": "141air",
  "begin_date": "2026-02-01",
  "end_date": "2026-02-15"
}
```

---

#### `POST /admin/release-report/validate/{seller_slug}`

Valida taxas do processor (`processor_fee`, `processor_shipping`) contra colunas do release report (`MP_FEE_AMOUNT`, `SHIPPING_FEE_AMOUNT`) e cria ajustes no CA quando houver diferenca.

**Query Parameters:**
| Parametro | Tipo | Obrigatorio | Descricao |
|-----------|------|-------------|-----------|
| `begin_date` | string | Sim | Data inicio `YYYY-MM-DD` |
| `end_date` | string | Sim | Data fim `YYYY-MM-DD` |

**Resposta 200:**
```json
{
  "seller": "141air",
  "total_rows": 1240,
  "payment_rows": 530,
  "adjustments_created": 17,
  "payments_adjusted": 12,
  "already_adjusted": 3,
  "not_in_payments": 4,
  "no_diff": 511,
  "fee_overcharged": 2,
  "breakdown": {
    "fee_adjustments": 12,
    "shipping_adjustments": 5
  }
}
```

---

#### `POST /admin/release-report/validate-all`

Valida fees para todos os sellers ativos no range D-1 a D-`lookback_days`.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `lookback_days` | int | `3` | Dias para tras a partir de ontem (BRT) |

**Resposta 200:**
```json
{
  "count": 4,
  "total_adjustments": 23,
  "results": [
    {"seller": "141air", "adjustments_created": 17},
    {"seller": "netparts-sp", "adjustments_created": 6}
  ]
}
```

---

#### `GET /admin/release-report/validation-status`

Retorna o resultado da ultima validacao em memoria.

**Resposta 200:**
```json
{
  "ran_at": "2026-02-19T03:12:18.220000+00:00",
  "results": [
    {"seller": "141air", "adjustments_created": 17}
  ]
}
```

---

#### `POST /admin/release-report/configure/{seller_slug}`

Configura colunas do release report no Mercado Pago (incluindo fee breakdown).

**Resposta 200:**
```json
{
  "status": "configured",
  "config": {
    "separator": ";",
    "display_timezone": "GMT-03",
    "report_translation": "pt",
    "columns": ["DATE", "SOURCE_ID", "MP_FEE_AMOUNT", "SHIPPING_FEE_AMOUNT"]
  }
}
```

---

#### `GET /admin/release-report/config/{seller_slug}`

Consulta configuracao atual do release report no MP.

---

#### `GET /admin/extrato/coverage/{seller_slug}`

Verifica cobertura do extrato (release report) por `payments` + `mp_expenses`.

**Query Parameters:**
| Parametro | Tipo | Obrigatorio | Descricao |
|-----------|------|-------------|-----------|
| `date_from` | string | Sim | Data inicio `YYYY-MM-DD` |
| `date_to` | string | Sim | Data fim `YYYY-MM-DD` |

**Resposta 200:**
```json
{
  "seller": "141air",
  "total_lines": 920,
  "covered_by_api": 610,
  "covered_by_expenses": 280,
  "covered_by_internal": 20,
  "uncovered": 10,
  "coverage_pct": 98.91,
  "uncovered_lines": [
    {
      "source_id": "144370799868",
      "description": "payment",
      "reason": "payment_not_tracked"
    }
  ]
}
```

---

#### `POST /admin/extrato/coverage-all`

Roda coverage check para todos os sellers ativos.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `lookback_days` | int | `3` | Dias para tras a partir de ontem (BRT) |

---

#### `GET /admin/extrato/coverage-status`

Retorna o ultimo resultado agregado de coverage check.

---

#### `POST /admin/sellers/{slug}/activate`

Ativa seller no onboarding v2 com modo de integracao.

**Request Body:**
```json
{
  "integration_mode": "dashboard_ca",
  "name": "141AIR",
  "dashboard_empresa": "141AIR",
  "dashboard_grupo": "NETAIR",
  "dashboard_segmento": "AR CONDICIONADO",
  "ca_conta_bancaria": "fea5f1de-...",
  "ca_centro_custo_variavel": "f7c214a6-...",
  "ca_start_date": "2026-01-01"
}
```

**Regras:**
- `integration_mode` deve ser `dashboard_only` ou `dashboard_ca`.
- Em `dashboard_ca`, `ca_conta_bancaria`, `ca_centro_custo_variavel` e `ca_start_date` sao obrigatorios.
- `ca_start_date` deve ser o **primeiro dia do mes**.

**Resposta 200:**
```json
{
  "status": "ok",
  "backfill_triggered": true
}
```

---

#### `POST /admin/sellers/{slug}/upgrade-to-ca`

Migra seller ativo de `dashboard_only` para `dashboard_ca` e dispara onboarding backfill.

**Request Body:**
```json
{
  "ca_conta_bancaria": "fea5f1de-...",
  "ca_centro_custo_variavel": "f7c214a6-...",
  "ca_start_date": "2026-01-01"
}
```

---

#### `GET /admin/sellers/{slug}/backfill-status`

Retorna status/progresso do onboarding backfill (`ca_backfill_*`).

**Resposta 200:**
```json
{
  "ca_backfill_status": "running",
  "ca_backfill_started_at": "2026-02-19T03:20:00+00:00",
  "ca_backfill_completed_at": null,
  "ca_backfill_progress": {
    "total": 520,
    "processed": 450,
    "orders_processed": 380,
    "expenses_classified": 60,
    "skipped": 10,
    "errors": 0,
    "baixas_created": 320,
    "last_payment_id": 144370799868
  }
}
```

---

#### `POST /admin/sellers/{slug}/backfill-retry`

Re-dispara backfill com comportamento idempotente (retoma sem duplicar eventos).

**Resposta 200:**
```json
{
  "status": "ok"
}
```

---

#### `GET /admin/onboarding/install-link`

Retorna link de install do OAuth ML para compartilhar com novos sellers.

**Resposta 200:**
```json
{
  "url": "https://conciliador.levermoney.com.br/auth/ml/install"
}
```

---

#### `POST /admin/extrato/ingest/{seller_slug}`

Ingere linhas de extrato (`account_statement`) nao cobertas por `payments`/`mp_expenses`.

**Query Parameters:**
| Parametro | Tipo | Obrigatorio | Descricao |
|-----------|------|-------------|-----------|
| `begin_date` | string | Sim | Data inicio `YYYY-MM-DD` |
| `end_date` | string | Sim | Data fim `YYYY-MM-DD` |

**Resposta 200:**
```json
{
  "seller": "141air",
  "total_lines": 920,
  "skipped_internal": 640,
  "already_covered": 220,
  "newly_ingested": 60,
  "errors": 0,
  "by_type": {
    "difal": 12,
    "faturas_ml": 8
  }
}
```

---

#### `POST /admin/extrato/ingest-all`

Roda ingestao de extrato para todos os sellers ativos no range D-1 a D-`lookback_days`.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `lookback_days` | int | `3` | Dias para tras a partir de ontem (BRT) |

**Resposta 200:**
```json
{
  "count": 4,
  "total_ingested": 113,
  "total_errors": 0,
  "results": [
    {"seller": "141air", "newly_ingested": 60},
    {"seller": "netparts-sp", "newly_ingested": 53}
  ]
}
```

---

#### `GET /admin/extrato/ingestion-status`

Retorna o ultimo resultado agregado da ingestao de extrato.

---

#### `GET /admin/ca/contas-financeiras`

Lista contas financeiras do Conta Azul.

**Resposta 200:**
```json
[
  {"id": "fea5f1de-...", "nome": "MERCADO PAGO 141AIR", "tipo": "CONTA_CORRENTE"}
]
```

---

#### `GET /admin/ca/centros-custo`

Lista centros de custo do Conta Azul.

**Resposta 200:**
```json
[
  {"id": "f7c214a6-...", "descricao": "141AIR Variavel"}
]
```

---

### 5.8 Dashboard

Endpoints publicos usados pelo dashboard React SPA.

#### `GET /dashboard/revenue-lines`

Lista linhas de receita ativas.

**Resposta 200:**
```json
[
  {
    "empresa": "141AIR",
    "grupo": "NETAIR",
    "segmento": "AR CONDICIONADO",
    "active": true,
    "source": "sync",
    "created_at": "2026-01-01T00:00:00"
  }
]
```

---

#### `GET /dashboard/goals`

Metas do ano.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `year` | int | `2026` | Ano das metas |

**Resposta 200:**
```json
[
  {
    "empresa": "141AIR",
    "grupo": "NETAIR",
    "year": 2026,
    "month": 1,
    "valor": 150000.00
  }
]
```

---

#### `POST /dashboard/faturamento/entry`

Upsert manual de entrada de faturamento.

**Request Body:**
```json
{
  "empresa": "141AIR",
  "date": "2026-02-18",
  "valor": 5234.50
}
```

**Resposta 200:**
```json
{
  "status": "ok"
}
```

---

#### `POST /dashboard/faturamento/delete`

Delete de entrada de faturamento.

**Request Body:**
```json
{
  "empresa": "141AIR",
  "date": "2026-02-18"
}
```

**Resposta 200:**
```json
{
  "status": "ok"
}
```

---

### 5.9 Queue

Monitoramento da fila de jobs CA.

#### `GET /queue/status`

Contagem de jobs por status.

**Resposta 200:**
```json
{
  "counts": {
    "pending": 5,
    "processing": 1,
    "completed": 1500,
    "failed": 2,
    "dead": 0
  }
}
```

---

#### `GET /queue/dead`

Lista dead-letter jobs (limite: 50 mais recentes).

**Resposta 200:**
```json
{
  "total": 2,
  "jobs": [
    {
      "id": "uuid-123",
      "seller_slug": "141air",
      "job_type": "receita",
      "ca_endpoint": "https://api-v2.contaazul.com/...",
      "ca_method": "POST",
      "ca_payload": {...},
      "group_id": "141air:144370799868",
      "status": "dead",
      "attempts": 3,
      "last_error": "400: {\"mensagem\":\"Erro de validacao\"}",
      "created_at": "2026-02-18T00:00:00"
    }
  ]
}
```

---

#### `POST /queue/retry/{job_id}`

Retry manual de um job dead.

**Path Parameters:**
| Parametro | Tipo | Descricao |
|-----------|------|-----------|
| `job_id` | string (UUID) | ID do job |

**Resposta 200 (sucesso):**
```json
{
  "ok": true,
  "job_id": "uuid-123"
}
```

**Resposta 200 (falha):**
```json
{
  "ok": false,
  "error": "Job not found or not in dead status"
}
```

---

#### `POST /queue/retry-all-dead`

Reset de todos os dead jobs para pending.

**Resposta 200:**
```json
{
  "ok": true,
  "retried": 5
}
```

---

#### `GET /queue/reconciliation/{seller_slug}`

Visao de reconciliacao operacional por seller.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `date_from` | string | null | Data inicio `YYYY-MM-DD` |
| `date_to` | string | null | Data fim `YYYY-MM-DD` |
| `sample_limit` | int | `200` | Limite de IDs retornados (1-1000) |

**Resposta 200:**
```json
{
  "seller": "141air",
  "payments_total": 500,
  "payments_by_status": {
    "synced": 480,
    "queued": 10,
    "skipped": 5,
    "skipped_non_sale": 5
  },
  "payments_open_count": 10,
  "payments_open_sample": [144370799868, 144370799869],
  "dead_job_payment_ids_count": 0,
  "pending_job_payment_ids_count": 5,
  "not_fully_reconciled_count": 10,
  "not_fully_reconciled_sample": [...]
}
```

---

### 5.10 Expenses

Gerenciamento de despesas non-order (mp_expenses). Todos requerem `X-Admin-Token`.

#### `GET /expenses/{seller_slug}`

Lista despesas com filtros.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `status` | string | null | `pending_review`, `auto_categorized`, `manually_categorized`, `exported` |
| `expense_type` | string | null | Inclui tipos API (`bill_payment`, `subscription`, `darf`, etc.) e tipos extrato (`difal`, `faturas_ml`, `reembolso_disputa`, `debito_envio_ml`, etc.) |
| `direction` | string | null | `expense`, `income`, `transfer` |
| `date_from` | string | null | `YYYY-MM-DD` |
| `date_to` | string | null | `YYYY-MM-DD` |
| `limit` | int | `100` | Limite (1-500) |
| `offset` | int | `0` | Offset para paginacao |

**Resposta 200:**
```json
{
  "seller": "141air",
  "count": 25,
  "offset": 0,
  "data": [
    {
      "id": 1,
      "payment_id": 14437079,
      "expense_type": "bill_payment",
      "expense_direction": "expense",
      "ca_category": null,
      "auto_categorized": false,
      "amount": 150.00,
      "description": "Boleto - COPEL DISTRIBUICAO",
      "business_branch": "Bill Payment",
      "operation_type": "regular_payment",
      "payment_method": "bolbradesco",
      "external_reference": null,
      "febraban_code": "04106.05...",
      "date_created": "2026-02-15T10:00:00-04:00",
      "date_approved": "2026-02-15T10:00:02-04:00",
      "status": "pending_review",
      "created_at": "2026-02-15T13:01:00"
    }
  ]
}
```

---

#### `PATCH /expenses/review/{seller_slug}/{expense_id}`

Revisao manual de uma despesa.

**Request Body (todos opcionais):**
```json
{
  "ca_category": "2.2.7",
  "description": "DARF - IRPJ Janeiro",
  "notes": "Pagamento trimestral",
  "beneficiary_name": "Receita Federal",
  "expense_type": "darf",
  "expense_direction": "expense"
}
```

**Resposta 200:**
```json
{
  "id": 1,
  "status": "manually_categorized",
  "..."
}
```

**Resposta 409:**
```json
{
  "detail": "Expense already exported"
}
```

---

#### `GET /expenses/{seller_slug}/pending-summary`

Resumo de despesas pendentes de revisao agrupadas por dia.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `date_from` | string | null | `YYYY-MM-DD` |
| `date_to` | string | null | `YYYY-MM-DD` |

**Resposta 200:**
```json
{
  "seller": "141air",
  "total_pending": 15,
  "by_day": [
    {
      "date": "2026-02-15",
      "count": 5,
      "amount_total": 1234.56,
      "payment_ids_sample": [14437079, 14437080]
    }
  ]
}
```

---

#### `GET /expenses/{seller_slug}/stats`

Contadores por tipo, direcao e status.

**Resposta 200:**
```json
{
  "seller": "141air",
  "total": 150,
  "total_amount": 45678.90,
  "by_type": {
    "bill_payment": 50,
    "subscription": 20,
    "cashback": 10,
    "transfer_pix": 30,
    "other": 40
  },
  "by_direction": {
    "expense": 90,
    "income": 10,
    "transfer": 50
  },
  "by_status": {
    "auto_categorized": 80,
    "pending_review": 50,
    "exported": 20
  }
}
```

---

#### `GET /expenses/{seller_slug}/export`

Gera ZIP com XLSX organizados por dia.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `date_from` | string | null | `YYYY-MM-DD` |
| `date_to` | string | null | `YYYY-MM-DD` |
| `status_filter` | string | null | Filtrar por status (default: todos nao-exportados) |
| `mark_exported` | bool | `false` | Marcar linhas como `exported` |

**Resposta 200:** Arquivo ZIP com estrutura:
```
EMPRESA/
├── 2026-02-15/
│   ├── PAGAMENTO_CONTAS.xlsx    # expense + income
│   └── TRANSFERENCIAS.xlsx       # transfer
├── 2026-02-16/
│   ├── PAGAMENTO_CONTAS.xlsx
│   └── TRANSFERENCIAS.xlsx
├── manifest.csv
└── manifest_pagamentos.csv
```

**Headers da resposta:**
| Header | Descricao |
|--------|-----------|
| `Content-Disposition` | `attachment; filename=despesas_EMPRESA_datas.zip` |
| `X-Export-Batch-Id` | ID do lote de exportacao |

**Colunas do XLSX:**
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

---

#### `GET /expenses/{seller_slug}/batches`

Lista lotes de exportacao/importacao.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `status` | string | null | `generated`, `exported`, `imported` |
| `limit` | int | `50` | Limite (1-500) |

---

#### `POST /expenses/{seller_slug}/batches/{batch_id}/confirm-import`

Confirma importacao de um lote no CA.

**Request Body:**
```json
{
  "imported_at": "2026-02-18T10:00:00",
  "notes": "Importado com sucesso"
}
```

---

#### `GET /expenses/{seller_slug}/closing`

Status de fechamento diario por empresa/dia.

**Query Parameters:**
| Parametro | Tipo | Default | Descricao |
|-----------|------|---------|-----------|
| `date_from` | string | null | `YYYY-MM-DD` |
| `date_to` | string | null | `YYYY-MM-DD` |
| `include_payment_ids` | bool | `false` | Incluir listas completas de payment_ids |

---

#### `POST /expenses/{seller_slug}/legacy-export`

Bridge legado: executa reconciliacao e exporta como ZIP (PAGAMENTO_CONTAS + TRANSFERENCIAS).

**Content-Type:** `multipart/form-data`

**Form Fields:**
| Campo | Tipo | Obrigatorio | Descricao |
|-------|------|-------------|-----------|
| `extrato` | file | Sim | Account statement (CSV ou ZIP) |
| `dinheiro` | file | Nao | Settlement report (CSV ou ZIP) |
| `vendas` | file | Nao | Collection report |
| `pos_venda` | file | Nao | After-collection report |
| `liberacoes` | file | Nao | Reserve-release report |
| `centro_custo` | string | Nao | Override do nome do centro de custo |

**Resposta 200:** ZIP com:
```
Conta Azul/PAGAMENTO_CONTAS.xlsx
Conta Azul/TRANSFERENCIAS.xlsx
Resumo/*_RESUMO.xlsx
Outros/*.csv
```

---

## 6. Modelos de Dados (Supabase)

**Projeto Supabase:** `wrbrbhuhsaaupqsimkqz`

### 6.1 sellers

Configuracao de cada seller (tokens ML, IDs CA, status de onboarding).

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `slug` | text (PK) | Identificador unico do seller |
| `name` | text | Nome de exibicao |
| `email` | text | Email do seller |
| `active` | bool | Seller ativo para processamento |
| `onboarding_status` | text | `pending_approval`, `approved`, `active`, `suspended` |
| `ml_user_id` | bigint | User ID do Mercado Livre |
| `ml_access_token` | text | Token de acesso ML |
| `ml_refresh_token` | text | Refresh token ML |
| `ml_token_expires_at` | timestamptz | Expiracao do access token |
| `ml_app_id` | text | App ID ML (per-seller, opcional) |
| `ml_secret_key` | text | Secret key ML (per-seller, opcional) |
| `ca_conta_bancaria` | uuid | ID da conta bancaria no CA |
| `ca_centro_custo_variavel` | uuid | ID do centro de custo no CA |
| `ca_contato_ml` | uuid | ID do contato "MERCADO LIVRE" no CA |
| `dashboard_empresa` | text | Nome da empresa no dashboard |
| `dashboard_grupo` | text | Grupo no dashboard (NETAIR, ACA, EASY, etc.) |
| `dashboard_segmento` | text | Segmento no dashboard |
| `source` | text | Origem do cadastro (`ml`, `manual`) |
| `integration_mode` | text | `dashboard_only` ou `dashboard_ca` |
| `ca_start_date` | date | Data inicial do backfill CA (primeiro dia do mes) |
| `ca_backfill_status` | text | `pending`, `running`, `completed`, `failed`, null |
| `ca_backfill_started_at` | timestamptz | Inicio do onboarding backfill |
| `ca_backfill_completed_at` | timestamptz | Fim do onboarding backfill |
| `ca_backfill_progress` | jsonb | Progresso acumulado do onboarding backfill |
| `approved_at` | timestamptz | Data de aprovacao |
| `created_at` | timestamptz | Data de criacao |

### 6.2 payments

Registro de cada payment processado (idempotencia + audit trail).

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `id` | bigserial (PK) | ID interno |
| `seller_slug` | text (FK) | Seller que processou |
| `ml_payment_id` | bigint | ID do payment no ML (unique per seller) |
| `ml_status` | text | Status do payment no ML |
| `amount` | numeric | Valor bruto (`transaction_amount`) |
| `net_amount` | numeric | Valor liquido efetivo |
| `money_release_date` | date | Data de liberacao do dinheiro |
| `ml_order_id` | bigint | ID do pedido ML |
| `status` | text | Status interno do processamento |
| `raw_payment` | jsonb | Payment completo do ML (cache) |
| `error` | text | Mensagem de erro (se houver) |
| `ca_evento_id` | text | Referencia ao evento CA |
| `processor_fee` | numeric | Fee calculada pelo processor (charges_details) |
| `processor_shipping` | numeric | Custo de frete calculado pelo processor |
| `fee_adjusted` | bool | Se ja recebeu ajuste de fee/frete via release report |
| `created_at` | timestamptz | Data de criacao |
| `updated_at` | timestamptz | Data de atualizacao |

**Status possiveis:**
| Status | Descricao |
|--------|-----------|
| `pending` | Recebido, aguardando processamento |
| `queued` | Jobs enfileirados no CA |
| `synced` | Todos os jobs CA concluidos |
| `refunded` | Processado como devolucao |
| `skipped` | Ignorado (cancelled/rejected) |
| `skipped_non_sale` | Ignorado (sem order, marketplace_shipment, compra, by_admin) |

### 6.3 ca_jobs

Fila persistente de jobs para Conta Azul API.

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `id` | uuid (PK) | ID do job |
| `idempotency_key` | text (unique) | Chave de idempotencia `{seller}:{payment_id}:{tipo}` |
| `seller_slug` | text | Seller |
| `job_type` | text | `receita`, `comissao`, `frete`, `estorno`, `estorno_taxa`, `baixa`, `partial_refund` |
| `ca_endpoint` | text | URL do endpoint CA |
| `ca_method` | text | `POST` ou `GET` |
| `ca_payload` | jsonb | Payload para o CA |
| `group_id` | text | Agrupamento `{seller}:{payment_id}` |
| `priority` | int | Prioridade (10=receita, 20=comissao/frete, 30=baixa) |
| `status` | text | `pending`, `processing`, `completed`, `failed`, `dead` |
| `attempts` | int | Numero de tentativas |
| `max_attempts` | int | Maximo de tentativas (default: 3) |
| `scheduled_for` | timestamptz | Quando executar |
| `next_retry_at` | timestamptz | Proximo retry |
| `started_at` | timestamptz | Inicio da execucao |
| `completed_at` | timestamptz | Fim da execucao |
| `ca_response_status` | int | HTTP status da resposta CA |
| `ca_response_body` | jsonb | Body da resposta CA |
| `ca_protocolo` | text | Protocolo retornado pelo CA |
| `last_error` | text | Ultimo erro |
| `created_at` | timestamptz | Data de criacao |
| `updated_at` | timestamptz | Data de atualizacao |

**Indices:**
- `idx_ca_jobs_queue`: `(status, scheduled_for, priority, created_at)` -- poll do worker
- `idx_ca_jobs_group`: `(group_id, status)` -- group completion
- `idx_ca_jobs_seller`: `(seller_slug, created_at DESC)` -- admin queries

### 6.4 ca_tokens

Tokens OAuth do Conta Azul (single row, id=1).

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `id` | int (PK) | Sempre 1 |
| `access_token` | text | Token de acesso |
| `refresh_token` | text | Refresh token (rotacionado a cada refresh) |
| `expires_at` | varies | Expiracao (epoch ms, ISO string, ou datetime) |

### 6.5 webhook_events

Log de todos os webhooks recebidos.

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `id` | bigserial (PK) | ID do evento |
| `seller_slug` | text | Seller identificado (ou "unknown") |
| `topic` | text | Topico do webhook (`payment`, `orders`, etc.) |
| `action` | text | Acao (`payment.created`, `payment.updated`, etc.) |
| `resource` | text | Resource URL |
| `data_id` | text | ID do objeto (payment_id, order_id) |
| `raw_payload` | jsonb | Payload completo do webhook |
| `status` | text | `received` ou `unmatched` |
| `created_at` | timestamptz | Data de recepcao |

### 6.6 faturamento

Totais diarios de faturamento por empresa (alimenta dashboard).

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `empresa` | text | Nome da empresa |
| `data` | date | Data |
| `valor` | numeric | Valor faturado |
| `source` | text | `sync` ou `manual` |
| `updated_at` | timestamptz | Ultima atualizacao |

**Constraint:** `UNIQUE(empresa, data)`

### 6.7 revenue_lines

Linhas de receita para o dashboard.

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `empresa` | text (PK) | Nome da empresa |
| `grupo` | text | Grupo |
| `segmento` | text | Segmento |
| `seller_id` | text (FK) | Referencia ao seller |
| `source` | text | Origem |
| `active` | bool | Ativa |
| `created_at` | timestamptz | Data de criacao |

### 6.8 goals

Metas mensais por empresa.

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `empresa` | text | Nome da empresa |
| `grupo` | text | Grupo |
| `year` | int | Ano |
| `month` | int | Mes (1-12) |
| `valor` | numeric | Valor da meta |

**Constraint:** `UNIQUE(empresa, year, month)`

### 6.9 mp_expenses

Classificacao de pagamentos non-order (V3).

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `id` | bigserial (PK) | ID interno |
| `seller_slug` | text (FK) | Seller |
| `payment_id` | text | ID do payment no ML (ou chave composta `id:tipo`) |
| `expense_type` | text | Tipo da despesa |
| `expense_direction` | text | `expense`, `income`, `transfer` |
| `ca_category` | text | Categoria CA (se auto-categorizado) |
| `auto_categorized` | bool | Se foi categorizado automaticamente |
| `amount` | numeric | Valor |
| `description` | text | Descricao gerada |
| `business_branch` | text | Branch do payment (Bill Payment, Virtual, etc.) |
| `operation_type` | text | Tipo de operacao ML |
| `payment_method` | text | Metodo de pagamento |
| `external_reference` | text | Referencia externa |
| `febraban_code` | text | Codigo Febraban (boletos) |
| `date_created` | timestamptz | Data de criacao no ML |
| `date_approved` | timestamptz | Data de aprovacao no ML |
| `beneficiary_name` | text | Nome do beneficiario |
| `notes` | text | Notas manuais |
| `source` | text | `payments_api` ou `extrato` |
| `status` | text | Status de processamento |
| `exported_at` | timestamptz | Data de exportacao |
| `raw_payment` | jsonb | Payment completo do ML |
| `created_at` | timestamptz | Data de criacao |
| `updated_at` | timestamptz | Data de atualizacao |

**Constraint:** `UNIQUE(seller_slug, payment_id)`

**Tipos de expense:**
| Tipo | Descricao |
|------|-----------|
| `bill_payment` | Boleto/conta |
| `subscription` | Assinatura SaaS |
| `darf` | DARF/imposto |
| `cashback` | Ressarcimento ML |
| `collection` | Cobranca ML |
| `transfer_pix` | Transferencia PIX |
| `transfer_intra` | Transferencia intra MP |
| `deposit` | Deposito/aporte |
| `savings_pot` | Cofrinho/Renda MP |
| `other` | Outros |
| `difal` | Diferenca de aliquota ICMS (extrato) |
| `faturas_ml` | Faturas vencidas ML (extrato) |
| `reembolso_disputa` | Reembolso de reclamacoes (extrato) |
| `dinheiro_retido` | Valor retido por disputa (extrato) |
| `entrada_dinheiro` | Credito avulso (extrato) |
| `debito_envio_ml` | Debito retroativo de envio (extrato) |
| `liberacao_cancelada` | Liberacao cancelada (extrato) |
| `reembolso_generico` | Reembolso generico/arredondamento (extrato) |
| `deposito_avulso` | Dinheiro recebido (extrato) |
| `debito_divida_disputa` | Debito por divida de disputa (extrato) |
| `debito_troca` | Debito de troca de produto (extrato) |
| `bonus_envio` | Bonus por envio (extrato) |

**Status possiveis:**
| Status | Descricao |
|--------|-----------|
| `pending_review` | Aguardando revisao manual |
| `auto_categorized` | Categorizado automaticamente |
| `manually_categorized` | Categorizado manualmente |
| `exported` | Exportado em XLSX |

### 6.10 admin_config

Password hash do admin (single row).

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `id` | int (PK) | Sempre 1 |
| `password_hash` | text | Hash bcrypt da senha |

### 6.11 meli_tokens

Tokens ML legados (migrados de Supabase antigo).

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `account_name` | text (PK) | Nome da conta |
| `seller_id` | text (FK) | Referencia ao seller |
| `refresh_token` | text | Refresh token ML |
| `access_token` | text | Access token ML |
| `access_token_expires_at` | timestamptz | Expiracao |
| `updated_at` | timestamptz | Ultima atualizacao |

### 6.12 release_report_fees

Audit trail de linhas parseadas do release report com fee breakdown.

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `id` | bigserial (PK) | ID interno |
| `seller_slug` | text (FK) | Seller |
| `source_id` | text | SOURCE_ID do release report |
| `release_date` | date | Data de liberacao |
| `description` | text | DESCRIPTION da linha |
| `record_type` | text | RECORD_TYPE da linha |
| `gross_amount` | numeric | GROSS_AMOUNT |
| `mp_fee_amount` | numeric | MP_FEE_AMOUNT |
| `financing_fee_amount` | numeric | FINANCING_FEE_AMOUNT |
| `shipping_fee_amount` | numeric | SHIPPING_FEE_AMOUNT |
| `taxes_amount` | numeric | TAXES_AMOUNT |
| `coupon_amount` | numeric | COUPON_AMOUNT |
| `net_credit_amount` | numeric | NET_CREDIT_AMOUNT |
| `net_debit_amount` | numeric | NET_DEBIT_AMOUNT |
| `external_reference` | text | EXTERNAL_REFERENCE |
| `order_id` | text | ORDER_ID |
| `payment_method` | text | PAYMENT_METHOD |
| `created_at` | timestamptz | Data de criacao |

**Constraint:** `UNIQUE(seller_slug, source_id, release_date, description)`

### 6.13 sync_state

Cursor persistido de sincronizacoes e jobs periodicos.

| Coluna | Tipo | Descricao |
|--------|------|-----------|
| `seller_slug` | text | Seller |
| `key` | text | Nome do cursor/rotina |
| `state` | jsonb | Payload do estado |
| `updated_at` | timestamptz | Ultima atualizacao |

---

## 7. Fluxos de Negocio

### 7.1 Payment -> Conta Azul

Fluxo principal de processamento de vendas ML para lancamentos no CA.

```
Daily Sync (00:01 BRT) ou Backfill manual
    |
    +-- search_payments() pagina todos payments do periodo
    +-- Filtra already_done (payments + mp_expenses)
    +-- Para cada payment:
           |
           +-- Tem order_id? --> process_payment_webhook(payment_data)
           +-- Sem order_id? --> classify_non_order_payment() --> mp_expenses
           |
           +-- status = "approved" / "in_mediation"
           |   +-- _process_approved()
           |       +-- GET ML order (titulo do item)
           |       +-- Extrai taxas de charges_details (source of truth)
           |       +-- comissao = soma fee (from=collector, exclui financing_fee)
           |       +-- frete_seller = max(0, shipping_collector - shipping_amount)
           |       +-- Persiste processor_fee/processor_shipping em payments
           |       +-- Enqueue: receita (contas-a-receber)
           |       +-- Enqueue: comissao (contas-a-pagar) se > 0
           |       +-- Enqueue: frete (contas-a-pagar) se > 0
           |       +-- Se net_real > net_calculado: Enqueue receita de subsidio (categoria 1.3.7)
           |
           +-- status = "charged_back" + status_detail = "reimbursed"
           |   +-- Trata como approved (ML cobriu o chargeback)
           |
           +-- status = "refunded" + status_detail = "by_admin"
           |   +-- Se JA synced: _process_refunded() (estornar)
           |   +-- Se NAO synced: SKIP (kit split)
           |
           +-- status = "refunded" / "charged_back"
           |   +-- _process_refunded()
           |       +-- Se nunca synced: cria receita+despesas primeiro
           |       +-- Enqueue: estorno receita (contas-a-pagar)
           |       +-- Enqueue: estorno taxa (contas-a-receber) se refund total
           |
           +-- status = "cancelled" / "rejected" --> skip
```

### Exemplo Numerico Completo

```
Payment 144370799868 (approved):
  transaction_amount:      R$ 284,74  (valor bruto)
  net_received_amount:     R$ 235,85
  shipping (collector):    R$  23,45  (charges_details type=shipping, from=collector)
  comissao = 284,74 - 235,85 - 23,45 = R$ 25,44

  --> Receita CA:   R$ 284,74  (contas-a-receber, cat 1.1.1, venc=money_release_date)
  --> Comissao CA:  R$  25,44  (contas-a-pagar, cat 2.8.2)
  --> Frete CA:     R$  23,45  (contas-a-pagar, cat 2.9.4)
  --> Baixas:       criadas pelo scheduler quando money_release_date <= hoje
```

### 7.2 CaWorker (Fila Persistente)

Background loop que processa a fila `ca_jobs` respeitando o rate limit global.

```
Poll ca_jobs (pending/failed, scheduled_for <= now) --> claim atomico
    |
    +-- POST/GET no endpoint CA com payload
    +-- 2xx --> completed (salva protocolo)
    +-- 401 --> invalidate token cache, retry
    +-- 429/5xx --> failed + backoff (30s, 120s, 480s)
    +-- 4xx outro --> dead (dead letter)
    +-- Quando todos jobs de um group_id completam --> payment status = "synced"
```

**Prioridades:**
| Prioridade | Tipo |
|------------|------|
| 10 | Receita |
| 20 | Comissao, Frete, Estorno, Estorno Taxa, Partial Refund |
| 30 | Baixa |

**Retry backoff:** 30s --> 120s --> 480s --> dead

**Recovery automatico:** No startup, jobs stuck em `processing` por mais de 5 minutos sao resetados para `failed`.

### 7.3 Baixas (Liquidacao)

As baixas sao separadas do processor porque a CA API retorna 400 se `data_pagamento > hoje`.

```
Scheduler (10:00 BRT) --> para cada seller ativo:
    +-- Busca parcelas abertas no CA (vencimento <= hoje)
    +-- ReleaseChecker: verifica money_release_status no ML
    |   +-- Preload do Supabase (raw_payment cache)
    |   +-- Re-check via ML API se release_date passada mas status "pending"
    +-- Split: released/bypass --> processar | pending --> skip
    +-- Enqueue baixa para cada parcela liberada
```

### 7.4 Daily Sync

Sync diario que substitui webhooks como mecanismo primario de ingestao.

**Horario:** 00:01 BRT, cobre D-1 a D-3

**Processo:**
1. Busca payments por `date_approved` (vendas novas) e `date_last_updated` (refunds/chargebacks)
2. Deduplica por `payment_id` (last_updated vence em colisao)
3. Detecta mudancas de status em payments ja processados
4. Orders --> `process_payment_webhook` (com payment_data pre-carregado)
5. Non-orders --> `classify_non_order_payment` (modo classifier) ou defer (modo legacy)

**Cursor:** Persiste ultimo window processado na tabela `sync_state` para evitar gaps.

### 7.5 Classificacao Non-Order (V3)

Payments sem `order_id` sao classificados automaticamente:

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

### 7.6 Financial Closing

Combina duas "faixas" de processamento:

- **Faixa automatica (auto):** Order payments processados via ca_jobs/payments --> Conta Azul
- **Faixa manual:** Non-order payments exportados como XLSX via mp_expenses --> import manual no CA

O fechamento verifica se todos os payments foram resolvidos (auto) e importados (manual) para cada dia.

### 7.7 Onboarding de Sellers

Fluxo principal (Onboarding V2):

```
1. create_signup(slug, name) --> status: pending_approval
2. approve_seller(seller_id, config) --> status: approved
   +-- Cria revenue_line
   +-- Cria 12 goals (valor=0)
   +-- Best effort: configura release_report no MP
3. POST /admin/sellers/{slug}/activate
   +-- integration_mode=dashboard_only  --> ativa sem backfill CA
   +-- integration_mode=dashboard_ca    --> exige conta/custo/ca_start_date (dia 1)
   +-- Em dashboard_ca: dispara run_onboarding_backfill(slug)
4. run_onboarding_backfill(slug)
   +-- Busca payments por money_release_date (ca_start_date -> ontem)
   +-- Orders -> process_payment_webhook
   +-- Non-orders -> classify_non_order_payment
   +-- Persiste progresso em sellers.ca_backfill_progress
   +-- Dispara baixas ao final
   +-- Marca status completed/failed
```

Fluxo alternativo (self-service OAuth):
```
1. /auth/ml/install --> OAuth ML
2. _handle_new_install() --> cria seller (pending_approval) + salva tokens
3. Admin aprova via POST /admin/sellers/{id}/approve
4. Admin ativa via POST /admin/sellers/{slug}/activate
```

---

## 8. Background Tasks (Lifespan)

Iniciados no startup do FastAPI via `asynccontextmanager`:

| Task | Intervalo | Funcao | Ativacao |
|------|-----------|--------|----------|
| **CaWorker** | Poll 1s | Processa fila ca_jobs --> CA API | Sempre |
| **FaturamentoSyncer** | 5 min (configuravel) | Sync ML orders --> tabela faturamento | Sempre |
| **CA Token Refresh** | 30 min | Refresh proativo do token CA | Sempre |
| **Daily Sync Scheduler** | 1x/dia 00:01 BRT | Backfill D-1..D-3 | Quando `nightly_pipeline_enabled=false` |
| **Daily Baixa Scheduler** | 1x/dia 10:00 BRT | Processar baixas de todos os sellers | Quando `nightly_pipeline_enabled=false` |
| **Financial Closing Scheduler** | 1x/dia 11:30 BRT | Fechamento financeiro | Quando `nightly_pipeline_enabled=false` |
| **Legacy Daily Export** | 1x/dia (config BRT) | Export legado ZIP | Quando `legacy_daily_enabled=true` e `nightly_pipeline_enabled=false` |
| **Nightly Pipeline** | 1x/dia (config BRT) | Sync -> fee validation -> ingestao extrato -> baixas -> legacy -> coverage -> closing | Quando `nightly_pipeline_enabled=true` |

### Nightly Pipeline

Quando habilitado, substitui os schedulers individuais com uma execucao sequencial:

1. `sync_all_sellers()` -- Daily sync de payments
2. `validate_release_fees_all_sellers()` -- Valida fees e cria ajustes CA
3. `ingest_extrato_all_sellers()` -- Ingestao de lacunas do extrato em `mp_expenses`
4. `_run_baixas_all_sellers()` -- Baixas
5. `run_legacy_daily_for_all()` -- Legacy export (apenas nos dias configurados em `nightly_pipeline_legacy_weekdays`)
6. `check_extrato_coverage_all_sellers()` -- Coverage check do extrato
7. `_run_financial_closing()` -- Fechamento financeiro (inclui resumo de coverage)

---

## 9. Regras de Negocio Criticas

### 9.1 Calculo de Comissao

```
comissao_ml = SUM(charges_details[type=fee, accounts.from=collector, name!=financing_fee])
frete_seller = max(0, SUM(charges_details[type=shipping, accounts.from=collector]) - shipping_amount)
liquido_calculado = amount - comissao_ml - frete_seller
```

**OBRIGATORIO:** Usar `charges_details` como fonte de verdade. NAO usar `fee_details` (incompleto).

### 9.1b Subsidio ML (net > calculado)

Quando `net_received_amount` vier maior que `amount - fee - frete`, o diff e tratado como
**receita de subsidio ML** (categoria `1.3.7`, `estorno_frete`).

### 9.1c Reprocessamento de fees no Backfill

`GET /backfill/{seller}` suporta `reprocess_missing_fees=true` (default), que reprocessa
payments ja finalizados (`synced/queued/refunded/skipped`) se `processor_fee` ou
`processor_shipping` estiver nulo.

### 9.2 financing_fee e NET-NEUTRAL

`financing_fee` = `financing_transfer` (pass-through). NAO gera despesa no CA. Ja esta descontado do net.

### 9.3 Datas

- **Competencia** = `_to_brt_date(date_approved)` -- quando o pagamento foi confirmado
- ML API retorna UTC-4, reports ML usam BRT (UTC-3)
- **Vencimento/baixa** = `money_release_date`
- NAO usar `date_created` para competencia (usar `date_approved`)

### 9.4 Filtros de Skip

Payments que NAO sao processados como vendas:

| Condicao | Motivo |
|----------|--------|
| Sem `order_id` | Classificado em mp_expenses (V3) |
| `description == "marketplace_shipment"` | Frete pago pelo comprador |
| `collector.id is not None` | Compra (seller e o comprador) |
| `refunded` + `by_admin` + NAO synced | Kit split (novos payments cobrem) |
| `partition_transfer` | Movimentacao interna MP |
| `payment_addition` | Frete adicional vinculado a order |

### 9.5 Charged Back

- `charged_back` + `reimbursed` --> tratar como approved (ML cobriu)
- `charged_back` sem reimbursed --> tratar como refunded
- `transaction_amount_refunded` pode ser 0 em chargebacks --> fallback para `amount`

### 9.6 Refund by_admin (Kit Split)

Quando ML separa pacote em etiquetas diferentes:
- `by_admin` + NAO synced --> **SKIP** (novos payments cobrem receita)
- `by_admin` + JA synced --> processar como refund normal

### 9.7 CA API v2

- Respostas sao **assincronas**: retornam `{"protocolo": "...", "status": "PENDING"}`
- Busca de parcelas e **GET** com params (nao POST)
- **Obrigatorio** incluir `valor_liquido` em `detalhe_valor` (senao 400)

### 9.8 Por que Baixas sao Separadas

A CA API retorna 400 se `data_pagamento > hoje`. Quando `money_release_date` e futuro, a baixa NAO pode ser feita na criacao da receita/despesa. Despesas/receitas sao criadas EM_ABERTO e o scheduler `/baixas/processar/{seller}` cria baixas quando vencimento <= hoje.

---

## 10. Rate Limiting

### Rate Limiter Global (CA API)

Token bucket compartilhado entre CaWorker e leituras diretas da CA API.

| Parametro | Valor |
|-----------|-------|
| Burst rate | 9 req/s |
| Guard rate | 540 req/min |
| Limites CA oficiais | 10 req/s, 600 req/min |
| Margem de seguranca | 90% dos limites |

**Implementacao:** `TokenBucket` singleton em `app/services/rate_limiter.py`

### Retry com Backoff (CaWorker)

| Tentativa | Espera |
|-----------|--------|
| 1 | 30 segundos |
| 2 | 120 segundos |
| 3 | 480 segundos |
| 4+ | Dead letter |

---

## 11. Idempotencia e Resiliencia

### Payments

Upsert por `(seller_slug, ml_payment_id)`. Reprocessar e seguro.

### ca_jobs

Unique constraint em `idempotency_key`. Padrao da chave: `{seller}:{payment_id}:{tipo}`

| Tipo | Formato da chave |
|------|------------------|
| Receita | `{seller}:{payment_id}:receita` |
| Comissao | `{seller}:{payment_id}:comissao` |
| Frete | `{seller}:{payment_id}:frete` |
| Estorno | `{seller}:{payment_id}:estorno` |
| Estorno Taxa | `{seller}:{payment_id}:estorno_taxa` |
| Partial Refund | `{seller}:{payment_id}:partial_refund:{index}` |
| Baixa | `{seller}:{parcela_id}:baixa` |

### Stuck Jobs

Recovery automatico no startup: jobs em `processing` por mais de 5 minutos sao marcados como `failed`.

### Token Refresh Concorrente

`asyncio.Lock` previne race condition no refresh do token CA. Multiplas coroutines aguardam a mesma operacao de refresh.

---

## 12. Categorias Conta Azul

Categorias compartilhadas por todos os sellers:

| Chave | UUID | Descricao |
|-------|------|-----------|
| `venda_ml` | `78f42170-23f7-41dc-80cd-7886c78fc397` | 1.1.1 MercadoLibre (Receita) |
| `comissao_ml` | `699d6072-031a-47bf-9aeb-563d1c2e8a41` | 2.8.2 Comissoes de Marketplace |
| `frete_mercadoenvios` | `6ccbf8ed-e174-4da0-ac8d-0ed1b387cb32` | 2.9.4 MercadoEnvios |
| `frete_full` | `27c8de66-cbb2-4778-94a5-b0de4405ae68` | 2.9.10 Frete Full |
| `devolucao` | `713ee216-8abe-4bcd-bc54-34421cb62a06` | 1.2.1 Devolucoes e Cancelamentos |
| `estorno_taxa` | `c4cc890c-126a-48da-8e47-da7c1492620d` | 1.3.4 Estornos de Taxas |
| `estorno_frete` | `2c0ef767-4983-4c4e-bfec-119f05708cd4` | 1.3.7 Estorno de Frete |
| `antecipacao` | `7e9efb50-6039-4238-b844-a10507c42ff2` | 2.11.9 Antecipacao de Recebiveis |
| `tarifa_pagamento` | `d77aa9d6-dd63-4d67-a622-64c3a05780a5` | 2.11.8 Tarifas de Pagamento |

**Contato CA padrao:** `b247cccb-38a2-4851-bf0e-700c53036c2c` (MERCADO LIVRE)

---

## 13. Deploy (Docker)

### Dockerfile (Multi-stage)

```dockerfile
## Stage 1: Build dashboard
FROM node:22-alpine AS dashboard-build
WORKDIR /dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ .
RUN npm run build

## Stage 2: Python API + static dashboard
FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=dashboard-build /dashboard/dist /code/dashboard-dist
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Comandos

```bash
# Dev local
source venv/bin/activate
uvicorn app.main:app --reload --port 8000

# Docker
docker compose up --build

# Dashboard dev (separado)
cd dashboard && npm run dev

# Build dashboard para producao
cd dashboard && npm run build
```

### Serve do Dashboard

O FastAPI serve o dashboard SPA construido a partir de `dashboard-dist/`:
- Assets estaticos servidos em `/assets/*`
- Todas as rotas nao-API servem `index.html` (SPA fallback)
- Rotas API (`/admin`, `/dashboard`, `/auth`, `/health`, etc.) tem prioridade

---

## 14. Dashboard (React SPA)

### Stack

| Camada | Tecnologia |
|--------|------------|
| Framework | React 19 + TypeScript |
| Build | Vite 7 |
| Graficos | Recharts 3 |
| Datas | date-fns |
| Backend | Supabase (direto via anon key + RLS) |
| Estilo | CSS Modules (sem Tailwind) |

### 4 Views

| View | Descricao |
|------|-----------|
| **Geral** (default) | KPIs, graficos, rankings, breakdowns |
| **Metas** | Acompanhamento meta vs realizado (dia/semana/mes/ano) |
| **Entrada** | Grid de entrada manual de dados |
| **Linhas** | Gerenciamento de linhas de receita |

### Funcionalidades

- Visualizacao de receitas diarias, semanais, mensais e anuais
- Comparacao desempenho real vs metas
- Projecao sazonal com quantis (p10/p50/p90)
- Entrada manual de dados via grid
- Gerenciamento de linhas de receita e metas
- PWA instalavel (offline read-only)
- Realtime subscription via Supabase

### Hooks Principais

| Hook | Descricao |
|------|-----------|
| `useSupabaseFaturamento` | Fetch + realtime do Supabase |
| `useFilters` | Motor central de calculos (KPIs, metas, breakdowns) |
| `useGoals` | Gestao de metas (Supabase + localStorage) |
| `useRevenueLines` | Gestao de linhas de receita |

### Regra de Negocio: D-1

Todos os indicadores de "esperado" usam **D-1 (ontem)** como referencia. Motivo: faturamento so pode ser fechado no dia seguinte.

### Regra AR CONDICIONADO

Segmento "AR CONDICIONADO" tem meta ajustada: dia util = 120%, fim de semana = 50%.

---

## 15. Testes e Validacao

### simulate_backfill.py

Script standalone que replica a logica do processor sem side effects.

**O que faz:**
1. Conecta ao Supabase para obter config do seller
2. Busca payments no ML via API
3. Aplica logica de classificacao e calculo
4. Gera relatorio no terminal + arquivo JSON

**O que NAO faz:** NAO grava no Supabase, NAO enfileira no CA.

**Como rodar:**
```bash
# Editar constantes no topo do script
SELLER_SLUG = "netparts-sp"
BEGIN_DATE = "2026-02-01"
END_DATE = "2026-02-01"

# Executar
python3 simulate_backfill.py
```

**Sellers disponiveis:**

| Slug | Nome | ML User ID |
|------|------|------------|
| `141air` | 141AIR | 1963376627 |
| `net-air` | NET AIR | 421259712 |
| `netparts-sp` | NETPARTS SP | 1092904133 |

### Fluxo Completo: Simulacao -> Producao

```
1. simulate_backfill.py (analise offline)
2. Verificar checklist de validacao
3. Conferir com relatorio CSV do ML
4. GET /backfill/{seller}?...&dry_run=true
5. Comparar dry_run com simulacao
6. GET /backfill/{seller}?...&dry_run=false
7. GET /queue/status (monitorar fila)
8. Verificar lancamentos no CA
```

### Resultado de Referencia

**NETPARTS SP -- 01/02/2026:**
```
87 payments | 74 approved | 4 refunded | 8 skipped | 1 pending

Categorias CA:
  1.1.1 MercadoLibre (Receita):      R$ 10.075,29
  2.8.2 Comissoes Marketplace:        R$  1.633,67
  2.9.4 MercadoEnvios:                R$    725,77
  1.2.1 Devolucoes e Cancelamentos:   R$    644,36
  1.3.4 Estornos de Taxas:            R$    155,48
```

---

## 16. Historico de Correcoes

Bugs ja corrigidos -- NAO reintroduzir:

| # | Bug | Correcao |
|---|-----|----------|
| 1 | `buscar_parcelas_pagar` usava POST | Corrigido para GET com params |
| 3 | `in_mediation` nao era processado | Adicionado ao branch de approved |
| 4 | Payments sem order_id processados | Filtro: skip se sem order_id |
| 5 | `_process_refunded` reprocessava | Check: se existing status=refunded --> skip |
| 7 | Refund de payment nunca synced | `_process_refunded` cria receita original primeiro |
| 8 | Estorno > transaction_amount | `estorno = min(refunded, amount)` |
| 9 | Chamadas CA diretas sem rate limit | Migrado para ca_queue + rate_limiter global |
| 10a | Competencia usava date_created | Corrigido para `_to_brt_date(date_approved)` |
| 10b | marketplace_shipment processado | Filtro: skip se description="marketplace_shipment" |
| 11 | charged_back nao tratado | Branch: reimbursed-->approved, outros-->refunded |
| 12 | charged_back refund=0, estorno zerado | Fallback: `refunded or amount` |
| 13 | charged_back+reimbursed gerava estorno | Check: reimbursed --> tratar como approved |
| 14 | by_admin inflava DRE | Skip se by_admin + nao synced |

---

## Apendice: Diagrama de Fluxo Completo

```
ML/MP (webhook) --> POST /webhooks/ml --> webhook_events (log only)
                                          daily_sync cuida do processamento

Daily Sync (00:01 BRT)
    |
    +-- search_payments(date_approved + date_last_updated)
    +-- Para cada payment:
           |
           +-- Tem order_id? --> processor.process_payment_webhook
           |                         |
           |                         +-- Enqueue jobs em ca_jobs
           |                         +-- Upsert em payments
           |
           +-- Sem order_id? --> expense_classifier.classify_non_order_payment
                                     |
                                     +-- Upsert em mp_expenses

Fee Validation (nightly pipeline)
    |
    +-- release_report_validator.validate_release_fees_all_sellers()
    +-- Compara processor_fee/shipping vs MP_FEE/SHIPPING
    +-- Enqueue ajustes em ca_jobs (ajuste_comissao/ajuste_frete)

Extrato Ingestion (nightly pipeline)
    |
    +-- extrato_ingester.ingest_extrato_all_sellers()
    +-- Classifica lacunas do account_statement
    +-- Upsert em mp_expenses (source="extrato")

CaWorker (poll 1s)
    |
    +-- Pega job de ca_jobs
    +-- Executa POST/GET na CA API
    +-- Marca completed/failed/dead
    +-- Quando grupo completa --> payments.status = "synced"

Daily Baixa (10:00 BRT)
    |
    +-- Busca parcelas abertas no CA
    +-- ReleaseChecker verifica money_release_status no ML
    +-- Enqueue baixas para parcelas liberadas

Financial Closing (11:30 BRT)
    |
    +-- Verifica auto lane (payments + ca_jobs)
    +-- Verifica manual lane (mp_expenses + expense_batches)
    +-- Inclui resumo de extrato_coverage (quando disponivel)
    +-- Gera relatorio de fechamento por dia/seller
```

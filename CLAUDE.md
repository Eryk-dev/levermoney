# API Conciliador V2/V3 - Lever Money

> **Leia este documento ANTES de qualquer alteracao.** Ele substitui a necessidade de ler cada arquivo individualmente.
> Para documentacao completa de endpoints (request/response examples, status codes), consulte `API_DOCUMENTATION.md`.

---

## 1. Visao Geral

Sistema de conciliacao automatica entre **Mercado Livre / Mercado Pago** e **Conta Azul ERP**. Para cada venda no ML, cria automaticamente no CA:
- **Receita** (contas-a-receber) com valor bruto da venda
- **Despesa comissao** (contas-a-pagar) com taxas ML/MP
- **Despesa frete** (contas-a-pagar) com custo MercadoEnvios
- **Baixas** automaticas quando dinheiro e liberado pelo ML

**V3:** Pagamentos sem order (boletos, SaaS, cashback, transferencias) sao classificados automaticamente na tabela `mp_expenses` e exportados como XLSX para o financeiro importar no CA.

**Fonte de caixa/extrato:** usar **account_statement** (endpoints `release_report` / `bank_report`).
`settlement_report` nao e a fonte oficial para fechamento diario de caixa.

**Mecanismo de ingestao:** Daily sync automatico as 00:01 BRT (D-1 a D-3). Webhooks continuam recebendo e logando, mas NAO processam payments (daily sync cuida de tudo).

**Funcionalidades adicionais:**
- Dashboard de faturamento (React SPA)
- Sync periodico de faturamento
- Onboarding self-service de sellers
- Painel admin
- Financial closing (fechamento financeiro diario)
- Legacy daily export (ZIP com PAGAMENTO_CONTAS e TRANSFERENCIAS)
- Nightly pipeline (orquestracao sequencial de todos os processos)

---

## 2. Stack e Infra

| Camada | Tecnologia | Versao |
|--------|------------|--------|
| API | FastAPI | 0.115.6 |
| Python | Python | 3.12 |
| HTTP Client | httpx | 0.28.1 |
| DB | Supabase (PostgreSQL) | via supabase-py 2.11.0 |
| Auth CA | OAuth2 / Cognito (token rotation) | - |
| Auth ML | OAuth2 / Mercado Pago | - |
| Settings | pydantic-settings | 2.7.1 |
| Planilhas | openpyxl | 3.1.5 |
| Criptografia | bcrypt | 4.2.1 |
| Data Processing | pandas 2.2.3 + numpy 2.1.3 | - |
| Google Drive | google-api-python-client 2.167.0 | - |
| Dashboard | React 19 + Vite + TypeScript (SPA separada) | - |
| Deploy | Docker multi-stage (Python + Nginx) | - |
| Dominio | conciliador.levermoney.com.br | - |

---

## 3. Comandos

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
# Output: dashboard/dist/ → copiado para dashboard-dist/ pelo Dockerfile
```

---

## 4. Variaveis de Ambiente (.env)

### Obrigatorias

```
ML_APP_ID=             # App ID do Mercado Livre
ML_SECRET_KEY=         # Secret key do app ML
ML_REDIRECT_URI=       # https://dominio/auth/ml/callback
CA_CLIENT_ID=          # Cognito Client ID
CA_CLIENT_SECRET=      # Cognito Client Secret
SUPABASE_URL=          # https://xxx.supabase.co
SUPABASE_KEY=          # Service role key
```

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
| `LEGACY_DAILY_UPLOAD_TIMEOUT_SECONDS` | `120` | Timeout do upload HTTP |
| `LEGACY_DAILY_REPORT_WAIT_SECONDS` | `300` | Tempo de espera por report ML |
| `LEGACY_DAILY_DEFAULT_CENTRO_CUSTO` | `NETAIR` | Centro de custo padrao |
| `LEGACY_DAILY_GOOGLE_DRIVE_ROOT_FOLDER_ID` | `""` | Pasta raiz do Google Drive |
| `LEGACY_DAILY_GOOGLE_DRIVE_ID` | `""` | Shared Drive ID (opcional) |
| `LEGACY_DAILY_GOOGLE_SERVICE_ACCOUNT_JSON` | `""` | JSON da service account |
| `LEGACY_DAILY_GOOGLE_SERVICE_ACCOUNT_FILE` | `""` | Caminho para arquivo da service account |

---

## 5. Estrutura do Projeto

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
│   │   ├── webhooks.py          # POST /webhooks/ml (receiver ML/MP, log only)
│   │   ├── backfill.py          # GET /backfill/{seller} (retroativo manual)
│   │   ├── baixas.py            # GET /baixas/processar/{seller}
│   │   ├── auth_ml.py           # OAuth ML (connect/callback/install)
│   │   ├── auth_ca.py           # OAuth CA (connect/callback/status)
│   │   ├── admin.py             # Admin CRUD (sellers, goals, sync, closing)
│   │   ├── dashboard_api.py     # Dashboard read API (publico)
│   │   ├── expenses.py          # MP expenses: list, export XLSX, stats, batches
│   │   ├── queue.py             # Queue monitoring + reconciliation
│   │   └── health.py            # Health check + debug endpoints
│   ├── services/
│   │   ├── processor.py         # CORE: ML payment → CA events
│   │   ├── ca_api.py            # CA HTTP client (retry, rate limit, token rotation)
│   │   ├── ml_api.py            # ML/MP HTTP client (per-seller tokens)
│   │   ├── ca_queue.py          # Persistent job queue + CaWorker
│   │   ├── daily_sync.py        # Daily sync: backfill D-1..D-3 + classify non-orders
│   │   ├── expense_classifier.py # Classify non-order payments → mp_expenses
│   │   ├── financial_closing.py # Fechamento financeiro diario (auto + manual lanes)
│   │   ├── faturamento_sync.py  # Polling ML orders → faturamento table
│   │   ├── release_checker.py   # Verifica money_release_status antes de baixas
│   │   ├── rate_limiter.py      # Token bucket (9 req/s, 540 req/min)
│   │   ├── onboarding.py        # Seller signup → approve → activate
│   │   ├── onboarding_backfill.py # Backfill de ativacao (dashboard_ca)
│   │   ├── legacy_daily_export.py # Export legado diario (release_report → ZIP → upload)
│   │   ├── legacy_bridge.py     # Bridge para formato legado (CSV → XLSX)
│   │   ├── legacy_engine.py     # Motor de reconciliacao legado (processar_conciliacao)
│   │   ├── release_report_sync.py # Sync release report → mp_expenses
│   │   ├── release_report_validator.py # Valida fees do processor vs release report
│   │   ├── extrato_ingester.py  # Ingesta lacunas do account_statement em mp_expenses
│   │   └── extrato_coverage_checker.py # Verifica 100% cobertura do extrato
│   └── static/
│       └── install.html         # Landing page self-service install
├── dashboard/                   # React SPA (tem seu proprio CLAUDE.md)
├── API_DOCUMENTATION.md         # Documentacao completa da API (endpoints detalhados)
├── PLANO.md                     # Plano do projeto v1.8
├── FLUXO-DETALHADO.md           # Fluxo detalhado v3.3
├── REFERENCIA-APIs-ML-MP.md     # Referencia APIs ML/MP
├── Dockerfile                   # Multi-stage: dashboard build + Python API
├── docker-compose.yml           # Services: api (8000) + dashboard (3000)
├── requirements.txt             # Deps Python
└── .env                         # Segredos (NAO commitar)
```

---

## 6. Tabelas Supabase

**Projeto:** `wrbrbhuhsaaupqsimkqz`

### sellers
Configuracao de cada seller (ML tokens, CA IDs, onboarding status).
```
slug (PK text), name, email, active (bool), onboarding_status,
ml_user_id, ml_access_token, ml_refresh_token, ml_token_expires_at,
ml_app_id, ml_secret_key,
ca_conta_bancaria, ca_centro_custo_variavel, ca_contato_ml,
dashboard_empresa, dashboard_grupo, dashboard_segmento,
source,
integration_mode (dashboard_only|dashboard_ca), ca_start_date,
ca_backfill_status (pending|running|completed|failed|null),
ca_backfill_started_at, ca_backfill_completed_at, ca_backfill_progress (jsonb),
approved_at, created_at
```

### payments
Registro de cada payment processado (idempotencia + audit trail).
```
id (PK), seller_slug, ml_payment_id (unique per seller), ml_status,
amount, net_amount, money_release_date, ml_order_id,
status (pending|queued|synced|refunded|skipped|skipped_non_sale),
raw_payment (jsonb), error, ca_evento_id,
processor_fee (numeric), processor_shipping (numeric), fee_adjusted (bool),
created_at, updated_at
```

### ca_jobs
Fila persistente de jobs para CA API.
```
id (PK uuid), idempotency_key (unique), seller_slug, job_type,
ca_endpoint, ca_method, ca_payload (jsonb), group_id,
priority (int), status (pending|processing|completed|failed|dead),
attempts, max_attempts, scheduled_for, next_retry_at,
started_at, completed_at, ca_response_status, ca_response_body,
ca_protocolo, last_error, created_at, updated_at

Indexes:
  idx_ca_jobs_queue: (status, scheduled_for, priority, created_at)  -- poll do worker
  idx_ca_jobs_group: (group_id, status)                             -- group completion
  idx_ca_jobs_seller: (seller_slug, created_at DESC)                -- admin queries
```

### ca_tokens
Tokens OAuth do Conta Azul (single row, id=1).
```
id (1), access_token, refresh_token, expires_at
```

### webhook_events
Log de todos os webhooks recebidos.
```
id, seller_slug, topic, action, resource, data_id,
raw_payload (jsonb), status (received|unmatched), created_at
```

### faturamento
Totais diarios de faturamento por empresa (alimenta dashboard).
```
empresa, data (date), valor, source (sync|manual), updated_at
UNIQUE(empresa, data)
```

### revenue_lines
Linhas de receita para o dashboard.
```
empresa (PK), grupo, segmento, seller_id, source, active, created_at
```

### goals
Metas mensais por empresa.
```
empresa, grupo, year, month, valor
UNIQUE(empresa, year, month)
```

### meli_tokens
Tokens ML legados (migrados do Supabase antigo). Referencia por account_name.
```
account_name (PK text), seller_id (FK sellers), refresh_token, access_token,
access_token_expires_at, updated_at
```

### admin_config
Password hash do admin (single row, id=1).
```
id (1), password_hash (bcrypt)
```

### mp_expenses
Classificacao de pagamentos non-order (boletos, SaaS, cashback, transferencias).
```
id (PK bigserial), seller_slug (FK sellers), payment_id (text; id numerico ou chave composta "id:tipo"),
expense_type (bill_payment|subscription|darf|cashback|collection|transfer_pix|transfer_intra|deposit|savings_pot|other|difal|faturas_ml|reembolso_disputa|dinheiro_retido|entrada_dinheiro|debito_envio_ml|liberacao_cancelada|reembolso_generico|deposito_avulso|debito_divida_disputa|debito_troca|bonus_envio),
expense_direction (expense|income|transfer),
ca_category, auto_categorized (bool),
amount, description, business_branch, operation_type, payment_method,
external_reference, febraban_code,
date_created, date_approved, beneficiary_name, notes, source (payments_api|extrato),
status (pending_review|auto_categorized|manually_categorized|exported),
exported_at, raw_payment (jsonb), created_at, updated_at
UNIQUE(seller_slug, payment_id)
```

### release_report_fees
Dados parseados do release report para audit trail e reconciliacao de fees.
```
id (PK bigserial), seller_slug (FK sellers), source_id, release_date (date),
description, record_type, gross_amount, mp_fee_amount, financing_fee_amount,
shipping_fee_amount, taxes_amount, coupon_amount, net_credit_amount, net_debit_amount,
external_reference, order_id, payment_method, created_at
UNIQUE(seller_slug, source_id, release_date, description)

Indexes:
  idx_rrf_seller_source: (seller_slug, source_id)
  idx_rrf_seller_date: (seller_slug, release_date)
```

### sync_state
Cursor de sincronizacao persistente do daily sync e legacy export.
```
seller_slug, key, state (jsonb), updated_at
```

---

## 7. Fluxo Principal: Payment → CA

```
Daily Sync (00:01 BRT, D-1 a D-3) ou Backfill manual
    │
    ├─ search_payments() pagina todos payments do periodo
    │   (busca por date_approved + date_last_updated, deduplica por payment_id)
    ├─ Filtra already_done (payments + mp_expenses)
    └─ Para cada payment:
           │
           ├─ Tem order_id? → process_payment_webhook(payment_data=payment)
           ├─ Sem order_id? → classify_non_order_payment() → mp_expenses
           ├─ Filtros order: skip se marketplace_shipment, collector_id (purchase)
           │
           ├─ status = "approved" / "in_mediation"
           │   └─ _process_approved()
           │       ├─ GET ML order (titulo do item)
           │       ├─ Extrai taxas de charges_details (source of truth)
           │       ├─ comissao = soma fee (from=collector, exclui financing_fee)
           │       ├─ frete_seller = max(0, soma shipping from=collector - shipping_amount)
           │       ├─ Persiste processor_fee/processor_shipping em payments
           │       ├─ Enqueue: receita (contas-a-receber)
           │       ├─ Enqueue: comissao (contas-a-pagar) se > 0
           │       ├─ Enqueue: frete (contas-a-pagar) se > 0
           │       └─ Se net_real > net_calculado: Enqueue receita de subsidio (1.3.7)
           │
           ├─ status = "charged_back" + status_detail = "reimbursed"
           │   └─ Trata como approved (ML cobriu o chargeback)
           │
           ├─ status = "refunded" + status_detail = "by_admin"
           │   ├─ Se JA synced: _process_refunded() (webhook: estornar receita existente)
           │   └─ Se NAO synced: SKIP (backfill: novos payments split cobrem a receita)
           │
           ├─ status = "refunded" / "charged_back"
           │   └─ _process_refunded()
           │       ├─ Se nunca synced: cria receita+despesas primeiro
           │       ├─ Enqueue: estorno receita (contas-a-pagar)
           │       └─ Enqueue: estorno taxa (contas-a-receber) se refund total
           │
           └─ status = "cancelled" / "rejected" → skip
```

### CaWorker (background loop)
```
Poll ca_jobs (pending/failed, scheduled_for <= now) → claim atomico
    │
    ├─ POST/GET no endpoint CA com payload
    ├─ 2xx → completed (salva protocolo)
    ├─ 401 → invalidate token cache, retry
    ├─ 429/5xx → failed + backoff (30s, 120s, 480s)
    ├─ 4xx outro → dead (dead letter)
    └─ Quando todos jobs de um group_id completam → payment status = "synced"
```

### Baixas (diario, 10h BRT)
```
Scheduler → para cada seller ativo:
    ├─ Busca parcelas abertas no CA (vencimento <= hoje)
    ├─ ReleaseChecker: verifica money_release_status no ML
    │   ├─ Preload do Supabase (raw_payment cache)
    │   └─ Re-check via ML API se release_date passada mas status "pending"
    ├─ Split: released/bypass → processar | pending → skip
    └─ Enqueue baixa para cada parcela liberada
```

### Financial Closing (11:30 BRT)
```
Para cada seller ativo:
    ├─ Auto lane: verifica payments + ca_jobs (synced, queued, dead)
    ├─ Manual lane: verifica mp_expenses + expense_batches (exported, imported)
    └─ Gera relatorio por dia/seller (closed = auto ok + manual ok)
```

### Nightly Pipeline (quando habilitado)
```
Substitui schedulers individuais. Execucao sequencial:
    1. sync_all_sellers() → Daily sync de payments
    2. validate_release_fees_all_sellers() → Valida fees vs release report, cria ajustes CA
    3. ingest_extrato_all_sellers() → Ingesta lacunas do account_statement
    4. _run_baixas_all_sellers() → Baixas
    5. run_legacy_daily_for_all() → Legacy export (dias configurados)
    6. check_extrato_coverage_all_sellers() → Verifica 100% cobertura do extrato
    7. _run_financial_closing() → Fechamento financeiro (inclui coverage data)
```

---

## 8. Code Map — Assinaturas de Todas as Funcoes

### app/services/processor.py — CORE

```python
def _to_brt_date(iso_str: str) -> str:
    """Converte ISO datetime ML (UTC-4) → BRT date (YYYY-MM-DD).
    Late-night sales cross midnight: 23:45 UTC-4 = 00:45 BRT → dia seguinte."""

def _extract_processor_charges(payment: dict) -> tuple[float, float, str | None, float, float]:
    """Extrai fee/frete de charges_details e reconcilia net calculado vs net real."""

def _build_parcela(descricao, data_vencimento, conta_financeira, valor, nota="") -> dict:
    """Monta parcela CA v2. Inclui detalhe_valor com valor_bruto e valor_liquido."""

def _build_evento(data_competencia, valor, descricao, observacao, contato,
                  conta_financeira, categoria, centro_custo, parcela,
                  rateio_centro_custo=True) -> dict:
    """Monta evento financeiro CA v2 com rateio e condicao_pagamento."""

def _build_despesa_payload(seller, data_competencia, data_vencimento,
                           valor, descricao, observacao, categoria, nota_parcela="") -> dict:
    """Build conta-a-pagar payload completo. Baixa feita separadamente pelo job /baixas."""

async def process_payment_webhook(seller_slug: str, payment_id: int, payment_data: dict = None):
    """Entry point principal. Classifica payment e despacha para handler correto.
    payment_data: if provided, skips API fetch (used by daily_sync)."""

async def _process_approved(db, seller, payment, existing):
    """EVENTO 1: Venda aprovada. Cria receita + comissao + frete + subsidio (se houver)."""

async def _process_partial_refund(db, seller, payment):
    """Refund parcial (status_detail='partially_refunded'). Estornos proporcionais."""

async def _process_refunded(db, seller, payment, existing):
    """EVENTO 4: Devolucao total. Cria receita original se necessario + estornos."""

def _upsert_payment(db, seller_slug, payment, status, error=None, ca_evento_id=None,
                    processor_fee=None, processor_shipping=None):
    """Insere ou atualiza payment no Supabase (idempotencia)."""
```

### app/services/ca_api.py — Cliente Conta Azul

```python
async def _get_ca_token() -> str:
    """Token CA com cache em memoria + refresh via OAuth2 com rotation.
    Lock asyncio previne refresh concorrente."""

async def _refresh_access_token(refresh_token) -> tuple[str, int, str | None]:
    """POST auth.contaazul.com/oauth2/token. Retorna (access, expires_in, new_refresh)."""

async def _request_with_retry(method, url, max_retries=3, **kwargs) -> Response:
    """HTTP com retry em 401 (re-auth), 429, 5xx. Respeita rate limiter global."""

async def criar_conta_receber(payload) -> dict:
    """POST /v1/financeiro/eventos-financeiros/contas-a-receber"""

async def criar_conta_pagar(payload) -> dict:
    """POST /v1/financeiro/eventos-financeiros/contas-a-pagar"""

async def listar_parcelas_evento(evento_id) -> list:
    """GET /v1/financeiro/eventos-financeiros/{id}/parcelas"""

async def buscar_parcelas_pagar(descricao, data_venc_de, data_venc_ate) -> list:
    """GET .../contas-a-pagar/buscar (filtro por descricao + datas)"""

async def buscar_parcelas_abertas_pagar(conta_id, data_de, data_ate, pagina, tamanho) -> tuple[list, int]:
    """GET .../contas-a-pagar/buscar (filtro por conta financeira + status aberto)"""

async def buscar_parcelas_abertas_receber(conta_id, data_de, data_ate, pagina, tamanho) -> tuple[list, int]:
    """GET .../contas-a-receber/buscar (filtro por conta financeira + status aberto)"""

async def listar_contas_financeiras() -> list:
    """GET /v1/conta-financeira — todas as contas (paginado)"""

async def listar_centros_custo() -> list:
    """GET /v1/centro-de-custo — todos os centros (paginado)"""

async def criar_baixa(parcela_id, data_pagamento, valor, conta_financeira) -> dict:
    """POST /v1/.../parcelas/{id}/baixa"""
```

### app/services/ml_api.py — Cliente ML/MP

```python
async def _get_token(seller_slug) -> str:
    """Token ML do seller. Auto-refresh se expirado."""

async def get_payment(seller_slug, payment_id) -> dict:
    """GET /v1/payments/{id}"""

async def get_order(seller_slug, order_id) -> dict:
    """GET /orders/{id}"""

async def get_shipment_costs(seller_slug, shipment_id) -> dict:
    """GET /shipments/{id}/costs"""

async def search_payments(seller_slug, begin_date, end_date, offset=0, limit=50, range_field="date_approved") -> dict:
    """GET /v1/payments/search — busca por periodo (date_approved/date_last_updated/money_release_date)."""

async def fetch_user_info(access_token) -> dict:
    """GET /users/me — perfil ML"""

async def exchange_code(code) -> dict:
    """POST /oauth/token — troca authorization_code por tokens"""

async def fetch_paid_orders(seller_slug, date_str) -> dict:
    """Busca orders pagos no dia → {valor, order_count, fraud_skipped}"""

async def get_release_report_config(seller_slug) -> dict:
    """GET /v1/account/release_report/config"""

async def configure_release_report(seller_slug) -> dict:
    """PUT /v1/account/release_report/config - configura colunas com fee breakdown."""
```

### app/services/ca_queue.py — Fila Persistente + Worker

```python
async def enqueue(seller_slug, job_type, ca_endpoint, ca_payload,
                  idempotency_key, group_id, priority, ca_method, scheduled_for) -> dict:
    """Insert job em ca_jobs. Retorna existente em conflito de idempotencia."""

# Wrappers (1:1 com call sites do processor):
async def enqueue_receita(seller_slug, payment_id, payload) -> dict      # priority=10
async def enqueue_comissao(seller_slug, payment_id, payload) -> dict     # priority=20
async def enqueue_frete(seller_slug, payment_id, payload) -> dict        # priority=20
async def enqueue_partial_refund(seller_slug, payment_id, index, payload) -> dict
async def enqueue_estorno(seller_slug, payment_id, payload) -> dict
async def enqueue_estorno_taxa(seller_slug, payment_id, payload) -> dict
async def enqueue_baixa(seller_slug, parcela_id, payload, scheduled_for) -> dict  # priority=30

class CaWorker:
    """Background loop: poll → claim atomico → execute → retry/dead."""
    async def start()               # Inicia loop + recover stuck jobs
    async def stop()                # Para gracefully
    async def _poll_next_job()      # Busca + claim atomico do proximo job
    async def _execute_job(job)     # POST/GET no CA, trata response
    def _mark_retryable(db, job, error, now)  # Backoff: 30s, 120s, 480s → dead
    async def _check_group_completion(group_id)  # Marca payment synced quando grupo completa
```

### app/services/daily_sync.py — Daily Sync

```python
async def _daily_sync_scheduler():
    """Async loop, roda as 00:01 BRT. Covers D-1 to D-3."""

async def sync_all_sellers(lookback_days=3) -> list[dict]:
    """Sync todos os sellers ativos. Retorna lista de resultados."""

async def sync_seller_payments(seller_slug, begin_date, end_date) -> dict:
    """Sync payments de um seller. Busca por date_approved + date_last_updated.
    Orders → processor, non-orders → classifier.
    Retorna {orders_processed, expenses_classified, skipped, errors}."""

def _compute_sync_window(cursor, lookback_days, seller_slug) -> tuple:
    """Calcula janela de sync baseada no cursor persistido."""

def _load_sync_cursor(db, seller_slug) -> dict | None:
    """Carrega cursor de sync do sync_state."""

def _persist_sync_cursor(db, seller_slug, cursor_data):
    """Persiste cursor de sync no sync_state."""
```

### app/services/expense_classifier.py — Classificador Non-Order

```python
AUTO_RULES = [...]  # Lista extensivel de regras de auto-categorizacao

def _extract_branch(payment) -> str:
    """Extrai point_of_interaction.business_info.branch."""

def _extract_unit(payment) -> str:
    """Extrai point_of_interaction.business_info.unit."""

def _extract_febraban(payment) -> str | None:
    """Extrai codigo Febraban dos references."""

def _match_rule(rule, payment, branch) -> bool:
    """Testa se uma auto-rule faz match."""

def _classify(payment) -> tuple[expense_type, direction, category, auto, description]:
    """Arvore de decisao: partition→skip, cashback→income, bill→expense, etc."""

async def classify_non_order_payment(db, seller_slug, payment) -> dict | None:
    """Classifica e salva em mp_expenses. Retorna None se skip (partition/addition)."""
```

### app/services/financial_closing.py — Fechamento Financeiro

```python
async def compute_seller_financial_closing(seller_slug, date_from, date_to) -> dict:
    """Computa fechamento financeiro de um seller (auto + manual lanes)."""

async def run_financial_closing_for_all(date_from, date_to) -> list[dict]:
    """Roda fechamento para todos os sellers ativos."""

def get_last_financial_closing() -> dict:
    """Retorna resultado do ultimo fechamento."""

def _compute_auto_lane(db, seller_slug, date_from, date_to) -> dict:
    """Lane automatica: payments + ca_jobs status."""

def _compute_manual_lane(db, seller_slug, date_from, date_to) -> dict:
    """Lane manual: mp_expenses + expense_batches status."""
```

### app/services/legacy_daily_export.py — Export Legado

```python
async def run_legacy_daily_for_seller(seller_slug, target_day, upload) -> dict:
    """Baixa account_statement, roda reconciliacao legada, gera ZIP, faz upload."""

async def run_legacy_daily_for_all(target_day, upload) -> list[dict]:
    """Roda export legado para todos os sellers ativos."""

def get_legacy_daily_status(seller_slug=None) -> dict:
    """Retorna status dos ultimos exports legados."""

async def _legacy_daily_scheduler():
    """Scheduler do export legado. Hora configuravel via env."""
```

### app/services/legacy_bridge.py — Bridge Legado

```python
async def run_legacy_reconciliation(extrato, dinheiro, vendas, pos_venda, liberacoes, centro_custo) -> dict:
    """Roda reconciliacao usando motor legado. Retorna resultado + erros."""

def build_legacy_expenses_zip(resultado) -> tuple[io.BytesIO, dict]:
    """Monta ZIP com PAGAMENTO_CONTAS.xlsx + TRANSFERENCIAS.xlsx a partir do resultado."""
```

### app/services/legacy_engine.py — Motor Legado

```python
def processar_conciliacao(arquivos, centro_custo="NETAIR") -> dict:
    """Motor de reconciliacao legado completo. ~1500 linhas. Processa CSVs do ML/MP."""

def gerar_xlsx_completo(rows, output_path) -> bool:
    """Gera XLSX com formatacao para importar no CA."""

def gerar_xlsx_resumo(rows, output_path) -> bool:
    """Gera XLSX de resumo."""
```

### app/services/release_report_sync.py — Sync Release Report

```python
async def sync_release_report(seller_slug, begin_date, end_date) -> dict:
    """Baixa release_report do ML e synca linhas para mp_expenses."""

def _classify_payout(row, same_day_payouts) -> tuple[str, str, str]:
    """Classifica linha de payout do release report."""

def _classify_credit(row) -> tuple[str, str, str, str | None]:
    """Classifica linha de credito do release report."""
```

### app/services/release_report_validator.py — Validacao de Fees

```python
async def validate_release_fees_for_seller(seller_slug, begin_date, end_date) -> dict:
    """Compara processor_fee/shipping com MP_FEE/SHIPPING do release report.
    Cria ajustes CA (contas-a-pagar) para diferencas."""

async def validate_release_fees_all_sellers(lookback_days=3) -> list[dict]:
    """Valida fees para todos os sellers ativos (D-1 a D-{lookback_days})."""

def get_last_validation_result() -> dict:
    """Retorna resultado da ultima validacao."""

def _parse_release_report_with_fees(csv_bytes) -> list[dict]:
    """Parse CSV do release report com colunas de fee breakdown."""
```

### app/services/extrato_ingester.py — Ingestao de Lacunas do Extrato

```python
async def ingest_extrato_for_seller(seller_slug, begin_date, end_date) -> dict:
    """Ingere linhas do account_statement nao cobertas por payments/mp_expenses.
    Upsert em mp_expenses com source="extrato" e payment_id composto."""

async def ingest_extrato_all_sellers(lookback_days=3) -> list[dict]:
    """Ingestao para todos os sellers ativos (D-1 a D-{lookback_days})."""

def get_last_ingestion_result() -> dict:
    """Retorna resultado da ultima ingestao."""
```

### app/services/extrato_coverage_checker.py — Cobertura do Extrato

```python
async def check_extrato_coverage(seller_slug, begin_date, end_date) -> dict:
    """Verifica que TODAS as linhas do extrato sao cobertas por payments, mp_expenses ou legacy.
    Retorna {total_lines, covered_by_api, covered_by_expenses, uncovered, coverage_pct}."""

async def check_extrato_coverage_all_sellers(lookback_days=3) -> list[dict]:
    """Coverage check para todos os sellers ativos."""

def get_last_coverage_result() -> dict:
    """Retorna resultado do ultimo coverage check."""
```

### app/services/onboarding_backfill.py — Backfill de Ativacao (Onboarding V2)

```python
async def run_onboarding_backfill(seller_slug: str) -> None:
    """Backfill historico por money_release_date (ca_start_date -> ontem)."""

async def retry_backfill(seller_slug: str) -> None:
    """Re-dispara backfill com retomada idempotente."""

def get_backfill_status(seller_slug: str) -> dict:
    """Retorna ca_backfill_status/started/completed/progress."""
```

### app/services/faturamento_sync.py — Sync Periodico

```python
class FaturamentoSyncer:
    """Polls ML paid orders a cada N minutos e upsert em faturamento."""
    async def start()                        # Inicia scheduler
    async def stop()                         # Para
    async def sync_all() -> list[dict]       # Sync todos os sellers ativos
    def _get_syncable_sellers() -> list      # Sellers com dashboard_empresa + ML tokens
    def _upsert_faturamento(empresa, date, valor) -> bool  # Upsert Supabase
```

### app/services/release_checker.py — Verificacao de Liberacao

```python
class ReleaseChecker:
    """Verifica money_release_status do ML antes de baixas."""
    async def check_parcelas_batch(parcelas) -> dict[str, str]:
        """Retorna {parcela_id: "released"|"pending"|"unknown"|"bypass"}"""
    async def _preload(payment_ids, order_ids):
        """Bulk-load do Supabase (raw_payment cache)"""
    async def _recheck_ml_api(payment_ids) -> dict[int, str]:
        """Re-fetch do ML API para payments com release_date passada"""
```

### app/services/rate_limiter.py

```python
class TokenBucket:
    """9 req/s burst, 540 req/min guard. Singleton: rate_limiter."""
    async def acquire()  # Aguarda token disponivel
```

### app/services/onboarding.py

```python
async def create_signup(slug, name, email=None) -> dict:
    """Cria seller com pending_approval."""
async def approve_seller(seller_id, config) -> dict:
    """Aprova seller + cria revenue_line + 12 goals vazias."""
async def reject_seller(seller_id) -> dict:
    """Rejeita seller (suspended)."""
async def activate_seller(slug):
    """Marca seller como active (pos-OAuth ML)."""
```

### app/models/sellers.py

```python
CA_CATEGORIES = {
    "venda_ml":           "78f42170-...",  # 1.1.1 MercadoLibre
    "comissao_ml":        "699d6072-...",  # 2.8.2 Comissoes Marketplace
    "frete_mercadoenvios":"6ccbf8ed-...",  # 2.9.4 MercadoEnvios
    "frete_full":         "27c8de66-...",  # 2.9.10 Frete Full
    "devolucao":          "713ee216-...",  # 1.2.1 Devolucoes
    "estorno_taxa":       "c4cc890c-...",  # 1.3.4 Estornos de Taxas
    "estorno_frete":      "2c0ef767-...",  # 1.3.7 Estorno de Frete
    "antecipacao":        "7e9efb50-...",  # 2.11.9 Antecipacao
    "tarifa_pagamento":   "d77aa9d6-...",  # 2.11.8 Tarifas
}
CA_CONTATO_ML = "b247cccb-38a2-4851-bf0e-700c53036c2c"  # Contato "MERCADO LIVRE"

def get_seller_config(db, seller_slug) -> dict | None
def get_seller_by_ml_user_id(db, ml_user_id) -> dict | None
def get_all_active_sellers(db) -> list[dict]
```

---

## 9. Rotas da API

> Para documentacao completa com request/response bodies e exemplos, ver `API_DOCUMENTATION.md`.

### Webhooks
| Metodo | Rota | Descricao |
|--------|------|-----------|
| POST | `/webhooks/ml` | Receiver ML/MP. Loga evento mas NAO processa (daily sync cuida) |

### Backfill
| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/backfill/{seller}?begin_date=...&end_date=...&dry_run=true&max_process=0&concurrency=10&reprocess_missing_fees=true` | Backfill retroativo. dry_run=true lista, false processa |

### Baixas
| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/baixas/processar/{seller}?dry_run=true&verify_release=true&data_ate=...&lookback_days=90` | Baixas de parcelas vencidas. Verifica release no ML |

### Auth ML
| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/auth/ml/connect?seller=xxx` | Redirect para OAuth ML |
| GET | `/auth/ml/install` | Self-service (cria seller automaticamente) |
| GET | `/auth/ml/callback?code=...&state=...` | Callback OAuth ML |

### Auth CA
| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/auth/ca/connect` | Redirect para OAuth CA (Cognito) |
| GET | `/auth/ca/callback?code=...` | Callback OAuth CA |
| GET | `/auth/ca/status` | Status dos tokens CA |

### Admin (requer X-Admin-Token header)
| Metodo | Rota | Descricao |
|--------|------|-----------|
| POST | `/admin/login` | Login → session token (24h) |
| GET | `/admin/sellers` | Lista todos os sellers |
| GET | `/admin/sellers/pending` | Sellers aguardando aprovacao |
| POST | `/admin/sellers/{id}/approve` | Aprova seller com config |
| POST | `/admin/sellers/{id}/reject` | Rejeita seller |
| PATCH | `/admin/sellers/{id}` | Atualiza seller |
| GET/POST | `/admin/revenue-lines` | CRUD revenue lines |
| PATCH | `/admin/revenue-lines/{empresa}` | Atualiza revenue line |
| DELETE | `/admin/revenue-lines/{empresa}` | Desativa revenue line (soft delete) |
| GET | `/admin/goals?year=2026` | Lista metas |
| POST | `/admin/goals/bulk` | Upsert metas em lote |
| POST | `/admin/sync/trigger` | Trigger sync faturamento |
| GET | `/admin/sync/status` | Status ultimo sync |
| POST | `/admin/closing/trigger?date_from=...&date_to=...` | Trigger financial closing |
| GET | `/admin/closing/status` | Resultado do ultimo closing |
| GET | `/admin/closing/seller/{seller}?date_from=...&date_to=...` | Closing detalhado por seller |
| POST | `/admin/release-report/sync` | Sync release report → mp_expenses |
| POST | `/admin/release-report/validate/{seller}?begin_date=...&end_date=...` | Validar fees vs release report |
| POST | `/admin/release-report/validate-all?lookback_days=3` | Validar fees todos sellers |
| GET | `/admin/release-report/validation-status` | Resultado da ultima validacao |
| POST | `/admin/release-report/configure/{seller}` | Configurar colunas do release report |
| GET | `/admin/release-report/config/{seller}` | Ver config do release report |
| GET | `/admin/extrato/coverage/{seller}?date_from=...&date_to=...` | Coverage check do extrato |
| POST | `/admin/extrato/coverage-all?lookback_days=3` | Coverage check todos sellers |
| GET | `/admin/extrato/coverage-status` | Resultado do ultimo coverage check |
| POST | `/admin/sellers/{slug}/activate` | Ativa seller (dashboard_only ou dashboard_ca) |
| POST | `/admin/sellers/{slug}/upgrade-to-ca` | Migra seller para dashboard_ca + dispara backfill |
| GET | `/admin/sellers/{slug}/backfill-status` | Status/progresso do onboarding backfill |
| POST | `/admin/sellers/{slug}/backfill-retry` | Re-dispara onboarding backfill |
| GET | `/admin/onboarding/install-link` | Link para install OAuth ML |
| POST | `/admin/extrato/ingest/{seller}?begin_date=...&end_date=...` | Ingestao manual de lacunas do extrato |
| POST | `/admin/extrato/ingest-all?lookback_days=3` | Ingestao de extrato para todos os sellers |
| GET | `/admin/extrato/ingestion-status` | Resultado da ultima ingestao de extrato |
| POST | `/admin/legacy/daily/trigger?seller_slug=...&target_day=...&upload=true` | Trigger export legado |
| GET | `/admin/legacy/daily/status?seller_slug=...` | Status exports legados |
| GET | `/admin/ca/contas-financeiras` | Lista contas CA |
| GET | `/admin/ca/centros-custo` | Lista centros de custo CA |

### Dashboard (publico)
| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/dashboard/revenue-lines` | Linhas de receita ativas |
| GET | `/dashboard/goals?year=2026` | Metas do ano |
| POST | `/dashboard/faturamento/entry` | Upsert manual |
| POST | `/dashboard/faturamento/delete` | Delete entrada |

### Queue
| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/queue/status` | Contagem de jobs por status |
| GET | `/queue/dead` | Lista dead-letter jobs |
| POST | `/queue/retry/{job_id}` | Retry manual de job dead |
| POST | `/queue/retry-all-dead` | Retry todos dead jobs |
| GET | `/queue/reconciliation/{seller}?date_from=...&date_to=...&sample_limit=200` | Reconciliacao operacional por seller |

### Expenses (requer X-Admin-Token header)
| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/expenses/{seller}?status=...&expense_type=...&direction=...&date_from=...&date_to=...` | Lista mp_expenses com filtros |
| PATCH | `/expenses/review/{seller}/{expense_id}` | Revisao manual de despesa |
| GET | `/expenses/{seller}/pending-summary?date_from=...&date_to=...` | Resumo pendentes por dia |
| GET | `/expenses/{seller}/stats` | Contadores por tipo/status |
| GET | `/expenses/{seller}/export?date_from=...&date_to=...&mark_exported=false` | ZIP com XLSX por dia |
| GET | `/expenses/{seller}/batches?status=...` | Lista lotes de exportacao |
| POST | `/expenses/{seller}/batches/{batch_id}/confirm-import` | Confirma importacao de lote |
| GET | `/expenses/{seller}/closing?date_from=...&date_to=...` | Status fechamento diario |
| POST | `/expenses/{seller}/legacy-export` | Bridge legado (multipart: extrato + CSVs) |

### Health/Debug
| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/health` | Health check |
| GET | `/debug/ca-token` | Testa refresh token CA |
| GET | `/debug/process-test` | Testa processamento de 1 payment |
| GET | `/debug/busca-parcela` | Testa busca de parcelas CA |

---

## 10. Background Tasks (Lifespan)

Iniciados no startup do FastAPI:

| Task | Intervalo | Funcao | Ativacao |
|------|-----------|--------|----------|
| **CaWorker** | Poll 1s | Processa fila ca_jobs → CA API | Sempre |
| **FaturamentoSyncer** | 5 min (config) | Sync ML orders → tabela faturamento | Sempre |
| **CA Token Refresh** | 30 min | Refresh proativo do token CA | Sempre |
| **Daily Sync Scheduler** | 1x/dia 00:01 BRT | Backfill D-1..D-3: orders → CA + non-orders → classifier | Quando `nightly_pipeline_enabled=false` |
| **Daily Baixa Scheduler** | 1x/dia 10h BRT | Processa baixas de todos os sellers | Quando `nightly_pipeline_enabled=false` |
| **Financial Closing Scheduler** | 1x/dia 11:30 BRT | Fechamento financeiro | Quando `nightly_pipeline_enabled=false` |
| **Legacy Daily Export** | 1x/dia (config BRT) | Baixa account_statement, monta ZIP, upload | Quando `legacy_daily_enabled=true` e `nightly_pipeline_enabled=false` |
| **Nightly Pipeline** | 1x/dia (config BRT) | Orquestracao sequencial: sync → fee validation → extrato ingestion → baixas → legacy → coverage check → closing | Quando `nightly_pipeline_enabled=true` |

---

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
receita de subsidio (`Subsídio ML - Payment {id}`, categoria 1.3.7).

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

---

## 12. IDs Importantes

| Item | ID |
|------|------|
| Supabase Project | `wrbrbhuhsaaupqsimkqz` |
| 141AIR ML user_id | `1963376627` |
| CA Conta Bancaria 141AIR | `fea5f1de-fd23-4462-9b43-0a2c6ae4df04` |
| CA Contato MERCADO LIVRE | `b247cccb-38a2-4851-bf0e-700c53036c2c` |
| CA Centro Custo 141AIR Variavel | `f7c214a6-be2f-11f0-8080-ab23c683d2a1` |
| Cognito Client ID | `6ri07ptg5k2u7dubdlttg3a7t8` |
| Cognito User Pool | `sa-east-1_Vp83J11wA` |

---

## 13. Idempotencia e Resiliencia

- **Payments**: upsert por `(seller_slug, ml_payment_id)`. Reprocessar e seguro.
- **ca_jobs**: unique constraint em `idempotency_key`. Key pattern: `{seller}:{payment_id}:{tipo}`.
- **Retry**: backoff exponencial (30s → 120s → 480s) com max 3 tentativas, depois dead letter.
- **Stuck jobs**: recover automatico no startup (processing > 5min → failed).
- **Concurrent refresh**: asyncio.Lock previne race condition no refresh do token CA.
- **Rate limit**: token bucket compartilhado entre CaWorker e ca_api reads.
- **Sync cursor**: daily sync persiste cursor em `sync_state` para evitar gaps entre execucoes.

---

## 14. Convencoes de Codigo

- **Linguagem do codigo**: Ingles (variaveis, funcoes, logs)
- **Linguagem dos docs/plans**: Portugues BR
- **Framework**: FastAPI com routers modulares
- **Async**: Todo I/O e async (httpx, Supabase via sync client mas wrapped)
- **Logs**: `logging.getLogger(__name__)` em cada modulo
- **Env vars**: via pydantic-settings (BaseSettings + .env)
- **Sem ORMs**: queries diretas via Supabase SDK
- **Testes**: simulacao com dados reais via `simulate_backfill.py` (ver secao 18)

---

## 15. Dashboard

O dashboard React tem seu **proprio CLAUDE.md** em `dashboard/claude.md` com documentacao completa de 780+ linhas cobrindo componentes, hooks, tipos, e regras de negocio.

Para trabalhar no dashboard, consulte esse arquivo. Resumo:
- React 19 + TypeScript + Vite 7 + Recharts 3
- 4 views: Geral, Metas, Entrada, Linhas
- Motor de calculos centralizado em `goalCalculator.ts`
- Sem backend proprio (SPA → Supabase direto + admin API)
- CSS Modules, sem Tailwind
- PWA instalavel (offline read-only)
- Realtime subscription via Supabase
- Regra D-1: indicadores "esperado" usam ontem como referencia
- Regra AR CONDICIONADO: dia util = 120%, fim de semana = 50%

---

## 16. Checklist Antes de Modificar

- [ ] Leu este CLAUDE.md e entendeu o fluxo payment → CA?
- [ ] Comissao usa formula `amount - net - shipping` (NAO fee_details)?
- [ ] financing_fee NAO gera despesa?
- [ ] Datas de competencia usam `date_approved` (NAO date_created)?
- [ ] Payments sem order_id / marketplace_shipment / collector sao filtrados?
- [ ] Novos endpoints respeitam rate_limiter?
- [ ] Jobs CA usam idempotency_key unica?
- [ ] CA API responses tratam o formato async (protocolo)?

---

## 17. Instrucoes para LLMs / Devs

**NUNCA:**
- Usar `fee_details` como fonte de taxas (e unreliavel)
- Criar despesa para `financing_fee` (e net-neutral)
- Usar `date_created` para data de competencia (usar `date_approved` que alinha com XLSX ML)
- Usar `settlement_report` como base primaria para fechamento diario de caixa
- Chamar CA API diretamente sem passar pelo rate_limiter
- Fazer refresh de token CA sem asyncio.Lock
- Processar payments com `description == "marketplace_shipment"`
- Assumir que CA API retorna `id` (retorna `protocolo`)

**SEMPRE:**
- Usar `charges_details` para breakdown de taxas
- Usar `_to_brt_date(date_approved)` para competencia
- Usar `account_statement` (`release_report`/`bank_report`) para comparativo de caixa diario
- Enfileirar via `ca_queue.enqueue_*()` em vez de chamar CA API diretamente
- Verificar `money_release_status` antes de dar baixa
- Manter idempotencia via `_upsert_payment()` e `idempotency_key`
- Logar em ingles com payment_id/seller_slug para rastreabilidade
- Rodar `simulate_backfill.py` antes de processar novo seller ou apos alterar processor.py

---

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

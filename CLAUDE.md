# API Conciliador V2/V3 - Lever Money

> **Leia este documento ANTES de qualquer alteracao.** Ele substitui a necessidade de ler cada arquivo individualmente.
> Para documentacao completa de endpoints (request/response examples, status codes), consulte `API_DOCUMENTATION.md`.
> Para detalhes especificos (tabelas, rotas, regras de negocio, etc.), consulte os arquivos em `docs/`.

---

## 1. Visao Geral

Sistema de conciliacao automatica entre **Mercado Livre / Mercado Pago** e **Conta Azul ERP**. Para cada venda no ML, cria automaticamente no CA:
- **Receita** (contas-a-receber) com valor bruto da venda
- **Despesa comissao** (contas-a-pagar) com taxas ML/MP
- **Despesa frete** (contas-a-pagar) com custo MercadoEnvios
- **Baixas** automaticas quando dinheiro e liberado pelo ML

**V3:** Pagamentos sem order (boletos, SaaS, cashback, transferencias) sao classificados automaticamente na tabela `mp_expenses` e exportados em lote.
O fluxo atual de despesas no Admin inclui export ZIP + backup Google Drive assincrono + historico de batches com re-download deterministico por `batch_id`.

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
SUPABASE_SERVICE_ROLE_KEY= # Service role key (backend writes)
SUPABASE_KEY=          # Fallback (legacy deployments, may be read-only under RLS)
```

### Opcionais

| Variavel | Default | Descricao |
|----------|---------|-----------|
| `CA_ACCESS_TOKEN` | `""` | Token CA bootstrap |
| `CA_REFRESH_TOKEN` | `""` | Refresh token CA bootstrap |
| `BASE_URL` | `http://localhost:8000` | URL base para OAuth callbacks |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Origens CORS (comma-separated) |
| `SYNC_INTERVAL_MINUTES` | `1` | Intervalo sync faturamento |
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
│   │   ├── admin/               # Admin CRUD package (8 submodulos)
│   │   │   ├── _deps.py         # Auth dependency (require_admin, set_syncer)
│   │   │   ├── auth.py          # Login/logout
│   │   │   ├── sellers.py       # CRUD sellers + onboarding
│   │   │   ├── revenue.py       # Revenue lines + goals bulk
│   │   │   ├── closing.py       # Financial closing triggers
│   │   │   ├── extrato.py       # Account statement ops
│   │   │   ├── legacy.py        # Legacy export triggers
│   │   │   ├── release_report.py # Release report ops
│   │   │   └── ca_debug.py      # CA API debug endpoints
│   │   ├── dashboard_api.py     # Dashboard read API (publico)
│   │   ├── expenses/            # MP expenses package
│   │   │   ├── _deps.py         # Auth dependency compartilhada
│   │   │   ├── crud.py          # List/review expenses
│   │   │   ├── export.py        # Export ZIP + GDrive backup + batches + re-download
│   │   │   ├── closing.py       # Closing status por seller
│   │   │   └── legacy.py        # Legacy bridge endpoint
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
│   │   ├── gdrive_client.py     # Upload ZIP despesas no Google Drive (ROOT/DESPESAS/EMPRESA/YYYY-MM)
│   │   ├── release_report_sync.py # Sync release report → mp_expenses
│   │   ├── release_report_validator.py # Valida fees do processor vs release report
│   │   ├── extrato_ingester.py  # Ingesta lacunas do account_statement em mp_expenses
│   │   ├── extrato_coverage_checker.py # Verifica 100% cobertura do extrato
│   │   ├── ca_categories_sync.py # Sync categorias CA → ca_categories.json
│   │   └── legacy/              # Subpacote legado
│   │       ├── daily_export.py  # Export legado diario (release_report → ZIP → upload)
│   │       ├── bridge.py        # Bridge para formato legado (CSV → XLSX)
│   │       └── engine.py        # Motor de reconciliacao legado (~1500 linhas)
│   └── static/
│       └── install.html         # Landing page self-service install
├── dashboard/                   # React SPA (tem seu proprio CLAUDE.md)
├── migrations/
│   ├── 003_expenses_batches_sync_state.sql    # Expense batch tracking + sync_state
│   ├── 004_onboarding_v2.sql                  # Onboarding V2 schema
│   └── 005_expense_batches_gdrive_snapshot.sql # gdrive_* em expense_batches + snapshot_payload
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

## 6. Expenses Export + GDrive (Admin)

### Backend
- `GET /expenses/{seller_slug}/stats` retorna contadores explicitos:
  - `pending_review_count`, `auto_categorized_count`
  - Aceita `status_filter` query param (ex: `pending_review,auto_categorized`)
- `GET /expenses/{seller_slug}/export` aceita `gdrive_backup` e `status_filter`:
  - Header sempre presente: `X-Export-Batch-Id`
  - Header condicional: `X-GDrive-Status` (`queued` ou `skipped_no_drive_root`)
  - Upload para Drive roda em background (`asyncio.to_thread` + `asyncio.create_task`), sem bloquear o download
- `GET /expenses/{seller_slug}/batches` retorna envelope:
  - `{ seller, count, data }`
- `GET /expenses/{seller_slug}/batches/{batch_id}/download`:
  - Reconstroi ZIP via `snapshot_payload` de `expense_batch_items`
  - Preserva `manifest.csv` e `manifest_pagamentos.csv`
  - Suporta batch vazio (ZIP com `README.txt`), sem 404 indevido
  - Ordenacao deterministica por `expense_date` + `expense_id`

### Persistencia
- Migration `005_expense_batches_gdrive_snapshot.sql` adiciona:
  - `expense_batches`: `gdrive_status`, `gdrive_folder_link`, `gdrive_file_id`, `gdrive_file_link`, `gdrive_error`, `gdrive_updated_at`
  - `expense_batch_items`: `snapshot_payload jsonb`
- `_persist_batch_metadata()` persiste snapshot por item.
- `update_batch_gdrive_status()` atualiza apenas campos `gdrive_*`.

### Frontend (Seller Cards Grid)
- Aba **Despesas** no `AdminPanel` com layout de **cards por seller**:
  - Grid de cards para todos sellers ativos (ordenados alfabeticamente)
  - Cada card mostra: total despesas, valor total, pendentes, auto-categorizadas
  - Botao de export individual por seller card (com confirmacao se `pending_review > 0`)
  - Botao global **"Exportar Todos os Pendentes"** (exporta sequencialmente todos sellers com pendencias)
  - Historico de batches **colapsavel** por seller card
  - Status badges: `queued`, `uploaded`, `skipped`, `failed`
  - Links diretos para GDrive quando disponivel
  - Polling de status GDrive (12 tentativas, 5s intervalo)
  - Re-download por `batch_id`
  - Sem filtros de mes/ano (usa `status_filter=pending_review,auto_categorized`)
- Hook `useExpenses`:
  - `loadStats(sellerSlug, dateFrom?, dateTo?, statusFilter?)`
  - `exportAndBackup(sellerSlug, dateFrom?, dateTo?, statusFilter?)`
  - `loadBatches(sellerSlug)` (consome `payload.data`)
  - `redownloadBatchById(sellerSlug, batchId)`

### CORS
- `app/main.py` expoe headers para o frontend:
  - `X-Export-Batch-Id`
  - `X-GDrive-Status`

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

> Para detalhes sobre CaWorker, Baixas, Financial Closing e Nightly Pipeline, ver `docs/FLUXO_DETALHADO.md`.

---

## Documentacao Detalhada

Para detalhes especificos, consulte os docs abaixo:

| Doc | Conteudo |
|-----|----------|
| `docs/TABELAS.md` | Schema de todas as tabelas Supabase |
| `docs/CODE_MAP.md` | Assinaturas de todas as funcoes (por service/router) |
| `docs/ROTAS.md` | Todas as rotas da API com metodos e parametros |
| `docs/REGRAS_NEGOCIO.md` | Regras de negocio criticas (comissao, fees, datas, filtros, etc.) |
| `docs/FLUXO_DETALHADO.md` | Fluxo detalhado: CaWorker, Baixas, Closing, Nightly Pipeline |
| `docs/BACKGROUND_TASKS.md` | Background tasks (lifespan) |
| `docs/IDS_IMPORTANTES.md` | IDs de producao (Supabase, ML, CA) |
| `docs/IDEMPOTENCIA.md` | Idempotencia e resiliencia |
| `docs/TESTES.md` | Simulacao com dados reais + checklist de validacao |
| `API_DOCUMENTATION.md` | Documentacao completa de endpoints (request/response) |

---

## 14. Convencoes de Codigo

- **Linguagem do codigo**: Ingles (variaveis, funcoes, logs)
- **Linguagem dos docs/plans**: Portugues BR
- **Framework**: FastAPI com routers modulares
- **Async**: Todo I/O e async (httpx, Supabase via sync client mas wrapped)
- **Logs**: `logging.getLogger(__name__)` em cada modulo
- **Env vars**: via pydantic-settings (BaseSettings + .env)
- **Sem ORMs**: queries diretas via Supabase SDK
- **Testes**: simulacao com dados reais via `simulate_backfill.py` (ver `docs/TESTES.md`)

---

## 15. Dashboard

O dashboard React tem seu **proprio CLAUDE.md** em `dashboard/claude.md` com documentacao completa de 780+ linhas cobrindo componentes, hooks, tipos, e regras de negocio.

Para trabalhar no dashboard, consulte esse arquivo. Resumo:
- React 19 + TypeScript + Vite 7 + Recharts 3
- 5 views: Geral, Metas, Entrada, Linhas, Admin
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

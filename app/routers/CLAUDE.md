# app/routers/ -- API Router Layer

FastAPI routers mounted in `main.py`. Routers call into `app/services/` for business logic.
Thin layer: validate input, check auth, delegate to services, return response.

---

## File Listing

| File / Package | Prefix | Auth | Description |
|----------------|--------|------|-------------|
| `health.py` | `/health`, `/debug/*` | Public | Health check + debug endpoints (CA token test, process test, parcela search) |
| `webhooks.py` | `/webhooks/ml` | Public (HMAC) | ML/MP webhook receiver. Logs events to `webhook_events` but does NOT process payments (daily sync handles that). |
| `auth_ml.py` | `/auth/ml/*` | Public | OAuth2 flow for Mercado Livre. Connect, callback, self-service install for new sellers. |
| `auth_ca.py` | `/auth/ca/*` | Public | OAuth2 flow for Conta Azul (Cognito). Connect, callback, token status check. |
| `dashboard_api.py` | `/dashboard/*` | Public | Read-only API for React dashboard. Revenue lines, goals, faturamento upsert/delete. |
| `backfill.py` | `/backfill/{seller}` | Public | Manual retroactive payment processing. Supports dry_run, concurrency, fee reprocessing. |
| `baixas.py` | `/baixas/processar/{seller}` | Public | Processes baixas (payment settlements) for open parcelas. Verifies ML release status before executing. |
| `queue.py` | `/queue/*` | Public | CA job queue monitoring: status counts, dead-letter list, retry, reconciliation per seller. |
| `admin/` (package) | `/admin/*` | **X-Admin-Token** | Full admin CRUD split into 8 submodules (see below). |
| `expenses/` (package) | `/expenses/*` | **X-Admin-Token** | MP expenses: list/review, stats, export ZIP, batches, backup GDrive async, re-download, closing status, legacy bridge. |

### admin/ Package (8 submodules)

| File | Responsibility |
|------|---------------|
| `__init__.py` | Assembles router, re-exports `set_syncer` and `require_admin` |
| `_deps.py` | Auth dependency (`require_admin`), `set_syncer()` for FaturamentoSyncer |
| `auth.py` | Login/logout endpoints |
| `sellers.py` | CRUD sellers + onboarding lifecycle |
| `revenue.py` | Revenue lines + goals bulk endpoints |
| `closing.py` | Financial closing triggers |
| `extrato.py` | Account statement operations (ingestion, coverage check) |
| `legacy.py` | Legacy export triggers |
| `release_report.py` | Release report sync + validation triggers |
| `ca_debug.py` | CA API debug endpoints (token test, category sync) |

### expenses/ Package (5 modules)

| File | Responsibility |
|------|---------------|
| `__init__.py` | Assembles sub-router |
| `_deps.py` | Shared auth dependency |
| `crud.py` | List/review expenses |
| `export.py` | Export ZIP + GDrive backup + batches + re-download |
| `closing.py` | Closing status per seller |
| `legacy.py` | Legacy bridge endpoint |

---

## Auth Pattern

**Admin auth** is session-based:
1. `POST /admin/login` with password -> returns session token (24h TTL)
2. All protected endpoints require `X-Admin-Token: <session_token>` header
3. Auth dependency: `require_admin()` in `admin/_deps.py`, imported by `expenses` package
4. Password verified against bcrypt hash in `admin_config` table (single row)

**Public endpoints** have no auth requirement. `backfill`, `baixas`, and `queue` are
operationally sensitive but currently unauthenticated (intended for internal use).

---

## How Routers Call Services

- **admin/** submodules import from: `financial_closing`, `legacy_daily_export`, `release_report_validator`, `extrato_coverage_checker`, `extrato_ingester`, `onboarding_backfill`, `onboarding`, `ca_categories_sync`, `faturamento_sync` (via `set_syncer()`)
- **expenses/** package imports from: `legacy_bridge` (legacy-export), `gdrive_client` (backup ZIP), `admin.require_admin`
- **backfill.py** imports: `processor.process_payment_webhook`, `ml_api`
- **baixas.py** imports: `ca_api`, `ca_queue`, `release_checker`
- **webhooks.py** only writes to `webhook_events` table (no service calls)
- **dashboard_api.py** queries Supabase directly (no service imports)
- **auth_ml.py** imports: `ml_api.exchange_code`, `ml_api.fetch_user_info`
- **auth_ca.py** imports: `ca_api` token functions

---

## Conditional Router

`expenses` router package is only mounted when `settings.expenses_api_enabled` is true (see `main.py`).

---

## Expenses Contracts

- `GET /expenses/{seller_slug}/stats`
  - Inclui `pending_review_count` e `auto_categorized_count`
  - Aceita `status_filter` query param (ex: `pending_review,auto_categorized`)
- `GET /expenses/{seller_slug}/export`
  - Aceita `status_filter` query param (mutuamente exclusivo com `date_from/date_to`)
  - Header sempre presente: `X-Export-Batch-Id`
  - Header condicional: `X-GDrive-Status` quando `gdrive_backup=true`
  - Backup GDrive roda em background e NAO bloqueia download
- `GET /expenses/{seller_slug}/batches`
  - Resposta e envelope: `{ seller, count, data }`
- `GET /expenses/{seller_slug}/batches/{batch_id}/download`
  - Reconstrucao via `snapshot_payload` de `expense_batch_items`
  - Inclui manifests no ZIP
  - Batch vazio e valido (retorna README)

---

## Common Gotchas

- `webhooks.py` logs but does NOT process. Do not add processing logic there; daily sync is the ingestion mechanism.
- `backfill.py` and `baixas.py` look public but are meant for internal/admin use. Consider adding auth if exposed externally.
- `admin/_deps.py` exposes `set_syncer(syncer)` called from `main.py` to receive the `FaturamentoSyncer` instance for trigger/status endpoints.
- The dashboard SPA catch-all route in `main.py` can shadow API routes if the path prefix is not in `API_PREFIXES`.
- Para leitura dos headers customizados (`X-Export-Batch-Id`, `X-GDrive-Status`) no frontend, `main.py` precisa manter `CORSMiddleware.expose_headers`.

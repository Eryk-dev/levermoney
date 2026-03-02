# app/routers/ -- API Router Layer

FastAPI routers mounted in `main.py`. Routers call into `app/services/` for business logic.
Thin layer: validate input, check auth, delegate to services, return response.

---

## File Listing

| File | Prefix | Auth | Description |
|------|--------|------|-------------|
| `health.py` | `/health`, `/debug/*` | Public | Health check + debug endpoints (CA token test, process test, parcela search) |
| `webhooks.py` | `/webhooks/ml` | Public (HMAC) | ML/MP webhook receiver. Logs events to `webhook_events` but does NOT process payments (daily sync handles that). |
| `auth_ml.py` | `/auth/ml/*` | Public | OAuth2 flow for Mercado Livre. Connect, callback, self-service install for new sellers. |
| `auth_ca.py` | `/auth/ca/*` | Public | OAuth2 flow for Conta Azul (Cognito). Connect, callback, token status check. |
| `dashboard_api.py` | `/dashboard/*` | Public | Read-only API for React dashboard. Revenue lines, goals, faturamento upsert/delete. |
| `backfill.py` | `/backfill/{seller}` | Public | Manual retroactive payment processing. Supports dry_run, concurrency, fee reprocessing. |
| `baixas.py` | `/baixas/processar/{seller}` | Public | Processes baixas (payment settlements) for open parcelas. Verifies ML release status before executing. |
| `queue.py` | `/queue/*` | Public | CA job queue monitoring: status counts, dead-letter list, retry, reconciliation per seller. |
| `admin.py` | `/admin/*` | **X-Admin-Token** | Full admin CRUD: sellers, goals, revenue lines, sync triggers, closing, release reports, extrato, legacy export, onboarding, CA accounts. |
| `expenses.py` | `/expenses/*` | **X-Admin-Token** | MP expenses: list with filters, manual review, XLSX export, batch management, closing status, legacy bridge. |

---

## Auth Pattern

**Admin auth** is session-based:
1. `POST /admin/login` with password -> returns session token (24h TTL)
2. All protected endpoints require `X-Admin-Token: <session_token>` header
3. Auth dependency: `require_admin()` in `admin.py`, imported by `expenses.py`
4. Password verified against bcrypt hash in `admin_config` table (single row)

**Public endpoints** have no auth requirement. `backfill`, `baixas`, and `queue` are
operationally sensitive but currently unauthenticated (intended for internal use).

---

## How Routers Call Services

- **admin.py** imports from: `financial_closing`, `legacy_daily_export`, `release_report_validator`, `extrato_coverage_checker`, `extrato_ingester`, `onboarding_backfill`, `onboarding`, `ca_categories_sync`, `faturamento_sync` (via `set_syncer()`)
- **expenses.py** imports from: `legacy_bridge` (for legacy-export endpoint), `admin.require_admin`
- **backfill.py** imports: `processor.process_payment_webhook`, `ml_api`
- **baixas.py** imports: `ca_api`, `ca_queue`, `release_checker`
- **webhooks.py** only writes to `webhook_events` table (no service calls)
- **dashboard_api.py** queries Supabase directly (no service imports)
- **auth_ml.py** imports: `ml_api.exchange_code`, `ml_api.fetch_user_info`
- **auth_ca.py** imports: `ca_api` token functions

---

## Conditional Router

`expenses.py` is only mounted when `settings.expenses_api_enabled` is true (see `main.py`).

---

## Common Gotchas

- `webhooks.py` logs but does NOT process. Do not add processing logic there; daily sync is the ingestion mechanism.
- `backfill.py` and `baixas.py` look public but are meant for internal/admin use. Consider adding auth if exposed externally.
- `admin.py` uses `set_syncer(syncer)` called from `main.py` to receive the `FaturamentoSyncer` instance for trigger/status endpoints.
- The dashboard SPA catch-all route in `main.py` can shadow API routes if the path prefix is not in `API_PREFIXES`.

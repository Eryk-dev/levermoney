# app/services/ -- Service Layer

Business logic and external API clients. All heavy I/O is async (httpx).
Routers call services; services never import routers.

---

## File Listing

| File | Responsibility |
|------|---------------|
| `processor.py` | **CORE.** Maps ML payments to CA financial events (receita, comissao, frete, estorno). Entry point: `process_payment_webhook()`. Uses `event_ledger` as sole write path — no longer writes to `payments` table. Sellers without CA config get skipped (no events recorded). |
| `event_ledger.py` | **Event Ledger.** Append-only financial event log. Each payment lifecycle event is an immutable record with signed_amount. **Source of truth** for all payment state. Public API: `record_event()`, `record_cash_event()`, `get_events()`, `get_balance()`, `get_dre_summary()`, `get_cash_summary()`, `get_processed_payment_ids()`, `get_processed_payment_ids_in()`, `get_payment_fees_from_events()`, `get_payment_statuses()`, `derive_payment_status()`, `EventRecordError`. 22 event types: 16 payment + 6 cash_* (extrato reconciliation). |
| `ca_api.py` | HTTP client for Conta Azul API v2. Token cache + OAuth2 refresh with asyncio.Lock. All requests go through `rate_limiter`. |
| `ml_api.py` | HTTP client for ML/MP APIs. Per-seller token management with auto-refresh. Raises `MLAuthError` on revoked tokens. |
| `ca_queue.py` | Persistent job queue backed by Supabase `ca_jobs`. `CaWorker` polls and executes jobs with retry/backoff/dead-letter. |
| `rate_limiter.py` | Singleton `TokenBucket` (9 req/s, 540 req/min). Shared by `CaWorker` and `ca_api` reads. |
| `daily_sync.py` | Daily payment ingestion (00:01 BRT, D-1 to D-3). Orders -> `processor`, non-orders -> `expense_classifier`. |
| `expense_classifier.py` | Classifies non-order ML/MP payments into `mp_expenses` table. Rule-based with extensible `AUTO_RULES` list. |
| `faturamento_sync.py` | `FaturamentoSyncer` class: polls ML paid orders every N minutes, upserts daily totals to `faturamento` table. |
| `financial_closing.py` | Computes daily financial closing per seller (auto lane: payments/ca_jobs, manual lane: mp_expenses/batches). |
| `release_checker.py` | `ReleaseChecker` class: verifies `money_release_status` via Supabase cache + ML API before baixas. |
| `release_report_sync.py` | Parses ML release report CSV, inserts missing transactions (payouts, cashback, shipping) into `mp_expenses`. |
| `release_report_validator.py` | Compares processor fees vs release report fees. Creates CA adjustment jobs for discrepancies. |
| `extrato_ingester.py` | Ingests account_statement gap lines (DIFAL, faturas ML, dispute refunds, etc.) into `mp_expenses`. |
| `extrato_coverage_checker.py` | Verifies 100% of release report lines are covered by payments, mp_expenses, or legacy export. |
| `onboarding.py` | Seller lifecycle: `create_signup` -> `approve_seller` -> `activate_seller`. Creates revenue_lines and goals. |
| `onboarding_backfill.py` | Historical payment ingestion for new sellers (by `money_release_date`). Includes release report backfill (payouts, cashback, shipping credits). Resumable with progress tracking. |
| `gdrive_client.py` | Public helper for expenses backup ZIP upload to Google Drive (`ROOT/DESPESAS/{EMPRESA}/{YYYY-MM}`). Reuses internals from `legacy/daily_export.py`. |
| `ca_categories_sync.py` | Daily sync of CA income/expense categories to local `ca_categories.json` file for offline lookups. |

### legacy/ Subpackage

Legacy reconciliation logic ported from V1, organized as a Python subpackage.

| File | Responsibility |
|------|---------------|
| `legacy/__init__.py` | Re-exports public API for the subpackage |
| `legacy/daily_export.py` | Downloads ML account_statement, runs legacy reconciliation, produces ZIP, uploads to external endpoint + GDrive. |
| `legacy/bridge.py` | Bridge to reuse legacy CSV reconciliation logic. Wraps `engine.py` and produces XLSX ZIP output. |
| `legacy/engine.py` | ~1500-line legacy reconciliation engine ported from V1. Processes ML CSVs into XLSX for CA import. |

> **Note:** Root-level `legacy_daily_export.py`, `legacy_bridge.py`, and `legacy_engine.py` are thin wrappers / re-exports for backward compatibility. The canonical implementation lives in `legacy/`.

---

## Dependency Graph

```
                      processor.py
                     /      |      \
              ml_api.py  ca_queue.py  models/sellers.py
                  |         |
            event_ledger.py |    (event_ledger is sole write path for payment state)
                  ^         |
                  |    ca_api.py
                  |         |
                  |    rate_limiter.py
                  |
     (all consumers read from event_ledger)

daily_sync.py -----> processor.py + expense_classifier.py + event_ledger (status detection)
onboarding_backfill.py --> processor.py + expense_classifier.py + ml_api.py + event_ledger + release_report_sync.py

release_report_validator.py --> ca_queue.py + ml_api.py + event_ledger (fees) + processor._build_despesa_payload
release_report_sync.py ------> ml_api.py (report download) + event_ledger (already-done check)
extrato_ingester.py ---------> release_report_sync._get_or_create_report + event_ledger (payment/refund lookups)
extrato_coverage_checker.py -> release_report_validator._get_or_create_report + event_ledger (payment lookups)
release_checker.py ----------> ml_api.py + event_ledger (release status cache)
financial_closing.py --------> event_ledger (payment status derivation) + ca_jobs

legacy/daily_export.py --> legacy/bridge.py --> legacy/engine.py
legacy/daily_export.py --> ml_api.py (report download)
gdrive_client.py ------> legacy/daily_export.py (_build_gdrive_client, _gdrive_ensure_folder, _gdrive_upload_bytes)

faturamento_sync.py --> ml_api.py (fetch_paid_orders)
ca_categories_sync.py -> ca_api.py (listar_categorias)
```

---

## Background Tasks vs On-Demand

### Background tasks (started in `main.py` lifespan)
- **CaWorker** (`ca_queue.py`) -- always running, polls ca_jobs every ~1s
- **FaturamentoSyncer** (`faturamento_sync.py`) -- always running, every 1 min (configurable via `SYNC_INTERVAL_MINUTES`)
- **CA token refresh** (`ca_api._get_ca_token`) -- every 30 min
- **CA categories sync** (`ca_categories_sync.py`) -- daily at 02:00 BRT
- **Daily sync** (`daily_sync.py`) -- daily at 00:01 BRT (when nightly pipeline disabled)
- **Financial closing** (`financial_closing.py`) -- daily at 11:30 BRT (when nightly pipeline disabled)
- **Legacy daily export** (`legacy/daily_export.py`) -- daily, configurable hour (when enabled)
- **Nightly pipeline** (`main.py`) -- orchestrates sync -> release report -> fee validation -> extrato ingestion -> baixas -> legacy -> coverage -> CA categories -> closing sequentially

### On-demand (called by routers or other services)
- `processor.py` -- called by daily_sync, backfill router, onboarding_backfill
- `expense_classifier.py` -- called by daily_sync, onboarding_backfill
- `release_checker.py` -- called by baixas router
- `onboarding.py` -- called by admin router
- `onboarding_backfill.py` -- called by admin router (runs as background asyncio.Task)
- `release_report_sync.py` -- called by admin router
- `release_report_validator.py` -- called by admin router, nightly pipeline
- `extrato_ingester.py` -- called by admin router, nightly pipeline
- `extrato_coverage_checker.py` -- called by admin router, nightly pipeline
- `legacy/bridge.py` -- called by expenses router (legacy-export endpoint)
- `gdrive_client.py` -- called by expenses export router (`gdrive_backup=true`) via background task

---

## Key Patterns

1. **Async everywhere.** All I/O uses `httpx.AsyncClient`. Supabase SDK is sync but wrapped in async functions.

2. **Rate limiting.** All CA API calls must go through `rate_limiter.acquire()`. The `ca_api.py` module does this automatically. Never call CA endpoints directly bypassing `ca_api`.

3. **Job queue for CA writes.** Never POST to CA API directly. Always enqueue via `ca_queue.enqueue_*()`. The `CaWorker` handles retry, backoff (30s/120s/480s), and dead-letter.

4. **Event Ledger is source of truth.** The `payments` table is no longer written to. All payment state comes from `payment_events` via `event_ledger.py`. Status is derived via `derive_payment_status()` (centralized, never re-implement locally). Priority: `ca_sync_failed` → error, `refund_created`/`charged_back` → refunded, `ca_sync_completed` → synced, `sale_approved` → queued. DB write errors raise `EventRecordError`.

5. **Idempotency.** `ca_jobs.idempotency_key` pattern: `{seller}:{payment_id}:{tipo}`. Event ledger uses `{seller}:{payment_id}:{event_type}` with ON CONFLICT DO NOTHING.

6. **Token management.** CA tokens use asyncio.Lock to prevent concurrent refresh races. ML tokens are per-seller with auto-refresh.

7. **In-memory result caches.** Several services store `_last_*_result` dicts for status endpoints (financial_closing, release_report_validator, extrato_coverage_checker, extrato_ingester).

---

## Common Gotchas

- **CA API is async (protocol-based).** Responses return `{"protocolo": "..."}`, NOT `{"id": "..."}`. Do not expect an entity ID back.
- **Baixas cannot be created with future dates.** CA returns 400 if `data_pagamento > hoje`. That is why processor creates entries WITHOUT baixa, and the baixas scheduler runs separately.
- **`charges_details` is the source of truth for fees.** Never use `fee_details` (it is incomplete).
- **`financing_fee` is net-neutral.** It must be excluded from comissao calculations and never generates a CA expense.
- **ML dates are UTC-4, reports use BRT (UTC-3).** Always use `_to_brt_date(date_approved)` for competencia.
- **`legacy/engine.py` is a ported monolith (~1500 lines).** Avoid modifying it directly; use `legacy/bridge.py` as the interface.
- **Nightly pipeline replaces individual schedulers.** When `nightly_pipeline_enabled=true`, daily_sync/baixas/closing schedulers are NOT started.
- **`upload_expenses_zip()` is sync.** Routers async devem chamar via `asyncio.to_thread(...)` para nao bloquear event loop.
- **Contrato de status GDrive para despesas:** `queued`, `uploaded`, `failed`, `skipped_no_drive_root`.
- **Sem `LEGACY_DAILY_GOOGLE_DRIVE_ROOT_FOLDER_ID`,** `gdrive_client` deve retornar skip sem excecao.

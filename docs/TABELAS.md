> Extraido do CLAUDE.md principal. Ver CLAUDE.md para visao geral.

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
status (pending|queued|synced|refunded|skipped|skipped_non_sale|pending_ca),
raw_payment (jsonb), error, ca_evento_id,
processor_fee (numeric), processor_shipping (numeric), fee_adjusted (bool),
created_at, updated_at
```

### payment_events
Event ledger — append-only log de eventos financeiros por payment.
```
id (PK bigserial), seller_slug (FK sellers), ml_payment_id, ml_order_id,
event_type (sale_approved|fee_charged|shipping_charged|subsidy_credited|
  refund_created|refund_fee|refund_shipping|partial_refund|
  ca_sync_completed|ca_sync_failed|money_released|mediation_opened|
  charged_back|reimbursed),
signed_amount (numeric 12,2), competencia_date (date), event_date (date),
created_at (timestamptz), source, idempotency_key (unique), metadata (jsonb)

Indexes:
  idx_pe_seller_payment: (seller_slug, ml_payment_id)
  idx_pe_seller_comp: (seller_slug, competencia_date)
  idx_pe_seller_event: (seller_slug, event_date)
  idx_pe_type: (event_type)
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

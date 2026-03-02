> Extraido do CLAUDE.md principal. Ver CLAUDE.md para visao geral.

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
| DELETE | `/admin/sellers/{slug}` | Soft-delete: desativa, limpa tokens, status=suspended |
| POST | `/admin/sellers/{slug}/disconnect` | Limpa ML tokens (seller precisa re-autenticar) |
| GET | `/admin/sellers/{slug}/reconnect-link` | Link de reconexao OAuth ML para o seller |
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

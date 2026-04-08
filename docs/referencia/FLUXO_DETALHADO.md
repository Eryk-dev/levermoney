> Extraido do CLAUDE.md principal. Ver CLAUDE.md para visao geral.

## 7. Fluxo Detalhado: CaWorker, Baixas, Closing, Nightly Pipeline

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
    2. sync_release_report_all_sellers() → Sync release report (payouts, cashback, shipping credits)
    3. validate_release_fees_all_sellers() → Valida fees vs release report, cria ajustes CA
    4. ingest_extrato_all_sellers() → Ingesta lacunas do account_statement
    5. _run_baixas_all_sellers() → Baixas (roda imediatamente apos sync, NAO em scheduler separado)
    6. run_legacy_daily_for_all() → Legacy export (dias configurados)
    7. check_extrato_coverage_all_sellers() → Verifica 100% cobertura do extrato
    8. sync_ca_categories() → Sync categorias CA
    9. _run_financial_closing() → Fechamento financeiro (inclui coverage data)
```

### Onboarding Backfill (on-demand, via admin)
```
run_onboarding_backfill(seller_slug):
    1. Load seller config (ca_start_date, integration_mode)
    2. Mark ca_backfill_status = "running"
    3. Fetch ALL payments by money_release_date (ca_start_date → today+90d)
    4. Build already-done set (payments + mp_expenses) para resumability
    5. Process each payment:
       ├─ Com order_id → process_payment_webhook()
       └─ Sem order_id → classify_non_order_payment()
    6. Backfill release report (ca_start_date → yesterday)
       └─ Captura payouts, DARFs, cashback, shipping credits do CSV
    7. Trigger baixas para parcelas com vencimento <= hoje
    8. Mark ca_backfill_status = "completed"
```

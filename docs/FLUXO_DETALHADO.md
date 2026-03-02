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
    2. validate_release_fees_all_sellers() → Valida fees vs release report, cria ajustes CA
    3. ingest_extrato_all_sellers() → Ingesta lacunas do account_statement
    4. _run_baixas_all_sellers() → Baixas
    5. run_legacy_daily_for_all() → Legacy export (dias configurados)
    6. check_extrato_coverage_all_sellers() → Verifica 100% cobertura do extrato
    7. _run_financial_closing() → Fechamento financeiro (inclui coverage data)
```

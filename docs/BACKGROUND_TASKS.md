> Extraido do CLAUDE.md principal. Ver CLAUDE.md para visao geral.

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

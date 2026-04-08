> Extraido do CLAUDE.md principal. Ver CLAUDE.md para visao geral.

## 13. Idempotencia e Resiliencia

- **Payments**: upsert por `(seller_slug, ml_payment_id)`. Reprocessar e seguro.
- **ca_jobs**: unique constraint em `idempotency_key`. Key pattern: `{seller}:{payment_id}:{tipo}`.
- **Retry**: backoff exponencial (30s → 120s → 480s) com max 3 tentativas, depois dead letter.
- **Stuck jobs**: recover automatico no startup (processing > 5min → failed).
- **Concurrent refresh**: asyncio.Lock previne race condition no refresh do token CA.
- **Rate limit**: token bucket compartilhado entre CaWorker e ca_api reads.
- **Sync cursor**: daily sync persiste cursor em `sync_state` para evitar gaps entre execucoes.

# PRD: Baixa Imediata + Status pending_ca

**Data:** 2026-03-03
**Versao:** 1.0
**Status:** Draft

---

## 1. Introducao / Overview

Este PRD cobre duas mudancas pontuais no sistema de conciliacao LeverMoney:

1. **Baixa imediata**: Remover a separacao temporal das baixas. Hoje as baixas rodam em horario fixo (10h BRT) ou como step 5 do nightly pipeline. A mudanca faz as baixas rodarem **imediatamente apos o sync de payments**, tanto no nightly pipeline quanto no onboarding backfill.

2. **Status `pending_ca`**: Quando o nightly pipeline processa payments de sellers sem configuracao CA (conta bancaria, centro de custo), hoje salva como `status="skipped"`. Isso impede o reprocessamento futuro quando o seller for migrado para `dashboard_ca`. A mudanca introduz um novo status `pending_ca` que sinaliza "dados coletados, aguardando config CA".

### Contexto da Arquitetura

O sistema LeverMoney e um conciliador automatico entre **Mercado Livre/Mercado Pago** e **Conta Azul ERP**. Para cada venda no ML, cria no CA: receita (contas-a-receber), despesa de comissao (contas-a-pagar), despesa de frete (contas-a-pagar), e baixas quando o dinheiro e liberado.

**Stack:** FastAPI + Python 3.12, Supabase (PostgreSQL), httpx async, React 19 dashboard.

**Projeto Supabase:** `wrbrbhuhsaaupqsimkqz`

### Sellers Atuais

| Seller | integration_mode | CA Config |
|--------|-----------------|-----------|
| 141air | dashboard_ca | setado (conta + centro) |
| autofy, automy, bellatorsports, easysp, easy-utilidades, net-air, netparts-sp, presentes-criativos-uniquebox, presentesunique | dashboard_only | null |

Apenas 141air tem integracao CA completa. Os outros 9 sao `dashboard_only` — o nightly pipeline puxa payments e classifica mp_expenses, mas nao cria ca_jobs.

### Fluxo Atual: Nightly Pipeline (9 steps sequenciais, 00:01 BRT)

```
Step 1: sync_all_sellers()                    — Payments D-1..D-3 via ML API
Step 2: sync_release_report_all_sellers()     — Payouts, cashback, shipping do CSV (lookback 3 dias)
Step 3: validate_release_fees_all_sellers()   — Compara processor fees vs release report
Step 4: ingest_extrato_all_sellers()          — Lacunas do account_statement
Step 5: _run_baixas_all_sellers()             — Baixas de parcelas liberadas
Step 6: run_legacy_daily_for_all()            — Export legado ZIP (Seg+Qui)
Step 7: check_extrato_coverage_all_sellers()  — Verifica 100% cobertura extrato
Step 8: sync_ca_categories()                  — Categorias CA
Step 9: _run_financial_closing()              — Fechamento financeiro
```

### Fluxo Atual: Onboarding Backfill

Triggered via `POST /admin/sellers/{slug}/activate` quando `integration_mode=dashboard_ca`:

```
1. Load seller config (ca_start_date)
2. Mark ca_backfill_status = "running"
3. Fetch ALL payments por money_release_date (ca_start_date → hoje+90d)
4. Build already_done set (payments + mp_expenses)
5. Process: order → process_payment_webhook(), non-order → classify_non_order_payment()
6. Backfill release report (ca_start_date → ontem)
7. Trigger baixas para parcelas com vencimento <= hoje
8. Mark ca_backfill_status = "completed"
```

### Problema 1: Baixa separada temporalmente

O nightly pipeline roda baixas no step 5, que e sequencial apos steps 1-4. Porem, na versao antiga (sem nightly pipeline), as baixas tinham scheduler proprio as 10h BRT — separado do sync das 00:01. Essa separacao temporal nao faz mais sentido: os payments ja foram puxados, os `money_release_date` ja sao conhecidos, e a unica restricao e que o CA rejeita `data_pagamento > hoje` (que ja e filtrada).

### Problema 2: Payments salvos como "skipped" bloqueiam backfill futuro

Quando o nightly pipeline processa um seller `dashboard_only`, o `processor.py` detecta campos CA ausentes e salva o payment com `status="skipped", error="missing_ca_config:ca_conta_bancaria,ca_centro_custo_variavel"`.

O `onboarding_backfill.py` no `_load_already_done()` inclui `status="skipped"` no set de IDs ja processados. Quando o seller for migrado para `dashboard_ca`, o backfill vai **pular esses payments** achando que ja foram processados — mas nunca tiveram ca_jobs criados.

**Codigo relevante — processor.py linhas 224-236:**
```python
# Only sellers with full CA launch config can create CA entries/jobs.
if status in ("approved", "in_mediation", "refunded", "charged_back"):
    missing_ca_fields = get_missing_ca_launch_fields(seller)
    if missing_ca_fields:
        reason = f"missing_ca_config:{','.join(missing_ca_fields)}"
        _upsert_payment(db, seller_slug, payment, "skipped", error=reason)
        return
```

**Codigo relevante — onboarding_backfill.py linhas 556-563:**
```python
rows = (
    db.table("payments")
    .select("ml_payment_id, status")
    .eq("seller_slug", seller_slug)
    .in_("status", ["synced", "queued", "refunded", "skipped", "skipped_non_sale"])
    .range(page_start, page_start + page_limit - 1)
    .execute()
)
```

---

## 2. Goals

- Eliminar a separacao temporal desnecessaria das baixas, executando-as logo apos o sync de payments
- Garantir que payments coletados pelo nightly para sellers sem CA config sejam reprocessaveis quando o seller migrar para `dashboard_ca`
- Nao quebrar nenhum fluxo existente (nightly pipeline, onboarding backfill, daily sync, backfill manual)

---

## 3. User Stories

### US-001: Baixa imediata no nightly pipeline

**Descricao:** Como operador, quero que as baixas no nightly pipeline rodem imediatamente apos o sync de payments, sem esperar um horario fixo separado.

**Acceptance Criteria:**
- [ ] No nightly pipeline, step 5 (baixas) continua rodando na mesma posicao sequencial (apos steps 1-4) — a mudanca e conceitual: nao existe mais scheduler separado de 10h BRT
- [ ] Quando `nightly_pipeline_enabled=true`, o scheduler standalone de baixas (`_daily_baixa_scheduler`) NAO e iniciado (comportamento atual, verificar que continua assim)
- [ ] Baixas filtram `vencimento <= hoje` (restricao CA mantida)
- [ ] Logs confirmam execucao das baixas no pipeline sem erro
- [ ] Typecheck/lint passam

### US-002: Baixa imediata no onboarding backfill

**Descricao:** Como operador, quero que o onboarding backfill continue executando baixas imediatamente apos processar todos os payments (comportamento atual — confirmar que esta correto).

**Acceptance Criteria:**
- [ ] O onboarding backfill continua chamando `_trigger_baixas()` apos processar payments e release report (step 7 do fluxo)
- [ ] Confirmar no codigo que nao ha dependencia de horario fixo
- [ ] Typecheck/lint passam

### US-003: Novo status `pending_ca` no processor

**Descricao:** Como sistema, quando o nightly pipeline processa payments de sellers sem configuracao CA, quero salvar com status `pending_ca` em vez de `skipped`, para que o onboarding backfill saiba que precisa reprocessar.

**Acceptance Criteria:**
- [ ] Em `processor.py`, quando `get_missing_ca_launch_fields()` retorna campos faltantes, o payment e salvo com `status="pending_ca"` (em vez de `"skipped"`)
- [ ] O campo `error` continua preenchido com `"missing_ca_config:..."` para rastreabilidade
- [ ] O `raw_payment` (jsonb) continua sendo salvo no upsert (dados do ML preservados)
- [ ] `processor_fee` e `processor_shipping` sao calculados e salvos mesmo para `pending_ca` (para evitar re-fetch futuro)
- [ ] Typecheck/lint passam

### US-004: Onboarding backfill reprocessa `pending_ca`

**Descricao:** Como sistema, quando o onboarding backfill roda para um seller recem-migrado para `dashboard_ca`, quero que payments com `status="pending_ca"` sejam reprocessados (ca_jobs criados).

**Acceptance Criteria:**
- [ ] Em `_load_already_done()` do `onboarding_backfill.py`, o status `"pending_ca"` NAO esta na lista de statuses considerados "done" (lista atual: `synced, queued, refunded, skipped, skipped_non_sale`)
- [ ] Na pratica: `"skipped"` permanece na lista (para nao reprocessar payments que foram legitimamente skippados por outros motivos, ex: cancelled/rejected via daily_sync)
- [ ] Payments `pending_ca` sao reprocessados pelo backfill — agora com seller tendo CA config, o processor cria ca_jobs normalmente
- [ ] O reprocessamento usa o `raw_payment` ja salvo? NAO — o backfill atual re-fetcha do ML API (via `_fetch_all_payments`). Isso e ok porque o backfill busca por `money_release_date` e pode encontrar payments que o nightly nao pegou (range diferente)
- [ ] Apos reprocessamento, o status do payment muda de `pending_ca` para `queued` (e depois `synced` quando CaWorker completa)
- [ ] Typecheck/lint passam

### US-005: Daily sync respeita `pending_ca`

**Descricao:** Como sistema, o daily sync (que roda no nightly step 1) precisa lidar corretamente com payments que ja existem como `pending_ca` no Supabase.

**Acceptance Criteria:**
- [ ] Em `sync_seller_payments()` do `daily_sync.py`, payments com `status="pending_ca"` no Supabase devem ser tratados como "nao processados" (permitir reprocessamento se o seller agora tem CA config)
- [ ] Se o seller continua sem CA config, o processor vai salvar como `pending_ca` novamente (idempotente)
- [ ] Se o seller agora tem CA config (migrou para dashboard_ca), o processor cria ca_jobs e muda para `queued`
- [ ] A deteccao de status change no daily_sync (`should_reprocess`) deve considerar `pending_ca` como reprocessavel
- [ ] Typecheck/lint passam

---

## 4. Functional Requirements

**FR-1:** O sistema deve salvar payments com status `pending_ca` quando o seller nao tem configuracao CA completa (campos `ca_conta_bancaria` e/ou `ca_centro_custo_variavel` ausentes).

**FR-2:** O sistema deve salvar `processor_fee` e `processor_shipping` calculados mesmo para payments `pending_ca`, evitando necessidade de recalculo futuro (embora o backfill re-fetche do ML API de qualquer forma).

**FR-3:** O sistema deve preservar `raw_payment` (jsonb) para payments `pending_ca`, mantendo os dados originais do ML disponiveis.

**FR-4:** O `_load_already_done()` do onboarding backfill NAO deve incluir `pending_ca` no set de payments ja processados.

**FR-5:** O daily sync deve tratar `pending_ca` como reprocessavel (equivalente a `pending` ou `queued` para fins de `should_reprocess`).

**FR-6:** O nightly pipeline deve continuar executando baixas no step 5, sequencialmente apos steps 1-4. Nao deve existir scheduler standalone de baixas quando `nightly_pipeline_enabled=true`.

**FR-7:** O status `pending_ca` deve ser adicionado ao enum de statuses aceitos na tabela `payments` (campo `status`): `pending|queued|synced|refunded|skipped|skipped_non_sale|pending_ca`.

---

## 5. Non-Goals (Out of Scope)

- **NAO** mudar o release report sync (manter lookback 3 dias no nightly, cobertura greedy no onboarding)
- **NAO** mudar a janela de sync de payments (manter D-1..D-3)
- **NAO** otimizar o onboarding backfill para usar `raw_payment` local em vez de re-fetchar do ML API (melhoria futura, fora deste escopo)
- **NAO** migrar sellers de `dashboard_only` para `dashboard_ca` (isso e operacao manual do admin)
- **NAO** alterar o fluxo de mp_expenses (non-order payments) — esses ja funcionam corretamente para ambos os modos
- **NAO** alterar o dashboard React
- **NAO** alterar a logica de fee validation, extrato ingestion, coverage check ou financial closing

---

## 6. Technical Considerations

### Arquivos a modificar

| Arquivo | Mudanca |
|---------|---------|
| `app/services/processor.py` | Linhas 224-236: mudar `"skipped"` para `"pending_ca"`. Adicionar calculo de `processor_fee`/`processor_shipping` antes do early return |
| `app/services/onboarding_backfill.py` | `_load_already_done()`: remover ou nao incluir `"pending_ca"` na lista de statuses |
| `app/services/daily_sync.py` | `sync_seller_payments()`: tratar `pending_ca` como reprocessavel no `should_reprocess` |
| `app/main.py` | Verificar que o scheduler standalone de baixas (10h) nao e iniciado quando `nightly_pipeline_enabled=true` (ja deve ser assim) |

### Arquivos de documentacao a atualizar

| Doc | O que atualizar |
|-----|-----------------|
| `CLAUDE.md` | Adicionar `pending_ca` na descricao do campo `status` da tabela `payments` |
| `docs/TABELAS.md` | Adicionar `pending_ca` ao enum de status |
| `docs/REGRAS_NEGOCIO.md` | Documentar comportamento de `pending_ca` |
| `docs/FLUXO_DETALHADO.md` | Atualizar nota sobre baixas |
| `app/services/CLAUDE.md` | Mencionar `pending_ca` no processor |

### Dependencias entre services

```
processor.py (US-003)
    ↓ usado por
daily_sync.py (US-005) — chama process_payment_webhook()
onboarding_backfill.py (US-004) — chama process_payment_webhook()

Nenhuma dependencia circular. Mudancas sao independentes entre si.
```

### Tabela payments — campo status (novo enum)

```
status: pending | queued | synced | refunded | skipped | skipped_non_sale | pending_ca
```

O campo `status` e TEXT no Supabase (sem CHECK constraint), entao nao precisa de migration SQL.

### Risao de regressao

- Payments que foram legitimamente skippados por outros motivos (cancelled/rejected via daily_sync) continuam como `"skipped"` — nao sao afetados
- Payments `pending_ca` existentes no banco: NAO existem hoje (o status e novo). Porem, payments que ja foram salvos como `"skipped"` com `error="missing_ca_config:..."` **nao serao retroativamente corrigidos**. Se necessario, um script de migracao pode fazer `UPDATE payments SET status='pending_ca' WHERE status='skipped' AND error LIKE 'missing_ca_config%'`

---

## 7. Success Metrics

- Apos deploy, o nightly pipeline continua rodando sem erros
- Payments de sellers `dashboard_only` aparecem com `status="pending_ca"` em vez de `"skipped"`
- Quando um seller e migrado para `dashboard_ca`, o onboarding backfill reprocessa payments `pending_ca` e cria ca_jobs
- Baixas rodam corretamente no nightly sem scheduler standalone

---

## 8. Open Questions

1. **Migracao retroativa:** Devemos rodar um UPDATE para corrigir payments ja salvos como `"skipped"` com `error="missing_ca_config%"` para `"pending_ca"`? Isso afetaria os 9 sellers `dashboard_only` que ja tem historico acumulado.

2. **Backfill manual (`/backfill/{seller}`):** Deve tambem respeitar `pending_ca`? Hoje o backfill manual usa o `daily_sync.sync_seller_payments()`, que sera atualizado (US-005). Verificar se o comportamento e consistente.

3. **Scheduler standalone de baixas (10h BRT):** Confirmar que quando `nightly_pipeline_enabled=false` (modo legado), o scheduler standalone continua funcionando normalmente. A mudanca so afeta o modo nightly pipeline.

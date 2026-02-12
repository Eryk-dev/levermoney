# Fluxo Detalhado - Lever Money Platform

**Versão:** 3.0 | **Data:** 2026-02-12

---

## Visão Geral do Sistema

Plataforma unificada que integra:
- **Sync Conciliador**: webhooks/backfill ML → lançamentos no Conta Azul (receitas, despesas, baixas)
- **Sync Faturamento**: polling ML orders → tabela `faturamento` (totais diários por seller)
- **Dashboard**: React SPA com metas, projeções, entrada manual e admin panel
- **Onboarding**: cadastro de sellers, aprovação admin, ativação via OAuth ML

```
┌─────────────────────┐     ┌─────────────────────────────────┐     ┌─────────────────────┐
│   MERCADO LIVRE /   │     │   LEVER MONEY API (FastAPI)     │     │    CONTA AZUL       │
│   MERCADO PAGO      │────▶│                                 │     │    (ERP)            │
│                     │     │  Routers:                       │     │                     │
│  Webhooks           │     │    /webhooks, /backfill, /baixas│     │  API v2 (Cognito)   │
│  Orders API         │     │    /admin, /dashboard           │     │                     │
│  Payments API       │     │    /connect, /queue, /health    │     │                     │
│  OAuth2             │     │                                 │     │                     │
└─────────────────────┘     │  Services:                      │     └─────────────────────┘
                            │    processor.py (CA sync)       │               ▲
                            │    faturamento_sync.py (polling)│               │
                            │    onboarding.py (sellers)      │               │
                            │    ca_queue.py (CaWorker)       │               │
                            │                                 │               │
                            │  Supabase (wrbrbhuh...):        │               │
                            │    payments, ca_jobs, sellers   │               │
                            │    faturamento, revenue_lines   │               │
                            │    goals, meli_tokens           │               │
                            └────────────────┬────────────────┘               │
                                             │                                │
                                    enqueue()│        CaWorker (9 req/s)     │
                                             └────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│   DASHBOARD (React 19 + TS)                                                │
│                                                                            │
│   Views: Geral | Metas | Entrada | Linhas | Admin                         │
│   Dados: Supabase realtime (faturamento, revenue_lines, goals)            │
│   Admin: seller management, sync control, goals/lines CRUD                │
│   PWA: offline read-only, installável                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Fluxo Completo: Venda Aprovada (EVENTO 1)

### Etapa 1 - Entrada de Dados (ML/MP)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     MERCADO LIVRE / MERCADO PAGO                        │
│                                                                         │
│  Webhook "payment" (status=approved/in_mediation/refunded/cancelled)    │
│  ou Backfill (GET /v1/payments/search?begin_date=...&end_date=...)     │
│                                                                         │
│         │                                                               │
│         ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  GET /v1/payments/{payment_id}                                  │    │
│  │                                                                  │    │
│  │  Retorna:                                                        │    │
│  │    transaction_amount     = R$ 284,74  (bruto)                  │    │
│  │    date_approved          = 2026-02-01 (data da venda)          │    │
│  │    money_release_date     = 2026-02-15 (data da liberação)      │    │
│  │    net_received_amount    = R$ 235,85  (líquido)                │    │
│  │    charges_details (SOURCE OF TRUTH):                            │    │
│  │      type=shipping, from=collector  → R$ 23,45 (frete seller)  │    │
│  │      type=ml_sale_fee, from=collector → (comissão ML)           │    │
│  │      type=mp_processing_fee → (taxa processamento)              │    │
│  │    fee_details: ⚠ UNRELIÁVEL (vazio em 86% dos payments!)      │    │
│  │    order.id               = 46410008520                          │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│         │                                                               │
│         ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  GET /orders/{order_id}                                         │    │
│  │                                                                  │    │
│  │  Retorna:                                                        │    │
│  │    order_items[0].item.title = "Filtro de Ar XPTO"              │    │
│  │    shipping.id               = 44789012345                       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│         │                                                               │
│         ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  GET /shipments/{shipping_id}/costs  (FALLBACK se charges vazio)│    │
│  │                                                                  │    │
│  │  Retorna:                                                        │    │
│  │    senders[0].cost = R$ 23,45  (frete pago pelo vendedor)       │    │
│  │                                                                  │    │
│  │  NOTA: Só chamado se charges_details não tem type=shipping.     │    │
│  │  charges_details é a fonte primária para shipping.               │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Etapa 2 - Cálculo dos Valores

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        PROCESSAMENTO (processor.py)                     │
│                                                                         │
│  Dados extraídos:                                                       │
│    amount             = R$ 284,74   (transaction_amount)                │
│    date_approved      = 2026-02-01  (competência de TODOS os lanç.)    │
│    money_release_date = 2026-02-15  (vencimento + baixa das despesas)  │
│    net                = R$ 235,85   (net_received_amount)              │
│                                                                         │
│  Shipping (de charges_details, type=shipping, from=collector):          │
│    shipping_cost      = R$ 23,45    (frete pago pelo vendedor)         │
│    Tipos: shp_cross_docking, shp_fulfillment, shp_adjustment           │
│    Fallback: API /shipments/{id}/costs se charges não tem dados        │
│                                                                         │
│  Comissão (FÓRMULA INFALÍVEL):                                         │
│    mp_fee = amount - net - shipping                                     │
│    mp_fee = 284,74 - 235,85 - 23,45 = R$ 25,44                        │
│    Captura automaticamente: ml_sale_fee + mp_processing_fee +          │
│    mp_financing_1x_fee + cashback-crypto + coupon_code + iof_fee       │
│                                                                         │
│  financing_fee: NÃO GERA DESPESA (net-neutral)                         │
│    financing_fee (collector→mp) = financing_transfer (payer→collector) │
│    Comprador paga o parcelamento como pass-through.                    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Etapa 3 - Enfileiramento para o Conta Azul (ca_queue)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    ENQUEUE → ca_jobs (Supabase)                         │
│                                                                         │
│  processor.py NÃO chama a API CA diretamente.                          │
│  Cada lançamento é inserido na tabela ca_jobs com:                     │
│    - idempotency_key UNIQUE (previne duplicatas)                       │
│    - group_id = "{seller}:{payment_id}" (agrupa jobs do payment)      │
│    - priority (receita=10, despesa=20, baixa=30)                       │
│  CaWorker (background) processa a fila respeitando rate limit global.  │
│                                                                         │
│  ═══════════════════════════════════════════════════════════════════     │
│  ║ A) RECEITA - enqueue_receita() (priority=10)                   ║     │
│  ═══════════════════════════════════════════════════════════════════     │
│                                                                         │
│  idempotency_key: "{seller}:{payment_id}:receita"                      │
│  endpoint: POST /v1/financeiro/eventos-financeiros/contas-a-receber    │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────┐      │
│  │  data_competencia: "2026-02-01"  ← date_approved              │      │
│  │  valor: 284.74                    ← transaction_amount        │      │
│  │  descricao: "Venda ML #46410008520 - Filtro de Ar XPTO"      │      │
│  │  contato: UUID MERCADO LIVRE                                   │      │
│  │  conta_financeira: UUID MP Retido - 141AIR                    │      │
│  │  categoria: 1.1.1 MercadoLibre                                │      │
│  │  centro_custo: 141AIR - VARIÁVEL                              │      │
│  │  parcela:                                                      │      │
│  │    data_vencimento: "2026-02-15"  ← money_release_date        │      │
│  │    detalhe_valor:                                              │      │
│  │      valor_bruto: 284.74                                       │      │
│  │      valor_liquido: 284.74                                     │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                                                                         │
│  ═══════════════════════════════════════════════════════════════════     │
│  ║ B) DESPESA - enqueue_comissao() (priority=20)                  ║     │
│  ═══════════════════════════════════════════════════════════════════     │
│                                                                         │
│  idempotency_key: "{seller}:{payment_id}:comissao"                     │
│  endpoint: POST /v1/financeiro/eventos-financeiros/contas-a-pagar      │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────┐      │
│  │  data_competencia: "2026-02-01"  ← date_approved              │      │
│  │  valor: 25.44                     ← amount - net - shipping   │      │
│  │  descricao: "Comissão ML - Payment 144370799868"              │      │
│  │  conta_financeira: UUID MP Retido - 141AIR                    │      │
│  │  categoria: 2.8.2 Comissões de Marketplace                   │      │
│  │  parcela:                                                      │      │
│  │    data_vencimento: "2026-02-15"  ← money_release_date        │      │
│  │    detalhe_valor:                                              │      │
│  │      valor_bruto: 25.44                                        │      │
│  │      valor_liquido: 25.44                                      │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                                                                         │
│  NOTA: Baixas são enfileiradas separadamente pelo scheduler diário.    │
│                                                                         │
│  ═══════════════════════════════════════════════════════════════════     │
│  ║ C) DESPESA - enqueue_frete() (priority=20)                     ║     │
│  ═══════════════════════════════════════════════════════════════════     │
│                                                                         │
│  idempotency_key: "{seller}:{payment_id}:frete"                        │
│  Mesmo payload, com:                                                    │
│    valor: 23.45 (shipping_cost_seller)                                  │
│    descricao: "Frete MercadoEnvios - Payment 144370799868"             │
│    categoria: 2.9.4 MercadoEnvios                                      │
│                                                                         │
│  ═══════════════════════════════════════════════════════════════════     │
│  ║ D) financing_fee: NÃO GERA DESPESA (net-neutral)               ║     │
│  ═══════════════════════════════════════════════════════════════════     │
│                                                                         │
│  financing_fee (collector→mp) é SEMPRE cancelado por                   │
│  financing_transfer (payer→collector) de mesmo valor.                  │
│  net_received_amount já exclui isso do cálculo.                        │
│                                                                         │
│  ═══════════════════════════════════════════════════════════════════     │
│  ║ E) CaWorker - Execução dos Jobs (background)                  ║     │
│  ═══════════════════════════════════════════════════════════════════     │
│                                                                         │
│  Loop contínuo (asyncio.Task):                                          │
│  1. Poll: SELECT ca_jobs WHERE status IN (pending, failed)             │
│           AND scheduled_for <= now() ORDER BY priority, created_at     │
│  2. Claim: UPDATE status=processing (atômico, impede duplicata)        │
│  3. Rate limit: await rate_limiter.acquire() (9 req/s global)         │
│  4. Execute: POST no CA API com ca_payload                             │
│  5. Resultado:                                                          │
│     - 2xx → completed, salva protocolo + response                      │
│     - 401 → invalida token cache, retry                                │
│     - 429/5xx → failed, backoff exponencial (30s, 120s, 480s)         │
│     - 4xx permanente → dead (dead letter, investigar via /queue/dead) │
│  6. Quando TODOS jobs do group_id completam → payments.status=synced  │
│                                                                         │
│  Crash recovery: jobs stuck em "processing" > 5min → reset "failed"   │
│                                                                         │
└──────────────────────────────────────────────────────────────────────────┘
```

### Etapa 4 - Persistência (Supabase)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          SUPABASE                                       │
│                                                                         │
│  Tabela: payments                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  ml_payment_id:    144370799868                                  │    │
│  │  seller_slug:      "141air"                                      │    │
│  │  ml_status:        "approved"                                    │    │
│  │  amount:           284.74                                        │    │
│  │  net_amount:       235.85                                        │    │
│  │  money_release_date: "2026-02-15"                                │    │
│  │  status:           "queued" → "synced" (quando worker completa) │    │
│  │  raw_payment:      { ...json completo do ML... }                │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  Idempotência: se ml_payment_id + seller_slug já existe com             │
│  status="synced" ou "queued", o payment é ignorado no reprocessamento. │
│  Se status="refunded", o estorno é ignorado no reprocessamento.        │
│                                                                         │
│  Statuses possíveis:                                                    │
│    "queued"           → jobs enfileirados, aguardando worker           │
│    "synced"           → todos jobs do group completaram no CA          │
│    "refunded"         → estornos criados                                │
│    "skipped"          → cancelled/rejected (sem lançamento CA)          │
│    "skipped_non_sale" → bill payment/money_transfer/ad credit           │
│    "pending"          → status ML desconhecido, salvo para análise     │
│                                                                         │
│  Tabela: ca_jobs (NOVA - fila persistente)                             │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  id:               UUID                                          │    │
│  │  idempotency_key:  "141air:144370799868:receita" (UNIQUE)       │    │
│  │  seller_slug:      "141air"                                      │    │
│  │  job_type:         "receita" | "comissao" | "frete" | "baixa"   │    │
│  │  ca_endpoint:      ".../contas-a-receber"                        │    │
│  │  ca_payload:       { ...payload completo... }                    │    │
│  │  group_id:         "141air:144370799868"                         │    │
│  │  priority:         10 (receita) | 20 (despesa) | 30 (baixa)    │    │
│  │  status:           pending → processing → completed | dead      │    │
│  │  attempts:         0-3 (max_attempts=3)                          │    │
│  │  ca_protocolo:     "abc123" (salvo após 2xx)                    │    │
│  │  scheduled_for:    now() | money_release_date (para baixas)     │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  Indexes:                                                               │
│    idx_ca_jobs_queue: (status, scheduled_for, priority, created_at)    │
│    idx_ca_jobs_group: (group_id, status)                               │
│    idx_ca_jobs_seller: (seller_slug, created_at DESC)                  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Roteamento por Status ML (processor.py)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    CLASSIFICAÇÃO DO PAYMENT                             │
│                                                                         │
│  Pré-filtros:                                                          │
│  1. Sem order_id? → "skipped_non_sale" (bill payment, transfer, ad)   │
│  2. description="marketplace_shipment"? → "skipped_non_sale"          │
│     (frete avulso pago pelo comprador, não é venda de produto)        │
│                                                                         │
│  Status ML:                                                             │
│  ┌─────────────────────┬────────────────────────────────────────┐      │
│  │ approved            │ → _process_approved() → receita+desp. │      │
│  │ in_mediation        │ → _process_approved() → receita+desp. │      │
│  │ refunded            │ → _process_refunded() → estornos      │      │
│  │ charged_back        │ → _process_refunded() → receita+est.  │      │
│  │ cancelled/rejected  │ → skipped (salva, sem lançamento CA)  │      │
│  │ outros              │ → pending (salva para análise)        │      │
│  └─────────────────────┴────────────────────────────────────────┘      │
│                                                                         │
│  in_mediation = venda aprovada sob disputa do comprador.               │
│  O dinheiro já foi creditado ou será. Lança como receita normal.       │
│  Se mediação resolver como refund, webhook atualiza para refunded.     │
│  date_approved existe e é a data de competência (quando a venda        │
│  realmente entrou para o seller).                                       │
│                                                                         │
│  cancelled/rejected = sem movimentação financeira.                     │
│  Salvo no Supabase para rastreamento, mas sem entrada no CA.           │
│  NÃO entra no faturamento bruto do CA (diferente do dashboard ML).    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Linha do Tempo: Datas no Conta Azul

```
  01/02 (date_approved)                     15/02 (money_release_date)
    │                                           │
    ▼                                           ▼
────●───────────────────────────────────────────●──────────────────▶ tempo
    │                                           │
    │  PROCESSOR → enqueue (imediato):           │  SCHEDULER 10:00 BRT → enqueue_baixa:
    │  Enfileira lançamentos SEM baixa           │  CaWorker processa baixas
    │                                           │
    │  ✅ Receita    R$ 284,74  (competência)   │  ✅ Receita    (baixa pelo job)
    │  ✅ Comissão   R$  25,44  (competência)   │  ✅ Comissão   (baixa pelo job)
    │  ✅ Frete      R$  23,45  (competência)   │  ✅ Frete      (baixa pelo job)
    │  ❌ Financing: NÃO GERA DESPESA           │  (net-neutral, pass-through)
    │                                           │
    │  Venda aconteceu aqui.                    │  ML libera o dinheiro aqui.
    │  Obrigações nasceram aqui.                │  Scheduler enfileira baixas.
    │  Parcelas ficam EM_ABERTO.                │  CaWorker executa via rate limit.
    │                                           │  Saldo MP Retido nesta data:
    │                                           │  +284,74 - 25,44 - 23,45 = R$ 235,85
    │                                           │  (= net_received_amount ✓)
```

---

## Fluxo: Devolução/Cancelamento (EVENTO 4)

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Webhook "payment" com status = "refunded"                              │
│                                                                         │
│  GET /v1/payments/{id}                                                  │
│    → refunds[]: lista de estornos com amounts e datas                  │
│    → transaction_amount_refunded: valor total devolvido                 │
│                                                                         │
│  Lançamentos no CA:                                                     │
│                                                                         │
│  PASSO 1: Se receita nunca foi criada (backfill direto como refunded): │
│  ┌───────────────────────────────────────────────────────┐             │
│  │  RECEITA ORIGINAL (contas-a-receber) ← _process_approved()         │
│  │  Valor: R$ 284,74 (transaction_amount)                 │             │
│  │  + DESPESA Comissão + DESPESA Frete                    │             │
│  │  (idêntico ao fluxo approved, para que 1.1.1 bata)    │             │
│  └───────────────────────────────────────────────────────┘             │
│                                                                         │
│  PASSO 2: Estornos                                                     │
│  ┌───────────────────────────────────────────────────────┐             │
│  │  ESTORNO RECEITA (contas-a-pagar)                      │             │
│  │  Valor: min(total_refunded, transaction_amount)        │             │
│  │  ⚠ refund.amount pode incluir frete devolvido ao       │             │
│  │    comprador, que EXCEDE o transaction_amount.          │             │
│  │    O estorno da receita nunca pode ser > receita.       │             │
│  │  Categoria: 1.2.1 Devoluções e Cancelamentos          │             │
│  │  Data: data do refund                                  │             │
│  │  Conta: MP Retido                                      │             │
│  └───────────────────────────────────────────────────────┘             │
│                                                                         │
│  ┌───────────────────────────────────────────────────────┐             │
│  │  ESTORNO TAXAS (contas-a-receber)                      │             │
│  │  Valor: R$ 48,89 (amount - net = total taxas)          │             │
│  │  Categoria: 1.3.4 Estornos de Taxas                   │             │
│  │  (só se estorno_receita >= transaction_amount)         │             │
│  │  Inclui comissão + frete em lançamento único           │             │
│  └───────────────────────────────────────────────────────┘             │
│                                                                         │
│  REFUND PARCIAL (status_detail = "partially_refunded"):                │
│  ┌───────────────────────────────────────────────────────┐             │
│  │  Para cada refund em payment.refunds[]:                │             │
│  │  ESTORNO PROPORCIONAL (contas-a-pagar)                │             │
│  │  Valor: refund.amount                                  │             │
│  │  Categoria: 1.2.1 Devoluções e Cancelamentos          │             │
│  │  Data: refund.date_created                             │             │
│  │  (status permanece "approved", não muda para refunded) │             │
│  └───────────────────────────────────────────────────────┘             │
│                                                                         │
│  Status no Supabase: "refunded" (total) ou "synced" (parcial)         │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Fluxo: Mediação (in_mediation)

```
┌──────────────────────────────────────────────────────────────────────────┐
│  MEDIAÇÃO: Comprador abre reclamação sobre venda já aprovada            │
│                                                                         │
│  Estado ML: approved → in_mediation → resolução                        │
│                                                                         │
│  Resoluções possíveis:                                                  │
│  ┌────────────────────────────────────────────────────────────────┐     │
│  │  1. Seller vence    → status volta a "approved"                │     │
│  │     Nenhuma ação necessária (receita já existe no CA)         │     │
│  │                                                                │     │
│  │  2. Buyer vence     → status muda para "refunded"              │     │
│  │     Webhook dispara _process_refunded() → estornos no CA      │     │
│  │                                                                │     │
│  │  3. Acordo parcial  → status "approved" + status_detail        │     │
│  │     "partially_refunded" → _process_partial_refund()          │     │
│  └────────────────────────────────────────────────────────────────┘     │
│                                                                         │
│  PROCESSAMENTO:                                                         │
│  in_mediation é tratado como approved no processor.py:                 │
│    → Cria receita + despesas normalmente usando date_approved          │
│    → date_approved existe (venda foi aprovada antes da mediação)       │
│    → Se mediação resolver como refund, o webhook de "refunded"         │
│      disparará os estornos automaticamente                              │
│                                                                         │
│  POR QUE LANÇAR COMO RECEITA?                                          │
│  O dinheiro da venda já foi creditado (ou será na money_release_date). │
│  A mediação é uma disputa POSTERIOR. Até que resolva, a receita        │
│  existe. Se resolvida como refund, os estornos compensam.              │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Diagrama de Contas no Conta Azul

```
┌────────────────────────────────────────────────────────────────────────┐
│                         CONTA AZUL - 141AIR                            │
│                                                                        │
│  ┌──────────────────────┐                                             │
│  │   MP RETIDO - 141AIR │   Conta virtual (tipo OUTROS)               │
│  │                      │   Representa dinheiro retido pelo ML        │
│  │   Saldo = quanto     │                                             │
│  │   falta liberar      │                                             │
│  └──────────┬───────────┘                                             │
│             │                                                          │
│   Venda:    │  +R$ 284,74  (receita, vencimento=money_release_date)   │
│   Comissão: │  -R$  25,44  (despesa com baixa em money_release_date)  │
│   Frete:    │  -R$  23,45  (despesa com baixa em money_release_date)  │
│             │  ─────────                                               │
│   Líquido:  │  =R$ 235,85  (= net_received_amount)                   │
│             │                                                          │
│             │  Liberação (EVENTO 2 - futuro)                          │
│             ▼                                                          │
│  ┌──────────────────────┐                                             │
│  │ 141AIR - MP          │   Conta real (CC Mercado Pago)              │
│  │ (MP Disponível)      │   Saldo disponível na conta MP              │
│  └──────────┬───────────┘                                             │
│             │                                                          │
│             │  Saque / PIX / TED (EVENTO 3 - futuro)                  │
│             ▼                                                          │
│  ┌──────────────────────┐                                             │
│  │ SICREDI - 141AIR     │   Conta bancária real                       │
│  │ (Banco)              │                                             │
│  └──────────────────────┘                                             │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Autenticação

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     FLUXO DE AUTENTICAÇÃO                               │
│                                                                         │
│  MERCADO LIVRE:                                                         │
│  ┌─────────────────┐                                                   │
│  │ OAuth2 padrão   │  access_token por seller (tabela Supabase)        │
│  │ Bearer Token    │  refresh automático quando expira                  │
│  └─────────────────┘                                                   │
│                                                                         │
│  CONTA AZUL:                                                            │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Supabase (ca_tokens)                                           │    │
│  │    access_token  ──▶  Bearer header para API v2                 │    │
│  │    refresh_token ──▶  Cognito REFRESH_TOKEN_AUTH                │    │
│  │    expires_at    ──▶  Controle de expiração (~1h)               │    │
│  │                                                                  │    │
│  │  Fluxo de refresh:                                               │    │
│  │    1. Token expirou? (expires_at < now + 60s)                   │    │
│  │    2. POST cognito-idp.sa-east-1.amazonaws.com                  │    │
│  │       X-Amz-Target: InitiateAuth                                │    │
│  │       AuthFlow: REFRESH_TOKEN_AUTH                               │    │
│  │       ClientId: 6ri07ptg5k2u7dubdlttg3a7t8                     │    │
│  │    3. Recebe novo access_token + ExpiresIn                      │    │
│  │    4. Atualiza Supabase + cache em memória                      │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  Cache em memória: evita query ao Supabase a cada request              │
│  Margem de segurança: 60s antes da expiração já faz refresh            │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Backfill (Reprocessamento em Lote)

```
GET /backfill/{seller_slug}?begin_date=2026-02-01&end_date=2026-02-11

┌──────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  1. GET /v1/payments/search?begin_date=...&end_date=...                │
│     (paginado, 50 por vez)                                             │
│                                                                         │
│  2. Filtra payments processáveis:                                       │
│     Status: approved, refunded, in_mediation, charged_back             │
│     Deve ter order_id (exclui bill payments, ad credits, transfers)   │
│     description != "marketplace_shipment" (frete avulso, não venda)   │
│     Não estar em status terminal no Supabase:                         │
│       synced, queued, refunded, skipped, skipped_non_sale → SKIP      │
│     Excluídos: cancelled, rejected (sem lançamento CA)                │
│                                                                         │
│  3. Para cada payment filtrado:                                         │
│     └── process_payment_webhook() (roteamento por status ML)           │
│                                                                         │
│  3. Batches de 10 concorrentes, 0.3s entre batches (rate limit ML)    │
│                                                                         │
│  4. Progresso logado a cada 20 payments                                │
│                                                                         │
│  Exemplo: 158 payments processados em ~600s (16/min)                   │
│           = ~3.8s por payment (APIs ML + CA, sem baixa inline)         │
│                                                                         │
│  Após backfill: jobs na fila ca_jobs, processados pelo CaWorker.       │
│  Baixas: scheduler diário 10:00 BRT ou manual /baixas/processar.      │
│  Monitorar: GET /queue/status                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Job de Baixas (Etapa 5 - Via Fila)

```
GET /baixas/processar/{seller_slug}?dry_run=true   (preview)
GET /baixas/processar/{seller_slug}?dry_run=false  (enfileira)

┌──────────────────────────────────────────────────────────────────────────┐
│                     JOB DE BAIXAS (via ca_jobs)                          │
│                                                                          │
│  POR QUE SEPARADO?                                                       │
│  A API CA retorna 400 se data_pagamento > hoje:                         │
│  "A data do pagamento deve ser igual ou anterior à data atual"          │
│  Quando money_release_date é futuro, a baixa NÃO pode ser feita        │
│  no momento da criação da despesa/receita. Precisa esperar.             │
│                                                                          │
│  FLUXO:                                                                  │
│                                                                          │
│  1. Buscar parcelas abertas na conta MP Retido do seller                │
│     GET /v1/financeiro/eventos-financeiros/contas-a-pagar/buscar        │
│     GET /v1/financeiro/eventos-financeiros/contas-a-receber/buscar      │
│     Params:                                                              │
│       ids_contas_financeiras=[UUID MP Retido]  (filtra só ML)           │
│       data_vencimento_de=hoje-90d                                        │
│       data_vencimento_ate=hoje                                           │
│       status=ATRASADO&status=EM_ABERTO                                  │
│       pagina=1, tamanho_pagina=50 (pagina automaticamente)              │
│                                                                          │
│  2. Para cada parcela encontrada → enqueue_baixa()                      │
│     idempotency_key: "{seller}:{parcela_id}:baixa"                     │
│     priority: 30 (processa após receitas e despesas)                    │
│     payload: {                                                           │
│       "data_pagamento": parcela.data_vencimento,                        │
│       "composicao_valor": { "valor_bruto": parcela.nao_pago },          │
│       "conta_financeira": "UUID MP Retido"                              │
│     }                                                                    │
│                                                                          │
│  3. CaWorker processa os jobs de baixa respeitando rate limit global.   │
│                                                                          │
│  RESULTADO (após worker executar):                                       │
│  ┌───────────────────────────────────────────────────────┐              │
│  │  Antes:  Despesa R$ 25,44  status=EM_ABERTO           │              │
│  │  Depois: Despesa R$ 25,44  status=RECEBIDO (baixada)  │              │
│  │          data_pagamento = 15/02 (money_release_date)   │              │
│  └───────────────────────────────────────────────────────┘              │
│                                                                          │
│  QUANDO RODA:                                                            │
│  - Scheduler automático: diariamente às 10:00 BRT (America/Sao_Paulo)  │
│    Para cada seller ativo → processar_baixas_auto(seller_slug)          │
│    Se servidor reiniciar após 10:00, roda imediatamente (catch-up)     │
│  - Manualmente: GET /baixas/processar/{seller}?dry_run=false            │
│                                                                          │
│  SEGURANÇA:                                                              │
│  - dry_run=true por padrão (preview sem executar)                       │
│  - Filtra por ids_contas_financeiras (só toca parcelas ML)              │
│  - Só baixa parcelas com vencimento <= hoje (API CA garante isso)       │
│  - idempotency_key impede baixa dupla da mesma parcela                  │
│  - Rate limit global via TokenBucket (9 req/s compartilhado)            │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Tratamento de Erros

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  ENQUEUE falhou (Supabase insert):                                     │
│    → Erro de rede/banco, não de CA. Raro.                              │
│    → Payment NÃO é marcado como "queued"                               │
│    → Pode ser reprocessado no próximo backfill/webhook                  │
│                                                                         │
│  IDEMPOTENCY CONFLICT (job já existe):                                 │
│    → enqueue() retorna job existente, sem duplicar                     │
│    → Seguro para reprocessamento/webhook retry                         │
│                                                                         │
│  JOB FALHOU (CA 429/5xx):                                              │
│    → Status: failed, retry com backoff exponencial (30s, 120s, 480s)  │
│    → Máximo 3 tentativas, depois → dead letter                         │
│    → Dead letters visíveis em GET /queue/dead                          │
│    → Retry manual: POST /queue/retry/{job_id}                          │
│                                                                         │
│  JOB MORTO (CA 4xx permanente):                                        │
│    → Status: dead (payload inválido, categoria não encontrada, etc.)  │
│    → NÃO faz retry automático (evita loop infinito)                   │
│    → Investigar via /queue/dead, corrigir, POST /queue/retry/{id}     │
│                                                                         │
│  TOKEN CA EXPIROU (401):                                                │
│    → Worker invalida cache, faz refresh via Cognito, retry             │
│    → Se refresh_token também expirou: erro fatal, renovar manualmente  │
│                                                                         │
│  SERVER CRASH MID-FLIGHT:                                               │
│    → Jobs em "processing" > 5min são resetados para "failed" no startup│
│    → Scheduler de baixas roda catch-up se reiniciou após 10:00 BRT    │
│    → Todos os payloads sobrevivem no Supabase (persistente)            │
│                                                                         │
│  Order 404 (ML):                                                        │
│    → Pedido antigo/deletado, log warning                               │
│    → Descrição da receita fica sem nome do item                        │
│    → Frete fica R$ 0 (sem dados de shipping)                           │
│                                                                         │
│  Comissão negativa (amount - net - shipping < 0):                      │
│    → Log warning, set comissão = 0                                      │
│    → Pode indicar cupom/desconto que supera as taxas                   │
│    → Fórmula infalível garante que net sempre bate                     │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Monitoramento da Fila (/queue)

```
GET  /queue/status         → contagem por status (pending/processing/completed/failed/dead)
GET  /queue/dead           → lista dead letters (payload, erro, tentativas)
POST /queue/retry/{job_id} → retry manual de um job dead
POST /queue/retry-all-dead → reseta todos dead para pending
```

---

## Correções Aplicadas (2026-02-11)

### Bug 1: `buscar_parcelas_pagar` usava POST em vez de GET (CRÍTICO)

**Arquivo:** `app/services/ca_api.py` - função `buscar_parcelas_pagar`

**Problema:** O endpoint `/v1/financeiro/eventos-financeiros/contas-a-pagar/buscar` é definido
no OpenAPI spec como **GET com query params**, mas o código enviava como POST com JSON body.
Isso fazia a busca de parcelas falhar silenciosamente, impedindo toda a baixa automática
de despesas (comissão e frete). O warning `"Parcela not found for baixa"` aparecia sempre.

**Correção:**
- `client.post(url, json={...})` → `client.get(url, params={...})`
- Adicionado parâmetro obrigatório `pagina: 1` (required pelo spec)
- Status `"EM_DIA"` → `"EM_ABERTO"` (valor correto do enum da API)

**Impacto:** Todas as despesas de comissão ML e frete criadas antes desta correção
ficaram SEM baixa no Conta Azul (status ATRASADO). Necessário rodar backfill
de baixas para corrigir os registros existentes.

### Bug 2: Debug endpoint com URL errada

**Arquivo:** `app/routers/health.py` - endpoint `/debug/busca-parcela`

**Problema:** URL incorreta `/v1/financeiro/parcelas/contas-a-pagar` (não existe).

**Correção:** URL corrigida para `/v1/financeiro/eventos-financeiros/contas-a-pagar/buscar`
com os mesmos params corretos (GET, pagina, status EM_ABERTO).

### Correção 3: Suporte a in_mediation (2026-02-12)

**Arquivo:** `app/services/processor.py` - função `process_payment_webhook`

**Problema:** Payments com status `in_mediation` caíam no branch `else` e eram salvos
como "pending" sem criar lançamentos no CA. Vendas em mediação são vendas aprovadas
sob disputa — o dinheiro já foi creditado ao seller. Devem gerar receita+despesas.

**Correção:**
- `in_mediation` agora é roteado para `_process_approved()` (mesmo fluxo do approved)
- `date_approved` é usado como data de competência (existe porque a venda foi aprovada antes)
- Se mediação resolver como refund, webhook de "refunded" cria os estornos automaticamente

### Correção 4: Filtro de non-sale payments (2026-02-12)

**Arquivo:** `app/services/processor.py` + `app/routers/backfill.py`

**Problema:** `search_payments` retorna TODOS os payments, incluindo pagamentos de
boletos (DARF, Supabase, Claude.ai), money_transfers (bonificações_flex, proteção
envios full) e ad credits (propaganda ML). Estes não têm order_id e inflavam o total.

**Correção:**
- processor.py: Verifica se payment tem `order_id` antes de processar. Sem order_id → "skipped_non_sale"
- backfill.py: Filtra `(p.get("order") or {}).get("id")` nos processáveis

### Correção 5: Idempotência em _process_refunded (2026-02-12)

**Arquivo:** `app/services/processor.py` - função `_process_refunded`

**Problema:** `_process_approved` tinha check de idempotência (skip se status="synced"),
mas `_process_refunded` não. Em caso de webhook retry ou reprocessamento, estornos
seriam duplicados no CA.

**Correção:** Adicionado check: se existing status="refunded" → skip.

### Correção 6: Backfill inclui in_mediation (2026-02-12)

**Arquivo:** `app/routers/backfill.py`

**Problema:** Backfill só processava `approved` e `refunded`, ignorando `in_mediation`.
Vendas em mediação ficavam de fora do CA.

**Correção:**
- Processáveis: `approved`, `refunded`, `in_mediation`
- Excluídos: `cancelled`, `rejected`, `charged_back` (sem lançamento no CA)
- Already_done: inclui `synced`, `refunded`, `skipped`, `skipped_non_sale`
- Filtro por order_id para excluir non-sale payments

### Correção 7: Refunded cria receita original antes dos estornos (2026-02-12)

**Arquivo:** `app/services/processor.py` - função `_process_refunded`

**Problema:** No backfill, payments com status `refunded` nunca passaram por `_process_approved`,
então a receita original (categoria 1.1.1) nunca era criada. Apenas os estornos eram lançados.
Resultado: o faturamento bruto (1.1.1) no CA não batia com o ML — faltavam as receitas
dos payments que já chegavam como refunded.

**Correção:** `_process_refunded` agora verifica se o payment já tem status "synced" no Supabase.
Se não tem, chama `_process_approved()` primeiro (cria receita + despesas), e depois cria os estornos.

### Correção 8: Estorno receita limitado ao transaction_amount (2026-02-12)

**Arquivo:** `app/services/processor.py` - função `_process_refunded`

**Problema:** O campo `refund.amount` no ML pode incluir o frete devolvido ao comprador,
resultando em valor MAIOR que `transaction_amount`. Exemplo:
- Payment 144522992246: venda R$ 18,90, refund.amount R$ 55,89 (inclui R$ 36,99 de frete)
- Payment 145508379302: venda R$ 76,90, refund.amount R$ 83,89 (inclui R$ 6,99 de frete)
Isso fazia o estorno da receita exceder a receita original (R$ 1.582,74 vs R$ 1.538,76).

**Correção:** `estorno_receita = min(total_refunded, transaction_amount)`.
O estorno da receita nunca pode ser maior que a receita original.

### Riscos conhecidos (não corrigidos)

- `contato` pode ser `None` se seller não tiver `ca_contato_ml` configurado, mas é campo
  obrigatório no `EventoFinanceiroRequest`. Mitigation: garantir que todo seller tenha contato.

### Correção 9: Fila Persistente CA com Rate Limiter Global (2026-02-12)

**Arquivos novos:** `rate_limiter.py`, `ca_queue.py`, `routers/queue.py`
**Arquivos editados:** `processor.py`, `ca_api.py`, `baixas.py`, `main.py`

**Problema:** processor.py chamava a API CA diretamente (fire-and-forget). Com múltiplos
sellers processando simultaneamente, isso causava estouro do rate limit (10 req/s compartilhado),
perda de dados se o servidor caísse mid-flight, sem audit trail, sem idempotência.

**Solução:**
- Tabela `ca_jobs` no Supabase: fila persistente com idempotency_key UNIQUE
- `CaWorker`: background task que processa a fila respeitando rate limit global (9 req/s)
- `TokenBucket`: rate limiter compartilhado entre worker e reads do ca_api
- `enqueue_*()`: 7 wrappers de conveniência (receita, comissao, frete, partial_refund, estorno, estorno_taxa, baixa)
- Scheduler diário de baixas às 10:00 BRT com catch-up no restart
- Endpoints de monitoramento: /queue/status, /queue/dead, /queue/retry

**Garantias:**
- Idempotência: UNIQUE idempotency_key previne duplicatas
- Persistência: payloads sobrevivem a crashes (Supabase)
- Ordem: priority garante receita(10) antes de despesa(20) antes de baixa(30)
- Retry: backoff exponencial (30s, 120s, 480s), max 3 tentativas, depois dead letter
- Audit: ca_jobs registra payload, resposta, protocolo, timestamps, tentativas

### Arquitetura: Baixa separada (2026-02-11)

**Problema:** A baixa imediata (sleep 3s + buscar + baixar) falhava quando
`money_release_date` era futuro. A API CA retorna 400:
`"A data do pagamento deve ser igual ou anterior à data atual"`.

**Solução:** Baixa removida do `processor.py`. Despesas e receitas são criadas
SEM baixa (ficam EM_ABERTO). Job separado `/baixas/processar/{seller}` roda
quando necessário e cria baixas para todas as parcelas com vencimento <= hoje.

**Benefícios:**
- Backfill 2x mais rápido (sem sleep 3s por despesa)
- Sem erros 400 por data futura
- Baixas podem ser retentadas sem reprocessar payments inteiros
- Filtro por `ids_contas_financeiras` garante que só toca parcelas ML

### Correção 10: Filtro marketplace_shipment (2026-02-12)

**Arquivos:** `processor.py`, `backfill.py`

**Problema:** A API `search_payments` do ML retorna payments com `description="marketplace_shipment"` —
pagamentos de frete avulso feitos pelo comprador. Estes têm `order.id` (ID de shipment) mas
NÃO são vendas de produto. O processor criava receita + frete para eles, inflando o faturamento.

**Exemplo real (141AIR, fev/2026):** 5 payments marketplace_shipment totalizando R$ 82,95 foram
lançados incorretamente como receitas no CA. O relatório "Vendas" do ML não inclui estes payments.

**Identificação:** Order IDs curtos (ex: `46453282315` vs `2000015xxxxxxxx`) e
`description="marketplace_shipment"`, `net_received_amount=0` (para fretes full, seller paga tudo).

**Correção:**
- processor.py: Após check de order_id, verifica `description == "marketplace_shipment"` → skip
- backfill.py: Filtro adicional no `processable` para excluir marketplace_shipment

### Correção 11: Suporte a charged_back (2026-02-12)

**Arquivos:** `processor.py`, `backfill.py`

**Problema:** Payments com status `charged_back` (chargeback de cartão) eram ignorados.
O comprador paga, o dinheiro é creditado, depois o banco reverte. Contabilmente, a venda
aconteceu e precisa ser registrada, junto com o estorno do chargeback.

**Exemplo real:** Payment 143699005939 (R$ 115,80) — comprador pagou por PIX (cancelado),
depois cartão (aprovado, depois chargeback). O relatório ML conta como venda (pack_id
`2000011326591001`), mas nosso sistema ignorava.

**Correção:**
- processor.py: `charged_back` roteado para `_process_refunded()` (cria receita + estornos)
- backfill.py: `charged_back` adicionado à lista de status processáveis

**Nota:** O CSV de vendas do ML usa `pack_id` como "N.º de venda", não o `order_id` da API.

### Correção 12: Estorno fallback para transaction_amount_refunded=0 (2026-02-12)

**Arquivo:** `processor.py` - função `_process_refunded`

**Problema:** Para payments `charged_back`, `transaction_amount_refunded` é 0 (não é refund técnico).
O código usava `payment.get("transaction_amount_refunded", amount)`, mas como o campo EXISTE
com valor 0, o default `amount` não era usado. Resultado: estorno com valor R$ 0.

**Correção:** `payment.get("transaction_amount_refunded") or amount` — usa `amount` como
fallback quando o valor é 0 ou None (operador `or` trata falsy values).

---

## Plataforma Unificada (v3.0 - 2026-02-12)

### Contexto

Três projetos separados foram unificados:
- **lever money** (FastAPI) → sync ML/MP payments → Conta Azul
- **dashatt** (FastAPI single-file) → polling ML orders → tabela `faturamento`
- **dash** (React 19 + TS) → dashboard com metas e revenue lines

Tudo agora reside em um único repo (`Eryk-dev/levermoney`) e um único Supabase (`wrbrbhuhsaaupqsimkqz`).

---

## Fluxo: Sync Faturamento (Polling ML Orders)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                  FATURAMENTO SYNC (faturamento_sync.py)                  │
│                                                                          │
│  FaturamentoSyncer - background task (asyncio.Task)                      │
│  Intervalo: 5 min (configurável via sync_interval_minutes)               │
│                                                                          │
│  CICLO:                                                                  │
│  1. _get_syncable_sellers():                                             │
│     SELECT sellers WHERE active=true AND dashboard_empresa IS NOT NULL   │
│     AND ml_user_id IS NOT NULL                                           │
│     → lista de sellers com ML vinculado                                  │
│                                                                          │
│  2. Para cada seller:                                                    │
│     a) ml_api.fetch_paid_orders(seller_slug, date_str)                  │
│        → GET /orders/search?seller={ml_user_id}&order.date_created=...   │
│        → Soma total de orders com status=paid (paginado)                 │
│     b) Usa per-seller ML credentials (ml_app_id, ml_secret_key)         │
│        com fallback para credentials globais                             │
│     c) Upsert na tabela faturamento:                                     │
│        empresa=seller.dashboard_empresa, data=hoje, valor=total          │
│                                                                          │
│  3. Resultado: cada seller tem 1 row por dia na tabela faturamento       │
│                                                                          │
│  TABELA: faturamento                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  empresa: "141AIR"         (de sellers.dashboard_empresa)       │     │
│  │  data: 2026-02-12          (date do sync)                       │     │
│  │  valor: 15420.50           (soma orders ML do dia)              │     │
│  │  source: "sync"            (auto) ou "manual" (DataEntry)       │     │
│  │  UNIQUE(empresa, data)     (upsert, não duplica)                │     │
│  └─────────────────────────────────────────────────────────────────┘     │
│                                                                          │
│  NOTA: Per-seller ML apps (dashatt legado) usam refresh tokens de        │
│  uso único. O token refresh é atômico: lê refresh_token → troca por     │
│  novo access+refresh → salva ambos. Se dois processos lerem o mesmo     │
│  refresh_token, o segundo falhará (token já consumido).                  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Fluxo: Onboarding de Sellers

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    ONBOARDING (onboarding.py)                            │
│                                                                          │
│  FLUXO HÍBRIDO: seller se cadastra → admin aprova → OAuth ML ativa      │
│                                                                          │
│  ┌─────────────┐     ┌──────────────┐     ┌───────────────┐             │
│  │  SIGNUP      │────▶│  PENDENTE    │────▶│  APROVADO     │             │
│  │  (seller)    │     │  (aguarda)   │     │  (admin)      │             │
│  └─────────────┘     └──────────────┘     └──────┬────────┘             │
│                                                    │                     │
│                                              OAuth ML                    │
│                                              /connect                    │
│                                                    │                     │
│                                                    ▼                     │
│                                           ┌───────────────┐             │
│                                           │  ATIVO         │             │
│                                           │  (syncing)     │             │
│                                           └───────────────┘             │
│                                                                          │
│  ETAPAS:                                                                 │
│                                                                          │
│  1. create_signup(slug, name, email):                                    │
│     INSERT INTO sellers (slug, name, email, onboarding_status)           │
│     VALUES (..., 'pending_approval')                                     │
│     CA fields ficam NULL (NOT NULL removido na migration)                │
│                                                                          │
│  2. approve_seller(id, config):                                          │
│     UPDATE sellers SET dashboard_empresa, dashboard_grupo,               │
│       dashboard_segmento, onboarding_status='approved'                   │
│     INSERT INTO revenue_lines (empresa, grupo, segmento, seller_id)     │
│     INSERT INTO goals (empresa, grupo, year, month, valor=0) x12        │
│                                                                          │
│  3. OAuth /connect → /callback:                                          │
│     auth_ml.py verifica onboarding_status IN (approved, active, NULL)   │
│     Salva tokens ML → activate_seller(slug):                             │
│       UPDATE sellers SET active=true, onboarding_status='active'        │
│                                                                          │
│  4. Seller agora aparece no FaturamentoSyncer (tem dashboard_empresa     │
│     + ml_user_id) e no processor.py (has CA config after admin setup)    │
│                                                                          │
│  REJECT:                                                                 │
│  reject_seller(id) → onboarding_status='suspended', active=false        │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Admin Panel (Dashboard)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    ADMIN PANEL (React + Backend API)                      │
│                                                                          │
│  AUTENTICAÇÃO:                                                           │
│  POST /admin/login { password } → { session_token }                     │
│  Senha bcrypt, hash em admin_config table                                │
│  Primeiro login: cria o hash (setup mode)                                │
│  Token de sessão: UUID em memória, 24h TTL                              │
│  Header: X-Admin-Token em todas as requests                              │
│                                                                          │
│  ENDPOINTS ADMIN:                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  GET  /admin/sellers           → lista todos sellers            │   │
│  │  GET  /admin/sellers/pending   → lista pendentes                │   │
│  │  POST /admin/sellers/{id}/approve  → aprova + cria line/goals  │   │
│  │  POST /admin/sellers/{id}/reject   → rejeita                   │   │
│  │  PATCH /admin/sellers/{id}    → edita config                    │   │
│  │  GET  /admin/revenue-lines    → lista revenue lines             │   │
│  │  POST /admin/revenue-lines    → cria line manual                │   │
│  │  PATCH /admin/revenue-lines/{empresa}  → edita line             │   │
│  │  GET  /admin/goals?year=2026  → lista goals                     │   │
│  │  POST /admin/goals/bulk       → upsert batch de goals           │   │
│  │  POST /admin/sync/trigger     → trigger sync manual             │   │
│  │  GET  /admin/sync/status      → último sync result              │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ENDPOINTS DASHBOARD (públicos):                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  GET  /dashboard/revenue-lines → linhas ativas                  │   │
│  │  GET  /dashboard/goals?year=   → goals do ano                   │   │
│  │  POST /dashboard/faturamento/entry  → upsert manual             │   │
│  │  POST /dashboard/faturamento/delete → deleta entrada            │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  DASHBOARD UI:                                                           │
│  - View "Admin" no ViewToggle (visível após login admin)                │
│  - Ícone cadeado no header (acesso rápido quando não logado)            │
│  - AdminLogin: form de senha simples                                     │
│  - AdminPanel: sync status, sellers pendentes/ativos, management        │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Schema Unificado (Supabase wrbrbhuhsaaupqsimkqz)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    TABELAS SUPABASE (migration 002)                      │
│                                                                          │
│  sellers (expandida):                                                    │
│    + email, onboarding_status, approved_at                               │
│    + dashboard_empresa, dashboard_grupo, dashboard_segmento              │
│    + source ('ml'|'manual'), ml_app_id, ml_secret_key                   │
│    + ca_contato_ml                                                       │
│    - ca_conta_mp_disponivel: NOW NULLABLE (pending sellers)              │
│    - ca_centro_custo_variavel: NOW NULLABLE (pending sellers)            │
│                                                                          │
│  faturamento (nova - migrada do Supabase antigo):                        │
│    empresa TEXT, data DATE, valor NUMERIC, source TEXT                    │
│    UNIQUE(empresa, data) | RLS: anon SELECT, service_role ALL           │
│    Realtime habilitado                                                    │
│                                                                          │
│  revenue_lines (nova - substitui localStorage):                          │
│    empresa TEXT UNIQUE, grupo TEXT, segmento TEXT                         │
│    seller_id UUID (FK sellers, NULL para manuais)                        │
│    source TEXT, active BOOLEAN                                           │
│    RLS: anon SELECT, service_role ALL | Realtime habilitado             │
│                                                                          │
│  goals (nova - substitui localStorage):                                  │
│    empresa TEXT, grupo TEXT, year INT, month INT, valor NUMERIC           │
│    UNIQUE(empresa, year, month)                                          │
│    RLS: anon SELECT, service_role ALL | Realtime habilitado             │
│                                                                          │
│  meli_tokens (nova - migrada do Supabase antigo):                        │
│    account_name TEXT PK, seller_id UUID (FK sellers)                     │
│    refresh_token, access_token, expires_at                               │
│                                                                          │
│  admin_config (nova):                                                    │
│    id INT PK DEFAULT 1, password_hash TEXT                               │
│                                                                          │
│  payments, ca_jobs, ca_tokens, sellers (existentes - sem mudança)       │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Estrutura do Repositório

```
levermoney/
├── app/                              # Python backend (FastAPI)
│   ├── main.py                       # Lifespan, CORS, routers, syncer
│   ├── config.py                     # Settings (cors_origins, sync_interval)
│   ├── db/supabase.py                # Singleton Supabase client
│   ├── models/sellers.py             # CA_CATEGORIES, get_seller_config
│   ├── routers/
│   │   ├── webhooks.py               # ML webhook receiver
│   │   ├── backfill.py               # Batch reprocessing
│   │   ├── baixas.py                 # Settlement job
│   │   ├── auth_ml.py                # ML OAuth + onboarding activation
│   │   ├── admin.py                  # Admin CRUD (password-protected)
│   │   ├── dashboard_api.py          # Public dashboard endpoints
│   │   ├── queue.py                  # Queue monitoring
│   │   └── health.py                 # Health + debug
│   └── services/
│       ├── processor.py              # Payment → CA entries (via queue)
│       ├── ca_api.py                 # Conta Azul API client
│       ├── ca_queue.py               # CaWorker (persistent queue)
│       ├── ml_api.py                 # ML API (per-seller credentials)
│       ├── rate_limiter.py           # TokenBucket (9 req/s)
│       ├── faturamento_sync.py       # ML orders polling → faturamento
│       └── onboarding.py             # Seller lifecycle management
├── dashboard/                        # React 19 + TypeScript
│   ├── src/
│   │   ├── App.tsx                   # 5 views (geral/metas/entrada/linhas/admin)
│   │   ├── lib/supabase.ts           # Supabase + API_BASE config
│   │   ├── hooks/
│   │   │   ├── useSupabaseFaturamento.ts
│   │   │   ├── useFilters.ts         # Motor central de cálculos
│   │   │   ├── useGoals.ts           # Supabase-backed goals
│   │   │   ├── useRevenueLines.ts    # Supabase-backed revenue lines
│   │   │   ├── useAdmin.ts           # Admin auth + seller management
│   │   │   └── useIsMobile.ts
│   │   ├── components/               # 24 componentes
│   │   │   ├── AdminPanel.tsx        # Seller/sync management
│   │   │   ├── AdminLogin.tsx        # Password form
│   │   │   ├── ViewToggle.tsx        # 5 views + admin lock icon
│   │   │   └── ... (21 existentes)
│   │   ├── data/                     # Fallback data
│   │   └── utils/                    # goalCalculator, projectionEngine
│   ├── Dockerfile                    # node:20 build + nginx serve
│   └── nginx.conf
├── migrations/
│   ├── 001_initial.sql               # sellers, payments, ca_jobs, ca_tokens
│   └── 002_unified_platform.sql      # faturamento, revenue_lines, goals, etc.
├── docker-compose.yml                # api (8000) + dashboard (3000)
├── Dockerfile                        # Python 3.12 + uvicorn
├── requirements.txt                  # FastAPI, supabase, httpx, bcrypt
├── PLANO.md                          # Business plan v1.8
├── FLUXO-DETALHADO.md                # THIS DOCUMENT
└── REFERENCIA-APIs-ML-MP.md          # ML/MP API reference
```

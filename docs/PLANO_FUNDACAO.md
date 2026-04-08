# Plano de Fundacao — Reconciliacao LeverMoney

> Plano persistente para reconstruir a fundacao de testes e validacao do pipeline.
> Acompanha o progresso entre multiplas sessoes de trabalho com Claude.
> **Seller de referencia:** 141air | **Mes de referencia:** Janeiro 2026

---

## Inventario: O que existe no repositorio

### Documentacao de auditoria (`docs/`)

| Arquivo | O que e | Status |
|---------|---------|--------|
| `DOSSIE_AUDITORIA_JAN2026.md` | Dossie completo: 6 tipos de falha, 8 fixes, resultado final | Completo |
| `AUDITORIA_JAN2026_141AIR.md` | Auditoria dia a dia com catalogo de falhas | Completo |
| `DIVERGENCIAS_CONHECIMENTO.md` | Base de conhecimento de divergencias extrato vs sistema | Completo |
| `GAP_ANALYSIS_JAN2026.md` | Analise de gap V1 (payments.net vs extrato) — superada | Historico |
| `GAP_ANALYSIS_V2_CAJOBS.md` | Analise de gap V2 (ca_jobs + mp_expenses por ref_id) | Historico |
| `METODOLOGIA_DIVERGENCIAS.md` | Metodologia de busca de divergencias | Referencia |

### Testes pytest (raiz de `testes/`)

Rodam com `pytest` (comando: `python3 -m pytest`). Offline, sem Supabase/API. **366 testes, ~2.5s.**

| Arquivo | O que testa | Criado em |
|---------|-------------|-----------|
| `test_processor_unit.py` | Matematica do processor: taxas, frete, estornos, subsidios (31 testes) | Sessao #1 |
| `test_extrato_classification.py` | Classificacao de linhas do extrato, parsing CSV, regras (73 testes) | Sessao #1 |
| `test_dre_reconciliation.py` | DRE completo jan 2026: receita, comissao, frete, devolucoes, estornos, extrato match (48 testes) | Sessao #2 |
| `test_dre_reconciliation_fev2026.py` | DRE completo fev 2026: mesma estrutura que jan (46 testes) | Sessao #3 |
| `test_event_ledger.py` | Event ledger: idempotency keys, validate_event, sign conventions, coverage (59 testes) | Sessao #3 |
| `test_event_ledger_backfill.py` | Backfill validation: simula ledger com cache, valida DRE jan+fev (25 testes) | Sessao #3 |
| `test_event_ledger_integration.py` | Event ledger async: record/get/balance/DRE/pagination com mock DB (26 testes) | Sessao #5 |
| `test_processor_integration.py` | Processor orquestracao: state machine, filtros, subsidio, refunds (25 testes) | Sessao #5 |
| `test_ca_queue_unit.py` | CA queue: enqueue idempotencia, wrappers, group completion (13 testes) | Sessao #5 |
| `test_daily_sync_unit.py` | Daily sync: sync window, dedup, filtros, status change detection (20 testes) | Sessao #5 |
| `conftest.py` | Fixtures compartilhados (payments reais do cache 141air) + collect_ignore_glob | Sessao #1 |

### Scripts standalone (`testes/standalone/`)

**NAO sao pytest.** Rodam individualmente com `python3 testes/standalone/script.py`.
Excluidos do pytest via `conftest.py:collect_ignore_glob = ["standalone/**"]`.

| Arquivo | O que faz | Precisa de |
|---------|-----------|------------|
| `test_extrato_ingester.py` | 9 grupos de testes offline do extrato_ingester (assertions manuais) | Nada (offline) |
| `test_reconciliation_141air.py` | Reconciliacao por ref_id contra Supabase, gera relatorio | Supabase |
| `test_onboarding_backfill.py` | 12 testes do backfill (10 pass, 2 skip) | Supabase + ML API |
| `test_admin_endpoints.py` | Validacao de endpoints admin | API rodando |

### Scripts de simulacao (`testes/simulacoes/`)

Simulacoes que usam cache local (JSON) + extratos (CSV). Nao gravam nada.

| Arquivo | O que simula |
|---------|-------------|
| `simulate_fresh_backfill_141air.py` | Backfill do zero, cobertura 690/690 linhas, gap R$ 0,00 |
| `simulate_onboarding_141air_jan2026.py` | Onboarding com ca_start_date=2026-01-01 (multi-seller) |
| `simulate_dre_141air_jan2026.py` | DRE por competencia (date_approved) |
| `simulate_dre.py` | DRE parametrico por mes |
| `simulate_caixa_jan2026.py` | DRE por caixa (money_release_date) |
| `dre_janeiro_141air.py` | DRE especifico 141air janeiro |
| `fluxo_caixa_netair_jan2026.py` | Fluxo de caixa netair |

### Utilitarios e dados

| Item | Localizacao | Descricao |
|------|-------------|-----------|
| `validate_full_coverage.py` | `testes/` | Mede cobertura do extrato vs Supabase (READ-ONLY) |
| `classify_non_orders.py` | `testes/` | Classifica non-order payments do cache → mp_expenses |
| `ingest_extrato_gaps.py` | `testes/` | Ingere gaps do extrato CSV → mp_expenses |
| `rebuild_cache.py` | `testes/utils/` | Reconstroi cache de payments via ML API |
| `backfill_events.py` | `testes/utils/` | Converte cache payments → eventos (validacao offline) |
| `cache_jan2026/` | `testes/data/` | Cache de payments JSON (4 sellers, ~2M tokens total) |
| `cache_fev2026/` | `testes/data/` | Cache de payments JSON fevereiro |
| `extratos/` | `testes/data/` | Extratos CSV reais do MP (4 sellers x 2 meses) |
| `reconciliation_report_*.txt` | `testes/reports/` | Relatorios gerados |

---

## Estado Atual (2026-03-12)

### Arquitetura v2.0: Event Ledger

A partir da Sessao #3, o sistema migrou de snapshot mutavel (`payments` table) para event sourcing
(`payment_events` table). Toda leitura e escrita de estado de pagamentos passa pelo `event_ledger.py`.
A tabela `payments` nao e mais escrita por nenhum servico. 12 arquivos migrados, zero referencias
ativas a tabela antiga no codigo de producao.

**Diagnostico (Sessao #4):** 4 agentes especializados auditaram a implementacao e encontraram
3 problemas criticos, 4 importantes, e 3 gaps criticos de teste. Ver secao "O que ainda falta" abaixo.

### Resumo de tudo que ja foi feito

- [x] Supabase limpo (tabelas transacionais zeradas, dashboard + sellers preservados)
- [x] `pyproject.toml` criado com configuracao pytest
- [x] Scripts standalone excluidos do pytest via `collect_ignore`
- [x] `conftest.py` com fixtures de payments reais (7 cenarios)
- [x] `test_processor_unit.py` — 31 testes do core financeiro
- [x] `test_extrato_classification.py` — 73 testes de classificacao
- [x] `test_dre_reconciliation.py` — 48 testes DRE jan/2026 com dados reais
- [x] `test_dre_reconciliation_fev2026.py` — 46 testes DRE fev/2026
- [x] `test_event_ledger.py` — 40 testes de pure functions do event ledger
- [x] `test_event_ledger_backfill.py` — 25 testes de backfill validation DRE via eventos
- [x] Inventario completo do repositorio documentado
- [x] Event Ledger implementado: `payment_events` table, `event_ledger.py`, 12 arquivos migrados
- [x] Migracao completa: zero referencias a `payments` table no codigo de producao
- [x] 282 testes passando, ~2.0s, offline (Fase 0)
- [x] `test_event_ledger_integration.py` — 26 testes async com mock DB
- [x] `test_processor_integration.py` — 25 testes de orquestracao (state machine, filtros, subsidio, refunds)
- [x] `test_ca_queue_unit.py` — 13 testes (enqueue idempotencia, wrappers, group completion)
- [x] `test_daily_sync_unit.py` — 20 testes (sync window, dedup, filtros, status change)
- [x] 366 testes passando, ~2.5s, offline (Fase 1)
- [x] Backfill real 141air jan/2026: 439 payments, 0 erros, 1441 CA jobs completed (Fase 2)
- [x] DRE validado em producao: gap = R$ 0,00 em todas as linhas (Fase 2)
- [x] Cobertura extrato completa: 690/690 linhas, 0 gaps, R$ 0,00 (Fase 2.5)
- [x] Classifier: 78 non-order payments classificados em mp_expenses (Fase 2.5)
- [x] Extrato ingester: 235 linhas de gap ingeridas em mp_expenses (Fase 2.5)
- [x] Migracao mp_expenses.payment_id: bigint → text (suporte a composite keys) (Fase 2.5)

---

## O que ainda falta

### Fase 0: Estabilizar Event Ledger (PRIORITARIA)

Bugs e problemas arquiteturais encontrados pelo diagnostico da Sessao #4.
**Devem ser resolvidos ANTES de rodar backfill real ou ir para producao.**

#### Bugs criticos
- [x] **Bug `len(payments)` em `queue.py:185`** — NameError em runtime, corrigido para `len(events_by_pid)` (Sessao #4)
- [x] **`record_event()` engole todas as excecoes** — agora levanta `EventRecordError`. Callers capturam especificamente e logam em nivel ERROR (Sessao #4)
- [x] **`get_dre_summary()` sem paginacao** — agora paginado com page_limit=1000 (Sessao #4)

#### Dívida arquitetural
- [x] **Status derivation duplicada em 4 arquivos** — criada `derive_payment_status()` centralizada em `event_ledger.py`. 3 copias removidas de `daily_sync.py`, `financial_closing.py`, `queue.py` (Sessao #4)
- [x] **`financial_closing.py` e `queue.py` queries diretas eliminadas** — criada `get_payment_statuses()` no event_ledger.py. Ambos agora usam o helper (Sessao #4)
- [x] **Ajustes do release_report_validator agora entram no ledger** — novos event types `adjustment_fee` e `adjustment_shipping` (negativos). Gravados com metadata (processor_fee vs release_fee) (Sessao #4)
- [x] **Float vs Decimal: decisao = manter float** — risco teorico sub-centavo para esta escala (<5000 eventos/mes, <R$200k). Supabase SDK retorna float mesmo com NUMERIC(12,2). 282 testes validam ao centavo. Converter tocaria dezenas de arquivos sem beneficio pratico (Sessao #4)

### Fase 1: Testes de integracao do Event Ledger (COMPLETA)

Cobertura de teste cobre funcoes async com mock DB e orquestracao com mock services.
84 testes novos em 4 arquivos.

- [x] Mock de `get_db()` para testar `record_event`, `get_events`, `get_balance`, `get_dre_summary` offline (26 testes, Sessao #5)
- [x] Testar orquestracao async do processor (`process_payment_webhook`, `_process_approved`, `_process_refunded`) com mock de `event_ledger` + `ca_queue` (25 testes, Sessao #5)
- [x] Testar state machine: `existing_event_types` → branching correto (8 caminhos) (Sessao #5)
- [x] Testar paginacao boundary de `get_processed_payment_ids` (exato 1000 rows) (Sessao #5)
- [x] Testar `charged_back+reimbursed` → emite `sale_approved` e NAO `refund_created` (Sessao #5)
- [x] Testar `_process_partial_refund` index-based idempotency com re-runs (Sessao #5)
- [x] Testar subsidio (`net_diff > 0`) → emite `subsidy_credited` com amount correto (Sessao #5)
- [x] Testes ca_queue.py (enqueue idempotencia, prioridade, group completion) (13 testes, Sessao #5)
- [x] Testes daily_sync.py (dedup, filtros, date ranges, status change) (20 testes, Sessao #5)

### Fase 2: Validar em producao (COMPLETA)

- [x] Supabase limpo para fresh start
- [x] Rodar backfill real para 141air Janeiro (439 payments, 0 erros, 1441 CA jobs completed) (Sessao #6)
- [x] Release report sync: 6 expenses ingeridos (Sessao #6)
- [x] Rodar reconciliacao e verificar gap = R$ 0,00 (Sessao #6)
- [ ] Resolver questoes abertas do dossie

**Resultado Fase 2 (Sessao #6):**
- Backfill: 486 payments ML → 439 processaveis (filtros: 47 excluidos) → 0 erros
- CA queue: 1441 jobs (438 receita, 435 comissao, 362 frete, 77 estorno, 75 estorno_taxa, 54 estorno_frete) → todos completed
- DRE gap = R$ 0,00 em todas as linhas (receita, comissao, frete, devolucoes, estornos)
- Resultado operacional: R$ 109.555,37 (match exato com referencia offline)
- Nota: 272/438 payments sem ca_sync_completed (race condition no backfill concorrente; nao afeta financeiro)

### Fase 2.5: Validacao completa do extrato (COMPLETA)

Validacao de que TODAS as 690 linhas do extrato 141Air Janeiro estao cobertas pelo sistema.

- [x] Script de medicao: `testes/validate_full_coverage.py` — mede cobertura a qualquer momento (Sessao #7)
- [x] Baseline: 535/690 (77.5%) — payments + release report cobrem apenas order-based (Sessao #7)
- [x] Classifier: 78 non-order payments → mp_expenses (boletos, transfers, subscriptions, cashback) (Sessao #7)
- [x] Extrato ingester: 235 linhas de gap → mp_expenses (disputas, DIFAL, faturas, liberacoes nao-sync) (Sessao #7)
- [x] Migracao: `mp_expenses.payment_id` bigint → text (composite keys como "123456:dd") (Sessao #7)
- [x] Cobertura final: **690/690 (100.0%), 0 gaps, R$ 0,00** (Sessao #7)

**Resultado Fase 2.5 (Sessao #7):**
- 690 linhas total: 50 skips + 330 payment_events + 310 mp_expenses = 690
- 319 registros em mp_expenses: 84 do classifier (plain IDs) + 235 do extrato ingester (composite keys)
- 3 amount updates (IOF correction: subscriptions com valor diferente no extrato vs API)
- Progressao: 77.5% → 79.3% → 100.0%

### GAP CRITICO: Divergencia entre Pipeline Automatico e Validacao Manual

> **Descoberto na Sessao #8.** Este gap explica por que a validacao manual atingiu 100%
> mas o pipeline automatico NAO atinge o mesmo resultado.

#### Contexto: Dois CSVs completamente diferentes

O Mercado Livre/Mercado Pago disponibiliza dois relatorios com formatos incompativeis:

**CSV 1: Account Statement (extrato)**
- Fonte: download manual do painel do Mercado Pago
- Formato: 5 colunas, numeros BR (virgula decimal), labels em portugues
- Tem saldo corrente (running balance) por linha e resumo com saldo inicial/final
- Exemplo de arquivo: `testes/data/extratos/extrato janeiro 141Air.csv`

```
INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
4.476,23;207.185,69;-210.571,52;1.090,40

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
01-01-2026;Transferencia Pix enviada LILLIAN;139632176183;-350,00;4.126,23
01-01-2026;Liberacao de dinheiro ;138199281600;3.994,84;5.771,27
```

**CSV 2: Release Report**
- Fonte: ML API endpoint `/v1/account/release_report` (download automatico)
- Formato: 17+ colunas, numeros decimais padrao, labels em ingles, breakdown de taxas
- SEM saldo corrente, SEM resumo de saldo

```
DATE;SOURCE_ID;RECORD_TYPE;DESCRIPTION;NET_CREDIT_AMOUNT;NET_DEBIT_AMOUNT;GROSS_AMOUNT;MP_FEE_AMOUNT;...
2026-01-05;138199281600;release;payment;3994.84;0;4600.00;497.71;...
```

#### O que cada fluxo usa

**Fluxo A: Pipeline Automatico (nightly, roda em producao)**

Usa **Release Report CSV** (baixado via ML API automaticamente).

| Step | Servico | O que faz | Fonte CSV |
|------|---------|-----------|-----------|
| 1 | `daily_sync.py` | Busca payments via ML Payments API, processa orders + classifica non-orders | ML Payments API (JSON) |
| 2 | `release_report_sync.py` | Parseia release report, ingesta payouts/cashback/shipping em `mp_expenses` | Release Report |
| 3 | `release_report_validator.py` | Valida fees do processor vs release report, cria adjustments | Release Report |
| 4 | `extrato_ingester.py` | Deveria ingerir gaps do extrato | **Release Report** (formato errado) |
| 5 | baixas | Processa baixas para todos sellers | - |
| 6 | legacy export | Export legado (XLSX) | - |
| 7 | `extrato_coverage_checker.py` | Verifica cobertura | **Release Report** (mede contra fonte diferente) |
| 8 | `ca_categories_sync.py` | Sync categorias CA | - |
| 9 | `financial_closing.py` | Fechamento financeiro | - |

**Fluxo B: Scripts Manuais (validacao, rodam sob demanda)**

Usa **Account Statement CSV** (baixado manualmente do painel MP).

| Script | O que faz | Fonte CSV | Grava no Supabase? |
|--------|-----------|-----------|---------------------|
| `classify_non_orders.py` | Classifica non-order payments do cache JSON | Cache JSON local | Sim (mp_expenses) |
| `ingest_extrato_gaps.py` | Ingesta linhas de gap → mp_expenses | Account Statement | Sim (mp_expenses) |
| `validate_full_coverage.py` | Mede cobertura por linhas (690/690 = 100%) | Account Statement | Nao (read-only) |
| `validate_daily_balance.py` | Reconcilia valores dia a dia (R$ 0,00 gap) | Account Statement | Nao (read-only) |

#### Problemas identificados

**Problema 1: Step 4 do pipeline e um no-op**

O `extrato_ingester.py` importa `_get_or_create_report()` de `release_report_sync.py`, que baixa
o **Release Report CSV** via ML API. Mas depois chama `_parse_account_statement()`, que espera o
formato do **Account Statement** (procura headers `INITIAL_BALANCE` e `RELEASE_DATE`). Como o
Release Report nao tem esses headers, o parser retorna 0 transacoes → nada e ingerido.

- `extrato_ingester.py` linha 39: `from app.services.release_report_sync import _get_or_create_report`
- `extrato_ingester.py` linha 780: `csv_bytes = await _get_or_create_report(...)` → recebe Release Report
- `extrato_ingester.py` linha 795: `_parse_account_statement(csv_text)` → espera Account Statement → retorna `([], {})`

Impacto: As 235 linhas de gap (DIFAL, faturas ML, disputas, liberacoes nao-sync) que o script
manual ingeriu **nunca seriam ingeridas pelo pipeline automatico**.

**Problema 2: Step 7 mede contra a fonte errada**

O `extrato_coverage_checker.py` importa `_parse_release_report_with_fees()` de `release_report_validator.py`,
que parseia o **Release Report CSV**. Ele mede cobertura contra o Release Report (que tem linhas com
`DESCRIPTION` como "payment", "refund", "payout"), NAO contra o Account Statement real (que tem
`TRANSACTION_TYPE` como "Liberacao de dinheiro", "Pagamento com QR codigo", "Diferenca da aliquota de ICMS").

O Release Report tem **menos granularidade**: tipos genericos em ingles vs ~30 tipos especificos em
portugues no Account Statement. A cobertura medida pelo pipeline pode dar 100% no Release Report
mas ter gaps no Account Statement.

**Problema 3: Reconciliacao de saldo dia a dia nao existe no pipeline**

O `validate_daily_balance.py` que verifica se os valores financeiros batem dia a dia (nao apenas
as linhas) **nao tem equivalente no pipeline automatico**. Isso exige o Account Statement com
saldo corrente — informacao que o Release Report nao tem.

#### O que funciona corretamente

Os steps 1, 2, 3 do pipeline funcionam corretamente com o Release Report:
- Step 1 (`daily_sync`) busca payments da ML Payments API (JSON, nao CSV) — funciona
- Step 2 (`release_report_sync`) parseia Release Report com `_parse_csv()` → ingesta payouts/cashback/shipping — funciona
- Step 3 (`release_report_validator`) parseia Release Report com `_parse_release_report_with_fees()` → valida fees — funciona

A logica de classificacao e ingestao do extrato tambem funciona corretamente:
- `_parse_account_statement()` — parseia o Account Statement CSV ✓
- `_classify_extrato_line()` — classifica cada linha com 30+ regras ✓
- `_build_expense_from_extrato()` — constroi o registro para mp_expenses ✓
- Composite keys, dedup, IOF correction, fuzzy matching — tudo funciona ✓

O problema e **exclusivamente a fonte de dados**: o pipeline baixa o CSV errado para os steps 4 e 7.

#### Causa raiz

O Account Statement (extrato com saldo corrente, formato PT-BR) **nao esta disponivel via nenhuma API do ML**.

**Investigacao completa da API (Sessao #9):**

Foram investigados **todos** os endpoints de relatorios do Mercado Pago:

| Endpoint API | Rota | Formato CSV | Saldo Inicial | Running Balance | Labels PT-BR | Tipos Granulares |
|---|---|---|---|---|---|---|
| Release Report | `/v1/account/release_report` | 17+ colunas, EN | Nao | Nao | Nao | ~10 genericos |
| Bank Report | `/v1/account/bank_report` | 17+ colunas, EN | **Sim** (`initial_available_balance` como RECORD_TYPE) | Nao | Nao | ~10 genericos |
| Settlement Report | `/v1/account/settlement_report` | 17+ colunas, EN | Nao | Nao | Nao | ~10 genericos |

Os 3 endpoints retornam o **mesmo formato** (Release Report):
```
DATE;SOURCE_ID;RECORD_TYPE;DESCRIPTION;NET_CREDIT_AMOUNT;NET_DEBIT_AMOUNT;GROSS_AMOUNT;MP_FEE_AMOUNT;...
```

**Nenhum** retorna o formato Account Statement com:
- Summary `INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE`
- Transacoes `RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE`
- ~30 tipos granulares em portugues ("Transferencia Pix enviada", "DIFAL", "Pagamento de fatura Mercado Pago")
- Running balance (saldo corrente) por linha

O report antigo "Money withdrawn" (que era o mais proximo do Account Statement) foi **descontinuado** pelo MP em favor do "Releases" report.

**`bank_report` e ligeiramente superior ao `release_report`:**
- Tem `initial_available_balance` como linha, permitindo calcular running balance sintetico
- Mesmo formato de colunas (17+), mesmos tipos genericos em ingles
- Perde granularidade: `payment` (generico) vs ~30 tipos especificos do Account Statement

**Conclusao:** A unica forma de obter o Account Statement real e download manual do painel web do MP.
API pura NAO resolve o problema. A conversao automatica via `_convert_release_to_account_statement_csv()`
(ja existente em `legacy/daily_export.py:315`) cobre ~95% mas perde granularidade.

#### Codigo existente de conversao

O sistema ja tem funcoes que convertem Release Report → Account Statement:

| Funcao | Arquivo | O que faz |
|---|---|---|
| `_convert_release_to_account_statement_csv()` | `legacy/daily_export.py:315` | Converte Release Report para formato Account Statement. Mapa lossy: `payment → "Liberacao de dinheiro"`, `refund → "Reembolso Reclamacoes"`, etc. Calcula running balance a partir de 0. INITIAL_BALANCE fixo em 0,00. |
| `_ensure_account_statement_csv()` | `legacy/daily_export.py:403` | Auto-detecta formato e converte se necessario. |
| `_convert_settlement_to_account_statement_csv()` | `legacy/daily_export.py:239` | Converte Settlement Report (formato similar). |

**Limitacoes da conversao:**
- ~10 tipos genericos em ingles → ~10 tipos em portugues (vs ~30 do extrato real)
- `INITIAL_BALANCE` fixo em R$ 0,00 (Release Report nao tem essa informacao; `bank_report` teria)
- Running balance calculado, nao verificado contra fonte oficial
- Tipos como DIFAL, faturas ML, transferencias PIX com nome do destinatario nao sao distinguiveis

#### Solucao recomendada: Hibrido (Caminho 1 + 2)

| Caminho | O que faz | Cobertura | Esforco |
|---|---|---|---|
| **1 — Conversao automatica** | Pipeline usa `bank_report` + `_ensure_account_statement_csv()` | ~95% (perde granularidade) | Medio |
| **2 — Upload manual mensal** | Endpoint admin para upload do extrato CSV real do painel MP | 100% (extrato real) | Baixo |
| ~~**3 — API pura**~~ | ~~Buscar extrato via API~~ | **Impossivel** — nenhuma API fornece Account Statement | — |

**Proposta concreta:**

1. **Automatico (diario):** Pipeline usa `bank_report` via API → converte para formato Account Statement
   → alimenta `extrato_ingester` e `extrato_coverage_checker`. Cobre ~95% das linhas automaticamente.
   Vantagem sobre `release_report`: tem `initial_available_balance`.

2. **Manual (mensal):** No fechamento do mes, usuario faz upload do extrato CSV real do painel MP via
   endpoint admin. O sistema roda a reconciliacao exata (mesma logica dos scripts `validate_full_coverage.py`
   e `validate_daily_balance.py`) e reporta gaps remanescentes. As linhas adicionais que so existem no
   extrato real (~5%) sao ingeridas automaticamente.

#### O que falta para implementar

1. **Endpoint admin de upload:** `POST /admin/extrato/upload` — recebe CSV do Account Statement,
   valida formato, armazena no Supabase (tabela nova `account_statements` ou storage), dispara
   ingestao + cobertura + reconciliacao dia a dia.

2. **Corrigir step 4 do pipeline:** `extrato_ingester.py` deve:
   - Verificar se existe Account Statement uploaded para o seller/mes
   - Se sim: usar o Account Statement real (100% cobertura)
   - Se nao: baixar `bank_report` via API, converter com `_ensure_account_statement_csv()` (~95%)

3. **Corrigir step 7 do pipeline:** `extrato_coverage_checker.py` deve medir contra a mesma fonte
   que o step 4 usou (Account Statement real se disponivel, senao bank_report convertido).

4. **Opcional: `bank_report` em vez de `release_report`:** Mudar `_get_or_create_report()` para
   usar `/v1/account/bank_report` em vez de `/v1/account/release_report`, ganhando
   `initial_available_balance` e running balance sintetico mais preciso.

### Fase 3: Expandir cobertura

- [ ] Reconciliacao: netair, netparts, easypeasy (Janeiro)
- [ ] Reconciliacao: 141air Fevereiro (validar timing refunds)
- [ ] Parametrizar teste de reconciliacao (generico por seller/mes)

### Fase 4: Engenharia

- [x] CHANGELOG.md
- [x] Versionamento (2.0.0)
- [ ] CI basico (rodar pytest no push)

---

## Como rodar

```bash
cd "/Volumes/SSD Eryk/LeverMoney"

# Testes automatizados pytest (366 testes, ~2.5s, offline)
python3 -m pytest
python3 -m pytest -v --tb=long                              # detalhado
python3 -m pytest testes/test_dre_reconciliation.py -v       # so DRE
python3 -m pytest testes/test_dre_reconciliation.py::TestReceita -v  # uma classe
python3 -m pytest testes/test_event_ledger.py -v             # so event ledger

# Scripts standalone (nao sao pytest)
python3 testes/standalone/test_reconciliation_141air.py
python3 testes/standalone/test_onboarding_backfill.py

# Simulacoes (offline, read-only)
python3 testes/simulacoes/simulate_fresh_backfill_141air.py
python3 testes/simulacoes/simulate_onboarding_141air_jan2026.py --seller 141air
python3 testes/simulacoes/simulate_dre_141air_jan2026.py --seller 141air

# Utilitarios
python3 testes/utils/rebuild_cache.py --seller 141air
```

---

## Log de Sessoes

| Data | Sessao | O que foi feito | Proximos passos |
|------|--------|-----------------|-----------------|
| 2026-03-12 | #1 | Plano criado, inventario, Supabase limpo, infra pytest, testes processor + extrato (104 pass) | DRE reconciliation, Fase 1 (backfill real) |
| 2026-03-12 | #2 | DRE reconciliation: 48 testes com dados reais, formula validada, extrato 289/289 match, 6 regras novas extrato fev/2026. Total: 152 passed | Fase 1 (backfill real), testes ca_queue, daily_sync |
| 2026-03-12 | #3 | Event Ledger: tabela payment_events criada no Supabase, event_ledger.py (record/get/validate), migracao completa de 12 arquivos, testes DRE fev/2026 (46), backfill validation jan+fev (25), testes ledger (40). Total: 263 passed | Estabilizar event ledger, backfill real |
| 2026-03-12 | #4 | Diagnostico (4 agentes) + Fase 0 completa: 3 bugs criticos, status centralizada, queries diretas eliminadas (get_payment_statuses), adjustment events no ledger, float vs Decimal resolvido. Total: 282 passed | Fase 1 (testes integracao event ledger) |
| 2026-03-12 | #5 | Fase 1 completa: 84 testes de integracao — event ledger async (26), processor state machine (25), ca_queue (13), daily_sync (20). Cobertura de orquestracao, paginacao, filtros, idempotencia, status change, subsidio. Total: 366 passed | Fase 2 (backfill real) |
| 2026-03-12 | #6 | Fase 2 completa: backfill real 141air jan/2026 (439 payments, 0 erros, 1441 CA jobs completed). DRE gap = R$ 0,00 em todas as linhas. Resultado operacional R$ 109.555,37 match exato. Release report sync (6 expenses). | Fase 2.5 (validacao extrato completo) |
| 2026-03-12 | #7 | Fase 2.5 completa: validacao extrato 141air jan/2026 — 690/690 linhas cobertas (100%). Classifier: 78 non-order payments. Extrato ingester: 235 gap lines. Migracao payment_id bigint→text. Progressao: 77.5%→79.3%→100%. | Fase 3 (expandir sellers) |
| 2026-03-12 | #8 | Descoberto gap critico: pipeline automatico usa Release Report CSV (ML API) mas validacao manual usa Account Statement CSV (download manual do MP). Formatos incompativeis → step 4 (extrato_ingester) e no-op, step 7 (coverage_checker) mede fonte errada. Documentado em detalhe no PLANO_FUNDACAO.md. | Resolver entrada do Account Statement no sistema |
| 2026-03-13 | #9 | Investigacao completa da API ML/MP: testados 3 endpoints (release_report, bank_report, settlement_report) — NENHUM fornece Account Statement. bank_report e ligeiramente melhor (tem initial_available_balance). Conclusao: API pura nao resolve. Recomendado hibrido: conversao automatica (bank_report, ~95%) + upload manual mensal (100%). Documentado no PLANO_FUNDACAO.md. | Implementar solucao hibrida (endpoint upload + corrigir steps 4/7 do pipeline) |

---

## Decisoes Tomadas

1. **Supabase limpo:** Tabelas transacionais zeradas. Dashboard (faturamento, revenue_lines, goals) preservado.
2. **Pytest para testes novos:** Scripts antigos continuam funcionando como standalone.
3. **Ordem: Fase 2 antes de Fase 1:** Construir rede de seguranca (testes) antes de rodar pipeline em producao.
4. **DRE por competencia:** `date_approved` BRT, nao `date_created`. `order.type=mercadolibre` → 1.1.1, `mercadopago` → 1.1.2.
5. **Exclusoes DRE:** `charged_back/reimbursed` = receita (ML cobriu), `bonificaciones_flex` = nao conta, `financing_fee` = net-neutral.
6. **Gap ML vs processor:** ~R$301 devido a filtros corretos (collector_id + by_admin). Processor exclui para evitar duplicacao.
7. **Event Ledger (v2.0):** Migracao de snapshot mutavel (`payments`) para event sourcing (`payment_events`). Append-only, idempotente via ON CONFLICT DO NOTHING, status derivado de event types.
8. **Fase 0 antes de tudo:** Diagnostico revelou bugs criticos que devem ser corrigidos antes de backfill real ou producao.
9. **Float vs Decimal:** Manter float. Risco sub-centavo para esta escala. Supabase SDK retorna float de qualquer forma. 282 testes validam ao centavo.
10. **Queries diretas a payment_events proibidas:** Sempre usar helpers do `event_ledger.py`. `get_payment_statuses()` para status, `get_dre_summary()` para DRE, `get_processed_payment_ids()` para dedup.

## Decisoes Pendentes

1. **Q1 do dossie:** Boleto Bank of America R$ 93k — real ou erro?
2. **Q4 do dossie:** Pipeline de baixas deve processar mp_expenses categorizados?
3. **Q5 do dossie:** Lookback de 3 dias no daily_sync e suficiente?
4. **CI:** GitHub Actions ou outra plataforma?
5. ~~**Float vs Decimal:**~~ Resolvido — manter float. Risco sub-centavo para esta escala, Supabase SDK retorna float de qualquer forma.
6. **Entrada do Account Statement:** ~~Investigar API~~ **RESOLVIDO (Sessao #9):** API pura NAO fornece Account Statement — nenhum dos 3 endpoints (release_report, bank_report, settlement_report) retorna o formato com saldo corrente e tipos PT-BR. **Decisao: hibrido** — conversao automatica via bank_report (~95% cobertura) + upload manual mensal do extrato real (100%). Falta implementar.

---

*Ultima atualizacao: 2026-03-13 — Sessao #9 (investigacao API ML/MP completa, solucao hibrida documentada)*

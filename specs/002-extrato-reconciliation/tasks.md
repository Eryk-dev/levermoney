# Tasks 002 — Reconciliação Extrato

Cada task tem: ID, descrição, **teste que deve ser escrito primeiro (red)**, arquivo esperado, e tag.

Formato ID: `T-XXX`. Tag mapeia para `@pytest.mark.<tag>` nos testes.

---

## FASE 1 — Eval harness + gate ✅ DONE

### T-001: Implementar `scripts/run_reconciliation.py` ✅
- **Tag:** `@eval_harness`
- **Red test:** `testes/e2e/test_eval_harness_exists.py::test_outputs_coverage_json` — roda o script e valida que emite JSON com chaves `coverage_credits`, `coverage_debits`, `orphan_extrato_count`, `orphan_system_count`, `daily_diff_max`
- **Arquivo:** `scripts/run_reconciliation.py`
- **Aceite:** `python3 scripts/run_reconciliation.py 141air 2026-01` emite JSON válido stdout + exit code 0

### T-002: Teste e2e de reconciliação 141air jan ✅
- **Tag:** `@reconciliation`
- **Red test:** `testes/e2e/test_reconciliation_141air_jan.py::test_cobertura_minima_creditos_debitos`
- **Comportamento esperado:** lê `contract.yml`, chama o script, asserta cobertura ≥ `coverage.credits_min_pct`.
- **No baseline atual FALHA** (56% vs 99,5%). Isso é esperado.
- **Aceite:** teste existe e falha com mensagem clara "coverage 56.04% < 99.5%"

### T-003: Wrapper `scripts/run_reconciliation.sh` ✅
- **Tag:** `@tooling`
- **Red test:** `testes/integration/test_scripts_logging.py::test_run_reconciliation_sh_appends_to_runs_md`
- **Comportamento:** script roda o Python, anexa linha formatada em `docs/reconciliation/RUNS.md` automaticamente, inclui commit SHA
- **Aceite:** executar o .sh adiciona exatamente 1 linha em RUNS.md com formato fixo

### T-004: Wrapper `scripts/run_tests.sh` ✅
- **Tag:** `@tooling`
- **Red test:** `testes/integration/test_scripts_logging.py::test_run_tests_sh_appends_to_test_log_md`
- **Comportamento:** roda pytest, anexa linha em `docs/reconciliation/TEST_LOG.md`
- **Aceite:** mesma logica que T-003

---

## FASE 2 — Derrubar bugs conhecidos (ver ERRORS.md) ✅ DONE

### T-010: Fix ERR-0001 — sinal invertido em transfer_intra ✅
- **Tag:** `@money_sign`
- **Red test:** property-based com Hypothesis em `testes/unit/test_expense_classifier_sign.py`:
  ```
  ∀ expense_type em {deposit, transfer_intra, transferencia_pix_in, transfer_pix, pix_enviado}:
      classify(payment_com_esse_tipo).signed_amount tem sinal consistente com direction
  ```
- **Arquivo alvo:** `app/services/expense_classifier.py`
- **Aceite:** teste passa, RUNS.md mostra bump no coverage de débitos/créditos no R$ 53k identificado

### T-011: Fix ERR-0002 — mp_expenses stale ✅
- **Tag:** `@stale_data`
- **Red test:** `testes/integration/test_stale_mp_expenses.py::test_liberacao_nao_sync_deleted_when_payment_exists`
- **Estratégia:** migration script + regra futura em `daily_sync.py` pra marcar/deletar
- **Aceite:** rows `liberacao_nao_sync`/`qr_pix_nao_sync` cujo payment_id está em `payment_events.sale_approved` são deletadas. Reconciliação ganha ~R$ 7k de matches.

### T-012: Fix ERR-0003 — CashMovement por evento (não por payment NET) ✅
- **Tag:** `@architecture`
- **Red test:** `testes/integration/test_cash_movement_per_event.py`
- **Estratégia:** refatorar `events_to_payment_movements` para emitir 1 CashMovement por evento contributor, com `date = event_date do evento específico`. Assim um payment aprovado em jan+refunded em fev tem 2 movements em datas diferentes.
- **Aceite:** dias anteriormente divergentes por timing de refund passam a bater

### T-013: Fix ERR-0004 — naming mismatch bill_payment/pagamento_conta ✅
- **Tag:** `@classifier`
- **Red test:** `testes/unit/test_category_mapping.py::test_classifier_output_matches_storage_type`
- **Estratégia:** unificar no contract.yml + função de mapping central
- **Aceite:** 22 linhas `pagamento_conta` passam a casar com 22 rows `bill_payment`

---

## FASE 3 — Classificador 100% coberto ✅ DONE

### T-020: Property test classifier_coverage ✅
- **Tag:** `@classifier`
- **Red test:** `testes/unit/test_classifier_coverage.py::test_no_unknown_extrato_types`
- **Estratégia:** carrega os 8 extratos CSV, extrai todos `TRANSACTION_TYPE` distintos, chama `_classify_extrato_line` em cada, asserta que nenhum retorna `("other", "expense", None)`.
- **Aceite:** 100% dos tipos reais tem regra explícita

---

## FASE 4 — Golden snapshots

### T-030: Curar e snapshot 30 payments diversos
- **Tag:** `@golden`
- **Red test:** `testes/golden/test_processor_snapshots.py::test_each_sample_payment_produces_expected_events`
- **Estratégia:** 30 payments JSON em `testes/golden/samples/`, para cada um snapshot dos events + payloads CA em `testes/golden/expected/`. Toolchain que regenera com flag `--update`.
- **Aceite:** qualquer mudança em `processor.py` que altere o output quebra teste e exige review explícito do snapshot

---

## FASE 5 — Invariantes ambientais

### T-040: Assertivas ambientais em daily_sync
- **Tag:** `@invariants`
- **Red test:** `testes/integration/test_ambient_invariants.py`
- **Estratégia:** flag `ASSERT_INVARIANTS=true` em dev, roda I-1, I-3, I-4, I-5 no fim de `daily_sync.sync_seller_payments`. Se falhar, aborta com stack trace.
- **Aceite:** em dev, bug que viola invariante quebra antes do commit

---

## FASE 6 — Scale out

### T-050: Reconciliação para outros sellers
- **Tag:** `@reconciliation`
- **Red test:** `testes/e2e/test_reconciliation_all_sellers.py` parametrizado
- **Aceite:** 141air, net-air, netparts-sp, easy-utilidades, easypeasy todos ≥ 99,5% em jan E fev

### T-051: Promover gate a CI bloqueante
- **Tag:** `@ci`
- **Red test:** —
- **Aceite:** `.github/workflows/ci.yml` roda `pytest -m reconciliation` e falha se coverage < threshold

---

## Nota sobre ordem

- T-001, T-002, T-003, T-004 **primeiro** (sem eval harness, tudo mais é sub-ótimo)
- T-010, T-011, T-012, T-013 em paralelo assim que eval harness rodar
- T-020 a qualquer momento
- T-030, T-040, T-050, T-051 depois de bater 99,5% em 141air jan

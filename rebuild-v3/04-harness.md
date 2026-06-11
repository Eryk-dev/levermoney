# 04 — O Juiz + Harness real-code dry-run

A peça que NUNCA existiu: reconciliação de valor automatizada. Agora existe, em 2 níveis.

## Nível 1 — O Juiz (`testes/judge_caixa_jan2026.py`)

Standalone, stdlib só, sem DB/env. Lê extratos CSV + cache de payments. Faz:
- **[A] ÂNCORA:** `INITIAL + Σnet == FINAL` + saldo corrido (`PARTIAL_BALANCE`) linha a linha.
  Prova que o extrato é verdade confiável. (✓ ao centavo nos 10 extratos.)
- **[B] BUCKETS:** classifica cada linha COMO O SISTEMA FAZ (importa as regras REAIS de
  `extrato_ingester.EXTRATO_CLASSIFICATION_RULES`) → % vendas/non-venda/OTHER/bug.
- **[C] RECON DE VENDAS:** liberação do extrato vs cálculo do processor por payment.

Rodar: `python3 testes/judge_caixa_jan2026.py`

## Nível 2 — Harness real-code dry-run (`testes/harness/`)

Roda as FUNÇÕES REAIS do app (não reimplementação) contra dados reais, capturando o que SERIA
lançado no CA. **É o que "testa o app de verdade".**

### Arquivos
- `dryrun.py` — core: `FakeDB` (read-only, stateful cross-month), captura dos `enqueue_*`,
  patches, `run_seller_month(slug, payments, state=...)`.
- `run.py` — CLI: roda + reconcilia. Seções [A] âncora, [C] recon vendas lifecycle,
  [D] caixa date-aware (só eventos com vencimento no mês), [E] full caixa.
- `test_rules.py` — testa as regras de classificação (Fase 7).
- `fetch_all.py` — busca payments via ML API (read-only) p/ (slug, mês), salva em
  `testes/cache_{mon}2026/`.

### Segurança (triplo cinto — ZERO escrita no CA)
1. `ca_queue.enqueue_*` → monkeypatch que CAPTURA em memória (não chama `ca_api`).
2. `ca_api` write → patchado pra RAISE (falha hard se algo tentar postar).
3. `get_db` → `FakeDB` que só aceita `.select()` (leitura) e captura upserts in-memory.
Nenhum `CaWorker`. Supabase só leitura.

### Como funciona o capture
Patcheia `processor.get_db`/`get_seller_config`/`get_missing_ca_launch_fields` (config CA
sintética pra todo seller exercitar a lógica completa), `ml_api.get_order`→None, e os
`ca_queue.enqueue_*`. Roda `process_payment_webhook` (order) ou `classify_non_order_payment`
(non-order) REAL. Cada evento capturado: tipo, payment_id, valor, competência, vencimento,
categoria. Net de caixa = Σ sinal×valor (receita +, comissão/frete/estorno −, etc.).

### FakeDB stateful (cross-month)
Captura upserts de payments (pid→status) e devolve no `select(payments).eq(ml_payment_id)`.
Passe o MESMO `state` dict ao rodar meses em ordem → payment processado em jan não re-cria
receita em fev. **Limite:** caches são snapshot (status final), então um refunded aparece em
jan+fev caches; cross-month EXATO precisa de dado event-time (status ao longo do tempo).

### Rodar
```
python3 -m testes.harness.run 141air jan,fev,mar   # reconcilia mês(es): [A][C][D][E]
python3 -m testes.harness.run 141air timeline      # cada payment 1x + resíduo de valor + caixa/mês
python3 -m testes.harness.run 141air dre           # DRE por competência (Fase 6)
python3 -m testes.harness.run 141air ponte         # pontes caixa↔DRE, DRE↔painel ML (Fase 5)
python3 -m testes.harness.test_rules               # regras de classificação (Fase 7)
python3 -m testes.harness.test_baixas_extrato      # baixa extrato-dirigida (Fase 3-full)
python3 -m testes.harness.fetch_all                # busca payments faltantes via API
```

### O que cada seção/modo mede
- **[A]** extrato fecha sozinho? (âncora)
- **[C]** vendas: Σ CA capturado vs Σ extrato por ref (lifecycle completo). Separa approved/refunded.
- **[D]** caixa date-aware: só eventos com vencimento no mês (estornos cross-month caem fora = spill).
- **[E]** full caixa: decompõe TODAS as linhas. **OTHER=0 = cobertura 100%** (confiável). O número
  de "vendas" em [E] tem refino pendente (double-count estorno×refund-debit) — use o modo **timeline**.
- **timeline** (recomendado): processa cada payment UMA vez (união dedupada) → bucketa por mês de
  caixa. Mostra **resíduo de VALOR date-independent** (isola erro de valor do desalinho de data) e
  caixa por mês. Foi aqui que se mediu o erro real ~0,1% (vs boundary).
- **dre / ponte:** Fase 6 (DRE competência) e Fase 5 (pontes) — ver doc 02 e 05.

### Fase 3-full — baixa extrato-dirigida (`app/services/baixas_extrato.py`)
Lógica pura `plan_baixas_from_extrato(extrato_lines, parcelas_abertas)`: casa crédito do extrato a
parcela CA por payment_id, planeja baixa com data+valor REAIS do extrato (não a promessa), trata
liberação parcelada (N baixas), ajuste de valor, e cancela-antes-de-liberar (nunca_baixou).
Testado: `test_baixas_extrato.py` 4 casos ALL PASS. Wiring ao CA real = produção.

# Decisions — Reconciliação Extrato (ADR log)

Toda decisão arquitetural que afeta reconciliação entra aqui. Formato ADR.
**Append-only**: decisões antigas não são editadas; se mudarem, cria-se novo ADR que supersede.

---

## ADR-0001 — Usar payment_id (não external_reference) para match de bill_payment
**Data:** 2026-04-16
**Status:** Aceito

**Contexto:**
O extrato do ML usa `reference_id` como chave primária de cada linha. Para linhas de `bill_payment` (Pagamento de conta), esse `reference_id` é o **payment_id** do MP (ex: `139636302479`). Já a tabela `mp_expenses` guarda esse mesmo valor em `payment_id` (string), enquanto `external_reference` contém o boleto/cobrança ID (ex: `2529988003`), que é diferente.

**Decisão:** Reconciliação sempre faz match por `mp_expenses.payment_id` quando for non-payment line, ou por `payment_events.ml_payment_id` quando for payment line. **NUNCA** por `external_reference`.

**Alternativas rejeitadas:**
- `external_reference`: testado em 22 linhas de bill_payment, 0% de match (R$ 116k "perdido").

**Consequência:** Matcher fica determinístico. Custa uma linha de código a mais no indexer (strip suffix `:dd`/`:rd`/etc de `payment_id`).

**Referência:** `specs/002-extrato-reconciliation/contracts/reconciliation.yml#id_mapping`

---

## ADR-0002 — mp_expenses "nao_sync" são stale quando payment existe depois
**Data:** 2026-04-16
**Status:** Aceito

**Contexto:**
`extrato_ingester.py` tem regra: se `reference_id` de uma linha de "Liberação de dinheiro" não está em `payment_events`, ingere como `mp_expense` com `expense_type='liberacao_nao_sync'`. Isso cobre o caso em que a ML search API silenciosamente derrubou o payment do resultado. **Porém**, se depois o payment for ingerido via backfill, a row `liberacao_nao_sync` vira duplicata — a informação correta passa a estar em `payment_events`.

**Decisão:** Qualquer row em `mp_expenses` com `expense_type` em `{liberacao_nao_sync, qr_pix_nao_sync, pix_nao_sync, dinheiro_recebido}` cujo `payment_id` existe em `payment_events` com evento `sale_approved` é **stale** e deve ser deletada (ou marcada `status='superseded'`).

**Alternativas rejeitadas:**
- Ignorar em reconciliação (sem limpar DB): adiciona complexidade no matcher + CA jobs eventualmente são criados duplicados em fluxos futuros.
- Prevenir o problema upstream (não ingerir se tem chance de virar payment depois): impossível sem conhecer o futuro.

**Consequência:** Task T-011 implementa o script de cleanup + regra em daily_sync pra prevenir reaparecimento. Reconciliação de 141air jan ganha ~R$ 7,4k em matches esperados.

**Referência:** `ERRORS.md#ERR-0002`

---

## ADR-0003 — CashMovement por evento, não por payment NET
**Data:** 2026-04-16
**Status:** Aceito

**Contexto:**
Na primeira iteração do reconciliador, representei cada payment como 1 CashMovement com amount = NET (sum of cash events signed). Quebrou em casos de timing: payment aprovado jan/liberado jan/refunded fev — extrato mostra só a liberação em jan (+valor); nosso NET já desconta o refund (+valor - refund), não casam.

**Decisão:** Cada evento contributor vira 1 CashMovement independente com sua própria data (event_date). O matcher compara linha-a-linha do extrato com movement-by-movement, não soma por payment.

**Alternativas rejeitadas:**
- NET por payment: perde granularidade de datas (falha I-3 daily totals).
- Forçar todos eventos na mesma data do sale_approved: ignora o que o extrato mostra (falha I-4 per-payment invariant).

**Consequência:** Task T-012 refatora `events_to_payment_movements`. Resolve classe inteira de amount_diffs.

**Referência:** `ERRORS.md#ERR-0003`

---

## ADR-0004 — SDD no nível do sistema, TDD no nível de unidade
**Data:** 2026-04-16
**Status:** Aceito

**Contexto:**
Usuário pediu TDD ou SDD. TDD sozinho não resolve loop "corrige → diverge na prova real" porque o problema é ausência de especificação multi-sistema, não falta de disciplina de teste.

**Decisão:**
- **SDD** no nível do sistema: `spec.md` + `contract.yml` são source of truth. Mudanças no que é "correto" começam lá.
- **TDD** no nível de unidade: cada task abre com teste falhando que verifica 1 ponto do spec, depois implementação.
- **Eval harness** como single-number gate: cada PR sobe ou mantém a % de reconciliação.
- **Spec-Kit** (`.specify/`) é a ferramenta (já instalada).

**Consequência:** Framework registrado em `plan.md`. Toda decisão futura respeita esse loop.

**Referência:** `plan.md`

---

## Template (copiar pra nova decisão)
```
## ADR-0005 — Direção de cancelamentos vem do sinal do CSV, não do pattern
**Data:** 2026-04-16
**Status:** Aceito

**Contexto:**
`EXTRATO_CLASSIFICATION_RULES` associa `direction` a cada pattern. Para a maioria das categorias a direção é intrínseca (venda → income, comissão → expense). Mas `Liberação de dinheiro cancelada` e `Dinheiro recebido cancelado` são *reversões* — o sinal no extrato indica se estão cancelando uma entrada (então debitam, -) ou uma saída (então creditam, +). Em fev/2026 o extrato 141air trouxe `+46,90` para uma `liberacao_cancelada`, mas a regra tinha `direction="expense"` hardcoded → `signed_amount = -46,90` (sinal invertido).

**Decisão:** Introduzir constante `_SIGN_DRIVEN_EXPENSE_TYPES` em `extrato_ingester.py`. Durante a primeira passagem de classificação, se o expense_type estiver nessa lista, a `direction` é sobrescrita pelo sinal de `tx["amount"]` (positivo → income, negativo → expense).

**Alternativas rejeitadas:**
- Criar dois patterns separados (`liberacao_cancelada_income` / `liberacao_cancelada_expense`): quebra a simetria com a categoria canônica usada pelo matcher.
- Deixar direction=None no rule + inferir sempre do sinal CSV: mudaria contrato de classificação para todos os tipos, risco de regressão.

**Consequência:** Tipos com semântica de reversão precisam entrar em `_SIGN_DRIVEN_EXPENSE_TYPES` explicitamente. Por ora: `liberacao_cancelada`, `dinheiro_recebido_cancelado`. Adicionar outros conforme novos sinais apareçam.

**Referência:** `ERRORS.md#ERR-0018`, `app/services/extrato_ingester.py`

---

## ADR-0006 — Step (c) do extrato_ingester pula por composite key, não por ref plain
**Data:** 2026-04-16
**Status:** Aceito (refina ADR-0001)

**Contexto:**
Step (c) de `ingest_extrato_for_seller()` pulava linha se o `ref_id` já tinha algum `expense_captured` event no DB (`ref_id in expense_ids_in_db`). Problema: pagamentos com múltiplos eventos complementares (dispute groups, reversals) têm linhas novas do extrato em meses diferentes sob o mesmo ref. O skip por ref plain pulava eventos legítimos como "already_covered" quando só a composite key (`ref:type`) seria real duplicata.

**Decisão:** Mantém step (c) (skip por ref plain) como default, mas introduz `_COMPLEMENTARY_EXPENSE_TYPES` (reembolso_disputa, reembolso_generico, reembolso_pix_enviado, entrada_dinheiro, dinheiro_retido, liberacao_cancelada, dinheiro_recebido_cancelado, debito_envio_ml, bonus_envio, debito_troca, debito_divida_disputa). Se o expense_type atual está nessa lista, step (c) não pula — step (a) (composite key) já garante deduplicação correta.

**Alternativas rejeitadas:**
- Remover step (c) completamente: perderia dedup API↔extrato para tipos não-complementares (arrisca duplicata).
- Fazer step (c) sempre usar composite key: equivalente a eliminar step (a); perde semântica da intent original.

**Consequência:** Tipos novos de cash event complementar (disputas inventadas por ML, novos tipos de reversão) precisam entrar em `_COMPLEMENTARY_EXPENSE_TYPES`. Impactou: `reconciliation 141air fev/2026` ganhou ~13 linhas adicionais de reembolso_disputa / dinheiro_retido / debito_envio_ml.

**Referência:** `ERRORS.md#ERR-0016`, `app/services/extrato_ingester.py`

---

## ADR-0007 — Cashback do classifier é condicional, não sempre dropado nem sempre traduzido
**Data:** 2026-04-16
**Status:** Aceito (supersede mapping estático `cashback → bonus_envio` para os casos de conflito)

**Contexto:**
`expense_classifier.py` emite `expense_type='cashback'` para qualquer `money_transfer` com branch=Cashback (Bonificação Flex, Ressarcimento Full, Ressarcimento). Mas o extrato lista o mesmo evento sob tx_types diferentes dependendo do caso: "Bônus por envio" (→ `bonus_envio`), "Dinheiro recebido Pagamento pelo Programa de Proteção Mercado Envios Full" (→ `dinheiro_recebido`), "Liberação de dinheiro" (→ `liberacao`), ou pode simplesmente não aparecer (MP-internal). O tradutor estático `cashback → bonus_envio` só resolve 1 dos 4 casos e produz orphans nos outros 3.

**Decisão:** `filter_stale_mp_expenses()` recebe `extrato_pids` e trata cashback em 3 casos:
1. Existe outro mp_expense não-cashback com mesmo (ref, amount) → drop (duplicata cross-source).
2. `ref` não aparece no extrato → drop (evento MP-interno, sem cash real).
3. Caso contrário, mantém → matcher pass 2 faz ref+amount e casa com qualquer categoria de extrato equivalente.

**Alternativas rejeitadas:**
- Manter `_RECON_ALWAYS_DROP_EXPENSE_TYPES = {cashback}`: testado, drop total cria 4 orphan_extrato (casos 3) = regressão.
- Expandir tradutor estático (cashback → dinheiro_recebido OU liberacao OU bonus_envio): requer context-lookup que o `_expense_type_to_category` não tem.
- Deletar o cashback da mp_expenses view: quebra histórico e relatórios fora da reconciliação.

**Consequência:** Reconciliação confia no matcher pass 2 para casos ambíguos. Tipos API-only que nunca surgem no extrato são silenciosamente dropados; tipos cross-source evitam double count.

**Referência:** `ERRORS.md#ERR-0015`, `app/services/reconciliation.py::filter_stale_mp_expenses`

---

## ADR-0008 — Release events de bpp_refunded são suprimidos quando extrato carrega como entrada_dinheiro
**Data:** 2026-04-16
**Status:** Aceito

**Contexto:**
Payments com `status_detail='bpp_refunded'` tipicamente têm o trio sale_approved + fee_charged + shipping_charged no event ledger (processor criou quando o payment ainda estava "approved") e depois refund_created (quando MP executou o bpp). No extrato, MP compensa o seller via linha "Entrada de dinheiro" (Programa de Proteção Mercado Envios Full), não via "Liberação de dinheiro" — ou seja, o release group não tem contraparte no extrato. Resultado: release movement fica orphan_system.

**Decisão:** `align_refund_created_with_extrato()` ganha case 4: quando um pid tem release movement com `status_detail ∈ {bpp_refunded, refunded}` E seu único footprint no extrato é `entrada_dinheiro`, suprime release + qualquer refund_debit/refund_fee do mesmo pid. O mp_expense `entrada_dinheiro` (ingerido por extrato_ingester) carrega o caixa real.

**Alternativas rejeitadas:**
- Não emitir release group em `events_to_payment_movements` para bpp_refunded: quebra casos em que o bpp foi revertido depois (extrato teria tanto entrada_dinheiro quanto liberacao separadamente).
- Marcar mp_expense entrada_dinheiro como stale pra pids com sale_approved: inverteria quem é source of truth (extrato é ground truth nesse caso, não event ledger).

**Consequência:** Pids com status_detail específico + padrão "só entrada_dinheiro no extrato" são detectados automaticamente. Se MP vier a usar outros status_detail para o mesmo padrão, adicionar ao set `{"bpp_refunded", "refunded"}` no check.

**Referência:** `ERRORS.md#ERR-0014`, `app/services/reconciliation.py::align_refund_created_with_extrato`

---

## ADR-NNNN — Título curto
**Data:** YYYY-MM-DD
**Status:** Aceito | Superseded por ADR-NNNN | Rejeitado

**Contexto:** o problema, restrições, fatos relevantes.
**Decisão:** a escolha feita.
**Alternativas rejeitadas:** e por quê.
**Consequência:** o que muda no código/processo.
**Referência:** arquivos / issues / ERR-NNNN.
```

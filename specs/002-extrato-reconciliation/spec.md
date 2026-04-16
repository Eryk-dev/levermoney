# Spec 002 — Reconciliação Extrato ↔ Sistema

**Status:** Draft
**Owner:** Eryk
**Criado:** 2026-04-16
**Depende de:** `app/services/processor.py`, `app/services/extrato_ingester.py`, `app/services/expense_classifier.py`

---

## 1. Problema

O sistema recebe 3 fontes de dados financeiros:

1. **ML API** (orders/payments) → processado por `processor.py` → `payment_events`
2. **ML API** (non-orders: boletos, PIX, etc.) → processado por `expense_classifier.py` → `mp_expenses`
3. **ML account_statement CSV** (extrato) → processado por `extrato_ingester.py` → `mp_expenses` (gaps)

O sistema emite entradas no **Conta Azul** derivadas de (1)+(2)+(3). Hoje não temos garantia de que o caixa que cai na Conta Azul bate com o que o ML reporta no extrato. Isso é **inegociável**: a contabilidade do seller precisa bater 100% com o extrato oficial do MP.

## 2. Objetivo

Construir um sistema de reconciliação que, para cada seller/período, **prove** que:

> Toda linha do extrato tem exatamente um lançamento contrapartida no nosso sistema, e a soma diária de créditos e débitos bate com o extrato dentro das tolerâncias definidas.

## 3. Invariantes (declaração formal)

Dado um seller S e um período [D₁, D₂]:

### I-1. Balance invariant (saldo)
```
initial_balance + Σ(credits) - Σ(debits) = final_balance
```
O extrato do ML tem essa identidade embutida; nosso sistema deve preservar.

### I-2. Coverage invariant (cobertura)
```
∀ linha L ∈ extrato(S, D₁, D₂):
    ∃! entrada E ∈ (payment_events ∪ mp_expenses)  tal que  match(L, E)
```
Toda linha do extrato tem uma e só uma contrapartida no sistema.

### I-3. Daily totals invariant (totais diários)
```
∀ dia d ∈ [D₁, D₂]:
    Σ(credits_extrato[d]) = Σ(credits_sistema[d])  ±  TOLERANCE_DAILY
    Σ(debits_extrato[d])  = Σ(debits_sistema[d])   ±  TOLERANCE_DAILY
```

### I-4. Per-payment invariant (NET do payment)
```
∀ payment_id P referenciado no extrato:
    Σ(extrato_lines[P])  =  net_cash(payment_events[P])  ±  TOLERANCE_LINE
```
Onde `net_cash = Σ signed_amount para event_type ∈ CASH_EVENT_TYPES`.

### I-5. Sign convention invariant (sinal)
```
∀ evento E com direction='income' ou 'transfer_in':   signed_amount > 0
∀ evento E com direction='expense' ou 'transfer_out': signed_amount < 0
```
Nunca pode haver signed_amount com sinal contrário à direção semântica.

### I-6. Idempotency invariant (reprocessamento)
```
reconcile(S, D₁, D₂)  =  reconcile(S, D₁, D₂)  [chamado N vezes]
```
Reprocessar o mesmo período produz o mesmo estado final (via ON CONFLICT DO NOTHING no event_ledger + upsert em mp_expenses).

### I-7. Classifier coverage invariant (classificador)
```
∀ transaction_type T encontrado nos extratos reais:
    _classify_extrato_line(T) != ("other", "expense", None)
```
Nenhum `transaction_type` cai no fallback genérico. Todo tipo conhecido é explicitamente mapeado.

### I-8. Stale mp_expenses invariant (dados antigos)
```
∀ mp_expense E:
    se E.payment_id ∈ payment_events com sale_approved:
        E.status = 'superseded'  OU  E.expense_type ∉ { liberacao_nao_sync, qr_pix_nao_sync, pix_nao_sync, dinheiro_recebido }
```
Rows `*_nao_sync` só existem pra payments que **não** estão em `payment_events`. Se depois o payment é ingerido, o mp_expense vira stale e precisa ser marcado/deletado.

## 4. Contratos (IDs e campos)

### 4.1. ID mapping
| Origem | Campo | Destino | Campo |
|---|---|---|---|
| extrato CSV | `reference_id` (payment line) | `payment_events` | `ml_payment_id` |
| extrato CSV | `reference_id` (non-payment) | `mp_expenses` | `payment_id` (string, possivelmente com sufixo `:dr`,`:dd`,`:rd`,`:rg`,`:fm`) |
| extrato CSV | `reference_id` | **NÃO** usar | `mp_expenses.external_reference` (esse é boleto_id, não serve pra match) |

### 4.2. Mapeamento extrato_type ↔ mp_expense.expense_type
Definido em `contracts/reconciliation.yml` seção `classifier_coverage.known_types`.

### 4.3. Cash event types
Eventos que contribuem para `net_cash` de um payment:
```
sale_approved, fee_charged, shipping_charged,
refund_created, refund_fee, refund_shipping,
subsidy_credited
```
(definido em `app/services/processor.py` como constante)

## 5. Tolerâncias

| Parâmetro | Valor | Justificativa |
|---|---|---|
| `TOLERANCE_LINE` | R$ 0,02 | Arredondamento de centavo em divisões proporcionais |
| `TOLERANCE_DAILY` | R$ 0,00 | Dia fechado é inegociável — nenhum centavo pode "sumir" |
| `TOLERANCE_PERIOD` | R$ 0,00 | Período fechado idem |
| `COVERAGE_TARGET_CREDITS` | ≥ 99,5% | Gate de CI |
| `COVERAGE_TARGET_DEBITS` | ≥ 99,5% | Gate de CI |

## 6. Escopo

### Dentro
- Reconciliação de 141air para janeiro/2026 (piloto)
- Framework reusável pra outros sellers/períodos
- Sistema de logging (RUNS.md, TEST_LOG.md) automatizado via scripts
- Correção de bugs descobertos (ver `docs/reconciliation/ERRORS.md`)

### Fora (por enquanto)
- Reconciliação contra o que está de fato posted na Conta Azul (isso é outra camada — requer pull da CA API)
- Reconciliação em tempo real (será batch daily)
- Outros sellers (virão após 141air bater 99,5%)

## 7. Critério de aceitação

```
Dado seller=141air, período=2026-01-01 a 2026-01-31
Quando rodar `python3 scripts/run_reconciliation.py 141air 2026-01`
Então:
    - coverage_credits >= 99,5%
    - coverage_debits  >= 99,5%
    - daily_diff_max   <= R$ 0,00 (dia fechado é inegociável — nenhum centavo pode sumir)
    - orphan_extrato_count <= 3 linhas (com allowlist documentada)
    - invariantes I-1, I-3, I-4, I-5 passam em 100% dos casos
```

Quando os 4 critérios atingem verde, promover o teste para **gate bloqueante de CI**.

## 8. Dependências

- `docs/reconciliation/DECISIONS.md` — registra toda decisão arquitetural
- `docs/reconciliation/ERRORS.md` — catalogo de bugs encontrados e fixes
- `docs/reconciliation/RUNS.md` — log histórico de cada reconciliação
- `docs/reconciliation/TEST_LOG.md` — log histórico de cada pytest run
- `specs/002-extrato-reconciliation/contracts/reconciliation.yml` — parâmetros travados
- `specs/002-extrato-reconciliation/tasks.md` — decomposição em tasks

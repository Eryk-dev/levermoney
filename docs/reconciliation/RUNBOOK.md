# Runbook — Validação de um período de reconciliação

> **Objetivo:** guia operacional passo a passo para validar (seller, período) contra o extrato MP até bater 100% em todos os gates — e gerar DRE + e2e + docs como entregáveis.
>
> **Contexto:** spec 002 (`specs/002-extrato-reconciliation/`). Gate definido em `contracts/reconciliation.yml`: `coverage_credits=100%`, `coverage_debits=100%`, `orphan_extrato_count=0`, `orphan_system_count=0`, `daily_diff_max=R$ 0,00`, `divergent_days=0`.
>
> **Escopo comprovado:** 141air jan/fev/mar 2026 (20 ERRs catalogados, 15/15 e2e passing).

---

## 1. Pré-requisitos

| Requisito | Como verificar |
|---|---|
| `.env` com `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` | `python3 -c "from app.db.supabase import get_db; get_db().table('payment_events').select('id').limit(1).execute()"` |
| CSV do extrato MP salvo em `testes/data/extratos/` | `ls testes/data/extratos/` |
| Formato do CSV: header `INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE` na linha 1 | `head -1 "testes/data/extratos/<arquivo>.csv"` |
| Deps Python instaladas (FastAPI, supabase-py, pytest, pyyaml) | `python3 -c "import yaml, supabase, pytest"` |

---

## 2. Visão geral do fluxo

```
                 ┌────────────────────┐
                 │  extrato CSV (MP)  │ ← fonte de verdade do caixa
                 └─────────┬──────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
  ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
  │ payment_    │ │ mp_expenses  │ │ match engine │
  │ events      │ │ (view)       │ │ (reconcile)  │
  │             │ │              │ │              │
  │ processor   │ │ extrato_     │ │ 4 passes:    │
  │ (ML sales)  │ │ ingester     │ │ ref+cat+amt  │
  │             │ │ (extrato gap)│ │ ref+amt      │
  │ expense_    │ │              │ │ ref+sign     │
  │ classifier  │ │              │ │ cat+amt±1d   │
  │ (non-order) │ │              │ │              │
  └─────────────┘ └──────────────┘ └──────┬───────┘
                                          │
                                          ▼
                                  ┌──────────────┐
                                  │ ReconciliationMetrics │
                                  │ (cov, orphans, diffs) │
                                  └───────┬──────┘
                                          │
                 ┌────────────────────────┼────────────────────────┐
                 ▼                        ▼                        ▼
          ┌──────────┐            ┌──────────────┐          ┌─────────────┐
          │ RUNS.md  │            │ DRE simulado │          │ e2e gate    │
          │ (log)    │            │ (receita,    │          │ (CI block)  │
          │          │            │  despesa,    │          │             │
          │          │            │  resultado)  │          │             │
          └──────────┘            └──────────────┘          └─────────────┘
```

**Regra mestra:** o extrato é source of truth do caixa. Toda divergência se resolve alinhando o sistema ao extrato, não o contrário.

---

## 3. Passo a passo — validar um período novo

### Passo 1 — Registrar o mapping

Editar `app/services/reconciliation.py`:

```python
_PERIOD_TO_MES = {
    "2026-01": "janeiro",
    "2026-02": "fevereiro",
    "2026-03": "março",
    "2026-04": "abril",   # ← adicionar
}

_SELLER_TO_FILENAME = {
    # ...
    ("141air", "abril"): "extrato abril 141air.csv",  # ← adicionar
}
```

Sem o mapping, `run_reconciliation.py` aborta com mensagem clara indicando o que faltou. Fail-fast intencional (ADR-0001 semântica: match exato, nunca silent fallback).

### Passo 2 — Rodar baseline (sem ingerir nada ainda)

```bash
python3 scripts/run_reconciliation.py <seller> <period>
# ex: python3 scripts/run_reconciliation.py 141air 2026-04
```

Saída: JSON com métricas. Interessante:
- `coverage_credits` / `coverage_debits`: % do volume de extrato que casou.
- `orphan_extrato_count`: linhas do extrato sem contraparte no sistema (faltam mp_expenses ou events).
- `orphan_system_count`: sys movs sem linha no extrato (mp_expense ou event que não corresponde a nada do caixa).
- `amount_diff_count`: pares com mesma ref+cat mas amounts divergentes.
- `daily_diff_max`: maior diferença diária em reais.

Se já vier 100% — sorte. Normalmente vem 80-95% na primeira rodada.

### Passo 3 — Ingerir o extrato (se ainda não foi)

O `extrato_ingester` pega linhas do extrato que **não** estão cobertas pelos payments da API (bill_payment, DIFAL, disputas, refunds específicos, etc.) e cria rows em `mp_expenses`. Se o período é novo, ele nunca rodou.

Criar script one-shot (template em `scripts/ingest_mar_141air.py`):

```python
#!/usr/bin/env python3
import asyncio, sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from app.services.extrato_ingester import ingest_extrato_from_csv

async def main():
    csv_path = PROJECT_ROOT / "testes" / "data" / "extratos" / "extrato abril 141air.csv"
    result = await ingest_extrato_from_csv(
        "141air", csv_path.read_text(encoding="utf-8-sig"), "2026-04",
    )
    print(result)

asyncio.run(main())
```

Rodar e observar `newly_ingested` + `by_type`. Se `newly_ingested == 0` e o período é novo, o pipeline do daily sync já fez isso automaticamente (verificar). Se > 0, rodar `run_reconciliation.py` de novo para ver o novo gap.

### Passo 4 — Diagnosticar orphans

```bash
python3 scripts/debug_orphans.py <seller> <period>
```

Saída: cada linha orphan do extrato e do sistema, com amount/ref/cat/meta. Para cada orphan extrato, lista candidatos sys movs com mesmo ref (útil pra ver se é mismatch de categoria ou amount).

**Leitura dos candidatos:**
- `src=payment_events` → vem do ledger de eventos (sale_approved, fee_charged, refund_*)
- `src=mp_expenses` → vem de linha ingerida do extrato ou classificada por expense_classifier
- `meta.group='release'/'refund_debit'/'refund_fee'` → grupo derivado do ledger
- `meta.status_detail` → status_detail do payment (bpp_refunded, refunded, accredited, etc.)

### Passo 5 — Catalogar bug novo (ERR-NNNN)

Para cada padrão não coberto, abrir entry em `docs/reconciliation/ERRORS.md` usando o template no fim do arquivo. Estrutura obrigatória:

```
## ERR-NNNN — Título curto
**Descoberto:** YYYY-MM-DD, contexto breve
**Status:** Aberto | Resolvido em YYYY-MM-DD (commit XXXX)

**Sintoma:** o que o debug_orphans mostra
**Causa raiz:** por que está acontecendo (hipótese testada)
**Fix:** como corrigir (com pseudo-código)
**Lição:** padrão genérico pra evitar no futuro
**Arquivos envolvidos:** paths
```

Numerar sequencial (último commitado + 1). Nunca reaproveitar número. Bug resolvido NÃO é apagado; fica como lição.

### Passo 6 — Escrever teste red-green

Antes de alterar engine, criar teste em `testes/integration/` ou `testes/unit/` que reproduz o bug (red). Pode importar do módulo direto (sem subprocess). Padrão:

```python
def test_err_NNNN_symptom_reproduces():
    """Dado <contexto real>, o matcher produz <orphan>.
    Esperado: <comportamento corrigido>."""
    ext_movs = [CashMovement(..., amount=Decimal("X"))]
    sys_movs = [CashMovement(..., amount=Decimal("Y"))]
    results = match_movements(ext_movs, sys_movs, Decimal("0.02"))
    orphan_count = sum(1 for r in results if r.status == "orphan_extrato")
    assert orphan_count == 0  # fails red, passes after fix
```

### Passo 7 — Aplicar fix

Onde fixar:

| Tipo de bug | Arquivo | Função |
|---|---|---|
| Classifier rule faltando (novo tx_type) | `app/services/extrato_ingester.py` | `EXTRATO_CLASSIFICATION_RULES` (ordem matters!) |
| Sign invertido em reversão | `app/services/extrato_ingester.py` | `_SIGN_DRIVEN_EXPENSE_TYPES` |
| Step (c) pulando linha que deveria ingerir | `app/services/extrato_ingester.py` | `_COMPLEMENTARY_EXPENSE_TYPES` |
| mp_expense duplicada / API-only | `app/services/reconciliation.py` | `filter_stale_mp_expenses` |
| Release ou refund amount divergente | `app/services/reconciliation.py` | `align_refund_created_with_extrato` (5 cases) |
| Tolerância por categoria | `app/services/reconciliation.py` | `PCT_TOLERANCE_BY_CATEGORY` |
| Match com ref_id diferente | `app/services/reconciliation.py` | `match_movements` Pass 4 |

### Passo 8 — Cleanup + re-ingest quando necessário

Se o fix muda regra de classificação ou sinal, linhas antigas ingeridas com a regra errada **permanecem** na view `mp_expenses`. Precisa:

1. Identificar rows stale (ex: `143104571692:lc` com sinal errado, `146365338433:pe:2` mal classificado).
2. Deletar do `payment_events` (mp_expenses é uma VIEW, migration 009).
3. Re-rodar `ingest_extrato_from_csv` → ingere com regra nova.

Template em `scripts/cleanup_feb_stale.py` (adaptar ref e seller).

### Passo 9 — Verificar 100%

```bash
./scripts/run_reconciliation.sh <seller> <period>
```

Esse wrapper:
- Roda `run_reconciliation.py`
- Anexa 1 bloco em `docs/reconciliation/RUNS.md`
- Imprime métricas resumidas

Se algum gate falhar (coverage < 100%, orphans > 0, daily_diff > 0), voltar ao passo 4.

### Passo 10 — Regression check nos meses anteriores

Uma mudança que faz mar passar pode quebrar jan/fev. Sempre:

```bash
python3 scripts/run_reconciliation.py <seller> 2026-01
python3 scripts/run_reconciliation.py <seller> 2026-02
python3 scripts/run_reconciliation.py <seller> 2026-03
# todos devem continuar 100/100/0/0/R$0
```

E rodar a suite completa:

```bash
python3 -m pytest testes/e2e/ -v -m "integration or e2e or reconciliation"
```

Expectativa: 15/15 pass (5 gates × 3 meses para 141air). Novos meses/sellers: n × 5.

### Passo 11 — Gerar DRE

```bash
python3 scripts/simulate_dre.py <seller> <period>
```

Saída stdout: tabela de RECEITA BRUTA, DEDUÇÕES, OUTRAS RECEITAS, TRIBUTOS, DESPESAS VENDAS, DESPESAS FINANCEIRAS + subtotais + receita líquida + resultado operacional.

JSON salvo em `docs/reconciliation/dre_<seller>_<period>.json`.

A DRE deriva do mesmo pipeline (payment_events + mp_expenses) que a reconciliação — se o período está 100%, o DRE reflete exatamente o que seria lançado no Conta Azul.

### Passo 12 — Criar e2e gate

Espelhar `testes/e2e/test_reconciliation_141air_jan.py`. Só troca `SELLER` e `PERIOD`. Asserts iguais. 5 testes (coverage_credits, coverage_debits, daily_diff_max, orphan_extrato, orphan_system).

Rodar:
```bash
python3 -m pytest testes/e2e/test_reconciliation_<seller>_<mes>.py -v -m "integration or e2e or reconciliation"
```

Expectativa: 5/5 green.

### Passo 13 — Atualizar docs

Antes do commit, registrar:

| Arquivo | O que anotar |
|---|---|
| `docs/reconciliation/CHANGELOG.md` | Nova seção com adicionado/modificado/runs |
| `docs/reconciliation/DECISIONS.md` | ADR-NNNN se tem decisão estrutural nova |
| `docs/reconciliation/ERRORS.md` | ERR-NNNN de cada bug novo catalogado |
| `docs/reconciliation/RUNS.md` | Já foi appendado pelo wrapper |

### Passo 14 — Commit

```bash
git add <arquivos-da-scope>
git commit -m "feat: spec 002 reconciliation — <seller> <period>/<year> hits 100%"
```

Scope do commit: apenas o que mudou para esse período. Evitar scope creep (mudanças em outros arquivos não relacionados).

---

## 4. Comandos rápidos (cheat sheet)

```bash
# Baseline de um período
python3 scripts/run_reconciliation.py 141air 2026-04

# Wrapper que registra em RUNS.md
./scripts/run_reconciliation.sh 141air 2026-04

# Orphan details
python3 scripts/debug_orphans.py 141air 2026-04

# Ingerir extrato CSV (one-shot)
python3 scripts/ingest_mar_141air.py  # adaptar para o período

# Gerar DRE
python3 scripts/simulate_dre.py 141air 2026-04

# Rodar e2e de 1 período
python3 -m pytest testes/e2e/test_reconciliation_141air_abr.py -v -m "integration or e2e or reconciliation"

# Rodar TODOS e2e gates
python3 -m pytest testes/e2e/ -v -m "integration or e2e or reconciliation"

# Suite completa (unit + integration + e2e)
python3 -m pytest -q
```

---

## 5. Gates (contract.yml)

Todos **devem** passar para considerar o período validado:

```yaml
# specs/002-extrato-reconciliation/contracts/reconciliation.yml
tolerances:
  per_line_brl: 0.02
  per_day_brl: 0.00         # STRICT — R$ 0
  per_period_brl: 0.00

coverage:
  credits_min_pct: 99.5     # na prática buscar 100
  debits_min_pct: 99.5      # na prática buscar 100
  orphan_extrato_max_count: 3   # na prática buscar 0
  orphan_system_max_count: 0
```

> **Nota sobre 99.5% vs 100%:** o contract tem floor em 99.5% como guardrail, mas a prática desde jan/2026 é perseguir 100% estrito. Não afrouxar o `per_day_brl: 0.00`.

---

## 6. Troubleshooting

### "Period not supported" ao rodar
Mapping faltando em `_PERIOD_TO_MES` ou `_SELLER_TO_FILENAME`. Editar `app/services/reconciliation.py`.

### "Extrato CSV missing: ..."
Path do arquivo não bate. Verificar se o CSV está em `testes/data/extratos/` com o nome exato do mapping.

### "unknown direction 'transfer'" em `money.signed_amount`
Classifier emitiu `direction='transfer'` mas a rota não foi tratada. Ver ADR-0001: `transfer_intra` deveria virar `transferencia_pix_in` via _expense_type_to_category.

### Orphan sys com `cat=liberacao status_detail=bpp_refunded`
Caso ERR-0014. O pid tem release do ledger mas o extrato só tem `entrada_dinheiro`. `align_refund_created_with_extrato` case 4 deve tratar — verificar se `ext_categories_by_pid[pid] == {"entrada_dinheiro"}` e o status_detail está nos aceitos.

### Orphan extrato + orphan sys com mesmo valor e ref
Geralmente mismatch de categoria (classificação divergente). Verificar se o `_expense_type_to_category` tem mapping correto entre expense_classifier vs extrato_ingester.

### Amount diff pequeno (< R$ 0,02)
Pode ser tolerance issue. Se o diff é < `per_line_brl` e ainda assim marca como amount_diff, revisar o `_is_match` em `match_movements`.

### Cloudflare 502 durante re-ingest
Supabase rate limit ou job-flood. Esperar 30s e reiniciar. O `expense_classified` companion é best-effort; falhas não quebram o fluxo (ver `_write_extrato_expense_events`).

### Fix aplicado mas reconciliação continua igual
Lembrete: se você editou `EXTRATO_CLASSIFICATION_RULES`, precisa re-ingerir o CSV. Se você editou `reconciliation.py` engine, não precisa re-ingerir — a mudança afeta só a leitura.

---

## 7. Anti-patterns (NÃO fazer)

1. **Afrouxar tolerance para esconder bug.** Se `daily_diff_max > R$ 0`, tem uma divergência real. Cataloga ERR e corrige.
2. **Alterar extrato CSV.** O CSV é imutável. Toda correção é no sistema.
3. **Forçar match com dados fake no teste.** Testes de reconciliação devem usar payments reais via ML API (memory `feedback_no_cache_json.md`). Extratos CSV são source of truth; eles SIM podem ser fixtures.
4. **Criar fix ad-hoc num pid específico.** Cada fix deve ser estrutural: ERR identifica padrão, fix trata família de casos, não 1 pid.
5. **Misturar múltiplos ERRs num mesmo commit.** Cada ERR = 1 commit (ou bloco coerente de commits) com red-test + fix + docs.
6. **Commitar sem checar regressão nos meses anteriores.** Sempre rodar jan/fev/mar depois de qualquer mudança em engine.
7. **Atualizar MEMORY.md sem uma conquista real.** Memory grava ganhos verificados, não promessas.

---

## 8. Adicionar um novo seller

Para cada seller novo:
1. Baixar extrato CSV do MP (account_statement) → salvar em `testes/data/extratos/` com nome `extrato <mes> <seller>.csv`.
2. Adicionar entry em `_SELLER_TO_FILENAME` para cada período que quer reconciliar.
3. Rodar `run_reconciliation.py` → iterar até 100%.
4. Criar `testes/e2e/test_reconciliation_<seller>_<mes>.py`.

Lista de sellers do plano original (sellers_after_pilot): `net-air`, `netparts-sp`, `easy-utilidades`, `easypeasy`. Cada um vai trazer novos ERRs — é esperado.

---

## 9. Artefatos gerados por período (checklist final)

Ao final da validação, cada (seller, período) deve ter:

- [x] Entry em `docs/reconciliation/RUNS.md` com 100/100/0/0/R$0
- [x] Arquivo `docs/reconciliation/dre_<seller>_<period>.json`
- [x] Arquivo `testes/e2e/test_reconciliation_<seller>_<mes>.py` (5 asserts)
- [x] ERRs novos catalogados em `docs/reconciliation/ERRORS.md`
- [x] ADRs novos em `docs/reconciliation/DECISIONS.md` (se aplicável)
- [x] CHANGELOG entry
- [x] Memória atualizada (`/Users/eryk/.claude/.../memory/reconciliation_141air_jan_100pct.md`)
- [x] Commit único com scope claro

---

## 10. Referências

- Spec: `specs/002-extrato-reconciliation/spec.md`
- Plan: `specs/002-extrato-reconciliation/plan.md`
- Tasks: `specs/002-extrato-reconciliation/tasks.md`
- Contract: `specs/002-extrato-reconciliation/contracts/reconciliation.yml`
- Engine: `app/services/reconciliation.py`
- Ingester: `app/services/extrato_ingester.py`
- Error log: `docs/reconciliation/ERRORS.md`
- ADR log: `docs/reconciliation/DECISIONS.md`
- Run log: `docs/reconciliation/RUNS.md`
- CLAUDE.md project guide: `/CLAUDE.md`

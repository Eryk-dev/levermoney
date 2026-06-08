# Conciliador — Finalização (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Levar o conciliador de "99,9% em dry-run" para "fecha ao centavo em produção", finalizando: baixa extrato-dirigida ligada ao CA real, fee bidirecional, ingester multi-formato, DRE/pontes como endpoints, e cutover ao vivo controlado.

**Architecture:** O núcleo de cálculo já está correto (~0,1% de erro, ver `rebuild-v3/`). Esta finalização (a) re-ancora a BAIXA no extrato (não na promessa `money_release_date`), (b) fecha o ajuste de fee nos dois sentidos, (c) garante que o ingester leia qualquer layout de relatório, (d) expõe DRE/pontes via API admin, (e) faz rollout por seller com flag, começando por 141air. Todo o trabalho é verificado contra dados reais jan-mai via o harness dry-run existente antes de habilitar escrita no CA.

**Tech Stack:** Python 3.12, FastAPI, httpx, Supabase (postgrest), pytest. Código em inglês; docs/plans em PT-BR. Sem ORM (queries diretas Supabase). CA API v2 (async, protocolo). Rate limiter global.

**Pré-requisitos de contexto (LER ANTES):**
- `rebuild-v3/README.md` + `rebuild-v3/02-arquitetura-alvo.md` + `rebuild-v3/06-reconciliacao-contabil.md`
- `app/services/CLAUDE.md` (camada de serviços), `app/routers/CLAUDE.md` (routers)
- Harness: `testes/harness/dryrun.py` (FakeDB, captura, segurança triplo-cinto)
- **Segurança:** NUNCA habilitar escrita no CA fora da Parte 5. Todo teste roda em dry-run (FakeDB) ou contra fixture. CA API só read (`buscar_*`, `listar_*`) até o cutover.

---

## Decisões de negócio (inputs — confirmar com o usuário antes da Parte 2/3)

Defaults recomendados (o plano é executável com eles; trocar só muda constantes):

| # | Decisão | Default recomendado | Onde impacta |
|---|---|---|---|
| 1 | Métrica do painel ML (régua da ponte) | **vendas líquidas** (bruto − comissão − frete) por `date_approved` | `app/services/pontes.py` (Parte 4) |
| 2 | Tolerância de resíduo por seller/mês | **R$ 50,00** (portão fecha se \|resíduo\| < tol) | `app/services/financial_closing.py` + endpoints |
| 3 | Antecipação (ML libera adiantado c/ desconto) | taxa de antecipação = **despesa financeira** cat `2.8.2` (comissão) ou nova cat; baixa parcial pelo valor real do extrato | `baixas_extrato` (ajuste) + Parte 3 |
| 4 | Cancela-antes-de-liberar | **cancelar** as contas (não-evento); parcela sem crédito vira `nunca_baixou` → job de cancelamento manual-confirmável | `baixas_extrato` (já sinaliza) + Parte 3 |

> Estas constantes ficam em `app/config.py` (ver Task 0.1) para serem ajustáveis sem editar lógica.

---

### Task 0.1: Constantes de finalização em config

**Files:**
- Modify: `app/config.py` (adicionar campos na classe Settings)
- Test: `testes/finalizacao/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# testes/finalizacao/test_config.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.config import settings


def test_reconciliation_defaults_present():
    assert settings.reconciliation_tolerance_brl == 50.0
    assert settings.baixa_extrato_driven_sellers == ""   # vazio = nenhum (rollout por seller)
    assert settings.painel_ml_metric == "vendas_liquidas"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_config.py -v`
Expected: FAIL (AttributeError: 'Settings' object has no attribute 'reconciliation_tolerance_brl')

- [ ] **Step 3: Add fields to Settings**

Em `app/config.py`, na classe `Settings(BaseSettings)`, adicionar (após os campos existentes, antes do `class Config`/`model_config`):

```python
    # --- Finalização do conciliador ---
    reconciliation_tolerance_brl: float = 50.0
    # Slugs com baixa extrato-dirigida habilitada (comma-separated). Vazio = nenhum (legado por-promessa).
    baixa_extrato_driven_sellers: str = ""
    # Slugs com ESCRITA no CA habilitada na baixa extrato-dirigida. Vazio = dry-run (não posta).
    baixa_extrato_write_sellers: str = ""
    painel_ml_metric: str = "vendas_liquidas"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
mkdir -p testes/finalizacao
git add app/config.py testes/finalizacao/test_config.py
git commit -m "feat(config): constantes de finalização do conciliador (tolerância, flags de rollout)"
```

---

## PARTE 1 — Fase 1: Ingester multi-formato (3 layouts)

**Problema:** `extrato_ingester._parse_account_statement` só entende o layout `RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;...`. Se a conta MP devolver o release_report (`DATE;SOURCE_ID;RECORD_TYPE;DESCRIPTION`), o parser retorna 0 transações silenciosamente. Já existe o conversor `legacy/daily_export._ensure_account_statement_csv` que normaliza os 3 layouts — basta CHAMÁ-LO antes de parsear.

**Files:**
- Modify: `app/services/extrato_ingester.py` (na função `ingest_extrato_for_seller`, normalizar bytes antes de `_parse_account_statement`)
- Test: `testes/finalizacao/test_ingester_formato.py`

### Task 1.1: Normalizar bytes do relatório antes de parsear

- [ ] **Step 1: Write the failing test** (alimenta o parser com layout release_report e exige >0 transações)

```python
# testes/finalizacao/test_ingester_formato.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.extrato_ingester import _normalize_report_bytes, _parse_account_statement

# layout RELEASE REPORT da API (RECORD_TYPE) — NÃO é o account_statement nativo
RELEASE_REPORT = (
    b"DATE;SOURCE_ID;RECORD_TYPE;DESCRIPTION;GROSS_AMOUNT;MP_FEE_AMOUNT;SHIPPING_FEE_AMOUNT;NET_CREDIT_AMOUNT;NET_DEBIT_AMOUNT\n"
    b"2026-01-05;138199281600;release;payment;100,00;-10,00;-5,00;85,00;0,00\n"
)

def test_release_report_layout_parsed_after_normalize():
    norm = _normalize_report_bytes(RELEASE_REPORT)
    summary, txs = _parse_account_statement(norm.decode("utf-8"))
    assert len(txs) > 0, "parser deveria entender o layout após normalizar"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_ingester_formato.py -v`
Expected: FAIL (ImportError: cannot import name '_normalize_report_bytes')

- [ ] **Step 3: Implementar `_normalize_report_bytes` + chamá-lo no pipeline**

Em `app/services/extrato_ingester.py`, adicionar perto do topo (após os imports):

```python
from app.services.legacy.daily_export import _ensure_account_statement_csv


def _normalize_report_bytes(report_bytes: bytes) -> bytes:
    """Normaliza qualquer layout (account_statement nativo, release_report, settlement)
    para o layout que _parse_account_statement entende. Idempotente: se já é o layout
    nativo (tem 'RELEASE_DATE;TRANSACTION_TYPE'), _ensure_account_statement_csv devolve igual."""
    try:
        return _ensure_account_statement_csv(report_bytes)
    except Exception:
        return report_bytes
```

Depois, dentro de `ingest_extrato_for_seller`, localizar onde os bytes do relatório são obtidos e parseados. O download usa `_get_or_create_report(...)` e em seguida `_parse_account_statement(csv_bytes.decode(...))`. Inserir a normalização **entre** os dois:

```python
    # ANTES (exemplo do código atual):
    #   summary, transactions = _parse_account_statement(csv_bytes.decode("utf-8"))
    # DEPOIS:
    csv_bytes = _normalize_report_bytes(csv_bytes)
    summary, transactions = _parse_account_statement(csv_bytes.decode("utf-8"))
```

> NOTA: confirmar o nome exato da variável de bytes lendo `ingest_extrato_for_seller` (≈ linha 565-600). Se for `report_bytes`, ajustar o nome no `_normalize_report_bytes(report_bytes)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_ingester_formato.py -v`
Expected: PASS

- [ ] **Step 5: Run regressão (layout nativo continua funcionando via judge)**

Run: `python3 testes/judge_caixa_jan2026.py 2>&1 | grep -c "OK ✓"`
Expected: `4` (âncoras OK — o conversor é idempotente no layout nativo)

- [ ] **Step 6: Commit**

```bash
git add app/services/extrato_ingester.py testes/finalizacao/test_ingester_formato.py
git commit -m "fix(ingester): normaliza relatório (3 layouts) antes de parsear (Fase 1)"
```

---

## PARTE 2 — Fase 4: Fee bidirecional + revalidação

**Problema:** `release_report_validator` só cria ajuste quando o release cobra MAIS que o processor (fee_diff >= 0.01). Quando cobra MENOS (release < processor), só incrementa um contador → o CA fica com despesa MAIOR que a real. Além disso `fee_adjusted=True` nunca é resetado → um report posterior nunca revalida.

**Arquitetura:** quando `fee_diff <= -0.01`, enfileirar um CRÉDITO (contas-a-receber, categoria estorno_taxa 1.3.4) da diferença, zerando o excesso de despesa. Idem shipping. Guardar o `fee_adjusted_amount` para revalidar se um novo report trouxer valor diferente.

**Files:**
- Modify: `app/services/release_report_validator.py:323-343` (os blocos de diff negativo + o mark fee_adjusted)
- Create: `testes/finalizacao/fixtures/release_report_overcharge.csv`
- Test: `testes/finalizacao/test_fee_bidirecional.py`

### Task 2.1: Fixture de release report com overcharge

- [ ] **Step 1: Criar a fixture** (1 payment onde release_fee < processor_fee)

```
# testes/finalizacao/fixtures/release_report_overcharge.csv
DATE;SOURCE_ID;RECORD_TYPE;DESCRIPTION;MP_FEE_AMOUNT;SHIPPING_FEE_AMOUNT;TRANSACTION_APPROVAL_DATE
2026-01-10;500000001;release;payment;-8,00;-0,00;2026-01-09
```

(Contexto: o payment 500000001 terá `processor_fee=10,00` no fake DB → release 8,00 < processor 10,00 → diff −2,00 → deve gerar crédito de R$2,00.)

- [ ] **Step 2: Commit a fixture**

```bash
mkdir -p testes/finalizacao/fixtures
git add testes/finalizacao/fixtures/release_report_overcharge.csv
git commit -m "test(fee): fixture release report com overcharge"
```

### Task 2.2: Test do crédito bidirecional (com fake db + captura de ca_queue)

- [ ] **Step 1: Write the failing test**

```python
# testes/finalizacao/test_fee_bidirecional.py
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services import release_report_validator as V
from app.services import ca_queue

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "release_report_overcharge.csv")


class _Q:
    def select(self,*a,**k): return self
    def eq(self,*a,**k): return self
    def update(self,*a,**k): self._u=a; return self
    def execute(self): return type("R",(),{"data":[]})()
class _DB:
    def table(self,n): return _Q()


def test_overcharge_gera_credito(monkeypatch):
    captured = []
    async def fake_enqueue(**kw):
        captured.append(kw); return {"captured": True}
    monkeypatch.setattr(ca_queue, "enqueue", fake_enqueue)

    # payment com processor_fee=10 (> release 8) e config CA
    payment = {"ml_payment_id": 500000001, "processor_fee": 10.0, "processor_shipping": 0.0, "fee_adjusted": False}
    seller = {"slug": "t", "ca_conta_bancaria": "c", "ca_centro_custo_variavel": "cc"}

    async def run():
        csv_bytes = open(FIX, "rb").read()
        await V._validate_rows(_DB(), seller, "t", V._parse_release_report_with_fees(csv_bytes),
                               {500000001: payment})
    asyncio.run(run())

    credits = [c for c in captured if "ajuste_fee_credito" in c.get("job_type", "")]
    assert credits, "deveria enfileirar crédito quando release < processor"
    assert abs(credits[0]["ca_payload"]["valor"] - 2.0) < 0.01
```

> NOTA: este teste assume um refactor: extrair o loop de validação para `_validate_rows(db, seller, slug, rows, payments_by_id)`. Ver Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_fee_bidirecional.py -v`
Expected: FAIL (AttributeError: module has no attribute '_validate_rows')

- [ ] **Step 3: Refatorar o loop para `_validate_rows` + adicionar o crédito bidirecional**

Em `app/services/release_report_validator.py`, extrair o corpo do loop de `validate_release_fees_for_seller` para uma função `_validate_rows(db, seller, seller_slug, rows, payments_by_id) -> dict` (mover as linhas 229-343 para dentro dela, retornando `stats`). Na função original, chamar `_validate_rows(...)`.

Dentro de `_validate_rows`, SUBSTITUIR os blocos de diff negativo (linhas atuais 323-333) por:

```python
        # Negative diff (release < processor): ML cobrou MENOS -> CA tem despesa a MAIS.
        # Postar CRÉDITO (contas-a-receber, estorno de taxa 1.3.4) da diferença.
        if fee_diff <= -0.01:
            competencia = (row.get("approval_date") or row["date"])[:10] or row["date"][:10]
            credito = abs(fee_diff)
            credito_payload = _build_credito_estorno(seller, competencia, row["date"][:10], credito,
                                                     f"Ajuste Comissão (crédito) - Payment {pid}",
                                                     f"processor={processor_fee}, release={release_fee}, diff={fee_diff}")
            await ca_queue.enqueue(
                seller_slug=seller_slug, job_type="ajuste_fee_credito",
                ca_endpoint=f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-receber",
                ca_payload=credito_payload,
                idempotency_key=f"{seller_slug}:{pid}:ajuste_fee_credito",
                group_id=f"{seller_slug}:{pid}:ajustes", priority=25,
            )
            adjustments_made += 1
            stats["fee_credits"] += 1

        if shipping_diff <= -0.01:
            stats["shipping_overcharged"] += 1
```

E adicionar o helper `_build_credito_estorno` (perto de onde `_build_despesa_payload` é importado):

```python
from app.services.processor import _build_evento, _build_parcela, CA_CATEGORIES, CA_CONTATO_ML


def _build_credito_estorno(seller, data_competencia, data_vencimento, valor, descricao, observacao):
    """Contas-a-receber (crédito) categoria estorno_taxa 1.3.4 — reduz o excesso de despesa."""
    conta = seller["ca_conta_bancaria"]
    contato = seller.get("ca_contato_ml") or CA_CONTATO_ML
    parcela = _build_parcela(descricao, data_vencimento, conta, valor)
    return _build_evento(data_competencia, valor, descricao, observacao, contato, conta,
                         CA_CATEGORIES["estorno_taxa"], seller.get("ca_centro_custo_variavel"), parcela)
```

Garantir que `stats` (Counter) tenha as chaves novas (`fee_credits`); como é `Counter()` acessar já cria. Confirmar que `CA_API` está importado no módulo (já está — usado nos `ca_endpoint`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_fee_bidirecional.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/release_report_validator.py testes/finalizacao/test_fee_bidirecional.py
git commit -m "fix(fee): ajuste bidirecional - crédito quando release < processor (Fase 4)"
```

### Task 2.3: Revalidação — reset de fee_adjusted quando o report muda

**Files:**
- Modify: `app/services/release_report_validator.py` (gate `fee_adjusted` na linha ~249 + persistência do valor ajustado na linha ~336)
- Test: adicionar ao `testes/finalizacao/test_fee_bidirecional.py`

- [ ] **Step 1: Write the failing test** (adicionar função ao arquivo)

```python
def test_revalida_quando_report_muda(monkeypatch):
    captured = []
    async def fake_enqueue(**kw): captured.append(kw); return {}
    monkeypatch.setattr(ca_queue, "enqueue", fake_enqueue)
    # payment já ajustado antes com fee_adjusted_amount=2,00; novo report traz fee 12 (diff +2 vs processor 10)
    payment = {"ml_payment_id": 500000002, "processor_fee": 10.0, "processor_shipping": 0.0,
               "fee_adjusted": True, "fee_adjusted_amount": 0.0}
    # ... monta row com mp_fee_amount -12,00 e roda _validate_rows; espera novo ajuste de +2,00
    # (detalhe da row análogo à fixture; ver helper _row abaixo)
    from app.services.release_report_validator import _validate_rows
    row = {"source_id": "500000002", "record_type": "release", "mp_fee_amount": -12.0,
           "shipping_fee_amount": 0.0, "date": "2026-01-10", "approval_date": "2026-01-09",
           "financing_fee_amount": 0.0, "taxes_amount": 0.0, "coupon_amount": 0.0, "gross_amount": 100.0}
    seller = {"slug": "t", "ca_conta_bancaria": "c", "ca_centro_custo_variavel": "cc"}
    class _Q:
        def select(s,*a,**k): return s
        def eq(s,*a,**k): return s
        def update(s,*a,**k): return s
        def execute(s): return type("R",(),{"data":[]})()
    class _DB:
        def table(s,n): return _Q()
    asyncio.run(_validate_rows(_DB(), seller, "t", [row], {500000002: payment}))
    debs = [c for c in captured if c.get("job_type") == "ajuste_comissao"]
    assert debs, "report novo com fee maior deve revalidar mesmo com fee_adjusted=True"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_fee_bidirecional.py::test_revalida_quando_report_muda -v`
Expected: FAIL (o gate `if payment.get("fee_adjusted"): continue` pula a row)

- [ ] **Step 3: Trocar o gate por comparação de valor**

Em `_validate_rows`, SUBSTITUIR (linhas ~249-251):

```python
        if payment.get("fee_adjusted"):
            stats["already_adjusted"] += 1
            continue
```

por:

```python
        # Revalida se o report trouxer um fee/shipping DIFERENTE do último ajustado.
        prev_fee = float(payment.get("fee_adjusted_amount") or 0)
        if payment.get("fee_adjusted") and abs(abs(row["mp_fee_amount"]) - prev_fee) < 0.01:
            stats["already_adjusted"] += 1
            continue
```

E no bloco que marca `fee_adjusted` (linhas ~335-339), persistir também o valor:

```python
        if adjustments_made > 0:
            db.table("payments").update({
                "fee_adjusted": True,
                "fee_adjusted_amount": abs(row["mp_fee_amount"]),
                "updated_at": _now_iso(),
            }).eq("ml_payment_id", pid).eq("seller_slug", seller_slug).execute()
            stats["payments_adjusted"] += 1
```

(Usar o helper de timestamp existente; se não houver, `from datetime import datetime; _now_iso = lambda: datetime.now().isoformat()` no topo. Confirmar se a coluna `fee_adjusted_amount` existe — ver Task 2.4 migration.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_fee_bidirecional.py -v`
Expected: PASS (ambos os testes)

- [ ] **Step 5: Commit**

```bash
git add app/services/release_report_validator.py testes/finalizacao/test_fee_bidirecional.py
git commit -m "fix(fee): revalida ajuste quando report traz fee diferente (reset fee_adjusted)"
```

### Task 2.4: Migration da coluna fee_adjusted_amount

**Files:**
- Create: `migrations/006_fee_adjusted_amount.sql`

- [ ] **Step 1: Escrever a migration**

```sql
-- migrations/006_fee_adjusted_amount.sql
ALTER TABLE payments ADD COLUMN IF NOT EXISTS fee_adjusted_amount numeric DEFAULT 0;
```

- [ ] **Step 2: Aplicar (via Supabase MCP apply_migration ou painel)** — executar manualmente no projeto Supabase. Confirmar com:

Run (read-only check via psql/painel): `SELECT column_name FROM information_schema.columns WHERE table_name='payments' AND column_name='fee_adjusted_amount';`
Expected: 1 linha.

- [ ] **Step 3: Commit**

```bash
git add migrations/006_fee_adjusted_amount.sql
git commit -m "feat(db): coluna fee_adjusted_amount para revalidação de fee"
```

---

## PARTE 3 — Fase 3-full: Baixa extrato-dirigida ligada ao CA real

**Problema:** `processar_baixas_auto` busca parcelas abertas e dá baixa com `data_pagamento = data_vencimento` (a promessa) e valor da parcela. Precisa virar: baixa com data+valor REAIS do extrato. O core (`baixas_extrato.plan_baixas_from_extrato`) já existe e está testado — falta o wiring: baixar o extrato, buscar parcelas, planejar, e (com flag) postar.

**Arquitetura:** nova função `processar_baixas_extrato(seller_slug)` que:
1. baixa o extrato do dia/período (via `release_report_sync._get_or_create_report` + `_normalize_report_bytes`);
2. parseia (`_parse_account_statement`) e monta `extrato_lines` (ref, net, date);
3. busca parcelas abertas no CA (`ca_api.buscar_parcelas_abertas_receber/pagar`), extrai `payment_id` da descrição (regex existente em `release_checker`);
4. chama `plan_baixas_from_extrato`;
5. para cada `BaixaPlan`: se o seller está em `baixa_extrato_write_sellers` → `ca_queue.enqueue_baixa` com data+valor do plano; senão só loga (dry-run);
6. emite ajustes (Parte 3, Task 3.3) e sinaliza `nunca_baixou`.

**Files:**
- Create: `app/services/baixas_extrato_runner.py` (o wiring; mantém `baixas_extrato.py` puro)
- Modify: `app/routers/baixas.py` (endpoint `GET /baixas/extrato/{seller}`)
- Test: `testes/finalizacao/test_baixas_runner.py`

### Task 3.1: Extrair payment_id da descrição da parcela CA

**Files:**
- Create: `app/services/baixas_extrato_runner.py`
- Test: `testes/finalizacao/test_baixas_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# testes/finalizacao/test_baixas_runner.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.baixas_extrato_runner import _payment_id_from_parcela


def test_extrai_payment_id_da_descricao():
    # formatos reais: "Venda ML #ORDER - titulo" tem order; a receita usa Payment no obs/descrição
    assert _payment_id_from_parcela({"descricao": "Comissão ML - Payment 138199281600"}) == "138199281600"
    assert _payment_id_from_parcela({"descricao": "Devolução ML #138199281600"}) == "138199281600"
    assert _payment_id_from_parcela({"descricao": "sem id"}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_baixas_runner.py::test_extrai_payment_id_da_descricao -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implementar**

```python
# app/services/baixas_extrato_runner.py
"""Fase 3-full — wiring da baixa extrato-dirigida ao CA real.

Mantém baixas_extrato.py PURO (planejamento). Aqui: download do extrato, lookup de parcelas
no CA, planejamento, e (com flag baixa_extrato_write_sellers) postagem via ca_queue.
"""
import logging
import re

from app.config import settings
from app.services import ca_api, ca_queue
from app.services.baixas_extrato import plan_baixas_from_extrato
from app.services.extrato_ingester import _normalize_report_bytes, _parse_account_statement
from app.services.release_report_sync import _get_or_create_report
from app.models.sellers import get_seller_config, get_missing_ca_launch_fields
from app.db.supabase import get_db

logger = logging.getLogger(__name__)

_PID_RE = re.compile(r"(?:Payment|#)\s*(\d{6,})")


def _payment_id_from_parcela(parcela: dict) -> str | None:
    m = _PID_RE.search(parcela.get("descricao", "") or "")
    return m.group(1) if m else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_baixas_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/baixas_extrato_runner.py testes/finalizacao/test_baixas_runner.py
git commit -m "feat(baixas): extrai payment_id da descrição da parcela CA (Fase 3-full wiring)"
```

### Task 3.2: Montar extrato_lines e parcelas, planejar baixas (dry-run)

**Files:**
- Modify: `app/services/baixas_extrato_runner.py` (adicionar `plan_for_seller`)
- Test: `testes/finalizacao/test_baixas_runner.py`

- [ ] **Step 1: Write the failing test** (com extrato + parcelas mockados, sem rede)

```python
def test_plan_for_seller_monta_baixas(monkeypatch):
    import asyncio
    from app.services import baixas_extrato_runner as R
    EXTRATO = (
        "INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE\n0,00;85,00;0,00;85,00\n\n"
        "RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE\n"
        "05-01-2026;Liberação de dinheiro;138199281600;85,00;85,00\n"
    ).encode("utf-8")
    monkeypatch.setattr(R, "_get_or_create_report", lambda *a, **k: _async(EXTRATO))
    async def fake_parcelas(fn, conta, de, ate):
        return [{"id": "p1", "descricao": "Venda ML #138199281600 - x", "nao_pago": 85.0}]
    monkeypatch.setattr(R, "_fetch_open_parcelas", fake_parcelas)
    res = asyncio.run(R.plan_for_seller("t", "2026-01-01", "2026-01-31",
                                        seller={"ca_conta_bancaria": "c"}))
    assert len(res.baixas) == 1
    assert res.baixas[0].data_pagamento == "2026-01-05" and res.baixas[0].valor == 85.0

def _async(v):
    async def f(*a, **k): return v
    return f()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_baixas_runner.py::test_plan_for_seller_monta_baixas -v`
Expected: FAIL (no attribute 'plan_for_seller')

- [ ] **Step 3: Implementar `_fetch_open_parcelas` + `plan_for_seller`**

Adicionar em `app/services/baixas_extrato_runner.py`:

```python
async def _fetch_open_parcelas(search_fn, conta_id: str, data_de: str, data_ate: str) -> list:
    """Pagina buscar_parcelas_abertas_* e normaliza para {id, descricao, nao_pago, payment_id}."""
    out, page = [], 1
    while True:
        itens, total = await search_fn(conta_id, data_de, data_ate, pagina=page, tamanho=50)
        for it in itens:
            for parc in it.get("parcelas", [it]):
                out.append({"id": str(parc.get("id")), "descricao": it.get("descricao", ""),
                            "nao_pago": float(parc.get("nao_pago", parc.get("valor", 0)) or 0)})
        if len(out) >= total or not itens:
            break
        page += 1
    return out


async def plan_for_seller(seller_slug: str, data_de: str, data_ate: str, seller: dict):
    """Baixa o extrato, busca parcelas abertas, planeja baixas extrato-dirigidas (não posta)."""
    conta = seller["ca_conta_bancaria"]
    report = await _get_or_create_report(seller_slug, data_de, data_ate)
    summary, txs = _parse_account_statement(_normalize_report_bytes(report).decode("utf-8"))
    extrato_lines = [{"ref": str(t["reference_id"]), "net": t["amount"], "date": t["date"]} for t in txs]

    parcelas = []
    for fn in (ca_api.buscar_parcelas_abertas_receber, ca_api.buscar_parcelas_abertas_pagar):
        raw = await _fetch_open_parcelas(fn, conta, data_de, data_ate)
        for p in raw:
            pid = _payment_id_from_parcela(p)
            if pid:
                p["payment_id"] = pid
                parcelas.append(p)
    return plan_baixas_from_extrato(extrato_lines, parcelas)
```

> NOTA: confirmar o formato de retorno de `buscar_parcelas_abertas_*` (itens com `parcelas` aninhadas vs flat) lendo a resposta real do CA; ajustar `_fetch_open_parcelas` ao formato. O teste mocka, então passa; a validação real é na Parte 5 (staging).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_baixas_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/baixas_extrato_runner.py testes/finalizacao/test_baixas_runner.py
git commit -m "feat(baixas): plan_for_seller monta baixas extrato-dirigidas (dry-run)"
```

### Task 3.3: Postar baixas + ajustes (gated por flag) + endpoint

**Files:**
- Modify: `app/services/baixas_extrato_runner.py` (adicionar `run_for_seller`)
- Modify: `app/routers/baixas.py` (endpoint)
- Test: `testes/finalizacao/test_baixas_runner.py`

- [ ] **Step 1: Write the failing test** (flag OFF não posta; flag ON posta)

```python
def test_run_for_seller_respeita_flag(monkeypatch):
    import asyncio
    from app.services import baixas_extrato_runner as R
    from app.services.baixas_extrato import BaixaPlan, BaixaPlanResult
    plan = BaixaPlanResult(baixas=[BaixaPlan("p1","138199281600","2026-01-05",85.0,0.0)])
    monkeypatch.setattr(R, "plan_for_seller", lambda *a, **k: _async(plan))
    posted = []
    async def fake_enqueue_baixa(slug, pid, payload, **k): posted.append((pid, payload)); return {}
    monkeypatch.setattr(R.ca_queue, "enqueue_baixa", fake_enqueue_baixa)
    monkeypatch.setattr(R.settings, "baixa_extrato_write_sellers", "")           # OFF
    asyncio.run(R.run_for_seller("t", "2026-01-01", "2026-01-31", {"ca_conta_bancaria": "c"}))
    assert posted == []
    monkeypatch.setattr(R.settings, "baixa_extrato_write_sellers", "t")          # ON
    asyncio.run(R.run_for_seller("t", "2026-01-01", "2026-01-31", {"ca_conta_bancaria": "c"}))
    assert len(posted) == 1 and posted[0][1]["data_pagamento"] == "2026-01-05"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_baixas_runner.py::test_run_for_seller_respeita_flag -v`
Expected: FAIL (no attribute 'run_for_seller')

- [ ] **Step 3: Implementar `run_for_seller`**

Adicionar em `app/services/baixas_extrato_runner.py`:

```python
async def run_for_seller(seller_slug: str, data_de: str, data_ate: str, seller: dict) -> dict:
    plan = await plan_for_seller(seller_slug, data_de, data_ate, seller)
    write_on = seller_slug in {s.strip() for s in settings.baixa_extrato_write_sellers.split(",") if s.strip()}
    posted = 0
    for b in plan.baixas:
        if write_on:
            payload = {"data_pagamento": b.data_pagamento,
                       "composicao_valor": {"valor_bruto": b.valor},
                       "conta_financeira": seller["ca_conta_bancaria"]}
            await ca_queue.enqueue_baixa(seller_slug, b.parcela_id, payload, scheduled_for=None)
            posted += 1
        else:
            logger.info("[dry-run] baixa %s payment=%s data=%s valor=%.2f ajuste=%.2f",
                        b.parcela_id, b.payment_id, b.data_pagamento, b.valor, b.ajuste)
    logger.info("baixas_extrato %s: %d planejadas, %d postadas, %d nunca_baixou, %d sem_parcela",
                seller_slug, len(plan.baixas), posted, len(plan.nunca_baixou), len(plan.sem_parcela))
    return {"seller": seller_slug, "planejadas": len(plan.baixas), "postadas": posted,
            "nunca_baixou": len(plan.nunca_baixou), "sem_parcela": len(plan.sem_parcela),
            "write": write_on}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_baixas_runner.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Adicionar endpoint em `app/routers/baixas.py`** (após `processar_baixas_auto` ou no fim)

```python
from app.services import baixas_extrato_runner

@router.get("/baixas/extrato/{seller_slug}")
async def baixas_extrato(seller_slug: str, data_de: str, data_ate: str):
    """Baixa extrato-dirigida. NÃO posta no CA a menos que o seller esteja em
    baixa_extrato_write_sellers (config). Default: dry-run (só planeja + loga)."""
    from app.db.supabase import get_db
    from app.models.sellers import get_seller_config
    seller = get_seller_config(get_db(), seller_slug)
    if not seller:
        return {"error": "seller_not_found"}
    return await baixas_extrato_runner.run_for_seller(seller_slug, data_de, data_ate, seller)
```

- [ ] **Step 6: Commit**

```bash
git add app/services/baixas_extrato_runner.py app/routers/baixas.py testes/finalizacao/test_baixas_runner.py
git commit -m "feat(baixas): run_for_seller posta baixa extrato-dirigida (gated) + endpoint (Fase 3-full)"
```

---

## PARTE 4 — Produtizar DRE + Pontes (endpoints admin)

**Problema:** DRE (Fase 6) e pontes (Fase 5) só existem no harness (capturando eventos). Produtizar como serviços que leem do DB (`payments` + valores armazenados) e expor via admin.

**Arquitetura:** `app/services/dre_report.py` agrega `payments` por mês de competência (receita por `date_approved`, devolução por data do estorno) usando os campos já persistidos (`processor_fee`, `processor_shipping`, `net_amount`, `ml_status`). `app/services/pontes.py` calcula caixa↔DRE (Δ recebíveis) e DRE↔painel ML (devolução diferida). Endpoints em `app/routers/admin/`.

> NOTA: a fonte de verdade muda de "eventos capturados" (harness) para "tabela payments" (produção). Os números devem bater com o harness `dre`/`ponte` rodado sobre os mesmos dados.

### Task 4.1: Serviço DRE por competência (lê do DB)

**Files:**
- Create: `app/services/dre_report.py`
- Test: `testes/finalizacao/test_dre_report.py`

- [ ] **Step 1: Write the failing test** (com lista de payments mockada)

```python
# testes/finalizacao/test_dre_report.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.dre_report import build_dre_from_payments


def test_dre_competencia_basico():
    payments = [
        {"ml_status": "approved", "amount": 100.0, "processor_fee": 10.0, "processor_shipping": 5.0,
         "raw_payment": {"date_approved": "2026-01-10T12:00:00.000-04:00"}},
        {"ml_status": "refunded", "amount": 40.0, "processor_fee": 4.0, "processor_shipping": 0.0,
         "raw_payment": {"date_approved": "2026-01-05T12:00:00.000-04:00",
                         "date_last_updated": "2026-02-03T12:00:00.000-04:00"}},
    ]
    dre = build_dre_from_payments(payments)
    assert round(dre["2026-01"]["receita_bruta"], 2) == 100.0
    assert round(dre["2026-01"]["comissao"], 2) == 10.0
    # devolução entra no mês do ESTORNO (date_last_updated), não da venda
    assert round(dre["2026-02"]["devolucoes"], 2) == 40.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_dre_report.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implementar**

```python
# app/services/dre_report.py
"""DRE por competência a partir da tabela payments (produção).
Receita bruta por date_approved (BRT); devolução por data do estorno (date_last_updated BRT)."""
from collections import defaultdict
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))


def _brt_month(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(BRT).strftime("%Y-%m")
    except (ValueError, TypeError):
        return (iso or "")[:7]


def build_dre_from_payments(payments: list[dict]) -> dict:
    dre = defaultdict(lambda: defaultdict(float))
    for p in payments:
        raw = p.get("raw_payment") or {}
        st = p.get("ml_status")
        venda_m = _brt_month(raw.get("date_approved") or raw.get("date_created", ""))
        amount = float(p.get("amount") or 0)
        fee = float(p.get("processor_fee") or 0)
        ship = float(p.get("processor_shipping") or 0)
        if st in ("approved", "in_mediation") or (st == "charged_back" and raw.get("status_detail") == "reimbursed"):
            dre[venda_m]["receita_bruta"] += amount
            dre[venda_m]["comissao"] += fee
            dre[venda_m]["frete"] += ship
        if st in ("refunded", "charged_back") and raw.get("status_detail") != "reimbursed":
            estorno_m = _brt_month(raw.get("date_last_updated") or raw.get("date_approved", ""))
            dre[estorno_m]["devolucoes"] += min(float(raw.get("transaction_amount_refunded") or amount), amount)
    # resultado de vendas
    for m, v in dre.items():
        v["resultado_vendas"] = v["receita_bruta"] - v["comissao"] - v["frete"] - v["devolucoes"]
    return {m: dict(v) for m, v in dre.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_dre_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/dre_report.py testes/finalizacao/test_dre_report.py
git commit -m "feat(dre): DRE por competência a partir de payments (Fase 6 produção)"
```

### Task 4.2: Endpoint admin do DRE

**Files:**
- Modify: `app/routers/admin/revenue.py` (ou criar `app/routers/admin/dre.py` e montar no `__init__.py`)
- Test: manual (curl) — documentado

- [ ] **Step 1: Adicionar endpoint** em `app/routers/admin/revenue.py`:

```python
from app.services.dre_report import build_dre_from_payments

@router.get("/dre/{seller_slug}", dependencies=[Depends(require_admin)])
async def dre_seller(seller_slug: str):
    from app.db.supabase import get_db
    db = get_db()
    rows = db.table("payments").select(
        "amount,processor_fee,processor_shipping,ml_status,raw_payment"
    ).eq("seller_slug", seller_slug).execute()
    return build_dre_from_payments(rows.data or [])
```

(Confirmar que `router`, `Depends`, `require_admin` já estão importados no módulo — estão, é o padrão do pacote admin.)

- [ ] **Step 2: Verificar manualmente** (servidor local rodando)

Run: `curl -s -H "X-Admin-Token: <token>" "http://localhost:8000/admin/dre/141air" | head`
Expected: JSON com meses → {receita_bruta, comissao, frete, devolucoes, resultado_vendas}

- [ ] **Step 3: Commit**

```bash
git add app/routers/admin/revenue.py
git commit -m "feat(admin): endpoint GET /admin/dre/{seller} (DRE por competência)"
```

### Task 4.3: Serviço de pontes (caixa↔DRE e DRE↔painel ML)

**Files:**
- Create: `app/services/pontes.py`
- Test: `testes/finalizacao/test_pontes.py`

- [ ] **Step 1: Write the failing test**

```python
# testes/finalizacao/test_pontes.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.pontes import ponte_caixa_dre, devolucao_diferida


def test_ponte_caixa_dre():
    dre = {"2026-01": {"resultado_vendas": 100.0}}
    caixa = {"2026-01": 80.0}
    p = ponte_caixa_dre(dre, caixa)
    assert round(p["2026-01"]["delta_receberveis"], 2) == 20.0  # DRE - caixa


def test_devolucao_diferida():
    # estorno em fev de venda de jan -> diferida em fev
    payments = [{"ml_status": "refunded", "amount": 50.0,
                 "raw_payment": {"date_approved": "2026-01-10T00:00:00-04:00",
                                 "date_last_updated": "2026-02-05T00:00:00-04:00",
                                 "transaction_amount_refunded": 50.0}}]
    d = devolucao_diferida(payments)
    assert round(d["2026-02"], 2) == 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_pontes.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implementar**

```python
# app/services/pontes.py
"""Pontes de reconciliação: caixa↔DRE (recebíveis a liberar) e DRE↔painel ML (devolução diferida)."""
from collections import defaultdict
from app.services.dre_report import _brt_month


def ponte_caixa_dre(dre: dict, caixa_por_mes: dict) -> dict:
    """delta_receberveis = DRE resultado - caixa de vendas (timing: dinheiro ainda não liberado)."""
    out = {}
    for m in set(dre) | set(caixa_por_mes):
        res = (dre.get(m, {}) or {}).get("resultado_vendas", 0.0)
        cx = caixa_por_mes.get(m, 0.0)
        out[m] = {"dre_resultado": res, "caixa": cx, "delta_receberveis": round(res - cx, 2)}
    return out


def devolucao_diferida(payments: list[dict]) -> dict:
    """Σ devoluções cujo ESTORNO caiu em mês != mês da venda (o painel ML conta no mês da venda)."""
    diff = defaultdict(float)
    for p in payments:
        raw = p.get("raw_payment") or {}
        if p.get("ml_status") in ("refunded", "charged_back") and raw.get("status_detail") != "reimbursed":
            venda_m = _brt_month(raw.get("date_approved") or raw.get("date_created", ""))
            estorno_m = _brt_month(raw.get("date_last_updated") or raw.get("date_approved", ""))
            if venda_m and estorno_m and venda_m != estorno_m:
                val = min(float(raw.get("transaction_amount_refunded") or p.get("amount") or 0),
                          float(p.get("amount") or 0))
                diff[estorno_m] += val
    return dict(diff)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_pontes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/pontes.py testes/finalizacao/test_pontes.py
git commit -m "feat(pontes): caixa↔DRE + devolução diferida (Fase 5 produção)"
```

### Task 4.4: Endpoint admin das pontes

**Files:**
- Modify: `app/routers/admin/revenue.py`

- [ ] **Step 1: Adicionar endpoint** (caixa vem do extrato — usa o harness/judge parser; aqui caixa por mês é input opcional, default vazio):

```python
from app.services.pontes import ponte_caixa_dre, devolucao_diferida

@router.get("/pontes/{seller_slug}", dependencies=[Depends(require_admin)])
async def pontes_seller(seller_slug: str):
    from app.db.supabase import get_db
    db = get_db()
    rows = (db.table("payments").select("amount,processor_fee,processor_shipping,ml_status,raw_payment")
            .eq("seller_slug", seller_slug).execute()).data or []
    dre = build_dre_from_payments(rows)
    caixa = {}  # TODO produção: alimentar com Σ extrato sale-lines por mês (via extrato ingerido)
    return {"caixa_dre": ponte_caixa_dre(dre, caixa),
            "devolucao_diferida": devolucao_diferida(rows),
            "nota": "painel ML ≈ DRE.devolucoes_do_mes_da_venda + devolucao_diferida + by_admin"}
```

- [ ] **Step 2: Verificar manualmente**

Run: `curl -s -H "X-Admin-Token: <token>" "http://localhost:8000/admin/pontes/141air" | head`
Expected: JSON com `caixa_dre` e `devolucao_diferida`.

- [ ] **Step 3: Commit**

```bash
git add app/routers/admin/revenue.py
git commit -m "feat(admin): endpoint GET /admin/pontes/{seller}"
```

---

## PARTE 5 — Cutover ao vivo (runbook, não-código)

**Objetivo:** habilitar a baixa extrato-dirigida em produção POR SELLER, começando por 141air (menor, único com config CA completa), validando a cada passo. NUNCA habilitar escrita global de uma vez.

### Task 5.1: Validação dry-run em produção (sem escrita)

- [ ] **Step 1:** Garantir `baixa_extrato_write_sellers=""` (vazio) no `.env` de produção.
- [ ] **Step 2:** Rodar o endpoint dry-run para 141air, mês fechado mais recente:
  `curl -H "X-Admin-Token: <t>" "https://conciliador.levermoney.com.br/baixas/extrato/141air?data_de=2026-04-01&data_ate=2026-04-30"`
- [ ] **Step 3:** Conferir o log: `planejadas`, `postadas=0` (write off), `nunca_baixou`, `sem_parcela`. `sem_parcela` alto = parcelas não encontradas (investigar regex/descrição). `nunca_baixou` = parcelas sem crédito (cancela-antes-liberar — esperado em pequena quantidade).
- [ ] **Step 4:** Comparar o nº de baixas planejadas com o nº de linhas de crédito de venda do extrato de abril (devem casar dentro da tolerância).

### Task 5.2: Habilitar escrita só para 141air

- [ ] **Step 1:** Setar `baixa_extrato_write_sellers=141air` no `.env` de produção. Deploy.
- [ ] **Step 2:** DESLIGAR o scheduler de baixa por-promessa para 141air (em `main.py`, o nightly/scheduler chama `processar_baixas_auto`; adicionar guard: pular sellers em `baixa_extrato_driven_sellers`). Setar `baixa_extrato_driven_sellers=141air`.
- [ ] **Step 3:** Rodar `GET /baixas/extrato/141air` para 1 dia recente. Conferir no Conta Azul: as baixas têm a DATA e o VALOR do extrato (não a promessa).
- [ ] **Step 4:** Rodar o financial_closing para 141air e confirmar que o fluxo de caixa do CA bate com o extrato dentro da tolerância (`reconciliation_tolerance_brl`).

### Task 5.3: Rollout incremental + monitoramento

- [ ] **Step 1:** Após 1 semana estável em 141air, adicionar `net-air` às duas flags.
- [ ] **Step 2:** Monitorar `/queue/dead` para baixas que falharam (4xx do CA) — parcela presa = caixa não fecha. Resolver caso a caso.
- [ ] **Step 3:** Documentar no `rebuild-v3/07-dados-resultados.md` o resíduo real de caixa pós-cutover por seller/mês.

### Task 5.4: Guard no scheduler legado de baixa

**Files:**
- Modify: `app/routers/baixas.py` (`processar_baixas_auto`) ou o caller em `main.py`/nightly

- [ ] **Step 1: Write the failing test**

```python
# testes/finalizacao/test_baixas_runner.py  (adicionar)
def test_scheduler_legado_pula_sellers_extrato(monkeypatch):
    import asyncio
    from app.routers import baixas
    monkeypatch.setattr(baixas.settings, "baixa_extrato_driven_sellers", "141air", raising=False)
    res = asyncio.run(baixas.processar_baixas_auto("141air"))
    assert res.get("skipped_reason") == "extrato_driven"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest testes/finalizacao/test_baixas_runner.py::test_scheduler_legado_pula_sellers_extrato -v`
Expected: FAIL

- [ ] **Step 3: Adicionar o guard** no topo de `processar_baixas_auto` (em `app/routers/baixas.py`):

```python
    from app.config import settings
    if seller_slug in {s.strip() for s in settings.baixa_extrato_driven_sellers.split(",") if s.strip()}:
        logger.info("processar_baixas_auto(%s): pulado (baixa extrato-dirigida ativa)", seller_slug)
        return {"seller": seller_slug, "skipped_reason": "extrato_driven"}
```

(Adicionar `import logging; logger = logging.getLogger(__name__)` se não existir no módulo — já existe.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest testes/finalizacao/test_baixas_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routers/baixas.py testes/finalizacao/test_baixas_runner.py
git commit -m "feat(baixas): scheduler legado pula sellers com baixa extrato-dirigida"
```

---

## Verificação final (rodar tudo)

- [ ] `python3 -m pytest testes/finalizacao/ -v` → todos PASS
- [ ] `python3 -m testes.harness.test_rules` → ALL PASS
- [ ] `python3 -m testes.harness.test_baixas_extrato` → ALL PASS
- [ ] `python3 testes/judge_caixa_jan2026.py | grep -c "OK ✓"` → 4
- [ ] `python3 -m testes.harness.run 141air timeline` → cobertura OTHER=0, resíduo de valor ~R$300
- [ ] Atualizar `rebuild-v3/05-fases-progresso.md` marcando Fase 1/3-full/4/5/6 como FEITAS (produção)

---

## Notas de risco

- **CA API formato de resposta:** `buscar_parcelas_abertas_*` pode retornar parcelas aninhadas em eventos OU flat. O `_fetch_open_parcelas` (Task 3.2) precisa de validação contra resposta real na Parte 5 — os testes mockam.
- **Regex de payment_id na descrição:** depende do template da parcela. Se houver parcelas com descrição editada manualmente, `sem_parcela` sobe. Monitorar na Task 5.1.
- **Fee bidirecional:** o teste usa fixture; a verificação real precisa de um release report de produção com overcharge conhecido.
- **Nunca habilitar `baixa_extrato_write_sellers` global** — sempre por seller, começando por 141air.

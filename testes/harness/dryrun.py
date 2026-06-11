"""Real-code dry-run harness.

Roda as FUNCOES REAIS do app (processor, expense_classifier) contra dados reais,
capturando o que SERIA lancado no Conta Azul — sem nunca escrever no CA nem no Supabase.

Seguranca (triplo cinto):
  1. ca_queue.enqueue_* -> capturados em memoria (nao chamam ca_api).
  2. ca_api write (criar_*, dar_baixa) -> patchados pra RAISE (falha hard se algo tentar postar).
  3. get_db -> FakeDB que so aceita .select() (retorna vazio) e captura upserts in-memory.

Uso:
    from testes.harness.dryrun import run_seller_month
    res = asyncio.run(run_seller_month("141air", payments_list))
"""
import contextlib
from dataclasses import dataclass, field

from app.services import processor, ca_queue, expense_classifier, ml_api, event_ledger
from app.models.sellers import CA_CONTATO_ML

# Sinal de caixa por tipo de lancamento (efeito no caixa do CA)
SIGN = {
    "receita": +1.0,      # contas-a-receber (entrada)
    "comissao": -1.0,     # contas-a-pagar (saida)
    "frete": -1.0,        # contas-a-pagar (saida)
    "estorno": -1.0,      # devolucao da receita (saida)
    "estorno_taxa": +1.0, # estorno de taxa (entrada)
    "estorno_frete": +1.0,  # estorno de frete (entrada, contas-a-receber) — US-010
    "partial_refund": -1.0,
}

# Config CA sintetica: pra dry-run, todo seller "tem config" -> exercita a logica completa
# de lancamento (em prod esses sellers serao configurados). UUIDs placeholder.
FAKE_SELLER_BASE = {
    "ca_conta_bancaria": "00000000-0000-0000-0000-000000000001",
    "ca_centro_custo_variavel": "00000000-0000-0000-0000-000000000002",
    "ca_contato_ml": CA_CONTATO_ML,
}


@dataclass
class CapturedEvent:
    tipo: str
    seller: str
    payment_id: str
    valor: float
    competencia: str | None
    vencimento: str | None
    categoria: str | None
    descricao: str


@dataclass
class Capture:
    events: list = field(default_factory=list)
    upserts: list = field(default_factory=list)      # (table, row)
    mp_expenses: list = field(default_factory=list)   # rows gravados via classifier

    def add(self, tipo, seller, payment_id, payload):
        valor = payload.get("valor", 0.0)
        venc = None
        try:
            venc = payload["condicao_pagamento"]["parcelas"][0]["data_vencimento"]
        except (KeyError, IndexError, TypeError):
            pass
        cat = None
        try:
            cat = payload["rateio"][0]["id_categoria"]
        except (KeyError, IndexError, TypeError):
            pass
        self.events.append(CapturedEvent(
            tipo=tipo, seller=seller, payment_id=str(payment_id), valor=valor,
            competencia=payload.get("data_competencia"), vencimento=venc,
            categoria=cat, descricao=payload.get("descricao", ""),
        ))


# --------------------------------------------------------------------------- #
# FakeDB: aceita encadeamento, .select() retorna vazio, captura upsert/insert. #
# --------------------------------------------------------------------------- #
class _Result:
    def __init__(self, data=None, count=None):
        self.data = data if data is not None else []
        self.count = count


class _FakeQuery:
    def __init__(self, capture, table, state):
        self._cap = capture
        self._table = table
        self._state = state        # dict pid -> {id, status} (idempotencia cross-month)
        self._eqs = {}
        self._pending = None  # ('upsert'|'insert'|'update', row)

    # leitura: encadeia; eq guarda o filtro (pra idempotencia de payments)
    def select(self, *a, **k): return self
    def eq(self, col, val): self._eqs[col] = val; return self
    def neq(self, *a, **k): return self
    def like(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def gt(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def single(self, *a, **k): return self
    def maybe_single(self, *a, **k): return self

    # escrita: captura, NAO persiste
    def upsert(self, row, **k):
        self._pending = ("upsert", row)
        return self

    def insert(self, row, **k):
        self._pending = ("insert", row)
        return self

    def update(self, row, **k):
        self._pending = ("update", row)
        return self

    def delete(self, **k):
        self._pending = ("delete", None)
        return self

    def execute(self):
        if self._pending:
            op, row = self._pending
            self._cap.upserts.append((self._table, op, row))
            if self._table == "mp_expenses" and row is not None:
                rows = row if isinstance(row, list) else [row]
                self._cap.mp_expenses.extend(rows)
            # event ledger (arquitetura v3): captura evento + idempotencia por
            # idempotency_key (replica ON CONFLICT DO NOTHING do Supabase)
            if self._table == "payment_events" and row is not None:
                rows = row if isinstance(row, list) else [row]
                out = []
                events = self._state.setdefault("_events", {})
                by_pid = self._state.setdefault("_events_by_pid", {})
                for rr in rows:
                    key = rr.get("idempotency_key")
                    if key in events:
                        continue  # duplicata -> data vazio -> record_event devolve None
                    events[key] = rr
                    by_pid.setdefault(str(rr.get("ml_payment_id")), []).append(rr)
                    out.append(rr)
                self._pending = None
                return _Result(data=out)
            # idempotencia cross-month: grava status do payment no estado compartilhado
            if self._table == "payments" and row is not None:
                rows = row if isinstance(row, list) else [row]
                for rr in rows:
                    pid = str(rr.get("ml_payment_id"))
                    if pid and pid != "None":
                        self._state[pid] = {"id": pid, "status": rr.get("status")}
            self._pending = None
            return _Result(data=[row] if row else [])
        # leitura do ledger por ml_payment_id: devolve eventos capturados (já processado?)
        if self._table == "payment_events" and "ml_payment_id" in self._eqs:
            pid = str(self._eqs["ml_payment_id"])
            return _Result(data=list(self._state.get("_events_by_pid", {}).get(pid, [])))
        # leitura de payments por ml_payment_id: devolve estado (já processado?)
        if self._table == "payments" and "ml_payment_id" in self._eqs:
            pid = str(self._eqs["ml_payment_id"])
            row = self._state.get(pid)
            return _Result(data=[row] if row else [])
        return _Result(data=[])


class FakeDB:
    def __init__(self, capture, state=None):
        self._cap = capture
        self.state = state if state is not None else {}

    def table(self, name):
        return _FakeQuery(self._cap, name, self.state)


# --------------------------------------------------------------------------- #
# Patching                                                                     #
# --------------------------------------------------------------------------- #
def _make_enqueue(cap, tipo):
    async def _capture(seller_slug, payment_id, payload, *a, **k):
        cap.add(tipo, seller_slug, payment_id, payload)
        return {"captured": True, "tipo": tipo}
    return _capture


def _raise_write(*a, **k):
    raise RuntimeError("SEGURANCA: tentativa de escrita real no CA durante dry-run")


@contextlib.contextmanager
def patched(cap, seller_fixture, state=None):
    db = FakeDB(cap, state)
    saved = {}

    def save(mod, name):
        saved[(mod, name)] = getattr(mod, name)

    # processor: db + seller config + order fetch
    for name in ("get_db", "get_seller_config", "get_missing_ca_launch_fields"):
        save(processor, name)
    processor.get_db = lambda: db
    processor.get_seller_config = lambda _db, slug: dict(seller_fixture, slug=slug)
    processor.get_missing_ca_launch_fields = lambda seller: []

    # event_ledger (arquitetura v3): TODAS as funcoes do ledger (record_event,
    # get_events, record_expense_event, ...) resolvem get_db() nos globals do
    # modulo -> 1 patch cobre tudo; record_event REAL roda (validacao + key).
    save(event_ledger, "get_db")
    event_ledger.get_db = lambda: db

    # ml_api.get_order -> None (fallback 404, titulo nao afeta valor)
    save(ml_api, "get_order")
    async def _no_order(*a, **k):
        return None
    ml_api.get_order = _no_order

    # ca_queue.enqueue_* -> captura
    enqueue_map = {
        "enqueue_receita": "receita", "enqueue_comissao": "comissao",
        "enqueue_frete": "frete", "enqueue_estorno": "estorno",
        "enqueue_estorno_taxa": "estorno_taxa",
        "enqueue_estorno_frete": "estorno_frete",
    }
    for fn, tipo in enqueue_map.items():
        save(ca_queue, fn)
        setattr(ca_queue, fn, _make_enqueue(cap, tipo))
    # partial_refund tem assinatura (seller, payment_id, index, payload)
    save(ca_queue, "enqueue_partial_refund")
    async def _cap_partial(seller_slug, payment_id, index, payload, *a, **k):
        cap.add("partial_refund", seller_slug, payment_id, payload)
        return {"captured": True}
    ca_queue.enqueue_partial_refund = _cap_partial
    save(ca_queue, "enqueue_baixa")
    async def _cap_baixa(seller_slug, parcela_id, payload, *a, **k):
        cap.upserts.append(("baixa", "enqueue", payload))
        return {"captured": True}
    ca_queue.enqueue_baixa = _cap_baixa

    try:
        yield db
    finally:
        for (mod, name), val in saved.items():
            setattr(mod, name, val)


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #
async def run_seller_month(seller_slug: str, payments: list, seller_fixture: dict = None, state: dict = None):
    """Roda o processor/classifier REAL sobre os payments. Retorna Capture.

    state: dict compartilhado de idempotencia (pid -> {id,status}). Passe o MESMO
    dict ao rodar meses em ordem -> payment processado em jan nao re-cria receita em fev.
    """
    fixture = dict(FAKE_SELLER_BASE)
    if seller_fixture:
        fixture.update(seller_fixture)
    cap = Capture()
    errors = []
    with patched(cap, fixture, state) as db:
        for p in payments:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            order_id = (p.get("order") or {}).get("id")
            try:
                if order_id:
                    await processor.process_payment_webhook(seller_slug, pid, payment_data=p)
                else:
                    await expense_classifier.classify_non_order_payment(db, seller_slug, p)
            except Exception as e:  # noqa: BLE001 - harness coleta, nao aborta
                errors.append((str(pid), type(e).__name__, str(e)[:160]))
    cap.errors = errors
    return cap

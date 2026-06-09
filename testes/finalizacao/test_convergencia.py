"""Suite de convergência (substitui os testes da implementação local superseded).

Cobre as mesmas preocupações contra o código ledger-based:
config flags, classificação de papel/pid do runner, payload do crédito
bidirecional do validator, bucketing do DRE (devolução diferida).
test_baixas_trio.py (harness) cobre a invariante do trio.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ── config flags de rollout ─────────────────────────────────────────────────

def test_config_flags_baixa_extrato():
    from app.config import settings
    assert hasattr(settings, "baixa_extrato_driven_sellers")
    assert hasattr(settings, "baixa_extrato_write_sellers")
    assert settings.baixa_extrato_driven_sellers == "" or isinstance(
        settings.baixa_extrato_driven_sellers, str)


# ── runner: papel + payment_id por descrição ────────────────────────────────

def test_runner_classify_papel():
    from app.services.baixas_extrato_runner import _classify_papel
    assert _classify_papel("Comissão ML - Payment 138199281600") == "comissao"
    assert _classify_papel("Frete MercadoEnvios - Payment 138199281600") == "frete"
    assert _classify_papel("Taxa ML adicional - Payment 138199281600") == "hiddenfee"
    assert _classify_papel("Subsídio ML - Payment 138199281600") == "subsidio"
    assert _classify_papel("Ajuste Comissão ML - Payment 138199281600") == "comissao"
    assert _classify_papel("Ajuste Comissão (crédito) - Payment 1381992") == "subsidio"
    assert _classify_papel("Venda ML #2000013659039448 - Compressor") == "receita"
    assert _classify_papel("Venda MP #123 - Item") == "receita"
    assert _classify_papel("Aluguel do galpão") is None


def test_runner_payment_id_patterns():
    from app.services.baixas_extrato_runner import _PAYMENT_RE, _ORDER_RE
    assert _PAYMENT_RE.search("Comissão ML - Payment 138199281600").group(1) == "138199281600"
    assert _PAYMENT_RE.search("Payment: 138199281600 | Liberação").group(1) == "138199281600"
    # receita: descrição tem ORDER id, não payment — resolvido via ledger
    m = _ORDER_RE.search("Venda ML #2000013659039448 - Compressor")
    assert m and m.group(1) == "2000013659039448"


# ── validator: crédito bidirecional ─────────────────────────────────────────

def test_credito_estorno_payload_contas_a_receber():
    from app.services.release_report_validator import _build_credito_estorno
    from app.models.sellers import CA_CATEGORIES
    seller = {"ca_conta_bancaria": "conta-1", "ca_centro_custo_variavel": "cc-1"}
    p = _build_credito_estorno(seller, "2026-01-10", "2026-01-12", 12.34,
                               "Ajuste Comissão (crédito) - Payment 1", "obs")
    assert p["valor"] == 12.34
    assert p["data_competencia"] == "2026-01-10"
    assert p["rateio"][0]["id_categoria"] == CA_CATEGORIES["estorno_taxa"]
    parcela = p["condicao_pagamento"]["parcelas"][0]
    assert parcela["data_vencimento"] == "2026-01-12"


def test_adjustment_events_bidirecionais():
    from app.services.event_ledger import validate_event
    validate_event("adjustment_fee", -5.0)   # ML cobrou mais -> despesa
    validate_event("adjustment_fee", 5.0)    # ML cobrou menos -> crédito
    validate_event("adjustment_shipping", -3.0)
    validate_event("adjustment_shipping", 3.0)


# ── DRE: devolução diferida (estorno bucketa no mês do event_date) ──────────

class _R:
    def __init__(s, d): s.data = d


class _Q:
    def __init__(s, rows): s._rows = rows; s._page = None; s._in = None
    def select(s, *a, **k): return s
    def eq(s, *a): return s
    def gte(s, *a): return s
    def lte(s, *a): return s
    def in_(s, col, vals): s._in = (col, set(vals)); return s
    def like(s, *a): return s
    def range(s, lo, hi): s._page = (lo, hi); return s
    def execute(s):
        rows = s._rows
        if s._in:
            col, vals = s._in
            rows = [r for r in rows if r.get(col) in vals]
        lo, hi = s._page
        return _R(rows[lo:hi + 1])


class _DB:
    def __init__(s, rows): s._rows = rows
    def table(s, n): return _Q(s._rows)


_ROWS = [
    {"event_type": "sale_approved", "signed_amount": 1000.0,
     "competencia_date": "2026-01-10", "event_date": "2026-01-10"},
    {"event_type": "fee_charged", "signed_amount": -100.0,
     "competencia_date": "2026-01-10", "event_date": "2026-01-10"},
    {"event_type": "shipping_charged", "signed_amount": -50.0,
     "competencia_date": "2026-01-10", "event_date": "2026-01-10"},
    # devolução DIFERIDA: venda jan, estorno fev
    {"event_type": "refund_created", "signed_amount": -1000.0,
     "competencia_date": "2026-01-10", "event_date": "2026-02-05"},
    {"event_type": "refund_fee", "signed_amount": 100.0,
     "competencia_date": "2026-01-10", "event_date": "2026-02-05"},
]


def test_dre_devolucao_diferida(monkeypatch):
    import app.services.dre_report as dre
    monkeypatch.setattr(dre, "get_db", lambda: _DB(_ROWS))
    out = asyncio.run(dre.build_dre_monthly("x", "2026-01-01", "2026-02-28"))
    assert out["2026-01"]["receita_bruta"] == 1000.0
    assert out["2026-01"]["comissao"] == 100.0
    assert "devolucoes" not in out["2026-01"]            # NÃO conta no mês da venda
    assert out["2026-02"]["devolucoes"] == 1000.0        # conta no mês do ESTORNO
    assert out["2026-02"]["estorno_taxa"] == 100.0       # estorno de taxa acompanha
    assert out["2026-01"]["resultado_vendas"] == 850.0
    assert out["2026-02"]["resultado_vendas"] == -900.0


def test_ponte_devolucao_diferida(monkeypatch):
    import app.services.pontes as pontes
    monkeypatch.setattr(pontes, "get_db", lambda: _DB(_ROWS))
    d = pontes._devolucao_diferida("x", "2026-01-01", "2026-02-28")
    assert d["saiu_do_mes"].get("2026-01") == 1000.0     # painel ML conta em jan
    assert d["entrou_no_mes"].get("2026-02") == 1000.0   # DRE conta em fev

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


# ── complemento: política disputa=cancelamento ──────────────────────────────

def _soma(grupo, comps):
    return round(sum(grupo.values()) + sum(c.valor for c in comps), 2)


def test_complemento_disputa_ja_lancada_zera_e_boka_perda():
    """Venda JÁ entrou (approved antes) e virou chargeback perdido: antis zeram
    cada categoria + perda real do banco em 1.2.1. Σ == extrato."""
    from app.services.complemento import plan_complemento
    grupo = {"receita": 100.0, "comissao": -12.0, "frete": -8.0}   # lançado
    extrato = -20.0  # banco: liberou +80, tomou de volta -100 (perdeu principal)
    comps = plan_complemento("1", {"status": "charged_back", "status_detail": ""},
                             grupo, extrato, "2026-02-05")
    antis = {c.categoria: c.valor for c in comps if c.motivo == "disputa_cancelamento"}
    assert antis["venda"] == -100.0      # zera venda -> "nunca virou venda"
    assert antis["comissao"] == 12.0     # zera comissão
    assert antis["frete"] == 8.0
    perda = [c for c in comps if c.motivo == "disputa_resultado"]
    assert len(perda) == 1 and perda[0].valor == -20.0
    assert perda[0].ca_categoria_key == "devolucao"   # 1.2.1
    assert _soma(grupo, comps) == extrato


def test_complemento_disputa_nunca_lancada_so_resultado():
    """Nunca entrou: NÃO cria par fantasma — só o resultado real do banco."""
    from app.services.complemento import plan_complemento
    comps = plan_complemento("2", {"status": "refunded", "status_detail": "bpp_refunded"},
                             {}, -36.40, "2026-03-01")
    assert len(comps) == 1
    assert comps[0].motivo == "disputa_resultado" and comps[0].valor == -36.40


def test_complemento_disputa_nunca_lancada_sem_caixa_nada():
    from app.services.complemento import plan_complemento
    comps = plan_complemento("3", {"status": "cancelled", "status_detail": ""},
                             {}, 0.0, "2026-03-01")
    assert comps == []


def test_complemento_api_cega_vira_divida_ml():
    """Caso 6,46: banco creditou menos do que a API inteira diz."""
    from app.services.complemento import plan_complemento
    grupo = {"receita": 18.33, "comissao": -1.63}    # net lançado 16.70
    comps = plan_complemento("148949991586", {"status": "approved", "status_detail": "accredited"},
                             grupo, 10.24, "2026-03-18")
    assert len(comps) == 1
    assert comps[0].categoria == "divida_ml" and comps[0].valor == -6.46
    assert comps[0].ca_categoria_key == "comissao_ml"
    assert _soma(grupo, comps) == 10.24


def test_complemento_refund_parcial_pelo_extrato():
    from app.services.complemento import plan_complemento
    grupo = {"receita": 200.0, "comissao": -30.0, "partial_refund": -50.0}  # net 120
    comps = plan_complemento("4", {"status": "approved", "status_detail": "partially_refunded"},
                             grupo, 110.0, "2026-04-10")
    assert len(comps) == 1
    assert comps[0].categoria == "estorno_parcial" and comps[0].valor == -10.0
    assert _soma(grupo, comps) == 110.0


def test_complemento_inelegivel_nao_lanca():
    from app.services.complemento import plan_complemento
    comps = plan_complemento("5", {"status": "approved", "status_detail": ""},
                             {"receita": 100.0}, 0.0, "2026-05-01", elegivel=False)
    assert comps == []

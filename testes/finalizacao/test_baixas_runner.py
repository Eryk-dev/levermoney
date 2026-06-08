import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.baixas_extrato_runner import _payment_id_from_parcela

def test_extrai_payment_id_da_descricao():
    assert _payment_id_from_parcela({"descricao": "Comissão ML - Payment 138199281600"}) == "138199281600"
    assert _payment_id_from_parcela({"descricao": "Devolução ML #138199281600"}) == "138199281600"
    assert _payment_id_from_parcela({"descricao": "sem id"}) is None


def test_plan_for_seller_monta_baixas(monkeypatch):
    import asyncio
    from app.services import baixas_extrato_runner as R
    EXTRATO = (
        "INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE\n0,00;85,00;0,00;85,00\n\n"
        "RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE\n"
        "05-01-2026;Liberação de dinheiro;138199281600;85,00;85,00\n"
    ).encode("utf-8")
    async def fake_report(*a, **k): return EXTRATO
    monkeypatch.setattr(R, "_get_or_create_report", fake_report)
    async def fake_parcelas(fn, conta, de, ate):
        return [{"id": "p1", "descricao": "Venda ML #138199281600 - x", "nao_pago": 85.0}]
    monkeypatch.setattr(R, "_fetch_open_parcelas", fake_parcelas)
    res = asyncio.run(R.plan_for_seller("t", "2026-01-01", "2026-01-31", seller={"ca_conta_bancaria": "c"}))
    assert len(res.baixas) == 1
    assert res.baixas[0].data_pagamento == "2026-01-05" and res.baixas[0].valor == 85.0


def test_run_for_seller_respeita_flag(monkeypatch):
    import asyncio
    from app.services import baixas_extrato_runner as R
    from app.services.baixas_extrato import BaixaPlan, BaixaPlanResult
    plan = BaixaPlanResult(baixas=[BaixaPlan("p1","138199281600","2026-01-05",85.0,0.0)])
    async def fake_plan(*a, **k): return plan
    monkeypatch.setattr(R, "plan_for_seller", fake_plan)
    posted = []
    async def fake_enqueue_baixa(slug, pid, payload, **k): posted.append((pid, payload)); return {}
    monkeypatch.setattr(R.ca_queue, "enqueue_baixa", fake_enqueue_baixa)
    monkeypatch.setattr(R.settings, "baixa_extrato_write_sellers", "")
    asyncio.run(R.run_for_seller("t", "2026-01-01", "2026-01-31", {"ca_conta_bancaria": "c"}))
    assert posted == []
    monkeypatch.setattr(R.settings, "baixa_extrato_write_sellers", "t")
    asyncio.run(R.run_for_seller("t", "2026-01-01", "2026-01-31", {"ca_conta_bancaria": "c"}))
    assert len(posted) == 1 and posted[0][1]["data_pagamento"] == "2026-01-05"

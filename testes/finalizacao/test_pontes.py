import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.pontes import ponte_caixa_dre, devolucao_diferida

def test_ponte_caixa_dre():
    dre = {"2026-01": {"resultado_vendas": 100.0}}
    caixa = {"2026-01": 80.0}
    p = ponte_caixa_dre(dre, caixa)
    assert round(p["2026-01"]["delta_receberveis"], 2) == 20.0

def test_devolucao_diferida():
    payments = [{"ml_status": "refunded", "amount": 50.0,
                 "raw_payment": {"date_approved": "2026-01-10T00:00:00-04:00",
                                 "date_last_updated": "2026-02-05T00:00:00-04:00",
                                 "transaction_amount_refunded": 50.0}}]
    d = devolucao_diferida(payments)
    assert round(d["2026-02"], 2) == 50.0

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
    assert round(dre["2026-02"]["devolucoes"], 2) == 40.0

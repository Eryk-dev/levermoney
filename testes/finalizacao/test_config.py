import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.config import settings

def test_reconciliation_defaults_present():
    assert settings.reconciliation_tolerance_brl == 50.0
    assert settings.baixa_extrato_driven_sellers == ""
    assert settings.painel_ml_metric == "vendas_liquidas"

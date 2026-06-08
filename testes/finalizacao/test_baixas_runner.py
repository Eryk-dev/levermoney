import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.baixas_extrato_runner import _payment_id_from_parcela

def test_extrai_payment_id_da_descricao():
    assert _payment_id_from_parcela({"descricao": "Comissão ML - Payment 138199281600"}) == "138199281600"
    assert _payment_id_from_parcela({"descricao": "Devolução ML #138199281600"}) == "138199281600"
    assert _payment_id_from_parcela({"descricao": "sem id"}) is None

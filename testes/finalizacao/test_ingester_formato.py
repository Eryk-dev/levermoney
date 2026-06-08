import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.extrato_ingester import _normalize_report_bytes, _parse_account_statement

RELEASE_REPORT = (
    b"DATE;SOURCE_ID;RECORD_TYPE;DESCRIPTION;GROSS_AMOUNT;MP_FEE_AMOUNT;SHIPPING_FEE_AMOUNT;NET_CREDIT_AMOUNT;NET_DEBIT_AMOUNT\n"
    b"2026-01-05;138199281600;release;payment;100,00;-10,00;-5,00;85,00;0,00\n"
)

def test_release_report_layout_parsed_after_normalize():
    norm = _normalize_report_bytes(RELEASE_REPORT)
    summary, txs = _parse_account_statement(norm.decode("utf-8"))
    assert len(txs) > 0, "parser deveria entender o layout após normalizar"

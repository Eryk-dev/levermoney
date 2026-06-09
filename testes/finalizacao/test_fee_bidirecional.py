import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services import release_report_validator as V
from app.services import ca_queue

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "release_report_overcharge.csv")

class _Q:
    def select(self,*a,**k): return self
    def eq(self,*a,**k): return self
    def update(self,*a,**k): return self
    def execute(self): return type("R",(),{"data":[]})()
class _DB:
    def table(self,n): return _Q()


def test_overcharge_gera_credito(monkeypatch):
    captured = []
    async def fake_enqueue(**kw):
        captured.append(kw); return {"captured": True}
    monkeypatch.setattr(ca_queue, "enqueue", fake_enqueue)
    payment = {"ml_payment_id": 500000001, "processor_fee": 10.0, "processor_shipping": 0.0, "fee_adjusted": False}
    seller = {"slug": "t", "ca_conta_bancaria": "c", "ca_centro_custo_variavel": "cc"}
    async def run():
        csv_bytes = open(FIX, "rb").read()
        await V._validate_rows(_DB(), seller, "t", V._parse_release_report_with_fees(csv_bytes), {500000001: payment})
    asyncio.run(run())
    credits = [c for c in captured if "ajuste_fee_credito" in c.get("job_type","")]
    assert credits, "deveria enfileirar crédito quando release < processor"
    assert abs(credits[0]["ca_payload"]["valor"] - 2.0) < 0.01


def test_revalida_quando_report_muda(monkeypatch):
    captured = []
    async def fake_enqueue(**kw): captured.append(kw); return {}
    monkeypatch.setattr(ca_queue, "enqueue", fake_enqueue)
    from app.services.release_report_validator import _validate_rows
    payment = {"ml_payment_id": 500000002, "processor_fee": 10.0, "processor_shipping": 0.0,
               "fee_adjusted": True, "fee_adjusted_amount": 0.0}
    row = {"source_id": "500000002", "record_type": "release", "description": "payment",
           "mp_fee_amount": -12.0,
           "shipping_fee_amount": 0.0, "date": "2026-01-10T00:00:00.000-03:00",
           "approval_date": "2026-01-09T00:00:00.000-03:00",
           "financing_fee_amount": 0.0, "taxes_amount": 0.0, "coupon_amount": 0.0, "gross_amount": 100.0,
           "external_reference": "", "net_credit_amount": 88.0, "net_debit_amount": 0.0,
           "order_id": "", "payment_method": "credit_card"}
    seller = {"slug": "t", "ca_conta_bancaria": "c", "ca_centro_custo_variavel": "cc"}
    asyncio.run(_validate_rows(_DB(), seller, "t", [row], {500000002: payment}))
    debs = [c for c in captured if c.get("job_type") == "ajuste_comissao"]
    assert debs, "report novo com fee maior deve revalidar mesmo com fee_adjusted=True"

"""Runner de produção do LANÇADOR DE COMPLEMENTO.

Por seller/período: lê o extrato (account_statement), o grupo lançado por ref
(payment_events) e o status do payment → `plan_complemento` (lógica pura) →
posta os lançamentos categorizados via ca_queue (gated por
`settings.baixa_extrato_write_sellers`; sem a flag = dry-run, retorna o plano).

Elegibilidade (não complementar ciclo aberto):
  - disputa/cancelled (status terminal): elegível.
  - approved: elegível se tem liberação no período E última linha do ref tem
    idade >= cycle_grace_days (liberação parcelada não é shortfall até fechar).

Refs com movimento no extrato e ZERO eventos no ledger (nunca-lançados pela
política): status vem da ML API (poucos casos; rate-limited).
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from app.config import settings
from app.db.supabase import get_db
from app.models.sellers import CA_CATEGORIES, CA_CONTATO_ML
from app.services import ca_queue, ml_api
from app.services.ca_api import CA_API
from app.services.complemento import plan_complemento
from app.services.extrato_ingester import _normalize_text, _parse_account_statement
from app.services.processor import _build_evento, _build_parcela, _build_despesa_payload
from app.services.release_report_sync import _get_or_create_report

logger = logging.getLogger(__name__)

# event_type do ledger -> tipo lógico do complemento (sinal vem do signed_amount)
_EVENT_TIPO = {
    "sale_approved": "receita",
    "fee_charged": "comissao",            # hiddenfee distinguido pela idempotency_key
    "shipping_charged": "frete",
    "subsidy_credited": "subsidio",
    "refund_created": "estorno",
    "partial_refund": "partial_refund",
    "refund_fee": "estorno_taxa",
    "refund_shipping": "estorno_frete",
    "adjustment_fee": "comissao",
    "adjustment_shipping": "frete",
}

CYCLE_GRACE_DAYS = 7


def _ddmmyyyy_to_iso(d: str) -> str:
    p = (d or "").strip().split("-")
    if len(p) == 3 and len(p[0]) == 2:
        return f"{p[2]}-{p[1]}-{p[0]}"
    return (d or "").strip()[:10]


def _grupo_por_ref(seller_slug: str) -> dict[str, dict]:
    """net_por_tipo (assinado) por ml_payment_id, a partir do ledger."""
    db = get_db()
    out: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    page = 0
    while True:
        rows = db.table("payment_events").select(
            "ml_payment_id, event_type, signed_amount, idempotency_key"
        ).eq("seller_slug", seller_slug).in_(
            "event_type", list(_EVENT_TIPO.keys())
        ).range(page * 1000, page * 1000 + 999).execute()
        data = rows.data or []
        for r in data:
            tipo = _EVENT_TIPO[r["event_type"]]
            if tipo == "comissao" and ":hiddenfee" in (r.get("idempotency_key") or ""):
                tipo = "hiddenfee"
            out[str(r["ml_payment_id"])][tipo] += float(r.get("signed_amount") or 0)
        if len(data) < 1000:
            break
        page += 1
    return {k: dict(v) for k, v in out.items()}


def _status_do_ledger(seller_slug: str, refs: list[str]) -> dict[str, dict]:
    """Status aproximado por ref a partir dos event_types (sem ML API)."""
    db = get_db()
    types_por_ref: dict[str, set] = defaultdict(set)
    for i in range(0, len(refs), 100):
        chunk = refs[i:i + 100]
        rows = db.table("payment_events").select("ml_payment_id, event_type").eq(
            "seller_slug", seller_slug
        ).in_("ml_payment_id", chunk).execute()
        for r in rows.data or []:
            types_por_ref[str(r["ml_payment_id"])].add(r["event_type"])
    out = {}
    for ref, ts in types_por_ref.items():
        if "reimbursed" in ts:
            st = {"status": "approved", "status_detail": "reimbursed_covered"}
        elif "refund_created" in ts or "charged_back" in ts:
            st = {"status": "refunded", "status_detail": ""}
        elif "partial_refund" in ts:
            st = {"status": "approved", "status_detail": "partially_refunded"}
        elif "sale_approved" in ts:
            st = {"status": "approved", "status_detail": ""}
        else:
            st = {"status": "unknown", "status_detail": ""}
        out[ref] = st
    return out


async def plan_for_seller(seller_slug: str, data_de: str, data_ate: str,
                          cycle_grace_days: int = CYCLE_GRACE_DAYS) -> dict:
    """Monta o plano de complementos. Leitura pura (+ ML API p/ refs sem eventos)."""
    report = await _get_or_create_report(seller_slug, data_de, data_ate)
    if not report:
        raise RuntimeError(f"account_statement indisponível {data_de}..{data_ate}")
    csv_text = report.decode("utf-8") if isinstance(report, bytes) else str(report)
    _, txs = _parse_account_statement(csv_text)

    ext_total: dict[str, float] = defaultdict(float)
    ultima_data: dict[str, str] = {}
    tem_liberacao: set[str] = set()
    for t in txs:
        ref = str(t.get("reference_id") or "")
        if not ref or not ref.isdigit():
            continue
        d = _ddmmyyyy_to_iso(t.get("date", ""))
        amount = float(t.get("amount", 0) or 0)
        ext_total[ref] += amount
        ultima_data[ref] = max(ultima_data.get(ref, ""), d)
        ttype = _normalize_text(t.get("transaction_type", ""))
        if "liberacao de dinheiro" in ttype and "cancelada" not in ttype and amount > 0:
            tem_liberacao.add(ref)

    grupo_ref = _grupo_por_ref(seller_slug)
    refs = sorted(set(ext_total) | set(grupo_ref))
    status_ref = _status_do_ledger(seller_slug, refs)

    hoje = datetime.now().strftime("%Y-%m-%d")
    corte = (datetime.now() - timedelta(days=cycle_grace_days)).strftime("%Y-%m-%d")

    plano = []
    inelegiveis = 0
    sem_status = []
    for ref in refs:
        etotal = round(ext_total.get(ref, 0.0), 2)
        grupo = grupo_ref.get(ref, {})
        st = status_ref.get(ref)
        if st is None or st["status"] == "unknown":
            # nunca lançado (política): status vem da ML API
            try:
                p = await ml_api.get_payment(seller_slug, int(ref))
                st = {"status": (p or {}).get("status"),
                      "status_detail": (p or {}).get("status_detail") or ""}
            except Exception:  # noqa: BLE001 — ref pode não ser payment (transfer etc.)
                sem_status.append(ref)
                continue
        is_disputa = st["status"] in ("refunded", "charged_back", "cancelled")
        eleg = is_disputa or (ref in tem_liberacao and ultima_data.get(ref, hoje) <= corte)
        if not eleg:
            inelegiveis += 1
            continue
        comps = plan_complemento(ref, st, grupo, etotal,
                                 data_lancamento=ultima_data.get(ref, data_ate))
        plano.extend(comps)

    resumo = defaultdict(lambda: [0, 0.0])
    for c in plano:
        resumo[c.categoria][0] += 1
        resumo[c.categoria][1] = round(resumo[c.categoria][1] + c.valor, 2)
    return {"seller": seller_slug, "de": data_de, "ate": data_ate,
            "complementos": plano,
            "resumo": {k: {"n": v[0], "total": v[1]} for k, v in resumo.items()},
            "inelegiveis_ciclo_aberto": inelegiveis,
            "refs_sem_status": len(sem_status)}


async def run_for_seller(seller_slug: str, data_de: str, data_ate: str,
                         seller: dict) -> dict:
    """Planeja e (se habilitado) posta os complementos via ca_queue."""
    plan = await plan_for_seller(seller_slug, data_de, data_ate)
    write_on = seller_slug in {
        s.strip() for s in settings.baixa_extrato_write_sellers.split(",") if s.strip()
    }
    contato = seller.get("ca_contato_ml") or CA_CONTATO_ML
    conta = seller["ca_conta_bancaria"]
    cc = seller.get("ca_centro_custo_variavel")

    posted = 0
    for c in plan["complementos"]:
        if not write_on:
            continue
        categoria_uuid = CA_CATEGORIES[c.ca_categoria_key]
        obs = f"complemento:{c.motivo} | extrato-driven"
        if c.valor > 0:
            payload = _build_evento(c.data, c.valor, c.descricao, obs, contato, conta,
                                    categoria_uuid, cc,
                                    _build_parcela(c.descricao, c.data, conta, c.valor))
            endpoint = f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-receber"
        else:
            payload = _build_despesa_payload(seller, c.data, c.data, abs(c.valor),
                                             c.descricao, obs, categoria_uuid)
            endpoint = f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-pagar"
        await ca_queue.enqueue(
            seller_slug=seller_slug,
            job_type=f"complemento_{c.categoria}",
            ca_endpoint=endpoint,
            ca_payload=payload,
            idempotency_key=f"{seller_slug}:{c.ref}:complemento:{c.categoria}:{c.data}",
            group_id=f"{seller_slug}:{c.ref}:complemento",
            priority=35,
        )
        posted += 1

    logger.info("complemento %s [%s..%s]: %d planejados, %d postados (write=%s)",
                seller_slug, data_de, data_ate, len(plan["complementos"]), posted, write_on)
    return {"seller": seller_slug, "de": data_de, "ate": data_ate,
            "planejados": len(plan["complementos"]), "postados": posted,
            "write": write_on, "resumo": plan["resumo"],
            "inelegiveis_ciclo_aberto": plan["inelegiveis_ciclo_aberto"],
            "refs_sem_status": plan["refs_sem_status"]}

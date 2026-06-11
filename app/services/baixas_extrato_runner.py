"""Baixa extrato-dirigida — wiring de produção do TRIO (Fase 3-full).

Fluxo: download do account_statement (release report convertido) → filtra créditos de
"Liberação de dinheiro" → busca parcelas abertas no CA (receber + pagar) → resolve
payment_id + papel de cada parcela → `plan_baixas_trio` (lógica pura) → posta baixas
via ca_queue (gated por `settings.baixa_extrato_write_sellers`).

Garantia: cada baixa usa DATA + VALOR reais do extrato, com o grupo da venda liquidado
proporcionalmente por tranche → Σ caixa CA do dia == Σ extrato do dia por construção.
Ajustes (over-release / crédito sem grupo) e resíduos (nunca_baixou) NÃO são postados:
voltam no resultado como fila de exceção explícita.
"""
import logging
import re
from collections import defaultdict

from app.config import settings
from app.db.supabase import get_db
from app.services import ca_api, ca_queue
from app.services.baixas_extrato import plan_baixas_trio, TrioPlanResult
from app.services.extrato_ingester import _normalize_text, _parse_account_statement
from app.services.release_report_sync import _get_or_create_report

logger = logging.getLogger(__name__)

# Padrões de descrição criados pelo processor.py (mesmos do release_checker)
_PAYMENT_RE = re.compile(r"Payment[:\s]+(\d{6,})")
_ORDER_RE = re.compile(r"Venda\s+M[LP]\s+#(\d+)")

# papel por padrão na descrição (despesas/créditos têm "- Payment {pid}")
_PAPEL_PATTERNS = [
    ("taxa ml adicional", "hiddenfee"),
    ("ajuste comissao (credito)", "subsidio"),
    ("ajuste frete (credito)", "subsidio"),
    ("ajuste comissao", "comissao"),
    ("ajuste frete", "frete"),
    ("comissao ml", "comissao"),
    ("frete mercadoenvios", "frete"),
    ("subsidio ml", "subsidio"),
    ("venda ml", "receita"),
    ("venda mp", "receita"),
]


def _classify_papel(descricao: str) -> str | None:
    d = _normalize_text(descricao or "")
    for pat, papel in _PAPEL_PATTERNS:
        if pat in d:
            return papel
    return None


def _ddmmyyyy_to_iso(d: str) -> str:
    p = (d or "").strip().split("-")
    if len(p) == 3 and len(p[0]) == 2:
        return f"{p[2]}-{p[1]}-{p[0]}"
    return (d or "").strip()[:10]


async def _order_to_payment_map(seller_slug: str, order_ids: set[int]) -> dict[int, str]:
    """Resolve ml_order_id → ml_payment_id via payment_events (sale_approved)."""
    if not order_ids:
        return {}
    db = get_db()
    out: dict[int, str] = {}
    ids = list(order_ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        rows = db.table("payment_events").select("ml_payment_id, ml_order_id").eq(
            "seller_slug", seller_slug
        ).eq("event_type", "sale_approved").in_("ml_order_id", chunk).execute()
        for r in rows.data or []:
            if r.get("ml_order_id") is not None:
                out[int(r["ml_order_id"])] = str(r["ml_payment_id"])
    return out


async def _fetch_parcelas_grupo(seller_slug: str, conta_id: str,
                                data_de: str, data_ate: str) -> list[dict]:
    """Busca parcelas abertas (receber+pagar) e resolve {payment_id, papel} de cada."""
    raw: list[tuple[dict, str]] = []  # (parcela, fonte)
    for fn, fonte in ((ca_api.buscar_parcelas_abertas_receber, "receber"),
                      (ca_api.buscar_parcelas_abertas_pagar, "pagar")):
        page = 1
        got = 0
        while True:
            itens, total = await fn(conta_id, data_de, data_ate, pagina=page, tamanho=50)
            for it in itens:
                raw.append((it, fonte))
            got += len(itens)
            if got >= total or not itens:
                break
            page += 1

    # resolve order→payment para receitas (descrição da receita tem order_id, não payment_id)
    order_ids: set[int] = set()
    for it, _ in raw:
        desc = it.get("descricao", "") or ""
        if not _PAYMENT_RE.search(desc):
            m = _ORDER_RE.search(desc)
            if m:
                order_ids.add(int(m.group(1)))
    order_map = await _order_to_payment_map(seller_slug, order_ids)

    parcelas: list[dict] = []
    nao_resolvidas = 0
    for it, fonte in raw:
        desc = it.get("descricao", "") or ""
        papel = _classify_papel(desc)
        if papel is None:
            continue  # parcela de outro fluxo (despesa avulsa etc.) — fora do trio
        pid: str | None = None
        m = _PAYMENT_RE.search(desc)
        if m:
            pid = m.group(1)
        else:
            mo = _ORDER_RE.search(desc)
            if mo:
                pid = order_map.get(int(mo.group(1)))
        if not pid:
            nao_resolvidas += 1
            continue
        valor = float(it.get("nao_pago", it.get("total", 0)) or 0)
        if valor < 0.01:
            continue
        parcelas.append({"id": str(it.get("id")), "payment_id": pid,
                         "papel": papel, "valor_aberto": valor,
                         "descricao": desc})
    if nao_resolvidas:
        logger.info("baixas_extrato %s: %d parcelas de venda sem payment_id resolvido",
                    seller_slug, nao_resolvidas)
    return parcelas


async def plan_for_seller(seller_slug: str, data_de: str, data_ate: str,
                          seller: dict) -> tuple[TrioPlanResult, list[dict]]:
    """Monta o plano de baixas extrato-dirigidas para o período. Não posta nada."""
    conta = seller["ca_conta_bancaria"]

    report = await _get_or_create_report(seller_slug, data_de, data_ate)
    if not report:
        raise RuntimeError(f"account_statement indisponível para {seller_slug} {data_de}..{data_ate}")
    csv_text = report.decode("utf-8") if isinstance(report, bytes) else str(report)
    _, txs = _parse_account_statement(csv_text)

    extrato_lines = []
    for t in txs:
        ttype = _normalize_text(t.get("transaction_type", ""))
        if "liberacao de dinheiro" not in ttype or "cancelada" in ttype:
            continue
        amount = float(t.get("amount", 0) or 0)
        if amount <= 0:
            continue
        extrato_lines.append({
            "ref": str(t.get("reference_id", "")),
            "net": amount,
            "date": _ddmmyyyy_to_iso(t.get("date", "")),
        })
    extrato_lines.sort(key=lambda x: x["date"])

    # janela de vencimento mais larga que o extrato: promessa pode cair fora do período
    parcelas = await _fetch_parcelas_grupo(seller_slug, conta, data_de, data_ate)

    plan = plan_baixas_trio(extrato_lines, parcelas)
    return plan, extrato_lines


async def run_for_seller(seller_slug: str, data_de: str, data_ate: str,
                         seller: dict) -> dict:
    """Planeja e (se habilitado) posta as baixas extrato-dirigidas via ca_queue."""
    plan, extrato_lines = await plan_for_seller(seller_slug, data_de, data_ate, seller)
    write_on = seller_slug in {
        s.strip() for s in settings.baixa_extrato_write_sellers.split(",") if s.strip()
    }
    conta = seller["ca_conta_bancaria"]

    posted = 0
    for b in plan.baixas:
        if not write_on:
            logger.info("[dry-run] baixa %s payment=%s papel=%s data=%s valor=%.2f",
                        b.parcela_id, b.payment_id, b.papel, b.data_pagamento, b.valor)
            continue
        payload = {
            "data_pagamento": b.data_pagamento,
            "composicao_valor": {"valor_bruto": b.valor},
            "conta_financeira": conta,
        }
        # idempotency por tranche (data+valor): liberação parcelada gera N baixas
        # legítimas na MESMA parcela — a key default {seller}:{parcela}:baixa colidiria
        await ca_queue.enqueue(
            seller_slug=seller_slug,
            job_type="baixa",
            ca_endpoint=f"{ca_api.CA_API}/v1/financeiro/eventos-financeiros/parcelas/{b.parcela_id}/baixa",
            ca_payload=payload,
            idempotency_key=f"{seller_slug}:{b.parcela_id}:baixa:{b.data_pagamento}:{int(round(b.valor * 100))}",
            group_id=f"{seller_slug}:{b.payment_id}:baixas_extrato",
            priority=30,
        )
        posted += 1

    ajustes_total = round(sum(a["valor"] for a in plan.ajustes), 2)
    residuo_total = round(sum(n["saldo"] for n in plan.nunca_baixou), 2)
    sem_parcela_total = round(sum(s["valor"] for s in plan.sem_parcela), 2)
    logger.info(
        "baixas_extrato %s [%s..%s]: %d baixas planejadas, %d postadas (write=%s); "
        "ajustes=%d (R$%.2f), nunca_baixou=%d (R$%.2f), sem_parcela=%d (R$%.2f)",
        seller_slug, data_de, data_ate, len(plan.baixas), posted, write_on,
        len(plan.ajustes), ajustes_total, len(plan.nunca_baixou), residuo_total,
        len(plan.sem_parcela), sem_parcela_total,
    )
    return {
        "seller": seller_slug, "de": data_de, "ate": data_ate,
        "liberacoes_extrato": len(extrato_lines),
        "baixas_planejadas": len(plan.baixas), "baixas_postadas": posted,
        "write": write_on,
        "ajustes": plan.ajustes, "ajustes_total": ajustes_total,
        "nunca_baixou": len(plan.nunca_baixou), "nunca_baixou_total": residuo_total,
        "sem_parcela": len(plan.sem_parcela), "sem_parcela_total": sem_parcela_total,
    }

"""RETROFIT do histórico de baixas — corrige baixas por-promessa já postadas no CA.

Antes do cutover pra baixa extrato-dirigida, o scheduler baixava pela PROMESSA
(`data_pagamento = money_release_date`, valor da parcela). Resultado: fluxo de
caixa do CA com datas/valores deslocados do banco. A API de baixas do CA expõe
PATCH (data/valor) e DELETE — então dá pra corrigir o histórico RETROATIVAMENTE:

  PATCH  → baixa cuja liberação EXISTE no extrato mas em outra data → re-data.
  DELETE → baixa de payment SEM liberação no extrato (cancelou-antes-de-liberar
           baixado indevidamente) → exclui, parcela reabre, cai na fila de exceção.
  MANUAL → casos que o script não decide sozinho (múltiplas liberações no mesmo
           payment, baixa conciliada no CA — id_reconciliacao preenchido, valor
           divergente do extrato).

`plan_retrofit` é leitura pura (GETs). `apply_retrofit` enfileira PATCH/DELETE
via ca_queue (worker estendido) — gated por `apply=True` + seller na flag
`baixa_extrato_write_sellers`. Piloto de 1 baixa antes de lote: use `limit=1`.
"""
import logging
import re
from collections import defaultdict

from app.config import settings
from app.db.supabase import get_db
from app.services import ca_api, ca_queue
from app.services.extrato_ingester import _normalize_text, _parse_account_statement
from app.services.release_report_sync import _get_or_create_report

logger = logging.getLogger(__name__)

_PAYMENT_RE = re.compile(r"Payment[:\s]+(\d{6,})")
_ORDER_RE = re.compile(r"Venda\s+M[LP]\s+#(\d+)")


def _ddmmyyyy_to_iso(d: str) -> str:
    p = (d or "").strip().split("-")
    if len(p) == 3 and len(p[0]) == 2:
        return f"{p[2]}-{p[1]}-{p[0]}"
    return (d or "").strip()[:10]


async def _liberacoes_por_ref(seller_slug: str, data_de: str, data_ate: str) -> dict[str, list]:
    """ref -> [{date, net}] das linhas de liberação do extrato."""
    report = await _get_or_create_report(seller_slug, data_de, data_ate)
    if not report:
        raise RuntimeError(f"account_statement indisponível {data_de}..{data_ate}")
    csv_text = report.decode("utf-8") if isinstance(report, bytes) else str(report)
    _, txs = _parse_account_statement(csv_text)
    out: dict[str, list] = defaultdict(list)
    for t in txs:
        ttype = _normalize_text(t.get("transaction_type", ""))
        if "liberacao de dinheiro" not in ttype or "cancelada" in ttype:
            continue
        amount = float(t.get("amount", 0) or 0)
        if amount <= 0:
            continue
        out[str(t.get("reference_id", ""))].append(
            {"date": _ddmmyyyy_to_iso(t.get("date", "")), "net": amount})
    return out


def _parcelas_baixadas(seller_slug: str, data_de: str, data_ate: str) -> list[dict]:
    """Parcelas com baixa postada pelo fluxo antigo (ca_jobs job_type=baixa completed,
    data_pagamento no período). Retorna [{parcela_id, data_pagamento, valor}]."""
    db = get_db()
    out = []
    page = 0
    while True:
        rows = db.table("ca_jobs").select("ca_endpoint, ca_payload").eq(
            "seller_slug", seller_slug
        ).eq("job_type", "baixa").eq("status", "completed").range(
            page * 1000, page * 1000 + 999
        ).execute()
        data = rows.data or []
        for r in data:
            payload = r.get("ca_payload") or {}
            d = (payload.get("data_pagamento") or "")[:10]
            if not d or d < data_de or d > data_ate:
                continue
            m = re.search(r"/parcelas/([^/]+)/baixa", r.get("ca_endpoint", "") or "")
            if not m:
                continue
            out.append({
                "parcela_id": m.group(1),
                "data_pagamento": d,
                "valor": float((payload.get("composicao_valor") or {}).get("valor_bruto", 0) or 0),
            })
        if len(data) < 1000:
            break
        page += 1
    return out


async def _payment_id_da_parcela(seller_slug: str, parcela_id: str) -> str | None:
    """Resolve payment_id pela descrição da parcela (order→payment via ledger)."""
    try:
        parcela = await ca_api.buscar_parcela(parcela_id)
    except Exception as e:  # noqa: BLE001 — parcela pode ter sido excluída
        logger.warning("retrofit: buscar_parcela(%s) falhou: %s", parcela_id, e)
        return None
    desc = (parcela.get("descricao") or "") if isinstance(parcela, dict) else ""
    m = _PAYMENT_RE.search(desc)
    if m:
        return m.group(1)
    mo = _ORDER_RE.search(desc)
    if mo:
        db = get_db()
        rows = db.table("payment_events").select("ml_payment_id").eq(
            "seller_slug", seller_slug
        ).eq("event_type", "sale_approved").eq(
            "ml_order_id", int(mo.group(1))
        ).limit(1).execute()
        if rows.data:
            return str(rows.data[0]["ml_payment_id"])
    return None


async def plan_retrofit(seller_slug: str, data_de: str, data_ate: str,
                        limit: int | None = None) -> dict:
    """Monta o plano de correção do histórico. Leitura pura (GETs no CA)."""
    liberacoes = await _liberacoes_por_ref(seller_slug, data_de, data_ate)
    baixadas = _parcelas_baixadas(seller_slug, data_de, data_ate)
    if limit:
        baixadas = baixadas[:limit]

    plano = []
    for item in baixadas:
        parcela_id = item["parcela_id"]
        pid = await _payment_id_da_parcela(seller_slug, parcela_id)
        if not pid:
            plano.append({**item, "acao": "manual", "motivo": "payment_id_nao_resolvido"})
            continue

        # baixas reais no CA (id + versao + id_reconciliacao)
        try:
            baixas_ca = await ca_api.listar_baixas(parcela_id)
        except Exception as e:  # noqa: BLE001
            plano.append({**item, "payment_id": pid, "acao": "manual",
                          "motivo": f"listar_baixas_falhou: {e}"})
            continue
        if not baixas_ca:
            plano.append({**item, "payment_id": pid, "acao": "ok",
                          "motivo": "sem_baixa_no_ca (já removida?)"})
            continue

        libs = liberacoes.get(pid, [])
        for bx in baixas_ca:
            baixa_id = bx.get("id")
            versao = bx.get("versao")
            data_atual = (bx.get("data_pagamento") or "")[:10]
            if bx.get("id_reconciliacao"):
                plano.append({**item, "payment_id": pid, "baixa_id": baixa_id,
                              "acao": "manual", "motivo": "baixa_conciliada_no_ca"})
                continue
            if not libs:
                # nenhum crédito de liberação no extrato → baixa indevida
                plano.append({**item, "payment_id": pid, "baixa_id": baixa_id,
                              "versao": versao, "acao": "delete",
                              "motivo": "sem_liberacao_no_extrato"})
                continue
            if len(libs) > 1:
                plano.append({**item, "payment_id": pid, "baixa_id": baixa_id,
                              "acao": "manual", "motivo": "multiplas_liberacoes"})
                continue
            alvo = libs[0]["date"]
            if data_atual == alvo:
                plano.append({**item, "payment_id": pid, "baixa_id": baixa_id,
                              "acao": "ok", "motivo": "data_ja_correta"})
            else:
                plano.append({**item, "payment_id": pid, "baixa_id": baixa_id,
                              "versao": versao, "acao": "patch",
                              "data_atual": data_atual, "data_alvo": alvo,
                              "motivo": "data_promessa_vs_extrato"})

    resumo = defaultdict(int)
    for p in plano:
        resumo[p["acao"]] += 1
    return {"seller": seller_slug, "de": data_de, "ate": data_ate,
            "total": len(plano), "resumo": dict(resumo), "plano": plano}


async def apply_retrofit(seller_slug: str, plan: dict) -> dict:
    """Enfileira PATCH/DELETE do plano via ca_queue. Gated: seller precisa estar
    em baixa_extrato_write_sellers. Itens manual/ok são pulados."""
    write_on = seller_slug in {
        s.strip() for s in settings.baixa_extrato_write_sellers.split(",") if s.strip()
    }
    if not write_on:
        return {"seller": seller_slug, "applied": 0,
                "error": "seller fora de baixa_extrato_write_sellers — apply bloqueado"}

    applied = 0
    for p in plan.get("plano", []):
        acao = p.get("acao")
        baixa_id = p.get("baixa_id")
        if acao == "patch" and baixa_id:
            await ca_queue.enqueue(
                seller_slug=seller_slug,
                job_type="retrofit_baixa_patch",
                ca_endpoint=f"{ca_api.CA_API}/v1/financeiro/eventos-financeiros/parcelas/baixa/{baixa_id}",
                ca_payload={"versao": p.get("versao"), "data_pagamento": p["data_alvo"]},
                idempotency_key=f"{seller_slug}:retrofit:{baixa_id}:patch:{p['data_alvo']}",
                ca_method="PATCH",
                priority=40,
            )
            applied += 1
        elif acao == "delete" and baixa_id:
            await ca_queue.enqueue(
                seller_slug=seller_slug,
                job_type="retrofit_baixa_delete",
                ca_endpoint=f"{ca_api.CA_API}/v1/financeiro/eventos-financeiros/parcelas/baixa/{baixa_id}",
                ca_payload={},
                idempotency_key=f"{seller_slug}:retrofit:{baixa_id}:delete",
                ca_method="DELETE",
                priority=40,
            )
            applied += 1
    logger.info("retrofit %s: %d jobs enfileirados", seller_slug, applied)
    return {"seller": seller_slug, "applied": applied}

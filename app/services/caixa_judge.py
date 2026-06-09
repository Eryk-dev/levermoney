"""JUIZ DE CAIXA (portão P1) — a peça que nunca existiu em produção.

Reconciliação de VALOR diária entre o extrato MP (verdade do caixa) e o lado CA.
Três verificações, da mais forte pra mais fina:

  [A] ÂNCORA do extrato: INITIAL_BALANCE + Σ(net) == FINAL_BALANCE e saldo corrido
      (PARTIAL_BALANCE) linha a linha. Prova que a fonte é internamente consistente.

  [B] SALDO ABSOLUTO: saldo-atual da conta no CA vs saldo do extrato. Drift
      acumulado é impossível de esconder — qualquer divergência histórica aparece
      aqui, hoje. (Só faz sentido pós-cutover/retrofit; antes, reporta o gap.)

  [C] CAIXA POR DIA: Σ baixas postadas no CA (ca_jobs job_type=baixa, completed,
      por data_pagamento) vs Σ linhas do extrato por dia. O portão diário:
      dia fecha quando |diff| <= tolerância; senão TRAVA (status diverged).

Não escreve nada — leitura pura. O resultado alimenta o financial_closing.
"""
import logging
from collections import defaultdict
from datetime import datetime

from app.db.supabase import get_db
from app.services import ca_api
from app.services.extrato_ingester import _normalize_text, _parse_account_statement
from app.services.release_report_sync import _get_or_create_report

logger = logging.getLogger(__name__)

TOLERANCIA_DIA_BRL = 0.01  # ao centavo


def _ddmmyyyy_to_iso(d: str) -> str:
    p = (d or "").strip().split("-")
    if len(p) == 3 and len(p[0]) == 2:
        return f"{p[2]}-{p[1]}-{p[0]}"
    return (d or "").strip()[:10]


def _anchor_check(summary: dict, txs: list[dict]) -> dict:
    """[A] O extrato fecha sozinho?"""
    soma = round(sum(float(t.get("amount", 0) or 0) for t in txs), 2)
    initial = float(summary.get("initial_balance", 0) or 0)
    final = float(summary.get("final_balance", 0) or 0)
    esperado = round(initial + soma, 2)
    diff = round(esperado - final, 2)
    drift_lines = 0
    bal = initial
    for t in txs:
        bal = round(bal + float(t.get("amount", 0) or 0), 2)
        pb = t.get("balance")
        if pb is not None and abs(bal - float(pb or 0)) > 0.01:
            drift_lines += 1
    return {
        "initial": initial, "final": final, "soma_linhas": soma,
        "esperado_final": esperado, "diff": diff,
        "ok": abs(diff) < 0.01 and drift_lines == 0,
        "drift_lines": drift_lines,
    }


def _ca_baixas_por_dia(seller_slug: str, data_de: str, data_ate: str) -> dict[str, float]:
    """Σ baixas completed no CA por data_pagamento (valor sem sinal de papel —
    o sinal vem da comparação com o extrato no nível de DECOMPOSIÇÃO; o portão
    fino por dia usa as baixas extrato-dirigidas, que já espelham o extrato)."""
    db = get_db()
    out: dict[str, float] = defaultdict(float)
    page = 0
    while True:
        rows = db.table("ca_jobs").select("ca_payload, status").eq(
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
            valor = float((payload.get("composicao_valor") or {}).get("valor_bruto", 0) or 0)
            out[d] += valor
        if len(data) < 1000:
            break
        page += 1
    return {d: round(v, 2) for d, v in out.items()}


async def judge_seller(seller_slug: str, data_de: str, data_ate: str, seller: dict) -> dict:
    """Roda o juiz P1 para um seller/período. Leitura pura."""
    conta = seller.get("ca_conta_bancaria")

    # extrato
    report = await _get_or_create_report(seller_slug, data_de, data_ate)
    if not report:
        return {"seller": seller_slug, "status": "error",
                "error": f"account_statement indisponível {data_de}..{data_ate}"}
    csv_text = report.decode("utf-8") if isinstance(report, bytes) else str(report)
    summary, txs = _parse_account_statement(csv_text)

    # [A] âncora
    anchor = _anchor_check(summary, txs)

    # extrato por dia (total e só-liberações)
    ext_dia: dict[str, float] = defaultdict(float)
    ext_dia_liberacao: dict[str, float] = defaultdict(float)
    for t in txs:
        d = _ddmmyyyy_to_iso(t.get("date", ""))
        amount = float(t.get("amount", 0) or 0)
        ext_dia[d] += amount
        ttype = _normalize_text(t.get("transaction_type", ""))
        if "liberacao de dinheiro" in ttype and "cancelada" not in ttype and amount > 0:
            ext_dia_liberacao[d] += amount

    # [B] saldo absoluto CA vs extrato
    saldo_ca = None
    saldo_diff = None
    if conta:
        try:
            s = await ca_api.saldo_atual(conta)
            saldo_ca = float(s.get("saldo", s.get("valor", 0)) or 0) if isinstance(s, dict) else None
        except Exception as e:  # noqa: BLE001 — juiz reporta, não derruba
            logger.warning("caixa_judge(%s): saldo-atual falhou: %s", seller_slug, e)
        if saldo_ca is not None:
            saldo_diff = round(saldo_ca - anchor["final"], 2)

    # [C] caixa por dia: baixas CA vs extrato
    ca_dia = _ca_baixas_por_dia(seller_slug, data_de, data_ate)
    dias = sorted(set(list(ext_dia.keys()) + list(ca_dia.keys())))
    dias = [d for d in dias if data_de <= d <= data_ate]
    por_dia = []
    dias_divergentes = 0
    for d in dias:
        e_total = round(ext_dia.get(d, 0.0), 2)
        e_lib = round(ext_dia_liberacao.get(d, 0.0), 2)
        c_baixa = round(ca_dia.get(d, 0.0), 2)
        # portão fino: baixas extrato-dirigidas devem espelhar o NET do dia.
        # Pré-cutover (baixas por-promessa) esse diff é esperadamente alto — o
        # juiz REPORTA; o gate decide com a tolerância.
        diff = round(c_baixa - e_total, 2)
        fecha = abs(diff) <= TOLERANCIA_DIA_BRL
        if not fecha:
            dias_divergentes += 1
        por_dia.append({"dia": d, "extrato_total": e_total, "extrato_liberacoes": e_lib,
                        "ca_baixas": c_baixa, "diff": diff, "fecha": fecha})

    status = "closed" if (anchor["ok"] and dias_divergentes == 0) else "diverged"
    return {
        "seller": seller_slug, "de": data_de, "ate": data_ate,
        "ran_at": datetime.now().isoformat(),
        "status": status,
        "anchor": anchor,
        "saldo_ca": saldo_ca, "saldo_extrato_final": anchor["final"], "saldo_diff": saldo_diff,
        "dias_divergentes": dias_divergentes, "dias_total": len(dias),
        "por_dia": por_dia,
    }

"""
Expenses export endpoints: XLSX/ZIP export, batches list, and confirm-import.
"""
import io
import logging
import zipfile
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.routers.admin import require_admin
from ._deps import (
    MP_CONTATO, MP_CNPJ, ML_CONTATO, ML_CNPJ,
    ConfirmImportRequest,
    _to_brt_date_str, _to_brt_iso_date,
    _get_centro_custo_name, _sanitize_path_component,
    _signed_amount, _group_rows_by_day, _safe_csv,
    _batch_tables_available, _persist_batch_metadata,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── XLSX builder ───────────────────────────────────────────────

def _build_xlsx(rows: list[dict], seller: dict, sheet_name: str) -> io.BytesIO:
    """Build an XLSX workbook from expense rows."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    headers = [
        "Data de Competencia",
        "Data de Vencimento",
        "Data de Pagamento",
        "Valor",
        "Categoria",
        "Descricao",
        "Cliente/Fornecedor",
        "CNPJ/CPF Cliente/Fornecedor",
        "Centro de Custo",
        "Observacoes",
    ]
    ws.append(headers)

    centro_custo = _get_centro_custo_name(seller)

    for r in rows:
        date_str = _to_brt_date_str(r.get("date_approved") or r.get("date_created"))
        direction = r.get("expense_direction", "expense")
        amount = float(r.get("amount") or 0)

        # Sign convention: expenses negative, income positive, transfers negative
        if direction == "income":
            valor = abs(amount)
            contato = ML_CONTATO
            cnpj = ML_CNPJ
        else:
            valor = -abs(amount)
            contato = MP_CONTATO
            cnpj = MP_CNPJ

        # Build observations
        obs_parts = []
        if r.get("payment_id"):
            obs_parts.append(f"Payment {r['payment_id']}")
        if r.get("external_reference"):
            obs_parts.append(f"Ref: {r['external_reference'][:40]}")
        if r.get("notes"):
            obs_parts.append(r["notes"])
        if r.get("auto_categorized"):
            obs_parts.append("(auto)")
        observacoes = " | ".join(obs_parts)

        ws.append([
            date_str,                          # Data de Competencia
            date_str,                          # Data de Vencimento
            date_str,                          # Data de Pagamento
            valor,                             # Valor
            r.get("ca_category") or "",        # Categoria
            r.get("description") or "",        # Descricao
            contato,                           # Cliente/Fornecedor
            cnpj,                              # CNPJ/CPF
            centro_custo,                      # Centro de Custo
            observacoes,                       # Observacoes
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ── Export XLSX/ZIP ────────────────────────────────────────────

@router.get("/{seller_slug}/export", dependencies=[Depends(require_admin)])
async def export_expenses(
    seller_slug: str,
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    status_filter: str | None = Query(None, description="Filter by status (default: all non-exported/non-imported)"),
    mark_exported: bool = Query(False, description="Mark exported rows as 'exported'"),
):
    """Generate ZIP with folder structure: EMPRESA/YYYY-MM-DD/*.xlsx.

    All non-exported rows are included:
    - expense/income -> PAGAMENTO_CONTAS.xlsx
    - transfer       -> TRANSFERENCIAS.xlsx
    If category is unknown, Categoria stays blank in XLSX.
    """
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"error": f"Seller {seller_slug} not found"}

    # Query all rows in requested status/date scope
    q = db.table("mp_expenses").select("*").eq("seller_slug", seller_slug)

    if status_filter:
        q = q.eq("status", status_filter)
    else:
        q = q.not_.in_("status", ["exported", "imported"])

    if date_from:
        q = q.gte("date_created", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        q = q.lte("date_created", f"{date_to}T23:59:59.999-03:00")

    q = q.order("date_created", desc=False)
    result = q.execute()
    rows = result.data or []

    rows_by_day = _group_rows_by_day(rows)
    batch_id = f"exp_{uuid4().hex[:24]}"

    empresa_nome = seller.get("dashboard_empresa") or seller_slug
    empresa_dir = _sanitize_path_component(empresa_nome.upper())
    written_files = 0
    manifest_rows: list[tuple[str, int, float]] = []
    payment_manifest_rows: list[tuple[str, str, int | None, float, str, str, str, str]] = []

    # Create ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for day, day_rows in rows_by_day.items():
            payment_rows = [r for r in day_rows if r.get("expense_direction") in ("expense", "income")]
            transfer_rows = [r for r in day_rows if r.get("expense_direction") == "transfer"]

            if payment_rows:
                file_path = f"{empresa_dir}/{day}/PAGAMENTO_CONTAS.xlsx"
                zf.writestr(file_path, _build_xlsx(payment_rows, seller, "PAGAMENTO_CONTAS").getvalue())
                written_files += 1
                manifest_rows.append((file_path, len(payment_rows), round(sum(_signed_amount(r) for r in payment_rows), 2)))
                for row in payment_rows:
                    payment_manifest_rows.append((
                        day,
                        "PAGAMENTO_CONTAS.xlsx",
                        row.get("payment_id"),
                        _signed_amount(row),
                        row.get("expense_direction") or "",
                        row.get("expense_type") or "",
                        row.get("ca_category") or "",
                        row.get("status") or "",
                    ))

            if transfer_rows:
                file_path = f"{empresa_dir}/{day}/TRANSFERENCIAS.xlsx"
                zf.writestr(file_path, _build_xlsx(transfer_rows, seller, "TRANSFERENCIAS").getvalue())
                written_files += 1
                manifest_rows.append((file_path, len(transfer_rows), round(sum(_signed_amount(r) for r in transfer_rows), 2)))
                for row in transfer_rows:
                    payment_manifest_rows.append((
                        day,
                        "TRANSFERENCIAS.xlsx",
                        row.get("payment_id"),
                        _signed_amount(row),
                        row.get("expense_direction") or "",
                        row.get("expense_type") or "",
                        row.get("ca_category") or "",
                        row.get("status") or "",
                    ))

        manifest_content = "arquivo,linhas,valor_total\n"
        manifest_content += f"batch_id,{batch_id},,\n"
        for path, row_count, total in manifest_rows:
            manifest_content += f"{path},{row_count},{total:.2f}\n"
        zf.writestr(f"{empresa_dir}/manifest.csv", manifest_content)

        payments_manifest = "empresa,data,arquivo,payment_id,valor,direcao,tipo,categoria,status\n"
        for day, arquivo, payment_id, valor, direcao, tipo, categoria, status in payment_manifest_rows:
            payments_manifest += (
                f"{_safe_csv(empresa_nome)},{_safe_csv(day)},{_safe_csv(arquivo)},"
                f"{_safe_csv(payment_id)},{valor:.2f},{_safe_csv(direcao)},"
                f"{_safe_csv(tipo)},{_safe_csv(categoria)},{_safe_csv(status)}\n"
            )
        zf.writestr(f"{empresa_dir}/manifest_pagamentos.csv", payments_manifest)

        if written_files == 0:
            zf.writestr(f"{empresa_dir}/README.txt", "Nenhuma linha encontrada para os filtros informados.\n")
    zip_buf.seek(0)

    # Mark as exported if requested
    if mark_exported and rows:
        ids = [r["id"] for r in rows]
        now = datetime.now().isoformat()
        # Batch update in chunks of 100
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            db.table("mp_expenses").update({
                "status": "exported",
                "exported_at": now,
                "updated_at": now,
            }).in_("id", chunk).execute()
        logger.info(f"Marked {len(ids)} expenses as exported for {seller_slug}")

    if _batch_tables_available(db):
        try:
            _persist_batch_metadata(
                db=db,
                batch_id=batch_id,
                seller_slug=seller_slug,
                company=empresa_nome,
                status="exported" if mark_exported else "generated",
                rows=rows,
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as e:
            logger.warning(f"Failed to persist batch metadata {batch_id}: {e}")
    else:
        logger.warning(
            "Batch tables not found. Exported file has batch_id but import confirmation API is disabled."
        )

    date_suffix = f"{date_from or 'all'}_{date_to or 'now'}"
    filename = f"despesas_{empresa_dir}_{date_suffix}.zip"

    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Export-Batch-Id": batch_id,
        },
    )


# ── Batches ────────────────────────────────────────────────────

@router.get("/{seller_slug}/batches", dependencies=[Depends(require_admin)])
async def list_batches(
    seller_slug: str,
    status: str | None = Query(None, description="generated|exported|imported"),
    limit: int = Query(50, ge=1, le=500),
):
    """List export/import batches for a seller."""
    db = get_db()
    if not _batch_tables_available(db):
        raise HTTPException(
            status_code=409,
            detail="Batch tables missing. Run migration to create expense_batches and expense_batch_items.",
        )

    q = db.table("expense_batches").select("*").eq("seller_slug", seller_slug).order(
        "updated_at", desc=True
    )
    if status:
        q = q.eq("status", status)
    result = q.limit(limit).execute()
    return {"seller": seller_slug, "count": len(result.data or []), "data": result.data or []}


# ── Confirm import ─────────────────────────────────────────────

@router.post("/{seller_slug}/batches/{batch_id}/confirm-import", dependencies=[Depends(require_admin)])
async def confirm_import_batch(
    seller_slug: str,
    batch_id: str,
    req: ConfirmImportRequest,
):
    """Confirm CA import for a batch (keeps row-level status untouched)."""
    db = get_db()
    if not _batch_tables_available(db):
        raise HTTPException(
            status_code=409,
            detail="Batch tables missing. Run migration to create expense_batches and expense_batch_items.",
        )

    batch = db.table("expense_batches").select("*").eq(
        "seller_slug", seller_slug
    ).eq("batch_id", batch_id).limit(1).execute()
    if not batch.data:
        raise HTTPException(status_code=404, detail="Batch not found")

    rows = db.table("expense_batch_items").select("expense_id").eq(
        "seller_slug", seller_slug
    ).eq("batch_id", batch_id).execute()
    expense_ids = [r.get("expense_id") for r in (rows.data or []) if r.get("expense_id") is not None]
    if not expense_ids:
        raise HTTPException(status_code=409, detail="Batch has no items")

    now = datetime.now().isoformat()
    imported_at = req.imported_at or now
    notes = (req.notes or "").strip()

    db.table("expense_batches").update({
        "status": "imported",
        "imported_at": imported_at,
        "notes": notes or None,
        "updated_at": now,
    }).eq("seller_slug", seller_slug).eq("batch_id", batch_id).execute()

    return {
        "ok": True,
        "seller": seller_slug,
        "batch_id": batch_id,
        "imported_rows": len(expense_ids),
        "imported_at": imported_at,
    }

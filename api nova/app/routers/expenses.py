"""
Expenses API — list, export XLSX, and stats for non-order MP payments.
Protected by admin token (same as admin router).
"""
import io
import logging
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Query, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.routers.admin import require_admin
from app.services.legacy_bridge import build_legacy_expenses_zip, run_legacy_reconciliation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/expenses", tags=["expenses"])

BRT = timezone(timedelta(hours=-3))

# Contato/CNPJ constants for XLSX
MP_CONTATO = "MERCADO PAGO"
MP_CNPJ = "10573521000191"
ML_CONTATO = "MERCADO LIVRE"
ML_CNPJ = "03007331000141"
MANUAL_EXPORTED_STATUSES = {"exported"}


def _to_brt_date_str(iso_str: str | None) -> str:
    """Convert ISO datetime to DD/MM/YYYY in BRT."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.astimezone(BRT).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso_str[:10] if iso_str else ""


def _to_brt_iso_date(iso_str: str | None) -> str:
    """Convert ISO datetime to YYYY-MM-DD in BRT."""
    if not iso_str:
        return "sem-data"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.astimezone(BRT).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return (iso_str or "")[:10] or "sem-data"


def _get_centro_custo_name(seller: dict) -> str:
    """Get human-readable centro de custo name for seller.
    Falls back to seller slug uppercased if no explicit name."""
    # The centro de custo in CA is an UUID. For XLSX we use the seller's
    # dashboard_empresa or slug as the display name.
    return (seller.get("dashboard_empresa") or seller.get("slug") or "").upper()


def _default_legacy_centro_custo(seller: dict) -> str:
    """Fallback center for legacy XLSX exports."""
    return (
        seller.get("legacy_centro_custo")
        or seller.get("dashboard_empresa")
        or (seller.get("slug") or "").upper()
        or "NETAIR"
    )


def _sanitize_path_component(value: str) -> str:
    """Return a safe ASCII-ish path component for ZIP folders."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    return cleaned.strip("._") or "SEM_NOME"


def _signed_amount(row: dict) -> float:
    """Amount using the same sign convention as XLSX export."""
    amount = float(row.get("amount") or 0)
    direction = row.get("expense_direction", "expense")
    if direction == "income":
        return abs(amount)
    return -abs(amount)


def _group_rows_by_day(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        day = _to_brt_iso_date(row.get("date_approved") or row.get("date_created"))
        grouped[day].append(row)
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def _safe_csv(value) -> str:
    text = "" if value is None else str(value)
    return text.replace(",", " ").replace("\n", " ").strip()


def _batch_tables_available(db) -> bool:
    """Check whether batch metadata tables exist."""
    try:
        db.table("expense_batches").select("batch_id").limit(1).execute()
        db.table("expense_batch_items").select("batch_id").limit(1).execute()
        return True
    except Exception:
        return False


def _persist_batch_metadata(
    db,
    batch_id: str,
    seller_slug: str,
    company: str,
    status: str,
    rows: list[dict],
    date_from: str | None,
    date_to: str | None,
):
    """Persist export batch metadata and item mapping."""
    now = datetime.now().isoformat()
    db.table("expense_batches").upsert({
        "batch_id": batch_id,
        "seller_slug": seller_slug,
        "company": company,
        "status": status,
        "rows_count": len(rows),
        "amount_total_signed": round(sum(_signed_amount(r) for r in rows), 2),
        "date_from": date_from,
        "date_to": date_to,
        "exported_at": now if status == "exported" else None,
        "updated_at": now,
    }, on_conflict="batch_id").execute()

    items = []
    for row in rows:
        items.append({
            "batch_id": batch_id,
            "seller_slug": seller_slug,
            "expense_id": row.get("id"),
            "payment_id": row.get("payment_id"),
            "expense_date": _to_brt_iso_date(row.get("date_approved") or row.get("date_created")),
            "expense_direction": row.get("expense_direction"),
            "amount_signed": _signed_amount(row),
            "status_snapshot": row.get("status"),
            "created_at": now,
        })

    for i in range(0, len(items), 500):
        chunk = items[i:i + 500]
        if chunk:
            db.table("expense_batch_items").upsert(
                chunk, on_conflict="batch_id,expense_id"
            ).execute()


# ── List expenses ──────────────────────────────────────────────

@router.get("/{seller_slug}", dependencies=[Depends(require_admin)])
async def list_expenses(
    seller_slug: str,
    status: str | None = Query(None, description="Filter by status"),
    expense_type: str | None = Query(None, description="Filter by expense_type"),
    direction: str | None = Query(None, description="Filter by expense_direction"),
    date_from: str | None = Query(None, description="Filter date_created >= YYYY-MM-DD"),
    date_to: str | None = Query(None, description="Filter date_created <= YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List mp_expenses for a seller with optional filters."""
    db = get_db()
    q = db.table("mp_expenses").select(
        "id, payment_id, expense_type, expense_direction, ca_category, "
        "auto_categorized, amount, description, business_branch, operation_type, "
        "payment_method, external_reference, febraban_code, date_created, "
        "date_approved, beneficiary_name, notes, status, exported_at, created_at"
    ).eq("seller_slug", seller_slug).order("date_created", desc=True)

    if status:
        q = q.eq("status", status)
    if expense_type:
        q = q.eq("expense_type", expense_type)
    if direction:
        q = q.eq("expense_direction", direction)
    if date_from:
        q = q.gte("date_created", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        q = q.lte("date_created", f"{date_to}T23:59:59.999-03:00")

    q = q.range(offset, offset + limit - 1)
    result = q.execute()

    return {
        "seller": seller_slug,
        "count": len(result.data or []),
        "offset": offset,
        "data": result.data or [],
    }


class ExpenseReviewUpdate(BaseModel):
    """Manual review payload for a pending expense."""
    ca_category: str | None = None
    description: str | None = None
    notes: str | None = None
    beneficiary_name: str | None = None
    expense_type: str | None = None
    expense_direction: str | None = None


@router.patch("/review/{seller_slug}/{expense_id}", dependencies=[Depends(require_admin)])
async def review_expense(
    seller_slug: str,
    expense_id: int,
    req: ExpenseReviewUpdate,
):
    """Manually classify an expense and mark it as manually_categorized."""
    db = get_db()
    existing = db.table("mp_expenses").select("*").eq(
        "seller_slug", seller_slug
    ).eq("id", expense_id).execute()

    if not existing.data:
        raise HTTPException(status_code=404, detail="Expense not found")

    row = existing.data[0]
    if row.get("status") == "exported":
        raise HTTPException(status_code=409, detail="Expense already exported")

    update_data = req.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_data["status"] = "manually_categorized"
    update_data["auto_categorized"] = False
    update_data["updated_at"] = datetime.now().isoformat()

    result = db.table("mp_expenses").update(update_data).eq("id", expense_id).execute()
    return result.data[0] if result.data else {"ok": True}


# ── Pending review summary ──────────────────────────────────────

@router.get("/{seller_slug}/pending-summary", dependencies=[Depends(require_admin)])
async def pending_review_summary(
    seller_slug: str,
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
):
    """Summary of pending_review rows grouped by day."""
    db = get_db()
    q = db.table("mp_expenses").select(
        "id, payment_id, amount, date_created, date_approved"
    ).eq("seller_slug", seller_slug).eq("status", "pending_review")

    if date_from:
        q = q.gte("date_created", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        q = q.lte("date_created", f"{date_to}T23:59:59.999-03:00")

    rows = q.order("date_created", desc=False).execute().data or []

    by_day: dict[str, dict] = {}
    for row in rows:
        day = _to_brt_iso_date(row.get("date_approved") or row.get("date_created"))
        if day not in by_day:
            by_day[day] = {"count": 0, "amount_total": 0.0, "payment_ids": []}
        by_day[day]["count"] += 1
        by_day[day]["amount_total"] += float(row.get("amount") or 0)
        if len(by_day[day]["payment_ids"]) < 20 and row.get("payment_id"):
            by_day[day]["payment_ids"].append(row["payment_id"])

    return {
        "seller": seller_slug,
        "total_pending": len(rows),
        "by_day": [
            {
                "date": day,
                "count": info["count"],
                "amount_total": round(info["amount_total"], 2),
                "payment_ids_sample": info["payment_ids"],
            }
            for day, info in sorted(by_day.items(), key=lambda item: item[0])
        ],
    }


class ConfirmImportRequest(BaseModel):
    imported_at: str | None = None
    notes: str | None = None


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


@router.get("/{seller_slug}/closing", dependencies=[Depends(require_admin)])
async def closing_status(
    seller_slug: str,
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    include_payment_ids: bool = Query(False, description="Include full payment_id lists"),
):
    """Daily closing status by company/day based on mp_expenses import status."""
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"error": f"Seller {seller_slug} not found"}

    q = db.table("mp_expenses").select(
        "payment_id, amount, expense_direction, status, date_created, date_approved"
    ).eq("seller_slug", seller_slug)

    if date_from:
        q = q.gte("date_created", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        q = q.lte("date_created", f"{date_to}T23:59:59.999-03:00")

    rows = q.order("date_created", desc=False).execute().data or []
    by_day = _group_rows_by_day(rows)
    company = seller.get("dashboard_empresa") or seller_slug

    imported_by_day: dict[str, set[int]] = defaultdict(set)
    import_source = "status_fallback"
    if _batch_tables_available(db):
        import_source = "batch_tables"
        try:
            bq = db.table("expense_batches").select("batch_id").eq(
                "seller_slug", seller_slug
            ).eq("status", "imported")
            if date_from:
                bq = bq.gte("imported_at", f"{date_from}T00:00:00.000-03:00")
            if date_to:
                bq = bq.lte("imported_at", f"{date_to}T23:59:59.999-03:00")

            batch_ids = [r["batch_id"] for r in (bq.execute().data or []) if r.get("batch_id")]
            for i in range(0, len(batch_ids), 100):
                chunk = batch_ids[i:i + 100]
                if not chunk:
                    continue
                iq = db.table("expense_batch_items").select("expense_date,payment_id").eq(
                    "seller_slug", seller_slug
                ).in_("batch_id", chunk)
                if date_from:
                    iq = iq.gte("expense_date", date_from)
                if date_to:
                    iq = iq.lte("expense_date", date_to)

                for item in iq.execute().data or []:
                    pid = item.get("payment_id")
                    day_key = item.get("expense_date")
                    if pid is None or not day_key:
                        continue
                    try:
                        imported_by_day[day_key].add(int(pid))
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            logger.warning(f"closing_status: failed to load imported batches for {seller_slug}: {e}")
            import_source = "status_fallback"

    days = []
    all_closed = True

    for day, day_rows in by_day.items():
        total_ids = {int(r["payment_id"]) for r in day_rows if r.get("payment_id") is not None}
        exported_ids = {
            int(r["payment_id"]) for r in day_rows
            if r.get("payment_id") is not None and r.get("status") in MANUAL_EXPORTED_STATUSES
        }
        imported_ids = set(imported_by_day.get(day, set()))
        if import_source == "status_fallback":
            imported_ids = set(exported_ids)
        missing_export_ids = sorted(total_ids - exported_ids)
        missing_import_ids = sorted(total_ids - imported_ids)

        total_signed = round(sum(_signed_amount(r) for r in day_rows), 2)
        exported_signed = round(sum(_signed_amount(r) for r in day_rows if r.get("status") in MANUAL_EXPORTED_STATUSES), 2)
        imported_signed = round(
            sum(_signed_amount(r) for r in day_rows if r.get("payment_id") is not None and int(r["payment_id"]) in imported_ids), 2
        )

        day_closed = len(missing_import_ids) == 0
        if not day_closed:
            all_closed = False

        day_item = {
            "date": day,
            "company": company,
            "rows_total": len(day_rows),
            "rows_exported": sum(1 for r in day_rows if r.get("status") in MANUAL_EXPORTED_STATUSES),
            "rows_imported": sum(
                1 for r in day_rows
                if r.get("payment_id") is not None and int(r["payment_id"]) in imported_ids
            ),
            "rows_not_exported": sum(1 for r in day_rows if r.get("status") not in MANUAL_EXPORTED_STATUSES),
            "rows_not_imported": sum(
                1 for r in day_rows
                if r.get("payment_id") is None or int(r["payment_id"]) not in imported_ids
            ),
            "amount_total_signed": total_signed,
            "amount_exported_signed": exported_signed,
            "amount_imported_signed": imported_signed,
            "amount_diff_export_signed": round(total_signed - exported_signed, 2),
            "amount_diff_import_signed": round(total_signed - imported_signed, 2),
            "payment_ids_total": len(total_ids),
            "payment_ids_exported": len(exported_ids),
            "payment_ids_imported": len(imported_ids),
            "payment_ids_missing_export": len(missing_export_ids),
            "payment_ids_missing_import": len(missing_import_ids),
            "payment_ids_missing_export_sample": missing_export_ids[:200],
            "payment_ids_missing_import_sample": missing_import_ids[:200],
            "closed": day_closed,
        }
        if include_payment_ids:
            day_item["payment_ids_total_list"] = sorted(total_ids)
            day_item["payment_ids_exported_list"] = sorted(exported_ids)
            day_item["payment_ids_imported_list"] = sorted(imported_ids)
            day_item["payment_ids_missing_export_list"] = missing_export_ids
            day_item["payment_ids_missing_import_list"] = missing_import_ids
        days.append(day_item)

    return {
        "seller": seller_slug,
        "company": company,
        "date_from": date_from,
        "date_to": date_to,
        "import_source": import_source,
        "days": days,
        "days_total": len(days),
        "days_closed": sum(1 for d in days if d["closed"]),
        "days_open": sum(1 for d in days if not d["closed"]),
        "all_closed": all_closed,
    }


@router.post("/{seller_slug}/legacy-export", dependencies=[Depends(require_admin)])
async def export_legacy_movements(
    seller_slug: str,
    extrato: UploadFile = File(..., description="Account statement (CSV or ZIP)"),
    dinheiro: UploadFile | None = File(None, description="Settlement report (CSV or ZIP)"),
    vendas: UploadFile | None = File(None, description="Collection report (CSV or ZIP)"),
    pos_venda: UploadFile | None = File(None, description="After-collection report (CSV or ZIP)"),
    liberacoes: UploadFile | None = File(None, description="Reserve-release report (CSV or ZIP)"),
    centro_custo: str | None = Form(None, description="Override for legacy center name in XLSX"),
):
    """
    Hybrid bridge: run legacy reconciliation and export only MP movement files.

    Output ZIP:
    - Conta Azul/PAGAMENTO_CONTAS.xlsx
    - Conta Azul/TRANSFERENCIAS.xlsx
    - Resumo/*_RESUMO.xlsx
    - Outros/*.csv
    """
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        raise HTTPException(status_code=404, detail=f"Seller {seller_slug} not found")

    centro = (centro_custo or "").strip() or _default_legacy_centro_custo(seller)

    try:
        resultado = await run_legacy_reconciliation(
            extrato=extrato,
            dinheiro=dinheiro,
            vendas=vendas,
            pos_venda=pos_venda,
            liberacoes=liberacoes,
            centro_custo=centro,
        )
        zip_buf, summary = build_legacy_expenses_zip(resultado)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("legacy-export failed for seller=%s", seller_slug)
        raise HTTPException(status_code=400, detail=f"Legacy export failed: {e}") from e

    company = _sanitize_path_component((seller.get("dashboard_empresa") or seller_slug).upper())
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"legacy_movimentos_{company}_{ts}.zip"

    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Legacy-Centro-Custo": centro,
            "X-Legacy-Pagamentos-Rows": str(summary.get("pagamentos_rows", 0)),
            "X-Legacy-Transferencias-Rows": str(summary.get("transferencias_rows", 0)),
        },
    )


# ── Stats ──────────────────────────────────────────────────────

@router.get("/{seller_slug}/stats", dependencies=[Depends(require_admin)])
async def expense_stats(
    seller_slug: str,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
):
    """Counters by expense_type, expense_direction, and status."""
    db = get_db()
    q = db.table("mp_expenses").select("expense_type, expense_direction, status, amount").eq(
        "seller_slug", seller_slug
    )
    if date_from:
        q = q.gte("date_created", f"{date_from}T00:00:00.000-03:00")
    if date_to:
        q = q.lte("date_created", f"{date_to}T23:59:59.999-03:00")

    result = q.execute()
    rows = result.data or []

    by_type = {}
    by_direction = {}
    by_status = {}
    total_amount = 0.0

    for r in rows:
        t = r.get("expense_type", "unknown")
        d = r.get("expense_direction", "unknown")
        s = r.get("status", "unknown")
        amt = float(r.get("amount") or 0)

        by_type[t] = by_type.get(t, 0) + 1
        by_direction[d] = by_direction.get(d, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1
        total_amount += amt

    return {
        "seller": seller_slug,
        "total": len(rows),
        "total_amount": round(total_amount, 2),
        "by_type": by_type,
        "by_direction": by_direction,
        "by_status": by_status,
    }


# ── XLSX Export ────────────────────────────────────────────────

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

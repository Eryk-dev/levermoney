"""
Shared dependencies for expenses sub-modules:
constants, helper functions, Pydantic models, and common imports.
"""
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import Depends
from pydantic import BaseModel

from app.db.supabase import get_db
from app.models.sellers import get_seller_config
from app.routers.admin import require_admin
from app.services import money

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))

# Contato/CNPJ constants for XLSX
MP_CONTATO = "MERCADO PAGO"
MP_CNPJ = "10573521000191"
ML_CONTATO = "MERCADO LIVRE"
ML_CNPJ = "03007331000141"
MANUAL_EXPORTED_STATUSES = {"exported"}


# ── Helper functions ──────────────────────────────────────────


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


def _is_incoming_transfer(row: dict, seller_ml_id: str = "") -> bool:
    """Return True when a transfer-direction row represents money IN."""
    expense_type = row.get("expense_type", "")
    # Deposits are always incoming
    if expense_type in ("deposit", "deposito_avulso"):
        return True
    # For payments_api rows: seller is collector → money received
    if seller_ml_id:
        rp = row.get("raw_payment") or {}
        collector = str(rp.get("collector_id") or "")
        if collector and collector == seller_ml_id:
            return True
    return False


def _compute_row_sign(row: dict, seller_ml_id: str = "") -> float:
    """Compute signed amount using unified sign convention."""
    amount = float(row.get("amount") or 0)
    direction = row.get("expense_direction", "expense")
    if direction == "income":
        return money.signed_amount("income", amount)
    if direction == "transfer":
        if _is_incoming_transfer(row, seller_ml_id):
            return money.signed_amount("transfer_in", amount)
        return money.signed_amount("transfer_out", amount)
    return money.signed_amount("expense", amount)


def _date_range_label(rows: list[dict], date_from: str | None = None, date_to: str | None = None) -> str:
    """Return DD.MM.YYYY_DD.MM.YYYY label for ZIP folder name.

    Uses date_from/date_to params when provided, otherwise computes
    min/max from row dates.
    """
    if date_from and date_to:
        d1 = datetime.strptime(date_from, "%Y-%m-%d").strftime("%d.%m.%Y")
        d2 = datetime.strptime(date_to, "%Y-%m-%d").strftime("%d.%m.%Y")
        return f"{d1}_{d2}"

    dates: list[datetime] = []
    for r in rows:
        iso = r.get("date_approved") or r.get("date_created")
        if iso:
            try:
                dates.append(datetime.fromisoformat(iso).astimezone(BRT))
            except (ValueError, TypeError):
                pass
    if not dates:
        return "sem-data"
    d1 = min(dates).strftime("%d.%m.%Y")
    d2 = max(dates).strftime("%d.%m.%Y")
    return f"{d1}_{d2}"


def _group_rows_by_day(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        day = _to_brt_iso_date(row.get("date_approved") or row.get("date_created"))
        grouped[day].append(row)
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def _batch_tables_available(db) -> bool:
    """Check whether batch metadata tables exist."""
    try:
        db.table("expense_batches").select("batch_id").limit(1).execute()
        db.table("expense_batch_items").select("batch_id").limit(1).execute()
        return True
    except Exception:
        return False


def _build_snapshot_payload(row: dict) -> dict:
    """Build a snapshot of expense fields needed for deterministic re-download."""
    rp = row.get("raw_payment") or {}
    snap: dict = {
        "date_approved": row.get("date_approved"),
        "date_created": row.get("date_created"),
        "expense_direction": row.get("expense_direction"),
        "expense_type": row.get("expense_type"),
        "status": row.get("status"),
        "amount": row.get("amount"),
        "ca_category": row.get("ca_category"),
        "description": row.get("description"),
        "payment_id": row.get("payment_id"),
        "external_reference": row.get("external_reference"),
        "notes": row.get("notes"),
        "auto_categorized": row.get("auto_categorized"),
    }
    # Preserve collector_id for transfer sign determination on re-download
    collector_id = rp.get("collector_id")
    if collector_id is not None:
        snap["raw_payment"] = {"collector_id": collector_id}
    return snap


def _persist_batch_metadata(
    db,
    batch_id: str,
    seller_slug: str,
    company: str,
    status: str,
    rows: list[dict],
    date_from: str | None,
    date_to: str | None,
    gdrive_status: str | None = None,
    seller_ml_id: str = "",
):
    """Persist export batch metadata and item mapping."""
    now = datetime.now().isoformat()
    batch_record: dict = {
        "batch_id": batch_id,
        "seller_slug": seller_slug,
        "company": company,
        "status": status,
        "rows_count": len(rows),
        "amount_total_signed": round(sum(_compute_row_sign(r, seller_ml_id) for r in rows), 2),
        "date_from": date_from,
        "date_to": date_to,
        "exported_at": now if status == "exported" else None,
        "updated_at": now,
    }
    if gdrive_status is not None:
        batch_record["gdrive_status"] = gdrive_status
        batch_record["gdrive_updated_at"] = now
    db.table("expense_batches").upsert(
        batch_record, on_conflict="batch_id"
    ).execute()

    items = []
    for row in rows:
        # payment_id column is bigint: extract numeric prefix, fallback to None
        raw_pid = str(row.get("payment_id") or "")
        try:
            ml_pid = int(raw_pid.split(":")[0])
        except (ValueError, TypeError):
            ml_pid = None

        items.append({
            "batch_id": batch_id,
            "seller_slug": seller_slug,
            "expense_id": row.get("id"),
            "payment_id": ml_pid,
            "expense_date": _to_brt_iso_date(row.get("date_approved") or row.get("date_created")),
            "expense_direction": row.get("expense_direction"),
            "amount_signed": _compute_row_sign(row, seller_ml_id),
            "status_snapshot": row.get("status"),
            "snapshot_payload": _build_snapshot_payload(row),
            "created_at": now,
        })

    for i in range(0, len(items), 500):
        chunk = items[i:i + 500]
        if chunk:
            db.table("expense_batch_items").upsert(
                chunk, on_conflict="batch_id,expense_id"
            ).execute()


def update_batch_gdrive_status(db, batch_id: str, gdrive_result: dict) -> None:
    """Update only the gdrive_* fields of an existing batch."""
    now = datetime.now().isoformat()
    status = gdrive_result.get("status", "failed")
    update: dict = {
        "gdrive_status": status,
        "gdrive_updated_at": now,
        "updated_at": now,
    }
    if status == "uploaded":
        update["gdrive_folder_link"] = gdrive_result.get("folder_link")
        update["gdrive_file_id"] = gdrive_result.get("file_id")
        update["gdrive_file_link"] = gdrive_result.get("file_link")
    elif status == "failed":
        update["gdrive_error"] = gdrive_result.get("error")
    db.table("expense_batches").update(update).eq("batch_id", batch_id).execute()


# ── Pydantic models ──────────────────────────────────────────


class ExpenseReviewUpdate(BaseModel):
    """Manual review payload for a pending expense."""
    ca_category: str | None = None
    description: str | None = None
    notes: str | None = None
    beneficiary_name: str | None = None
    expense_type: str | None = None
    expense_direction: str | None = None


class ConfirmImportRequest(BaseModel):
    imported_at: str | None = None
    notes: str | None = None

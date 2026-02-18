"""
Daily automation for legacy movement files.

Flow (per seller):
1. Request MP settlement report for a target day.
2. Download CSV report.
3. Run legacy bridge (extrato-based) to produce legacy ZIP files.
4. Upload ZIP to an external endpoint (optional).
5. Persist last run status in sync_state.
"""
import asyncio
import io
import json
import logging
import math
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pandas as pd
from fastapi import UploadFile

from app.config import settings
from app.db.supabase import get_db
from app.models.sellers import get_all_active_sellers, get_seller_config
from app.services import ml_api
from app.services.legacy_bridge import build_legacy_expenses_zip, run_legacy_reconciliation

logger = logging.getLogger(__name__)

SYNC_KEY = "legacy_daily_export"
READY_STATUSES = {
    "ready",
    "generated",
    "available",
    "success",
    "processed",
    "done",
    "completed",
    # release_report/list may return this for downloadable files
    "enabled",
}
CHECK_INTERVAL_SECONDS = 10
VALID_REPORT_EXTENSIONS = (".csv", ".zip", ".xlsx")

_sync_state_table_available: bool | None = None


def _parse_weekdays(raw: str) -> set[int]:
    weekdays: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if 0 <= value <= 6:
            weekdays.add(value)
    return weekdays


def _to_brt_day(dt: datetime | None = None) -> datetime:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=-3)))


def _default_target_day() -> str:
    return (_to_brt_day() - timedelta(days=1)).strftime("%Y-%m-%d")


def _target_window_iso(day_yyyy_mm_dd: str) -> tuple[str, str]:
    return (
        f"{day_yyyy_mm_dd}T00:00:00.000-03:00",
        f"{day_yyyy_mm_dd}T23:59:59.999-03:00",
    )


def _sync_state_available(db) -> bool:
    global _sync_state_table_available
    if _sync_state_table_available is not None:
        return _sync_state_table_available
    try:
        db.table("sync_state").select("sync_key").limit(1).execute()
        _sync_state_table_available = True
    except Exception:
        _sync_state_table_available = False
    return _sync_state_table_available


def _persist_state(db, seller_slug: str, state: dict[str, Any]) -> bool:
    if not _sync_state_available(db):
        return False
    try:
        db.table("sync_state").upsert(
            {
                "sync_key": SYNC_KEY,
                "seller_slug": seller_slug,
                "state": state,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="sync_key,seller_slug",
        ).execute()
        return True
    except Exception as e:
        logger.warning("legacy_daily_export %s: failed to persist state: %s", seller_slug, e)
        return False


def _iter_report_items(payload: Any):
    if isinstance(payload, dict):
        if any(k in payload for k in ("file_name", "filename", "name")):
            yield payload
        for key in ("results", "reports", "files", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
            elif isinstance(value, dict):
                yield from _iter_report_items(value)
        return
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item


def _item_file_name(item: dict) -> str | None:
    return item.get("file_name") or item.get("filename") or item.get("name")


def _item_status(item: dict) -> str:
    raw = item.get("status") or item.get("file_status") or item.get("report_status") or ""
    return str(raw).strip().lower()


def _item_matches_day(item: dict, target_day: str) -> bool:
    begin_date = str(item.get("begin_date") or "")[:10]
    if begin_date:
        return begin_date == target_day
    for key in ("name", "file_name"):
        value = item.get(key)
        if value and target_day in str(value):
            return True
    return False


def _pick_ready_file_name(payload: Any, target_day: str) -> str | None:
    candidates: list[tuple[int, str]] = []
    for item in _iter_report_items(payload):
        name = _item_file_name(item)
        if not name:
            continue
        lower_name = str(name).strip().lower()
        if not lower_name.endswith(VALID_REPORT_EXTENSIONS):
            continue
        status = _item_status(item)
        if status and status not in READY_STATUSES:
            continue
        if not _item_matches_day(item, target_day):
            continue
        score = 10
        if lower_name.endswith(".csv"):
            score += 5
        elif lower_name.endswith(".zip"):
            score += 3
        elif lower_name.endswith(".xlsx"):
            score += 2
        candidates.append((score, name))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _preview_payload(payload: Any, max_chars: int = 1200) -> str:
    text = str(payload)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...(truncated)"


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        number = float(text)
        return number if math.isfinite(number) else 0.0
    except ValueError:
        text = text.replace(".", "").replace(",", ".")
        try:
            number = float(text)
            return number if math.isfinite(number) else 0.0
        except ValueError:
            return 0.0


def _to_br_money(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _map_settlement_transaction_type(row: dict[str, Any]) -> str:
    tx_type = str(row.get("TRANSACTION_TYPE") or "").strip().upper()
    order_id = str(row.get("ORDER_ID") or "").strip()
    payment_method = str(row.get("PAYMENT_METHOD") or "").strip().lower()
    description = str(row.get("DESCRIPTION") or "").strip().lower()
    net_amount = _to_float(row.get("SETTLEMENT_NET_AMOUNT") or row.get("REAL_AMOUNT") or row.get("TRANSACTION_AMOUNT"))

    if tx_type == "SETTLEMENT":
        if order_id and order_id.lower() not in {"nan", "none", "null"}:
            return "Liberação de dinheiro"
        if "mercado libre" in description or "mercado livre" in description:
            return "Liberação de dinheiro"
        if payment_method in {"pix", "bank_transfer"} and abs(net_amount) > 0.01:
            return "Transferência"
        return "Pagamento de contas" if net_amount < 0 else "Liberação de dinheiro"

    if tx_type in {"DISPUTE", "MEDIATION", "RESERVE_FOR_DISPUTE"}:
        return "Dinheiro retido"
    if tx_type in {"REFUND", "REFUNDED"}:
        return "Reembolso"
    if tx_type == "CHARGEBACK":
        return "Débito por dívida Reclamações"
    if tx_type in {"WITHDRAWAL", "PAYOUT", "PAYOUTS", "MONEY_TRANSFER"}:
        return "Transferência"
    return tx_type.title() or "Outros"


def _convert_settlement_to_account_statement_csv(report_bytes: bytes) -> bytes:
    text = report_bytes.decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(text), sep=";", on_bad_lines="skip")
    if df.empty:
        return report_bytes

    required = {"SOURCE_ID", "TRANSACTION_TYPE"}
    if not required.issubset(set(df.columns)):
        return report_bytes

    running_balance = 0.0
    rows = []
    for _, raw in df.iterrows():
        row = raw.to_dict()
        date_val = (
            row.get("SETTLEMENT_DATE")
            or row.get("TRANSACTION_DATE")
            or row.get("MONEY_RELEASE_DATE")
            or ""
        )
        release_date = str(date_val)[:10]
        if not release_date:
            continue

        reference_id = str(row.get("SOURCE_ID") or "").replace(".0", "").strip()
        if not reference_id:
            continue

        net_amount = _to_float(
            row.get("SETTLEMENT_NET_AMOUNT")
            if row.get("SETTLEMENT_NET_AMOUNT") is not None
            else row.get("REAL_AMOUNT")
        )
        if abs(net_amount) < 0.0001:
            continue

        tx_type = _map_settlement_transaction_type(row)
        running_balance += net_amount
        rows.append(
            (
                release_date,
                tx_type,
                reference_id,
                _to_br_money(net_amount),
                _to_br_money(running_balance),
            )
        )

    credits = sum(_to_float(r[3].replace(",", ".")) for r in rows if _to_float(r[3].replace(",", ".")) > 0)
    debits = abs(sum(_to_float(r[3].replace(",", ".")) for r in rows if _to_float(r[3].replace(",", ".")) < 0))
    final_balance = credits - debits

    out_lines = [
        "INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE",
        f"0,00;{_to_br_money(credits)};{_to_br_money(debits)};{_to_br_money(final_balance)}",
        "",
        "RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE",
    ]
    for r in rows:
        out_lines.append(";".join(r))

    return ("\n".join(out_lines) + "\n").encode("utf-8")


def _normalize_release_date(date_val: Any) -> str:
    raw = str(date_val or "").strip()
    if not raw:
        return ""

    day = raw[:10]
    # YYYY-MM-DD -> DD-MM-YYYY
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        return f"{day[8:10]}-{day[5:7]}-{day[0:4]}"
    return day


def _convert_release_to_account_statement_csv(report_bytes: bytes) -> bytes:
    """Convert release_report raw CSV into account_statement layout expected by legacy engine."""
    def _clean_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text or text.lower() in {"nan", "none", "null"}:
            return ""
        return text

    text = report_bytes.decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(text), sep=";", on_bad_lines="skip")
    if df.empty:
        return report_bytes

    required = {"DATE", "SOURCE_ID", "DESCRIPTION", "NET_CREDIT_AMOUNT", "NET_DEBIT_AMOUNT"}
    if not required.issubset(set(df.columns)):
        return report_bytes

    running_balance = 0.0
    rows = []
    for _, raw in df.iterrows():
        row = raw.to_dict()
        description = _clean_text(row.get("DESCRIPTION")).lower()
        if description in {"initial_available_balance", "total"}:
            continue

        release_date = _normalize_release_date(row.get("DATE"))
        if not release_date:
            continue

        reference_id = _clean_text(row.get("SOURCE_ID")).replace(".0", "")
        if not reference_id:
            continue

        net_credit = _to_float(row.get("NET_CREDIT_AMOUNT"))
        net_debit = _to_float(row.get("NET_DEBIT_AMOUNT"))
        net_amount = net_credit - net_debit
        if abs(net_amount) < 0.0001:
            continue

        # Normalize to labels already handled by legacy_engine.
        if description == "payment":
            tx_type = "Liberação de dinheiro" if net_amount > 0 else "Transferência"
        elif description == "mediation":
            tx_type = "Débito por dívida Reclamações no Mercado Livre"
        elif description == "refund":
            tx_type = "Reembolso Reclamações e devoluções"
        elif description == "reserve_for_dispute":
            tx_type = "Dinheiro retido Reclamações e devoluções"
        elif description in {"payout", "money_transfer"}:
            tx_type = "Transferência"
        elif description in {"shipping", "cashback", "mediation_cancel"}:
            tx_type = "Dinheiro recebido"
        elif description in {"reserve_for_bpp_shipping_return", "reserve_for_bpp_shipping_retur"}:
            tx_type = (
                "Reembolso Envío cancelado"
                if net_amount > 0
                else "Débito por dívida Envio do Mercado Livre"
            )
        else:
            tx_type = description or "Outros"

        running_balance += net_amount
        rows.append(
            (
                release_date,
                tx_type,
                reference_id,
                _to_br_money(net_amount),
                _to_br_money(running_balance),
            )
        )

    credits = sum(_to_float(r[3].replace(",", ".")) for r in rows if _to_float(r[3].replace(",", ".")) > 0)
    debits = abs(sum(_to_float(r[3].replace(",", ".")) for r in rows if _to_float(r[3].replace(",", ".")) < 0))
    final_balance = credits - debits

    out_lines = [
        "INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE",
        f"0,00;{_to_br_money(credits)};{_to_br_money(debits)};{_to_br_money(final_balance)}",
        "",
        "RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE",
    ]
    for r in rows:
        out_lines.append(";".join(r))

    return ("\n".join(out_lines) + "\n").encode("utf-8")


def _ensure_account_statement_csv(report_bytes: bytes) -> bytes:
    text = report_bytes.decode("utf-8", errors="replace")
    first_line = text.splitlines()[0] if text else ""
    normalized_first_line = first_line.lstrip("\ufeff").strip()
    if "RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE" in text:
        return report_bytes

    # release_report raw layout (DATE;SOURCE_ID;...;RECORD_TYPE;DESCRIPTION;NET_CREDIT_AMOUNT;NET_DEBIT_AMOUNT;...)
    if normalized_first_line.upper().startswith("DATE;SOURCE_ID;EXTERNAL_REFERENCE;RECORD_TYPE;DESCRIPTION;"):
        return _convert_release_to_account_statement_csv(report_bytes)

    # Settlement layout variant 1: EXTERNAL_REFERENCE;SOURCE_ID;...
    if normalized_first_line.upper().startswith("EXTERNAL_REFERENCE;SOURCE_ID;"):
        return _convert_settlement_to_account_statement_csv(report_bytes)

    # Settlement layout variant 2 (observed in production):
    # SOURCE_ID;...;TRANSACTION_TYPE;...;REAL_AMOUNT / TRANSACTION_AMOUNT
    header_cols = [c.strip().upper() for c in normalized_first_line.split(";") if c.strip()]
    header_set = set(header_cols)
    looks_like_settlement = (
        {"SOURCE_ID", "TRANSACTION_TYPE"}.issubset(header_set)
        and (
            "REAL_AMOUNT" in header_set
            or "SETTLEMENT_NET_AMOUNT" in header_set
            or "TRANSACTION_AMOUNT" in header_set
        )
    )
    if looks_like_settlement:
        return _convert_settlement_to_account_statement_csv(report_bytes)

    return report_bytes


def _load_gdrive_service_account_info() -> dict[str, Any]:
    raw_json = (settings.legacy_daily_google_service_account_json or "").strip()
    if raw_json:
        return json.loads(raw_json)

    file_path = (settings.legacy_daily_google_service_account_file or "").strip()
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    raise RuntimeError(
        "Google Drive credentials missing. Configure LEGACY_DAILY_GOOGLE_SERVICE_ACCOUNT_JSON or "
        "LEGACY_DAILY_GOOGLE_SERVICE_ACCOUNT_FILE."
    )


def _build_gdrive_client():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "Google Drive dependencies missing. Install google-api-python-client and google-auth."
        ) from e

    info = _load_gdrive_service_account_info()
    scopes = ["https://www.googleapis.com/auth/drive"]
    credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _gdrive_escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("'", "\\'")


def _gdrive_list_files(service, parent_id: str, name: str, drive_id: str = "") -> list[dict]:
    q = (
        f"name = '{_gdrive_escape(name)}' and '{parent_id}' in parents and trashed = false"
    )
    kwargs: dict[str, Any] = {
        "q": q,
        "fields": "files(id,name,mimeType)",
        "pageSize": 50,
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }
    if drive_id:
        kwargs["driveId"] = drive_id
        kwargs["corpora"] = "drive"
    resp = service.files().list(**kwargs).execute()
    return resp.get("files", [])


def _gdrive_ensure_folder(service, parent_id: str, name: str, drive_id: str = "") -> str:
    folder_mime = "application/vnd.google-apps.folder"
    existing = [
        f for f in _gdrive_list_files(service, parent_id, name, drive_id=drive_id)
        if f.get("mimeType") == folder_mime
    ]
    if existing:
        return existing[0]["id"]

    metadata = {
        "name": name,
        "mimeType": folder_mime,
        "parents": [parent_id],
    }
    created = service.files().create(
        body=metadata,
        fields="id,name",
        supportsAllDrives=True,
    ).execute()
    return created["id"]


def _gdrive_remove_existing_file(service, parent_id: str, name: str, drive_id: str = "") -> None:
    existing = [
        f for f in _gdrive_list_files(service, parent_id, name, drive_id=drive_id)
        if f.get("mimeType") != "application/vnd.google-apps.folder"
    ]
    for item in existing:
        service.files().delete(fileId=item["id"], supportsAllDrives=True).execute()


def _gdrive_upload_bytes(
    service,
    parent_id: str,
    name: str,
    content: bytes,
    mimetype: str,
    drive_id: str = "",
) -> dict[str, Any]:
    from googleapiclient.http import MediaIoBaseUpload

    _gdrive_remove_existing_file(service, parent_id, name, drive_id=drive_id)

    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype, resumable=False)
    metadata = {"name": name, "parents": [parent_id]}
    uploaded = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()
    return uploaded


def _extract_xlsx_targets(zip_bytes: bytes) -> dict[str, bytes]:
    targets = {
        "PAGAMENTO_CONTAS.xlsx": None,
        "TRANSFERENCIAS.xlsx": None,
    }
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = zf.namelist()
        for target in list(targets):
            preferred = f"Conta Azul/{target}"
            if preferred in names:
                targets[target] = zf.read(preferred)
                continue
            for name in names:
                if name.split("/")[-1].upper() == target.upper():
                    targets[target] = zf.read(name)
                    break
    return {name: content for name, content in targets.items() if content}


def _upload_to_gdrive(
    *,
    seller_slug: str,
    seller: dict | None,
    target_day: str,
    zip_bytes: bytes,
) -> dict[str, Any]:
    root_id = (settings.legacy_daily_google_drive_root_folder_id or "").strip()
    if not root_id:
        return {"enabled": True, "performed": False, "status": "skipped_no_drive_root"}

    files = _extract_xlsx_targets(zip_bytes)
    if not files:
        return {"enabled": True, "performed": False, "status": "skipped_no_xlsx"}

    drive_id = (settings.legacy_daily_google_drive_id or "").strip()
    company = (seller or {}).get("dashboard_empresa") or seller_slug
    month_folder = target_day[:7]

    service = _build_gdrive_client()
    company_id = _gdrive_ensure_folder(service, root_id, company, drive_id=drive_id)
    month_id = _gdrive_ensure_folder(service, company_id, month_folder, drive_id=drive_id)
    day_id = _gdrive_ensure_folder(service, month_id, target_day, drive_id=drive_id)

    uploaded = []
    for file_name, content in files.items():
        item = _gdrive_upload_bytes(
            service=service,
            parent_id=day_id,
            name=file_name,
            content=content,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            drive_id=drive_id,
        )
        uploaded.append(
            {
                "name": item.get("name"),
                "id": item.get("id"),
                "webViewLink": item.get("webViewLink"),
                "size_bytes": len(content),
            }
        )

    return {
        "enabled": True,
        "performed": True,
        "status": "uploaded",
        "provider": "gdrive",
        "company_folder": company,
        "month_folder": month_folder,
        "day_folder": target_day,
        "uploaded_files": uploaded,
    }


async def _upload_to_http(
    *,
    seller_slug: str,
    target_day: str,
    zip_bytes: bytes,
    filename: str,
) -> dict[str, Any]:
    upload_url = (settings.legacy_daily_upload_url or "").strip()
    if not upload_url:
        return {"enabled": True, "performed": False, "status": "skipped_no_url"}

    headers = {}
    token = (settings.legacy_daily_upload_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = {
        "seller_slug": seller_slug,
        "date": target_day,
        "source": "legacy_daily_export",
    }
    files = {
        "file": (filename, zip_bytes, "application/zip"),
    }

    timeout = max(30, int(settings.legacy_daily_upload_timeout_seconds or 120))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(upload_url, headers=headers, data=data, files=files)

    return {
        "enabled": True,
        "performed": True,
        "status": "uploaded" if 200 <= response.status_code < 300 else "failed",
        "provider": "http",
        "http_status": response.status_code,
        "response_preview": (response.text or "")[:300],
    }


async def _upload_zip_if_configured(
    *,
    seller_slug: str,
    seller: dict | None,
    target_day: str,
    zip_bytes: bytes,
    filename: str,
    upload_enabled: bool,
) -> dict[str, Any]:
    if not upload_enabled:
        return {"enabled": False, "performed": False, "status": "disabled"}
    mode = (settings.legacy_daily_upload_mode or "http").strip().lower()

    if mode == "gdrive":
        try:
            return _upload_to_gdrive(
                seller_slug=seller_slug,
                seller=seller,
                target_day=target_day,
                zip_bytes=zip_bytes,
            )
        except Exception as e:
            logger.error("legacy_daily_export %s: gdrive upload failed: %s", seller_slug, e, exc_info=True)
            return {
                "enabled": True,
                "performed": False,
                "status": "failed",
                "provider": "gdrive",
                "error": str(e),
            }

    return await _upload_to_http(
        seller_slug=seller_slug,
        target_day=target_day,
        zip_bytes=zip_bytes,
        filename=filename,
    )


async def run_legacy_daily_for_seller(
    seller_slug: str,
    *,
    target_day: str | None = None,
    upload: bool = True,
) -> dict[str, Any]:
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"seller": seller_slug, "ok": False, "error": "seller_not_found"}

    day = target_day or _default_target_day()
    begin_date, end_date = _target_window_iso(day)
    wait_seconds = max(60, int(settings.legacy_daily_report_wait_seconds or 300))

    started_at = datetime.now(timezone.utc).isoformat()
    requested = None
    file_name = None
    report_source = "release_report"
    list_reports_fn = ml_api.list_release_reports
    download_report_fn = ml_api.download_release_report

    try:
        requested = await ml_api.create_release_report(
            seller_slug=seller_slug,
            begin_date=begin_date,
            end_date=end_date,
        )
    except Exception as release_error:
        logger.warning(
            "legacy_daily_export %s: create_release_report failed, fallback to settlement_report: %s",
            seller_slug,
            release_error,
        )
        try:
            requested = await ml_api.create_settlement_report(
                seller_slug=seller_slug,
                begin_date=begin_date,
                end_date=end_date,
                file_format="csv",
            )
            report_source = "settlement_report"
            list_reports_fn = ml_api.list_settlement_reports
            download_report_fn = ml_api.download_settlement_report
        except Exception as settlement_error:
            state = {
                "ok": False,
                "target_day": day,
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "report_source": "release_report",
                "error": f"create_report_failed: release={release_error} | settlement={settlement_error}",
            }
            _persist_state(db, seller_slug, state)
            return {"seller": seller_slug, **state}

    file_name = _pick_ready_file_name(requested, day)
    elapsed = 0
    last_list_payload = None

    while not file_name and elapsed < wait_seconds:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        elapsed += CHECK_INTERVAL_SECONDS
        try:
            last_list_payload = await list_reports_fn(seller_slug, limit=50)
            file_name = _pick_ready_file_name(last_list_payload, day)
        except Exception as e:
            logger.warning(
                "legacy_daily_export %s: list reports error (%s): %s",
                seller_slug,
                report_source,
                e,
            )

    if not file_name:
        state = {
            "ok": False,
            "target_day": day,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "report_source": report_source,
            "error": "report_not_ready_or_not_found",
            "wait_seconds": wait_seconds,
            "request_payload_preview": _preview_payload(requested),
            "last_list_payload_preview": _preview_payload(last_list_payload),
        }
        _persist_state(db, seller_slug, state)
        return {"seller": seller_slug, **state}

    try:
        report_bytes = await download_report_fn(seller_slug, file_name)
        extrato_bytes = _ensure_account_statement_csv(report_bytes)
        extrato_upload = UploadFile(file=io.BytesIO(extrato_bytes), filename=file_name)
        centro = (
            seller.get("legacy_centro_custo")
            or settings.legacy_daily_default_centro_custo
            or seller.get("dashboard_empresa")
            or (seller_slug or "").upper()
        )
        resultado = await run_legacy_reconciliation(
            extrato=extrato_upload,
            centro_custo=centro,
        )
        zip_buf, summary = build_legacy_expenses_zip(resultado)
        zip_bytes = zip_buf.getvalue()
        zip_filename = f"legacy_movimentos_{seller_slug}_{day}.zip"

        upload_info = await _upload_zip_if_configured(
            seller_slug=seller_slug,
            seller=seller,
            target_day=day,
            zip_bytes=zip_bytes,
            filename=zip_filename,
            upload_enabled=upload,
        )

        state = {
            "ok": True,
            "target_day": day,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "report_source": report_source,
            "report_file_name": file_name,
            "report_size_bytes": len(report_bytes),
            "extrato_size_bytes": len(extrato_bytes),
            "zip_size_bytes": len(zip_bytes),
            "pagamentos_rows": summary.get("pagamentos_rows", 0),
            "transferencias_rows": summary.get("transferencias_rows", 0),
            "files": summary.get("files", []),
            "upload": upload_info,
        }
        _persist_state(db, seller_slug, state)
        return {"seller": seller_slug, **state}
    except Exception as e:
        state = {
            "ok": False,
            "target_day": day,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "report_source": report_source,
            "report_file_name": file_name,
            "error": f"process_or_upload_failed: {e}",
        }
        _persist_state(db, seller_slug, state)
        return {"seller": seller_slug, **state}


async def run_legacy_daily_for_all(
    *,
    target_day: str | None = None,
    upload: bool = True,
) -> list[dict[str, Any]]:
    db = get_db()
    sellers = get_all_active_sellers(db)
    day = target_day or _default_target_day()

    results = []
    for seller in sellers:
        slug = seller["slug"]
        try:
            result = await run_legacy_daily_for_seller(slug, target_day=day, upload=upload)
            results.append(result)
        except Exception as e:
            logger.error("legacy_daily_export all-sellers error for %s: %s", slug, e, exc_info=True)
            results.append({
                "seller": slug,
                "ok": False,
                "target_day": day,
                "error": str(e),
            })
        await asyncio.sleep(1)
    return results


def get_legacy_daily_status(seller_slug: str | None = None) -> dict[str, Any]:
    db = get_db()
    if not _sync_state_available(db):
        return {"available": False, "detail": "sync_state table not available"}

    q = db.table("sync_state").select("seller_slug,state,updated_at").eq("sync_key", SYNC_KEY)
    if seller_slug:
        q = q.eq("seller_slug", seller_slug)
    rows = q.order("updated_at", desc=True).limit(200).execute().data or []
    return {
        "available": True,
        "count": len(rows),
        "rows": rows,
    }


async def _legacy_daily_scheduler():
    """Run legacy daily export once per day at configured BRT time."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    brt = ZoneInfo("America/Sao_Paulo")
    target_hour = max(0, min(23, int(settings.legacy_daily_hour_brt)))
    target_minute = max(0, min(59, int(settings.legacy_daily_minute_brt)))
    allowed_weekdays = _parse_weekdays(settings.nightly_pipeline_legacy_weekdays)
    if not allowed_weekdays:
        allowed_weekdays = {0, 3}

    # On startup, if already past target time, run catch-up immediately.
    now_brt = datetime.now(brt)
    if (now_brt.hour, now_brt.minute) >= (target_hour, target_minute):
        if now_brt.weekday() in allowed_weekdays:
            target_day = (now_brt - timedelta(days=1)).strftime("%Y-%m-%d")
            logger.info("LegacyDaily scheduler: past configured time, running catch-up for %s", target_day)
            results = await run_legacy_daily_for_all(target_day=target_day, upload=True)
            logger.info("LegacyDaily catch-up done: sellers=%s", len(results))
        else:
            logger.info(
                "LegacyDaily scheduler: startup catch-up skipped (weekday=%s, allowed=%s)",
                now_brt.weekday(),
                sorted(allowed_weekdays),
            )

    while True:
        now_brt = datetime.now(brt)
        if (now_brt.hour, now_brt.minute) < (target_hour, target_minute):
            target = now_brt.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        else:
            target = (now_brt + timedelta(days=1)).replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )

        wait_seconds = (target - now_brt).total_seconds()
        logger.info("LegacyDaily scheduler: next run in %.0fs (%s)", wait_seconds, target.isoformat())
        await asyncio.sleep(wait_seconds)

        target_day = (datetime.now(brt) - timedelta(days=1)).strftime("%Y-%m-%d")
        run_weekday = datetime.now(brt).weekday()
        if run_weekday not in allowed_weekdays:
            logger.info(
                "LegacyDaily skipped for %s (weekday=%s, allowed=%s)",
                target_day,
                run_weekday,
                sorted(allowed_weekdays),
            )
            continue
        try:
            results = await run_legacy_daily_for_all(target_day=target_day, upload=True)
            logger.info("LegacyDaily done for %s: sellers=%s", target_day, len(results))
        except Exception as e:
            logger.error("LegacyDaily scheduler run error: %s", e, exc_info=True)

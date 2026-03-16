"""
Public GDrive client for uploading files to Google Drive.

Reuses internal GDrive helpers from the legacy daily export module.
Expenses:  ROOT -> DESPESAS -> {EMPRESA} -> {YYYY-MM}
Extratos:  ROOT -> EXTRATOS -> {EMPRESA} -> {YYYY-MM}.csv
"""
import logging
from typing import Any

from app.config import settings
from app.services.legacy.daily_export import (
    _build_gdrive_client,
    _gdrive_ensure_folder,
    _gdrive_upload_bytes,
)

logger = logging.getLogger(__name__)

EXPENSES_ROOT_FOLDER_NAME = "DESPESAS"
EXTRATOS_ROOT_FOLDER_NAME = "EXTRATOS"


def _derive_month_folder(date_from: str | None, date_to: str | None) -> str:
    """Extract YYYY-MM from date_from or date_to, fallback to current month."""
    for raw in (date_from, date_to):
        if raw and len(raw) >= 7:
            return raw[:7]
    from datetime import datetime, timezone, timedelta

    now_brt = datetime.now(timezone(timedelta(hours=-3)))
    return now_brt.strftime("%Y-%m")


def upload_expenses_zip(
    seller_slug: str,
    seller: dict | None,
    zip_bytes: bytes,
    date_from: str | None,
    date_to: str | None,
    filename: str,
) -> dict[str, Any]:
    """Upload an expenses ZIP to Google Drive.

    Returns a dict with at least a ``status`` key:
    - ``skipped_no_drive_root`` – root folder not configured, nothing uploaded.
    - ``uploaded`` – file uploaded successfully (includes folder_link, file_id, file_link).
    - ``failed`` – an error occurred (includes error message).
    """
    root_id = (settings.legacy_daily_google_drive_root_folder_id or "").strip()
    if not root_id:
        return {"status": "skipped_no_drive_root"}

    drive_id = (settings.legacy_daily_google_drive_id or "").strip()
    company = (seller or {}).get("dashboard_empresa") or seller_slug
    month_folder = _derive_month_folder(date_from, date_to)

    try:
        service = _build_gdrive_client()

        despesas_id = _gdrive_ensure_folder(
            service, root_id, EXPENSES_ROOT_FOLDER_NAME, drive_id=drive_id,
        )
        company_id = _gdrive_ensure_folder(
            service, despesas_id, company, drive_id=drive_id,
        )
        month_id = _gdrive_ensure_folder(
            service, company_id, month_folder, drive_id=drive_id,
        )

        uploaded = _gdrive_upload_bytes(
            service=service,
            parent_id=month_id,
            name=filename,
            content=zip_bytes,
            mimetype="application/zip",
            drive_id=drive_id,
        )

        folder_link = f"https://drive.google.com/drive/folders/{month_id}"
        file_id = uploaded.get("id", "")
        file_link = uploaded.get("webViewLink", "")

        logger.info(
            "gdrive_client: uploaded expenses ZIP for %s → %s/%s/%s (%s)",
            seller_slug,
            EXPENSES_ROOT_FOLDER_NAME,
            company,
            month_folder,
            filename,
        )

        return {
            "status": "uploaded",
            "folder_link": folder_link,
            "file_id": file_id,
            "file_link": file_link,
        }
    except Exception as e:
        logger.error(
            "gdrive_client: upload failed for %s: %s", seller_slug, e, exc_info=True,
        )
        return {"status": "failed", "error": str(e)}


def upload_extrato_csv(
    seller_slug: str,
    seller: dict | None,
    csv_bytes: bytes,
    month: str,
    filename: str,
) -> dict[str, Any]:
    """Upload an extrato CSV to Google Drive.

    Hierarchy: ROOT -> EXTRATOS -> {EMPRESA} -> {YYYY-MM}.csv

    Returns a dict with at least a ``status`` key:
    - ``skipped_no_drive_root`` – root folder not configured.
    - ``uploaded`` – file uploaded successfully.
    - ``failed`` – an error occurred.
    """
    root_id = (settings.legacy_daily_google_drive_root_folder_id or "").strip()
    if not root_id:
        return {"status": "skipped_no_drive_root"}

    drive_id = (settings.legacy_daily_google_drive_id or "").strip()
    company = (seller or {}).get("dashboard_empresa") or seller_slug

    try:
        service = _build_gdrive_client()

        extratos_id = _gdrive_ensure_folder(
            service, root_id, EXTRATOS_ROOT_FOLDER_NAME, drive_id=drive_id,
        )
        company_id = _gdrive_ensure_folder(
            service, extratos_id, company, drive_id=drive_id,
        )

        uploaded = _gdrive_upload_bytes(
            service=service,
            parent_id=company_id,
            name=filename,
            content=csv_bytes,
            mimetype="text/csv",
            drive_id=drive_id,
        )

        folder_link = f"https://drive.google.com/drive/folders/{company_id}"
        file_id = uploaded.get("id", "")
        file_link = uploaded.get("webViewLink", "")

        logger.info(
            "gdrive_client: uploaded extrato CSV for %s → %s/%s/%s",
            seller_slug,
            EXTRATOS_ROOT_FOLDER_NAME,
            company,
            filename,
        )

        return {
            "status": "uploaded",
            "folder_link": folder_link,
            "file_id": file_id,
            "file_link": file_link,
        }
    except Exception as e:
        logger.error(
            "gdrive_client: extrato upload failed for %s: %s",
            seller_slug, e, exc_info=True,
        )
        return {"status": "failed", "error": str(e)}

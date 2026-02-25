"""
Extrato Onboarding — validate and process an uploaded account_statement CSV
during seller onboarding (dashboard_ca activation / upgrade-to-ca).

Provides:
  validate_extrato_period  — checks that the CSV covers ca_start_date through D-1
  process_onboarding_extrato — processes CSV into mp_expenses gap lines
  upload_extrato_to_drive   — stores the raw CSV in Google Drive for audit
"""
import logging
from datetime import datetime, timedelta, timezone

from app.services.extrato_ingester import parse_account_statement, process_extrato_csv_text

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))


def _decode_csv_bytes(csv_bytes: bytes) -> str:
    """Decode CSV bytes trying utf-8-sig first, then latin-1 fallback."""
    try:
        return csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return csv_bytes.decode("latin-1")


def validate_extrato_period(
    csv_bytes: bytes,
    ca_start_date: str,
) -> tuple[bool, str, dict]:
    """Validate that the uploaded extrato CSV covers the required period.

    Checks that the CSV transactions span from ca_start_date through D-1 (yesterday
    in BRT). Does not verify balance reconciliation — only date range coverage.

    Args:
        csv_bytes:     Raw bytes of the uploaded account_statement CSV.
        ca_start_date: ISO date string YYYY-MM-DD (seller's CA activation start).

    Returns:
        Tuple of (is_valid, error_message, info_dict).
        info_dict has keys: min_date, max_date, line_count, summary.
        error_message is empty string when is_valid is True.
    """
    csv_text = _decode_csv_bytes(csv_bytes)
    summary, transactions = parse_account_statement(csv_text)

    yesterday = (datetime.now(BRT) - timedelta(days=1)).strftime("%Y-%m-%d")

    if not transactions:
        return (
            False,
            "Nenhuma transação encontrada no extrato. Verifique se o arquivo está correto.",
            {"min_date": None, "max_date": None, "line_count": 0, "summary": summary},
        )

    dates = [tx["date"] for tx in transactions]
    min_date = min(dates)
    max_date = max(dates)
    line_count = len(transactions)

    info: dict = {
        "min_date": min_date,
        "max_date": max_date,
        "line_count": line_count,
        "summary": summary,
    }

    if min_date > ca_start_date:
        return (
            False,
            (
                f"Extrato começa em {min_date} mas ca_start_date é {ca_start_date}. "
                f"Faltam dados de {ca_start_date} a {_day_before(min_date)}."
            ),
            info,
        )

    if max_date < yesterday:
        return (
            False,
            (
                f"Extrato vai até {max_date} mas é necessário cobrir até {yesterday} (D-1). "
                f"Faltam dados de {_day_after(max_date)} a {yesterday}."
            ),
            info,
        )

    return True, "", info


def _day_before(iso_date: str) -> str:
    """Return the ISO date string for the day before the given date."""
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")


def _day_after(iso_date: str) -> str:
    """Return the ISO date string for the day after the given date."""
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    return (dt + timedelta(days=1)).strftime("%Y-%m-%d")


def process_onboarding_extrato(
    seller_slug: str,
    csv_bytes: bytes,
    ca_start_date: str,
) -> dict:
    """Process an uploaded extrato CSV into mp_expenses gap lines for onboarding.

    Decodes the CSV bytes and delegates to the shared process_extrato_csv_text
    function, using ca_start_date as begin_date and yesterday as end_date.

    Args:
        seller_slug:   Seller identifier.
        csv_bytes:     Raw bytes of the uploaded account_statement CSV.
        ca_start_date: ISO date string YYYY-MM-DD (seller's CA activation start).

    Returns:
        Stats dict with keys: seller, total_lines, skipped_internal,
        already_covered, newly_ingested, errors, by_type, summary.
    """
    csv_text = _decode_csv_bytes(csv_bytes)
    yesterday = (datetime.now(BRT) - timedelta(days=1)).strftime("%Y-%m-%d")
    return process_extrato_csv_text(seller_slug, csv_text, ca_start_date, yesterday)


def upload_extrato_to_drive(
    seller_slug: str,
    csv_bytes: bytes,
    ca_start_date: str,
    end_date: str,
) -> "dict | None":
    """Upload the extrato CSV to Google Drive for audit purposes.

    Organises files under: <root>/onboarding/<seller_slug>/extrato_<start>_to_<end>.csv

    Args:
        seller_slug:   Seller identifier (used as subfolder name).
        csv_bytes:     Raw bytes of the CSV to store.
        ca_start_date: ISO date string YYYY-MM-DD (period start, used in filename).
        end_date:      ISO date string YYYY-MM-DD (period end, used in filename).

    Returns:
        Dict with keys file_id and web_view_link if upload succeeds, else None.
    """
    try:
        from app.config import settings
        from app.services.legacy_daily_export import (
            _build_gdrive_client,
            _gdrive_ensure_folder,
            _gdrive_upload_bytes,
        )
    except ImportError as exc:
        logger.error("upload_extrato_to_drive: import error — %s", exc, exc_info=True)
        return None

    root_folder_id = settings.legacy_daily_google_drive_root_folder_id
    if not root_folder_id:
        logger.warning(
            "upload_extrato_to_drive %s: Google Drive not configured, skipping extrato upload",
            seller_slug,
        )
        return None

    try:
        drive_id = settings.legacy_daily_google_drive_id or ""
        service = _build_gdrive_client()

        # Ensure folder chain: root → "onboarding" → seller_slug
        onboarding_folder_id = _gdrive_ensure_folder(
            service, root_folder_id, "onboarding", drive_id
        )
        seller_folder_id = _gdrive_ensure_folder(
            service, onboarding_folder_id, seller_slug, drive_id
        )

        filename = f"extrato_{ca_start_date}_to_{end_date}.csv"
        uploaded = _gdrive_upload_bytes(
            service,
            seller_folder_id,
            filename,
            csv_bytes,
            "text/csv",
            drive_id,
        )

        return {
            "file_id": uploaded["id"],
            "web_view_link": uploaded.get("webViewLink"),
        }

    except Exception as exc:
        logger.error(
            "upload_extrato_to_drive %s: failed to upload extrato — %s",
            seller_slug,
            exc,
            exc_info=True,
        )
        return None

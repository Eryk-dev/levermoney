"""Event Ledger — append-only financial event log for payments.

Each payment lifecycle event (sale, fee, refund, sync, release) is recorded
as an immutable row with a signed amount.  The ledger is the source of truth
for DRE por competencia and DRE por caixa.

Convention:
    positive signed_amount = money IN  (receita, estorno taxa/frete, subsidy)
    negative signed_amount = money OUT (fee, shipping, refund)
    zero     signed_amount = flag      (ca_sync, money_released, mediation)
"""

import logging
from datetime import datetime, timezone

from app.db.supabase import get_db

logger = logging.getLogger(__name__)

TABLE = "payment_events"

# Valid event types and their expected sign direction (for validation)
EVENT_TYPES = {
    # Financial events
    "sale_approved":     "positive",
    "fee_charged":       "negative",
    "shipping_charged":  "negative",
    "subsidy_credited":  "positive",
    "refund_created":    "negative",
    "refund_fee":        "positive",
    "refund_shipping":   "positive",
    "partial_refund":    "negative",
    # Operational flags (signed_amount = 0)
    "ca_sync_completed": "zero",
    "ca_sync_failed":    "zero",
    "money_released":    "zero",
    "mediation_opened":  "zero",
    # Chargeback lifecycle
    "charged_back":      "negative",
    "reimbursed":        "positive",
    # Adjustments (release report validator)
    "adjustment_fee":      "negative",
    "adjustment_shipping": "negative",
    # Expense lifecycle events (dual-write with mp_expenses)
    "expense_captured":   "any",    # Despesa/receita identified (signed amount)
    "expense_classified": "zero",   # Auto-classified (metadata: ca_category)
    "expense_reviewed":   "zero",   # Reviewed by human (metadata: approved)
    "expense_exported":   "zero",   # Exported in batch (metadata: batch_id)
    # Cash events — competencia_date = event_date (caixa, NAO competencia real). DRE queries MUST exclude these.
    "cash_release":      "positive",
    "cash_expense":      "negative",
    "cash_income":       "positive",
    "cash_transfer_out": "negative",
    "cash_transfer_in":  "positive",
    "cash_internal":     "any",
}

# Extrato line → cash event type mapping (for classified lines with expense_type)
CASH_TYPE_MAP: dict[str, str] = {
    "liberacao_cancelada":      "cash_expense",
    "reembolso_disputa":        "cash_income",
    "reembolso_generico":       "cash_income",
    "entrada_dinheiro":         "cash_income",
    "dinheiro_retido":          "cash_expense",
    "difal":                    "cash_expense",
    "faturas_ml":               "cash_expense",
    "debito_envio_ml":          "cash_expense",
    "debito_divida_disputa":    "cash_expense",
    "debito_troca":             "cash_expense",
    "bonus_envio":              "cash_income",
    "subscription":             "cash_expense",
    "pagamento_cartao_credito": "cash_expense",
    "emprestimo_mp":            "cash_income",
    "liberacao_nao_sync":       "cash_release",
    "qr_pix_nao_sync":          "cash_income",
    "dinheiro_recebido":        "cash_income",
    "pix_nao_sync":             "cash_transfer_in",
}

# Skip rules: extrato lines with expense_type=None → per-rule cash event mapping
# Keys are normalized transaction type substrings (lowercase)
SKIP_TO_CASH_TYPE: dict[str, str] = {
    "transferencia pix":         "cash_transfer_out",
    "pix enviado":               "cash_transfer_out",
    "pagamento de conta":        "cash_transfer_out",
    "compra mercado libre":      "cash_expense",
    "compra mercado livre":      "cash_expense",
    "transferencia enviada":     "cash_transfer_out",
    "transferência enviada":     "cash_transfer_out",
    "compra de ":                "cash_expense",
    "transferencia de saldo":    "cash_internal",
    "transferência de saldo":    "cash_internal",
    "dinheiro reservado renda":  "cash_internal",
    "dinheiro retirado renda":   "cash_internal",
    "dinheiro reservado":        "cash_internal",
}

# Abbreviation for skip rules (used in idempotency key)
SKIP_ABBREV: dict[str, str] = {
    "transferencia pix":         "tp",
    "pix enviado":               "pe",
    "pagamento de conta":        "pg",
    "compra mercado libre":      "cm",
    "compra mercado livre":      "cm",
    "transferencia enviada":     "te",
    "transferência enviada":     "te",
    "compra de ":                "cd",
    "transferencia de saldo":    "ts",
    "transferência de saldo":    "ts",
    "dinheiro reservado renda":  "rr",
    "dinheiro retirado renda":   "xr",
    "dinheiro reservado":        "rv",
}


def build_idempotency_key(
    seller_slug: str,
    payment_id: int,
    event_type: str,
    suffix: str = "",
) -> str:
    """Build deterministic idempotency key.

    Format: {seller_slug}:{payment_id}:{event_type}[:{suffix}]
    """
    key = f"{seller_slug}:{payment_id}:{event_type}"
    if suffix:
        key = f"{key}:{suffix}"
    return key


def validate_event(event_type: str, signed_amount: float) -> None:
    """Validate event_type exists and signed_amount matches expected direction.

    Raises ValueError on invalid input.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event_type: {event_type}")

    expected = EVENT_TYPES[event_type]
    if expected == "positive" and signed_amount < 0:
        raise ValueError(
            f"{event_type} expects positive amount, got {signed_amount}"
        )
    if expected == "negative" and signed_amount > 0:
        raise ValueError(
            f"{event_type} expects negative amount, got {signed_amount}"
        )
    if expected == "any":
        pass  # accepts any sign (cash_internal can be + or -)
    elif expected == "zero" and signed_amount != 0:
        raise ValueError(
            f"{event_type} expects zero amount, got {signed_amount}"
        )


def derive_payment_status(event_types: set[str]) -> str:
    """Derive payment status from its event types.

    Priority order (first match wins):
        ca_sync_failed  → "error"    (any failure needs attention)
        refund/chargeback → "refunded" (terminal state)
        ca_sync_completed → "synced"
        sale_approved     → "queued"
        (none of the above) → "unknown"
    """
    if "ca_sync_failed" in event_types:
        return "error"
    if "refund_created" in event_types or "charged_back" in event_types:
        return "refunded"
    if "ca_sync_completed" in event_types:
        return "synced"
    if "sale_approved" in event_types:
        return "queued"
    return "unknown"


class EventRecordError(Exception):
    """Raised when record_event fails due to a non-idempotency DB error."""


async def record_event(
    seller_slug: str,
    ml_payment_id: int,
    event_type: str,
    signed_amount: float,
    competencia_date: str,
    event_date: str,
    ml_order_id: int | None = None,
    source: str = "processor",
    metadata: dict | None = None,
    idempotency_key: str | None = None,
    reference_id: str | None = None,
) -> dict | None:
    """Insert an event into the ledger.

    Returns the inserted row dict, or None if the event already exists
    (idempotency — ON CONFLICT DO NOTHING).

    Raises EventRecordError on database failures so callers can decide
    whether to continue or abort.
    """
    validate_event(event_type, signed_amount)

    if idempotency_key is None:
        idempotency_key = build_idempotency_key(
            seller_slug, ml_payment_id, event_type
        )

    row = {
        "seller_slug": seller_slug,
        "ml_payment_id": ml_payment_id,
        "ml_order_id": ml_order_id,
        "event_type": event_type,
        "signed_amount": signed_amount,
        "competencia_date": competencia_date,
        "event_date": event_date,
        "source": source,
        "idempotency_key": idempotency_key,
        "metadata": metadata,
        "reference_id": reference_id if reference_id is not None else str(ml_payment_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    db = get_db()
    try:
        result = db.table(TABLE).upsert(
            row,
            on_conflict="idempotency_key",
            ignore_duplicates=True,
        ).execute()

        if result.data:
            logger.debug(
                "Event recorded: %s %s payment=%s amount=%.2f",
                seller_slug, event_type, ml_payment_id, signed_amount,
            )
            return result.data[0]

        # Duplicate — ignore_duplicates=True returns empty data
        logger.debug(
            "Event already exists (idempotent skip): %s", idempotency_key,
        )
        return None

    except Exception as e:
        logger.error(
            "Failed to record event %s for payment %s: %s",
            event_type, ml_payment_id, e,
        )
        raise EventRecordError(
            f"DB error recording {event_type} for payment {ml_payment_id}: {e}"
        ) from e


async def get_events(
    seller_slug: str,
    ml_payment_id: int,
) -> list[dict]:
    """Return all events for a payment, ordered by created_at ASC."""
    db = get_db()
    result = db.table(TABLE).select("*").eq(
        "seller_slug", seller_slug
    ).eq(
        "ml_payment_id", ml_payment_id
    ).order("created_at").execute()

    return result.data or []


async def get_balance(
    seller_slug: str,
    ml_payment_id: int,
    as_of_date: str | None = None,
) -> float:
    """Compute cumulative balance for a payment.

    If as_of_date is provided, only includes events with
    competencia_date <= as_of_date.
    """
    db = get_db()
    query = db.table(TABLE).select("signed_amount").eq(
        "seller_slug", seller_slug
    ).eq(
        "ml_payment_id", ml_payment_id
    )

    if as_of_date:
        query = query.lte("competencia_date", as_of_date)

    result = query.execute()
    if not result.data:
        return 0.0

    return round(sum(row["signed_amount"] for row in result.data), 2)


async def get_processed_payment_ids(
    seller_slug: str,
    event_type: str = "sale_approved",
) -> set[int]:
    """Return set of ml_payment_ids that have a specific event type.

    Default: sale_approved (every processed order payment has this).
    Paginated to handle large datasets.
    """
    db = get_db()
    found: set[int] = set()
    page_start = 0
    page_limit = 1000
    while True:
        result = db.table(TABLE).select("ml_payment_id").eq(
            "seller_slug", seller_slug
        ).eq("event_type", event_type).range(
            page_start, page_start + page_limit - 1
        ).execute()
        rows = result.data or []
        for r in rows:
            found.add(int(r["ml_payment_id"]))
        if len(rows) < page_limit:
            break
        page_start += page_limit
    return found


async def get_processed_payment_ids_in(
    seller_slug: str,
    payment_ids: list[int],
    event_type: str = "sale_approved",
) -> set[int]:
    """Check which payment_ids have a specific event type. Batch lookup in chunks of 100."""
    if not payment_ids:
        return set()
    db = get_db()
    found: set[int] = set()
    for i in range(0, len(payment_ids), 100):
        chunk = payment_ids[i:i + 100]
        result = db.table(TABLE).select("ml_payment_id").eq(
            "seller_slug", seller_slug
        ).eq("event_type", event_type).in_(
            "ml_payment_id", chunk
        ).execute()
        for r in (result.data or []):
            found.add(int(r["ml_payment_id"]))
    return found


async def get_payment_fees_from_events(
    seller_slug: str,
    payment_ids: list[int],
) -> dict[int, dict]:
    """Derive fee/shipping from fee_charged/shipping_charged events.

    Returns {ml_payment_id: {"fee": float, "shipping": float}}.
    """
    if not payment_ids:
        return {}
    db = get_db()
    all_events: list[dict] = []
    for i in range(0, len(payment_ids), 100):
        chunk = payment_ids[i:i + 100]
        result = db.table(TABLE).select(
            "ml_payment_id, event_type, signed_amount"
        ).eq("seller_slug", seller_slug).in_(
            "event_type", ["fee_charged", "shipping_charged"]
        ).in_("ml_payment_id", chunk).execute()
        all_events.extend(result.data or [])

    fees: dict[int, dict] = {}
    for evt in all_events:
        pid = int(evt["ml_payment_id"])
        if pid not in fees:
            fees[pid] = {"fee": 0.0, "shipping": 0.0}
        if evt["event_type"] == "fee_charged":
            fees[pid]["fee"] += abs(float(evt["signed_amount"]))
        elif evt["event_type"] == "shipping_charged":
            fees[pid]["shipping"] += abs(float(evt["signed_amount"]))

    for pid in fees:
        fees[pid]["fee"] = round(fees[pid]["fee"], 2)
        fees[pid]["shipping"] = round(fees[pid]["shipping"], 2)

    return fees


async def get_payment_statuses(
    seller_slug: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[int, str]:
    """Return {payment_id: derived_status} for all payments of a seller.

    Optionally filtered by competencia_date range.
    Paginated to handle large datasets. Uses derive_payment_status() internally.
    """
    db = get_db()
    events_by_pid: dict[int, set[str]] = {}
    page_start = 0
    page_limit = 1000
    while True:
        q = db.table(TABLE).select("ml_payment_id, event_type").eq(
            "seller_slug", seller_slug
        )
        if date_from:
            q = q.gte("competencia_date", date_from)
        if date_to:
            q = q.lte("competencia_date", date_to)

        rows = q.range(page_start, page_start + page_limit - 1).execute().data or []
        rows = [r for r in rows
                if not r["event_type"].startswith("cash_")
                and not r["event_type"].startswith("expense_")]
        for r in rows:
            pid = int(r["ml_payment_id"])
            if pid not in events_by_pid:
                events_by_pid[pid] = set()
            events_by_pid[pid].add(r["event_type"])

        if len(rows) < page_limit:
            break
        page_start += page_limit

    return {pid: derive_payment_status(ets) for pid, ets in events_by_pid.items()}


async def get_dre_summary(
    seller_slug: str,
    date_from: str,
    date_to: str,
) -> dict:
    """Aggregate events by type for a date range (competencia_date).

    Returns dict like:
        {"sale_approved": 12345.67, "fee_charged": -1234.56, ...}

    Paginated to avoid PostgREST row limits.
    """
    db = get_db()
    summary: dict[str, float] = {}
    page_start = 0
    page_limit = 1000
    while True:
        result = db.table(TABLE).select("event_type, signed_amount").eq(
            "seller_slug", seller_slug
        ).gte(
            "competencia_date", date_from
        ).lte(
            "competencia_date", date_to
        ).range(page_start, page_start + page_limit - 1).execute()

        rows = result.data or []
        rows = [r for r in rows
                if not r["event_type"].startswith("cash_")
                and not r["event_type"].startswith("expense_")]
        for row in rows:
            et = row["event_type"]
            summary[et] = round(summary.get(et, 0) + row["signed_amount"], 2)

        if len(rows) < page_limit:
            break
        page_start += page_limit

    return summary


async def record_cash_event(
    seller_slug: str,
    reference_id: str,
    event_type: str,
    signed_amount: float,
    event_date: str,
    extrato_type: str,
    expense_type_abbrev: str = "xx",
    metadata: dict | None = None,
) -> dict | None:
    """Record a cash flow event from an extrato line.

    Idempotency key: {seller}:{ref_id}:{event_type}:{date}:{abbrev}
    The abbreviation prevents collision when two different transaction types
    generate the same cash event type for the same ref_id on the same day.

    NOTE: competencia_date is set equal to event_date for cash events.
    It does NOT represent accrual competencia.
    """
    if not event_type.startswith("cash_"):
        raise ValueError(f"cash_* event type required, got {event_type}")

    idem_key = f"{seller_slug}:{reference_id}:{event_type}:{event_date}:{expense_type_abbrev}"

    try:
        ml_pid = int(reference_id)
    except (ValueError, TypeError):
        ml_pid = 0

    full_metadata = {"extrato_type": extrato_type, "source": "account_statement"}
    if metadata:
        full_metadata.update(metadata)

    return await record_event(
        seller_slug=seller_slug,
        ml_payment_id=ml_pid,
        event_type=event_type,
        signed_amount=signed_amount,
        competencia_date=event_date,
        event_date=event_date,
        source="extrato",
        metadata=full_metadata,
        idempotency_key=idem_key,
        reference_id=reference_id,
    )


async def record_expense_event(
    seller_slug: str,
    payment_id: str,
    event_type: str,
    signed_amount: float,
    competencia_date: str,
    expense_type: str,
    metadata: dict | None = None,
) -> dict | None:
    """Record an expense lifecycle event.

    Idempotency key: {seller}:{payment_id}:{event_type} (3 parts).
    payment_id can be plain ("12345") or composite ("12345:df").
    ml_payment_id is extracted via int(payment_id.split(':')[0]) with fallback 0.
    """
    idem_key = f"{seller_slug}:{payment_id}:{event_type}"

    try:
        ml_pid = int(payment_id.split(":")[0])
    except (ValueError, TypeError):
        ml_pid = 0

    full_metadata = {"expense_type": expense_type}
    if metadata:
        full_metadata.update(metadata)

    return await record_event(
        seller_slug=seller_slug,
        ml_payment_id=ml_pid,
        event_type=event_type,
        signed_amount=signed_amount,
        competencia_date=competencia_date,
        event_date=competencia_date,
        source="expense_lifecycle",
        metadata=full_metadata,
        idempotency_key=idem_key,
        reference_id=payment_id,
    )


def derive_expense_status(event_types: set[str]) -> str:
    """Derive expense status from its event types.

    Priority order (first match wins):
        expense_exported   → "exported"
        expense_reviewed   → "reviewed"
        expense_classified → "auto_categorized"
        expense_captured   → "pending_review"
        (none)             → "unknown"
    """
    if "expense_exported" in event_types:
        return "exported"
    if "expense_reviewed" in event_types:
        return "reviewed"
    if "expense_classified" in event_types:
        return "auto_categorized"
    if "expense_captured" in event_types:
        return "pending_review"
    return "unknown"


async def get_cash_summary(
    seller_slug: str,
    date_from: str,
    date_to: str,
) -> dict:
    """Aggregate cash events by type for a date range (event_date).

    Returns dict like: {"cash_release": 12345.67, "cash_expense": -1234.56}
    Only includes event_type starting with 'cash_'.
    Paginated to avoid PostgREST row limits.
    """
    db = get_db()
    summary: dict[str, float] = {}
    page_start = 0
    page_limit = 1000
    while True:
        result = db.table(TABLE).select("event_type, signed_amount").eq(
            "seller_slug", seller_slug
        ).gte("event_date", date_from).lte(
            "event_date", date_to
        ).range(page_start, page_start + page_limit - 1).execute()

        rows = result.data or []
        for row in rows:
            et = row["event_type"]
            if not et.startswith("cash_"):
                continue
            summary[et] = round(summary.get(et, 0) + float(row["signed_amount"]), 2)

        if len(rows) < page_limit:
            break
        page_start += page_limit

    return summary

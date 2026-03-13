"""
Extrato Line Ingester — ingests account_statement (release_report) lines that
are NOT covered by the Payments API or existing payment_events.

Handles gap types that exist only in the account_statement:
  1. DIFAL            — state tax difference (Diferença da aliquota ICMS)
  2. faturas_ml       — overdue ML invoices (Faturas vencidas do Mercado Livre)
  3. reembolso_disputa — dispute refund returned to seller (Reembolso Reclamações)
  4. dinheiro_retido  — disputed funds held (Dinheiro retido)
  5. entrada_dinheiro — miscellaneous credit entry (Entrada de dinheiro)
  6. debito_envio_ml  — retroactive shipping charge (Débito por dívida Envio)
  7. liberacao_cancelada — reversed release (Liberação de dinheiro cancelada)
  8. reembolso_generico — generic reimbursement / rounding (Reembolso genérico)
  9. debito_divida_disputa — dispute debit direct charge (Reclamações no Mercado Livre)
 10. deposito_avulso  — one-off deposit / aporte (Dinheiro recebido)
 11. pagamento_cartao_credito — credit card payment debit (Pagamento cartão de crédito)
 12. liberacao_nao_sync — release not found in payment_events (ML API gap)
 13. qr_pix_nao_sync  — QR/PIX payment not found in payment_events (ML API gap)

Smart skip logic: lines like "Liberacao de dinheiro" or "Pagamento com QR"
are only skipped if their reference_id already exists in the payment_events.
If the ML search API silently dropped them (batch release bug), the ingester
captures them as expense_captured events with pending_review status.

All gap lines go to the event ledger for XLSX export. The financial team categorises
and imports them in Conta Azul.

Runs inside the nightly pipeline AFTER sync_all_sellers() and BEFORE
check_extrato_coverage_all_sellers() so coverage reaches 100%.
"""
import calendar
import logging
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db.supabase import get_db
from app.models.sellers import CA_CATEGORIES, get_all_active_sellers, get_seller_config
from app.services.event_ledger import EventRecordError, record_expense_event
from app.services.release_report_sync import _get_or_create_report

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))

# ---------------------------------------------------------------------------
# In-memory result cache
# ---------------------------------------------------------------------------

_last_ingestion_result: dict = {
    "ran_at": None,
    "results": [],
}

# ---------------------------------------------------------------------------
# CA category UUID mapping for extrato types
# ---------------------------------------------------------------------------

# Maps category code strings to CA_CATEGORIES keys (or hardcoded UUIDs when
# the category is not present in the CA_CATEGORIES dict).
# "1.3.4" → estorno_taxa, "1.3.7" → estorno_frete,
# "2.2.3" → DIFAL (hardcoded UUID — not in CA_CATEGORIES),
# "2.8.2" → comissao_ml, "2.9.4" → frete_mercadoenvios
_CA_CATEGORY_CODE_MAP: dict[str, str] = {
    "1.3.4": CA_CATEGORIES["estorno_taxa"],
    "1.3.7": CA_CATEGORIES["estorno_frete"],
    # 2.2.3 DIFAL (Diferencial de Alíquota ICMS) — UUID from ca_categories.json
    "2.2.3": "3b1acab2-9fd6-4fce-b9ac-d418c6355c5d",
    "2.8.2": CA_CATEGORIES["comissao_ml"],
    "2.9.4": CA_CATEGORIES["frete_mercadoenvios"],
}

# ---------------------------------------------------------------------------
# Extrato classification rules (order is significant: first match wins)
# ---------------------------------------------------------------------------
# Each rule: (normalised_pattern, expense_type | None, direction | None, ca_category_code | None)
#
# None expense_type → UNCONDITIONAL SKIP (truly internal, never needs ingestion).
# "_check_payments" expense_type → CONDITIONAL SKIP: skip only if the
#   reference_id exists in the payment_events.  If the ML search API silently
#   dropped the payment (batch release bug), ingest as mp_expense with the
#   fallback_type specified in _CHECK_PAYMENTS_FALLBACK.
# direction values: "expense", "income", "transfer"
# ca_category_code: maps to _CA_CATEGORY_CODE_MAP; None means pending_review.

# Sentinel value for conditional skip rules
_CHECK_PAYMENTS = "_check_payments"

EXTRATO_CLASSIFICATION_RULES: list[tuple[str, Optional[str], Optional[str], Optional[str]]] = [
    # --- CONDITIONAL SKIPS (check payment_events first) ---
    # "Liberacao de dinheiro cancelada" must come BEFORE "liberacao de dinheiro"
    ("liberacao de dinheiro cancelada",   "liberacao_cancelada",   "expense",  None),
    ("liberacao de dinheiro",             _CHECK_PAYMENTS,         "income",   None),
    ("pagamento com",                     _CHECK_PAYMENTS,         "income",   None),
    # --- CONDITIONAL SKIPS (PIX received — check if ref_id is in payments) ---
    ("pix recebido",                      _CHECK_PAYMENTS,         "income",   None),
    # --- UNCONDITIONAL SKIPS (truly internal, no financial impact) ---
    ("transferencia pix",                 None,                    None,       None),
    ("pix enviado",                       None,                    None,       None),
    ("pagamento de conta",                None,                    None,       None),
    # --- INCOME ---
    ("reembolso reclamacoes",             "reembolso_disputa",     "income",   "1.3.4"),
    ("reembolso reclamações",             "reembolso_disputa",     "income",   "1.3.4"),
    ("reembolso envio cancelado",         "reembolso_disputa",     "income",   "1.3.4"),
    ("reembolso envío cancelado",         "reembolso_disputa",     "income",   "1.3.4"),
    ("reembolso de tarifas",              "reembolso_generico",    "income",   "1.3.4"),
    ("reembolso",                         "reembolso_generico",    "income",   "1.3.4"),
    ("entrada de dinheiro",               "entrada_dinheiro",      "income",   None),
    ("dinheiro recebido",                 _CHECK_PAYMENTS,         "income",   None),
    # --- EXPENSES ---
    ("dinheiro retido",                   "dinheiro_retido",       "expense",  None),
    ("diferenca da aliquota",             "difal",                 "expense",  "2.2.3"),
    ("difal",                             "difal",                 "expense",  "2.2.3"),
    ("faturas vencidas",                  "faturas_ml",            "expense",  "2.8.2"),
    ("envio do mercado livre",            "debito_envio_ml",       "expense",  "2.9.4"),
    ("reclamacoes no mercado livre",      "debito_divida_disputa", "expense",  None),
    ("reclamações no mercado livre",      "debito_divida_disputa", "expense",  None),
    # Additional types found in real extratos (jan 2026)
    ("troca de produto",                  "debito_troca",          "expense",  None),
    ("bonificacao",                       "bonus_envio",           "income",   "1.3.7"),
    ("bonus por envio",                   "bonus_envio",           "income",   "1.3.7"),
    ("bônus por envio",                   "bonus_envio",           "income",   "1.3.7"),
    ("compra mercado libre",              None,                    None,       None),
    ("compra mercado livre",              None,                    None,       None),
    ("transferencia enviada",             None,                    None,       None),
    ("transferência enviada",             None,                    None,       None),
    ("transferencia recebida",            "entrada_dinheiro",      "income",   None),
    ("transferência recebida",            "entrada_dinheiro",      "income",   None),
    ("transferencia de saldo",            None,                    None,       None),
    ("transferência de saldo",            None,                    None,       None),
    # FIX: credit card payments are real debits, not internal transfers.
    # ca_category=None → pending_review (user must assign the correct CA category).
    ("pagamento cartao de credito",       "pagamento_cartao_credito", "expense", None),
    ("pagamento cartão de crédito",       "pagamento_cartao_credito", "expense", None),
    # SaaS subscriptions billed directly through MP (no "de conta" / "com QR")
    # e.g. "Pagamento Supabase", "Pagamento Claude.ai subscription", "Pagamento Notion"
    # Must come AFTER more specific "pagamento de conta" / "pagamento com" rules.
    ("pagamento",                         "subscription",          "expense",  None),
    # MP loan approval (Empréstimos Express)
    ("aprovacao do dinheiro express",      "emprestimo_mp",         "income",   None),
    ("aprovação do dinheiro express",      "emprestimo_mp",         "income",   None),
    # MP investment (Renda = money market fund within MP)
    ("dinheiro reservado renda",           None,                    None,       None),
    ("dinheiro retirado renda",            None,                    None,       None),
    # Internal transfers to sub-accounts (e.g. Lever Talents)
    ("dinheiro reservado",                 None,                    None,       None),
    # Purchase made via ML (product description embedded in tx_type)
    # e.g. "Compra de Adaptador Acelerador Piloto Automático..."
    ("compra de ",                        None,                    None,       None),
]

# Fallback expense_type when _CHECK_PAYMENTS finds a line NOT in the payments
# table.  Keyed by the normalised pattern prefix from the classification rule.
_CHECK_PAYMENTS_FALLBACK: dict[str, tuple[str, str]] = {
    # pattern_prefix → (fallback_expense_type, fallback_direction)
    "liberacao de dinheiro":  ("liberacao_nao_sync",   "income"),
    "pagamento com":          ("qr_pix_nao_sync",      "income"),
    "dinheiro recebido":      ("dinheiro_recebido",     "income"),
    "pix recebido":           ("pix_nao_sync",          "income"),
}

# Abbreviated suffixes used when the same REFERENCE_ID appears multiple times
# in the extrato with different transaction types (e.g. dispute groups).
_EXPENSE_TYPE_ABBREV: dict[str, str] = {
    "liberacao_cancelada":      "lc",
    "reembolso_disputa":        "rd",
    "reembolso_generico":       "rg",
    "entrada_dinheiro":         "ed",
    "deposito_avulso":          "da",
    "dinheiro_retido":          "dr",
    "difal":                    "df",
    "faturas_ml":               "fm",
    "debito_envio_ml":          "de",
    "debito_divida_disputa":    "dd",
    "debito_troca":             "dt",
    "bonus_envio":              "be",
    "subscription":             "sb",
    "pagamento_cartao_credito": "pc",
    "emprestimo_mp":            "em",
    # New types for smart skip (lines not found in payment_events)
    "liberacao_nao_sync":       "ln",
    "qr_pix_nao_sync":         "qn",
    "dinheiro_recebido":        "dc",
    "pix_nao_sync":             "pn",
}


# Human-readable description templates keyed by expense_type
_DESCRIPTION_TEMPLATES: dict[str, str] = {
    "difal":                 "DIFAL ICMS - Ref {ref_id}",
    "faturas_ml":            "Fatura Vencida ML - Ref {ref_id}",
    "reembolso_disputa":     "Reembolso Disputa ML - Ref {ref_id}",
    "dinheiro_retido":       "Reserva Disputa ML - Ref {ref_id}",
    "entrada_dinheiro":      "Credito Avulso ML - Ref {ref_id}",
    "debito_envio_ml":       "Debito Envio ML - Ref {ref_id}",
    "liberacao_cancelada":   "Liberacao Cancelada - Ref {ref_id}",
    "reembolso_generico":    "Reembolso ML - Ref {ref_id}",
    "debito_divida_disputa": "Debito Divida ML - Ref {ref_id}",
    "deposito_avulso":       "Deposito Avulso MP - Ref {ref_id}",
    "debito_troca":          "Debito Troca Produto ML - Ref {ref_id}",
    "bonus_envio":           "Bonus Envio ML - Ref {ref_id}",
    "subscription":          "Assinatura MP - Ref {ref_id}",
    "pagamento_cartao_credito": "Pagamento Cartao Credito MP - Ref {ref_id}",
    "emprestimo_mp":         "Emprestimo Express MP - Ref {ref_id}",
    # New types for smart skip (lines not found in payment_events)
    "liberacao_nao_sync":    "Liberacao Nao Sincronizada - Ref {ref_id}",
    "qr_pix_nao_sync":      "Pagamento QR/PIX Nao Sincronizado - Ref {ref_id}",
    "dinheiro_recebido":     "Dinheiro Recebido Nao Sincronizado - Ref {ref_id}",
    "pix_nao_sync":          "PIX Recebido Nao Sincronizado - Ref {ref_id}",
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_br_number(raw: str) -> float:
    """Parse a Brazilian-formatted number string to float.

    Handles formats like '1.234,56', '-210.571,52', '0,00'.
    Returns 0.0 on parse error.
    """
    if not raw or not raw.strip():
        return 0.0
    cleaned = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _normalize_text(text: str) -> str:
    """Normalize accented/special characters for pattern matching.

    Strips diacritics (ã→a, ç→c, é→e, etc.) and lowercases.
    """
    # Decompose unicode characters then drop combining diacritical marks
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_text.lower()


def _parse_account_statement(csv_text: str) -> tuple[dict, list[dict]]:
    """Parse account_statement CSV into (summary, transactions).

    The file has two sections separated by a blank line:
      Section 1 — balance summary (header + single data row)
      Section 2 — transaction detail (header + N data rows)

    Args:
        csv_text: Raw text content of the account_statement CSV.

    Returns:
        Tuple of:
          - summary dict: {initial_balance, credits, debits, final_balance}
          - transactions list: each item is
            {date, transaction_type, reference_id, amount, balance}
    """
    lines = csv_text.splitlines()

    summary: dict = {}
    transactions: list[dict] = []

    in_transactions = False
    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue

        # Detect the summary header
        if line.startswith("INITIAL_BALANCE"):
            # Next non-empty line is the data row
            for data_line in lines[idx + 1:]:
                data_line = data_line.strip()
                if not data_line:
                    continue
                parts = data_line.split(";")
                if len(parts) >= 4:
                    summary = {
                        "initial_balance": _parse_br_number(parts[0]),
                        "credits":         _parse_br_number(parts[1]),
                        "debits":          _parse_br_number(parts[2]),
                        "final_balance":   _parse_br_number(parts[3]),
                    }
                break
            continue

        # Detect the transaction header line
        if line.startswith("RELEASE_DATE"):
            in_transactions = True
            continue

        if not in_transactions:
            continue

        # Parse transaction data rows
        # Format: DD-MM-YYYY;TRANSACTION_TYPE;REFERENCE_ID;AMOUNT;BALANCE
        parts = line.split(";")
        if len(parts) < 5:
            # May be a shorter line — skip it
            logger.debug("Skipping short extrato line: %r", line)
            continue

        raw_date    = parts[0].strip()
        tx_type     = parts[1].strip()
        ref_id      = parts[2].strip()
        raw_amount  = parts[3].strip()
        raw_balance = parts[4].strip() if len(parts) > 4 else ""

        # Convert DD-MM-YYYY → YYYY-MM-DD ISO date
        try:
            dt = datetime.strptime(raw_date, "%d-%m-%Y")
            iso_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            logger.debug("Cannot parse extrato date %r, skipping line", raw_date)
            continue

        transactions.append({
            "date":             iso_date,
            "transaction_type": tx_type,
            "reference_id":     ref_id,
            "amount":           _parse_br_number(raw_amount),
            "balance":          _parse_br_number(raw_balance),
        })

    logger.debug("Parsed account_statement: summary=%s transactions=%d", summary, len(transactions))
    return summary, transactions


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------


def _classify_extrato_line(
    transaction_type: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Classify an extrato TRANSACTION_TYPE into (expense_type, direction, ca_category_uuid).

    Uses normalised text matching against EXTRATO_CLASSIFICATION_RULES (first
    match wins). Returns (None, None, None) when the line should be
    unconditionally skipped (truly internal transfers with no financial impact).

    Returns ("_check_payments", direction, None) when the line should be
    conditionally skipped: skip only if the reference_id exists in the payments
    table. The caller must resolve _CHECK_PAYMENTS into a real expense_type
    using _CHECK_PAYMENTS_FALLBACK when the ref_id is NOT in payments.

    Args:
        transaction_type: Raw TRANSACTION_TYPE string from the extrato.

    Returns:
        Tuple of (expense_type, direction, ca_category_uuid).
        All three are None → unconditional skip (already covered).
        expense_type == "_check_payments" → conditional skip (check payment_events).
        Otherwise → real gap line to ingest.
    """
    normalized = _normalize_text(transaction_type)

    for pattern, expense_type, direction, cat_code in EXTRATO_CLASSIFICATION_RULES:
        if pattern in normalized:
            ca_category_uuid = _CA_CATEGORY_CODE_MAP.get(cat_code) if cat_code else None
            return expense_type, direction, ca_category_uuid

    # No rule matched — log as unknown and treat as pending-review expense
    logger.warning("No classification rule matched extrato type: %r", transaction_type)
    return "other", "expense", None


def _resolve_check_payments(
    transaction_type: str,
) -> tuple[str, str]:
    """Resolve a _CHECK_PAYMENTS classification into a concrete fallback type.

    Called when the ref_id is NOT found in the payment_events, meaning the ML
    search API dropped this payment and we need to ingest it.

    Args:
        transaction_type: Raw TRANSACTION_TYPE string from the extrato.

    Returns:
        Tuple of (fallback_expense_type, fallback_direction).
    """
    normalized = _normalize_text(transaction_type)
    for pattern, (fallback_type, fallback_dir) in _CHECK_PAYMENTS_FALLBACK.items():
        if pattern in normalized:
            return fallback_type, fallback_dir
    # Should not happen if _CHECK_PAYMENTS_FALLBACK covers all _CHECK_PAYMENTS rules
    logger.warning(
        "_resolve_check_payments: no fallback for %r, defaulting to 'other'",
        transaction_type,
    )
    return "other", "expense"


# ---------------------------------------------------------------------------
# Description builder
# ---------------------------------------------------------------------------


def _build_extrato_description(tx: dict, expense_type: str) -> str:
    """Build description string for an extrato expense event."""
    return _DESCRIPTION_TEMPLATES.get(
        expense_type, "{tx_type} - Ref {ref_id}"
    ).format(
        ref_id=tx["reference_id"],
        tx_type=tx["transaction_type"],
    )[:200]


# ---------------------------------------------------------------------------
# Event ledger write helpers
# ---------------------------------------------------------------------------


def _extrato_signed_amount(direction: str, amount: float) -> float:
    """Return signed amount: positive for income, negative for expense/transfer."""
    if direction == "income":
        return abs(amount)
    return -abs(amount)


def _build_extrato_expense_metadata(
    tx: dict,
    expense_type: str,
    direction: str,
    ca_category_uuid: Optional[str],
    description: str,
) -> dict:
    """Build rich metadata dict for expense_captured event from extrato tx."""
    auto_cat = ca_category_uuid is not None
    return {
        "expense_type": expense_type,
        "expense_direction": direction,
        "ca_category": ca_category_uuid,
        "auto_categorized": auto_cat,
        "description": description,
        "amount": abs(tx["amount"]),
        "date_created": tx["date"],
        "date_approved": tx["date"],
        "business_branch": None,
        "operation_type": f"extrato_{expense_type}",
        "payment_method": None,
        "external_reference": tx["reference_id"],
        "beneficiary_name": None,
        "notes": tx["transaction_type"],
    }


async def _write_extrato_expense_events(
    seller_slug: str,
    payment_id_key: str,
    expense_type: str,
    direction: str,
    ca_category_uuid: Optional[str],
    tx: dict,
    description: str,
) -> None:
    """Write expense_captured (and expense_classified if auto) to event ledger.

    Failures are logged as warnings but do not propagate.
    """
    amount = abs(tx["amount"])
    signed = _extrato_signed_amount(direction, amount)
    competencia = tx["date"][:10]
    auto_cat = ca_category_uuid is not None
    metadata = _build_extrato_expense_metadata(
        tx, expense_type, direction, ca_category_uuid, description,
    )

    try:
        await record_expense_event(
            seller_slug=seller_slug,
            payment_id=payment_id_key,
            event_type="expense_captured",
            signed_amount=signed,
            competencia_date=competencia,
            expense_type=expense_type,
            metadata=metadata,
        )
    except EventRecordError:
        logger.warning(
            "expense_captured failed for %s/%s, continuing",
            seller_slug, payment_id_key,
        )

    if auto_cat:
        try:
            await record_expense_event(
                seller_slug=seller_slug,
                payment_id=payment_id_key,
                event_type="expense_classified",
                signed_amount=0,
                competencia_date=competencia,
                expense_type=expense_type,
                metadata={"ca_category": ca_category_uuid},
            )
        except EventRecordError:
            logger.warning(
                "Dual-write expense_classified failed for %s/%s, continuing",
                seller_slug, payment_id_key,
            )


# ---------------------------------------------------------------------------
# Batch DB lookups
# ---------------------------------------------------------------------------


async def _batch_lookup_payment_ids(
    db,
    seller_slug: str,
    ref_ids: list[str],
) -> set[str]:
    """Return set of reference_id strings found in payment_events (processed).

    Looks up by ml_payment_id (integer). Only numeric reference IDs are
    checked — DIFAL and similar IDs are non-payment and will not match.
    """
    from app.services import event_ledger

    numeric_ids: list[int] = []
    for rid in ref_ids:
        try:
            numeric_ids.append(int(rid))
        except (ValueError, TypeError):
            continue

    found_ints = await event_ledger.get_processed_payment_ids_in(seller_slug, numeric_ids)
    return {str(pid) for pid in found_ints}


def _batch_lookup_expense_payment_ids(
    db,
    seller_slug: str,
    ref_ids: list[str],
) -> set[str]:
    """Return set of payment_id strings found in payment_events (expense_captured).

    Looks up by ml_payment_id (integer) where event_type='expense_captured'.
    Only numeric reference IDs are checked.
    """
    found: set[str] = set()
    if not ref_ids:
        return found

    numeric_ids: list[int] = []
    for rid in ref_ids:
        try:
            numeric_ids.append(int(rid))
        except (ValueError, TypeError):
            continue

    if numeric_ids:
        for i in range(0, len(numeric_ids), 100):
            chunk = numeric_ids[i : i + 100]
            result = (
                db.table("payment_events")
                .select("ml_payment_id")
                .eq("seller_slug", seller_slug)
                .eq("event_type", "expense_captured")
                .in_("ml_payment_id", chunk)
                .execute()
            )
            for row in result.data or []:
                found.add(str(row["ml_payment_id"]))

    return found


def _batch_lookup_composite_expense_ids(
    db,
    seller_slug: str,
    composite_keys: list[str],
) -> set[str]:
    """Return set of composite payment_id strings already in payment_events.

    Used to detect previously ingested extrato lines (e.g. "123456:df").
    """
    found: set[str] = set()
    if not composite_keys:
        return found

    for i in range(0, len(composite_keys), 100):
        chunk = composite_keys[i : i + 100]
        result = (
            db.table("payment_events")
            .select("reference_id")
            .eq("seller_slug", seller_slug)
            .eq("event_type", "expense_captured")
            .in_("reference_id", chunk)
            .execute()
        )
        for row in result.data or []:
            found.add(str(row["reference_id"]))

    return found


async def _batch_lookup_refunded_payment_ids(
    db,
    seller_slug: str,
    ref_ids: list[str],
) -> set[str]:
    """Return set of reference_id strings for payments with refund_created event.

    Used to prevent double-counting devoluções: when processor.py already created
    estorno_receita (1.2.1) for a refunded payment, the extrato debito_divida_disputa
    line for the same payment_id must be skipped to avoid duplicating the deduction.
    """
    from app.services import event_ledger

    numeric_ids: list[int] = []
    for rid in ref_ids:
        try:
            numeric_ids.append(int(rid))
        except (ValueError, TypeError):
            continue

    found_ints = await event_ledger.get_processed_payment_ids_in(
        seller_slug, numeric_ids, event_type="refund_created"
    )
    return {str(pid) for pid in found_ints}


def _fuzzy_match_expense(
    db,
    seller_slug: str,
    amount: float,
    date: str,
    expense_types: list[str],
) -> bool:
    """Check if an expense_captured event exists with matching amount, date, and type.

    Used to deduplicate faturas ML and similar charges that appear in the
    extrato with internal ML IDs (e.g. 27xxxxx) while an existing event stores
    them with collection IDs (e.g. 14xxxxxxxxxx). Same charge, different IDs.

    Matches by: seller_slug + approximate amount (within R$ 0.01) +
    competencia_date + expense_type in the given list.

    Returns True if a matching record exists (meaning the extrato line is
    already covered and should be skipped).
    """
    if not expense_types:
        return False

    try:
        result = (
            db.table("payment_events")
            .select("reference_id, signed_amount, metadata")
            .eq("seller_slug", seller_slug)
            .eq("event_type", "expense_captured")
            .eq("competencia_date", date)
            .execute()
        )
        for row in result.data or []:
            meta = row.get("metadata") or {}
            row_expense_type = meta.get("expense_type")
            if row_expense_type not in expense_types:
                continue
            existing_amount = abs(float(row.get("signed_amount") or 0))
            if abs(existing_amount - amount) < 0.01:
                logger.debug(
                    "fuzzy_match_expense: found match for amount=%.2f date=%s "
                    "type=%s → existing reference_id=%s",
                    amount, date, expense_types, row["reference_id"],
                )
                return True
    except Exception as exc:
        logger.warning("fuzzy_match_expense: query failed — %s", exc)

    return False


# ---------------------------------------------------------------------------
# Core per-seller ingestion
# ---------------------------------------------------------------------------


async def ingest_extrato_for_seller(
    seller_slug: str,
    begin_date: str,
    end_date: str,
) -> dict:
    """Ingest account_statement lines not covered by existing records.

    Pipeline:
      1. Download account_statement (release_report) via ML API.
      2. Parse all transaction lines.
      3. Classify each line using EXTRATO_CLASSIFICATION_RULES.
      4. First-pass: unconditional skips + _CHECK_PAYMENTS markers.
      5. Batch-lookup reference_ids against payment_events.
      5b. Resolve _CHECK_PAYMENTS: if ref_id in payments → skip,
          otherwise resolve to fallback expense_type and ingest.
      6. For truly uncovered lines: write to event ledger.
      7. Return stats dict.

    Smart skip logic: lines like "Liberacao de dinheiro" and "Pagamento com QR"
    are only skipped if their reference_id exists in the payment_events. If the
    ML search API silently dropped a payment (batch release bug), the line is
    ingested as an expense_captured event with pending_review status.

    Idempotency: same-REFERENCE_ID lines with different expense_types use
    composite payment_id strings (e.g. "123456789:df") to avoid collisions.
    Re-running the ingester for the same period is safe.

    Args:
        seller_slug: Seller identifier.
        begin_date:  ISO date string YYYY-MM-DD (inclusive start).
        end_date:    ISO date string YYYY-MM-DD (inclusive end).

    Returns:
        Stats dict with keys: seller, total_lines, skipped_internal,
        already_covered, newly_ingested, errors, by_type.
    """
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"seller": seller_slug, "error": "seller_not_found"}

    # 1. Download account_statement (reuses the same release_report pipeline)
    csv_bytes = await _get_or_create_report(seller_slug, begin_date, end_date)
    if not csv_bytes:
        logger.error("extrato_ingester %s: could not obtain account_statement", seller_slug)
        return {"seller": seller_slug, "error": "report_not_available"}

    # 2. Parse CSV
    try:
        csv_text = csv_bytes.decode("utf-8-sig")  # Handle BOM
    except UnicodeDecodeError:
        try:
            csv_text = csv_bytes.decode("latin-1")
        except UnicodeDecodeError as exc:
            logger.error("extrato_ingester %s: cannot decode CSV — %s", seller_slug, exc)
            return {"seller": seller_slug, "error": f"decode_error: {exc}"}

    summary, transactions = _parse_account_statement(csv_text)

    if not transactions:
        logger.info("extrato_ingester %s: no transactions found in statement", seller_slug)
        return {
            "seller":          seller_slug,
            "total_lines":     0,
            "skipped_internal": 0,
            "already_covered": 0,

            "newly_ingested":  0,
            "errors":          0,
            "by_type":         {},
            "summary":         summary,
        }

    logger.info(
        "extrato_ingester %s: %d transactions between %s and %s",
        seller_slug,
        len(transactions),
        begin_date,
        end_date,
    )

    # 3. Filter by date range (the extrato may contain dates outside our window
    #    if the downloaded report covers a wider period)
    if begin_date and end_date:
        transactions = [
            tx for tx in transactions
            if begin_date <= tx["date"] <= end_date
        ]
        logger.info(
            "extrato_ingester %s: %d transactions after date filtering",
            seller_slug,
            len(transactions),
        )

    # 4. First-pass classification — split into skip vs gap lists
    # _CHECK_PAYMENTS lines get a temporary "cp" abbreviation; they will be
    # resolved in step 5b once we know which ref_ids are in the payment_events.
    classified: list[tuple[dict, str, str, Optional[str], str]] = []
    # (tx, expense_type, direction, ca_category_uuid, payment_id_key)
    stats = Counter()

    for tx in transactions:
        expense_type, direction, ca_category_uuid = _classify_extrato_line(
            tx["transaction_type"]
        )

        # (None, None, None) → unconditionally covered by existing pipeline
        if expense_type is None and direction is None:
            stats["skipped_internal"] += 1
            continue

        # _CHECK_PAYMENTS lines need batch lookup before skip/ingest decision.
        # Use a temporary "cp" abbreviation for the composite key.
        if expense_type == _CHECK_PAYMENTS:
            payment_id_key = f"{tx['reference_id']}:cp"
        else:
            # Build composite key: "{reference_id}:{abbrev}"
            abbrev = _EXPENSE_TYPE_ABBREV.get(expense_type, "xx") if expense_type else "xx"
            payment_id_key = f"{tx['reference_id']}:{abbrev}"

        classified.append((tx, expense_type, direction, ca_category_uuid, payment_id_key))

    logger.info(
        "extrato_ingester %s: %d gap lines to check (%d internal skips)",
        seller_slug,
        len(classified),
        stats["skipped_internal"],
    )

    if not classified:
        return {
            "seller":           seller_slug,
            "total_lines":      len(transactions) + stats["skipped_internal"],
            "skipped_internal": stats["skipped_internal"],
            "already_covered":  0,
            "newly_ingested":   0,
            "errors":           0,
            "by_type":          {},
            "summary":          summary,
        }

    # 5. Batch lookups to detect already-covered lines
    all_ref_ids = list({item[0]["reference_id"] for item in classified})

    payment_ids_in_db = await _batch_lookup_payment_ids(db, seller_slug, all_ref_ids)
    expense_ids_in_db = _batch_lookup_expense_payment_ids(db, seller_slug, all_ref_ids)

    # 5b. Resolve _CHECK_PAYMENTS lines now that we have payment_ids_in_db.
    #     Lines whose ref_id IS in payments → skip (covered by processor).
    #     Lines whose ref_id is NOT in payments → resolve to real expense_type.
    resolved: list[tuple[dict, str, str, Optional[str], str]] = []
    for tx, expense_type, direction, ca_category_uuid, payment_id_key in classified:
        if expense_type == _CHECK_PAYMENTS:
            ref_id = tx["reference_id"]
            if ref_id in payment_ids_in_db:
                # Payment exists in DB — line is covered by processor pipeline
                stats["skipped_internal"] += 1
                continue
            # Payment NOT in DB — ML API gap, resolve to fallback type
            fallback_type, fallback_dir = _resolve_check_payments(tx["transaction_type"])
            abbrev = _EXPENSE_TYPE_ABBREV.get(fallback_type, "xx")
            payment_id_key = f"{ref_id}:{abbrev}"
            logger.warning(
                "extrato_ingester %s: ref_id %s NOT in payments (type=%r) — "
                "ingesting as %s (ML API gap)",
                seller_slug,
                ref_id,
                tx["transaction_type"],
                fallback_type,
            )
            resolved.append((tx, fallback_type, fallback_dir, None, payment_id_key))
        else:
            resolved.append((tx, expense_type, direction, ca_category_uuid, payment_id_key))

    classified = resolved

    # Recompute composite keys after _CHECK_PAYMENTS resolution
    composite_keys = [item[4] for item in classified]
    composite_ids_in_db = _batch_lookup_composite_expense_ids(db, seller_slug, composite_keys)
    # Only needed for debito_divida_disputa deduplication — payments already
    # handled as refund by processor.py must not generate a second 1.2.1 entry.
    refunded_payment_ids_in_db = await _batch_lookup_refunded_payment_ids(db, seller_slug, all_ref_ids)

    logger.info(
        "extrato_ingester %s: found %d in payments (%d refunded), %d in expenses (plain), %d in expenses (composite)",
        seller_slug,
        len(payment_ids_in_db),
        len(refunded_payment_ids_in_db),
        len(expense_ids_in_db),
        len(composite_ids_in_db),
    )

    # 6. Write uncovered lines to event ledger
    by_type: Counter = Counter()

    for tx, expense_type, direction, ca_category_uuid, payment_id_key in classified:
        ref_id = tx["reference_id"]

        # a. Composite key already ingested (exact match: same ref + same type)
        if payment_id_key in composite_ids_in_db:
            stats["already_covered"] += 1
            continue

        # b. Plain ref_id covered by payment_events → skip unless it's a
        #    distinct expense type that can legitimately share the same ref_id
        #    (e.g. a dispute group: debit + refund + entry on the same payment_id).
        #    _CHECK_PAYMENTS lines have already been resolved above, so anything
        #    that arrives here with a payment-table match is a supplementary
        #    line (e.g. dinheiro_retido, debito_envio_ml on a payment that was
        #    also liberado). We still ingest it to capture the full picture.
        if ref_id in payment_ids_in_db:
            if expense_type == "debito_divida_disputa":
                # Dispute deduplication: if processor.py already created estorno_receita
                # (1.2.1 Devoluções) for this refunded payment, do NOT insert the extrato
                # line — it would double-count the deduction in the DRE.
                if ref_id in refunded_payment_ids_in_db:
                    stats["already_covered"] += 1
                    logger.debug(
                        "extrato_ingester %s: %s debito_divida_disputa skipped — processor already refunded",
                        seller_slug,
                        ref_id,
                    )
                    continue
                # Payment exists but was not refunded by processor — ingest the
                # extrato line (dispute debit not yet reflected in CA).
            elif expense_type in ("reembolso_disputa", "reembolso_generico",
                                  "entrada_dinheiro", "dinheiro_retido",
                                  "liberacao_cancelada", "debito_envio_ml",
                                  "bonus_envio", "debito_troca"):
                pass  # Distinct cash events that complement the payment — always ingest
            else:
                # For most types, if the ref_id already has a payment record,
                # the line is implicitly covered.
                stats["already_covered"] += 1
                continue

        # c. Plain ref_id already captured as expense event (API path)
        if ref_id in expense_ids_in_db:
            stats["already_covered"] += 1
            continue

        # c2. Fuzzy dedup for faturas_ml / collection with internal ML IDs.
        #     The extrato may use an internal ML ID (e.g. 27xxxxx) while
        #     an existing event stores the same charge under a collection ID
        #     (e.g. 14xxxxxxxxxx). Match by amount + date + type.
        if expense_type in ("faturas_ml", "collection"):
            extrato_amount = abs(tx["amount"])
            if _fuzzy_match_expense(
                db, seller_slug, extrato_amount, tx["date"],
                ["faturas_ml", "collection"],
            ):
                stats["already_covered"] += 1
                logger.debug(
                    "extrato_ingester %s: %s fuzzy-matched existing faturas_ml/collection "
                    "(amount=%.2f date=%s), skipping",
                    seller_slug, ref_id, extrato_amount, tx["date"],
                )
                continue

        # d. Write to event ledger (idempotent via ON CONFLICT DO NOTHING)
        description = _build_extrato_description(tx, expense_type)
        await _write_extrato_expense_events(
            seller_slug, payment_id_key, expense_type, direction,
            ca_category_uuid, tx, description,
        )
        stats["newly_ingested"] += 1
        by_type[expense_type] += 1
        logger.info(
            "extrato_ingester %s: ingested %s type=%s dir=%s amount=%.2f",
            seller_slug,
            payment_id_key,
            expense_type,
            direction,
            abs(tx["amount"]),
        )

    total_lines = len(transactions) + stats["skipped_internal"]
    result = {
        "seller":           seller_slug,
        "total_lines":      total_lines,
        "skipped_internal": stats["skipped_internal"],
        "already_covered":  stats["already_covered"],
        "newly_ingested":   stats["newly_ingested"],
        "errors":           stats["errors"],
        "by_type":          dict(by_type),
        "summary":          summary,
    }
    logger.info("extrato_ingester %s: %s", seller_slug, {
        k: v for k, v in result.items() if k not in ("summary", "by_type")
    })
    logger.info("extrato_ingester %s: by_type=%s", seller_slug, dict(by_type))
    return result


# ---------------------------------------------------------------------------
# CSV upload entry point (admin panel)
# ---------------------------------------------------------------------------


async def ingest_extrato_from_csv(
    seller_slug: str,
    csv_text: str,
    month: str,
) -> dict:
    """Ingest account_statement lines from a pre-downloaded CSV string.

    Variant of ingest_extrato_for_seller() that accepts raw CSV text
    instead of downloading the report from the ML API. Called by the
    admin upload endpoint when the user supplies the file manually.

    The month parameter is used for date filtering (keeps only lines
    within the calendar month) and for logging. It does NOT restrict
    what the CSV may contain — the CSV may include a few days of the
    previous or next month at the margins (MP reports overlap); those
    lines are filtered out before ingestion.

    Args:
        seller_slug: Seller identifier (must exist in sellers table).
        csv_text:    Raw text content of the account_statement CSV.
                     BOM (UTF-8-sig) is handled internally.
        month:       Calendar month to ingest, format "YYYY-MM".
                     Lines outside this month are skipped.

    Returns:
        Stats dict matching ingest_extrato_for_seller() return format:
        {
            seller, total_lines, skipped_internal, already_covered,
            newly_ingested, errors, by_type, summary
        }

    Raises:
        ValueError: If the CSV does not contain an INITIAL_BALANCE header
                    (i.e. it is not a valid account_statement CSV).
    """
    # Validate CSV format
    if "INITIAL_BALANCE" not in csv_text.upper():
        raise ValueError("CSV invalido: header INITIAL_BALANCE nao encontrado")

    # Derive begin_date / end_date from month
    year, mo = int(month[:4]), int(month[5:7])
    begin_date = f"{year:04d}-{mo:02d}-01"
    last_day = calendar.monthrange(year, mo)[1]
    end_date = f"{year:04d}-{mo:02d}-{last_day:02d}"

    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        return {"seller": seller_slug, "error": "seller_not_found"}

    # Parse CSV
    summary, transactions = _parse_account_statement(csv_text)

    if not transactions:
        logger.info("extrato_ingester %s: upload path, month=%s — no transactions found", seller_slug, month)
        return {
            "seller":          seller_slug,
            "total_lines":     0,
            "skipped_internal": 0,
            "already_covered": 0,

            "newly_ingested":  0,
            "errors":          0,
            "by_type":         {},
            "summary":         summary,
        }

    logger.info(
        "extrato_ingester %s: upload path, month=%s, %d transactions",
        seller_slug,
        month,
        len(transactions),
    )

    # Filter by date range
    transactions = [
        tx for tx in transactions
        if begin_date <= tx["date"] <= end_date
    ]
    logger.info(
        "extrato_ingester %s: %d transactions after date filtering (%s to %s)",
        seller_slug,
        len(transactions),
        begin_date,
        end_date,
    )

    # --- From here the pipeline is identical to ingest_extrato_for_seller() ---

    # First-pass classification
    classified: list[tuple[dict, str, str, Optional[str], str]] = []
    stats = Counter()

    for tx in transactions:
        expense_type, direction, ca_category_uuid = _classify_extrato_line(
            tx["transaction_type"]
        )

        if expense_type is None and direction is None:
            stats["skipped_internal"] += 1
            continue

        if expense_type == _CHECK_PAYMENTS:
            payment_id_key = f"{tx['reference_id']}:cp"
        else:
            abbrev = _EXPENSE_TYPE_ABBREV.get(expense_type, "xx") if expense_type else "xx"
            payment_id_key = f"{tx['reference_id']}:{abbrev}"

        classified.append((tx, expense_type, direction, ca_category_uuid, payment_id_key))

    logger.info(
        "extrato_ingester %s: %d gap lines to check (%d internal skips)",
        seller_slug,
        len(classified),
        stats["skipped_internal"],
    )

    if not classified:
        return {
            "seller":           seller_slug,
            "total_lines":      len(transactions) + stats["skipped_internal"],
            "skipped_internal": stats["skipped_internal"],
            "already_covered":  0,
            "newly_ingested":   0,
            "errors":           0,
            "by_type":          {},
            "summary":          summary,
        }

    # Batch lookups
    all_ref_ids = list({item[0]["reference_id"] for item in classified})

    payment_ids_in_db = await _batch_lookup_payment_ids(db, seller_slug, all_ref_ids)
    expense_ids_in_db = _batch_lookup_expense_payment_ids(db, seller_slug, all_ref_ids)

    # Resolve _CHECK_PAYMENTS
    resolved: list[tuple[dict, str, str, Optional[str], str]] = []
    for tx, expense_type, direction, ca_category_uuid, payment_id_key in classified:
        if expense_type == _CHECK_PAYMENTS:
            ref_id = tx["reference_id"]
            if ref_id in payment_ids_in_db:
                stats["skipped_internal"] += 1
                continue
            fallback_type, fallback_dir = _resolve_check_payments(tx["transaction_type"])
            abbrev = _EXPENSE_TYPE_ABBREV.get(fallback_type, "xx")
            payment_id_key = f"{ref_id}:{abbrev}"
            logger.warning(
                "extrato_ingester %s: ref_id %s NOT in payments (type=%r) — "
                "ingesting as %s (ML API gap)",
                seller_slug,
                ref_id,
                tx["transaction_type"],
                fallback_type,
            )
            resolved.append((tx, fallback_type, fallback_dir, None, payment_id_key))
        else:
            resolved.append((tx, expense_type, direction, ca_category_uuid, payment_id_key))

    classified = resolved

    composite_keys = [item[4] for item in classified]
    composite_ids_in_db = _batch_lookup_composite_expense_ids(db, seller_slug, composite_keys)
    refunded_payment_ids_in_db = await _batch_lookup_refunded_payment_ids(db, seller_slug, all_ref_ids)

    logger.info(
        "extrato_ingester %s: found %d in payments (%d refunded), %d in expenses (plain), %d in expenses (composite)",
        seller_slug,
        len(payment_ids_in_db),
        len(refunded_payment_ids_in_db),
        len(expense_ids_in_db),
        len(composite_ids_in_db),
    )

    # Write uncovered lines to event ledger
    by_type: Counter = Counter()

    for tx, expense_type, direction, ca_category_uuid, payment_id_key in classified:
        ref_id = tx["reference_id"]

        if payment_id_key in composite_ids_in_db:
            stats["already_covered"] += 1
            continue

        if ref_id in payment_ids_in_db:
            if expense_type == "debito_divida_disputa":
                if ref_id in refunded_payment_ids_in_db:
                    stats["already_covered"] += 1
                    logger.debug(
                        "extrato_ingester %s: %s debito_divida_disputa skipped — processor already refunded",
                        seller_slug,
                        ref_id,
                    )
                    continue
            elif expense_type in ("reembolso_disputa", "reembolso_generico",
                                  "entrada_dinheiro", "dinheiro_retido",
                                  "liberacao_cancelada", "debito_envio_ml",
                                  "bonus_envio", "debito_troca"):
                pass
            else:
                stats["already_covered"] += 1
                continue

        # Plain ref_id already captured as expense event (API path)
        if ref_id in expense_ids_in_db:
            stats["already_covered"] += 1
            continue

        # Fuzzy dedup for faturas_ml / collection with internal ML IDs
        if expense_type in ("faturas_ml", "collection"):
            extrato_amount = abs(tx["amount"])
            if _fuzzy_match_expense(
                db, seller_slug, extrato_amount, tx["date"],
                ["faturas_ml", "collection"],
            ):
                stats["already_covered"] += 1
                logger.debug(
                    "extrato_ingester %s: %s fuzzy-matched existing faturas_ml/collection "
                    "(amount=%.2f date=%s), skipping",
                    seller_slug, ref_id, extrato_amount, tx["date"],
                )
                continue

        # Write to event ledger (idempotent via ON CONFLICT DO NOTHING)
        description = _build_extrato_description(tx, expense_type)
        await _write_extrato_expense_events(
            seller_slug, payment_id_key, expense_type, direction,
            ca_category_uuid, tx, description,
        )
        stats["newly_ingested"] += 1
        by_type[expense_type] += 1
        logger.info(
            "extrato_ingester %s: ingested %s type=%s dir=%s amount=%.2f",
            seller_slug,
            payment_id_key,
            expense_type,
            direction,
            abs(tx["amount"]),
        )

    total_lines = len(transactions) + stats["skipped_internal"]
    result = {
        "seller":           seller_slug,
        "total_lines":      total_lines,
        "skipped_internal": stats["skipped_internal"],
        "already_covered":  stats["already_covered"],
        "newly_ingested":   stats["newly_ingested"],
        "errors":           stats["errors"],
        "by_type":          dict(by_type),
        "summary":          summary,
    }
    logger.info("extrato_ingester %s: upload result — %s", seller_slug, {
        k: v for k, v in result.items() if k not in ("summary", "by_type")
    })
    logger.info("extrato_ingester %s: upload by_type=%s", seller_slug, dict(by_type))
    return result


# ---------------------------------------------------------------------------
# All-sellers entry point
# ---------------------------------------------------------------------------


async def ingest_extrato_all_sellers(lookback_days: int = 3) -> list[dict]:
    """Run extrato ingestion for all active sellers (D-1 to D-{lookback_days}).

    This is the nightly pipeline entry point. Runs sequentially per seller to
    avoid overloading the ML API or Supabase rate limits.

    Args:
        lookback_days: Number of days to look back from yesterday (inclusive).
                       Default 3 matches the daily sync window.

    Returns:
        List of per-seller result dicts from ingest_extrato_for_seller().
    """
    db = get_db()
    sellers = get_all_active_sellers(db)

    now_brt = datetime.now(BRT)
    end_date   = (now_brt - timedelta(days=1)).strftime("%Y-%m-%d")
    begin_date = (now_brt - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    logger.info(
        "extrato_ingester: starting for %d sellers, window %s → %s",
        len(sellers),
        begin_date,
        end_date,
    )

    results: list[dict] = []
    for seller in sellers:
        slug = seller["slug"]
        try:
            result = await ingest_extrato_for_seller(slug, begin_date, end_date)
            results.append(result)
        except Exception as exc:
            logger.error(
                "extrato_ingester: unhandled error for %s — %s",
                slug,
                exc,
                exc_info=True,
            )
            results.append({"seller": slug, "error": str(exc)})

    _last_ingestion_result["ran_at"] = datetime.now(timezone.utc).isoformat()
    _last_ingestion_result["results"] = results

    total_ingested = sum(r.get("newly_ingested", 0) for r in results)
    total_errors   = sum(r.get("errors", 0) for r in results)
    logger.info(
        "extrato_ingester: completed. total_ingested=%d total_errors=%d",
        total_ingested,
        total_errors,
    )
    return results


def get_last_ingestion_result() -> dict:
    """Return the in-memory result of the last ingestion run.

    Returns:
        Dict with keys: ran_at (ISO timestamp or None), results (list of
        per-seller dicts).
    """
    return _last_ingestion_result

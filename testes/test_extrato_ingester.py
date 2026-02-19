#!/usr/bin/env python3
"""
Test script for extrato_ingester.py
Tests extrato parsing, classification, and coverage against real CSV data.

Usage:
    cd "/Volumes/SSD Eryk/financeiro v2/lever money claude v3"
    python3 testes/test_extrato_ingester.py

What it tests:
1. CSV parsing (Brazilian number format, semicolons, headers)
2. Classification of all known TRANSACTION_TYPE strings (skip vs classify)
3. Composite payment_id generation for idempotency
4. Dispute group handling (same REFERENCE_ID, multiple tx types)
5. Brazilian number parsing (1.234,56 -> 1234.56)
6. Coverage: after classification, zero unclassified lines from real extrato
7. Text normalisation (accent stripping)
8. Skip rules for already-covered lines (liberacao, PIX, boleto)
9. Summary section parsing from account_statement CSV

All tests use real CSV files from testes/extratos/ or inline sample data.
No API calls. No Supabase writes.
"""
import sys
import logging
from pathlib import Path

# ── Project setup ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

# ── Paths ──────────────────────────────────────────────────────────────────────
EXTRATOS_DIR = PROJECT_ROOT / "testes" / "extratos"

EXTRATO_FILES = {
    "141air":        "extrato janeiro 141Air.csv",
    "net-air":       "extrato janeiro netair.csv",
    "netparts-sp":   "extrato janeiro netparts.csv",
    "easy":          "extrato janeiro Easyutilidades.csv",
}

# ── Result tracking ────────────────────────────────────────────────────────────
_results: list[dict] = []


def _pass(name: str, detail: str = "") -> None:
    _results.append({"name": name, "status": "PASS", "detail": detail})
    print(f"  {GREEN}PASS{RESET}  {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, detail: str = "") -> None:
    _results.append({"name": name, "status": "FAIL", "detail": detail})
    print(f"  {RED}FAIL{RESET}  {name}" + (f" — {detail}" if detail else ""))


def _skip(name: str, reason: str) -> None:
    _results.append({"name": name, "status": "SKIP", "detail": reason})
    print(f"  {YELLOW}SKIP{RESET}  {name} — {reason}")


# ── Import the module under test ───────────────────────────────────────────────

try:
    from app.services.extrato_ingester import (
        _parse_account_statement,
        _classify_extrato_line,
        _parse_br_number,
        _normalize_text,
        _build_expense_from_extrato,
        _EXPENSE_TYPE_ABBREV,
        EXTRATO_CLASSIFICATION_RULES,
    )
    _IMPORT_OK = True
except ImportError as _e:
    _IMPORT_OK = False
    _IMPORT_ERR = str(_e)


def _require_import(name: str) -> bool:
    """Print skip and return False when the module could not be imported."""
    if not _IMPORT_OK:
        _skip(name, f"import failed: {_IMPORT_ERR}")
        return False
    return True


# ── 1. Brazilian number parsing ───────────────────────────────────────────────


def test_brazilian_number_parsing() -> None:
    name = "brazilian_number_parsing"
    if not _require_import(name):
        return

    cases = [
        # (input_string,  expected_float)
        ("1.234,56",      1234.56),
        ("-210.571,52",   -210571.52),
        ("0,00",          0.0),
        ("3.994,84",      3994.84),
        ("-20,36",        -20.36),
        ("4.476,23",      4476.23),
        ("207.185,69",    207185.69),
        ("-350,00",       -350.0),
        ("",              0.0),
        ("  ",            0.0),
        ("bad",           0.0),      # Graceful parse error
        ("100",           100.0),    # No decimal separator
        ("-88,57",        -88.57),
    ]

    failures = []
    for raw, expected in cases:
        got = _parse_br_number(raw)
        if abs(got - expected) > 1e-9:
            failures.append(f"{raw!r}: expected {expected}, got {got}")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, f"all {len(cases)} number formats parsed correctly")


# ── 2. Text normalisation ─────────────────────────────────────────────────────


def test_normalize_text() -> None:
    name = "normalize_text"
    if not _require_import(name):
        return

    cases = [
        # (input,                          expected_output)
        ("Liberação de dinheiro",          "liberacao de dinheiro"),
        ("Débito por dívida",              "debito por divida"),
        ("Transferência Pix enviada",      "transferencia pix enviada"),
        ("Reembolso Reclamações",          "reembolso reclamacoes"),
        ("Diferença da alíquota (DIFAL)",  "diferenca da aliquota (difal)"),
        ("Dinheiro retido",                "dinheiro retido"),
        ("Bônus por Envio",                "bonus por envio"),
        ("UPPER CASE",                     "upper case"),
        ("já foi",                         "ja foi"),
    ]

    failures = []
    for raw, expected in cases:
        got = _normalize_text(raw)
        if got != expected:
            failures.append(f"{raw!r}: expected {expected!r}, got {got!r}")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, f"all {len(cases)} accent normalisations correct")


# ── 3. Classification rules — skip vs classify ────────────────────────────────


def test_classification_rules() -> None:
    """Feed all known TRANSACTION_TYPE patterns through the classifier and
    verify each maps to the expected expense_type and direction."""
    name = "classification_rules"
    if not _require_import(name):
        return

    # (raw_tx_type,                                     expected_expense_type, expected_direction)
    # None expense_type means the line should be SKIPPED (already covered)
    cases = [
        # SKIPS — covered by processor or mp_expenses via API
        ("Liberação de dinheiro ",                         None,                    None),
        ("Liberacao de dinheiro ",                         None,                    None),
        ("Transferência Pix enviada JOAO SILVA",           None,                    None),
        ("Pix enviado",                                    None,                    None),
        ("Pagamento de conta Itaú Unibanco S.A.",          None,                    None),
        ("Pagamento com Código QR Pix DAVID JHONY",        None,                    None),
        # INCOME
        ("Reembolso Reclamações e devoluções",             "reembolso_disputa",     "income"),
        ("Reembolso Envio cancelado a Joao",               "reembolso_disputa",     "income"),
        ("Reembolso de tarifas cobradas",                  "reembolso_generico",    "income"),
        ("Reembolso ",                                     "reembolso_generico",    "income"),
        ("Entrada de dinheiro ",                           "entrada_dinheiro",      "income"),
        ("Dinheiro recebido ",                             "deposito_avulso",       "income"),
        ("Bônus por Envio",                                "bonus_envio",           "income"),
        # EXPENSES
        ("Dinheiro retido Reclamações e devoluções",       "dinheiro_retido",       "expense"),
        ("Débito por dívida Diferença da alíquota (DIFAL)","difal",                 "expense"),
        ("Debito por divida DIFAL 2025",                   "difal",                 "expense"),
        ("Débito por dívida Faturas vencidas do ML",       "faturas_ml",            "expense"),
        ("Envio do Mercado Livre retroativo",              "debito_envio_ml",       "expense"),
        ("Reclamações no Mercado Livre",                   "debito_divida_disputa", "expense"),
        ("Reclamacoes no Mercado Livre",                   "debito_divida_disputa", "expense"),
        ("Troca de produto",                               "debito_troca",          "expense"),
        # LIBERACAO CANCELADA — special: is an expense even though name has "liberacao"
        ("Liberação de dinheiro cancelada",                "liberacao_cancelada",   "expense"),
    ]

    failures = []
    for tx_type, expected_type, expected_dir in cases:
        got_type, got_dir, got_cat = _classify_extrato_line(tx_type)

        if got_type != expected_type:
            failures.append(
                f"tx_type={tx_type!r}: expense_type expected={expected_type!r}, got={got_type!r}"
            )
        if got_dir != expected_dir:
            failures.append(
                f"tx_type={tx_type!r}: direction expected={expected_dir!r}, got={got_dir!r}"
            )

    if failures:
        _fail(name, f"{len(failures)} mismatches:\n    " + "\n    ".join(failures))
    else:
        _pass(name, f"all {len(cases)} classification rules produce correct results")


def test_skip_rules() -> None:
    """Verify that the skip rules for already-covered lines return all-None."""
    name = "skip_rules"
    if not _require_import(name):
        return

    skip_patterns = [
        "Liberação de dinheiro ",
        "Transferência Pix enviada EMPRESA XYZ",
        "Pix enviado para 12345",
        "Pagamento de conta BRADESCO",
        "Pagamento com QR Code",
        "Compra Mercado Libre produto ABC",
        "Transferencia enviada",
        "Transferência de saldo",
        "Pagamento cartão de crédito",
    ]

    failures = []
    for tx in skip_patterns:
        et, direction, cat = _classify_extrato_line(tx)
        if et is not None or direction is not None:
            failures.append(
                f"{tx!r} should be SKIP but got expense_type={et!r}, direction={direction!r}"
            )

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, f"all {len(skip_patterns)} skip patterns correctly return (None, None, None)")


# ── 4. Composite payment_id for idempotency ───────────────────────────────────


def test_composite_payment_id() -> None:
    """Verify that the composite payment_id format is stable and that different
    expense_types on the same reference_id produce different keys."""
    name = "composite_payment_id"
    if not _require_import(name):
        return

    ref_id = "135321847364"

    # Three lines that can share the same reference_id in a dispute group
    types_expected = [
        ("debito_divida_disputa", f"{ref_id}:dd"),
        ("entrada_dinheiro",      f"{ref_id}:ed"),
        ("reembolso_disputa",     f"{ref_id}:rd"),
    ]

    failures = []
    keys_generated = set()
    for expense_type, expected_key in types_expected:
        abbrev = _EXPENSE_TYPE_ABBREV.get(expense_type, "xx")
        got_key = f"{ref_id}:{abbrev}"
        keys_generated.add(got_key)
        if got_key != expected_key:
            failures.append(f"type={expense_type!r}: expected {expected_key!r}, got {got_key!r}")

    # All three keys must be distinct
    if len(keys_generated) != len(types_expected):
        failures.append(f"keys not distinct: {keys_generated}")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, f"3 distinct composite keys for ref_id={ref_id!r}")


def test_composite_key_for_all_expense_types() -> None:
    """Every expense_type in the system must have an abbreviation in
    _EXPENSE_TYPE_ABBREV so composite keys never fall back to 'xx'."""
    name = "composite_key_for_all_expense_types"
    if not _require_import(name):
        return

    # Collect all expense_types produced by EXTRATO_CLASSIFICATION_RULES
    classified_types = set()
    for _pattern, expense_type, _direction, _cat in EXTRATO_CLASSIFICATION_RULES:
        if expense_type is not None:
            classified_types.add(expense_type)

    missing_abbrev = classified_types - set(_EXPENSE_TYPE_ABBREV.keys())
    if missing_abbrev:
        _fail(name, f"expense_types missing abbreviations: {missing_abbrev}")
    else:
        _pass(
            name,
            f"all {len(classified_types)} classified expense_types have abbreviations",
        )


# ── 5. Dispute group handling ─────────────────────────────────────────────────


def test_dispute_group() -> None:
    """Simulate the 3-line dispute group from extrato 141Air (ref 135321847364):
    debito_divida_disputa + entrada_dinheiro + reembolso_disputa.
    All three should classify to separate expense_types with distinct composite keys."""
    name = "dispute_group"
    if not _require_import(name):
        return

    # Replicated from real 141Air extrato (January 2026, ref 139749344683)
    dispute_lines = [
        {
            "date": "2026-01-02",
            "transaction_type": "Débito por dívida Reclamações no Mercado Livre",
            "reference_id": "139749344683",
            "amount": -4850.00,
            "balance": 60.46,
        },
        {
            "date": "2026-01-02",
            "transaction_type": "Entrada de dinheiro ",
            "reference_id": "139749344683",
            "amount": 531.95,
            "balance": 4378.51,  # Not exact from file but representative
        },
        {
            "date": "2026-01-02",
            "transaction_type": "Reembolso Envío cancelado a Lilian Barbosa Oliveira",
            "reference_id": "139749344683",
            "amount": 531.95,
            "balance": 4910.46,
        },
    ]

    ref_id = "139749344683"
    classified = []
    keys_seen = set()
    failures = []

    for tx in dispute_lines:
        et, direction, cat = _classify_extrato_line(tx["transaction_type"])
        if et is None and direction is None:
            failures.append(
                f"tx_type={tx['transaction_type']!r} should NOT be skipped in dispute group"
            )
            continue

        abbrev = _EXPENSE_TYPE_ABBREV.get(et, "xx") if et else "xx"
        key = f"{ref_id}:{abbrev}"

        if key in keys_seen:
            failures.append(f"duplicate composite key {key!r} for ref_id={ref_id!r}")
        keys_seen.add(key)

        classified.append({
            "tx": tx,
            "expense_type": et,
            "direction": direction,
            "key": key,
        })

    if len(classified) != 3:
        failures.append(f"expected 3 classified lines, got {len(classified)}")

    # Verify directions are coherent
    directions = {c["direction"] for c in classified}
    if "expense" not in directions or "income" not in directions:
        failures.append(f"expected both expense and income directions, got: {directions}")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(
            name,
            f"dispute group: {[(c['expense_type'], c['direction']) for c in classified]}",
        )


# ── 6. CSV parsing ────────────────────────────────────────────────────────────


def test_parse_account_statement() -> None:
    """Parse real account_statement CSV (141Air January 2026) and verify the
    output structure."""
    name = "parse_account_statement"
    if not _require_import(name):
        return

    extrato_path = EXTRATOS_DIR / EXTRATO_FILES["141air"]
    if not extrato_path.exists():
        _skip(name, f"extrato file not found: {extrato_path}")
        return

    try:
        csv_text = extrato_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            csv_text = extrato_path.read_text(encoding="latin-1")
        except Exception as exc:
            _fail(name, f"cannot read extrato file: {exc}")
            return

    try:
        summary, transactions = _parse_account_statement(csv_text)
    except Exception as exc:
        _fail(name, f"_parse_account_statement raised: {exc}")
        return

    # Validate summary
    required_summary_keys = {"initial_balance", "credits", "debits", "final_balance"}
    missing_s = required_summary_keys - set(summary.keys())
    if missing_s:
        _fail(name, f"summary missing keys: {missing_s}")
        return

    # Known values from extrato 141Air January 2026
    expected_initial = 4476.23
    expected_credits = 207185.69
    if abs(summary["initial_balance"] - expected_initial) > 0.01:
        _fail(
            name,
            f"initial_balance: expected {expected_initial}, got {summary['initial_balance']}",
        )
        return
    if abs(summary["credits"] - expected_credits) > 0.01:
        _fail(
            name,
            f"credits: expected {expected_credits}, got {summary['credits']}",
        )
        return

    # Validate transaction structure
    if not transactions:
        _fail(name, "no transactions parsed")
        return

    required_tx_keys = {"date", "transaction_type", "reference_id", "amount", "balance"}
    for i, tx in enumerate(transactions[:5]):
        missing_tx = required_tx_keys - set(tx.keys())
        if missing_tx:
            _fail(name, f"transaction[{i}] missing keys: {missing_tx}")
            return

    # Validate date format (YYYY-MM-DD after conversion from DD-MM-YYYY)
    first_date = transactions[0]["date"]
    if not (len(first_date) == 10 and first_date[4] == "-" and first_date[7] == "-"):
        _fail(name, f"unexpected date format: {first_date!r}")
        return

    # Validate amounts are floats
    for tx in transactions[:10]:
        if not isinstance(tx["amount"], float):
            _fail(name, f"amount is not float: {tx['amount']!r}")
            return

    _pass(
        name,
        f"summary OK (initial={expected_initial}, credits={expected_credits}), "
        f"{len(transactions)} transactions parsed",
    )


def test_parse_inline_csv() -> None:
    """Parse a small inline CSV with known values to verify the parser
    independently of file I/O."""
    name = "parse_inline_csv"
    if not _require_import(name):
        return

    # Minimal valid account_statement CSV
    inline_csv = (
        "INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE\n"
        "1.000,00;5.000,00;-4.500,00;1.500,00\n"
        "\n"
        "RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE\n"
        "01-01-2026;Liberacao de dinheiro ;111111111111;3.994,84;4.994,84\n"
        "01-01-2026;Transferencia Pix enviada JOAO;222222222222;-350,00;4.644,84\n"
        "02-01-2026;Debito por divida Diferenca da aliquota (DIFAL);333333333333;-20,36;4.624,48\n"
        "02-01-2026;Reembolso Reclamacoes;444444444444;531,95;5.156,43\n"
        "03-01-2026;Dinheiro retido Reclamacoes e devolucoes;555555555555;-88,57;5.067,86\n"
    )

    try:
        summary, transactions = _parse_account_statement(inline_csv)
    except Exception as exc:
        _fail(name, f"parser raised: {exc}")
        return

    failures = []

    if abs(summary.get("initial_balance", 0) - 1000.0) > 0.01:
        failures.append(f"initial_balance: got {summary.get('initial_balance')}")
    if abs(summary.get("credits", 0) - 5000.0) > 0.01:
        failures.append(f"credits: got {summary.get('credits')}")
    if abs(summary.get("final_balance", 0) - 1500.0) > 0.01:
        failures.append(f"final_balance: got {summary.get('final_balance')}")

    if len(transactions) != 5:
        failures.append(f"expected 5 transactions, got {len(transactions)}")
    else:
        # Check specific amounts
        if abs(transactions[0]["amount"] - 3994.84) > 0.01:
            failures.append(f"tx[0] amount: {transactions[0]['amount']}")
        if abs(transactions[1]["amount"] - (-350.0)) > 0.01:
            failures.append(f"tx[1] amount: {transactions[1]['amount']}")
        if abs(transactions[2]["amount"] - (-20.36)) > 0.01:
            failures.append(f"tx[2] amount: {transactions[2]['amount']}")

        # Verify date conversion
        if transactions[0]["date"] != "2026-01-01":
            failures.append(f"date conversion: {transactions[0]['date']!r}")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, "inline CSV parsed correctly (5 transactions, correct amounts and dates)")


# ── 7. Coverage: zero unclassified lines from real extrato ────────────────────


def test_coverage_after_classification() -> None:
    """Load the 141Air extrato, classify every transaction, and verify that
    EVERY line either maps to a known expense_type or is an explicit skip.
    There must be ZERO lines falling through to 'other'."""
    name = "coverage_after_classification_141air"
    if not _require_import(name):
        return

    extrato_path = EXTRATOS_DIR / EXTRATO_FILES["141air"]
    if not extrato_path.exists():
        _skip(name, f"extrato file not found: {extrato_path}")
        return

    try:
        csv_text = extrato_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        csv_text = extrato_path.read_text(encoding="latin-1")

    _, transactions = _parse_account_statement(csv_text)

    skipped   = 0
    classified = 0
    unknown   = []

    for tx in transactions:
        et, direction, _cat = _classify_extrato_line(tx["transaction_type"])

        if et is None and direction is None:
            # Explicit skip — already covered by processor
            skipped += 1
        elif et == "other":
            # Fell through all rules — this is the warning path
            unknown.append(tx["transaction_type"])
        else:
            classified += 1

    total = len(transactions)
    coverage_pct = 100.0 * (skipped + classified) / total if total else 0.0

    if unknown:
        _fail(
            name,
            f"{len(unknown)} unclassified ('other') lines out of {total}. "
            f"First 3: {unknown[:3]}",
        )
    else:
        _pass(
            name,
            f"{total} lines: {classified} classified + {skipped} skipped = "
            f"{coverage_pct:.1f}% coverage, 0 unknown",
        )


def test_coverage_all_sellers() -> None:
    """Run classification coverage check across all four January 2026 extrato
    files. Tallies unknown ('other') lines per seller."""
    name = "coverage_all_sellers"
    if not _require_import(name):
        return

    any_found = False
    all_failures = []

    for seller, filename in EXTRATO_FILES.items():
        path = EXTRATOS_DIR / filename
        if not path.exists():
            continue

        any_found = True
        try:
            try:
                csv_text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                csv_text = path.read_text(encoding="latin-1")

            _, transactions = _parse_account_statement(csv_text)
        except Exception as exc:
            all_failures.append(f"{seller}: parse error — {exc}")
            continue

        unknown = [
            tx["transaction_type"]
            for tx in transactions
            if _classify_extrato_line(tx["transaction_type"])[0] == "other"
        ]

        if unknown:
            all_failures.append(
                f"{seller}: {len(unknown)} unclassified lines. "
                f"Examples: {unknown[:2]}"
            )

    if not any_found:
        _skip(name, "no extrato files found in testes/extratos/")
        return

    if all_failures:
        _fail(name, " | ".join(all_failures))
    else:
        sellers_checked = sum(
            1 for f in EXTRATO_FILES.values()
            if (EXTRATOS_DIR / f).exists()
        )
        _pass(name, f"0 unclassified lines across {sellers_checked} seller extratos")


# ── 8. _build_expense_from_extrato output structure ──────────────────────────


def test_build_expense_row_structure() -> None:
    """Verify that _build_expense_from_extrato returns a dict with all required
    mp_expenses schema fields."""
    name = "build_expense_row_structure"
    if not _require_import(name):
        return

    from app.models.sellers import CA_CATEGORIES

    sample_tx = {
        "date": "2026-01-02",
        "transaction_type": "Débito por dívida Diferença da alíquota (DIFAL)",
        "reference_id": "2728587235",
        "amount": -20.36,
        "balance": 4459.87,
    }

    try:
        row = _build_expense_from_extrato(
            tx=sample_tx,
            seller_slug="141air",
            expense_type="difal",
            direction="expense",
            ca_category_uuid=CA_CATEGORIES.get("tarifa_pagamento"),
            payment_id_key="2728587235:df",
        )
    except Exception as exc:
        _fail(name, f"_build_expense_from_extrato raised: {exc}")
        return

    required_fields = {
        "seller_slug",
        "payment_id",
        "expense_type",
        "expense_direction",
        "ca_category",
        "auto_categorized",
        "amount",
        "description",
        "operation_type",
        "date_created",
        "date_approved",
        "notes",
        "status",
        "raw_payment",
    }

    missing = required_fields - set(row.keys())
    if missing:
        _fail(name, f"missing fields: {missing}")
        return

    failures = []

    if row["seller_slug"] != "141air":
        failures.append(f"seller_slug: {row['seller_slug']!r}")
    if row["payment_id"] != "2728587235:df":
        failures.append(f"payment_id: {row['payment_id']!r}")
    if row["expense_type"] != "difal":
        failures.append(f"expense_type: {row['expense_type']!r}")
    if row["expense_direction"] != "expense":
        failures.append(f"expense_direction: {row['expense_direction']!r}")
    # amount should be stored as positive (direction conveys sign)
    if abs(row["amount"] - 20.36) > 0.001:
        failures.append(f"amount: {row['amount']} (should be positive abs value)")
    if row["auto_categorized"] is not True:
        failures.append(f"auto_categorized: {row['auto_categorized']!r} (expected True with CA category)")
    if row["status"] != "auto_categorized":
        failures.append(f"status: {row['status']!r}")
    if row["date_created"] != "2026-01-02":
        failures.append(f"date_created: {row['date_created']!r}")

    # raw_payment must be a dict with source=account_statement
    rp = row["raw_payment"]
    if not isinstance(rp, dict):
        failures.append("raw_payment is not a dict")
    elif rp.get("source") != "account_statement":
        failures.append(f"raw_payment.source: {rp.get('source')!r}")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, "all required mp_expenses fields present with correct values")


def test_build_expense_row_pending_review() -> None:
    """When no ca_category_uuid is provided, the row status should be
    'pending_review' and auto_categorized should be False."""
    name = "build_expense_row_pending_review"
    if not _require_import(name):
        return

    sample_tx = {
        "date": "2026-01-02",
        "transaction_type": "Dinheiro retido Reclamações e devoluções",
        "reference_id": "138913863776",
        "amount": -88.57,
        "balance": 4910.46,
    }

    try:
        row = _build_expense_from_extrato(
            tx=sample_tx,
            seller_slug="141air",
            expense_type="dinheiro_retido",
            direction="expense",
            ca_category_uuid=None,
            payment_id_key="138913863776:dr",
        )
    except Exception as exc:
        _fail(name, f"raised: {exc}")
        return

    if row["auto_categorized"] is not False:
        _fail(name, f"expected auto_categorized=False, got {row['auto_categorized']!r}")
        return
    if row["status"] != "pending_review":
        _fail(name, f"expected status='pending_review', got {row['status']!r}")
        return
    if row["ca_category"] is not None:
        _fail(name, f"expected ca_category=None, got {row['ca_category']!r}")
        return

    _pass(name, "pending_review row built correctly (auto_categorized=False, ca_category=None)")


# ── 9. Description template coverage ─────────────────────────────────────────


def test_description_templates() -> None:
    """Verify that every classified expense_type has a description template and
    that the template can be formatted without errors."""
    name = "description_templates"
    if not _require_import(name):
        return

    from app.services.extrato_ingester import _DESCRIPTION_TEMPLATES

    # Collect all non-None expense_types from the rules
    classified_types = set()
    for _pattern, expense_type, _direction, _cat in EXTRATO_CLASSIFICATION_RULES:
        if expense_type is not None:
            classified_types.add(expense_type)

    failures = []
    for et in sorted(classified_types):
        template = _DESCRIPTION_TEMPLATES.get(et)
        if template is None:
            failures.append(f"no template for expense_type={et!r}")
            continue
        # Verify the template can be formatted with sample values
        try:
            formatted = template.format(ref_id="TEST123", tx_type="test tx type")
        except KeyError as exc:
            failures.append(f"template for {et!r} has unknown placeholder: {exc}")
            continue
        if not formatted:
            failures.append(f"template for {et!r} formatted to empty string")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, f"all {len(classified_types)} expense_types have valid description templates")


# ── 10. Operation_type field in built rows ────────────────────────────────────


def test_operation_type_prefix() -> None:
    """The operation_type field in built rows should be 'extrato_{expense_type}'
    so Supabase queries can filter by source."""
    name = "operation_type_prefix"
    if not _require_import(name):
        return

    test_cases = [
        ("difal",               "extrato_difal"),
        ("reembolso_disputa",   "extrato_reembolso_disputa"),
        ("dinheiro_retido",     "extrato_dinheiro_retido"),
        ("debito_envio_ml",     "extrato_debito_envio_ml"),
    ]

    failures = []
    for expense_type, expected_op_type in test_cases:
        tx = {
            "date": "2026-01-01",
            "transaction_type": "Test",
            "reference_id": "111",
            "amount": -10.0,
            "balance": 0.0,
        }
        row = _build_expense_from_extrato(
            tx=tx,
            seller_slug="test",
            expense_type=expense_type,
            direction="expense",
            ca_category_uuid=None,
            payment_id_key=f"111:xx",
        )
        if row.get("operation_type") != expected_op_type:
            failures.append(
                f"expense_type={expense_type!r}: "
                f"expected operation_type={expected_op_type!r}, "
                f"got={row.get('operation_type')!r}"
            )

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, f"all {len(test_cases)} operation_type prefixes correct")


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    print()
    print("=" * 65)
    print("  Extrato Ingester — Test Suite")
    print(f"  Extrato files dir: testes/extratos/")
    print("=" * 65)

    print()
    print("--- Number & Text Parsing ---")
    test_brazilian_number_parsing()
    test_normalize_text()

    print()
    print("--- Classification Rules ---")
    test_classification_rules()
    test_skip_rules()

    print()
    print("--- Composite Payment ID (Idempotency) ---")
    test_composite_payment_id()
    test_composite_key_for_all_expense_types()

    print()
    print("--- Dispute Group Handling ---")
    test_dispute_group()

    print()
    print("--- CSV Parsing ---")
    test_parse_inline_csv()
    test_parse_account_statement()

    print()
    print("--- Coverage: Zero Unclassified Lines ---")
    test_coverage_after_classification()
    test_coverage_all_sellers()

    print()
    print("--- Row Building (mp_expenses schema) ---")
    test_build_expense_row_structure()
    test_build_expense_row_pending_review()
    test_description_templates()
    test_operation_type_prefix()

    # Summary
    print()
    print("=" * 65)
    passed  = sum(1 for r in _results if r["status"] == "PASS")
    failed  = sum(1 for r in _results if r["status"] == "FAIL")
    skipped = sum(1 for r in _results if r["status"] == "SKIP")
    total   = len(_results)

    print(
        f"  Results: {GREEN}{passed} passed{RESET}  "
        f"{RED}{failed} failed{RESET}  "
        f"{YELLOW}{skipped} skipped{RESET}  "
        f"({total} total)"
    )
    print("=" * 65)
    print()

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

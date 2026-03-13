"""
Unit tests for extrato_ingester.py — classification and parsing logic.

Tests all pure functions: parsing, normalization, classification rules,
smart-skip resolution. No DB or API calls.

Run: python3 -m pytest testes/test_extrato_classification.py -v
"""
import pytest

from app.services.extrato_ingester import (
    _parse_br_number,
    _normalize_text,
    _parse_account_statement,
    _classify_extrato_line,
    _resolve_check_payments,
    _build_expense_from_extrato,
    _CHECK_PAYMENTS,
    EXTRATO_CLASSIFICATION_RULES,
    _EXPENSE_TYPE_ABBREV,
    _DESCRIPTION_TEMPLATES,
    _CA_CATEGORY_CODE_MAP,
)


# ===========================================================================
# _parse_br_number
# ===========================================================================

class TestParseBrNumber:
    def test_positive_integer(self):
        assert _parse_br_number("100") == 100.0

    def test_positive_decimal(self):
        assert _parse_br_number("1.234,56") == 1234.56

    def test_negative_decimal(self):
        assert _parse_br_number("-210.571,52") == -210571.52

    def test_large_number(self):
        assert _parse_br_number("207.185,69") == 207185.69

    def test_small_decimal(self):
        assert _parse_br_number("0,23") == 0.23

    def test_zero(self):
        assert _parse_br_number("0,00") == 0.0

    def test_empty_string(self):
        assert _parse_br_number("") == 0.0

    def test_none(self):
        assert _parse_br_number(None) == 0.0

    def test_whitespace(self):
        assert _parse_br_number("  1.234,56  ") == 1234.56

    def test_no_thousands_separator(self):
        assert _parse_br_number("45,68") == 45.68


# ===========================================================================
# _normalize_text
# ===========================================================================

class TestNormalizeText:
    def test_accents_removed(self):
        assert _normalize_text("Liberação de dinheiro") == "liberacao de dinheiro"

    def test_cedilla(self):
        assert _normalize_text("Bonificação") == "bonificacao"

    def test_spanish_accent(self):
        assert _normalize_text("Envío cancelado") == "envio cancelado"

    def test_uppercase(self):
        assert _normalize_text("DIFAL") == "difal"

    def test_mixed(self):
        assert _normalize_text("Débito por dívida Diferença da alíquota (DIFAL)") == \
            "debito por divida diferenca da aliquota (difal)"

    def test_plain_ascii(self):
        assert _normalize_text("hello world") == "hello world"


# ===========================================================================
# _parse_account_statement
# ===========================================================================

class TestParseAccountStatement:
    def test_summary_parsing(self, sample_extrato_csv):
        summary, _ = _parse_account_statement(sample_extrato_csv)
        assert summary["initial_balance"] == pytest.approx(4476.23, abs=0.01)
        assert summary["credits"] == pytest.approx(207185.69, abs=0.01)
        assert summary["debits"] == pytest.approx(-210571.52, abs=0.01)
        assert summary["final_balance"] == pytest.approx(1090.40, abs=0.01)

    def test_transaction_count(self, sample_extrato_csv):
        _, transactions = _parse_account_statement(sample_extrato_csv)
        assert len(transactions) == 12

    def test_transaction_fields(self, sample_extrato_csv):
        _, transactions = _parse_account_statement(sample_extrato_csv)
        first = transactions[0]
        assert first["date"] == "2026-01-01"
        assert first["transaction_type"] == "Liberação de dinheiro"
        assert first["reference_id"] == "140282341986"
        assert first["amount"] == pytest.approx(45.68, abs=0.01)

    def test_negative_amount(self, sample_extrato_csv):
        _, transactions = _parse_account_statement(sample_extrato_csv)
        # Claude.ai subscription line
        subscription = next(t for t in transactions if "Claude" in t["transaction_type"])
        assert subscription["amount"] == pytest.approx(-569.25, abs=0.01)
        assert subscription["reference_id"] == "141215405790"

    def test_date_format_conversion(self, sample_extrato_csv):
        """DD-MM-YYYY in CSV → YYYY-MM-DD in output."""
        _, transactions = _parse_account_statement(sample_extrato_csv)
        for tx in transactions:
            assert len(tx["date"]) == 10
            assert tx["date"][4] == "-"
            assert tx["date"][7] == "-"

    def test_large_negative(self, sample_extrato_csv):
        _, transactions = _parse_account_statement(sample_extrato_csv)
        # Credit card payment
        cc = next(t for t in transactions if "Cartão" in t["transaction_type"])
        assert cc["amount"] == pytest.approx(-3010.62, abs=0.01)

    def test_empty_csv(self):
        summary, transactions = _parse_account_statement("")
        assert summary == {}
        assert transactions == []


# ===========================================================================
# _classify_extrato_line — THE classification engine
# ===========================================================================

class TestClassifyExtratoLine:
    """Tests every classification rule in EXTRATO_CLASSIFICATION_RULES.

    Each test uses a real TRANSACTION_TYPE from the 141air January 2026 extrato.
    """

    # --- Conditional skips (smart skip) ---

    def test_liberacao_dinheiro(self):
        """'Liberação de dinheiro' → _CHECK_PAYMENTS (conditional skip)."""
        exp_type, direction, cat = _classify_extrato_line("Liberação de dinheiro")
        assert exp_type == _CHECK_PAYMENTS
        assert direction == "income"

    def test_pagamento_com_qr(self):
        """'Pagamento com Código QR Pix ...' → _CHECK_PAYMENTS."""
        exp_type, direction, cat = _classify_extrato_line(
            "Pagamento com Código QR Pix PEDRO DUARTE CAMARGO BARROS"
        )
        assert exp_type == _CHECK_PAYMENTS
        assert direction == "income"

    def test_dinheiro_recebido(self):
        """'Dinheiro recebido' → _CHECK_PAYMENTS."""
        exp_type, direction, cat = _classify_extrato_line("Dinheiro recebido")
        assert exp_type == _CHECK_PAYMENTS
        assert direction == "income"

    # --- Unconditional skips ---

    def test_transferencia_pix_skip(self):
        exp_type, direction, cat = _classify_extrato_line("Transferência Pix para fulano")
        assert exp_type is None
        assert direction is None

    def test_pix_enviado_skip(self):
        exp_type, direction, cat = _classify_extrato_line("Pix enviado para conta X")
        assert exp_type is None
        assert direction is None

    def test_pagamento_de_conta_skip(self):
        exp_type, direction, cat = _classify_extrato_line("Pagamento de conta Boleto Itau")
        assert exp_type is None
        assert direction is None

    def test_compra_mercado_libre_skip(self):
        exp_type, direction, cat = _classify_extrato_line("Compra Mercado Libre - item X")
        assert exp_type is None
        assert direction is None

    def test_compra_de_skip(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Compra de Adaptador Acelerador Piloto Automático"
        )
        assert exp_type is None
        assert direction is None

    def test_transferencia_enviada_skip(self):
        exp_type, direction, cat = _classify_extrato_line("Transferência enviada para conta Y")
        assert exp_type is None
        assert direction is None

    def test_transferencia_saldo_skip(self):
        exp_type, direction, cat = _classify_extrato_line("Transferência de saldo")
        assert exp_type is None
        assert direction is None

    # --- Income types ---

    def test_reembolso_reclamacoes(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Reembolso Reclamações e devoluções"
        )
        assert exp_type == "reembolso_disputa"
        assert direction == "income"
        assert cat is not None  # has CA category (1.3.4)

    def test_reembolso_envio_cancelado(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Reembolso Envío cancelado a Manoel Jackson Dias Da Silva"
        )
        assert exp_type == "reembolso_disputa"
        assert direction == "income"

    def test_reembolso_de_tarifas(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Reembolso Reembolso de tarifas"
        )
        assert exp_type == "reembolso_generico"
        assert direction == "income"

    def test_reembolso_generic(self):
        """Plain 'Reembolso' without qualifier."""
        exp_type, direction, cat = _classify_extrato_line("Reembolso")
        assert exp_type == "reembolso_generico"
        assert direction == "income"

    def test_entrada_dinheiro(self):
        exp_type, direction, cat = _classify_extrato_line("Entrada de dinheiro")
        assert exp_type == "entrada_dinheiro"
        assert direction == "income"
        assert cat is None  # pending_review

    def test_bonus_envio(self):
        exp_type, direction, cat = _classify_extrato_line("Bônus por envio")
        assert exp_type == "bonus_envio"
        assert direction == "income"
        assert cat is not None  # 1.3.7

    def test_bonificacao(self):
        """'Bonificação' → bonus_envio (pattern added in FIX 8)."""
        exp_type, direction, cat = _classify_extrato_line("Bonificação")
        assert exp_type == "bonus_envio"
        assert direction == "income"

    def test_transferencia_recebida(self):
        exp_type, direction, cat = _classify_extrato_line("Transferência recebida de conta Z")
        assert exp_type == "entrada_dinheiro"
        assert direction == "income"

    # --- Expense types ---

    def test_liberacao_cancelada(self):
        """Must match BEFORE 'liberacao de dinheiro' rule."""
        exp_type, direction, cat = _classify_extrato_line(
            "Liberação de dinheiro cancelada"
        )
        assert exp_type == "liberacao_cancelada"
        assert direction == "expense"

    def test_dinheiro_retido(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Dinheiro retido Reclamações e devoluções"
        )
        assert exp_type == "dinheiro_retido"
        assert direction == "expense"
        assert cat is None  # pending_review

    def test_difal(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Débito por dívida Diferença da aliquota (DIFAL)"
        )
        assert exp_type == "difal"
        assert direction == "expense"
        assert cat is not None  # 2.2.3

    def test_faturas_vencidas(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Débito por dívida Faturas vencidas do Mercado Livre"
        )
        assert exp_type == "faturas_ml"
        assert direction == "expense"
        assert cat is not None  # 2.8.2

    def test_envio_mercado_livre(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Débito por dívida Envio do Mercado Livre"
        )
        assert exp_type == "debito_envio_ml"
        assert direction == "expense"
        assert cat is not None  # 2.9.4

    def test_reclamacoes_mercado_livre(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Débito por dívida Reclamações no Mercado Livre"
        )
        assert exp_type == "debito_divida_disputa"
        assert direction == "expense"
        assert cat is None  # pending_review

    def test_troca_produto(self):
        exp_type, direction, cat = _classify_extrato_line(
            "Débito por dívida Troca de produto"
        )
        assert exp_type == "debito_troca"
        assert direction == "expense"

    def test_pagamento_cartao_credito(self):
        """FIX 3: credit card payments are real debits, not skips."""
        exp_type, direction, cat = _classify_extrato_line(
            "Pagamento Cartão de crédito"
        )
        assert exp_type == "pagamento_cartao_credito"
        assert direction == "expense"
        assert cat is None  # pending_review

    def test_subscription(self):
        """SaaS subscriptions: 'Pagamento X' (not 'de conta' or 'com QR')."""
        exp_type, direction, cat = _classify_extrato_line(
            "Pagamento Claude.ai subscription"
        )
        assert exp_type == "subscription"
        assert direction == "expense"

    def test_subscription_supabase(self):
        exp_type, direction, cat = _classify_extrato_line("Pagamento Supabase")
        assert exp_type == "subscription"
        assert direction == "expense"

    # --- Rule ordering ---

    def test_pagamento_de_conta_before_pagamento(self):
        """'Pagamento de conta' must skip (not classify as subscription)."""
        exp_type, _, _ = _classify_extrato_line("Pagamento de conta Boleto BB")
        assert exp_type is None  # skip

    def test_pagamento_com_before_pagamento(self):
        """'Pagamento com QR' → _CHECK_PAYMENTS (not subscription)."""
        exp_type, _, _ = _classify_extrato_line("Pagamento com Código QR Pix fulano")
        assert exp_type == _CHECK_PAYMENTS

    def test_liberacao_cancelada_before_liberacao(self):
        """'Liberação de dinheiro cancelada' → liberacao_cancelada (not _CHECK_PAYMENTS)."""
        exp_type, _, _ = _classify_extrato_line("Liberação de dinheiro cancelada")
        assert exp_type == "liberacao_cancelada"

    def test_reembolso_reclamacoes_before_generic_reembolso(self):
        """'Reembolso Reclamações' → reembolso_disputa (not reembolso_generico)."""
        exp_type, _, _ = _classify_extrato_line("Reembolso Reclamações e devoluções")
        assert exp_type == "reembolso_disputa"


# ===========================================================================
# _resolve_check_payments
# ===========================================================================

class TestResolveCheckPayments:
    def test_liberacao_fallback(self):
        fb_type, fb_dir = _resolve_check_payments("Liberação de dinheiro")
        assert fb_type == "liberacao_nao_sync"
        assert fb_dir == "income"

    def test_pagamento_com_fallback(self):
        fb_type, fb_dir = _resolve_check_payments(
            "Pagamento com Código QR Pix PEDRO DUARTE"
        )
        assert fb_type == "qr_pix_nao_sync"
        assert fb_dir == "income"

    def test_dinheiro_recebido_fallback(self):
        fb_type, fb_dir = _resolve_check_payments("Dinheiro recebido")
        assert fb_type == "dinheiro_recebido"
        assert fb_dir == "income"

    def test_unknown_fallback(self):
        """Unrecognized pattern defaults to 'other'."""
        fb_type, fb_dir = _resolve_check_payments("Unknown type XYZ")
        assert fb_type == "other"
        assert fb_dir == "expense"


# ===========================================================================
# _build_expense_from_extrato
# ===========================================================================

class TestBuildExpenseFromExtrato:
    def test_basic_expense(self):
        tx = {
            "date": "2026-01-14",
            "transaction_type": "Débito por dívida Diferença da aliquota (DIFAL)",
            "reference_id": "2728587235",
            "amount": -20.36,
            "balance": 3179.64,
        }
        row = _build_expense_from_extrato(
            tx, "141air", "difal", "expense",
            _CA_CATEGORY_CODE_MAP.get("2.2.3"), "2728587235:df",
        )
        assert row["seller_slug"] == "141air"
        assert row["payment_id"] == "2728587235:df"
        assert row["expense_type"] == "difal"
        assert row["expense_direction"] == "expense"
        assert row["amount"] == 20.36  # stored as positive
        assert row["date_approved"] == "2026-01-14"
        assert row["source"] == "extrato"
        assert row["status"] == "auto_categorized"  # has CA category
        assert row["ca_category"] is not None

    def test_pending_review_status(self):
        """Without CA category → pending_review."""
        tx = {
            "date": "2026-01-08",
            "transaction_type": "Dinheiro retido Reclamações e devoluções",
            "reference_id": "138209751237",
            "amount": -77.91,
            "balance": 0,
        }
        row = _build_expense_from_extrato(
            tx, "141air", "dinheiro_retido", "expense", None, "138209751237:dr",
        )
        assert row["status"] == "pending_review"
        assert row["ca_category"] is None

    def test_income_direction(self):
        tx = {
            "date": "2026-01-26",
            "transaction_type": "Reembolso Reclamações e devoluções",
            "reference_id": "140241282353",
            "amount": 82.62,
            "balance": 0,
        }
        row = _build_expense_from_extrato(
            tx, "141air", "reembolso_disputa", "income",
            _CA_CATEGORY_CODE_MAP.get("1.3.4"), "140241282353:rd",
        )
        assert row["expense_direction"] == "income"
        assert row["amount"] == 82.62  # positive, abs(82.62)
        assert row["status"] == "auto_categorized"


# ===========================================================================
# Coverage: every expense_type has abbreviation and description
# ===========================================================================

class TestExpenseTypeCompleteness:
    """Ensure every classified expense_type has an abbreviation and description."""

    def _get_all_expense_types(self) -> set[str]:
        """Collect all non-None, non-skip expense_types from classification rules."""
        types = set()
        for rule in EXTRATO_CLASSIFICATION_RULES:
            exp_type = rule[1]
            if exp_type is not None and exp_type != _CHECK_PAYMENTS:
                types.add(exp_type)
        # Also add fallback types
        from app.services.extrato_ingester import _CHECK_PAYMENTS_FALLBACK
        for _, (fb_type, _) in _CHECK_PAYMENTS_FALLBACK.items():
            types.add(fb_type)
        return types

    def test_all_types_have_abbreviation(self):
        """Every expense_type must have an entry in _EXPENSE_TYPE_ABBREV."""
        for exp_type in self._get_all_expense_types():
            assert exp_type in _EXPENSE_TYPE_ABBREV, \
                f"Missing abbreviation for expense_type: {exp_type}"

    def test_all_types_have_description(self):
        """Every expense_type must have an entry in _DESCRIPTION_TEMPLATES."""
        for exp_type in self._get_all_expense_types():
            assert exp_type in _DESCRIPTION_TEMPLATES, \
                f"Missing description template for expense_type: {exp_type}"

    def test_abbreviations_are_unique(self):
        """No two expense_types share the same abbreviation."""
        abbrevs = list(_EXPENSE_TYPE_ABBREV.values())
        assert len(abbrevs) == len(set(abbrevs)), \
            f"Duplicate abbreviations found: {[a for a in abbrevs if abbrevs.count(a) > 1]}"


# ===========================================================================
# Real extrato coverage: zero unclassified lines
# ===========================================================================

class TestRealExtratoFullCoverage:
    """Parse real extrato files and verify every line is classified.

    This catches any new TRANSACTION_TYPEs that ML adds to the extrato
    format that our rules don't cover yet.
    """

    @pytest.fixture
    def all_extrato_files(self):
        from pathlib import Path
        extrato_dir = Path(__file__).parent / "data" / "extratos"
        return list(extrato_dir.glob("*.csv"))

    def test_no_unclassified_lines(self, all_extrato_files):
        """Every line in every real extrato must be classified (not 'other')."""
        if not all_extrato_files:
            pytest.skip("No extrato files found in testes/data/extratos/")

        unclassified = []
        for filepath in all_extrato_files:
            for enc in ("utf-8-sig", "latin-1"):
                try:
                    text = filepath.read_text(encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                continue

            _, transactions = _parse_account_statement(text)
            for tx in transactions:
                exp_type, direction, _ = _classify_extrato_line(tx["transaction_type"])
                if exp_type == "other":
                    unclassified.append(
                        f"{filepath.name}: {tx['transaction_type']!r}"
                    )

        assert not unclassified, \
            f"Unclassified extrato lines found:\n" + "\n".join(unclassified)

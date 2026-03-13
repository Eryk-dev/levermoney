"""
Classifier for non-order ML/MP payments (bill payments, subscriptions, cashback, etc.).
Writes expense_captured (+ expense_classified) events to the event ledger.
"""
import logging

from app.services.event_ledger import EventRecordError, record_expense_event

logger = logging.getLogger(__name__)


# ── Auto-categorization rules (order matters: first match wins) ──────────
# Each rule is a dict for easy extensibility. Add new rules at the end of the
# appropriate section or before the catch-all.

AUTO_RULES = [
    # DARF / impostos
    {
        "name": "DARF",
        "match_field": "description",
        "match_contains": ["DARF"],
        "case_insensitive": True,
        "category": "2.2.7 Simples Nacional",
        "type": "expense",
        "expense_type": "darf",
        "desc_template": "DARF - {description}",
    },
    # Assinatura - Claude.ai / Anthropic
    {
        "name": "Claude/Anthropic",
        "match_branch": "Virtual",
        "match_field": "description",
        "match_contains": ["claude", "anthropic"],
        "case_insensitive": True,
        "category": "2.6.5 APIs e Integrações",
        "type": "expense",
        "expense_type": "subscription",
        "desc_template": "Assinatura - {description}",
    },
    # Assinatura - Supabase
    {
        "name": "Supabase",
        "match_branch": "Virtual",
        "match_field": "description",
        "match_contains": ["supabase"],
        "case_insensitive": True,
        "category": "2.6.4 Banco de Dados (Supabase)",
        "type": "expense",
        "expense_type": "subscription",
        "desc_template": "Assinatura - {description}",
    },
    # Assinatura - Notion
    {
        "name": "Notion",
        "match_branch": "Virtual",
        "match_field": "description",
        "match_contains": ["notion"],
        "case_insensitive": True,
        "category": "2.6.1 Software e Licenças",
        "type": "expense",
        "expense_type": "subscription",
        "desc_template": "Assinatura - {description}",
    },
]


def _extract_branch(payment: dict) -> str:
    """Extract point_of_interaction.business_info.branch from payment."""
    return (
        (payment.get("point_of_interaction") or {})
        .get("business_info", {})
        .get("branch")
        or ""
    )


def _extract_unit(payment: dict) -> str:
    """Extract point_of_interaction.business_info.unit from payment."""
    return (
        (payment.get("point_of_interaction") or {})
        .get("business_info", {})
        .get("unit")
        or ""
    )


def _extract_febraban(payment: dict) -> str | None:
    """Extract Febraban code from references."""
    refs = (
        (payment.get("point_of_interaction") or {})
        .get("transaction_data", {})
        .get("references")
        or []
    )
    for ref in refs:
        if ref.get("type") == "febraban_code":
            return ref.get("id")
    return None


def _extract_bank_info(payment: dict) -> dict:
    """Extract payer/collector bank info from point_of_interaction."""
    td = (
        (payment.get("point_of_interaction") or {})
        .get("transaction_data", {})
        .get("bank_info")
        or {}
    )
    return {
        "payer_bank": (td.get("payer") or {}).get("long_name") or "",
        "collector_alias": (td.get("collector") or {}).get("account_alias") or "",
        "payer_id_type": ((payment.get("payer") or {}).get("identification") or {}).get("type") or "",
        "payer_id_number": ((payment.get("payer") or {}).get("identification") or {}).get("number") or "",
    }


def _short_bank_name(long_name: str) -> str:
    """Shorten bank long_name to a readable label."""
    if not long_name:
        return ""
    # Take first meaningful part before " - " or "S.A." etc.
    name = long_name.split(" - ")[0].split(" S.A.")[0].split(" LTDA")[0].strip()
    # Capitalize nicely if all upper
    if name == name.upper() and len(name) > 5:
        name = name.title()
    # Truncate very long names (e.g. cooperatives)
    if len(name) > 40:
        name = name[:37] + "..."
    return name


def _match_rule(rule: dict, payment: dict, branch: str) -> bool:
    """Check if a rule matches the payment."""
    # Check branch constraint if present
    if "match_branch" in rule:
        if branch != rule["match_branch"]:
            return False

    # Check field contains constraint
    if "match_field" in rule and "match_contains" in rule:
        field_val = payment.get(rule["match_field"]) or ""
        if rule.get("case_insensitive"):
            field_val = field_val.lower()
        for keyword in rule["match_contains"]:
            check = keyword.lower() if rule.get("case_insensitive") else keyword
            if check in field_val:
                return True
        return False

    return True


def _format_description(template: str, payment: dict) -> str:
    """Format description template with payment fields."""
    return template.format(
        description=payment.get("description") or "",
        amount=payment.get("transaction_amount", 0),
        external_reference=payment.get("external_reference") or "",
    )[:200]


def _classify(payment: dict) -> tuple[str, str, str | None, bool, str]:
    """Classify a non-order payment.

    Returns: (expense_type, expense_direction, ca_category, auto_categorized, description)
    """
    op_type = payment.get("operation_type", "")
    branch = _extract_branch(payment)
    description = payment.get("description") or ""
    amount = payment.get("transaction_amount", 0)
    payment_method = payment.get("payment_method_id") or ""
    unit = _extract_unit(payment)

    # 1a. partition_transfer COM branch AM-to-POT → TRANSFER (cofrinho/Renda)
    if op_type == "partition_transfer" and "am-to-pot" in branch.lower():
        return "savings_pot", "transfer", None, False, f"Cofrinho Renda MP - R$ {amount}"

    # 1b. partition_transfer genérico → SKIP (internal MP movement)
    if op_type == "partition_transfer":
        return "partition_transfer", "skip", None, False, ""

    # 2. payment_addition → SKIP (extra shipping linked to order)
    if op_type == "payment_addition":
        return "payment_addition", "skip", None, False, ""

    # 3. money_transfer + Cashback → INCOME
    if op_type == "money_transfer" and branch == "Cashback":
        description_lower = description.lower()

        # Ressarcimento por perda no Full: classificar como receita eventual
        # (nao como estorno de taxa/tarifa).
        if (
            "programa de proteção do mercado envios full" in description_lower
            or "programa de protecao do mercado envios full" in description_lower
        ):
            return "cashback", "income", "1.4.2 Outras Receitas Eventuais", True, f"Ressarcimento Full ML - {description}"[:200]

        if "flex" in description_lower:
            return "cashback", "income", "1.3.4 Descontos e Estornos de Taxas e Tarifas", True, f"Bonificacao Flex ML - {description}"[:200]
        return "cashback", "income", "1.3.4 Descontos e Estornos de Taxas e Tarifas", True, f"Ressarcimento ML - {description}"[:200]

    # 4. money_transfer + Intra MP → TRANSFER
    if op_type == "money_transfer" and branch == "Intra MP":
        bi = _extract_bank_info(payment)
        dest = bi["collector_alias"] or bi["payer_id_number"] or ""
        dest_label = f" p/ {dest}" if dest else ""
        return "transfer_intra", "transfer", None, False, f"Transferencia Intra MP{dest_label} - R$ {amount}"[:200]

    # 5. money_transfer + other → TRANSFER
    if op_type == "money_transfer":
        bi = _extract_bank_info(payment)
        dest = bi["collector_alias"] or bi["payer_id_number"] or ""
        dest_label = f" p/ {dest}" if dest else ""
        return "transfer_pix", "transfer", None, False, f"Transferencia{dest_label} - {description or f'R$ {amount}'}"[:200]

    # 6. branch contains "Bill Payment" → check auto-rules (DARF), else EXPENSE
    if "bill payment" in branch.lower():
        for rule in AUTO_RULES:
            if _match_rule(rule, payment, branch):
                desc = _format_description(rule["desc_template"], payment)
                return rule["expense_type"], rule["type"], rule["category"], True, desc
        return "bill_payment", "expense", None, False, f"Boleto - {description}"[:200]

    # 7. branch == "Virtual" → check auto-rules (SaaS), else default subscription
    if branch == "Virtual":
        for rule in AUTO_RULES:
            if _match_rule(rule, payment, branch):
                desc = _format_description(rule["desc_template"], payment)
                return rule["expense_type"], rule["type"], rule["category"], True, desc
        # Default for Virtual: Software e Licencas
        return "subscription", "expense", "2.6.1 Software e Licenças", True, f"Assinatura - {description}"[:200]

    # 8. branch contains "Collections" → EXPENSE (ML charge)
    if "collections" in branch.lower():
        return "collection", "expense", "2.8.2 Comissões de Marketplace", True, f"Cobranca ML - {description or payment.get('external_reference', '')}"[:200]

    # 9. PIX without branch → TRANSFER (deposit/aporte)
    if payment_method == "pix" and not branch:
        bi = _extract_bank_info(payment)
        bank = _short_bank_name(bi["payer_bank"])
        origin = f" de {bank}" if bank else ""
        return "deposit", "transfer", None, False, f"Deposito PIX{origin} - R$ {amount}"[:200]

    # 10. No match → OTHER
    return "other", "expense", None, False, f"Outro - {description or f'R$ {amount}'}"[:200]


def _expense_signed_amount(direction: str, amount: float) -> float:
    """Return signed amount: positive for income, negative for expense/transfer."""
    if direction == "income":
        return abs(amount)
    return -abs(amount)


def _expense_competencia_date(payment: dict) -> str:
    """Extract competencia date from date_approved or date_created (date-only)."""
    raw = payment.get("date_approved") or payment.get("date_created") or ""
    return raw[:10]


def _build_expense_metadata(
    expense_type: str, direction: str, category: str | None,
    auto_cat: bool, desc: str, payment: dict,
) -> dict:
    """Build rich metadata dict for expense_captured event."""
    return {
        "expense_type": expense_type,
        "expense_direction": direction,
        "ca_category": category,
        "auto_categorized": auto_cat,
        "description": desc,
        "amount": payment.get("transaction_amount", 0),
        "date_created": payment.get("date_created"),
        "date_approved": payment.get("date_approved"),
        "business_branch": _extract_branch(payment) or None,
        "operation_type": payment.get("operation_type"),
        "payment_method": payment.get("payment_method_id"),
        "external_reference": payment.get("external_reference"),
        "beneficiary_name": (
            ((payment.get("payer") or {}).get("identification") or {}).get("number")
        ),
        "notes": payment.get("description"),
    }


async def _write_expense_events(
    seller_slug: str, payment_id: str, expense_type: str, direction: str,
    category: str | None, auto_cat: bool, desc: str, payment: dict,
) -> None:
    """Write expense_captured (and expense_classified if auto) to event ledger.

    Failures are logged as warnings but do not propagate.
    """
    amount = payment.get("transaction_amount", 0)
    signed = _expense_signed_amount(direction, amount)
    competencia = _expense_competencia_date(payment)
    metadata = _build_expense_metadata(
        expense_type, direction, category, auto_cat, desc, payment,
    )

    try:
        await record_expense_event(
            seller_slug=seller_slug,
            payment_id=payment_id,
            event_type="expense_captured",
            signed_amount=signed,
            competencia_date=competencia,
            expense_type=expense_type,
            metadata=metadata,
        )
    except EventRecordError:
        logger.warning(
            "expense_captured failed for %s/%s, continuing",
            seller_slug, payment_id,
        )

    if auto_cat:
        try:
            await record_expense_event(
                seller_slug=seller_slug,
                payment_id=payment_id,
                event_type="expense_classified",
                signed_amount=0,
                competencia_date=competencia,
                expense_type=expense_type,
                metadata={"ca_category": category},
            )
        except EventRecordError:
            logger.warning(
                "expense_classified failed for %s/%s, continuing",
                seller_slug, payment_id,
            )


async def classify_non_order_payment(db, seller_slug: str, payment: dict) -> dict | None:
    """Classify a non-order payment and record it in the event ledger.

    Returns classification dict, or None if skipped (partition_transfer, payment_addition).
    The `db` parameter is kept for backward compatibility with callers.
    """
    payment_id = payment["id"]
    expense_type, direction, category, auto_cat, desc = _classify(payment)

    # Skip internal movements entirely (don't store)
    if direction == "skip":
        logger.info(f"Payment {payment_id} classified as {expense_type}, skipping (internal)")
        return None

    # Write to event ledger
    await _write_expense_events(
        seller_slug, str(payment_id), expense_type, direction,
        category, auto_cat, desc, payment,
    )

    logger.info(f"Payment {payment_id} classified: type={expense_type} dir={direction} cat={category} auto={auto_cat}")

    return {
        "seller_slug": seller_slug,
        "payment_id": payment_id,
        "expense_type": expense_type,
        "expense_direction": direction,
        "ca_category": category,
        "auto_categorized": auto_cat,
        "description": desc,
        "amount": payment.get("transaction_amount", 0),
    }

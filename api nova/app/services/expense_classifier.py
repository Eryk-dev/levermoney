"""
Classifier for non-order ML/MP payments (bill payments, subscriptions, cashback, etc.).
Saves classified expenses to mp_expenses table for XLSX export.
"""
import logging
from datetime import datetime

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
        "category": "2.2.7",
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
        "category": "2.6.5",
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
        "category": "2.6.4",
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
        "category": "2.6.1",
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
        return "cashback", "income", "1.3.4", True, f"Ressarcimento ML - {description}"[:200]

    # 4. money_transfer + Intra MP → TRANSFER
    if op_type == "money_transfer" and branch == "Intra MP":
        return "transfer_intra", "transfer", None, False, f"Transferencia Intra MP - R$ {amount}"[:200]

    # 5. money_transfer + other → TRANSFER
    if op_type == "money_transfer":
        return "transfer_pix", "transfer", None, False, f"Transferencia - {description or f'R$ {amount}'}"[:200]

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
        return "subscription", "expense", "2.6.1", True, f"Assinatura - {description}"[:200]

    # 8. branch contains "Collections" → EXPENSE (ML charge)
    if "collections" in branch.lower():
        return "collection", "expense", "2.8.2", True, f"Cobranca ML - {description or payment.get('external_reference', '')}"[:200]

    # 9. PIX without branch → TRANSFER (deposit/aporte)
    if payment_method == "pix" and not branch:
        return "deposit", "transfer", None, False, f"Deposito PIX - R$ {amount}"[:200]

    # 10. No match → OTHER
    return "other", "expense", None, False, f"Outro - {description or f'R$ {amount}'}"[:200]


async def classify_non_order_payment(db, seller_slug: str, payment: dict) -> dict | None:
    """Classify and store a non-order payment in mp_expenses.

    Returns the inserted/updated row, or None if skipped (partition_transfer, payment_addition).
    """
    payment_id = payment["id"]
    expense_type, direction, category, auto_cat, desc = _classify(payment)

    # Skip internal movements entirely (don't store)
    if direction == "skip":
        logger.info(f"Payment {payment_id} classified as {expense_type}, skipping (internal)")
        return None

    branch = _extract_branch(payment)
    febraban = _extract_febraban(payment)
    status = "auto_categorized" if auto_cat else "pending_review"

    data = {
        "seller_slug": seller_slug,
        "payment_id": payment_id,
        "expense_type": expense_type,
        "expense_direction": direction,
        "ca_category": category,
        "auto_categorized": auto_cat,
        "amount": payment.get("transaction_amount", 0),
        "description": desc,
        "business_branch": branch or None,
        "operation_type": payment.get("operation_type"),
        "payment_method": payment.get("payment_method_id"),
        "external_reference": payment.get("external_reference"),
        "febraban_code": febraban,
        "date_created": payment.get("date_created"),
        "date_approved": payment.get("date_approved"),
        "status": status,
        "raw_payment": payment,
        "updated_at": datetime.now().isoformat(),
    }

    # Upsert: insert or update on conflict
    existing = db.table("mp_expenses").select("id, status").eq(
        "seller_slug", seller_slug
    ).eq("payment_id", payment_id).execute()

    if existing.data:
        # Don't overwrite if already exported
        if existing.data[0].get("status") == "exported":
            logger.info(f"Payment {payment_id} already exported, skipping update")
            return existing.data[0]
        db.table("mp_expenses").update(data).eq("id", existing.data[0]["id"]).execute()
        logger.info(f"Payment {payment_id} updated: type={expense_type} dir={direction} auto={auto_cat}")
    else:
        data["created_at"] = datetime.now().isoformat()
        db.table("mp_expenses").insert(data).execute()
        logger.info(f"Payment {payment_id} classified: type={expense_type} dir={direction} cat={category} auto={auto_cat}")

    return data

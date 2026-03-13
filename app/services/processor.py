"""
Processador de eventos ML/MP → Conta Azul.
Implementa o mapeamento definido em PLANO.md Seção 13.

Source of truth: payment_events (event ledger). The payments table is no longer
written to by processor — events are the authoritative record. Consumers that
need payment data should query payment_events via event_ledger helpers.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.db.supabase import get_db
from app.models.sellers import (
    CA_CATEGORIES,
    CA_CONTATO_ML,
    get_missing_ca_launch_fields,
    get_seller_config,
)
from app.services import ml_api, ca_queue, event_ledger
from app.services.event_ledger import EventRecordError

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_brt_date(iso_str: str) -> str:
    """Convert ISO datetime string from ML API to BRT date (YYYY-MM-DD).

    ML API returns dates in UTC-4. The ML sales report uses BRT (UTC-3),
    so late-night sales (e.g. 23:45 UTC-4 = 00:45 BRT) cross midnight
    and must be attributed to the next day to match ML's reports.
    """
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.astimezone(BRT).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return iso_str[:10]


def _compute_effective_net_amount(payment: dict) -> float:
    """Compute net cash amount for reconciliation/storage.

    For partially_refunded payments, MP's net_received_amount can represent the
    pre-refund net while the account statement shows the effective released cash.
    In that case, adjust by subtracting the refunded amount net of refunded
    collector charges (fees/shipping) present in charges_details.
    """
    net = _to_float((payment.get("transaction_details") or {}).get("net_received_amount"))
    status_detail = str(payment.get("status_detail") or "").lower()
    if status_detail != "partially_refunded":
        return round(net, 2)

    refunded_amount = _to_float(payment.get("transaction_amount_refunded"))
    if refunded_amount <= 0:
        refunds = payment.get("refunds") or []
        refunded_amount = sum(_to_float(r.get("amount")) for r in refunds)
    if refunded_amount <= 0:
        return round(net, 2)

    refunded_charges = 0.0
    for charge in payment.get("charges_details") or []:
        accounts = charge.get("accounts") or {}
        if accounts.get("from") != "collector":
            continue

        charge_type = str(charge.get("type") or "").lower()
        if charge_type not in {"fee", "shipping"}:
            continue

        # financing_fee is net-neutral in this project.
        charge_name = str(charge.get("name") or "").strip().lower()
        if charge_name == "financing_fee":
            continue

        refunded_charges += _to_float((charge.get("amounts") or {}).get("refunded"))

    adjusted = net - max(0.0, refunded_amount - refunded_charges)
    return round(max(0.0, adjusted), 2)


def _extract_processor_charges(payment: dict) -> tuple[float, float, str | None, float, float]:
    """Compute fee/shipping from charges_details and reconcile against payment net."""
    amount = _to_float(payment.get("transaction_amount"))
    net = _to_float((payment.get("transaction_details") or {}).get("net_received_amount"))
    charges = payment.get("charges_details", [])

    shipping_charges_collector = 0.0
    mp_fee = 0.0
    shipping_ids: set[str] = set()

    for charge in charges:
        accounts = charge.get("accounts", {}) or {}
        if accounts.get("from") != "collector":
            continue

        charge_amount = _to_float((charge.get("amounts", {}) or {}).get("original"))
        charge_type = charge.get("type")

        if charge_type == "shipping":
            shipping_charges_collector += charge_amount
            shipment_id = str((charge.get("metadata", {}) or {}).get("shipment_id") or "").strip()
            if shipment_id:
                shipping_ids.add(shipment_id)
        elif charge_type == "fee":
            charge_name = (charge.get("name") or "").strip().lower()
            # financing_fee is offset by financing_transfer and does not impact net.
            if charge_name == "financing_fee":
                continue
            mp_fee += charge_amount
        elif charge_type == "coupon":
            # coupon_fee: ML charges the seller for buyer coupons (from=collector, to=ml)
            mp_fee += charge_amount

    shipping_amount_buyer = _to_float(payment.get("shipping_amount"))
    shipping_cost_seller = round(max(0.0, shipping_charges_collector - shipping_amount_buyer), 2)
    mp_fee = round(mp_fee, 2)
    shipping_id = next(iter(shipping_ids), None)

    reconciled_net = round(amount - mp_fee - shipping_cost_seller, 2)
    net_diff = round(net - reconciled_net, 2)
    return mp_fee, shipping_cost_seller, shipping_id, reconciled_net, net_diff


def _build_parcela(descricao: str, data_vencimento: str, conta_financeira: str, valor: float, nota: str = "") -> dict:
    """Monta parcela no formato correto do CA v2."""
    return {
        "descricao": descricao,
        "data_vencimento": data_vencimento,
        "nota": nota or descricao,
        "conta_financeira": conta_financeira,
        "detalhe_valor": {
            "valor_bruto": valor,
            "valor_liquido": valor,
        },
    }


def _build_evento(data_competencia: str, valor: float, descricao: str, observacao: str,
                   contato: str, conta_financeira: str, categoria: str,
                   centro_custo: str, parcela: dict, rateio_centro_custo: bool = True) -> dict:
    """Monta evento financeiro (receita ou despesa) no formato CA v2."""
    rateio_item = {
        "id_categoria": categoria,
        "valor": valor,
    }
    if rateio_centro_custo and centro_custo:
        rateio_item["rateio_centro_custo"] = [{
            "id_centro_custo": centro_custo,
            "valor": valor,
        }]

    return {
        "data_competencia": data_competencia,
        "valor": valor,
        "descricao": descricao,
        "observacao": observacao,
        "contato": contato,
        "conta_financeira": conta_financeira,
        "rateio": [rateio_item],
        "condicao_pagamento": {
            "parcelas": [parcela],
        },
    }


def _build_despesa_payload(seller: dict, data_competencia: str, data_vencimento: str,
                           valor: float, descricao: str,
                           observacao: str, categoria: str, nota_parcela: str = "") -> dict:
    """Build conta-a-pagar payload (does NOT call CA API).

    Baixa é feita pelo job separado /baixas/processar/{seller} quando vencimento <= hoje.
    data_competencia: quando a venda foi confirmada (date_approved convertido para BRT)
    data_vencimento: quando o ML desconta (money_release_date)
    """
    conta = seller["ca_conta_bancaria"]
    contato = seller.get("ca_contato_ml") or CA_CONTATO_ML

    parcela = _build_parcela(descricao, data_vencimento, conta, valor, nota_parcela)
    return _build_evento(data_competencia, valor, descricao, observacao, contato, conta,
                         categoria, seller.get("ca_centro_custo_variavel"), parcela)


async def process_payment_webhook(seller_slug: str, payment_id: int, payment_data: dict = None):
    """
    Processa webhook de payment.
    Fluxo: GET payment → classifica → lança no CA.
    payment_data: if provided, skips the API fetch (used by daily_sync for efficiency).

    Idempotency is ensured via event_ledger (ON CONFLICT DO NOTHING on idempotency_key)
    and ca_queue (idempotency_key on ca_jobs).
    """
    seller = get_seller_config(get_db(), seller_slug)
    if not seller:
        logger.error(f"Seller {seller_slug} not found")
        return

    # Check existing events for this payment (replaces old payments table read)
    existing_events = await event_ledger.get_events(seller_slug, payment_id)
    existing_event_types = {e["event_type"] for e in existing_events}

    payment = payment_data or await ml_api.get_payment(seller_slug, payment_id)
    status = payment["status"]

    # Skip non-sale payments (bill payments, money_transfers, ad credits)
    order_id = (payment.get("order") or {}).get("id")
    if not order_id:
        logger.info(f"Payment {payment_id} has no order_id, skipping (non-sale)")
        return

    # Skip marketplace_shipment (buyer-paid shipping as separate payment, not a product sale)
    if payment.get("description") == "marketplace_shipment":
        logger.info(f"Payment {payment_id} is marketplace_shipment, skipping")
        return

    # Skip purchases: when collector_id is set, the seller is the BUYER, not the seller.
    collector_id = (payment.get("collector") or {}).get("id")
    if collector_id is not None:
        logger.info(f"Payment {payment_id} has collector_id={collector_id}, skipping (purchase, not sale)")
        return

    # Only sellers with full CA launch config can create CA entries/jobs.
    # Without config, skip — onboarding_backfill will re-fetch from ML API later.
    if status in ("approved", "in_mediation", "refunded", "charged_back"):
        missing_ca_fields = get_missing_ca_launch_fields(seller)
        if missing_ca_fields:
            reason = f"missing_ca_config:{','.join(missing_ca_fields)}"
            logger.warning(
                "Payment %s seller %s skipped (pending CA config): %s",
                payment_id,
                seller_slug,
                reason,
            )
            return

    if status in ("approved", "in_mediation"):
        await _process_approved(seller, payment, existing_event_types)
    elif status == "charged_back" and payment.get("status_detail") == "reimbursed":
        # Chargeback coberto pela proteção ML: seller recebeu o dinheiro.
        # Tratar como venda normal (receita + despesas, sem estorno).
        logger.info(f"Payment {payment_id} charged_back+reimbursed, treating as approved (no estorno)")
        await _process_approved(seller, payment, existing_event_types)
    elif status == "refunded" and payment.get("status_detail") == "by_admin":
        # Kit split: ML cancelled original and created new payments for each package.
        # If already has sale event (was processed), we need the estorno. Otherwise skip.
        if "sale_approved" in existing_event_types:
            logger.info(f"Payment {payment_id} by_admin but already processed, processing refund")
            await _process_refunded(seller, payment, existing_event_types)
        else:
            logger.info(f"Payment {payment_id} refunded/by_admin (kit split), skipping")
    elif status in ("refunded", "charged_back"):
        await _process_refunded(seller, payment, existing_event_types)
    elif status in ("cancelled", "rejected"):
        logger.info(f"Payment {payment_id} status={status}, skipping")
    else:
        logger.info(f"Payment {payment_id} status={status}, no action needed")


async def _process_approved(seller: dict, payment: dict, existing_event_types: set[str]):
    """EVENTO 1: Venda Aprovada → Receita + Despesas no CA."""
    payment_id = payment["id"]
    seller_slug = seller["slug"]

    # If sale_approved already exists, check for partial refund only
    if "sale_approved" in existing_event_types:
        if payment.get("status_detail") == "partially_refunded":
            await _process_partial_refund(seller, payment, existing_event_types)
        else:
            logger.info(
                "Payment %s already has sale_approved event, skipping _process_approved",
                payment_id,
            )
        return

    # 1. Dados do pedido
    order = None
    order_id = payment.get("order", {}).get("id") if payment.get("order") else None
    if order_id:
        try:
            order = await ml_api.get_order(seller_slug, order_id)
        except Exception as e:
            logger.warning(f"Could not fetch order {order_id}: {e}")

    # 2. Extrair valores do payment
    amount = payment["transaction_amount"]
    date_approved_raw = payment.get("date_approved") or payment.get("date_created", "")
    competencia = _to_brt_date(date_approved_raw)
    money_release_date = (payment.get("money_release_date") or date_approved_raw)[:10]
    net = payment.get("transaction_details", {}).get("net_received_amount", 0)

    # 3. Extrair taxas direto do ML charges_details (source of truth)
    mp_fee, shipping_cost_seller, shipping_id, reconciled_net, net_diff = _extract_processor_charges(payment)
    if abs(net_diff) >= 0.01:
        logger.warning(
            "Payment %s: net mismatch using direct charges (net=%s vs calc=%s, diff=%s)",
            payment_id,
            net,
            reconciled_net,
            net_diff,
        )

    # Descrição
    item_title = ""
    if order and order.get("order_items"):
        item_title = order["order_items"][0].get("item", {}).get("title", "")
    order_type = (payment.get("order") or {}).get("type", "mercadolibre")
    venda_label = "Venda MP" if order_type == "mercadopago" else "Venda ML"
    desc_receita = f"{venda_label} #{order_id or ''} - {item_title}"[:200]
    obs = f"Payment: {payment_id} | Liberação: {money_release_date}"

    contato = seller.get("ca_contato_ml") or CA_CONTATO_ML
    conta = seller["ca_conta_bancaria"]
    cc = seller.get("ca_centro_custo_variavel")

    # === ENQUEUE JOBS FOR CONTA AZUL ===

    # A) RECEITA (contas-a-receber)
    cat_receita = CA_CATEGORIES["venda_ecommerce"] if order_type == "mercadopago" else CA_CATEGORIES["venda_ml"]
    parcela_receita = _build_parcela(desc_receita, money_release_date, conta, amount)
    receita_payload = _build_evento(
        competencia, amount, desc_receita, obs, contato, conta,
        cat_receita, cc, parcela_receita,
    )
    await ca_queue.enqueue_receita(seller_slug, payment_id, receita_payload)
    try:
        await event_ledger.record_event(
            seller_slug=seller_slug, ml_payment_id=payment_id,
            event_type="sale_approved", signed_amount=amount,
            competencia_date=competencia, event_date=competencia,
            ml_order_id=order_id, source="processor",
            metadata={
                "order_type": order_type,
                "item_title": item_title[:100],
                "ml_status": payment.get("status"),
                "status_detail": payment.get("status_detail"),
                "money_release_date": money_release_date,
            },
        )
    except EventRecordError as e:
        logger.error("Event ledger sale_approved failed for %s: %s", payment_id, e)

    # B) DESPESA - Comissão ML (se > 0)
    if mp_fee > 0:
        comissao_payload = _build_despesa_payload(
            seller, competencia, money_release_date, mp_fee,
            f"Comissão ML - Payment {payment_id}",
            f"Venda #{order_id} | fee={mp_fee}",
            CA_CATEGORIES["comissao_ml"],
            f"Comissão ML #{payment_id}",
        )
        await ca_queue.enqueue_comissao(seller_slug, payment_id, comissao_payload)
        try:
            await event_ledger.record_event(
                seller_slug=seller_slug, ml_payment_id=payment_id,
                event_type="fee_charged", signed_amount=-mp_fee,
                competencia_date=competencia, event_date=competencia,
                ml_order_id=order_id, source="processor",
            )
        except EventRecordError as e:
            logger.error("Event ledger fee_charged failed for %s: %s", payment_id, e)

    # C) DESPESA - Frete (se > 0)
    if shipping_cost_seller > 0:
        frete_payload = _build_despesa_payload(
            seller, competencia, money_release_date, shipping_cost_seller,
            f"Frete MercadoEnvios - Payment {payment_id}",
            f"Shipment #{shipping_id}",
            CA_CATEGORIES["frete_mercadoenvios"],
            f"Frete ML #{payment_id}",
        )
        await ca_queue.enqueue_frete(seller_slug, payment_id, frete_payload)
        try:
            await event_ledger.record_event(
                seller_slug=seller_slug, ml_payment_id=payment_id,
                event_type="shipping_charged", signed_amount=-shipping_cost_seller,
                competencia_date=competencia, event_date=competencia,
                ml_order_id=order_id, source="processor",
            )
        except EventRecordError as e:
            logger.error("Event ledger shipping_charged failed for %s: %s", payment_id, e)

    # NOTA: financing_fee NÃO gera despesa (net-neutral).

    # D) RECEITA - Subsídio ML (net > calculated net → ML paying extra to seller)
    subsidy = round(net - reconciled_net, 2) if net_diff > 0 else 0.0
    if subsidy >= 0.01:
        subsidy_desc = f"Subsídio ML - Payment {payment_id}"
        subsidy_obs = f"calc_net={reconciled_net}, net_real={net}, diff={subsidy}"
        subsidy_payload = _build_evento(
            competencia, subsidy, subsidy_desc, subsidy_obs,
            contato, conta, CA_CATEGORIES["estorno_frete"], cc,
            _build_parcela(subsidy_desc, money_release_date, conta, subsidy),
        )
        await ca_queue.enqueue_receita(seller_slug, f"{payment_id}_subsidy", subsidy_payload)
        logger.info("Payment %s: ML subsidy detected R$%.2f, enqueued receita 1.3.7", payment_id, subsidy)
        try:
            await event_ledger.record_event(
                seller_slug=seller_slug, ml_payment_id=payment_id,
                event_type="subsidy_credited", signed_amount=subsidy,
                competencia_date=competencia, event_date=competencia,
                ml_order_id=order_id, source="processor",
            )
        except EventRecordError as e:
            logger.error("Event ledger subsidy_credited failed for %s: %s", payment_id, e)

    logger.info(
        f"Payment {payment_id} queued: receita={amount}, comissão={mp_fee}, "
        f"frete={shipping_cost_seller}, net={net}"
    )


async def _process_partial_refund(seller: dict, payment: dict, existing_event_types: set[str]):
    """Refund parcial: status permanece 'approved', status_detail='partially_refunded'.
    Cria estornos proporcionais para cada refund não processado."""
    payment_id = payment["id"]
    seller_slug = seller["slug"]
    refunds = payment.get("refunds", [])

    if not refunds:
        logger.info(f"Payment {payment_id}: partially_refunded but no refunds array")
        return

    # Count already-processed partial refunds from event ledger
    existing_events = await event_ledger.get_events(seller_slug, payment_id)
    processed_count = sum(1 for e in existing_events if e["event_type"] == "partial_refund")

    contato = seller.get("ca_contato_ml") or CA_CONTATO_ML
    conta = seller["ca_conta_bancaria"]
    cc = seller.get("ca_centro_custo_variavel")

    for i, refund in enumerate(refunds):
        # Pular refunds já processados (por índice)
        if i < processed_count:
            continue

        refund_amount = refund.get("amount", 0)
        if refund_amount <= 0:
            continue

        date_refund = refund.get("date_created", datetime.now().isoformat())[:10]
        refund_id = refund.get("id", i)

        # Estorno proporcional da receita
        parcela = _build_parcela(
            f"Devolução parcial ML #{payment_id}-{refund_id}",
            date_refund, conta, refund_amount,
        )
        estorno_payload = _build_evento(
            date_refund, refund_amount,
            f"Devolução parcial ML - Payment {payment_id}",
            f"Refund #{refund_id}: R${refund_amount}",
            contato, conta, CA_CATEGORIES["devolucao"], cc, parcela,
        )

        await ca_queue.enqueue_partial_refund(seller_slug, payment_id, i, estorno_payload)
        logger.info(f"Enqueued partial refund: payment {payment_id}, refund {refund_id}, R${refund_amount}")
        try:
            competencia_pr = _to_brt_date(payment.get("date_approved") or payment.get("date_created", ""))
            await event_ledger.record_event(
                seller_slug=seller_slug, ml_payment_id=payment_id,
                event_type="partial_refund", signed_amount=-refund_amount,
                competencia_date=competencia_pr, event_date=date_refund,
                ml_order_id=(payment.get("order") or {}).get("id"),
                source="processor",
                idempotency_key=event_ledger.build_idempotency_key(
                    seller_slug, payment_id, "partial_refund", suffix=str(i),
                ),
            )
        except EventRecordError as e:
            logger.error("Event ledger partial_refund failed for %s: %s", payment_id, e)


async def _process_refunded(seller: dict, payment: dict, existing_event_types: set[str]):
    """EVENTO 4: Cancelamento/Devolução → Receita original + Estornos no CA.

    Se a receita nunca foi criada (backfill direto como refunded), cria primeiro
    a receita + despesas (comissão, frete) para que o faturamento bruto bata com ML.
    Depois cria os estornos normalmente.
    """
    payment_id = payment["id"]
    seller_slug = seller["slug"]

    # Idempotência: se já processou refund, skip
    if "refund_created" in existing_event_types:
        logger.info(f"Payment {payment_id} already has refund_created event, skipping")
        return

    # Se receita nunca foi criada, cria primeiro
    if "sale_approved" not in existing_event_types:
        await _process_approved(seller, payment, existing_event_types)

    date_refunded = datetime.now().strftime("%Y-%m-%d")
    amount = payment["transaction_amount"]
    refunds = payment.get("refunds", [])

    if refunds:
        total_refunded_raw = sum(r.get("amount", 0) for r in refunds)
        date_refunded = refunds[-1].get("date_created", date_refunded)[:10]
    else:
        total_refunded_raw = payment.get("transaction_amount_refunded") or amount

    # Estorno da receita não pode exceder transaction_amount.
    estorno_receita = min(total_refunded_raw, amount)

    contato = seller.get("ca_contato_ml") or CA_CONTATO_ML
    conta = seller["ca_conta_bancaria"]
    cc = seller.get("ca_centro_custo_variavel")

    # A) Estorno da receita (contas-a-pagar)
    parcela = _build_parcela(f"Devolução ML #{payment_id}", date_refunded, conta, estorno_receita)
    estorno_payload = _build_evento(
        date_refunded, estorno_receita,
        f"Devolução ML - Payment {payment_id}",
        f"Refund: R${estorno_receita} (original: R${amount})",
        contato, conta, CA_CATEGORIES["devolucao"], cc, parcela,
    )
    await ca_queue.enqueue_estorno(seller_slug, payment_id, estorno_payload)

    # Event ledger: refund_created
    competencia_refund = _to_brt_date(payment.get("date_approved") or payment.get("date_created", ""))
    order_id_refund = (payment.get("order") or {}).get("id")
    try:
        await event_ledger.record_event(
            seller_slug=seller_slug, ml_payment_id=payment_id,
            event_type="refund_created", signed_amount=-estorno_receita,
            competencia_date=competencia_refund, event_date=date_refunded,
            ml_order_id=order_id_refund, source="processor",
        )
    except EventRecordError as e:
        logger.error("Event ledger refund_created failed for %s: %s", payment_id, e)

    # B) Estorno de taxas: use charges_details to determine ACTUAL refunded amounts
    refunded_fee = 0.0
    refunded_shipping = 0.0
    charges = payment.get("charges_details") or []
    has_charges_detail = False

    for charge in charges:
        accounts = charge.get("accounts") or {}
        if accounts.get("from") != "collector":
            continue

        charge_type = str(charge.get("type") or "").lower()
        charge_name = str(charge.get("name") or "").strip().lower()

        # financing_fee is net-neutral, skip it
        if charge_name == "financing_fee":
            continue

        refunded_val = _to_float((charge.get("amounts") or {}).get("refunded"))
        if charge_type == "fee":
            refunded_fee += refunded_val
            has_charges_detail = True
        elif charge_type == "shipping":
            refunded_shipping += refunded_val
            has_charges_detail = True

    if not has_charges_detail:
        # Fallback for old payments without charges_details: use blanket calculation
        net = _to_float((payment.get("transaction_details") or {}).get("net_received_amount"))
        total_fees = round(amount - net, 2) if net > 0 else 0
        refunded_fee = total_fees  # assume all fees refunded (legacy behavior)

    refunded_fee = round(refunded_fee, 2)
    refunded_shipping = round(refunded_shipping, 2)

    if refunded_fee > 0 and estorno_receita >= amount:
        parcela_est = _build_parcela(f"Estorno taxa ML #{payment_id}", date_refunded, conta, refunded_fee)
        estorno_taxa_payload = _build_evento(
            date_refunded, refunded_fee,
            f"Estorno taxas ML - Payment {payment_id}",
            f"Estorno comissão por devolução (fee_refunded={refunded_fee})",
            contato, conta, CA_CATEGORIES["estorno_taxa"], cc, parcela_est,
        )
        await ca_queue.enqueue_estorno_taxa(seller_slug, payment_id, estorno_taxa_payload)
        try:
            await event_ledger.record_event(
                seller_slug=seller_slug, ml_payment_id=payment_id,
                event_type="refund_fee", signed_amount=refunded_fee,
                competencia_date=competencia_refund, event_date=date_refunded,
                ml_order_id=order_id_refund, source="processor",
            )
        except EventRecordError as e:
            logger.error("Event ledger refund_fee failed for %s: %s", payment_id, e)

    if refunded_shipping > 0 and estorno_receita >= amount:
        parcela_frete = _build_parcela(f"Estorno frete ML #{payment_id}", date_refunded, conta, refunded_shipping)
        estorno_frete_payload = _build_evento(
            date_refunded, refunded_shipping,
            f"Estorno frete ML - Payment {payment_id}",
            f"Estorno frete por devolução (shipping_refunded={refunded_shipping})",
            contato, conta, CA_CATEGORIES["estorno_frete"], cc, parcela_frete,
        )
        await ca_queue.enqueue_estorno_frete(seller_slug, payment_id, estorno_frete_payload)
        try:
            await event_ledger.record_event(
                seller_slug=seller_slug, ml_payment_id=payment_id,
                event_type="refund_shipping", signed_amount=refunded_shipping,
                competencia_date=competencia_refund, event_date=date_refunded,
                ml_order_id=order_id_refund, source="processor",
            )
        except EventRecordError as e:
            logger.error("Event ledger refund_shipping failed for %s: %s", payment_id, e)

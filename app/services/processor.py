"""
Processador de eventos ML/MP → Conta Azul.
Implementa o mapeamento definido em PLANO.md Seção 13.
"""
import logging
from datetime import datetime

from app.db.supabase import get_db
from app.models.sellers import CA_CATEGORIES, get_seller_config
from app.services import ml_api, ca_queue

logger = logging.getLogger(__name__)


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
    data_competencia: quando a despesa ocorreu (date_approved)
    data_vencimento: quando o ML desconta (money_release_date)
    """
    conta = seller["ca_conta_mp_retido"]
    contato = seller.get("ca_contato_ml")

    parcela = _build_parcela(descricao, data_vencimento, conta, valor, nota_parcela)
    return _build_evento(data_competencia, valor, descricao, observacao, contato, conta,
                         categoria, seller.get("ca_centro_custo_variavel"), parcela)


async def process_payment_webhook(seller_slug: str, payment_id: int):
    """
    Processa webhook de payment.
    Fluxo: GET payment → classifica → lança no CA.
    """
    db = get_db()
    seller = get_seller_config(db, seller_slug)
    if not seller:
        logger.error(f"Seller {seller_slug} not found")
        return

    # Idempotência: verifica se já processou
    existing = db.table("payments").select("id, status").eq(
        "ml_payment_id", payment_id
    ).eq("seller_slug", seller_slug).execute()

    payment = await ml_api.get_payment(seller_slug, payment_id)
    status = payment["status"]

    # Skip non-sale payments (bill payments, money_transfers, ad credits)
    order_id = (payment.get("order") or {}).get("id")
    if not order_id:
        logger.info(f"Payment {payment_id} has no order_id, skipping (non-sale)")
        _upsert_payment(db, seller_slug, payment, "skipped_non_sale")
        return

    # Skip marketplace_shipment (buyer-paid shipping as separate payment, not a product sale)
    if payment.get("description") == "marketplace_shipment":
        logger.info(f"Payment {payment_id} is marketplace_shipment, skipping")
        _upsert_payment(db, seller_slug, payment, "skipped_non_sale")
        return

    # Skip purchases: when collector_id is set, the seller is the BUYER, not the seller.
    # Normal sales have collector=null; purchases have collector=another seller's ID.
    collector_id = (payment.get("collector") or {}).get("id")
    if collector_id is not None:
        logger.info(f"Payment {payment_id} has collector_id={collector_id}, skipping (purchase, not sale)")
        _upsert_payment(db, seller_slug, payment, "skipped_non_sale")
        return

    if status in ("approved", "in_mediation"):
        await _process_approved(db, seller, payment, existing.data)
    elif status in ("refunded", "charged_back"):
        await _process_refunded(db, seller, payment, existing.data)
    elif status in ("cancelled", "rejected"):
        logger.info(f"Payment {payment_id} status={status}, skipping")
        _upsert_payment(db, seller_slug, payment, "skipped")
    else:
        logger.info(f"Payment {payment_id} status={status}, saving for later")
        _upsert_payment(db, seller_slug, payment, "pending")


async def _process_approved(db, seller: dict, payment: dict, existing: list):
    """EVENTO 1: Venda Aprovada → Receita + Despesas no CA."""
    payment_id = payment["id"]
    seller_slug = seller["slug"]

    # Se já sincronizou ou está na fila, verificar refund parcial
    if existing and any(e.get("status") in ("synced", "queued") for e in existing):
        if payment.get("status_detail") == "partially_refunded":
            await _process_partial_refund(db, seller, payment)
        else:
            logger.info(f"Payment {payment_id} already synced/queued, skipping")
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
    date_approved = payment.get("date_approved", payment["date_created"])[:10]
    money_release_date = (payment.get("money_release_date") or date_approved)[:10]
    net = payment.get("transaction_details", {}).get("net_received_amount", 0)

    # 3. Extrair taxas de charges_details (source of truth)
    # fee_details é unreliável (vazio em ~86% dos payments).
    # charges_details tem o breakdown completo com from/to accounts.
    charges = payment.get("charges_details", [])

    # Shipping: charges type="shipping" onde seller paga (from=collector)
    shipping_cost_seller = sum(
        c.get("amounts", {}).get("original", 0) for c in charges
        if c.get("type") == "shipping"
        and c.get("accounts", {}).get("from") == "collector"
    )

    # Fallback: buscar shipping via API se charges_details não tem dados
    shipping_id = None
    if shipping_cost_seller == 0 and order and order.get("shipping", {}).get("id"):
        shipping_id = order["shipping"]["id"]
        try:
            costs = await ml_api.get_shipment_costs(seller_slug, shipping_id)
            senders = costs.get("senders", [])
            if senders:
                shipping_cost_seller = senders[0].get("cost", 0.0)
        except Exception as e:
            logger.warning(f"Could not fetch shipping costs {shipping_id}: {e}")

    # Comissão ML = tudo que o ML desconta EXCETO shipping
    # Formula infalível: amount - net = total descontado, menos shipping = comissão
    # Captura automaticamente: ml_sale_fee + mp_processing_fee + mp_financing_1x_fee + subsídios
    # NOTA: financing_fee é net-neutral (financing_transfer cancela), NÃO entra no cálculo
    mp_fee = round(amount - net - shipping_cost_seller, 2) if net > 0 else 0
    if mp_fee < 0:
        logger.warning(f"Payment {payment_id}: negative comissão {mp_fee}, setting to 0")
        mp_fee = 0

    # Descrição
    item_title = ""
    if order and order.get("order_items"):
        item_title = order["order_items"][0].get("item", {}).get("title", "")
    desc_receita = f"Venda ML #{order_id or ''} - {item_title}"[:200]
    obs = f"Payment: {payment_id} | Liberação: {money_release_date}"

    contato = seller.get("ca_contato_ml")
    conta = seller["ca_conta_mp_retido"]
    cc = seller.get("ca_centro_custo_variavel")

    # === ENQUEUE JOBS FOR CONTA AZUL ===

    # A) RECEITA (contas-a-receber)
    parcela_receita = _build_parcela(desc_receita, money_release_date, conta, amount)
    receita_payload = _build_evento(
        date_approved, amount, desc_receita, obs, contato, conta,
        CA_CATEGORIES["venda_ml"], cc, parcela_receita,
    )
    await ca_queue.enqueue_receita(seller_slug, payment_id, receita_payload)

    # B) DESPESA - Comissão ML (se > 0)
    if mp_fee > 0:
        comissao_payload = _build_despesa_payload(
            seller, date_approved, money_release_date, mp_fee,
            f"Comissão ML - Payment {payment_id}",
            f"Venda #{order_id} | fee={mp_fee}",
            CA_CATEGORIES["comissao_ml"],
            f"Comissão ML #{payment_id}",
        )
        await ca_queue.enqueue_comissao(seller_slug, payment_id, comissao_payload)

    # C) DESPESA - Frete (se > 0)
    if shipping_cost_seller > 0:
        frete_payload = _build_despesa_payload(
            seller, date_approved, money_release_date, shipping_cost_seller,
            f"Frete MercadoEnvios - Payment {payment_id}",
            f"Shipment #{shipping_id}",
            CA_CATEGORIES["frete_mercadoenvios"],
            f"Frete ML #{payment_id}",
        )
        await ca_queue.enqueue_frete(seller_slug, payment_id, frete_payload)

    # NOTA: financing_fee NÃO gera despesa (net-neutral).

    # Salva no Supabase como queued (worker updates to synced when group completes)
    _upsert_payment(db, seller_slug, payment, "queued")

    logger.info(
        f"Payment {payment_id} queued: receita={amount}, comissão={mp_fee}, "
        f"frete={shipping_cost_seller}, net={net}"
    )


async def _process_partial_refund(db, seller: dict, payment: dict):
    """Refund parcial: status permanece 'approved', status_detail='partially_refunded'.
    Cria estornos proporcionais para cada refund não processado."""
    payment_id = payment["id"]
    seller_slug = seller["slug"]
    refunds = payment.get("refunds", [])

    if not refunds:
        logger.info(f"Payment {payment_id}: partially_refunded but no refunds array")
        return

    # Buscar refunds já processados no Supabase
    existing_refunds = db.table("payments").select("id").eq(
        "seller_slug", seller_slug
    ).like("ca_evento_id", f"partial_refund_{payment_id}_%").execute()
    processed_count = len(existing_refunds.data) if existing_refunds.data else 0

    contato = seller.get("ca_contato_ml")
    conta = seller["ca_conta_mp_retido"]
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

    # Atualizar status no Supabase
    _upsert_payment(db, seller_slug, payment, "synced")


async def _process_refunded(db, seller: dict, payment: dict, existing: list):
    """EVENTO 4: Cancelamento/Devolução → Receita original + Estornos no CA.

    Se a receita nunca foi criada (backfill direto como refunded), cria primeiro
    a receita + despesas (comissão, frete) para que o faturamento bruto bata com ML.
    Depois cria os estornos normalmente.
    """
    payment_id = payment["id"]
    seller_slug = seller["slug"]

    # Idempotência: se já processou como refunded, skip
    if existing and any(e.get("status") == "refunded" for e in existing):
        logger.info(f"Payment {payment_id} already refunded, skipping")
        return

    # Se receita nunca foi criada (não tem status synced/queued), cria primeiro
    already_synced = existing and any(e.get("status") in ("synced", "queued") for e in existing)
    if not already_synced:
        await _process_approved(db, seller, payment, existing)

    date_refunded = datetime.now().strftime("%Y-%m-%d")
    amount = payment["transaction_amount"]
    refunds = payment.get("refunds", [])

    if refunds:
        total_refunded_raw = sum(r.get("amount", 0) for r in refunds)
        date_refunded = refunds[-1].get("date_created", date_refunded)[:10]
    else:
        total_refunded_raw = payment.get("transaction_amount_refunded") or amount

    # Estorno da receita não pode exceder transaction_amount.
    # refund.amount pode incluir frete devolvido ao comprador, que não faz parte da receita.
    estorno_receita = min(total_refunded_raw, amount)

    contato = seller.get("ca_contato_ml")
    conta = seller["ca_conta_mp_retido"]
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

    # B) Estorno de comissão (ML devolve comissão → receita)
    net = payment.get("transaction_details", {}).get("net_received_amount", 0)
    total_fees = round(amount - net, 2) if net > 0 else 0

    if total_fees > 0 and estorno_receita >= amount:
        parcela_est = _build_parcela(f"Estorno taxa ML #{payment_id}", date_refunded, conta, total_fees)
        estorno_taxa_payload = _build_evento(
            date_refunded, total_fees,
            f"Estorno taxas ML - Payment {payment_id}",
            f"Estorno comissão+frete por devolução total",
            contato, conta, CA_CATEGORIES["estorno_taxa"], cc, parcela_est,
        )
        await ca_queue.enqueue_estorno_taxa(seller_slug, payment_id, estorno_taxa_payload)

    _upsert_payment(db, seller_slug, payment, "queued")


def _upsert_payment(db, seller_slug: str, payment: dict, status: str, error: str = None, ca_evento_id: str = None):
    """Insere ou atualiza payment no Supabase."""
    payment_id = payment["id"]
    data = {
        "seller_slug": seller_slug,
        "ml_payment_id": payment_id,
        "ml_status": payment.get("status"),
        "amount": payment.get("transaction_amount"),
        "net_amount": payment.get("transaction_details", {}).get("net_received_amount"),
        "money_release_date": (payment.get("money_release_date") or "")[:10] or None,
        "ml_order_id": (payment.get("order") or {}).get("id"),
        "status": status,
        "raw_payment": payment,
        "updated_at": datetime.now().isoformat(),
    }
    if error:
        data["error"] = error
    if ca_evento_id:
        data["ca_evento_id"] = ca_evento_id

    existing = db.table("payments").select("id").eq(
        "ml_payment_id", payment_id
    ).eq("seller_slug", seller_slug).execute()

    if existing.data:
        db.table("payments").update(data).eq("id", existing.data[0]["id"]).execute()
    else:
        data["created_at"] = datetime.now().isoformat()
        db.table("payments").insert(data).execute()

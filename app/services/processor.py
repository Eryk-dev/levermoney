"""
Processador de eventos ML/MP → Conta Azul.
Implementa o mapeamento definido em PLANO.md Seção 13.
"""
import logging
from datetime import datetime

from app.db.supabase import get_db
from app.models.sellers import CA_CATEGORIES, get_seller_config
from app.services import ml_api, ca_api

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


async def _criar_despesa_com_baixa(seller: dict, data: str, valor: float, descricao: str,
                                    observacao: str, categoria: str, nota_parcela: str = ""):
    """Cria conta-a-pagar no CA e tenta fazer baixa automática."""
    conta = seller["ca_conta_mp_retido"]
    contato = seller.get("ca_contato_ml")

    parcela = _build_parcela(descricao, data, conta, valor, nota_parcela)
    payload = _build_evento(data, valor, descricao, observacao, contato, conta,
                            categoria, seller.get("ca_centro_custo_variavel"), parcela)

    ca_evento = await ca_api.criar_conta_pagar(payload)
    evento_id = ca_evento.get("id")

    # Tentar baixa automática
    if evento_id:
        try:
            parcelas = await ca_api.listar_parcelas_evento(evento_id)
            if parcelas:
                parcela_id = parcelas[0].get("id") if isinstance(parcelas, list) else None
                if parcela_id:
                    await ca_api.criar_baixa(parcela_id, data, valor, conta)
        except Exception as e:
            logger.warning(f"Baixa failed for evento {evento_id}: {e}")

    return ca_evento


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

    if status == "approved":
        await _process_approved(db, seller, payment, existing.data)
    elif status == "refunded":
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

    # Se já sincronizou com sucesso, pula
    if existing and any(e.get("status") == "synced" for e in existing):
        logger.info(f"Payment {payment_id} already synced, skipping")
        return

    # 1. Dados do pedido
    order = None
    order_id = payment.get("order", {}).get("id") if payment.get("order") else None
    if order_id:
        try:
            order = await ml_api.get_order(seller_slug, order_id)
        except Exception as e:
            logger.warning(f"Could not fetch order {order_id}: {e}")

    # 2. Dados do frete
    shipping_cost_seller = 0.0
    shipping_id = None
    if order and order.get("shipping", {}).get("id"):
        shipping_id = order["shipping"]["id"]
        try:
            costs = await ml_api.get_shipment_costs(seller_slug, shipping_id)
            senders = costs.get("senders", [])
            if senders:
                shipping_cost_seller = senders[0].get("cost", 0.0)
        except Exception as e:
            logger.warning(f"Could not fetch shipping costs {shipping_id}: {e}")

    # 3. Extrair valores do payment
    amount = payment["transaction_amount"]
    date_approved = payment.get("date_approved", payment["date_created"])[:10]
    money_release_date = (payment.get("money_release_date") or date_approved)[:10]
    net = payment.get("transaction_details", {}).get("net_received_amount", 0)

    # Taxas - usar fee_details se disponível, senão calcular do net
    fees = payment.get("fee_details", [])
    mp_fee = sum(f["amount"] for f in fees if f.get("type") == "mercadopago_fee")
    financing_fee = sum(f["amount"] for f in fees if f.get("type") == "financing_fee")

    # Fallback: calcular comissão pela diferença quando fee_details vazio
    if not fees and net > 0 and amount > 0:
        implied_fee = round(amount - net - shipping_cost_seller, 2)
        if implied_fee > 0.01:
            mp_fee = implied_fee
            logger.info(f"Payment {payment_id}: fee_details empty, calculated mp_fee={mp_fee} from net")

    # Descrição
    item_title = ""
    if order and order.get("order_items"):
        item_title = order["order_items"][0].get("item", {}).get("title", "")
    desc_receita = f"Venda ML #{order_id or ''} - {item_title}"[:200]
    obs = f"Payment: {payment_id} | Liberação: {money_release_date}"

    contato = seller.get("ca_contato_ml")
    conta = seller["ca_conta_mp_retido"]
    cc = seller.get("ca_centro_custo_variavel")

    # === LANÇAMENTOS NO CONTA AZUL ===

    # A) RECEITA (contas-a-receber)
    parcela_receita = _build_parcela(desc_receita, money_release_date, conta, amount)
    receita_payload = _build_evento(
        date_approved, amount, desc_receita, obs, contato, conta,
        CA_CATEGORIES["venda_ml"], cc, parcela_receita,
    )

    ca_receita = None
    try:
        ca_receita = await ca_api.criar_conta_receber(receita_payload)
        logger.info(f"CA receita created for payment {payment_id}: {ca_receita.get('id')}")
    except Exception as e:
        logger.error(f"CA receita failed for payment {payment_id}: {e}")
        _upsert_payment(db, seller_slug, payment, "error_ca_receita", str(e))
        return

    # B) DESPESA - Comissão ML (se > 0)
    if mp_fee > 0:
        try:
            await _criar_despesa_com_baixa(
                seller, date_approved, mp_fee,
                f"Comissão ML - Payment {payment_id}",
                f"Venda #{order_id} | fee={mp_fee}",
                CA_CATEGORIES["comissao_ml"],
                f"Comissão ML #{payment_id}",
            )
            logger.info(f"CA comissão created for payment {payment_id}: R${mp_fee}")
        except Exception as e:
            logger.error(f"CA comissão failed for payment {payment_id}: {e}")

    # C) DESPESA - Frete (se > 0)
    if shipping_cost_seller > 0:
        try:
            await _criar_despesa_com_baixa(
                seller, date_approved, shipping_cost_seller,
                f"Frete MercadoEnvios - Payment {payment_id}",
                f"Shipment #{shipping_id}",
                CA_CATEGORIES["frete_mercadoenvios"],
                f"Frete ML #{payment_id}",
            )
            logger.info(f"CA frete created for payment {payment_id}: R${shipping_cost_seller}")
        except Exception as e:
            logger.error(f"CA frete failed for payment {payment_id}: {e}")

    # D) DESPESA - Financing fee / parcelamento (se > 0)
    if financing_fee > 0:
        try:
            await _criar_despesa_com_baixa(
                seller, date_approved, financing_fee,
                f"Taxa parcelamento ML - Payment {payment_id}",
                f"Financing fee #{payment_id}",
                CA_CATEGORIES["tarifa_pagamento"],
                f"Financing fee ML #{payment_id}",
            )
            logger.info(f"CA financing fee created for payment {payment_id}: R${financing_fee}")
        except Exception as e:
            logger.error(f"CA financing fee failed for payment {payment_id}: {e}")

    # Salva no Supabase como synced
    _upsert_payment(db, seller_slug, payment, "synced",
                     ca_evento_id=ca_receita.get("id") if ca_receita else None)

    # Validação: transaction_amount - fees == net_received_amount
    calculated_net = amount - mp_fee - financing_fee - shipping_cost_seller
    if net > 0 and abs(net - calculated_net) > 0.02:
        logger.warning(
            f"Payment {payment_id}: net mismatch! ML says {net}, we calc {calculated_net} "
            f"(diff={net - calculated_net})"
        )


async def _process_refunded(db, seller: dict, payment: dict, existing: list):
    """EVENTO 4: Cancelamento/Devolução → Estornos no CA."""
    payment_id = payment["id"]
    date_refunded = datetime.now().strftime("%Y-%m-%d")
    amount = payment["transaction_amount"]
    refunds = payment.get("refunds", [])

    if refunds:
        total_refunded = sum(r.get("amount", 0) for r in refunds)
        date_refunded = refunds[-1].get("date_created", date_refunded)[:10]
    else:
        total_refunded = payment.get("transaction_amount_refunded", amount)

    contato = seller.get("ca_contato_ml")
    conta = seller["ca_conta_mp_retido"]
    cc = seller.get("ca_centro_custo_variavel")

    # A) Estorno da receita (contas-a-pagar)
    parcela = _build_parcela(f"Devolução ML #{payment_id}", date_refunded, conta, total_refunded)
    estorno_payload = _build_evento(
        date_refunded, total_refunded,
        f"Devolução ML - Payment {payment_id}",
        f"Refund total: R${total_refunded}",
        contato, conta, CA_CATEGORIES["devolucao"], cc, parcela,
    )

    try:
        await ca_api.criar_conta_pagar(estorno_payload)
        logger.info(f"CA devolução created for payment {payment_id}")
    except Exception as e:
        logger.error(f"CA devolução failed for payment {payment_id}: {e}")

    # B) Estorno de comissão (ML devolve comissão → receita)
    fees = payment.get("fee_details", [])
    mp_fee = sum(f["amount"] for f in fees if f.get("type") == "mercadopago_fee")

    # Fallback: calcular comissão do net se fee_details vazio
    if not fees:
        net = payment.get("transaction_details", {}).get("net_received_amount", 0)
        if net > 0 and amount > 0:
            mp_fee = round(amount - net, 2)

    if mp_fee > 0 and total_refunded >= amount:
        parcela_est = _build_parcela(f"Estorno taxa ML #{payment_id}", date_refunded, conta, mp_fee)
        estorno_taxa_payload = _build_evento(
            date_refunded, mp_fee,
            f"Estorno comissão ML - Payment {payment_id}",
            f"Estorno taxa por devolução",
            contato, conta, CA_CATEGORIES["estorno_taxa"], cc, parcela_est,
        )
        try:
            await ca_api.criar_conta_receber(estorno_taxa_payload)
            logger.info(f"CA estorno taxa created for payment {payment_id}")
        except Exception as e:
            logger.error(f"CA estorno taxa failed for payment {payment_id}: {e}")

    _upsert_payment(db, seller["slug"], payment, "refunded")


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

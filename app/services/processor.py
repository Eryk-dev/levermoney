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
    order_id = payment.get("order", {}).get("id")
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

    # Taxas
    fees = payment.get("fee_details", [])
    mp_fee = sum(f["amount"] for f in fees if f.get("type") == "mercadopago_fee")
    financing_fee = sum(f["amount"] for f in fees if f.get("type") == "financing_fee")

    # Descrição
    item_title = ""
    if order and order.get("order_items"):
        item_title = order["order_items"][0].get("item", {}).get("title", "")
    desc_receita = f"Venda ML #{order_id or ''} - {item_title}"[:200]
    obs = f"Payment: {payment_id} | Liberação: {money_release_date}"

    # === LANÇAMENTOS NO CONTA AZUL ===

    # A) RECEITA (contas-a-receber)
    receita_payload = {
        "data_competencia": date_approved,
        "valor": amount,
        "descricao": desc_receita,
        "observacao": obs,
        "conta_financeira": seller["ca_conta_mp_retido"],
        "rateio": [{
            "id_categoria": CA_CATEGORIES["venda_ml"],
            "valor": amount,
            "rateio_centro_custo": [{
                "id_centro_custo": seller["ca_centro_custo_variavel"],
                "valor": amount,
            }],
        }],
        "condicao_pagamento": {
            "parcelas": [{
                "descricao": desc_receita,
                "data_vencimento": money_release_date,
                "conta_financeira": seller["ca_conta_mp_retido"],
                "detalhe_valor": {
                    "valor_bruto": amount,
                    "valor_liquido": amount,
                    "taxa": 0,
                    "desconto": 0,
                },
            }],
        },
    }

    ca_receita = None
    try:
        ca_receita = await ca_api.criar_conta_receber(receita_payload)
        logger.info(f"CA receita created for payment {payment_id}: {ca_receita.get('id')}")
    except Exception as e:
        logger.error(f"CA receita failed for payment {payment_id}: {e}")
        _upsert_payment(db, seller["slug"], payment, "error_ca_receita", str(e))
        return

    # B) DESPESA - Comissão ML (se > 0)
    ca_comissao = None
    if mp_fee > 0:
        comissao_payload = {
            "data_competencia": date_approved,
            "valor": mp_fee,
            "descricao": f"Comissão ML - Payment {payment_id}",
            "observacao": f"Venda #{order_id} | fee_type=mercadopago_fee",
            "conta_financeira": seller["ca_conta_mp_retido"],
            "rateio": [{
                "id_categoria": CA_CATEGORIES["comissao_ml"],
                "valor": mp_fee,
                "rateio_centro_custo": [{
                    "id_centro_custo": seller["ca_centro_custo_variavel"],
                    "valor": mp_fee,
                }],
            }],
            "condicao_pagamento": {
                "parcelas": [{
                    "descricao": f"Comissão ML #{payment_id}",
                    "data_vencimento": date_approved,
                    "conta_financeira": seller["ca_conta_mp_retido"],
                    "detalhe_valor": {
                        "valor_bruto": mp_fee,
                        "valor_liquido": mp_fee,
                        "taxa": 0,
                        "desconto": 0,
                    },
                }],
            },
        }
        try:
            ca_comissao = await ca_api.criar_conta_pagar(comissao_payload)
            logger.info(f"CA comissão created for payment {payment_id}")
            # Baixa automática (já deduzido pelo MP)
            parcelas = await ca_api.listar_parcelas_evento(ca_comissao["id"])
            if parcelas:
                parcela_id = parcelas[0]["id"] if isinstance(parcelas, list) else parcelas.get("items", [{}])[0].get("id")
                if parcela_id:
                    await ca_api.criar_baixa(parcela_id, {
                        "data": date_approved,
                        "valor": mp_fee,
                        "conta_financeira": seller["ca_conta_mp_retido"],
                    })
        except Exception as e:
            logger.error(f"CA comissão failed for payment {payment_id}: {e}")

    # C) DESPESA - Frete (se > 0)
    if shipping_cost_seller > 0:
        frete_payload = {
            "data_competencia": date_approved,
            "valor": shipping_cost_seller,
            "descricao": f"Frete MercadoEnvios - Payment {payment_id}",
            "observacao": f"Shipment #{shipping_id}",
            "conta_financeira": seller["ca_conta_mp_retido"],
            "rateio": [{
                "id_categoria": CA_CATEGORIES["frete_mercadoenvios"],
                "valor": shipping_cost_seller,
                "rateio_centro_custo": [{
                    "id_centro_custo": seller["ca_centro_custo_variavel"],
                    "valor": shipping_cost_seller,
                }],
            }],
            "condicao_pagamento": {
                "parcelas": [{
                    "descricao": f"Frete ML #{payment_id}",
                    "data_vencimento": date_approved,
                    "conta_financeira": seller["ca_conta_mp_retido"],
                    "detalhe_valor": {
                        "valor_bruto": shipping_cost_seller,
                        "valor_liquido": shipping_cost_seller,
                        "taxa": 0,
                        "desconto": 0,
                    },
                }],
            },
        }
        try:
            ca_frete = await ca_api.criar_conta_pagar(frete_payload)
            logger.info(f"CA frete created for payment {payment_id}")
            # Baixa automática
            parcelas = await ca_api.listar_parcelas_evento(ca_frete["id"])
            if parcelas:
                parcela_id = parcelas[0]["id"] if isinstance(parcelas, list) else parcelas.get("items", [{}])[0].get("id")
                if parcela_id:
                    await ca_api.criar_baixa(parcela_id, {
                        "data": date_approved,
                        "valor": shipping_cost_seller,
                        "conta_financeira": seller["ca_conta_mp_retido"],
                    })
        except Exception as e:
            logger.error(f"CA frete failed for payment {payment_id}: {e}")

    # D) DESPESA - Financing fee / parcelamento (se > 0)
    if financing_fee > 0:
        fin_payload = {
            "data_competencia": date_approved,
            "valor": financing_fee,
            "descricao": f"Taxa parcelamento ML - Payment {payment_id}",
            "conta_financeira": seller["ca_conta_mp_retido"],
            "rateio": [{
                "id_categoria": CA_CATEGORIES["comissao_ml"],
                "valor": financing_fee,
                "rateio_centro_custo": [{
                    "id_centro_custo": seller["ca_centro_custo_variavel"],
                    "valor": financing_fee,
                }],
            }],
            "condicao_pagamento": {
                "parcelas": [{
                    "descricao": f"Financing fee ML #{payment_id}",
                    "data_vencimento": date_approved,
                    "conta_financeira": seller["ca_conta_mp_retido"],
                    "detalhe_valor": {
                        "valor_bruto": financing_fee,
                        "valor_liquido": financing_fee,
                        "taxa": 0,
                        "desconto": 0,
                    },
                }],
            },
        }
        try:
            ca_fin = await ca_api.criar_conta_pagar(fin_payload)
            parcelas = await ca_api.listar_parcelas_evento(ca_fin["id"])
            if parcelas:
                parcela_id = parcelas[0]["id"] if isinstance(parcelas, list) else parcelas.get("items", [{}])[0].get("id")
                if parcela_id:
                    await ca_api.criar_baixa(parcela_id, {
                        "data": date_approved,
                        "valor": financing_fee,
                        "conta_financeira": seller["ca_conta_mp_retido"],
                    })
        except Exception as e:
            logger.error(f"CA financing fee failed for payment {payment_id}: {e}")

    # Salva no Supabase como synced
    _upsert_payment(db, seller["slug"], payment, "synced", ca_evento_id=ca_receita.get("id") if ca_receita else None)

    # Validação: transaction_amount - fees == net_received_amount
    net = payment.get("transaction_details", {}).get("net_received_amount", 0)
    calculated_net = amount - mp_fee - financing_fee - shipping_cost_seller
    if abs(net - calculated_net) > 0.02:
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
        # Devolução parcial ou total via refunds array
        total_refunded = sum(r.get("amount", 0) for r in refunds)
        date_refunded = refunds[-1].get("date_created", date_refunded)[:10]
    else:
        total_refunded = payment.get("transaction_amount_refunded", amount)

    # A) Estorno da receita (contas-a-pagar como despesa)
    estorno_payload = {
        "data_competencia": date_refunded,
        "valor": total_refunded,
        "descricao": f"Devolução ML - Payment {payment_id}",
        "observacao": f"Refund total: R${total_refunded}",
        "conta_financeira": seller["ca_conta_mp_retido"],
        "rateio": [{
            "id_categoria": CA_CATEGORIES["devolucao"],
            "valor": total_refunded,
            "rateio_centro_custo": [{
                "id_centro_custo": seller["ca_centro_custo_variavel"],
                "valor": total_refunded,
            }],
        }],
        "condicao_pagamento": {
            "parcelas": [{
                "descricao": f"Devolução ML #{payment_id}",
                "data_vencimento": date_refunded,
                "conta_financeira": seller["ca_conta_mp_retido"],
                "detalhe_valor": {
                    "valor_bruto": total_refunded,
                    "valor_liquido": total_refunded,
                    "taxa": 0,
                    "desconto": 0,
                },
            }],
        },
    }

    try:
        await ca_api.criar_conta_pagar(estorno_payload)
        logger.info(f"CA devolução created for payment {payment_id}")
    except Exception as e:
        logger.error(f"CA devolução failed for payment {payment_id}: {e}")

    # B) Estorno de comissão (ML devolve a comissão → receita para nós)
    fees = payment.get("fee_details", [])
    mp_fee = sum(f["amount"] for f in fees if f.get("type") == "mercadopago_fee")
    if mp_fee > 0 and total_refunded >= amount:
        estorno_taxa_payload = {
            "data_competencia": date_refunded,
            "valor": mp_fee,
            "descricao": f"Estorno comissão ML - Payment {payment_id}",
            "conta_financeira": seller["ca_conta_mp_retido"],
            "rateio": [{
                "id_categoria": CA_CATEGORIES["estorno_taxa"],
                "valor": mp_fee,
                "rateio_centro_custo": [{
                    "id_centro_custo": seller["ca_centro_custo_variavel"],
                    "valor": mp_fee,
                }],
            }],
            "condicao_pagamento": {
                "parcelas": [{
                    "descricao": f"Estorno taxa ML #{payment_id}",
                    "data_vencimento": date_refunded,
                    "conta_financeira": seller["ca_conta_mp_retido"],
                    "detalhe_valor": {
                        "valor_bruto": mp_fee,
                        "valor_liquido": mp_fee,
                        "taxa": 0,
                        "desconto": 0,
                    },
                }],
            },
        }
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

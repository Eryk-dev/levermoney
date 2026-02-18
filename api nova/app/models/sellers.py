"""
Configuração dos sellers ML e seus mapeamentos para o Conta Azul.
Cada seller tem: tokens ML, IDs de contas CA, centro de custo, etc.
"""
from app.config import settings

# Categorias CA compartilhadas (iguais para todos os sellers)
CA_CATEGORIES = {
    "venda_ml": "78f42170-23f7-41dc-80cd-7886c78fc397",           # 1.1.1 MercadoLibre
    "comissao_ml": "699d6072-031a-47bf-9aeb-563d1c2e8a41",        # 2.8.2 Comissões de Marketplace
    "frete_mercadoenvios": "6ccbf8ed-e174-4da0-ac8d-0ed1b387cb32", # 2.9.4 MercadoEnvios
    "frete_full": "27c8de66-cbb2-4778-94a5-b0de4405ae68",         # 2.9.10 Frete Full
    "devolucao": "713ee216-8abe-4bcd-bc54-34421cb62a06",           # 1.2.1 Devoluções e Cancelamentos
    "estorno_taxa": "c4cc890c-126a-48da-8e47-da7c1492620d",       # 1.3.4 Estornos de Taxas
    "estorno_frete": "2c0ef767-4983-4c4e-bfec-119f05708cd4",      # 1.3.7 Estorno de Frete
    "antecipacao": "7e9efb50-6039-4238-b844-a10507c42ff2",        # 2.11.9 Antecipação de Recebíveis
    "tarifa_pagamento": "d77aa9d6-dd63-4d67-a622-64c3a05780a5",   # 2.11.8 Tarifas de Pagamento
}

# Contato CA padrão — "MERCADO LIVRE" compartilhado por todos os sellers.
# Filtragem por seller é feita pela conta bancária (ca_conta_bancaria).
CA_CONTATO_ML = "b247cccb-38a2-4851-bf0e-700c53036c2c"


def get_seller_config(db, seller_slug: str) -> dict | None:
    """Busca config do seller no Supabase."""
    result = db.table("sellers").select("*").eq("slug", seller_slug).single().execute()
    return result.data if result.data else None


def get_seller_by_ml_user_id(db, ml_user_id: int) -> dict | None:
    """Busca seller pelo user_id do Mercado Livre."""
    result = db.table("sellers").select("*").eq("ml_user_id", ml_user_id).single().execute()
    return result.data if result.data else None


def get_all_active_sellers(db) -> list[dict]:
    """Lista todos os sellers ativos."""
    result = db.table("sellers").select("*").eq("active", True).execute()
    sellers = result.data or []
    allowlist_raw = (settings.seller_allowlist or "").strip()
    if not allowlist_raw:
        return sellers

    allowlist = {
        slug.strip().lower()
        for slug in allowlist_raw.split(",")
        if slug.strip()
    }
    if not allowlist:
        return sellers
    return [s for s in sellers if (s.get("slug") or "").lower() in allowlist]

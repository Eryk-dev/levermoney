"""
Cliente para API do Conta Azul v2.
Base: https://api-v2.contaazul.com
Rate limit: 600 req/min, 10 req/seg
"""
import httpx
from app.config import settings

CA_API = "https://api-v2.contaazul.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.ca_access_token}",
        "Content-Type": "application/json",
    }


async def criar_conta_receber(payload: dict) -> dict:
    """POST /v1/financeiro/eventos-financeiros/contas-a-receber"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-receber",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def criar_conta_pagar(payload: dict) -> dict:
    """POST /v1/financeiro/eventos-financeiros/contas-a-pagar"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{CA_API}/v1/financeiro/eventos-financeiros/contas-a-pagar",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def criar_baixa(parcela_id: str, payload: dict) -> dict:
    """POST /v1/financeiro/eventos-financeiros/parcelas/{id}/baixa"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{CA_API}/v1/financeiro/eventos-financeiros/parcelas/{parcela_id}/baixa",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def listar_parcelas_evento(evento_id: str) -> list:
    """GET /v1/financeiro/eventos-financeiros/{id}/parcelas - lista parcelas de um evento."""
    # A API usa query param no endpoint de parcelas
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{CA_API}/v1/financeiro/eventos-financeiros/parcelas",
            headers=_headers(),
            params={"id_evento": evento_id},
        )
        resp.raise_for_status()
        return resp.json()

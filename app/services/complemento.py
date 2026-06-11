"""LANÇADOR DE COMPLEMENTO — fecha o nível de LANÇAMENTO contra o extrato.

Princípio (decisão do dono, jun/2026): toda divergência entre o grupo lançado e o
caixa real é um FATO ECONÔMICO e vira lançamento CATEGORIZADO — nunca resíduo aberto.
E: **disputa/chargeback é CANCELAMENTO** — "nunca virou venda". Como o CA não deleta
eventos, cancelar = estorno na MESMA categoria (líquido da categoria zera no DRE) e o
movimento real do banco vira resultado de disputa em categoria própria.

Política por caso:

  DISPUTA/CHARGEBACK (status refunded/charged_back, não-reimbursed):
    SE a venda JÁ ENTROU no CA (grupo lançado != 0):
      1. anti-lançamento por categoria pra ZERAR o líquido atual de cada uma
         (venda_ml, comissao_ml, frete, estornos) → DRE: nunca foi venda.
      2. resultado real do banco (Σ extrato do ref) num único lançamento:
         < 0 → "Perda com disputa" (1.2.1 Devoluções e Cancelamentos)
         > 0 → "Resultado de disputa" (1.3.4 Estornos de Taxas)
    SE NUNCA ENTROU (nada lançado): NÃO cria par venda+cancelamento fantasma —
      só o resultado real do banco (se o dinheiro moveu); se nada moveu, nada.
    Invariante: Σ(grupo + complementos) == Σ extrato do ref, por construção.

  API-CEGA (approved limpo; banco creditou ≠ do que a API inteira diz):
    delta = extrato − grupo:
      < 0 → "Dívida ML compensada na liberação" (2.8.2 Comissões) — o caso R$6,46
      > 0 → "Crédito ML não-modelado" (1.3.7)

  REFUND PARCIAL (partially_refunded):
    delta < 0 → estorno parcial complementar (1.2.1) pelo valor REAL do extrato
    delta > 0 → reversão a maior (1.3.4)

Elegibilidade (produção): ciclo fechado — money_release_status terminal E última
linha do extrato do ref com idade >= cycle_grace_days (liberação parcelada não é
shortfall até o ciclo fechar). Offline (harness/janela passada): sempre elegível,
exceto boundary (sem linha de extrato na janela).

Módulo PURO: sem I/O. O runner (complemento_runner) busca dados e posta via ca_queue.
"""
from dataclasses import dataclass

# tipo de evento capturado/lançado -> categoria CA "dona" do líquido
TIPO_CATEGORIA = {
    "receita": "venda",
    "estorno": "venda",
    "partial_refund": "venda",
    "comissao": "comissao",
    "hiddenfee": "comissao",
    "estorno_taxa": "estorno_taxa",
    "frete": "frete",
    "estorno_frete": "estorno_frete",
    "subsidio": "estorno_frete",
}

# categoria lógica -> (CA_CATEGORIES key, descrição humana)
CATEGORIA_INFO = {
    "venda":         ("venda_ml", "Venda ML"),
    "comissao":      ("comissao_ml", "Comissão ML"),
    "frete":         ("frete_mercadoenvios", "Frete MercadoEnvios"),
    "estorno_taxa":  ("estorno_taxa", "Estorno de Taxas"),
    "estorno_frete": ("estorno_frete", "Estorno de Frete"),
    "perda_disputa": ("devolucao", "Perda com disputa"),
    "ganho_disputa": ("estorno_taxa", "Resultado de disputa"),
    "divida_ml":     ("comissao_ml", "Dívida ML compensada na liberação"),
    "credito_ml":    ("estorno_frete", "Crédito ML não-modelado"),
    "estorno_parcial": ("devolucao", "Estorno parcial (extrato)"),
    "reversao_maior": ("estorno_taxa", "Reversão a maior (extrato)"),
}

DISPUTA_STATUSES = {"refunded", "charged_back"}


@dataclass
class Complemento:
    ref: str
    categoria: str        # chave lógica (CATEGORIA_INFO)
    ca_categoria_key: str  # chave em CA_CATEGORIES
    valor: float          # assinado: + entrada (contas-a-receber), − saída (contas-a-pagar)
    data: str             # YYYY-MM-DD (data real do extrato)
    descricao: str
    motivo: str


def _liquido_por_categoria(net_por_tipo: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for tipo, v in net_por_tipo.items():
        cat = TIPO_CATEGORIA.get(tipo)
        if cat is None:
            continue
        out[cat] = round(out.get(cat, 0.0) + v, 2)
    return out


def plan_complemento(
    ref: str,
    payment: dict,
    net_por_tipo: dict[str, float],
    extrato_total: float,
    data_lancamento: str,
    elegivel: bool = True,
) -> list[Complemento]:
    """Planeja os lançamentos complementares de um ref.

    net_por_tipo: líquido ASSINADO já lançado, por tipo (receita +, comissao −, ...).
    extrato_total: Σ assinado de TODAS as linhas do extrato do ref.
    data_lancamento: data do último movimento do extrato do ref.

    Garantia: Σ(net_por_tipo) + Σ(complementos.valor) == extrato_total (round 2).
    """
    if not elegivel:
        return []

    grupo_total = round(sum(net_por_tipo.values()), 2)
    delta = round(extrato_total - grupo_total, 2)

    status = (payment or {}).get("status")
    detail = (payment or {}).get("status_detail") or ""
    is_disputa = status in DISPUTA_STATUSES and detail != "reimbursed"
    is_parcial = status == "approved" and "partially_refunded" in detail

    out: list[Complemento] = []

    if is_disputa:
        # 1. zera cada categoria (anti-lançamento na MESMA categoria → "nunca foi venda")
        liquidos = _liquido_por_categoria(net_por_tipo)
        for cat, liq in sorted(liquidos.items()):
            if abs(liq) < 0.01:
                continue
            ca_key, nome = CATEGORIA_INFO[cat]
            out.append(Complemento(
                ref=ref, categoria=cat, ca_categoria_key=ca_key,
                valor=round(-liq, 2), data=data_lancamento,
                descricao=f"Cancelamento {nome} (disputa) - Payment {ref}",
                motivo="disputa_cancelamento",
            ))
        # 2. resultado real do banco numa categoria própria
        if abs(extrato_total) >= 0.01:
            cat = "perda_disputa" if extrato_total < 0 else "ganho_disputa"
            ca_key, nome = CATEGORIA_INFO[cat]
            out.append(Complemento(
                ref=ref, categoria=cat, ca_categoria_key=ca_key,
                valor=round(extrato_total, 2), data=data_lancamento,
                descricao=f"{nome} - Payment {ref}",
                motivo="disputa_resultado",
            ))
        return out

    if abs(delta) < 0.01:
        return out

    if is_parcial:
        cat = "estorno_parcial" if delta < 0 else "reversao_maior"
    else:
        cat = "divida_ml" if delta < 0 else "credito_ml"
    ca_key, nome = CATEGORIA_INFO[cat]
    out.append(Complemento(
        ref=ref, categoria=cat, ca_categoria_key=ca_key,
        valor=delta, data=data_lancamento,
        descricao=f"{nome} - Payment {ref}",
        motivo=cat,
    ))
    return out

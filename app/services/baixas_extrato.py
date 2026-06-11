"""Fase 3-full — Baixa DIRIGIDA PELO EXTRATO.

Princípio: a baixa no Conta Azul deve usar a DATA e o VALOR REAIS do crédito no extrato
do Mercado Pago, não a promessa (`money_release_date` + valor da parcela). Assim o fluxo de
caixa do CA fica idêntico ao banco POR CONSTRUÇÃO, e liberação parcelada / cancela-antes-de-
liberar são tratados naturalmente.

Este módulo é a LÓGICA PURA (planejamento). A execução (buscar parcelas abertas no CA, postar
a baixa via ca_queue) é wiring de produção — ver `plan_baixas_from_extrato` -> enfileirar.

Algoritmo:
  Para cada linha de crédito/débito do extrato vinculada a um payment de venda:
    1. acha a(s) parcela(s) CA aberta(s) daquele payment (por payment_id na descrição);
    2. emite baixa com data = data do extrato, valor = valor (líquido) do extrato;
    3. se o valor do extrato != saldo aberto da parcela, emite AJUSTE da diferença
       (a diferença entre o provisório lançado e o caixa real).
  Liberação parcial: várias linhas de crédito p/ o mesmo payment -> várias baixas parciais.
  Cancela-antes-de-liberar: sem linha de crédito -> parcela nunca baixa -> sinalizada como
       'a receber que nunca virou caixa' (candidata a cancelamento).
"""
from dataclasses import dataclass, field


@dataclass
class BaixaPlan:
    parcela_id: str
    payment_id: str
    data_pagamento: str   # data REAL do extrato (YYYY-MM-DD)
    valor: float          # valor REAL do extrato
    ajuste: float = 0.0   # diferença vs saldo aberto da parcela (provisório - real)


@dataclass
class BaixaPlanResult:
    baixas: list = field(default_factory=list)         # BaixaPlan
    sem_parcela: list = field(default_factory=list)    # linhas de extrato sem parcela CA
    nunca_baixou: list = field(default_factory=list)   # parcelas abertas sem crédito no extrato


def plan_baixas_from_extrato(
    extrato_lines: list[dict],
    parcelas_abertas: list[dict],
    is_credit=lambda ln: ln.get("net", 0) > 0,
) -> BaixaPlanResult:
    """Planeja baixas a partir do extrato.

    extrato_lines: [{ref, net, date}] — ref = payment_id, net = valor (sinal), date = YYYY-MM-DD.
    parcelas_abertas: [{id, payment_id, nao_pago}] — parcelas EM_ABERTO no CA.
    is_credit: filtro de quais linhas geram baixa de recebível (default: net > 0).

    Casa por payment_id. Para liberação parcelada, consome o saldo aberto da parcela em ordem.
    """
    # index parcelas abertas por payment_id (pode haver várias por payment)
    by_payment: dict[str, list] = {}
    for p in parcelas_abertas:
        by_payment.setdefault(str(p["payment_id"]), []).append(dict(p))

    result = BaixaPlanResult()
    consumed_payments = set()
    last_baixa_by_ref = {}   # ref -> última BaixaPlan (p/ alocar over-release de linhas posteriores)

    for ln in extrato_lines:
        if not is_credit(ln):
            continue
        ref = str(ln.get("ref"))
        valor = abs(ln.get("net", 0.0))
        data = ln.get("date", "")
        parcelas = by_payment.get(ref)
        if not parcelas:
            result.sem_parcela.append({"ref": ref, "valor": valor, "data": data})
            continue
        consumed_payments.add(ref)
        # consome o crédito contra as parcelas abertas do payment
        restante = valor
        line_baixas = []
        for parc in parcelas:
            if restante <= 0.009:
                break
            saldo = float(parc.get("nao_pago", 0.0))
            if saldo <= 0.009:
                continue
            usar = min(restante, saldo)
            bp = BaixaPlan(
                parcela_id=str(parc["id"]), payment_id=ref,
                data_pagamento=data, valor=round(usar, 2),
                # ajuste = diferença entre o provisório lançado (saldo) e o que caiu (usar),
                # quando o crédito do extrato fecha a parcela com valor menor que o provisório
                ajuste=round(saldo - usar, 2) if usar < saldo - 0.01 else 0.0,
            )
            result.baixas.append(bp)
            line_baixas.append(bp)
            parc["nao_pago"] = round(saldo - usar, 2)
            restante = round(restante - usar, 2)
        if line_baixas:
            last_baixa_by_ref[ref] = line_baixas[-1]
        # over-release: ML liberou MAIS que o recebível (subsídio/over-release). Posta o
        # valor CHEIO do extrato (o caixa REAL), excesso vira ajuste NEGATIVO. Não dropar o
        # caixa que de fato caiu -> mantém Σbaixa == Σextrato por construção também aqui.
        # Aloca na última baixa do REF (mesmo de linha anterior, se a parcela já fechou).
        if restante > 0.009:
            target = last_baixa_by_ref.get(ref)
            if target is not None:
                target.valor = round(target.valor + restante, 2)
                target.ajuste = round(target.ajuste - restante, 2)

    # parcelas que nunca receberam crédito no extrato (cancela-antes-de-liberar / em trânsito)
    for ref, parcelas in by_payment.items():
        if ref in consumed_payments:
            continue
        for parc in parcelas:
            if float(parc.get("nao_pago", 0.0)) > 0.009:
                result.nunca_baixou.append({"parcela_id": str(parc["id"]), "payment_id": ref,
                                            "saldo": float(parc["nao_pago"])})
    return result


# ---------------------------------------------------------------------------
# TRIO — liquidação do grupo de venda dirigida pelo extrato (produção)
# ---------------------------------------------------------------------------
# No CA uma venda vira ATÉ 5 parcelas: receita (bruto, a-receber), comissão e
# frete (a-pagar), taxa oculta (a-pagar) e subsídio (a-receber). O crédito de
# "Liberação de dinheiro" no extrato é o LÍQUIDO. Pra o fluxo de caixa do CA
# espelhar o banco, TODO crédito do extrato liquida o grupo proporcionalmente
# na MESMA data, de modo que Σ(baixas assinadas do dia) == Σ(linhas do dia)
# EXATO — liberação parcelada incluída. Sobra de over-release vira AJUSTE
# explícito; shortfall (grupo não fecha) deixa resíduo aberto -> exceção
# `nunca_baixou` (não dá pra distinguir tranche futura de taxa não-modelada
# no momento da linha; o caixa do dia continua exato de qualquer forma).

PAPEL_SIGN = {
    "receita": +1.0,    # contas-a-receber
    "subsidio": +1.0,   # contas-a-receber (1.3.7)
    "comissao": -1.0,   # contas-a-pagar
    "frete": -1.0,      # contas-a-pagar
    "hiddenfee": -1.0,  # contas-a-pagar (taxa ML oculta)
}


@dataclass
class TrioBaixa:
    parcela_id: str
    payment_id: str
    papel: str
    data_pagamento: str   # data REAL do crédito no extrato (YYYY-MM-DD)
    valor: float          # sempre > 0 (valor da baixa na parcela)


@dataclass
class TrioPlanResult:
    baixas: list = field(default_factory=list)        # TrioBaixa
    ajustes: list = field(default_factory=list)       # {payment_id, data, valor(+), motivo}
    sem_parcela: list = field(default_factory=list)   # crédito sem grupo aberto no CA
    nunca_baixou: list = field(default_factory=list)  # resíduo aberto sem crédito que cubra


def plan_baixas_trio(extrato_lines: list[dict], parcelas: list[dict]) -> TrioPlanResult:
    """Planeja a liquidação do grupo de venda dirigida pelo extrato.

    extrato_lines: [{ref, net, date}] — SÓ créditos de liberação (net > 0),
                   em ordem cronológica (o caller filtra/ordena).
    parcelas:      [{id, payment_id, papel, valor_aberto}] — papel em PAPEL_SIGN.

    Invariante garantida: para cada linha L,
        Σ(PAPEL_SIGN[papel] * baixa.valor das baixas da linha) + ajuste == L.net
    => Σ caixa CA do dia == Σ extrato do dia, por construção.
    """
    by_payment: dict[str, list] = {}
    for p in parcelas:
        papel = p.get("papel")
        if papel not in PAPEL_SIGN:
            continue
        by_payment.setdefault(str(p["payment_id"]), []).append({
            "id": str(p["id"]), "papel": papel,
            "rem": round(float(p.get("valor_aberto", 0.0)), 2),
        })

    result = TrioPlanResult()

    for ln in extrato_lines:
        net = round(float(ln.get("net", 0.0)), 2)
        if net <= 0:
            continue
        ref = str(ln.get("ref"))
        data = ln.get("date", "")
        if ref not in by_payment:
            result.sem_parcela.append({"ref": ref, "valor": net, "data": data})
            continue
        grupo = [p for p in by_payment[ref] if p["rem"] >= 0.01]
        net_rem = round(sum(PAPEL_SIGN[p["papel"]] * p["rem"] for p in grupo), 2)

        if not grupo or net_rem <= 0.0:
            # grupo já liquidado (ou líquido restante <= 0) e chegou MAIS crédito:
            # caixa real sem contrapartida -> ajuste explícito (mantém o dia exato)
            result.ajustes.append({"payment_id": ref, "data": data, "valor": net,
                                   "motivo": "credito_sem_liquido_aberto"})
            continue

        if net >= net_rem - 0.01:
            # tranche final (ou única): liquida TUDO que resta do grupo
            for p in grupo:
                result.baixas.append(TrioBaixa(p["id"], ref, p["papel"], data, p["rem"]))
                p["rem"] = 0.0
            excesso = round(net - net_rem, 2)
            if excesso >= 0.01:
                # over-release (subsídio não-modelado): caixa real > grupo -> ajuste
                result.ajustes.append({"payment_id": ref, "data": data, "valor": excesso,
                                       "motivo": "over_release"})
        else:
            # tranche parcial: liquida proporcionalmente (f = net / net_rem) e
            # corrige o arredondamento na maior parcela pra fechar EXATO
            f = net / net_rem
            vals = []
            for p in grupo:
                v = round(p["rem"] * f, 2)
                vals.append(v)
            soma = round(sum(PAPEL_SIGN[p["papel"]] * v for p, v in zip(grupo, vals)), 2)
            residuo = round(net - soma, 2)
            if abs(residuo) >= 0.01:
                imax = max(range(len(grupo)), key=lambda i: grupo[i]["rem"])
                vals[imax] = round(vals[imax] + PAPEL_SIGN[grupo[imax]["papel"]] * residuo, 2)
            for p, v in zip(grupo, vals):
                if v < 0.01:
                    continue
                v = min(v, p["rem"])
                result.baixas.append(TrioBaixa(p["id"], ref, p["papel"], data, v))
                p["rem"] = round(p["rem"] - v, 2)

    for ref, grupo in by_payment.items():
        for p in grupo:
            if p["rem"] >= 0.01:
                result.nunca_baixou.append({"parcela_id": p["id"], "payment_id": ref,
                                            "papel": p["papel"], "saldo": p["rem"]})
    return result

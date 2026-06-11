"""Teste do TRIO — liquidação do grupo de venda dirigida pelo extrato.

Invariante central: para todo dia D,
    Σ extrato(D) == Σ PAPEL_SIGN[papel]*baixa.valor (D) + Σ ajustes(D)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.baixas_extrato import plan_baixas_trio, PAPEL_SIGN


def _caixa_por_dia(r):
    out = {}
    for b in r.baixas:
        out[b.data_pagamento] = round(out.get(b.data_pagamento, 0.0) + PAPEL_SIGN[b.papel] * b.valor, 2)
    for a in r.ajustes:
        out[a["data"]] = round(out.get(a["data"], 0.0) + a["valor"], 2)
    return out


def run():
    fails = []

    def check(n, desc, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] caso {n}: {desc}")
        if not cond:
            fails.append(n)

    # 1: trio simples — liberação == bruto-comissão-frete -> 3 baixas full, dia exato
    r = plan_baixas_trio(
        [{"ref": "100", "net": 388.63, "date": "2026-01-05"}],
        [{"id": "r1", "payment_id": "100", "papel": "receita", "valor_aberto": 471.45},
         {"id": "c1", "payment_id": "100", "papel": "comissao", "valor_aberto": 56.57},
         {"id": "f1", "payment_id": "100", "papel": "frete", "valor_aberto": 26.25}],
    )
    dia = _caixa_por_dia(r)
    check(1, "trio fecha exato no dia (471.45-56.57-26.25=388.63)",
          len(r.baixas) == 3 and dia.get("2026-01-05") == 388.63
          and not r.ajustes and not r.nunca_baixou)

    # 2: liberação PARCELADA — 2 tranches proporcionais, cada dia soma exato
    r = plan_baixas_trio(
        [{"ref": "200", "net": 200.00, "date": "2026-01-10"},
         {"ref": "200", "net": 188.63, "date": "2026-01-20"}],
        [{"id": "r2", "payment_id": "200", "papel": "receita", "valor_aberto": 471.45},
         {"id": "c2", "payment_id": "200", "papel": "comissao", "valor_aberto": 56.57},
         {"id": "f2", "payment_id": "200", "papel": "frete", "valor_aberto": 26.25}],
    )
    dia = _caixa_por_dia(r)
    check(2, "parcelada: cada tranche soma exato + grupo zera",
          dia.get("2026-01-10") == 200.00 and dia.get("2026-01-20") == 188.63
          and not r.nunca_baixou and not r.ajustes)

    # 3: over-release — ML liberou MAIS que o grupo -> ajuste explícito, dia exato
    r = plan_baixas_trio(
        [{"ref": "300", "net": 400.00, "date": "2026-02-01"}],
        [{"id": "r3", "payment_id": "300", "papel": "receita", "valor_aberto": 450.00},
         {"id": "c3", "payment_id": "300", "papel": "comissao", "valor_aberto": 60.00}],
    )
    dia = _caixa_por_dia(r)
    check(3, "over-release vira ajuste (+10) e dia soma 400.00",
          dia.get("2026-02-01") == 400.00 and len(r.ajustes) == 1
          and r.ajustes[0]["valor"] == 10.00 and r.ajustes[0]["motivo"] == "over_release")

    # 4: shortfall — ML liberou MENOS (taxa não-modelada): dia exato, resíduo -> nunca_baixou
    r = plan_baixas_trio(
        [{"ref": "400", "net": 10.24, "date": "2026-03-18"}],
        [{"id": "r4", "payment_id": "400", "papel": "receita", "valor_aberto": 20.00},
         {"id": "c4", "payment_id": "400", "papel": "comissao", "valor_aberto": 3.30}],
    )
    dia = _caixa_por_dia(r)
    check(4, "shortfall: dia soma exato 10.24 + resíduo fica em nunca_baixou",
          dia.get("2026-03-18") == 10.24 and len(r.nunca_baixou) >= 1)

    # 5: cancela-antes-de-liberar — sem crédito -> grupo inteiro em nunca_baixou
    r = plan_baixas_trio(
        [],
        [{"id": "r5", "payment_id": "500", "papel": "receita", "valor_aberto": 150.00},
         {"id": "c5", "payment_id": "500", "papel": "comissao", "valor_aberto": 20.00}],
    )
    check(5, "cancela-antes-liberar: 2 parcelas em nunca_baixou, 0 baixas",
          len(r.baixas) == 0 and len(r.nunca_baixou) == 2)

    # 6: crédito sem parcela (venda fora da janela) -> sem_parcela; crédito após grupo
    #    liquidado -> ajuste credito_sem_liquido_aberto
    r = plan_baixas_trio(
        [{"ref": "600", "net": 50.00, "date": "2026-04-01"},
         {"ref": "700", "net": 100.00, "date": "2026-04-02"},
         {"ref": "700", "net": 30.00, "date": "2026-04-09"}],
        [{"id": "r7", "payment_id": "700", "papel": "receita", "valor_aberto": 100.00}],
    )
    dia = _caixa_por_dia(r)
    check(6, "sem_parcela p/ ref desconhecida + crédito extra vira ajuste",
          len(r.sem_parcela) == 1 and r.sem_parcela[0]["ref"] == "600"
          and dia.get("2026-04-02") == 100.00 and dia.get("2026-04-09") == 30.00
          and any(a["motivo"] == "credito_sem_liquido_aberto" for a in r.ajustes))

    # 7: grupo de 5 papéis (com hiddenfee + subsidio) — sinais correto, dia exato
    r = plan_baixas_trio(
        [{"ref": "800", "net": 390.00, "date": "2026-05-05"}],
        [{"id": "r8", "payment_id": "800", "papel": "receita", "valor_aberto": 471.45},
         {"id": "c8", "payment_id": "800", "papel": "comissao", "valor_aberto": 56.57},
         {"id": "f8", "payment_id": "800", "papel": "frete", "valor_aberto": 26.25},
         {"id": "h8", "payment_id": "800", "papel": "hiddenfee", "valor_aberto": 3.63},
         {"id": "s8", "payment_id": "800", "papel": "subsidio", "valor_aberto": 5.00}],
    )
    dia = _caixa_por_dia(r)
    check(7, "grupo 5 papéis: 471.45-56.57-26.25-3.63+5.00=390.00 exato",
          len(r.baixas) == 5 and dia.get("2026-05-05") == 390.00 and not r.ajustes)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAIL: {fails}'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(run())

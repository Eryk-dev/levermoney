"""Teste do core da baixa extrato-dirigida (Fase 3-full)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.baixas_extrato import plan_baixas_from_extrato


def run():
    fails = []

    # Caso 1: liberação simples, valor bate -> 1 baixa com data/valor do extrato, sem ajuste
    r = plan_baixas_from_extrato(
        extrato_lines=[{"ref": "100", "net": 200.0, "date": "2026-01-05"}],
        parcelas_abertas=[{"id": "p100", "payment_id": "100", "nao_pago": 200.0}],
    )
    ok = (len(r.baixas) == 1 and r.baixas[0].data_pagamento == "2026-01-05"
          and r.baixas[0].valor == 200.0 and r.baixas[0].ajuste == 0.0)
    print(f"  [{'PASS' if ok else 'FAIL'}] caso 1: baixa simples usa data+valor do extrato")
    if not ok: fails.append(1)

    # Caso 2: liberação PARCELADA -> 2 créditos no extrato p/ o mesmo payment -> 2 baixas parciais
    r = plan_baixas_from_extrato(
        extrato_lines=[{"ref": "200", "net": 120.0, "date": "2026-01-10"},
                       {"ref": "200", "net": 80.0, "date": "2026-01-20"}],
        parcelas_abertas=[{"id": "p200", "payment_id": "200", "nao_pago": 200.0}],
    )
    ok = (len(r.baixas) == 2 and r.baixas[0].valor == 120.0 and r.baixas[1].valor == 80.0
          and r.baixas[1].data_pagamento == "2026-01-20")
    print(f"  [{'PASS' if ok else 'FAIL'}] caso 2: liberação parcelada -> N baixas parciais")
    if not ok: fails.append(2)

    # Caso 3: crédito do extrato MENOR que a parcela (ML descontou mais) -> ajuste da diferença
    r = plan_baixas_from_extrato(
        extrato_lines=[{"ref": "300", "net": 90.0, "date": "2026-01-15"}],
        parcelas_abertas=[{"id": "p300", "payment_id": "300", "nao_pago": 100.0}],
    )
    ok = (len(r.baixas) == 1 and r.baixas[0].valor == 90.0 and r.baixas[0].ajuste == 10.0)
    print(f"  [{'PASS' if ok else 'FAIL'}] caso 3: crédito < parcela -> ajuste R$10 (valor real do extrato)")
    if not ok: fails.append(3)

    # Caso 4: cancela-antes-de-liberar -> parcela aberta SEM crédito no extrato -> nunca_baixou
    r = plan_baixas_from_extrato(
        extrato_lines=[{"ref": "999", "net": -50.0, "date": "2026-01-01"}],  # só débito, sem crédito
        parcelas_abertas=[{"id": "p400", "payment_id": "400", "nao_pago": 150.0}],
    )
    ok = (len(r.baixas) == 0 and len(r.nunca_baixou) == 1
          and r.nunca_baixou[0]["payment_id"] == "400")
    print(f"  [{'PASS' if ok else 'FAIL'}] caso 4: cancela-antes-liberar -> parcela sinalizada nunca_baixou")
    if not ok: fails.append(4)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAIL: {fails}'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(run())

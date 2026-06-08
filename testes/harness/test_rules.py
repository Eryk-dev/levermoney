"""Testes das regras de classificacao do extrato (Fase 7) — importam as regras REAIS.

Roda: python3 -m testes.harness.test_rules
Cada caso = (transaction_type real do extrato, etype esperado, direcao esperada).
None etype = SKIP. '__income__'/'__expense__' valida direcao.
"""
import sys, os, unicodedata, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.extrato_ingester import EXTRATO_CLASSIFICATION_RULES as RULES


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def classify(tx_type):
    n = norm(tx_type)
    for pat, etype, direction, code in RULES:
        if pat in n:
            return etype, direction
    return "__OTHER__", None


# (tx_type real, etype_nao_pode_ser, direcao_esperada, label)
CASES = [
    # reembolso de boleto = ENTRADA, nao pode cair em skip de 'pagamento de conta'
    ("Reembolso de pagamento de conta Banco Safra S.A.", "income", "reembolso boleto -> income"),
    # pix recebido = entrada real, nao pode ser OTHER
    ("Pix recebido JOAO DA SILVA", "income", "pix recebido -> income"),
    ("Pix recebido 141 Air Comercio", "income", "pix recebido 2 -> income"),
    # compra ML em portugues deve ser SKIP (saida do proprio seller)
    ("Compra Mercado Livre", "skip", "compra ML PT -> skip"),
    # reversao de credito = SAIDA (expense), nao income
    ("Dinheiro recebido cancelado", "expense", "dinheiro recebido cancelado -> expense"),
]


def run():
    fails = []
    for tx, want, label in CASES:
        etype, direction = classify(tx)
        if want == "income":
            ok = direction == "income"
        elif want == "expense":
            ok = direction == "expense"
        elif want == "skip":
            ok = etype is None
        else:
            ok = etype != "__OTHER__"
        status = "PASS" if ok else "FAIL"
        if not ok:
            fails.append(label)
        print(f"  [{status}] {label:<34} -> etype={etype} dir={direction}  ({tx[:40]})")
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAIL'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(run())

"""Testes das regras de classificacao do extrato (Fase 7) — importam as regras REAIS.

Roda: python3 -m testes.harness.test_rules

Replica o pipeline REAL do ingester: regra -> override _SIGN_DRIVEN (sinal do CSV
manda em reversoes/cancelamentos, ERR-0025) -> dedup contra classifier (compra_ml
e similares deduplicam por ref/expense event, nao por skip de regra).
Cada caso = (transaction_type real, amount com sinal do extrato, expectativa).
"""
import sys, os, unicodedata, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.services.extrato_ingester import (
    EXTRATO_CLASSIFICATION_RULES as RULES,
    _SIGN_DRIVEN_EXPENSE_TYPES,
)


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def classify(tx_type, amount):
    """Replica regra + override sign-driven do ingester real."""
    n = norm(tx_type)
    for pat, etype, direction, code in RULES:
        if pat in n:
            if etype in _SIGN_DRIVEN_EXPENSE_TYPES:
                direction = "income" if (amount or 0) >= 0 else "expense"
            return etype, direction
    return "__OTHER__", None


# (tx_type real, amount assinado, expectativa, label)
CASES = [
    # reembolso de boleto = ENTRADA (+) — sign-driven corrige o pattern pagamento_conta
    ("Reembolso de pagamento de conta Banco Safra S.A.", +2168.75, "income",
     "reembolso boleto -> income"),
    # pagamento de conta normal = SAIDA (−)
    ("Pagamento de conta Itaú Unibanco  S.A.", -2168.75, "expense",
     "pagamento de conta -> expense"),
    # pix recebido = entrada real, nao pode ser OTHER
    ("Pix recebido JOAO DA SILVA", +100.0, "income", "pix recebido -> income"),
    ("Pix recebido 141 Air Comercio", +50.0, "income", "pix recebido 2 -> income"),
    # compra ML: classificada (nao OTHER) — double-count tratado por dedup vs classifier
    ("Compra Mercado Livre", -300.0, "classified", "compra ML PT -> classificada (dedup cobre)"),
    # reversao de credito = SAIDA (−) — sign-driven
    ("Dinheiro recebido cancelado", -80.0, "expense", "dinheiro recebido cancelado -> expense"),
    # reversao com sinal + (cancelamento de debito) tem que virar income
    ("Liberação de dinheiro cancelada", +40.0, "income", "liberacao cancelada (+) -> income"),
]


def run():
    fails = []
    for tx, amount, want, label in CASES:
        etype, direction = classify(tx, amount)
        if want == "income":
            ok = direction == "income"
        elif want == "expense":
            ok = direction == "expense"
        elif want == "skip":
            ok = etype is None
        elif want == "classified":
            ok = etype not in ("__OTHER__", None)
        else:
            ok = etype != "__OTHER__"
        status = "PASS" if ok else "FAIL"
        if not ok:
            fails.append(label)
        print(f"  [{status}] {label:<44} -> etype={etype} dir={direction}  ({tx[:40]})")
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAIL'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(run())

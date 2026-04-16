"""Reconciliação extrato ↔ sistema — engine core.

Lê spec 002-extrato-reconciliation/contracts/reconciliation.yml como source
of truth de parâmetros. Compara cada linha do extrato CSV contra
payment_events + mp_expenses no Supabase. Produz métricas consumíveis.

API pública:
    reconcile(seller, period) → ReconciliationMetrics
    ReconciliationMetrics.as_dict() → dict JSON-serializable

Status: baseline implementation conforme T-001. Bugs conhecidos
(ERR-0001..ERR-0004) ainda não corrigidos — coverage esperada:
~56% créditos, ~86% débitos para 141air jan/2026.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Optional

import yaml

from app.db.supabase import get_db
from app.services.extrato_ingester import (
    STALE_EXPENSE_TYPES,
    _CHECK_PAYMENTS,
    _classify_extrato_line,
    _normalize_text,
    _parse_account_statement,
    _resolve_check_payments,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACT_PATH = PROJECT_ROOT / "specs" / "002-extrato-reconciliation" / "contracts" / "reconciliation.yml"
EXTRATOS_DIR = PROJECT_ROOT / "testes" / "data" / "extratos"

# Mapping (seller, period) → extrato CSV filename
# Added explicitly to fail fast on unsupported combos (better than silent mismatch).
_PERIOD_TO_MES = {
    "2026-01": "janeiro",
    "2026-02": "fevereiro",
    "2026-03": "março",
}

_SELLER_TO_FILENAME = {
    # Mapping: (seller_slug, mes) → filename fragment used in the extrato CSV
    ("141air", "janeiro"): "extrato janeiro 141Air.csv",
    ("141air", "fevereiro"): "extrato fevereiro 141Air.csv",
    ("141air", "março"): "extrato março 141air.csv",
    ("net-air", "janeiro"): "extrato janeiro netair.csv",
    ("net-air", "fevereiro"): "extrato fevereiro netair.csv",
    ("netparts-sp", "janeiro"): "extrato janeiro netparts.csv",
    ("netparts-sp", "fevereiro"): "extrato fevereiro netparts.csv",
    ("easy-utilidades", "janeiro"): "extrato janeiro Easyutilidades.csv",
    ("easypeasy", "fevereiro"): "extrato fevereiro easypeasy.csv",
}


def _D(val: Any) -> Decimal:
    return Decimal(str(val or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _iso_to_ord(date_str: str) -> int:
    """Convert a YYYY-MM-DD string to a day ordinal (for small-date-diff checks)."""
    from datetime import date
    y, m, d = map(int, date_str[:10].split("-"))
    return date(y, m, d).toordinal()


# ---------------------------------------------------------------------------
# CashMovement model
# ---------------------------------------------------------------------------


@dataclass
class CashMovement:
    """Um movimento de caixa unificado (do extrato ou do sistema)."""
    date: str                   # YYYY-MM-DD
    ref_id: str                 # payment_id ou external ref
    amount: Decimal             # signed: + credit, - debit
    category: str               # canonical category name
    source: str                 # 'extrato' | 'payment_events' | 'mp_expenses'
    tx_type: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class MatchResult:
    """Resultado de match entre uma linha extrato e uma entry sistema."""
    status: str                 # 'match' | 'orphan_extrato' | 'orphan_system' | 'amount_diff' | 'skip'
    extrato: Optional[CashMovement] = None
    system: Optional[CashMovement] = None
    diff: Decimal = Decimal("0")


@dataclass
class ReconciliationMetrics:
    seller: str
    period: str
    extrato_lines: int
    coverage_credits: float       # percentage 0-100
    coverage_debits: float
    orphan_extrato_count: int
    orphan_system_count: int
    amount_diff_count: int
    matched_count: int
    skip_count: int
    daily_diff_max: float         # max abs daily diff across credits+debits
    divergent_days: int
    total_days: int
    extrato_credits_total: float
    extrato_debits_total: float
    # Detalhamento (pra debug, não é contrato de schema)
    orphan_extrato_by_category: dict = field(default_factory=dict)
    orphan_system_by_category: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        # Decimals são serializados via str(); mas aqui já temos floats
        return d


# ---------------------------------------------------------------------------
# Contract loader
# ---------------------------------------------------------------------------


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    """Carrega reconciliation.yml. Falha cedo se não existir."""
    if not path.exists():
        raise FileNotFoundError(f"Contract not found at {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Extrato loading
# ---------------------------------------------------------------------------


def _resolve_extrato_path(seller: str, period: str) -> Path:
    if period not in _PERIOD_TO_MES:
        raise ValueError(
            f"Period {period!r} not supported. "
            f"Add mapping to _PERIOD_TO_MES in app/services/reconciliation.py"
        )
    mes = _PERIOD_TO_MES[period]
    key = (seller, mes)
    if key not in _SELLER_TO_FILENAME:
        raise ValueError(
            f"No extrato CSV registered for seller={seller!r} mes={mes!r}. "
            f"Add mapping to _SELLER_TO_FILENAME in app/services/reconciliation.py"
        )
    p = EXTRATOS_DIR / _SELLER_TO_FILENAME[key]
    if not p.exists():
        raise FileNotFoundError(f"Extrato CSV missing: {p}")
    return p


def load_extrato(seller: str, period: str) -> tuple[dict, list[dict]]:
    """Return (summary, transactions)."""
    return _parse_account_statement(_resolve_extrato_path(seller, period).read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------------------
# DB loading (paginated to handle >1000 rows)
# ---------------------------------------------------------------------------


def _paginated(builder) -> list[dict]:
    rows: list[dict] = []
    page, size = 0, 1000
    while True:
        q = builder(page * size, (page + 1) * size - 1)
        batch = q.execute().data or []
        rows.extend(batch)
        if len(batch) < size:
            break
        page += 1
    return rows


def load_payment_events(db, seller: str, period_start: str, period_end: str) -> list[dict]:
    """All payment_events for seller whose lifecycle touches the period.

    A payment's lifecycle touches the period when either:
      • it has any event with event_date ∈ [period], OR
      • it has any event with competencia_date ∈ [period].

    Once a lifecycle is known to touch the period, ALL events of that pid
    must be loaded — otherwise `events_to_payment_movements` cannot rebuild
    release/refund groups when sale_approved and money_released fall in
    different months (ERR-0006).

    Deduplicates by id.
    """
    a = _paginated(lambda s, e: db.table("payment_events").select("*")
                   .eq("seller_slug", seller)
                   .gte("event_date", period_start).lte("event_date", period_end)
                   .range(s, e))
    b = _paginated(lambda s, e: db.table("payment_events").select("*")
                   .eq("seller_slug", seller)
                   .gte("competencia_date", period_start).lte("competencia_date", period_end)
                   .range(s, e))
    seen = {}
    for r in a + b:
        seen[r["id"]] = r

    # ERR-0006: expand to full lifecycle of every pid touched by the period.
    touched_pids = sorted({
        int(r["ml_payment_id"]) for r in seen.values()
        if r.get("ml_payment_id") is not None
    })
    for extra in load_events_for_pids(db, seller, touched_pids):
        seen[extra["id"]] = extra

    return list(seen.values())


def load_events_for_pids(db, seller: str, pids: list[int]) -> list[dict]:
    """For extrato ref_ids not yet in the period event query (payments released
    in the period but approved earlier), fetch their events separately."""
    rows: list[dict] = []
    for i in range(0, len(pids), 100):
        chunk = pids[i:i + 100]
        r = db.table("payment_events").select("*").eq(
            "seller_slug", seller
        ).in_("ml_payment_id", chunk).range(0, 4999).execute()
        rows.extend(r.data or [])
    return rows


def load_mp_expenses(db, seller: str, period_start: str, period_end: str) -> list[dict]:
    """Load mp_expenses whose date_approved falls inside the period.

    `date_approved` is stored as text in `YYYY-MM-DD` format. Comparing it
    against a timestamp string like `'2026-01-01T00:00:00'` under
    PostgREST's lexicographic text comparison excludes the first day of
    the period (ERR-0009). We compare against plain date strings.
    """
    return _paginated(lambda s, e: db.table("mp_expenses").select("*")
                      .eq("seller_slug", seller)
                      .gte("date_approved", period_start)
                      .lte("date_approved", period_end)
                      .range(s, e))


def load_mp_expenses_for_pids(db, seller: str, pids: list[int]) -> list[dict]:
    """Fetch mp_expenses for explicit payment_ids (any date).

    ERR-0013: ML may credit a bonus/cashback in month N+1 even though our
    expense_classifier captured it in month N (date_approved diverges from
    extrato release date). This helper lets reconciliation expand mp_expenses
    to the full lifecycle of any pid touched by the period's extrato.
    """
    if not pids:
        return []
    rows: list[dict] = []
    for i in range(0, len(pids), 100):
        chunk = pids[i:i + 100]
        # mp_expenses.payment_id is text — use 'in' on string list
        chunk_strs = [str(p) for p in chunk]
        r = db.table("mp_expenses").select("*").eq(
            "seller_slug", seller
        ).in_("payment_id", chunk_strs).range(0, 4999).execute()
        rows.extend(r.data or [])
    return rows


# ---------------------------------------------------------------------------
# Extrato → CashMovement
# ---------------------------------------------------------------------------


def _category_from_extrato(tx_type: str, ref_id: str, payment_ids: set[int]) -> str:
    """Mapeia linha do extrato para categoria canônica."""
    expense_type, _direction, _cat = _classify_extrato_line(tx_type)

    if expense_type is None:
        return "skip_internal"

    if expense_type == _CHECK_PAYMENTS:
        ref_as_int = int(ref_id) if str(ref_id).isdigit() else None
        if ref_as_int is not None and ref_as_int in payment_ids:
            normalized = _normalize_text(tx_type)
            if "liberacao" in normalized:
                return "liberacao"
            if "pagamento com" in normalized:
                return "pagamento_qr"
            if "pix recebido" in normalized:
                return "pix_recebido"
            if "dinheiro recebido" in normalized:
                return "dinheiro_recebido"
            if "reclamacoes" in normalized or "reclamações" in normalized:
                return "debito_divida_disputa"
            return "payment_cash"
        fallback_type, _ = _resolve_check_payments(tx_type)
        return fallback_type

    return expense_type


def extrato_to_movements(transactions: list[dict], payment_ids: set[int]) -> list[CashMovement]:
    return [
        CashMovement(
            date=tx["date"][:10],
            ref_id=str(tx["reference_id"]),
            amount=_D(tx["amount"]),
            category=_category_from_extrato(tx["transaction_type"], str(tx["reference_id"]), payment_ids),
            source="extrato",
            tx_type=tx["transaction_type"],
        )
        for tx in transactions
    ]


# ---------------------------------------------------------------------------
# payment_events → CashMovement (NET por payment na release_date)
#
# NOTA: implementação atual é "1 CashMovement por payment com NET agregado".
# Isso será refatorado em T-012 para "1 CashMovement por evento na event_date".
# Baseline atual tem amount_diffs por causa dessa simplificação.
# ---------------------------------------------------------------------------


RELEASE_EVENT_TYPES = {
    "sale_approved", "fee_charged", "shipping_charged", "subsidy_credited",
}
REFUND_EVENT_TYPES = {
    "refund_created", "refund_fee", "refund_shipping",
}
CASH_EVENT_TYPES = RELEASE_EVENT_TYPES | REFUND_EVENT_TYPES


# Expense types in mp_expenses that already represent fee-refund / income
# lines from the MP extrato. When a payment has any of these mp_expenses, the
# refund_fee+refund_shipping movement from payment_events would double-count
# the same cash event (ERR-0007 dedup).
_FEE_REFUND_DEDUP_EXPENSE_TYPES = frozenset({
    "reembolso_disputa",
    "reembolso_generico",
    "entrada_dinheiro",
})


def events_to_payment_movements(
    events: list[dict],
    pids_with_fee_refund_expense: set[str] | None = None,
) -> list[CashMovement]:
    """Emit one CashMovement per natural cash-event group.

    Two groups per payment (when present):
      • release group    = sale_approved + fee_charged + shipping_charged + subsidy_credited
                           dated at money_release_date (fallback: sale.event_date)
      • refund debit     = refund_created alone (≈ -sale), mirrors MP extrato
                           "Débito por dívida" line
      • refund fee line  = refund_fee + refund_shipping, mirrors MP extrato
                           "Reembolso Envío cancelado"/"Entrada de dinheiro"

    When ``pids_with_fee_refund_expense`` is supplied, the refund-fee movement
    is suppressed for those pids because the corresponding extrato line is
    already materialized in mp_expenses (dedup — ERR-0007).
    """
    if pids_with_fee_refund_expense is None:
        pids_with_fee_refund_expense = set()

    by_pid: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        pid = str(e.get("ml_payment_id") or "")
        if pid:
            by_pid[pid].append(e)

    movements: list[CashMovement] = []
    for pid, evs in by_pid.items():
        sale = next((e for e in evs if e["event_type"] == "sale_approved"), None)
        sd = (sale.get("metadata") or {}).get("status_detail") if sale else None

        # ── ERR-0008: detect same-day release+refund wash ────────────────
        # Only status_detail == "refunded" (plain cancellation / kit split)
        # nets out invisibly on the extrato. "bpp_refunded" etc. produce the
        # 3-line pattern (debit + release + fee refund) that must be matched.
        release_evs_all = [e for e in evs if e["event_type"] in RELEASE_EVENT_TYPES]
        refund_evs_all = [e for e in evs if e["event_type"] in REFUND_EVENT_TYPES]
        if release_evs_all and refund_evs_all and sd == "refunded":
            release_date_probe = None
            if sale:
                release_date_probe = (
                    (sale.get("metadata") or {}).get("money_release_date")
                    or sale.get("event_date")
                )
            if not release_date_probe:
                release_date_probe = release_evs_all[0].get("event_date")
            refund_date_probe = next(
                (e.get("event_date") for e in refund_evs_all
                 if e["event_type"] == "refund_created"),
                refund_evs_all[0].get("event_date"),
            )
            if release_date_probe and refund_date_probe:
                release_date_probe = release_date_probe[:10]
                refund_date_probe = refund_date_probe[:10]
                total_net = _D(sum(_D(e.get("signed_amount")) for e in release_evs_all + refund_evs_all))
                if release_date_probe == refund_date_probe and abs(total_net) < Decimal("0.02"):
                    continue  # wash — skip this payment entirely

        # ── Release group ────────────────────────────────────────────────
        release_evs = release_evs_all
        if release_evs and sd != "by_admin":
            release_date = None
            if sale:
                release_date = (sale.get("metadata") or {}).get("money_release_date")
                if not release_date:
                    release_date = sale.get("event_date")
            if not release_date:
                release_date = release_evs[0].get("event_date")
            if release_date:
                release_date = release_date[:10]
                net = _D(sum(_D(e.get("signed_amount")) for e in release_evs))
                if net != 0:
                    movements.append(CashMovement(
                        date=release_date,
                        ref_id=pid,
                        amount=net,
                        category="liberacao" if net > 0 else "liberacao_negative",
                        source="payment_events",
                        meta={"status_detail": sd, "group": "release",
                              "events": len(release_evs)},
                    ))

        # ── Refund group ─────────────────────────────────────────────────
        # ERR-0007: MP extrato splits a dispute refund into two lines:
        #   "Débito por dívida Reclamações"  = refund_created (≈ -sale)
        #   "Reembolso Envío cancelado"      = refund_fee + refund_shipping
        # We mirror this split: 1 movement per conceptual extrato line so the
        # matcher can pair them individually.
        refund_created = next(
            (e for e in evs if e["event_type"] == "refund_created"), None,
        )
        refund_fee_evs = [
            e for e in evs if e["event_type"] in ("refund_fee", "refund_shipping")
        ]

        refund_event_date = None
        if refund_created:
            refund_event_date = (refund_created.get("event_date") or "")[:10]
        elif refund_fee_evs:
            refund_event_date = (refund_fee_evs[0].get("event_date") or "")[:10]

        if refund_event_date:
            if refund_created:
                debit_amount = _D(refund_created.get("signed_amount"))
                if debit_amount != 0:
                    movements.append(CashMovement(
                        date=refund_event_date,
                        ref_id=pid,
                        amount=debit_amount,
                        category="debito_divida_disputa" if debit_amount < 0 else "reembolso_disputa",
                        source="payment_events",
                        meta={"status_detail": sd, "group": "refund_debit",
                              "events": 1},
                    ))

            if refund_fee_evs and pid not in pids_with_fee_refund_expense:
                fee_refund = _D(sum(_D(e.get("signed_amount")) for e in refund_fee_evs))
                if fee_refund != 0:
                    movements.append(CashMovement(
                        date=refund_event_date,
                        ref_id=pid,
                        amount=fee_refund,
                        category="reembolso_disputa" if fee_refund > 0 else "debito_divida_disputa",
                        source="payment_events",
                        meta={"status_detail": sd, "group": "refund_fee",
                              "events": len(refund_fee_evs)},
                    ))

    return movements


# ---------------------------------------------------------------------------
# mp_expenses → CashMovement
# ---------------------------------------------------------------------------


def _expense_type_to_category(expense_type: str) -> str:
    """Translate any producer's expense_type → canonical category for matcher.

    Different producers (extrato_ingester, expense_classifier) use different
    names for the same conceptual category. This translator collapses
    synonyms so the matcher's (ref_id, category) pass works as intended.

    See contract.yml#classifier_coverage for the canonical names.
    """
    mapping = {
        # ERR-0004: extrato uses pagamento_conta, classifier uses bill_payment
        "bill_payment": "pagamento_conta",
        # Intra-MP transfers — extrato canonical is transferencia_pix_in,
        # classifier emits transfer_intra
        "transfer_intra": "transferencia_pix_in",
        # Faturas ML vencidas: extrato line references a charge_id (not the
        # payment_id stored in mp_expenses), but both sides use amount+date
        # to identify the same event.
        "collection": "faturas_ml",
        # ERR-0013: ML "Bônus por envio" appears in extrato as bonus_envio,
        # but expense_classifier stores it as cashback (Bonificação Flex).
        "cashback": "bonus_envio",
    }
    return mapping.get(expense_type, expense_type)


# Reconciliation-only extension: when a payment has a sale_approved event in
# the ledger, the corresponding mp_expense with one of these types is a
# duplicate of the cash event already captured by the release group. The
# global STALE_EXPENSE_TYPES is kept narrow (covers only `_nao_sync` /
# `dinheiro_recebido`), so we layer this extra set here.
_RECON_STALE_WITH_SALE_TYPES = frozenset({
    # Payment was captured as an intra-MP pix receipt before the sale linked
    # back to it. Once sale_approved lands, the release group carries the
    # cash; the mp_expense row is stale.
    "transferencia_pix_in",
})



def filter_stale_mp_expenses(
    expenses: list[dict],
    approved_payment_ids: set[int],
    extrato_pids: set[str] | None = None,
) -> list[dict]:
    """Drop mp_expenses rows that violate the I-8 stale invariant.

    A row is stale when its expense_type is in STALE_EXPENSE_TYPES (or the
    reconciliation-local stale set) *and* the same payment_id has a
    sale_approved event in the ledger (meaning the canonical record now
    lives in payment_events).

    ERR-0015: ``cashback`` mp_expenses from expense_classifier may shadow
    either:
      1. An extrato line captured independently by extrato_ingester under a
         different expense_type (e.g. bonus_envio, entrada_dinheiro) — in
         that case the extrato_ingester row is canonical; drop the cashback
         to avoid duplicate sys movements.
      2. An MP-internal event that never surfaces in the extrato — keeping
         such a row produces an orphan_system. Drop it too.
      3. A genuine extrato line whose tx_type differs from the classifier
         category (e.g. "Dinheiro recebido Pagamento pelo Programa de
         Proteção Mercado Envios Full" or "Liberação de dinheiro" for a
         Cashback-branch payment) — keep the row; the matcher's ref+amount
         pass will pair them with the extrato line.

    Rule: drop a cashback row when either a non-cashback mp_expense with the
    same base ref and amount already exists in the batch (case 1), or the
    ref does not appear in the extrato at all (case 2). Otherwise keep it
    (case 3).
    """
    combined_stale = set(STALE_EXPENSE_TYPES) | _RECON_STALE_WITH_SALE_TYPES
    extrato_pids = extrato_pids or set()

    non_cashback_by_ref_amount: dict[tuple[str, str], bool] = {}
    for ex in expenses:
        if ex.get("expense_type") == "cashback":
            continue
        raw_pid = str(ex.get("payment_id") or "").split(":")[0]
        amt_key = f"{float(ex.get('amount') or 0):.2f}"
        non_cashback_by_ref_amount[(raw_pid, amt_key)] = True

    filtered: list[dict] = []
    for ex in expenses:
        expense_type = ex.get("expense_type", "")
        raw_pid = str(ex.get("payment_id") or "").split(":")[0]

        if expense_type == "cashback":
            amt_key = f"{float(ex.get('amount') or 0):.2f}"
            if (raw_pid, amt_key) in non_cashback_by_ref_amount:
                continue  # case 1: duplicate of extrato_ingester row
            if raw_pid and raw_pid not in extrato_pids:
                continue  # case 2: no extrato counterpart at all
            # case 3: keep — matcher pass 2 will pair by ref+amount

        if expense_type in combined_stale:
            if raw_pid.isdigit() and int(raw_pid) in approved_payment_ids:
                continue
        filtered.append(ex)
    return filtered


def align_refund_created_with_extrato(
    sys_movs: list[CashMovement],
    ext_movs: list[CashMovement],
) -> list[CashMovement]:
    """ERR-0010 / ERR-0012 / ERR-0014 — align system refund movements with extrato.

    Four corrections:
      1. **Phantom payment** — a pid has refund_debit/refund_fee in system
         but ZERO presence in extrato. The whole payment is a phantom
         (e.g. status=by_admin or refunded same-day with shipping inflation).
         Suppress all system movements for this pid (release + refund).
      2. **Refund without dispute line** — a pid has extrato presence but
         no `debito_divida_disputa` line. Suppress only refund_debit/
         refund_fee groups (the release group reflects a real cash event).
      3. **Aligned refund** — a pid has both a refund_debit movement and
         extrato `debito_divida_disputa` lines. Replace the single system
         refund_debit with one movement per extrato dispute line, each
         carrying the extrato amount and date. MP's actual debit (post
         dispute interest/admin fee) is the source of truth, and emitting
         one-per-line lets the matcher pair them 1:1.
      4. **BPP-refunded entrada-masked (ERR-0014)** — a pid whose release
         group is in system AND refund events happened (bpp_refunded) but
         whose only extrato line is an `entrada_dinheiro` credit. MP
         compensated the seller via "Entrada de dinheiro" instead of a
         normal "Liberação de dinheiro" line. The mp_expense row captures
         the real cash; the release + refund event groups are duplicates
         and must be suppressed.
    """
    ext_dispute_by_pid: dict[str, list[CashMovement]] = defaultdict(list)
    ext_pids: set[str] = set()
    ext_categories_by_pid: dict[str, set[str]] = defaultdict(set)
    for mv in ext_movs:
        ext_pids.add(mv.ref_id)
        ext_categories_by_pid[mv.ref_id].add(mv.category)
        if mv.category == "debito_divida_disputa" and mv.amount < 0:
            ext_dispute_by_pid[mv.ref_id].append(mv)

    refund_pids: set[str] = {
        mv.ref_id for mv in sys_movs
        if (mv.meta or {}).get("group") in {"refund_debit", "refund_fee"}
    }

    # ERR-0019: extrato releases with MP-deducted extra fees. When the extrato
    # has a single `liberacao` line for a ref AND the system release group
    # differs in amount (e.g. MP deducted an additional antecipation discount
    # or tax that isn't in the event ledger), the extrato is source of truth.
    # Replace the release group amount with the extrato amount on a 1-to-1 map.
    ext_liberacao_by_pid: dict[str, list[CashMovement]] = defaultdict(list)
    for mv in ext_movs:
        if mv.category == "liberacao" and mv.amount > 0:
            ext_liberacao_by_pid[mv.ref_id].append(mv)

    # ERR-0014: bpp_refunded (or similar) release masked by entrada_dinheiro.
    # When a payment's release group exists in the event ledger BUT the extrato
    # carries the cash as a plain entrada_dinheiro line (Programa de Proteção
    # do Mercado Envios Full, bpp-refunded compensation, etc.), the mp_expense
    # already captures the net cash — suppress the release movement and any
    # refund movements for that pid.
    bpp_entrada_masked_pids: set[str] = {
        mv.ref_id for mv in sys_movs
        if (mv.meta or {}).get("group") == "release"
        and (mv.meta or {}).get("status_detail") in {"bpp_refunded", "refunded"}
        and ext_categories_by_pid.get(mv.ref_id) == {"entrada_dinheiro"}
    }

    aligned: list[CashMovement] = []
    for mv in sys_movs:
        pid = mv.ref_id
        group = (mv.meta or {}).get("group")

        # (4) ERR-0014: bpp_refunded + entrada masks the event-level groups.
        if pid in bpp_entrada_masked_pids and group in {"release", "refund_debit", "refund_fee"}:
            continue

        # (1) Phantom payment: refund exists in events but extrato has nothing.
        if pid in refund_pids and pid not in ext_pids:
            continue

        # (2) Refund movement without matching extrato dispute line.
        if group in {"refund_debit", "refund_fee"} and pid not in ext_dispute_by_pid:
            continue

        # (3) Refund debit aligned to extrato: emit one mov per extrato line.
        if group == "refund_debit" and pid in ext_dispute_by_pid:
            for ext_line in ext_dispute_by_pid[pid]:
                new_meta = dict(mv.meta or {})
                new_meta["aligned_to_extrato"] = True
                new_meta["original_amount"] = float(mv.amount)
                aligned.append(CashMovement(
                    date=ext_line.date,
                    ref_id=mv.ref_id,
                    amount=ext_line.amount,
                    category=mv.category,
                    source=mv.source,
                    tx_type=mv.tx_type,
                    meta=new_meta,
                ))
            continue

        # (5) ERR-0019: release group aligned to extrato liberacao. When the
        # extrato has exactly one liberacao line for the pid and the sys
        # release group amount differs (MP deducted fees/discounts outside
        # the event ledger), trust the extrato amount.
        if group == "release" and len(ext_liberacao_by_pid.get(pid, [])) == 1:
            ext_line = ext_liberacao_by_pid[pid][0]
            if mv.amount != ext_line.amount:
                new_meta = dict(mv.meta or {})
                new_meta["aligned_to_extrato"] = True
                new_meta["original_amount"] = float(mv.amount)
                aligned.append(CashMovement(
                    date=ext_line.date,
                    ref_id=mv.ref_id,
                    amount=ext_line.amount,
                    category=mv.category,
                    source=mv.source,
                    tx_type=mv.tx_type,
                    meta=new_meta,
                ))
                continue

        aligned.append(mv)

    return aligned


def expenses_to_movements(
    expenses: list[dict],
    extrato_date_overrides: dict[str, str] | None = None,
) -> list[CashMovement]:
    """Convert mp_expenses rows to CashMovements.

    `extrato_date_overrides` (ERR-0013) lets the reconciliation engine remap
    a mp_expense.date_approved to the corresponding extrato line date when
    they diverge (e.g. cashback captured in month N but credited in month
    N+1). Keyed by ref_id; when present, overrides the row's date_approved.
    """
    overrides = extrato_date_overrides or {}
    movements: list[CashMovement] = []
    for ex in expenses:
        raw_pid = str(ex.get("payment_id") or "")
        base_ref = raw_pid.split(":")[0] if raw_pid else ""
        external = str(ex.get("external_reference") or "")
        ref_id = base_ref or external

        amount = _D(ex["amount"])
        direction = ex.get("expense_direction", "")
        expense_type = ex.get("expense_type", "")
        canonical = _expense_type_to_category(expense_type)

        if direction == "income":
            signed = amount
        elif direction == "expense":
            signed = -amount
        elif direction == "transfer":
            # Sign by CANONICAL category so the matcher's pair (extrato vs
            # mp_expense) see consistent signs. ERR-0005: historic rows with
            # raw_payment stripped still need the ERR-0001 default (incoming)
            # for transfer_intra, which maps to transferencia_pix_in.
            if canonical in (
                "deposit",
                "deposito_avulso",
                "transferencia_pix_in",
                "entrada_dinheiro",
            ):
                signed = amount
            elif canonical in (
                "transfer_pix",
                "pix_enviado",
                "transferencia_pix_out",
            ):
                signed = -amount
            else:
                # Unknown transfer: default incoming per ERR-0001 lesson.
                signed = amount
        else:
            signed = amount

        date = (ex.get("date_approved") or "")[:10]
        if ref_id in overrides:
            date = overrides[ref_id]
        if not date:
            continue

        movements.append(CashMovement(
            date=date,
            ref_id=ref_id,
            amount=signed,
            category=_expense_type_to_category(expense_type),
            source="mp_expenses",
            meta={"suffix": raw_pid.split(":")[1] if ":" in raw_pid else None,
                  "external": external},
        ))
    return movements


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


# ERR-0011 — categories where FX/IOF drift is expected (foreign-currency
# subscriptions). Match accepts up to PCT_TOLERANCE_BY_CATEGORY[cat] relative
# drift instead of strict per_line_brl tolerance.
PCT_TOLERANCE_BY_CATEGORY: dict[str, Decimal] = {
    "subscription": Decimal("0.05"),  # 3.5% IOF + small FX wiggle
}


def _within_pct_tolerance(ext_amount: Decimal, sys_amount: Decimal, pct: Decimal) -> bool:
    base = min(abs(ext_amount), abs(sys_amount))
    if base == 0:
        return ext_amount == sys_amount
    return abs(sys_amount - ext_amount) / base <= pct


def _is_match(ext: CashMovement, sys_mov: CashMovement, tolerance: Decimal) -> bool:
    """True if the diff fits inside per-line tolerance OR a category-aware %."""
    diff = abs(sys_mov.amount - ext.amount)
    if diff < tolerance:
        return True
    pct = PCT_TOLERANCE_BY_CATEGORY.get(ext.category)
    if pct is not None and ext.category == sys_mov.category:
        return _within_pct_tolerance(ext.amount, sys_mov.amount, pct)
    return False


def match_movements(
    extrato_movs: list[CashMovement],
    system_movs: list[CashMovement],
    tolerance: Decimal,
) -> list[MatchResult]:
    """Match 3-pass: (ref+category+amount) → (ref+amount) → (ref+same-sign unique)."""
    sys_pool: list[Optional[CashMovement]] = list(system_movs)
    results: list[MatchResult] = []

    for ext in extrato_movs:
        if ext.category == "skip_internal":
            results.append(MatchResult(status="skip", extrato=ext))
            continue

        matched_idx = None

        # Pass 1: (ref_id, category)
        for i, m in enumerate(sys_pool):
            if m is None:
                continue
            if m.ref_id == ext.ref_id and m.category == ext.category:
                if _is_match(ext, m, tolerance):
                    matched_idx = i
                    break

        # Pass 2: (ref_id, amount) ignoring category
        if matched_idx is None:
            for i, m in enumerate(sys_pool):
                if m is None:
                    continue
                if m.ref_id == ext.ref_id and _is_match(ext, m, tolerance):
                    matched_idx = i
                    break

        # Pass 3: same ref_id, same sign, único candidato
        if matched_idx is None:
            candidates = [
                i for i, m in enumerate(sys_pool)
                if m is not None and m.ref_id == ext.ref_id
                and ((ext.amount >= 0 and m.amount >= 0) or (ext.amount < 0 and m.amount < 0))
            ]
            if len(candidates) == 1:
                matched_idx = candidates[0]

        # Pass 4: ref_ids differ but (category, amount, date±1) is unique.
        # Covers cases where extrato uses one ID (e.g. charge_id) and the
        # system stores another (e.g. payment_id) for the same event. A ±1d
        # tolerance absorbs extrato/system posting-date drift.
        if matched_idx is None:
            candidates = [
                i for i, m in enumerate(sys_pool)
                if m is not None
                and m.category == ext.category
                and abs((_iso_to_ord(m.date) - _iso_to_ord(ext.date))) <= 1
                and _is_match(ext, m, tolerance)
            ]
            if len(candidates) == 1:
                matched_idx = candidates[0]

        if matched_idx is not None:
            sys_mov = sys_pool[matched_idx]
            sys_pool[matched_idx] = None
            diff = sys_mov.amount - ext.amount
            status = "match" if _is_match(ext, sys_mov, tolerance) else "amount_diff"
            results.append(MatchResult(status=status, extrato=ext, system=sys_mov, diff=diff))
        else:
            results.append(MatchResult(status="orphan_extrato", extrato=ext))

    # Remaining unmatched system movements
    for m in sys_pool:
        if m is not None:
            results.append(MatchResult(status="orphan_system", system=m))

    return results


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------


def compute_metrics(
    results: list[MatchResult],
    extrato_summary: dict,
    seller: str,
    period: str,
    extrato_lines: int,
    tolerance: Decimal,
) -> ReconciliationMetrics:
    credits_total = _D(extrato_summary["credits"])
    # debits no extrato vem negativo; convertemos para positivo absoluto
    debits_total = abs(_D(extrato_summary["debits"]))

    credits_match = _D(0)
    debits_match = _D(0)

    # Daily rollup for max daily diff
    daily_extrato_credit: dict[str, Decimal] = defaultdict(lambda: _D(0))
    daily_extrato_debit: dict[str, Decimal] = defaultdict(lambda: _D(0))
    daily_match_credit: dict[str, Decimal] = defaultdict(lambda: _D(0))
    daily_match_debit: dict[str, Decimal] = defaultdict(lambda: _D(0))

    orphan_ext_by_cat: dict[str, dict] = defaultdict(lambda: {"count": 0, "amount": _D(0)})
    orphan_sys_by_cat: dict[str, dict] = defaultdict(lambda: {"count": 0, "amount": _D(0)})

    counters = {"match": 0, "orphan_extrato": 0, "orphan_system": 0, "amount_diff": 0, "skip": 0}

    for r in results:
        counters[r.status] = counters.get(r.status, 0) + 1
        if r.extrato is not None:
            date = r.extrato.date
            amt = r.extrato.amount
            if amt > 0:
                daily_extrato_credit[date] += amt
            else:
                daily_extrato_debit[date] += -amt

            if r.status == "match":
                if amt > 0:
                    credits_match += amt
                    daily_match_credit[date] += amt
                else:
                    debits_match += -amt
                    daily_match_debit[date] += -amt

        if r.status == "orphan_extrato":
            cat = r.extrato.category
            orphan_ext_by_cat[cat]["count"] += 1
            orphan_ext_by_cat[cat]["amount"] += abs(r.extrato.amount)
        elif r.status == "orphan_system":
            cat = r.system.category
            orphan_sys_by_cat[cat]["count"] += 1
            orphan_sys_by_cat[cat]["amount"] += abs(r.system.amount)

    cov_cred = float(credits_match / credits_total * 100) if credits_total > 0 else 0.0
    cov_deb = float(debits_match / debits_total * 100) if debits_total > 0 else 0.0

    all_dates = set(daily_extrato_credit) | set(daily_extrato_debit)
    divergent_days = 0
    max_daily_diff = _D(0)
    for d in all_dates:
        diff_cred = daily_extrato_credit[d] - daily_match_credit[d]
        diff_deb = daily_extrato_debit[d] - daily_match_debit[d]
        if diff_cred > tolerance or diff_deb > tolerance:
            divergent_days += 1
        if diff_cred > max_daily_diff:
            max_daily_diff = diff_cred
        if diff_deb > max_daily_diff:
            max_daily_diff = diff_deb

    return ReconciliationMetrics(
        seller=seller,
        period=period,
        extrato_lines=extrato_lines,
        coverage_credits=round(cov_cred, 2),
        coverage_debits=round(cov_deb, 2),
        orphan_extrato_count=counters["orphan_extrato"],
        orphan_system_count=counters["orphan_system"],
        amount_diff_count=counters["amount_diff"],
        matched_count=counters["match"],
        skip_count=counters["skip"],
        daily_diff_max=float(max_daily_diff),
        divergent_days=divergent_days,
        total_days=len(all_dates),
        extrato_credits_total=float(credits_total),
        extrato_debits_total=float(debits_total),
        orphan_extrato_by_category={
            k: {"count": v["count"], "amount": float(v["amount"])}
            for k, v in orphan_ext_by_cat.items()
        },
        orphan_system_by_category={
            k: {"count": v["count"], "amount": float(v["amount"])}
            for k, v in orphan_sys_by_cat.items()
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reconcile(seller: str, period: str) -> ReconciliationMetrics:
    """Run reconciliation. period format: YYYY-MM."""
    contract = load_contract()
    tolerance = Decimal(str(contract["tolerances"]["per_line_brl"]))

    period_start = f"{period}-01"
    # naive "last day of month" — works for Jan (31). For other months, use calendar.
    import calendar
    year, month = int(period[:4]), int(period[5:7])
    last_day = calendar.monthrange(year, month)[1]
    period_end = f"{period}-{last_day:02d}"

    extrato_summary, transactions = load_extrato(seller, period)

    db = get_db()
    events = load_payment_events(db, seller, period_start, period_end)

    extrato_pids = {int(tx["reference_id"]) for tx in transactions
                    if str(tx["reference_id"]).isdigit()}
    current_pids = {int(e["ml_payment_id"]) for e in events if e.get("ml_payment_id")}
    extra_pids = list(extrato_pids - current_pids)
    if extra_pids:
        extra_events = load_events_for_pids(db, seller, extra_pids)
        events.extend(extra_events)

    expenses = load_mp_expenses(db, seller, period_start, period_end)

    # ERR-0013: pull mp_expenses whose payment_id matches an extrato ref
    # but whose date_approved is outside the period (e.g. cashback captured
    # in month N but credited by ML in month N+1).
    in_period_pids = {
        str(ex.get("payment_id") or "").split(":")[0]
        for ex in expenses
        if ex.get("payment_id")
    }
    extra_expense_pids = [
        p for p in extrato_pids if str(p) not in in_period_pids
    ]
    if extra_expense_pids:
        for ex in load_mp_expenses_for_pids(db, seller, extra_expense_pids):
            expenses.append(ex)

    payment_ids = {int(e["ml_payment_id"]) for e in events if e.get("ml_payment_id")}
    approved_pids = {
        int(e["ml_payment_id"]) for e in events
        if e.get("event_type") == "sale_approved" and e.get("ml_payment_id")
    }
    extrato_pids_str = {str(p) for p in extrato_pids}
    expenses = filter_stale_mp_expenses(expenses, approved_pids, extrato_pids_str)

    # ERR-0007 dedup: payments whose fee refund is already materialized in
    # mp_expenses must not get a duplicate CashMovement from refund_fee events.
    pids_with_fee_refund_expense = {
        str(ex.get("payment_id") or "").split(":")[0]
        for ex in expenses
        if ex.get("expense_type") in _FEE_REFUND_DEDUP_EXPENSE_TYPES
        and str(ex.get("payment_id") or "").split(":")[0].isdigit()
    }

    ext_movs = extrato_to_movements(transactions, payment_ids)

    # ERR-0013: build extrato_date_overrides for pids whose mp_expense is
    # outside the period but whose extrato line lives inside.
    extrato_date_overrides: dict[str, str] = {}
    if extra_expense_pids:
        extra_pid_strs = {str(p) for p in extra_expense_pids}
        for ext in ext_movs:
            if ext.ref_id in extra_pid_strs:
                extrato_date_overrides[ext.ref_id] = ext.date

    sys_movs = (
        events_to_payment_movements(events, pids_with_fee_refund_expense)
        + expenses_to_movements(expenses, extrato_date_overrides)
    )
    sys_movs = [m for m in sys_movs if period_start <= m.date <= period_end]

    # ERR-0010 / ERR-0012: align dispute refund_created with extrato debito.
    sys_movs = align_refund_created_with_extrato(sys_movs, ext_movs)

    results = match_movements(ext_movs, sys_movs, tolerance)

    return compute_metrics(
        results=results,
        extrato_summary=extrato_summary,
        seller=seller,
        period=period,
        extrato_lines=len(transactions),
        tolerance=tolerance,
    )

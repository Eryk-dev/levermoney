"""
Parity validation: mp_expenses vs event ledger responses.

For each consumer (list_expenses, expense_stats, pending_exports, financial_closing),
feeds IDENTICAL data through both the mp_expenses path and the ledger path, then
asserts that outputs match — same counts, same payment_ids, same totals.

Uses representative 141air jan2026 expense patterns:
- DIFAL (expense, auto_categorized)
- subscription (expense, pending_review)
- cashback (income, auto_categorized)
- payout (transfer, exported)
- deposit (transfer/income, auto_categorized)
- faturas_ml (expense, auto_categorized)

Run: python3 -m pytest testes/test_parity_expenses_source.py -v
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.services.event_ledger import (
    _group_expense_events,
    _build_expense_row,
    get_expense_list,
    get_expense_stats,
    get_pending_exports,
)


# ── Shared realistic data: 6 expenses matching 141air jan2026 patterns ─────


def _sample_expenses_mp() -> list[dict]:
    """Sample mp_expenses rows — as returned by Supabase mp_expenses table."""
    return [
        {
            "id": 90001,
            "payment_id": 90001,
            "seller_slug": "141air",
            "expense_type": "difal",
            "expense_direction": "expense",
            "ca_category": "2.2.7 Simples Nacional",
            "auto_categorized": True,
            "amount": 11.04,
            "description": "Débito por DIFAL",
            "business_branch": None,
            "operation_type": "regular_payment",
            "payment_method": None,
            "external_reference": None,
            "febraban_code": None,
            "date_created": "2026-01-10T10:00:00.000-03:00",
            "date_approved": "2026-01-10T10:00:00.000-03:00",
            "beneficiary_name": None,
            "notes": None,
            "status": "auto_categorized",
            "exported_at": None,
            "created_at": "2026-01-10T13:00:00.000Z",
        },
        {
            "id": 90002,
            "payment_id": 90002,
            "seller_slug": "141air",
            "expense_type": "subscription",
            "expense_direction": "expense",
            "ca_category": None,
            "auto_categorized": False,
            "amount": 79.90,
            "description": "Mercado Pago - Assinatura",
            "business_branch": None,
            "operation_type": "regular_payment",
            "payment_method": None,
            "external_reference": None,
            "febraban_code": None,
            "date_created": "2026-01-12T08:30:00.000-03:00",
            "date_approved": "2026-01-12T08:30:00.000-03:00",
            "beneficiary_name": None,
            "notes": None,
            "status": "pending_review",
            "exported_at": None,
            "created_at": "2026-01-12T11:30:00.000Z",
        },
        {
            "id": 90003,
            "payment_id": 90003,
            "seller_slug": "141air",
            "expense_type": "cashback",
            "expense_direction": "income",
            "ca_category": "1.3.7 Estorno de Frete sobre Vendas",
            "auto_categorized": True,
            "amount": 25.50,
            "description": "Cashback crédito",
            "business_branch": None,
            "operation_type": None,
            "payment_method": None,
            "external_reference": "REF-CB-001",
            "febraban_code": None,
            "date_created": "2026-01-15T14:00:00.000-03:00",
            "date_approved": "2026-01-15T14:00:00.000-03:00",
            "beneficiary_name": None,
            "notes": None,
            "status": "auto_categorized",
            "exported_at": None,
            "created_at": "2026-01-15T17:00:00.000Z",
        },
        {
            "id": 90004,
            "payment_id": 90004,
            "seller_slug": "141air",
            "expense_type": "payout",
            "expense_direction": "transfer",
            "ca_category": "2.4.1 Saques e Transferencias",
            "auto_categorized": True,
            "amount": 5000.00,
            "description": "Saque PIX p/ conta 1234 - R$ 5000.00",
            "business_branch": None,
            "operation_type": None,
            "payment_method": None,
            "external_reference": None,
            "febraban_code": None,
            "date_created": "2026-01-18T09:00:00.000-03:00",
            "date_approved": "2026-01-18T09:00:00.000-03:00",
            "beneficiary_name": None,
            "notes": None,
            "status": "exported",
            "exported_at": "2026-01-20T10:00:00.000Z",
            "created_at": "2026-01-18T12:00:00.000Z",
        },
        {
            "id": 90005,
            "payment_id": 90005,
            "seller_slug": "141air",
            "expense_type": "deposit",
            "expense_direction": "transfer",
            "ca_category": "1.4.1 Depositos Recebidos",
            "auto_categorized": True,
            "amount": 200.00,
            "description": "Depósito avulso recebido",
            "business_branch": None,
            "operation_type": None,
            "payment_method": None,
            "external_reference": None,
            "febraban_code": None,
            "date_created": "2026-01-20T11:00:00.000-03:00",
            "date_approved": "2026-01-20T11:00:00.000-03:00",
            "beneficiary_name": None,
            "notes": None,
            "status": "auto_categorized",
            "exported_at": None,
            "created_at": "2026-01-20T14:00:00.000Z",
        },
        {
            "id": 90006,
            "payment_id": 90006,
            "seller_slug": "141air",
            "expense_type": "faturas_ml",
            "expense_direction": "expense",
            "ca_category": "2.1.8 Faturas ML",
            "auto_categorized": True,
            "amount": 150.33,
            "description": "Fatura Mercado Livre",
            "business_branch": None,
            "operation_type": None,
            "payment_method": None,
            "external_reference": "FAT-2026-01",
            "febraban_code": None,
            "date_created": "2026-01-25T16:00:00.000-03:00",
            "date_approved": "2026-01-25T16:00:00.000-03:00",
            "beneficiary_name": None,
            "notes": None,
            "status": "auto_categorized",
            "exported_at": None,
            "created_at": "2026-01-25T19:00:00.000Z",
        },
    ]


def _sample_events_from_mp(mp_rows: list[dict]) -> list[dict]:
    """Build ledger events (expense_captured + expense_classified) from mp_expenses rows.

    This mirrors what the dual-write code produces: one expense_captured per row,
    plus expense_classified when auto_categorized=True, plus expense_exported for exported.
    """
    events: list[dict] = []
    for r in mp_rows:
        ref_id = str(r["payment_id"])

        # expense_captured — contains all the metadata
        captured_meta = {
            "expense_type": r["expense_type"],
            "expense_direction": r["expense_direction"],
            "ca_category": r.get("ca_category"),
            "auto_categorized": r.get("auto_categorized", False),
            "amount": r["amount"],
            "description": r.get("description"),
            "business_branch": r.get("business_branch"),
            "operation_type": r.get("operation_type"),
            "payment_method": r.get("payment_method"),
            "external_reference": r.get("external_reference"),
            "febraban_code": r.get("febraban_code"),
            "date_created": r["date_created"],
            "date_approved": r["date_approved"],
            "beneficiary_name": r.get("beneficiary_name"),
            "notes": r.get("notes"),
        }
        comp_date = r["date_approved"][:10] if r.get("date_approved") else "2026-01-01"
        events.append({
            "reference_id": ref_id,
            "event_type": "expense_captured",
            "signed_amount": -abs(r["amount"]) if r["expense_direction"] == "expense" else abs(r["amount"]),
            "competencia_date": comp_date,
            "metadata": captured_meta,
            "created_at": r.get("created_at", "2026-01-01T00:00:00Z"),
        })

        # expense_classified — when auto_categorized and has ca_category
        if r.get("auto_categorized") and r.get("ca_category"):
            events.append({
                "reference_id": ref_id,
                "event_type": "expense_classified",
                "signed_amount": 0,
                "competencia_date": comp_date,
                "metadata": {
                    "expense_type": r["expense_type"],
                    "ca_category": r["ca_category"],
                },
                "created_at": r.get("created_at", "2026-01-01T00:00:00Z"),
            })

        # expense_exported — for rows with status=exported
        if r.get("status") == "exported":
            events.append({
                "reference_id": ref_id,
                "event_type": "expense_exported",
                "signed_amount": 0,
                "competencia_date": comp_date,
                "metadata": {"expense_type": r["expense_type"], "batch_id": "exp_parity_test"},
                "created_at": r.get("exported_at", r.get("created_at", "2026-01-01T00:00:01Z")),
            })

    return events


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def mp_rows():
    return _sample_expenses_mp()


@pytest.fixture
def ledger_events(mp_rows):
    return _sample_events_from_mp(mp_rows)


# ===========================================================================
# 1. list_expenses parity — same count, same payment_ids
# ===========================================================================

class TestListExpensesParity:
    """Compare list_expenses output from mp_expenses vs ledger."""

    @pytest.mark.asyncio
    async def test_same_count(self, mp_rows, ledger_events):
        """Both sources return the same number of expenses."""
        # mp_expenses path: simulated rows from DB
        mp_count = len(mp_rows)

        # ledger path
        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1000)

        assert len(ledger_rows) == mp_count, (
            f"Count mismatch: mp_expenses={mp_count}, ledger={len(ledger_rows)}"
        )

    @pytest.mark.asyncio
    async def test_same_payment_ids(self, mp_rows, ledger_events):
        """Both sources return the same set of payment_ids."""
        mp_pids = {str(r["payment_id"]) for r in mp_rows}

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1000)

        ledger_pids = {r["payment_id"] for r in ledger_rows}
        assert ledger_pids == mp_pids, (
            f"Payment ID mismatch: only_mp={mp_pids - ledger_pids}, only_ledger={ledger_pids - mp_pids}"
        )

    @pytest.mark.asyncio
    async def test_same_amounts_per_id(self, mp_rows, ledger_events):
        """Each expense has the same amount in both sources."""
        mp_amounts = {str(r["payment_id"]): float(r["amount"]) for r in mp_rows}

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1000)

        for row in ledger_rows:
            pid = row["payment_id"]
            assert float(row["amount"]) == mp_amounts[pid], (
                f"Amount mismatch for {pid}: mp={mp_amounts[pid]}, ledger={row['amount']}"
            )

    @pytest.mark.asyncio
    async def test_same_expense_types(self, mp_rows, ledger_events):
        """Each expense has the same expense_type in both sources."""
        mp_types = {str(r["payment_id"]): r["expense_type"] for r in mp_rows}

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1000)

        for row in ledger_rows:
            pid = row["payment_id"]
            assert row["expense_type"] == mp_types[pid], (
                f"Type mismatch for {pid}: mp={mp_types[pid]}, ledger={row['expense_type']}"
            )

    @pytest.mark.asyncio
    async def test_same_directions(self, mp_rows, ledger_events):
        """Each expense has the same expense_direction in both sources."""
        mp_dirs = {str(r["payment_id"]): r["expense_direction"] for r in mp_rows}

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1000)

        for row in ledger_rows:
            pid = row["payment_id"]
            assert row["expense_direction"] == mp_dirs[pid], (
                f"Direction mismatch for {pid}: mp={mp_dirs[pid]}, ledger={row['expense_direction']}"
            )

    @pytest.mark.asyncio
    async def test_same_categories(self, mp_rows, ledger_events):
        """Each expense has the same ca_category in both sources."""
        mp_cats = {str(r["payment_id"]): r.get("ca_category") for r in mp_rows}

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1000)

        for row in ledger_rows:
            pid = row["payment_id"]
            assert row["ca_category"] == mp_cats[pid], (
                f"Category mismatch for {pid}: mp={mp_cats[pid]}, ledger={row['ca_category']}"
            )

    @pytest.mark.asyncio
    async def test_same_descriptions(self, mp_rows, ledger_events):
        """Each expense has the same description in both sources."""
        mp_descs = {str(r["payment_id"]): r.get("description") for r in mp_rows}

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1000)

        for row in ledger_rows:
            pid = row["payment_id"]
            assert row["description"] == mp_descs[pid], (
                f"Description mismatch for {pid}: mp={mp_descs[pid]}, ledger={row['description']}"
            )


# ===========================================================================
# 2. expense_stats parity — same totals and counters
# ===========================================================================

class TestExpenseStatsParity:
    """Compare expense_stats output from mp_expenses vs ledger."""

    def _compute_mp_stats(self, mp_rows: list[dict], status_filter: list[str] | None = None) -> dict:
        """Compute stats the same way the mp_expenses endpoint does."""
        by_type: dict[str, int] = {}
        by_direction: dict[str, int] = {}
        by_status: dict[str, int] = {}
        total_amount = 0.0

        for r in mp_rows:
            s = r.get("status", "unknown")
            if status_filter and s not in status_filter:
                continue
            t = r.get("expense_type", "unknown")
            d = r.get("expense_direction", "unknown")
            amt = float(r.get("amount") or 0)

            by_type[t] = by_type.get(t, 0) + 1
            by_direction[d] = by_direction.get(d, 0) + 1
            by_status[s] = by_status.get(s, 0) + 1
            total_amount += amt

        total = sum(by_status.values())
        return {
            "seller": "141air",
            "total": total,
            "total_amount": round(total_amount, 2),
            "by_type": by_type,
            "by_direction": by_direction,
            "by_status": by_status,
            "pending_review_count": by_status.get("pending_review", 0),
            "auto_categorized_count": by_status.get("auto_categorized", 0),
        }

    @pytest.mark.asyncio
    async def test_stats_total_count(self, mp_rows, ledger_events):
        mp_stats = self._compute_mp_stats(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_stats = await get_expense_stats("141air")

        assert ledger_stats["total"] == mp_stats["total"], (
            f"Total count: mp={mp_stats['total']}, ledger={ledger_stats['total']}"
        )

    @pytest.mark.asyncio
    async def test_stats_total_amount(self, mp_rows, ledger_events):
        mp_stats = self._compute_mp_stats(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_stats = await get_expense_stats("141air")

        assert ledger_stats["total_amount"] == mp_stats["total_amount"], (
            f"Total amount: mp={mp_stats['total_amount']}, ledger={ledger_stats['total_amount']}"
        )

    @pytest.mark.asyncio
    async def test_stats_by_type(self, mp_rows, ledger_events):
        mp_stats = self._compute_mp_stats(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_stats = await get_expense_stats("141air")

        assert ledger_stats["by_type"] == mp_stats["by_type"], (
            f"by_type: mp={mp_stats['by_type']}, ledger={ledger_stats['by_type']}"
        )

    @pytest.mark.asyncio
    async def test_stats_by_direction(self, mp_rows, ledger_events):
        mp_stats = self._compute_mp_stats(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_stats = await get_expense_stats("141air")

        assert ledger_stats["by_direction"] == mp_stats["by_direction"], (
            f"by_direction: mp={mp_stats['by_direction']}, ledger={ledger_stats['by_direction']}"
        )

    @pytest.mark.asyncio
    async def test_stats_by_status(self, mp_rows, ledger_events):
        mp_stats = self._compute_mp_stats(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_stats = await get_expense_stats("141air")

        assert ledger_stats["by_status"] == mp_stats["by_status"], (
            f"by_status: mp={mp_stats['by_status']}, ledger={ledger_stats['by_status']}"
        )

    @pytest.mark.asyncio
    async def test_stats_pending_review_count(self, mp_rows, ledger_events):
        mp_stats = self._compute_mp_stats(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_stats = await get_expense_stats("141air")

        assert ledger_stats["pending_review_count"] == mp_stats["pending_review_count"]

    @pytest.mark.asyncio
    async def test_stats_auto_categorized_count(self, mp_rows, ledger_events):
        mp_stats = self._compute_mp_stats(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_stats = await get_expense_stats("141air")

        assert ledger_stats["auto_categorized_count"] == mp_stats["auto_categorized_count"]

    @pytest.mark.asyncio
    async def test_stats_with_status_filter(self, mp_rows, ledger_events):
        """With status_filter, both sources return matching subsets."""
        filt = ["pending_review", "auto_categorized"]
        mp_stats = self._compute_mp_stats(mp_rows, status_filter=filt)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_stats = await get_expense_stats("141air", status_filter=filt)

        assert ledger_stats["total"] == mp_stats["total"]
        assert ledger_stats["total_amount"] == mp_stats["total_amount"]
        assert ledger_stats["by_type"] == mp_stats["by_type"]


# ===========================================================================
# 3. export (pending_exports) parity — same XLSX rows
# ===========================================================================

class TestExportParity:
    """Compare get_pending_exports output from mp_expenses vs ledger.

    The mp_expenses export path filters: status NOT IN ('exported', 'imported').
    The ledger path filters: expense_captured WITHOUT expense_exported.
    Both should yield the same set of exportable rows.
    """

    @pytest.mark.asyncio
    async def test_pending_export_count(self, mp_rows, ledger_events):
        """Same number of pending-export rows from both sources."""
        mp_pending = [r for r in mp_rows if r["status"] not in ("exported", "imported")]

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_pending = await get_pending_exports("141air")

        assert len(ledger_pending) == len(mp_pending), (
            f"Pending count: mp={len(mp_pending)}, ledger={len(ledger_pending)}"
        )

    @pytest.mark.asyncio
    async def test_pending_export_payment_ids(self, mp_rows, ledger_events):
        """Same payment_ids in pending exports."""
        mp_pids = {str(r["payment_id"]) for r in mp_rows if r["status"] not in ("exported", "imported")}

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_pending = await get_pending_exports("141air")

        ledger_pids = {r["payment_id"] for r in ledger_pending}
        assert ledger_pids == mp_pids, (
            f"Pending PIDs: only_mp={mp_pids - ledger_pids}, only_ledger={ledger_pids - mp_pids}"
        )

    @pytest.mark.asyncio
    async def test_pending_export_amounts(self, mp_rows, ledger_events):
        """Same amounts for each pending export row."""
        mp_amounts = {
            str(r["payment_id"]): float(r["amount"])
            for r in mp_rows if r["status"] not in ("exported", "imported")
        }

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_pending = await get_pending_exports("141air")

        for row in ledger_pending:
            pid = row["payment_id"]
            assert float(row["amount"]) == mp_amounts[pid], (
                f"Export amount mismatch for {pid}"
            )

    @pytest.mark.asyncio
    async def test_pending_export_directions(self, mp_rows, ledger_events):
        """Same expense_direction for each pending export row."""
        mp_dirs = {
            str(r["payment_id"]): r["expense_direction"]
            for r in mp_rows if r["status"] not in ("exported", "imported")
        }

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_pending = await get_pending_exports("141air")

        for row in ledger_pending:
            pid = row["payment_id"]
            assert row["expense_direction"] == mp_dirs[pid]

    @pytest.mark.asyncio
    async def test_pending_export_categories(self, mp_rows, ledger_events):
        """Same ca_category for each pending export row."""
        mp_cats = {
            str(r["payment_id"]): r.get("ca_category")
            for r in mp_rows if r["status"] not in ("exported", "imported")
        }

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_pending = await get_pending_exports("141air")

        for row in ledger_pending:
            pid = row["payment_id"]
            assert row["ca_category"] == mp_cats[pid], (
                f"Export category mismatch for {pid}: mp={mp_cats[pid]}, ledger={row['ca_category']}"
            )

    @pytest.mark.asyncio
    async def test_pending_export_with_status_filter(self, mp_rows, ledger_events):
        """With status_filter, both sources return same subset."""
        filt = ["auto_categorized"]
        mp_pending = [
            r for r in mp_rows
            if r["status"] not in ("exported", "imported") and r["status"] in filt
        ]

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_pending = await get_pending_exports("141air", status_filter=filt)

        assert len(ledger_pending) == len(mp_pending), (
            f"Filtered pending: mp={len(mp_pending)}, ledger={len(ledger_pending)}"
        )

    @pytest.mark.asyncio
    async def test_export_has_all_xlsx_fields(self, ledger_events):
        """Every pending-export row has all fields required by _build_xlsx."""
        required_fields = {
            "amount", "expense_direction", "ca_category", "description",
            "payment_id", "date_approved", "date_created",
            "auto_categorized", "external_reference", "notes",
            "expense_type", "status",
        }

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_pending = await get_pending_exports("141air")

        for row in ledger_pending:
            missing = required_fields - set(row.keys())
            assert not missing, f"Row {row['payment_id']} missing fields: {missing}"


# ===========================================================================
# 4. financial_closing manual lane parity — same day totals
# ===========================================================================

class TestFinancialClosingManualLaneParity:
    """Compare _compute_manual_lane output from mp_expenses vs ledger.

    Both paths call _compute_manual_lane which groups expenses by day and
    computes totals. We verify the day-level aggregation matches.
    """

    def _compute_day_totals_from_mp(self, mp_rows: list[dict]) -> dict[str, dict]:
        """Simulate what _compute_manual_lane produces from mp_expenses rows."""
        from app.services.financial_closing import _to_brt_day, _row_sign
        from collections import defaultdict

        by_day: dict[str, list[dict]] = defaultdict(list)
        for r in mp_rows:
            day = _to_brt_day(r.get("date_approved") or r.get("date_created"))
            by_day[day].append(r)

        result = {}
        for day, rows in sorted(by_day.items()):
            total_signed = round(sum(_row_sign(r) for r in rows), 2)
            total_ids = {int(r["payment_id"]) for r in rows if r.get("payment_id") is not None}
            exported_ids = {
                int(r["payment_id"]) for r in rows
                if r.get("payment_id") is not None and r.get("status") == "exported"
            }
            result[day] = {
                "rows_total": len(rows),
                "amount_total_signed": total_signed,
                "payment_ids_total": len(total_ids),
                "payment_ids_exported": len(exported_ids),
            }
        return result

    @pytest.mark.asyncio
    async def test_same_day_count(self, mp_rows, ledger_events):
        """Both sources group into the same number of days."""
        mp_days = self._compute_day_totals_from_mp(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1_000_000)

        from app.services.financial_closing import _to_brt_day
        from collections import defaultdict
        ledger_by_day: dict[str, list[dict]] = defaultdict(list)
        for r in ledger_rows:
            day = _to_brt_day(r.get("date_approved") or r.get("date_created"))
            ledger_by_day[day].append(r)

        assert set(ledger_by_day.keys()) == set(mp_days.keys()), (
            f"Day keys differ: mp={set(mp_days.keys())}, ledger={set(ledger_by_day.keys())}"
        )

    @pytest.mark.asyncio
    async def test_same_rows_per_day(self, mp_rows, ledger_events):
        """Both sources have same row count per day."""
        mp_days = self._compute_day_totals_from_mp(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1_000_000)

        from app.services.financial_closing import _to_brt_day
        from collections import defaultdict
        ledger_by_day: dict[str, list[dict]] = defaultdict(list)
        for r in ledger_rows:
            day = _to_brt_day(r.get("date_approved") or r.get("date_created"))
            ledger_by_day[day].append(r)

        for day, mp_info in mp_days.items():
            assert len(ledger_by_day[day]) == mp_info["rows_total"], (
                f"Day {day}: mp={mp_info['rows_total']}, ledger={len(ledger_by_day[day])}"
            )

    @pytest.mark.asyncio
    async def test_same_signed_amounts_per_day(self, mp_rows, ledger_events):
        """Both sources have same total signed amount per day."""
        from app.services.financial_closing import _to_brt_day, _row_sign

        mp_days = self._compute_day_totals_from_mp(mp_rows)

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1_000_000)

        from collections import defaultdict
        ledger_by_day: dict[str, list[dict]] = defaultdict(list)
        for r in ledger_rows:
            day = _to_brt_day(r.get("date_approved") or r.get("date_created"))
            ledger_by_day[day].append(r)

        for day, mp_info in mp_days.items():
            ledger_signed = round(sum(_row_sign(r) for r in ledger_by_day[day]), 2)
            assert ledger_signed == mp_info["amount_total_signed"], (
                f"Day {day}: mp_signed={mp_info['amount_total_signed']}, ledger_signed={ledger_signed}"
            )

    @pytest.mark.asyncio
    async def test_same_payment_ids_per_day(self, mp_rows, ledger_events):
        """Both sources have same payment_ids per day."""
        from app.services.financial_closing import _to_brt_day
        from collections import defaultdict

        mp_by_day: dict[str, set[str]] = defaultdict(set)
        for r in mp_rows:
            day = _to_brt_day(r.get("date_approved") or r.get("date_created"))
            mp_by_day[day].add(str(r["payment_id"]))

        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_rows = await get_expense_list("141air", limit=1_000_000)

        ledger_by_day: dict[str, set[str]] = defaultdict(set)
        for r in ledger_rows:
            day = _to_brt_day(r.get("date_approved") or r.get("date_created"))
            ledger_by_day[day].add(r["payment_id"])

        for day in mp_by_day:
            assert ledger_by_day[day] == mp_by_day[day], (
                f"Day {day}: pid diff={mp_by_day[day].symmetric_difference(ledger_by_day[day])}"
            )


# ===========================================================================
# 5. DRE unchanged — event ledger DRE helpers are unaffected by expense reads
# ===========================================================================

class TestDREUnchanged:
    """Verify that DRE-related event ledger functions (get_dre_summary, get_cash_summary)
    are NOT affected by expense_* events — they filter them out.

    This confirms the read migration doesn't regress the DRE.
    """

    @pytest.mark.asyncio
    async def test_dre_summary_excludes_expense_events(self):
        """get_dre_summary skips expense_* and cash_* events."""
        from app.services.event_ledger import get_dre_summary

        # Mix of payment events and expense events
        mixed_rows = [
            {"event_type": "sale_approved", "signed_amount": 100.0},
            {"event_type": "fee_charged", "signed_amount": -10.0},
            {"event_type": "expense_captured", "signed_amount": -50.0},
            {"event_type": "expense_classified", "signed_amount": 0},
            {"event_type": "cash_release", "signed_amount": 90.0},
        ]

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.gte.return_value.lte.return_value.range.return_value.execute.return_value.data = mixed_rows

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            summary = await get_dre_summary("141air", "2026-01-01", "2026-01-31")

        # Only payment events should be included (sale_approved, fee_charged)
        assert "sale_approved" in summary
        assert "fee_charged" in summary
        assert "expense_captured" not in summary
        assert "expense_classified" not in summary
        assert "cash_release" not in summary

    @pytest.mark.asyncio
    async def test_cash_summary_only_cash_events(self):
        """get_cash_summary only includes cash_* events."""
        from app.services.event_ledger import get_cash_summary

        mixed_rows = [
            {"event_type": "cash_release", "signed_amount": 90.0},
            {"event_type": "cash_expense", "signed_amount": -30.0},
            {"event_type": "sale_approved", "signed_amount": 100.0},
            {"event_type": "expense_captured", "signed_amount": -50.0},
        ]

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.gte.return_value.lte.return_value.range.return_value.execute.return_value.data = mixed_rows

        with patch("app.services.event_ledger.get_db", return_value=mock_db):
            summary = await get_cash_summary("141air", "2026-01-01", "2026-01-31")

        assert "cash_release" in summary
        assert "cash_expense" in summary
        assert "sale_approved" not in summary
        assert "expense_captured" not in summary


# ===========================================================================
# 6. Zero differences summary — comprehensive cross-check
# ===========================================================================

class TestZeroDifferences:
    """End-to-end: feed identical data through both paths and verify zero unexplained differences."""

    @pytest.mark.asyncio
    async def test_comprehensive_parity_check(self, mp_rows, ledger_events):
        """Single test that checks all dimensions at once and reports any differences."""
        differences: list[str] = []

        # 1. list_expenses
        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_list = await get_expense_list("141air", limit=1000)

        if len(ledger_list) != len(mp_rows):
            differences.append(f"list count: mp={len(mp_rows)}, ledger={len(ledger_list)}")

        mp_by_pid = {str(r["payment_id"]): r for r in mp_rows}
        ledger_by_pid = {r["payment_id"]: r for r in ledger_list}

        for pid in mp_by_pid:
            if pid not in ledger_by_pid:
                differences.append(f"payment {pid}: missing in ledger")
                continue
            mp_r = mp_by_pid[pid]
            lg_r = ledger_by_pid[pid]
            for field in ("amount", "expense_type", "expense_direction", "ca_category",
                          "description", "auto_categorized", "external_reference"):
                mp_val = mp_r.get(field)
                lg_val = lg_r.get(field)
                if mp_val != lg_val:
                    differences.append(f"payment {pid}.{field}: mp={mp_val}, ledger={lg_val}")

        # 2. stats
        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_stats = await get_expense_stats("141air")

        mp_total = len(mp_rows)
        mp_amount = round(sum(float(r.get("amount") or 0) for r in mp_rows), 2)
        if ledger_stats["total"] != mp_total:
            differences.append(f"stats.total: mp={mp_total}, ledger={ledger_stats['total']}")
        if ledger_stats["total_amount"] != mp_amount:
            differences.append(f"stats.total_amount: mp={mp_amount}, ledger={ledger_stats['total_amount']}")

        # 3. pending exports
        mp_pending = [r for r in mp_rows if r["status"] not in ("exported", "imported")]
        with patch("app.services.event_ledger._fetch_expense_events",
                    new_callable=AsyncMock, return_value=ledger_events):
            ledger_pending = await get_pending_exports("141air")

        if len(ledger_pending) != len(mp_pending):
            differences.append(
                f"pending_exports count: mp={len(mp_pending)}, ledger={len(ledger_pending)}"
            )

        # FINAL ASSERTION: zero differences
        assert differences == [], (
            f"Parity check found {len(differences)} difference(s):\n" +
            "\n".join(f"  - {d}" for d in differences)
        )

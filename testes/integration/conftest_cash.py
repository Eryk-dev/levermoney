"""T002 - Session-scoped fixtures for Daily Cash Reconciliation tests.

These fixtures fetch LIVE data from the ML API and parse real extrato CSVs.
They are session-scoped so the expensive API calls are made only once per
pytest run.

Usage in test files:
    pytest_plugins = ["testes.integration.conftest_cash"]
"""
from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

import pytest

# Ensure project root is on sys.path when running from any working directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import ml_api  # noqa: E402
from testes.helpers.extrato_parser import parse_extrato_csv  # noqa: E402

EXTRATOS_DIR = PROJECT_ROOT / "testes" / "data" / "extratos"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Remove accents for matching (e.g. 'Liberação' -> 'liberacao')."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _fetch_payments(seller_slug: str, begin_date: str, end_date: str) -> list[dict]:
    """Fetch all payments for a date range, handling ML API pagination."""

    async def _collect() -> list[dict]:
        all_payments: list[dict] = []
        offset = 0
        limit = 50

        while True:
            response = await ml_api.search_payments(
                seller_slug,
                begin_date,
                end_date,
                offset=offset,
                limit=limit,
            )
            # search_payments returns a dict with "results" key
            results: list[dict] = response.get("results") or []
            all_payments.extend(results)

            if len(results) < limit:
                break
            offset += limit

        return all_payments

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_collect())
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# ML payment fixtures (live API)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def ml_payments_jan() -> list[dict]:
    """All 141air payments approved in January 2026 (live ML API)."""
    return _fetch_payments("141air", "2026-01-01T00:00:00.000-03:00", "2026-01-31T23:59:59.999-03:00")


@pytest.fixture(scope="session")
def ml_payments_feb() -> list[dict]:
    """All 141air payments approved in February 2026 (live ML API)."""
    return _fetch_payments("141air", "2026-02-01T00:00:00.000-03:00", "2026-02-28T23:59:59.999-03:00")


# ---------------------------------------------------------------------------
# Extrato fixtures (CSV)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def extrato_jan() -> list[dict]:
    """Parsed extrato for 141air January 2026."""
    return parse_extrato_csv(EXTRATOS_DIR / "extrato janeiro 141Air.csv")


@pytest.fixture(scope="session")
def extrato_feb() -> list[dict]:
    """Parsed extrato for 141air February 2026."""
    return parse_extrato_csv(EXTRATOS_DIR / "extrato fevereiro 141Air.csv")


# ---------------------------------------------------------------------------
# Lookup helpers -- January
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def payments_by_id_jan(ml_payments_jan: list[dict]) -> dict[int, dict]:
    """Map payment_id (int) -> payment dict for January."""
    return {p["id"]: p for p in ml_payments_jan if p.get("id") is not None}


@pytest.fixture(scope="session")
def extrato_by_ref_id_jan(extrato_jan: list[dict]) -> dict[str, list[dict]]:
    """Map reference_id (str) -> list of extrato lines for January."""
    result: dict[str, list[dict]] = defaultdict(list)
    for line in extrato_jan:
        result[line["reference_id"]].append(line)
    return dict(result)


@pytest.fixture(scope="session")
def extrato_liberacoes_jan(extrato_jan: list[dict]) -> list[dict]:
    """Extrato lines for January where transaction_type contains 'liberac' (releases)."""
    return [
        line
        for line in extrato_jan
        if "liberac" in _normalize(line["transaction_type"])
    ]


@pytest.fixture(scope="session")
def extrato_by_date_jan(extrato_jan: list[dict]) -> dict[str, list[dict]]:
    """Map date (DD-MM-YYYY) -> list of extrato lines for January."""
    result: dict[str, list[dict]] = defaultdict(list)
    for line in extrato_jan:
        result[line["date"]].append(line)
    return dict(result)


# ---------------------------------------------------------------------------
# Lookup helpers -- February
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def payments_by_id_feb(ml_payments_feb: list[dict]) -> dict[int, dict]:
    """Map payment_id (int) -> payment dict for February."""
    return {p["id"]: p for p in ml_payments_feb if p.get("id") is not None}


@pytest.fixture(scope="session")
def extrato_by_ref_id_feb(extrato_feb: list[dict]) -> dict[str, list[dict]]:
    """Map reference_id (str) -> list of extrato lines for February."""
    result: dict[str, list[dict]] = defaultdict(list)
    for line in extrato_feb:
        result[line["reference_id"]].append(line)
    return dict(result)


@pytest.fixture(scope="session")
def extrato_liberacoes_feb(extrato_feb: list[dict]) -> list[dict]:
    """Extrato lines for February where transaction_type contains 'liberac' (releases)."""
    return [
        line
        for line in extrato_feb
        if "liberac" in _normalize(line["transaction_type"])
    ]


@pytest.fixture(scope="session")
def extrato_by_date_feb(extrato_feb: list[dict]) -> dict[str, list[dict]]:
    """Map date (DD-MM-YYYY) -> list of extrato lines for February."""
    result: dict[str, list[dict]] = defaultdict(list)
    for line in extrato_feb:
        result[line["date"]].append(line)
    return dict(result)

"""
Unit tests for the multi-file extrato upload endpoint (US-003).

Tests the endpoint logic: file reading, coverage validation, month detection,
ingestion orchestration, seller flag updates, and GDrive background upload.

All DB and service calls are mocked — no external dependencies.

Run: python3 -m pytest testes/test_extrato_multi_upload.py -v
"""
import asyncio
import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile

from app.routers.admin.extrato import upload_extrato_multi, _decode_csv_bytes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv(dates: list[str]) -> str:
    """Build a minimal extrato CSV with transactions on given dates (YYYY-MM-DD)."""
    lines = [
        "INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE",
        "100,00;200,00;-50,00;250,00",
        "",
        "RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE",
    ]
    for d in dates:
        parts = d.split("-")
        csv_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
        lines.append(f"{csv_date};Liberação de dinheiro;123456;100,00;200,00")
    return "\n".join(lines)


def _make_upload_file(csv_text: str, filename: str = "extrato.csv") -> MagicMock:
    """Create a mock UploadFile that returns csv_text bytes on read()."""
    raw = csv_text.encode("utf-8")
    mock = MagicMock(spec=UploadFile)
    mock.filename = filename
    mock.read = AsyncMock(return_value=raw)
    return mock


# Seller fixtures
_SELLER_CA = {
    "slug": "testco",
    "name": "TestCo",
    "dashboard_empresa": "TestCo Ltda",
    "integration_mode": "dashboard_ca",
    "ca_start_date": "2026-01-01",
    "active": True,
}

_SELLER_NO_CA = {
    "slug": "testco",
    "name": "TestCo",
    "integration_mode": "dashboard_only",
    "ca_start_date": None,
    "active": True,
}


# ---------------------------------------------------------------------------
# Tests: _decode_csv_bytes
# ---------------------------------------------------------------------------

class TestDecodeCsvBytes:
    def test_utf8_sig(self):
        text = "INITIAL_BALANCE;x"
        raw = b"\xef\xbb\xbf" + text.encode("utf-8")
        assert _decode_csv_bytes(raw) == text

    def test_latin1_fallback(self):
        # Latin-1 bytes that are NOT valid UTF-8
        raw = b"\xc0\xe7\xe3o"  # "Àção" in latin-1
        result = _decode_csv_bytes(raw)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests: upload_extrato_multi
# ---------------------------------------------------------------------------

class TestUploadExtratoMulti:
    """Test the multi-file extrato upload endpoint."""

    @pytest.fixture(autouse=True)
    def _patch_deps(self, monkeypatch):
        """Patch DB, services, and asyncio for all tests."""
        # Mock DB
        self.mock_table = MagicMock()
        self.mock_db = MagicMock()
        self.mock_db.table.return_value = self.mock_table

        # Chain pattern: table().upsert().execute() / table().update().eq().execute()
        self.mock_table.upsert.return_value = self.mock_table
        self.mock_table.update.return_value = self.mock_table
        self.mock_table.eq.return_value = self.mock_table
        self.mock_table.execute.return_value = MagicMock(data=[{"id": 42}])

        monkeypatch.setattr("app.routers.admin.extrato.get_db", lambda: self.mock_db)

        # Mock get_seller_config
        self._seller = _SELLER_CA.copy()
        monkeypatch.setattr(
            "app.routers.admin.extrato.get_seller_config",
            lambda db, slug: self._seller,
        )

        # Mock ingest_extrato_from_csv
        self.ingest_mock = AsyncMock(return_value={
            "seller": "testco",
            "total_lines": 5,
            "newly_ingested": 3,
            "skipped_internal": 1,
            "already_covered": 1,
            "errors": 0,
            "by_type": {"liberacao_nao_sync": 3},
            "summary": {"initial_balance": 100.0, "final_balance": 250.0},
        })
        monkeypatch.setattr(
            "app.routers.admin.extrato.ingest_extrato_from_csv",
            self.ingest_mock,
        )

        # Mock asyncio.create_task to avoid actual background tasks
        monkeypatch.setattr(
            "app.routers.admin.extrato.asyncio.create_task",
            lambda coro: coro.close() if asyncio.iscoroutine(coro) else None,
        )

    @pytest.mark.asyncio
    async def test_seller_not_found(self, monkeypatch):
        monkeypatch.setattr(
            "app.routers.admin.extrato.get_seller_config",
            lambda db, slug: None,
        )
        csv = _make_csv(["2026-01-15", "2026-02-15", "2026-03-15"])
        files = [_make_upload_file(csv)]

        with pytest.raises(Exception) as exc_info:
            await upload_extrato_multi(slug="unknown", files=files)
        assert "not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_wrong_integration_mode(self, monkeypatch):
        self._seller["integration_mode"] = "dashboard_only"
        csv = _make_csv(["2026-01-15"])
        files = [_make_upload_file(csv)]

        with pytest.raises(Exception) as exc_info:
            await upload_extrato_multi(slug="testco", files=files)
        assert "dashboard_ca" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_no_ca_start_date(self, monkeypatch):
        self._seller["ca_start_date"] = None
        csv = _make_csv(["2026-01-15"])
        files = [_make_upload_file(csv)]

        with pytest.raises(Exception) as exc_info:
            await upload_extrato_multi(slug="testco", files=files)
        assert "ca_start_date" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_file_too_large(self, monkeypatch):
        big_bytes = b"x" * (6 * 1024 * 1024)  # 6MB
        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "big.csv"
        mock_file.read = AsyncMock(return_value=big_bytes)

        with pytest.raises(Exception) as exc_info:
            await upload_extrato_multi(slug="testco", files=[mock_file])
        assert "too large" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_coverage_validation_fails(self, monkeypatch):
        # CSV covers only January, but ca_start_date is Jan 1 and yesterday
        # would require more months
        self._seller["ca_start_date"] = "2025-01-01"
        csv = _make_csv(["2025-01-15"])
        files = [_make_upload_file(csv)]

        with pytest.raises(Exception) as exc_info:
            await upload_extrato_multi(slug="testco", files=files)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_successful_multi_file_upload(self, monkeypatch):
        """Successful upload of multiple CSVs covering full period."""
        # Use ca_start_date of Mar 1 and CSV covers March
        # (so yesterday=Mar 15 is covered)
        self._seller["ca_start_date"] = "2026-03-01"
        csv = _make_csv(["2026-03-01", "2026-03-10", "2026-03-15"])
        files = [_make_upload_file(csv, "march.csv")]

        result = await upload_extrato_multi(slug="testco", files=files)

        assert result["seller_slug"] == "testco"
        assert result["total_files"] == 1
        assert "2026-03" in result["months_processed"]
        assert result["total_errors"] == 0
        assert len(result["results"]) > 0

        # Verify ingest was called for March
        self.ingest_mock.assert_called()
        call_args = self.ingest_mock.call_args_list
        months_called = [c.args[2] for c in call_args]
        assert "2026-03" in months_called

    @pytest.mark.asyncio
    async def test_seller_flags_updated(self, monkeypatch):
        """Seller extrato_missing=False and extrato_uploaded_at set on success."""
        self._seller["ca_start_date"] = "2026-03-01"
        csv = _make_csv(["2026-03-01", "2026-03-15"])
        files = [_make_upload_file(csv)]

        await upload_extrato_multi(slug="testco", files=files)

        # Verify sellers table was updated
        self.mock_db.table.assert_any_call("sellers")
        # Check that update was called with extrato_missing=False
        update_calls = [
            c for c in self.mock_table.update.call_args_list
            if "extrato_missing" in (c.args[0] if c.args else c.kwargs.get("data", {}))
        ]
        assert len(update_calls) >= 1
        update_payload = update_calls[0].args[0]
        assert update_payload["extrato_missing"] is False
        assert "extrato_uploaded_at" in update_payload

    @pytest.mark.asyncio
    async def test_extrato_uploads_upserted(self, monkeypatch):
        """Each month creates an extrato_uploads record."""
        self._seller["ca_start_date"] = "2026-03-01"
        csv = _make_csv(["2026-03-01", "2026-03-15"])
        files = [_make_upload_file(csv)]

        await upload_extrato_multi(slug="testco", files=files)

        # Verify extrato_uploads table was written
        self.mock_db.table.assert_any_call("extrato_uploads")
        upsert_calls = self.mock_table.upsert.call_args_list
        assert len(upsert_calls) >= 1
        row = upsert_calls[0].args[0]
        assert row["seller_slug"] == "testco"
        assert row["status"] == "processing"

    @pytest.mark.asyncio
    async def test_multiple_csvs_multiple_months(self, monkeypatch):
        """Two CSVs covering different months both get ingested."""
        self._seller["ca_start_date"] = "2026-02-01"
        csv_feb = _make_csv(["2026-02-05", "2026-02-20"])
        csv_mar = _make_csv(["2026-03-01", "2026-03-15"])
        files = [
            _make_upload_file(csv_feb, "feb.csv"),
            _make_upload_file(csv_mar, "mar.csv"),
        ]

        result = await upload_extrato_multi(slug="testco", files=files)

        assert result["total_files"] == 2
        assert "2026-02" in result["months_processed"]
        assert "2026-03" in result["months_processed"]
        assert self.ingest_mock.call_count >= 2

    @pytest.mark.asyncio
    async def test_gdrive_status_queued(self, monkeypatch):
        """GDrive status is 'queued' when upload is attempted."""
        self._seller["ca_start_date"] = "2026-03-01"
        csv = _make_csv(["2026-03-01", "2026-03-15"])
        files = [_make_upload_file(csv)]

        result = await upload_extrato_multi(slug="testco", files=files)

        assert result["gdrive_status"] == "queued"

    @pytest.mark.asyncio
    async def test_ingestion_error_continues(self, monkeypatch):
        """If one month fails ingestion, other months still proceed."""
        self._seller["ca_start_date"] = "2026-02-01"
        csv_feb = _make_csv(["2026-02-05", "2026-02-20"])
        csv_mar = _make_csv(["2026-03-01", "2026-03-15"])
        files = [
            _make_upload_file(csv_feb, "feb.csv"),
            _make_upload_file(csv_mar, "mar.csv"),
        ]

        # First call fails, second succeeds
        self.ingest_mock.side_effect = [
            Exception("DB connection error"),
            {
                "seller": "testco", "total_lines": 5, "newly_ingested": 3,
                "skipped_internal": 1, "already_covered": 1, "errors": 0,
                "by_type": {}, "summary": {},
            },
        ]

        result = await upload_extrato_multi(slug="testco", files=files)

        assert result["total_errors"] == 1
        assert len(result["results"]) == 2
        statuses = {r["month"]: r["status"] for r in result["results"]}
        assert statuses["2026-02"] == "failed"
        assert statuses["2026-03"] == "completed"

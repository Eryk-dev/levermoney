"""BRT date conversion edge-case tests."""
import pytest
from app.services.processor import _to_brt_date


class TestBrtConversion:
    def test_late_night_utc4_crosses_midnight(self):
        """23:00 UTC-4 = 00:00 BRT next day."""
        assert _to_brt_date("2026-01-20T23:00:00.000-04:00") == "2026-01-21"
        # Prove the bug: truncation gives wrong date
        assert "2026-01-20T23:00:00.000-04:00"[:10] == "2026-01-20"

    def test_early_morning_utc4_same_day(self):
        """10:00 UTC-4 = 11:00 BRT same day."""
        assert _to_brt_date("2026-01-20T10:00:00.000-04:00") == "2026-01-20"

    def test_midnight_boundary(self):
        """22:00 UTC-4 = 23:00 BRT same day (just before crossing)."""
        assert _to_brt_date("2026-01-20T22:00:00.000-04:00") == "2026-01-20"

    def test_fallback_on_bad_input(self):
        """Bad input returns truncated string."""
        assert _to_brt_date("2026-01-20") == "2026-01-20"

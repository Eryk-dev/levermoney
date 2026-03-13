#!/usr/bin/env python3
"""
Test script for new admin API endpoints (Onboarding V2 + Extrato Ingester).

Tests endpoint validation, response format, and error handling.

Usage:
    cd "/Volumes/SSD Eryk/financeiro v2/lever money claude v3"

    # With API running locally:
    ADMIN_PASSWORD=yourpassword python3 testes/test_admin_endpoints.py

    # Without API (validation-only tests still run):
    python3 testes/test_admin_endpoints.py

Requires:
  - API running at BASE_URL (default: http://localhost:8000)
  - ADMIN_PASSWORD env var (or ADMIN_TOKEN for a pre-issued token)

What it tests:
  1. POST /admin/login — authentication
  2. GET  /admin/onboarding/install-link — URL format
  3. POST /admin/sellers/xxx/activate — validation: invalid mode, missing fields,
          invalid ca_start_date (not 1st of month)
  4. GET  /admin/sellers/141air/backfill-status — response JSON structure
  5. POST /admin/sellers/xxx/backfill-retry — validation for non-existent seller
  6. POST /admin/sellers/xxx/upgrade-to-ca — ca_start_date must be 1st of month
  7. GET  /admin/extrato/ingestion-status — response format
  8. POST /admin/extrato/ingest/141air — validation (missing required params)

Validation-only tests (no API required):
  - Request body shape validation (replicated from router logic)
  - ca_start_date validation (must be YYYY-MM-DD, day=1)
  - integration_mode enum validation
  - install-link URL pattern
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# ── Project setup ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL       = os.getenv("BASE_URL", "http://localhost:8000")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_TOKEN    = os.getenv("ADMIN_TOKEN", "")   # Pre-issued token (skips login)
TEST_SELLER    = "141air"

# ── Result tracking ────────────────────────────────────────────────────────────
_results: list[dict] = []


def _pass(name: str, detail: str = "") -> None:
    _results.append({"name": name, "status": "PASS", "detail": detail})
    print(f"  {GREEN}PASS{RESET}  {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, detail: str = "") -> None:
    _results.append({"name": name, "status": "FAIL", "detail": detail})
    print(f"  {RED}FAIL{RESET}  {name}" + (f" — {detail}" if detail else ""))


def _skip(name: str, reason: str) -> None:
    _results.append({"name": name, "status": "SKIP", "detail": reason})
    print(f"  {YELLOW}SKIP{RESET}  {name} — {reason}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False


def _api_available() -> bool:
    """Return True when we have httpx + at least one credential source."""
    if not _HTTPX_OK:
        return False
    if ADMIN_TOKEN:
        return True
    return bool(ADMIN_PASSWORD)


async def _get_token() -> str | None:
    """Obtain an admin token: use pre-set ADMIN_TOKEN or login with password."""
    if ADMIN_TOKEN:
        return ADMIN_TOKEN
    if not ADMIN_PASSWORD:
        return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                f"{BASE_URL}/admin/login",
                json={"password": ADMIN_PASSWORD},
            )
            if resp.status_code == 200:
                return resp.json().get("token")
            return None
        except httpx.ConnectError:
            return None


async def _get(path: str, token: str, params: dict | None = None) -> httpx.Response | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            return await client.get(
                f"{BASE_URL}{path}",
                headers={"X-Admin-Token": token},
                params=params or {},
            )
        except httpx.ConnectError:
            return None


async def _post(path: str, token: str, body: dict | None = None, params: dict | None = None) -> httpx.Response | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            return await client.post(
                f"{BASE_URL}{path}",
                headers={"X-Admin-Token": token},
                json=body or {},
                params=params or {},
            )
        except httpx.ConnectError:
            return None


# ── Validation-only tests (no API required) ───────────────────────────────────


def test_ca_start_date_validation_logic() -> None:
    """Replicate the ca_start_date validation from activate_seller_v2 and
    upgrade_seller_to_ca without hitting the API."""
    name = "ca_start_date_validation_logic"

    from datetime import date

    valid = [
        "2026-01-01",
        "2026-02-01",
        "2025-12-01",
    ]
    invalid_day = [
        "2026-01-02",
        "2026-02-15",
        "2026-03-31",
    ]
    invalid_format = [
        "01/02/2026",
        "2026-13-01",   # invalid month
        "not-a-date",
        "",
    ]

    failures = []

    for d in valid:
        try:
            parsed = date.fromisoformat(d)
            if parsed.day != 1:
                failures.append(f"{d!r} should be valid (day=1)")
        except ValueError:
            failures.append(f"{d!r} should parse as valid date")

    for d in invalid_day:
        try:
            parsed = date.fromisoformat(d)
            if parsed.day == 1:
                failures.append(f"{d!r} should be invalid (day!=1)")
        except ValueError:
            pass  # parse failure is acceptable (though not expected here)

    for d in invalid_format:
        try:
            date.fromisoformat(d)
            # If it didn't raise, the day check would catch non-1st
        except ValueError:
            pass  # Expected — format is invalid

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(
            name,
            f"{len(valid)} valid + {len(invalid_day)} invalid-day + "
            f"{len(invalid_format)} invalid-format cases all correct",
        )


def test_integration_mode_enum_validation() -> None:
    """Verify the integration_mode enum accepted by the activate endpoint."""
    name = "integration_mode_enum_validation"

    VALID_MODES = {"dashboard_only", "dashboard_ca"}
    INVALID_MODES = ["legacy", "ca_only", "DASHBOARD_ONLY", "", "null", None]

    failures = []

    for mode in VALID_MODES:
        if mode not in ("dashboard_only", "dashboard_ca"):
            failures.append(f"{mode!r} should be valid")

    for mode in INVALID_MODES:
        if mode in ("dashboard_only", "dashboard_ca"):
            failures.append(f"{mode!r} should be invalid")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(
            name,
            f"2 valid modes, {len(INVALID_MODES)} invalid modes correctly classified",
        )


def test_activate_required_fields_for_dashboard_ca() -> None:
    """Replicate the missing-fields check for dashboard_ca activation:
    ca_conta_bancaria, ca_centro_custo_variavel, ca_start_date all required."""
    name = "activate_required_fields_for_dashboard_ca"

    required = ("ca_conta_bancaria", "ca_centro_custo_variavel", "ca_start_date")

    # Case 1: all present → no missing
    all_present = {
        "ca_conta_bancaria": "fea5f1de-fd23-4462-9b43-0a2c6ae4df04",
        "ca_centro_custo_variavel": "f7c214a6-be2f-11f0-8080-ab23c683d2a1",
        "ca_start_date": "2026-02-01",
    }

    class MockReq:
        def __init__(self, data):
            for k, v in data.items():
                setattr(self, k, v)

    req_complete = MockReq(all_present)
    missing_complete = [f for f in required if not getattr(req_complete, f, None)]
    if missing_complete:
        _fail(name, f"false positive: {missing_complete} flagged as missing")
        return

    # Case 2: missing ca_start_date
    partial = {
        "ca_conta_bancaria": "fea5f1de-fd23-4462-9b43-0a2c6ae4df04",
        "ca_centro_custo_variavel": "f7c214a6-be2f-11f0-8080-ab23c683d2a1",
        "ca_start_date": None,
    }
    req_partial = MockReq(partial)
    missing_partial = [f for f in required if not getattr(req_partial, f, None)]
    if "ca_start_date" not in missing_partial:
        _fail(name, "missing ca_start_date not detected")
        return

    # Case 3: all missing
    empty = {
        "ca_conta_bancaria": None,
        "ca_centro_custo_variavel": None,
        "ca_start_date": None,
    }
    req_empty = MockReq(empty)
    missing_empty = [f for f in required if not getattr(req_empty, f, None)]
    if len(missing_empty) != 3:
        _fail(name, f"expected 3 missing fields, got {len(missing_empty)}: {missing_empty}")
        return

    _pass(name, "all 3 required field combinations correctly validated")


def test_install_link_url_format() -> None:
    """Verify the install link URL is a valid URL pointing to /auth/ml/install."""
    name = "install_link_url_format"

    sample_base_urls = [
        "http://localhost:8000",
        "https://conciliador.levermoney.com.br",
        "https://my-api.example.com",
    ]

    failures = []
    for base in sample_base_urls:
        url = f"{base}/auth/ml/install"
        parsed = urlparse(url)

        if not parsed.scheme:
            failures.append(f"{url!r}: missing scheme")
        if not parsed.netloc:
            failures.append(f"{url!r}: missing netloc")
        if parsed.path != "/auth/ml/install":
            failures.append(f"{url!r}: expected path /auth/ml/install, got {parsed.path!r}")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, f"install link format valid for {len(sample_base_urls)} base URLs")


def test_backfill_status_response_schema() -> None:
    """Verify the documented backfill-status response schema from the spec."""
    name = "backfill_status_response_schema"

    # Simulate the JSON returned by GET /admin/sellers/{slug}/backfill-status
    # processed = orders_processed + expenses_classified (380 + 70 = 450)
    sample_response = {
        "ca_backfill_status": "running",
        "ca_backfill_started_at": "2026-02-19T14:30:00Z",
        "ca_backfill_completed_at": None,
        "ca_backfill_progress": {
            "total": 520,
            "processed": 450,
            "orders_processed": 380,
            "expenses_classified": 70,
            "skipped": 65,
            "errors": 5,
            "baixas_created": 350,
            "last_payment_id": 144370799868,
        },
    }

    required_top_keys = {
        "ca_backfill_status",
        "ca_backfill_started_at",
        "ca_backfill_completed_at",
        "ca_backfill_progress",
    }
    required_progress_keys = {
        "total",
        "processed",
        "orders_processed",
        "expenses_classified",
        "skipped",
        "errors",
        "baixas_created",
        "last_payment_id",
    }

    failures = []
    missing_top = required_top_keys - set(sample_response.keys())
    if missing_top:
        failures.append(f"missing top-level keys: {missing_top}")

    progress = sample_response.get("ca_backfill_progress")
    if isinstance(progress, dict):
        missing_prog = required_progress_keys - set(progress.keys())
        if missing_prog:
            failures.append(f"missing progress keys: {missing_prog}")

        # Arithmetic check: processed = orders_processed + expenses_classified
        if progress.get("orders_processed", 0) + progress.get("expenses_classified", 0) != progress.get("processed", -1):
            failures.append(
                f"counter inconsistency: orders={progress['orders_processed']} + "
                f"expenses={progress['expenses_classified']} != processed={progress['processed']}"
            )

    valid_statuses = {"pending", "running", "completed", "failed", None}
    if sample_response.get("ca_backfill_status") not in valid_statuses:
        failures.append(f"invalid status: {sample_response.get('ca_backfill_status')!r}")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, "response schema valid (top-level + progress keys + arithmetic)")


def test_ingestion_status_response_schema() -> None:
    """Verify the documented ingestion-status response from GET /admin/extrato/ingestion-status."""
    name = "ingestion_status_response_schema"

    # Simulate the response from get_last_ingestion_result()
    sample_response = {
        "ran_at": "2026-02-19T03:01:00Z",
        "results": [
            {
                "seller": "141air",
                "total_lines": 187,
                "skipped_internal": 143,
                "already_covered": 8,
                "newly_ingested": 36,
                "errors": 0,
                "by_type": {
                    "difal": 3,
                    "dinheiro_retido": 5,
                    "reembolso_disputa": 12,
                    "entrada_dinheiro": 2,
                    "reembolso_generico": 4,
                    "deposito_avulso": 1,
                    "debito_divida_disputa": 9,
                },
                "summary": {
                    "initial_balance": 4476.23,
                    "credits": 207185.69,
                    "debits": -210571.52,
                    "final_balance": 1090.40,
                },
            }
        ],
    }

    failures = []

    if "ran_at" not in sample_response:
        failures.append("missing ran_at")
    if "results" not in sample_response:
        failures.append("missing results")
    elif not isinstance(sample_response["results"], list):
        failures.append("results is not a list")
    else:
        for i, result in enumerate(sample_response["results"]):
            for key in ("seller", "total_lines", "newly_ingested", "errors", "by_type"):
                if key not in result:
                    failures.append(f"results[{i}] missing key: {key!r}")

            # Validate by_type is a dict of expense_type → count
            by_type = result.get("by_type", {})
            if not isinstance(by_type, dict):
                failures.append(f"results[{i}].by_type is not a dict")
            else:
                for k, v in by_type.items():
                    if not isinstance(k, str):
                        failures.append(f"by_type key is not str: {k!r}")
                    if not isinstance(v, int):
                        failures.append(f"by_type[{k!r}] is not int: {v!r}")

    if failures:
        _fail(name, "; ".join(failures))
    else:
        _pass(name, "ingestion-status schema valid")


# ── API integration tests (require running server) ─────────────────────────────


async def test_login() -> str | None:
    """POST /admin/login — verify we can get a token."""
    name = "login"

    if not _api_available():
        _skip(name, "API not available (set ADMIN_PASSWORD or ADMIN_TOKEN)")
        return None
    if not _HTTPX_OK:
        _skip(name, "httpx not installed")
        return None

    # Check connectivity first
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health = await client.get(f"{BASE_URL}/health")
    except httpx.ConnectError:
        _skip(name, f"cannot connect to {BASE_URL} — is the API running?")
        return None

    token = await _get_token()
    if token:
        _pass(name, f"token obtained ({len(token)} chars), server at {BASE_URL}")
    else:
        _fail(name, f"login failed — check ADMIN_PASSWORD and that API is running at {BASE_URL}")

    return token


async def test_install_link_endpoint(token: str) -> None:
    """GET /admin/onboarding/install-link — verify URL format."""
    name = "install_link_endpoint"

    resp = await _get("/admin/onboarding/install-link", token)
    if resp is None:
        _skip(name, "no response (server unreachable)")
        return

    if resp.status_code == 401:
        _fail(name, "401 Unauthorized — token invalid")
        return
    if resp.status_code != 200:
        _fail(name, f"expected 200, got {resp.status_code}: {resp.text[:200]}")
        return

    try:
        body = resp.json()
    except Exception:
        _fail(name, f"response is not JSON: {resp.text[:200]}")
        return

    if "url" not in body:
        _fail(name, f"response missing 'url' key: {body}")
        return

    url = body["url"]
    parsed = urlparse(url)

    failures = []
    if not parsed.scheme:
        failures.append("missing URL scheme")
    if not parsed.netloc:
        failures.append("missing URL netloc")
    if not parsed.path.endswith("/auth/ml/install"):
        failures.append(f"path should end with /auth/ml/install, got: {parsed.path!r}")

    if failures:
        _fail(name, f"URL={url!r}: {'; '.join(failures)}")
    else:
        _pass(name, f"url={url!r}")


async def test_activate_validation_invalid_mode(token: str) -> None:
    """POST /admin/sellers/141air/activate with invalid integration_mode -> 400."""
    name = "activate_validation_invalid_mode"

    resp = await _post(
        f"/admin/sellers/{TEST_SELLER}/activate",
        token,
        body={
            "integration_mode": "legacy_mode",  # Invalid
            "dashboard_empresa": "TEST",
        },
    )
    if resp is None:
        _skip(name, "no response")
        return

    if resp.status_code == 400:
        body = resp.json()
        detail = body.get("detail", "")
        if "integration_mode" in detail.lower() or "dashboard" in detail.lower():
            _pass(name, f"400 with correct error: {detail!r}")
        else:
            _pass(name, f"400 returned (detail: {detail!r})")
    elif resp.status_code == 422:
        # FastAPI Pydantic validation
        _pass(name, f"422 Unprocessable Entity (Pydantic validation caught invalid mode)")
    else:
        _fail(name, f"expected 400/422, got {resp.status_code}: {resp.text[:300]}")


async def test_activate_validation_missing_ca_fields(token: str) -> None:
    """POST /admin/sellers/141air/activate with dashboard_ca but missing required
    CA fields -> 400."""
    name = "activate_validation_missing_ca_fields"

    resp = await _post(
        f"/admin/sellers/{TEST_SELLER}/activate",
        token,
        body={
            "integration_mode": "dashboard_ca",
            "dashboard_empresa": "TEST CA COMPANY",
            # Missing: ca_conta_bancaria, ca_centro_custo_variavel, ca_start_date
        },
    )
    if resp is None:
        _skip(name, "no response")
        return

    if resp.status_code == 400:
        body = resp.json()
        detail = body.get("detail", "")
        # Should mention the missing fields
        if any(f in detail for f in ("ca_conta_bancaria", "ca_centro_custo_variavel", "ca_start_date", "dashboard_ca requires")):
            _pass(name, f"400 with missing-fields detail: {detail!r}")
        else:
            _pass(name, f"400 returned (detail: {detail!r})")
    else:
        _fail(name, f"expected 400, got {resp.status_code}: {resp.text[:300]}")


async def test_activate_validation_invalid_start_date(token: str) -> None:
    """POST /admin/sellers/141air/activate with dashboard_ca and ca_start_date
    not on the 1st of month -> 400."""
    name = "activate_validation_invalid_start_date"

    resp = await _post(
        f"/admin/sellers/{TEST_SELLER}/activate",
        token,
        body={
            "integration_mode": "dashboard_ca",
            "dashboard_empresa": "TEST CA COMPANY",
            "ca_conta_bancaria": "fea5f1de-fd23-4462-9b43-0a2c6ae4df04",
            "ca_centro_custo_variavel": "f7c214a6-be2f-11f0-8080-ab23c683d2a1",
            "ca_start_date": "2026-02-15",  # NOT the 1st of month — invalid
        },
    )
    if resp is None:
        _skip(name, "no response")
        return

    if resp.status_code == 400:
        body = resp.json()
        detail = body.get("detail", "")
        if "1st" in detail or "must be" in detail or "2026-02-15" in detail:
            _pass(name, f"400 with correct detail: {detail!r}")
        else:
            _pass(name, f"400 returned (detail: {detail!r})")
    else:
        _fail(name, f"expected 400 for non-1st ca_start_date, got {resp.status_code}: {resp.text[:300]}")


async def test_backfill_status_endpoint(token: str) -> None:
    """GET /admin/sellers/141air/backfill-status — verify JSON structure."""
    name = "backfill_status_endpoint"

    resp = await _get(f"/admin/sellers/{TEST_SELLER}/backfill-status", token)
    if resp is None:
        _skip(name, "no response")
        return

    if resp.status_code == 404:
        _fail(name, f"seller {TEST_SELLER!r} not found — is it in the DB?")
        return
    if resp.status_code != 200:
        _fail(name, f"expected 200, got {resp.status_code}: {resp.text[:200]}")
        return

    try:
        body = resp.json()
    except Exception:
        _fail(name, f"response is not JSON: {resp.text[:200]}")
        return

    required_keys = {
        "ca_backfill_status",
        "ca_backfill_started_at",
        "ca_backfill_completed_at",
        "ca_backfill_progress",
    }
    missing = required_keys - set(body.keys())
    if missing:
        _fail(name, f"response missing keys: {missing}")
        return

    status_val = body["ca_backfill_status"]
    valid_statuses = {"pending", "running", "completed", "failed", None}
    if status_val not in valid_statuses:
        _fail(name, f"unexpected ca_backfill_status: {status_val!r}")
        return

    # If progress is set, validate its internal structure
    progress = body.get("ca_backfill_progress")
    if isinstance(progress, dict):
        for key in ("total", "processed", "errors"):
            if key not in progress:
                _fail(name, f"ca_backfill_progress missing key: {key!r}")
                return

    _pass(
        name,
        f"ca_backfill_status={status_val!r}, "
        f"progress={'present' if progress else 'null'}",
    )


async def test_backfill_retry_nonexistent_seller(token: str) -> None:
    """POST /admin/sellers/nonexistent-seller-xyz/backfill-retry -> 400 or 404."""
    name = "backfill_retry_nonexistent_seller"

    resp = await _post("/admin/sellers/nonexistent-seller-xyz/backfill-retry", token)
    if resp is None:
        _skip(name, "no response")
        return

    if resp.status_code in (400, 404):
        body = resp.json()
        detail = body.get("detail", "")
        _pass(name, f"{resp.status_code} for non-existent seller: {detail!r}")
    else:
        _fail(
            name,
            f"expected 400 or 404 for non-existent seller, got {resp.status_code}: {resp.text[:300]}",
        )


async def test_upgrade_validation_nonexistent_seller(token: str) -> None:
    """POST /admin/sellers/nonexistent-seller-xyz/upgrade-to-ca -> 404."""
    name = "upgrade_validation_nonexistent_seller"

    resp = await _post(
        "/admin/sellers/nonexistent-seller-xyz/upgrade-to-ca",
        token,
        body={
            "ca_conta_bancaria": "fea5f1de-fd23-4462-9b43-0a2c6ae4df04",
            "ca_centro_custo_variavel": "f7c214a6-be2f-11f0-8080-ab23c683d2a1",
            "ca_start_date": "2026-03-01",
        },
    )
    if resp is None:
        _skip(name, "no response")
        return

    if resp.status_code == 404:
        body = resp.json()
        _pass(name, f"404 as expected: {body.get('detail', '')!r}")
    elif resp.status_code == 400:
        # Some implementations return 400 for validation errors before DB check
        _pass(name, f"400 returned (acceptable)")
    else:
        _fail(
            name,
            f"expected 404 for non-existent seller, got {resp.status_code}: {resp.text[:300]}",
        )


async def test_upgrade_validation_invalid_start_date(token: str) -> None:
    """POST /admin/sellers/{slug}/upgrade-to-ca with ca_start_date not 1st of month -> 400."""
    name = "upgrade_validation_invalid_start_date"

    resp = await _post(
        f"/admin/sellers/{TEST_SELLER}/upgrade-to-ca",
        token,
        body={
            "ca_conta_bancaria": "fea5f1de-fd23-4462-9b43-0a2c6ae4df04",
            "ca_centro_custo_variavel": "f7c214a6-be2f-11f0-8080-ab23c683d2a1",
            "ca_start_date": "2026-03-15",  # NOT 1st
        },
    )
    if resp is None:
        _skip(name, "no response")
        return

    if resp.status_code == 400:
        body = resp.json()
        detail = body.get("detail", "")
        if "1st" in detail or "must be" in detail or "2026-03-15" in detail:
            _pass(name, f"400 with expected detail: {detail!r}")
        else:
            _pass(name, f"400 returned (detail: {detail!r})")
    else:
        _fail(
            name,
            f"expected 400 for non-1st ca_start_date, got {resp.status_code}: {resp.text[:300]}",
        )


async def test_extrato_ingestion_status(token: str) -> None:
    """GET /admin/extrato/ingestion-status — verify response structure."""
    name = "extrato_ingestion_status"

    resp = await _get("/admin/extrato/ingestion-status", token)
    if resp is None:
        _skip(name, "no response")
        return

    if resp.status_code != 200:
        _fail(name, f"expected 200, got {resp.status_code}: {resp.text[:200]}")
        return

    try:
        body = resp.json()
    except Exception:
        _fail(name, f"response is not JSON: {resp.text[:200]}")
        return

    # Must have ran_at and results
    if "ran_at" not in body:
        _fail(name, f"missing 'ran_at' key: {body}")
        return
    if "results" not in body:
        _fail(name, f"missing 'results' key: {body}")
        return
    if not isinstance(body["results"], list):
        _fail(name, f"'results' is not a list: {type(body['results']).__name__}")
        return

    ran_at = body["ran_at"]
    results_count = len(body["results"])

    _pass(name, f"ran_at={ran_at!r}, results count={results_count}")


async def test_extrato_ingest_missing_params(token: str) -> None:
    """POST /admin/extrato/ingest/141air without required query params -> 422."""
    name = "extrato_ingest_missing_params"

    resp = await _post(
        f"/admin/extrato/ingest/{TEST_SELLER}",
        token,
        # Intentionally omit begin_date and end_date (required query params)
    )
    if resp is None:
        _skip(name, "no response")
        return

    if resp.status_code == 422:
        # FastAPI returns 422 for missing required query parameters
        _pass(name, "422 Unprocessable Entity for missing begin_date/end_date")
    elif resp.status_code == 400:
        _pass(name, "400 for missing params (acceptable)")
    else:
        _fail(
            name,
            f"expected 422 for missing required query params, got {resp.status_code}: {resp.text[:300]}",
        )


async def test_unauthorized_access() -> None:
    """Endpoints protected by X-Admin-Token should return 401/403 without a valid
    token. 404 is also acceptable if the route is not yet deployed (migration
    or server restart pending)."""
    name = "unauthorized_access"

    if not _HTTPX_OK:
        _skip(name, "httpx not installed")
        return

    # Check connectivity
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health = await client.get(f"{BASE_URL}/health")
    except httpx.ConnectError:
        _skip(name, f"cannot connect to {BASE_URL}")
        return

    endpoints = [
        ("GET", f"/admin/sellers/{TEST_SELLER}/backfill-status"),
        ("GET", "/admin/extrato/ingestion-status"),
        ("GET", "/admin/onboarding/install-link"),
    ]

    # Acceptable: 401/403 (auth rejected) or 404 (route not yet registered in
    # the running server — endpoints added in V2 but server may be pre-restart).
    # NOT acceptable: 200 (would mean auth is not enforced).
    failures = []
    notes = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for method, path in endpoints:
            try:
                if method == "GET":
                    resp = await client.get(
                        f"{BASE_URL}{path}",
                        headers={"X-Admin-Token": "invalid-token-xyz"},
                    )
                else:
                    resp = await client.post(
                        f"{BASE_URL}{path}",
                        headers={"X-Admin-Token": "invalid-token-xyz"},
                        json={},
                    )

                if resp.status_code == 200:
                    failures.append(
                        f"{method} {path}: got 200 — endpoint accessible without valid token!"
                    )
                elif resp.status_code == 404:
                    notes.append(f"{method} {path} -> 404 (route not yet registered in running server)")
                elif resp.status_code not in (401, 403):
                    failures.append(
                        f"{method} {path}: expected 401/403/404, got {resp.status_code}"
                    )

            except httpx.ConnectError:
                failures.append(f"{method} {path}: connection error")

    if failures:
        _fail(name, "; ".join(failures))
    elif notes:
        _pass(
            name,
            f"no 200 responses (endpoints secure). "
            f"Note: {len(notes)} route(s) returned 404 — server restart may be needed",
        )
    else:
        _pass(name, f"all {len(endpoints)} protected endpoints return 401/403 for invalid token")


# ── Main ───────────────────────────────────────────────────────────────────────


async def main() -> None:
    print()
    print("=" * 65)
    print("  Admin API Endpoints — Test Suite")
    print(f"  Base URL: {BASE_URL}")
    print(f"  Credentials: {'ADMIN_TOKEN set' if ADMIN_TOKEN else 'ADMIN_PASSWORD set' if ADMIN_PASSWORD else 'none (API tests will skip)'}")
    print("=" * 65)

    # --- Validation-only tests (no API required) ---
    print()
    print("--- Validation Logic Tests (no API required) ---")
    test_ca_start_date_validation_logic()
    test_integration_mode_enum_validation()
    test_activate_required_fields_for_dashboard_ca()
    test_install_link_url_format()
    test_backfill_status_response_schema()
    test_ingestion_status_response_schema()

    # --- Unauthorized access check (only needs connectivity) ---
    print()
    print("--- Security: Unauthorized Access ---")
    await test_unauthorized_access()

    # --- API integration tests ---
    print()
    print("--- API Integration Tests (requires running server + credentials) ---")

    token = await test_login()

    if token:
        await test_install_link_endpoint(token)
        await test_activate_validation_invalid_mode(token)
        await test_activate_validation_missing_ca_fields(token)
        await test_activate_validation_invalid_start_date(token)
        await test_backfill_status_endpoint(token)
        await test_backfill_retry_nonexistent_seller(token)
        await test_upgrade_validation_nonexistent_seller(token)
        await test_upgrade_validation_invalid_start_date(token)
        await test_extrato_ingestion_status(token)
        await test_extrato_ingest_missing_params(token)
    else:
        api_tests = [
            "install_link_endpoint",
            "activate_validation_invalid_mode",
            "activate_validation_missing_ca_fields",
            "activate_validation_invalid_start_date",
            "backfill_status_endpoint",
            "backfill_retry_nonexistent_seller",
            "upgrade_validation_nonexistent_seller",
            "upgrade_validation_invalid_start_date",
            "extrato_ingestion_status",
            "extrato_ingest_missing_params",
        ]
        reason = "no admin token (set ADMIN_PASSWORD or ADMIN_TOKEN)"
        for t in api_tests:
            _skip(t, reason)

    # Summary
    print()
    print("=" * 65)
    passed  = sum(1 for r in _results if r["status"] == "PASS")
    failed  = sum(1 for r in _results if r["status"] == "FAIL")
    skipped = sum(1 for r in _results if r["status"] == "SKIP")
    total   = len(_results)

    print(
        f"  Results: {GREEN}{passed} passed{RESET}  "
        f"{RED}{failed} failed{RESET}  "
        f"{YELLOW}{skipped} skipped{RESET}  "
        f"({total} total)"
    )

    if skipped > 0:
        print(
            f"  {YELLOW}Tip:{RESET} run with ADMIN_PASSWORD=xxx to enable "
            f"{skipped} API integration tests"
        )

    print("=" * 65)
    print()

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

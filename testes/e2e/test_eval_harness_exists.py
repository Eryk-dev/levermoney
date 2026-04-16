"""T-001 red test: scripts/run_reconciliation.py CLI emits valid JSON.

Usa mark `integration` (requer Supabase + ML API + CSV de extrato no disco).
Rodar com: pytest -m eval_harness -v
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_reconciliation.py"

REQUIRED_KEYS = {
    "seller", "period",
    "coverage_credits", "coverage_debits",
    "orphan_extrato_count", "orphan_system_count",
    "daily_diff_max", "extrato_lines",
}


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.eval_harness
def test_script_exists():
    assert SCRIPT_PATH.exists(), (
        f"{SCRIPT_PATH.relative_to(PROJECT_ROOT)} does not exist. "
        f"Task T-001 pending: implement the CLI entrypoint."
    )


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.eval_harness
def test_script_emits_required_json_keys():
    result = subprocess.run(
        ["python3", str(SCRIPT_PATH), "141air", "2026-01"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"script exit={result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"stdout is not valid JSON: {e}\n--- STDOUT ---\n{result.stdout}\n--- STDERR ---\n{result.stderr}")

    missing = REQUIRED_KEYS - data.keys()
    assert not missing, f"missing required keys: {sorted(missing)}. Got: {sorted(data.keys())}"


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.eval_harness
def test_script_emits_typed_coverage_values():
    result = subprocess.run(
        ["python3", str(SCRIPT_PATH), "141air", "2026-01"],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)

    for key in ("coverage_credits", "coverage_debits"):
        assert isinstance(data[key], (int, float)), f"{key} must be numeric"
        assert 0 <= data[key] <= 100, f"{key}={data[key]} not a percentage"

    for key in ("orphan_extrato_count", "orphan_system_count", "extrato_lines"):
        assert isinstance(data[key], int), f"{key} must be int"
        assert data[key] >= 0

    assert isinstance(data["daily_diff_max"], (int, float))
    assert data["daily_diff_max"] >= 0

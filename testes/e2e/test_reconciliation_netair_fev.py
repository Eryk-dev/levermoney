"""E2E gate: net-air fev/2026 reconciliation must hit contract thresholds.

Builds on ERR-0021..0031 (net-air jan) + new fevereiro fixes:
ERR-0027 (loan disbursement skip), ERR-0028 (money_transfer skip),
ERR-0029 (cross-month suffix seed), ERR-0030 (dinheiro_recebido / pix_nao_sync
complementary), ERR-0031 (compra_ml / transferencia_saldo),
ERR-0032 (phantom release wash for by_payer / expired).

Run: pytest testes/e2e/test_reconciliation_netair_fev.py -m reconciliation -v
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "run_reconciliation.py"
CONTRACT = PROJECT_ROOT / "specs" / "002-extrato-reconciliation" / "contracts" / "reconciliation.yml"

SELLER = "net-air"
PERIOD = "2026-02"


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def result() -> dict:
    proc = subprocess.run(
        ["python3", str(SCRIPT), SELLER, PERIOD],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"script crashed: {proc.stderr}"
    return json.loads(proc.stdout)


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.reconciliation
def test_cobertura_minima_creditos(result: dict, contract: dict):
    target = contract["coverage"]["credits_min_pct"]
    actual = result["coverage_credits"]
    assert actual >= target, (
        f"coverage_credits {actual:.2f}% < target {target}%\n"
        f"  Gap: {target - actual:.2f} pp"
    )


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.reconciliation
def test_cobertura_minima_debitos(result: dict, contract: dict):
    target = contract["coverage"]["debits_min_pct"]
    actual = result["coverage_debits"]
    assert actual >= target, (
        f"coverage_debits {actual:.2f}% < target {target}%"
    )


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.reconciliation
def test_daily_diff_max_tolerance(result: dict, contract: dict):
    target = contract["tolerances"]["per_day_brl"]
    actual = result["daily_diff_max"]
    assert actual <= target, (
        f"daily_diff_max R$ {actual:.2f} > tolerance R$ {target}\n"
        f"  Dias divergentes: {result['divergent_days']}/{result['total_days']}"
    )


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.reconciliation
def test_orphan_extrato_count_max(result: dict, contract: dict):
    target = contract["coverage"]["orphan_extrato_max_count"]
    actual = result["orphan_extrato_count"]
    assert actual <= target


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.reconciliation
def test_orphan_system_count_max(result: dict, contract: dict):
    target = contract["coverage"]["orphan_system_max_count"]
    actual = result["orphan_system_count"]
    assert actual <= target

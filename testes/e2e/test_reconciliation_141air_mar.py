"""E2e gate: 141air março/2026 deve bater thresholds do contract.yml.

Espelha `test_reconciliation_141air_jan.py` e `..._fev.py`. Ficou green
após ERR-0019 resolvido + nova regra `pagamento_qr_cancelado` (2026-04-16).

Uso:
    pytest testes/e2e/test_reconciliation_141air_mar.py -m reconciliation -v
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

SELLER = "141air"
PERIOD = "2026-03"


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def result() -> dict:
    """Run reconciliation once for the whole module."""
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
        f"  Gap: {target - actual:.2f} pp\n"
        f"  Orphans extrato: {result['orphan_extrato_count']} linhas\n"
        f"  Top categorias: {sorted(result['orphan_extrato_by_category'].items(), key=lambda x: -x[1]['amount'])[:3]}"
    )


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.reconciliation
def test_cobertura_minima_debitos(result: dict, contract: dict):
    target = contract["coverage"]["debits_min_pct"]
    actual = result["coverage_debits"]
    assert actual >= target, (
        f"coverage_debits {actual:.2f}% < target {target}%\n"
        f"  Gap: {target - actual:.2f} pp"
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
    assert actual <= target, (
        f"orphan_extrato_count {actual} > max {target}\n"
        f"  Top categorias: "
        f"{sorted(result['orphan_extrato_by_category'].items(), key=lambda x: -x[1]['count'])[:5]}"
    )


@pytest.mark.integration
@pytest.mark.e2e
@pytest.mark.reconciliation
def test_orphan_system_count_max(result: dict, contract: dict):
    target = contract["coverage"]["orphan_system_max_count"]
    actual = result["orphan_system_count"]
    assert actual <= target, (
        f"orphan_system_count {actual} > max {target}\n"
        f"  Top categorias: "
        f"{sorted(result['orphan_system_by_category'].items(), key=lambda x: -x[1]['count'])[:5]}"
    )

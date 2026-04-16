#!/usr/bin/env python3
"""CLI entrypoint para reconciliação extrato ↔ sistema.

Uso:
    python3 scripts/run_reconciliation.py <seller> <period>
    python3 scripts/run_reconciliation.py 141air 2026-01

Saída: JSON no stdout com métricas. Exit 0 em sucesso, 2 em erro de uso.

Lê contrato de specs/002-extrato-reconciliation/contracts/reconciliation.yml.
Engine: app.services.reconciliation.reconcile().
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.reconciliation import reconcile


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: run_reconciliation.py <seller> <period>\n"
            "       period format: YYYY-MM (e.g. 2026-01)\n"
            "Example: run_reconciliation.py 141air 2026-01",
            file=sys.stderr,
        )
        return 2

    seller, period = sys.argv[1], sys.argv[2]

    metrics = reconcile(seller, period)
    print(json.dumps(metrics.as_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

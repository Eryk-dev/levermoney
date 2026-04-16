#!/usr/bin/env bash
# Wrapper: roda scripts/run_reconciliation.py + anexa 1 linha em docs/reconciliation/RUNS.md
#
# Uso:
#   ./scripts/run_reconciliation.sh <seller> <period>
#   ./scripts/run_reconciliation.sh 141air 2026-01
#
# Saída: JSON no stdout (do script Python) + bloco markdown anexado em RUNS.md.
# Exit code:
#   0 = ran successfully, metrics appended
#   1 = script crashed
#   2 = coverage dropped vs previous run (investigar antes de commitar)

set -euo pipefail

SELLER="${1:-}"
PERIOD="${2:-}"

if [[ -z "$SELLER" || -z "$PERIOD" ]]; then
  echo "Usage: $0 <seller> <period>" >&2
  echo "       $0 141air 2026-01" >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNS_FILE="$PROJECT_ROOT/docs/reconciliation/RUNS.md"
PY_SCRIPT="$PROJECT_ROOT/scripts/run_reconciliation.py"

if [[ ! -f "$PY_SCRIPT" ]]; then
  echo "ERROR: $PY_SCRIPT does not exist yet (task T-001 pending)" >&2
  exit 1
fi

COMMIT=$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo "no-git")
NOW=$(date +"%Y-%m-%d %H:%M")

# Run the Python reconciliation and capture JSON output
JSON_OUTPUT=$(python3 "$PY_SCRIPT" "$SELLER" "$PERIOD" 2>/dev/null) || {
  echo "ERROR: run_reconciliation.py crashed" >&2
  exit 1
}

# Extract metrics via Python (jq may not be installed)
METRICS=$(python3 - <<PYEOF
import json, sys
data = json.loads('''$JSON_OUTPUT''')
print(f"{data.get('coverage_credits', 0):.2f}|{data.get('coverage_debits', 0):.2f}|{data.get('orphan_extrato_count', 0)}|{data.get('orphan_system_count', 0)}|{data.get('daily_diff_max', 0):.2f}|{data.get('extrato_lines', 0)}")
PYEOF
)

IFS='|' read -r COV_CRED COV_DEB ORPH_EXT ORPH_SYS DAILY_MAX LINES <<< "$METRICS"

cat >> "$RUNS_FILE" <<MDEOF

## $NOW — $SELLER $PERIOD
**Commit:** $COMMIT
**Trigger:** manual

| Métrica | Valor |
|---|---|
| Cobertura créditos | $COV_CRED% |
| Cobertura débitos | $COV_DEB% |
| Orphan extrato | $ORPH_EXT |
| Orphan sistema | $ORPH_SYS |
| Daily diff max | R\$ $DAILY_MAX |
| Linhas extrato | $LINES |

MDEOF

echo "[run_reconciliation.sh] appended $NOW to $RUNS_FILE"
echo "  cred=$COV_CRED% deb=$COV_DEB% orph_ext=$ORPH_EXT orph_sys=$ORPH_SYS"

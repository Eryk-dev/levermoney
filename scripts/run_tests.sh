#!/usr/bin/env bash
# Wrapper: roda pytest + anexa 1 linha em docs/reconciliation/TEST_LOG.md
#
# Uso:
#   ./scripts/run_tests.sh                   # full suite
#   ./scripts/run_tests.sh -m reconciliation # só tags reconciliation
#   ./scripts/run_tests.sh -k processor      # só testes que casam com "processor"
#
# Repassa todos os args para pytest.

set -uo pipefail   # NOTE: removido -e para poder capturar exit code do pytest

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$PROJECT_ROOT/docs/reconciliation/TEST_LOG.md"

COMMIT=$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo "no-git")
NOW=$(date +"%Y-%m-%d %H:%M")

# Capture pytest output
OUTPUT=$(cd "$PROJECT_ROOT" && python3 -m pytest "$@" 2>&1)
PYTEST_EXIT=$?

# Parse summary line (last non-empty line usually has "N passed, N failed, N skipped in X.XXs")
SUMMARY=$(echo "$OUTPUT" | grep -E "(passed|failed|error)" | tail -1 | sed 's/^=*\s*//' | sed 's/\s*=*$//')

# Tag summary
TAG_SUMMARY="—"
if [[ "$*" == *"-m "* ]]; then
  TAG=$(echo "$*" | sed -n 's/.*-m \([^ ]*\).*/\1/p')
  TAG_SUMMARY="@$TAG"
fi

CONTEXT_LINE="${*:-full suite}"

cat >> "$LOG_FILE" <<MDEOF

## $NOW — $CONTEXT_LINE
**Commit:** $COMMIT
**Trigger:** manual
**Exit code:** $PYTEST_EXIT

| Métrica | Valor |
|---|---|
| Summary | $SUMMARY |
| Tag | $TAG_SUMMARY |

MDEOF

echo "[run_tests.sh] exit=$PYTEST_EXIT summary=\"$SUMMARY\""
echo "[run_tests.sh] appended $NOW to $LOG_FILE"

exit $PYTEST_EXIT

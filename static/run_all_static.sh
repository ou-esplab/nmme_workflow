#!/usr/bin/env bash
# Orchestrate all one-time static computations in dependency order.
# Run from the project root: bash static/run_all_static.sh [options]
#
# Options:
#   --skip-climos   Skip climatology + tercile threshold steps (already done)
#   --skip-skill    Skip skill computation + plot steps
#   --publish       Also publish skill plots to web server after completion
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

SKIP_CLIMOS=0; SKIP_SKILL=0; DO_PUBLISH=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-climos) SKIP_CLIMOS=1; shift ;;
        --skip-skill)  SKIP_SKILL=1;  shift ;;
        --publish)     DO_PUBLISH=1;  shift ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [[ $SKIP_CLIMOS -eq 0 ]]; then
    echo "=== Step 1: Climatologies ==="
    bash static/run_all_climos.sh

    echo "=== Step 2: Tercile thresholds ==="
    python3 static/precompute_tercile_thresholds.py
fi

if [[ $SKIP_SKILL -eq 0 ]]; then
    echo "=== Step 3: Hindcast skill (RPSS + ACC 1991-2020) ==="
    bash static/skill/run_skill.sh

    echo "=== Step 4: Skill plots ==="
    bash static/skill/run_plots.sh
fi

if [[ $DO_PUBLISH -eq 1 ]]; then
    echo "=== Step 5: Publish skill to web ==="
    bash publish/publish_skill.sh
fi

echo ""
echo "=== Static pipeline complete ==="

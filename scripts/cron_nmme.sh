#!/usr/bin/env bash
# cron_nmme.sh — monthly NMME forecast workflow cron wrapper
#
# Intended crontab entry (runs on the 8th of each month at 03:00 UTC):
#   0 3 8 * *  /home/kpegion/projects/nmme_workflow/scripts/cron_nmme.sh >> /home/kpegion/projects/nmme_workflow/logs/cron.log 2>&1
#
# Usage: cron_nmme.sh [OPTIONS]
#   -s, --stages STAGES   Space-separated stage list (default: ingest preprocess products publish arraylake)
#   -i, --init   YYYYMM   Forecast init date (default: current month)
#   -c, --config FILE     Path to config YAML (default: confignmme.yaml)
#   -e, --env    NAME     Conda environment name (default: nmme_workflow_env)
#       --conda-base DIR  Path to miniconda/anaconda root (default: ~/miniconda3)
#   -n, --dry-run         Full pipeline dry run: no downloads, no NetCDF writes,
#                         no Arraylake commits, no SSH/SCP. Passes
#                         --ingest-dry-run --preprocess-dry-run --products-dry-run
#                         --arraylake-dry-run --publish-dry-run to runners/cli.py.
#   -h, --help            Show this help
#
# Options override environment variables (NMME_STAGES, NMME_INIT, NMME_CONFIG, ENV_NAME, CONDA_BASE, NMME_DRY_RUN).

set -euo pipefail

# ---- Resolve script location ------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ---- Defaults from environment ----------------------------------------------
CONFIG="${NMME_CONFIG:-$ROOT_DIR/confignmme.yaml}"
STAGES="${NMME_STAGES:-ingest preprocess products publish arraylake}"
CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"
ENV_NAME="${ENV_NAME:-nmme_workflow_env}"
INIT_ARG="${NMME_INIT:-}"
DRY_RUN="${NMME_DRY_RUN:-0}"

# ---- Parse CLI arguments (override env vars) --------------------------------
usage() {
    sed -n '/#/!d; s/^# \{0,1\}//p' "$0" | sed -n '/^Usage:/,/^$/p'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--stages)    STAGES="$2";    shift 2 ;;
        -i|--init)      INIT_ARG="$2";  shift 2 ;;
        -c|--config)    CONFIG="$2";    shift 2 ;;
        -e|--env)       ENV_NAME="$2";  shift 2 ;;
        --conda-base)   CONDA_BASE="$2"; shift 2 ;;
        -n|--dry-run)   DRY_RUN=1;       shift ;;
        -h|--help)      usage ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

LOCK_FILE="$ROOT_DIR/.nmme_cron.lock"

# ---- Logging ----------------------------------------------------------------
TS="$(date -u +%Y%m%d_%H%M%S)"
LOG_DIR="$ROOT_DIR/logs/cron"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/nmme_${TS}.log"

log() { echo "[$(date -u +%F\ %T\ UTC)] $*"; }

exec > >(tee -a "$LOG_FILE") 2>&1

log "==> NMME cron run starting (TS=$TS)"

# ---- Lock: prevent overlapping runs -----------------------------------------
if [ -e "$LOCK_FILE" ]; then
    LOCKED_PID="$(cat "$LOCK_FILE" 2>/dev/null || echo unknown)"
    if kill -0 "$LOCKED_PID" 2>/dev/null; then
        log "[WARN] Another run is active (PID=$LOCKED_PID). Exiting."
        exit 0
    else
        log "[INFO] Stale lock found (PID=$LOCKED_PID); removing."
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"; log "==> Lock released."' EXIT

# ---- Conda setup ------------------------------------------------------------
CONDA_SH="$CONDA_BASE/etc/profile.d/conda.sh"
if [[ ! -f "$CONDA_SH" ]]; then
    log "[FATAL] conda.sh not found at $CONDA_SH. Set CONDA_BASE."
    exit 2
fi
# Disable set -u briefly: conda.sh may reference unset variables (e.g. PS1)
# in non-interactive shells, which would otherwise abort the script.
set +u
# shellcheck source=/dev/null
source "$CONDA_SH"
set -u
log "[INFO] Using conda env: $ENV_NAME"

# ---- Resolve forecast init date (YYYYMM) ------------------------------------
if [[ -n "$INIT_ARG" ]]; then
    INIT_DATE="$INIT_ARG"
    log "[INFO] Using provided init date: $INIT_DATE"
else
    # Default: current month (NMME forecasts are available by the 5th-8th)
    INIT_DATE="$(date -u +%Y%m)"
    log "[INFO] Resolved init date (current month): $INIT_DATE"
fi

# ---- Run workflow -----------------------------------------------------------
log "[INFO] Running stages: $STAGES"
DRY_RUN_FLAGS=()
if [[ "$DRY_RUN" == "1" ]]; then
    log "[INFO] DRY RUN mode: no downloads, no NetCDF writes, no Arraylake commits, no SSH/SCP."
    DRY_RUN_FLAGS=(--ingest-dry-run --preprocess-dry-run --products-dry-run --arraylake-dry-run --publish-dry-run)
fi
# shellcheck disable=SC2086
conda run --no-capture-output -n "$ENV_NAME" \
    python3 "$ROOT_DIR/runners/cli.py" \
    --system nmme \
    --config "$CONFIG" \
    --init "$INIT_DATE" \
    --stages $STAGES \
    "${DRY_RUN_FLAGS[@]}"

log "==> NMME cron run complete."

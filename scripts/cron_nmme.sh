#!/usr/bin/env bash
# cron_nmme.sh — monthly NMME forecast workflow cron wrapper
#
# Intended crontab entry (e.g. runs on the 15th of each month at 03:00 UTC):
#   0 3 15 * *  /home/kpegion/projects/nmme_workflow/scripts/cron_nmme.sh >> /home/kpegion/projects/nmme_workflow/logs/cron.log 2>&1
#
# Optional overrides via environment variables:
#   NMME_CONFIG   — path to config YAML (default: confignmme.yaml next to scripts/)
#   NMME_INIT     — override forecast init date as YYYYMM (default: previous month)
#   NMME_STAGES   — space-separated stage list (default: ingest preprocess products pycpt publish)
#   CONDA_BASE    — path to miniconda/anaconda root (default: ~/miniconda3)
#   ENV_NAME      — conda environment name (default: nmme_workflow_env)

set -euo pipefail

# ---- Resolve script location ------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ---- Configuration ----------------------------------------------------------
CONFIG="${NMME_CONFIG:-$ROOT_DIR/confignmme.yaml}"
STAGES="${NMME_STAGES:-ingest preprocess products publish}"
CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"
ENV_NAME="${ENV_NAME:-nmme_workflow_env}"
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
if [[ -n "${NMME_INIT:-}" ]]; then
    INIT_DATE="$NMME_INIT"
    log "[INFO] Using provided init date: $INIT_DATE"
else
    # Default: previous month (NMME data is typically available ~2 weeks into
    # the current month for the previous month's initialization)
    INIT_DATE="$(date -u -d 'last month' +%Y%m)"
    log "[INFO] Resolved init date (previous month): $INIT_DATE"
fi

# ---- Run workflow -----------------------------------------------------------
log "[INFO] Running stages: $STAGES"
# shellcheck disable=SC2086
conda run --no-capture-output -n "$ENV_NAME" \
    python3 "$ROOT_DIR/runners/cli.py" \
    --system nmme \
    --config "$CONFIG" \
    --init "$INIT_DATE" \
    --stages $STAGES

log "==> NMME cron run complete."

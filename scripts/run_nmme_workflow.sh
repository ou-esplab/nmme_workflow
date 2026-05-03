#!/bin/bash
# NMME Workflow Wrapper Script
# Supports both CLI and cron execution with file-based mutual exclusion locking

set -euo pipefail

# Configuration
WORKFLOW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCKFILE="${WORKFLOW_DIR}/.nmme.lock"
CONFIG="${WORKFLOW_DIR}/confignmme.yaml"
CONDA_ENV="nmme_workflow_env"

# Logging
LOGS_DIR="${WORKFLOW_DIR}/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_DIR="${LOGS_DIR}/${TIMESTAMP}"
LOG_FILE="${LOG_DIR}/workflow.log"

# Function: print usage
usage() {
    cat << 'EOF'
Usage: ./scripts/run_nmme_workflow.sh [OPTIONS]

OPTIONS:
  --init YYYYMM              Forecast init date (required for regular runs)
  --stages STAGE [STAGE...]  Stages to run (default: ingest preprocess products pycpt)
                             Available: ingest, preprocess, products, pycpt, publish
  --publish                  Include publish stage
  --publish-dry-run          Publish without SSH/SCP side effects
  --products-dry-run         Products stage without writing output
  --pycpt-dry-run            PyCPT without running CPT.x
  --cron                     Cron mode: uses flock, suppresses stderr if already running
  --help                     Show this message

EXAMPLES (CLI):
  # Full pipeline for February 2026 initialization
  ./scripts/run_nmme_workflow.sh --init 202602

  # Products only, dry-run
  ./scripts/run_nmme_workflow.sh --init 202602 --stages products --products-dry-run

  # Full pipeline with publish
  ./scripts/run_nmme_workflow.sh --init 202602 --stages ingest preprocess products pycpt publish

EXAMPLES (CRON):
  # In crontab, add flock + cron mode:
  0 3 15 * * /home/kpegion/projects/nmme_workflow/scripts/run_nmme_workflow.sh --init 202602 --cron 2>/dev/null

  # Or use the wrapper with environment:
  0 3 15 * * /usr/bin/env bash /path/to/run_nmme_workflow.sh --init 202602 --cron 2>/dev/null
EOF
    exit 1
}

# Function: setup logging directory
setup_logging() {
    mkdir -p "${LOG_DIR}"
    touch "${LOG_FILE}"
    echo "Log: ${LOG_FILE}" >&2
}

# Function: run with conda activation
run_workflow() {
    local init_arg="${1}"
    shift
    local extra_args=("$@")
    
    # Build the runner command
    local cmd=(
        "conda" "run" "-n" "${CONDA_ENV}"
        "python" "runners/cli.py"
        "--system" "nmme"
        "--config" "${CONFIG}"
        "--init" "${init_arg}"
    )
    
    # Add extra arguments
    for arg in "${extra_args[@]}"; do
        cmd+=("${arg}")
    done
    
    # Execute in workflow directory
    cd "${WORKFLOW_DIR}"
    "${cmd[@]}" 2>&1 | tee -a "${LOG_FILE}"
}

# Function: main entry point
main() {
    local init_date=""
    local stages=()
    local cron_mode=false
    local extra_args=()
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --init)
                init_date="$2"
                shift 2
                ;;
            --stages)
                shift
                while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                    stages+=("$1")
                    shift
                done
                ;;
            --cron)
                cron_mode=true
                shift
                ;;
            --publish|--publish-dry-run|--products-dry-run|--pycpt-dry-run|--products-direct)
                extra_args+=("$1")
                shift
                ;;
            --help)
                usage
                ;;
            *)
                echo "Unknown option: $1" >&2
                usage
                ;;
        esac
    done
    
    # Validate required arguments
    if [[ -z "${init_date}" ]]; then
        echo "ERROR: --init YYYYMM is required" >&2
        usage
    fi
    
    # Setup logging
    setup_logging
    
    # Add stages if specified
    if [[ ${#stages[@]} -gt 0 ]]; then
        extra_args+=("--stages" "${stages[@]}")
    fi
    
    # Cron mode: use flock with non-blocking flag
    if [[ "${cron_mode}" == true ]]; then
        if ! flock -n "${LOCKFILE}" run_workflow "${init_date}" "${extra_args[@]}"; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Previous workflow still running (lock held); skipping." >> "${LOG_FILE}"
            exit 0
        fi
    else
        # CLI mode: direct execution
        run_workflow "${init_date}" "${extra_args[@]}"
    fi
    
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Workflow complete." >> "${LOG_FILE}"
}

# Run main function
main "$@"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

INIT_DATE="${1:-}"
CONFIG_IN="${2:-${WORKFLOW_DIR}/confignmme.yaml}"

if [[ -z "${INIT_DATE}" ]]; then
  echo "[FATAL] init date is required" >&2
  exit 2
fi

CONFIG="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "${CONFIG_IN}")"

if [[ ! -f "${CONFIG}" ]]; then
  echo "[FATAL] config not found: ${CONFIG}" >&2
  exit 2
fi

ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

CMD=(python3 "${SCRIPT_DIR}/update_arraylake_fcsts.py" --config "${CONFIG}" --date "${INIT_DATE}")
if [[ "${ARRAYLAKE_DRY_RUN:-0}" == "1" ]]; then
  CMD+=(--dry-run)
fi

cd "${WORKFLOW_DIR}"
exec "${CMD[@]}"
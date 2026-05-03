#!/usr/bin/env bash
set -Eeuo pipefail

cfg_get() {
  local key="$1"
  local default="$2"

  if [[ ! -f "$NMME_CONFIG" ]] || ! command -v python3 >/dev/null 2>&1; then
    echo "$default"
    return
  fi

  python3 - "$NMME_CONFIG" "$key" "$default" <<'PY'
import sys

cfg_path, key, default = sys.argv[1:]

try:
  import yaml
except Exception:
  print(default)
  raise SystemExit(0)

try:
  with open(cfg_path, "r") as f:
    data = yaml.safe_load(f) or {}
except Exception:
  print(default)
  raise SystemExit(0)

cur = data
for part in key.split('.'):
  if not isinstance(cur, dict) or part not in cur:
    print(default)
    raise SystemExit(0)
  cur = cur[part]

if isinstance(cur, bool):
  print("1" if cur else "0")
elif cur is None:
  print(default)
else:
  print(cur)
PY
}

run_cmd() {
  if [[ "$PUBLISH_DRY_RUN" == "1" ]]; then
    echo "[DRY-RUN] $*"
  else
    "$@"
  fi
}

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 YYYYMM" >&2
  exit 2
fi

fcstdate="$1"
if [[ ! "$fcstdate" =~ ^[0-9]{6}$ ]]; then
  echo "ERROR: fcstdate must be YYYYMM" >&2
  exit 2
fi

NMME_CONFIG="${NMME_CONFIG:-confignmme.yaml}"

PUBLISH_ENABLED="${PUBLISH_ENABLED:-$(cfg_get 'pipeline.publish.enabled' '1')}"
PUBLISH_DRY_RUN="${PUBLISH_DRY_RUN:-$(cfg_get 'pipeline.publish.dry_run' '0')}"
PUBLISH_COPY_MONTHLY="${PUBLISH_COPY_MONTHLY:-$(cfg_get 'pipeline.publish.copy_monthly' '1')}"
PUBLISH_COPY_SEASONAL="${PUBLISH_COPY_SEASONAL:-$(cfg_get 'pipeline.publish.copy_seasonal' '1')}"
PUBLISH_COPY_LATEST="${PUBLISH_COPY_LATEST:-$(cfg_get 'pipeline.publish.copy_latest' '1')}"
PUBLISH_UPDATE_HTML="${PUBLISH_UPDATE_HTML:-$(cfg_get 'pipeline.publish.update_html' '1')}"
PUBLISH_DEST_HOST="${PUBLISH_DEST_HOST:-$(cfg_get 'pipeline.publish.dest_host' 'somclass23.som.nor.ou.edu')}"
PUBLISH_DEST_DIR="${PUBLISH_DEST_DIR:-$(cfg_get 'pipeline.publish.dest_dir' '/home/kpegion/http/nmme/forecasts')}"
PUBLISH_SSH_KEY="${PUBLISH_SSH_KEY:-$(cfg_get 'pipeline.publish.ssh_key' '~/.ssh/id_ed25519')}"

MONTHLY_ROOT="${NMME_MONTHLY_ROOT:-$(cfg_get 'data.output.nmme_monthly' '/data/esplab/shared/model/initialized/nmme/forecast/monthly')}"
SEASONAL_ROOT="${NMME_SEASONAL_ROOT:-$(cfg_get 'data.output.nmme_seasonal' '/data/esplab/shared/model/initialized/nmme/forecast/seasonal')}"

if [[ "$PUBLISH_ENABLED" != "1" ]]; then
  echo "[INFO] publish stage disabled; skipping"
  exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ssh_key_expanded="${PUBLISH_SSH_KEY/#\~/$HOME}"

sourceDirMon="${MONTHLY_ROOT}/${fcstdate}"
sourceDirSeas="${SEASONAL_ROOT}/${fcstdate}"

if [[ -f "/home/${USER}/miniconda3/etc/profile.d/conda.sh" ]]; then
  . "/home/${USER}/miniconda3/etc/profile.d/conda.sh"
  conda activate subxnmme || true
fi

timeout=60
while (( timeout > 0 )) && { [[ -f "${sourceDirMon}/nmmefcst.lock" ]] || [[ -f "${sourceDirSeas}/nmmefcst.lock" ]]; }
do
  sleep 60
  ((timeout -= 1))
done

if (( timeout == 0 )); then
  echo "ERROR: products may not be complete. Lock file still present for ${fcstdate}" >&2
  exit 1
fi

run_cmd ssh -i "${ssh_key_expanded}" "${PUBLISH_DEST_HOST}" \
  "mkdir -p ${PUBLISH_DEST_DIR}/images/${fcstdate} ${PUBLISH_DEST_DIR}/images/Latest ${PUBLISH_DEST_DIR}/data/${fcstdate}"

shopt -s nullglob

if [[ "$PUBLISH_COPY_MONTHLY" == "1" ]]; then
  image_files=("${sourceDirMon}/images/"*)
  data_files=("${sourceDirMon}/data/"*)
  if (( ${#image_files[@]} > 0 )); then
    run_cmd scp -i "${ssh_key_expanded}" "${image_files[@]}" "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/"
    if [[ "$PUBLISH_COPY_LATEST" == "1" ]]; then
      run_cmd scp -i "${ssh_key_expanded}" "${image_files[@]}" "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/Latest/"
    fi
  fi
  if (( ${#data_files[@]} > 0 )); then
    run_cmd scp -i "${ssh_key_expanded}" "${data_files[@]}" "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/data/${fcstdate}/"
  fi
fi

if [[ "$PUBLISH_COPY_SEASONAL" == "1" ]]; then
  image_files=("${sourceDirSeas}/images/"*)
  data_files=("${sourceDirSeas}/data/"*)
  if (( ${#image_files[@]} > 0 )); then
    run_cmd scp -i "${ssh_key_expanded}" "${image_files[@]}" "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/"
    if [[ "$PUBLISH_COPY_LATEST" == "1" ]]; then
      run_cmd scp -i "${ssh_key_expanded}" "${image_files[@]}" "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/Latest/"
    fi
  fi
  if (( ${#data_files[@]} > 0 )); then
    run_cmd scp -i "${ssh_key_expanded}" "${data_files[@]}" "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/data/${fcstdate}/"
  fi
fi

if [[ "$PUBLISH_UPDATE_HTML" == "1" ]]; then
  local_in="${script_dir}/forecasts.${fcstdate}.html"
  local_out="${script_dir}/output.${fcstdate}.html"
  run_cmd scp -i "${ssh_key_expanded}" "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/forecasts.html" "${local_in}"
  run_cmd python3 "${script_dir}/updatehtmldates.py" --date "${fcstdate}" --input "${local_in}" --output "${local_out}"
  run_cmd scp -i "${ssh_key_expanded}" "${local_out}" "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/forecasts.html"
  run_cmd rm -f "${local_in}" "${local_out}"
fi

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

to_bool01() {
  local val="${1:-0}"
  case "${val,,}" in
    1|true|yes|on) echo "1" ;;
    *) echo "0" ;;
  esac
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
PUBLISH_DEST_HOST="${PUBLISH_DEST_HOST:-$(cfg_get 'pipeline.publish.dest_host' 'somclass23')}"
PUBLISH_DEST_DIR="${PUBLISH_DEST_DIR:-$(cfg_get 'pipeline.publish.dest_dir' '/data/web/kpegion/http/nmme/forecasts')}"
PUBLISH_SSH_KEY="${PUBLISH_SSH_KEY:-$(cfg_get 'pipeline.publish.ssh_key' '~/.ssh/id_ed25519')}"
PUBLISH_COPY_STATIC_ONCE="${PUBLISH_COPY_STATIC_ONCE:-$(cfg_get 'pipeline.publish.copy_static_once' '1')}"
PUBLISH_COPY_STATIC_FORCE="${PUBLISH_COPY_STATIC_FORCE:-$(cfg_get 'pipeline.publish.copy_static_force' '0')}"
PUBLISH_STATIC_CLIMO_SRC="${PUBLISH_STATIC_CLIMO_SRC:-$(cfg_get 'pipeline.publish.static_climatology_source' '/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020')}"
PUBLISH_STATIC_TERCILES_SRC="${PUBLISH_STATIC_TERCILES_SRC:-$(cfg_get 'pipeline.publish.static_terciles_source' '/data/esplab/shared/model/initialized/nmme/terciles/1991-2020')}"
PUBLISH_HINDCASTS_DEST_DIR="${PUBLISH_HINDCASTS_DEST_DIR:-$(cfg_get 'pipeline.publish.hindcasts_dest_dir' '')}"

PUBLISH_ENABLED="$(to_bool01 "$PUBLISH_ENABLED")"
PUBLISH_DRY_RUN="$(to_bool01 "$PUBLISH_DRY_RUN")"
PUBLISH_COPY_MONTHLY="$(to_bool01 "$PUBLISH_COPY_MONTHLY")"
PUBLISH_COPY_SEASONAL="$(to_bool01 "$PUBLISH_COPY_SEASONAL")"
PUBLISH_COPY_LATEST="$(to_bool01 "$PUBLISH_COPY_LATEST")"
PUBLISH_UPDATE_HTML="$(to_bool01 "$PUBLISH_UPDATE_HTML")"
PUBLISH_COPY_STATIC_ONCE="$(to_bool01 "$PUBLISH_COPY_STATIC_ONCE")"
PUBLISH_COPY_STATIC_FORCE="$(to_bool01 "$PUBLISH_COPY_STATIC_FORCE")"

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

if [[ -z "$PUBLISH_HINDCASTS_DEST_DIR" ]]; then
  if [[ "$PUBLISH_DEST_DIR" == */forecasts ]]; then
    PUBLISH_HINDCASTS_DEST_DIR="${PUBLISH_DEST_DIR%/forecasts}/hindcasts"
  else
    PUBLISH_HINDCASTS_DEST_DIR="${PUBLISH_DEST_DIR%/}/hindcasts"
  fi
fi

HINDCASTS_CLIMO_DEST="${PUBLISH_HINDCASTS_DEST_DIR%/}/climatology/monthly/1991-2020"
HINDCASTS_TERCILES_DEST="${PUBLISH_HINDCASTS_DEST_DIR%/}/terciles/seasonal/1991-2020"
HINDCASTS_MARKER="${PUBLISH_HINDCASTS_DEST_DIR%/}/.static_publish_complete_1991-2020"

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
  "mkdir -p \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Global \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/North\ America \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Venezuela \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Iran \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Mexico \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/Venezuela \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/Iran \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/Mexico"

if [[ "$PUBLISH_COPY_MONTHLY" == "1" ]]; then
  if [[ -d "${sourceDirMon}/images/anomalies" ]]; then
      # Copy monthly forecast images organized by region
      # Global images
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDirMon}"/images/anomalies/Global/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Global/" 2>/dev/null || true
      # North America images
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDirMon}"/images/anomalies/NorthAmerica/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/North\ America/" 2>/dev/null || true
      # Venezuela images
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDirMon}"/images/anomalies/Venezuela/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Venezuela/" 2>/dev/null || true
      # Iran images
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDirMon}"/images/anomalies/Iran/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Iran/" 2>/dev/null || true
      # Mexico images
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDirMon}"/images/anomalies/Mexico/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Mexico/" 2>/dev/null || true
  fi
fi

if [[ "$PUBLISH_COPY_SEASONAL" == "1" ]]; then
  if [[ -d "${sourceDirSeas}/images/terciles" ]]; then
      # Copy seasonal tercile maps organized by region
      for region in Venezuela Iran Mexico; do
        if [[ -d "${sourceDirSeas}/images/terciles/${region}" ]]; then
          run_cmd scp -r -i "${ssh_key_expanded}" "${sourceDirSeas}/images/terciles/${region}/." "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/${region}/"
        fi
      done
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

if [[ "$PUBLISH_COPY_STATIC_ONCE" == "1" ]]; then
  static_should_copy="1"

  if [[ "$PUBLISH_COPY_STATIC_FORCE" != "1" ]]; then
    if [[ "$PUBLISH_DRY_RUN" == "1" ]]; then
      echo "[DRY-RUN] ssh -i ${ssh_key_expanded} ${PUBLISH_DEST_HOST} test -f ${HINDCASTS_MARKER}"
      echo "[INFO] dry-run mode cannot verify marker existence; assuming static copy will run"
    else
      if ssh -i "${ssh_key_expanded}" "${PUBLISH_DEST_HOST}" "test -f '${HINDCASTS_MARKER}'"; then
        static_should_copy="0"
      fi
    fi
  fi

  if [[ "$static_should_copy" == "1" ]]; then
    if [[ ! -d "$PUBLISH_STATIC_CLIMO_SRC" ]]; then
      echo "ERROR: static climatology source does not exist: ${PUBLISH_STATIC_CLIMO_SRC}" >&2
      exit 1
    fi
    if [[ ! -d "$PUBLISH_STATIC_TERCILES_SRC" ]]; then
      echo "ERROR: static terciles source does not exist: ${PUBLISH_STATIC_TERCILES_SRC}" >&2
      exit 1
    fi

    echo "[INFO] publishing static climatology once to ${HINDCASTS_CLIMO_DEST}/"
    echo "[INFO] publishing static terciles once to ${HINDCASTS_TERCILES_DEST}/"
    run_cmd ssh -i "${ssh_key_expanded}" "${PUBLISH_DEST_HOST}" \
      "mkdir -p ${HINDCASTS_CLIMO_DEST} ${HINDCASTS_TERCILES_DEST}"
    run_cmd scp -r -i "${ssh_key_expanded}" "${PUBLISH_STATIC_CLIMO_SRC}/." "${PUBLISH_DEST_HOST}:${HINDCASTS_CLIMO_DEST}/"
    run_cmd scp -r -i "${ssh_key_expanded}" "${PUBLISH_STATIC_TERCILES_SRC}/." "${PUBLISH_DEST_HOST}:${HINDCASTS_TERCILES_DEST}/"
    run_cmd ssh -i "${ssh_key_expanded}" "${PUBLISH_DEST_HOST}" "touch '${HINDCASTS_MARKER}'"
  else
    echo "[INFO] static hindcast publish already completed; skipping (set copy_static_force=true to recopy)"
  fi
fi

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

FORECAST_ROOT="${NMME_FORECAST_ROOT:-$(cfg_get 'data.output.nmme_forecast' '/data/esplab/shared/model/initialized/nmme/forecast')}"

if [[ "$PUBLISH_ENABLED" != "1" ]]; then
  echo "[INFO] publish stage disabled; skipping"
  exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ssh_key_expanded="${PUBLISH_SSH_KEY/#\~/$HOME}"

sourceDir="${FORECAST_ROOT}/${fcstdate}"

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
while (( timeout > 0 )) && [[ -f "${sourceDir}/nmmefcst.lock" ]]; do
  sleep 60
  ((timeout -= 1))
done

if (( timeout == 0 )); then
  echo "ERROR: products may not be complete. Lock file still present for ${fcstdate}" >&2
  exit 1
fi

run_cmd ssh -i "${ssh_key_expanded}" "${PUBLISH_DEST_HOST}" \
  "mkdir -p \
      ${PUBLISH_DEST_DIR}/data/${fcstdate}/monthly \
      ${PUBLISH_DEST_DIR}/data/${fcstdate}/seasonal \
      ${PUBLISH_DEST_DIR}/data/${fcstdate}/tercile_probs \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Global \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/North\ America \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Venezuela \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Iran \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Mexico \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/Venezuela \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/Iran \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/Mexico \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/threshold_maps/Venezuela \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/threshold_maps/Iran \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/threshold_maps/Mexico \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/most_likely/Venezuela \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/most_likely/Iran \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/most_likely/Mexico \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/cpt_dominant/Venezuela \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/cpt_dominant/Iran \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/cpt_dominant/Mexico \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/seasonal_total_summary/Venezuela \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/seasonal_total_summary/Iran \
      ${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/seasonal_total_summary/Mexico"

if [[ "$PUBLISH_COPY_MONTHLY" == "1" ]]; then
  # Copy monthly anomaly NetCDF data files
  if [[ -d "${sourceDir}/data/monthly" ]]; then
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/data/monthly/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/data/${fcstdate}/monthly/" 2>/dev/null || true
  fi

  # Copy monthly forecast images organized by region
  if [[ -d "${sourceDir}/images/anomalies" ]]; then
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/images/anomalies/Global/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Global/" 2>/dev/null || true
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/images/anomalies/NorthAmerica/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/North\ America/" 2>/dev/null || true
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/images/anomalies/Venezuela/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Venezuela/" 2>/dev/null || true
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/images/anomalies/Iran/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Iran/" 2>/dev/null || true
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/images/anomalies/Mexico/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Mexico/" 2>/dev/null || true
  fi
fi

if [[ "$PUBLISH_COPY_SEASONAL" == "1" ]]; then
  # Copy seasonal anomaly and tercile probability NetCDF data files
  if [[ -d "${sourceDir}/data/seasonal" ]]; then
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/data/seasonal/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/data/${fcstdate}/seasonal/" 2>/dev/null || true
  fi
  if [[ -d "${sourceDir}/data/tercile_probs" ]]; then
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/data/tercile_probs/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/data/${fcstdate}/tercile_probs/" 2>/dev/null || true
  fi

  # Copy seasonal tercile probability maps organized by region
  if [[ -d "${sourceDir}/images/tercile_probs" ]]; then
      for region in Venezuela Iran Mexico; do
        if [[ -d "${sourceDir}/images/tercile_probs/${region}" ]]; then
          run_cmd scp -r -i "${ssh_key_expanded}" "${sourceDir}/images/tercile_probs/${region}/." "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/${region}/"
        fi
      done
  fi

  # Copy additional seasonal image products (threshold_maps, most_likely, cpt_dominant, seasonal_total_summary)
  for product in threshold_maps most_likely cpt_dominant seasonal_total_summary; do
    for region in Venezuela Iran Mexico; do
      if [[ -d "${sourceDir}/images/${product}/${region}" ]]; then
        run_cmd scp -r -i "${ssh_key_expanded}" \
          "${sourceDir}/images/${product}/${region}/." \
          "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/${product}/${region}/"
      fi
    done
  done
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

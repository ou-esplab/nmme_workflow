#!/usr/bin/env bash
set -Eeuo pipefail

cfg_get_regions() {
  # Returns space-separated region names from confignmme.yaml regions[].name
  local cfg="${NMME_CONFIG:-confignmme.yaml}"
  # Resolve relative path against project root (one level up from publish/)
  if [[ ! "$cfg" = /* ]]; then
    cfg="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/$cfg"
  fi
  python3 - "$cfg" <<'PY'
import sys, yaml
try:
    with open(sys.argv[1]) as f:
        data = yaml.safe_load(f) or {}
    print(" ".join(r["name"] for r in data.get("regions", [])))
except Exception:
    print("Venezuela Iran Mexico CONUS")
PY
}

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

# Region list from config — single source of truth
read -ra REGIONS <<< "$(cfg_get_regions)"

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

# Build remote directory list and create via xargs (avoids SSH quoting issues)
_mkdir_dirs=(
  "${PUBLISH_DEST_DIR}/data/${fcstdate}/monthly"
  "${PUBLISH_DEST_DIR}/data/${fcstdate}/seasonal"
  "${PUBLISH_DEST_DIR}/data/${fcstdate}/tercile_probs"
  "${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Global"
  "${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/NorthAmerica"
)
for _region in "${REGIONS[@]}"; do
  _mkdir_dirs+=("${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/${_region}")
  _mkdir_dirs+=("${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/${_region}")
  for _product in threshold_maps most_likely cpt_dominant seasonal_total_summary; do
    _mkdir_dirs+=("${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/${_product}/${_region}")
  done
done

_mkdir_cmd="mkdir -p"
for _d in "${_mkdir_dirs[@]}"; do
  _mkdir_cmd+=" $(printf '%q' "$_d")"
done
run_cmd ssh -i "${ssh_key_expanded}" "${PUBLISH_DEST_HOST}" "$_mkdir_cmd"

if [[ "$PUBLISH_COPY_MONTHLY" == "1" ]]; then
  # Copy monthly anomaly NetCDF data files
  if [[ -d "${sourceDir}/data/monthly" ]]; then
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/data/monthly/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/data/${fcstdate}/monthly/" 2>/dev/null || true
  fi

  # Copy monthly forecast images organized by region
  if [[ -d "${sourceDir}/images/anomalies" ]]; then
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/images/anomalies/Global/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/Global/" 2>/dev/null || true
      run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}"/images/anomalies/NorthAmerica/* "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/NorthAmerica/" 2>/dev/null || true
      for _region in "${REGIONS[@]}"; do
        run_cmd scp -i "${ssh_key_expanded}" "${sourceDir}/images/anomalies/${_region}/"* \
          "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/monthly/${_region}/" 2>/dev/null || true
      done
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
      for _region in "${REGIONS[@]}"; do
        if [[ -d "${sourceDir}/images/tercile_probs/${_region}" ]]; then
          run_cmd scp -r -i "${ssh_key_expanded}" "${sourceDir}/images/tercile_probs/${_region}/." "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/${_region}/"
        fi
      done
  fi

  # Copy additional seasonal image products (threshold_maps, most_likely, cpt_dominant, seasonal_total_summary)
  for _product in threshold_maps most_likely cpt_dominant seasonal_total_summary; do
    for _region in "${REGIONS[@]}"; do
      if [[ -d "${sourceDir}/images/${_product}/${_region}" ]]; then
        run_cmd scp -r -i "${ssh_key_expanded}" \
          "${sourceDir}/images/${_product}/${_region}/." \
          "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/images/${fcstdate}/seasonal/${_product}/${_region}/"
      fi
    done
  done
fi

# Render products_outputs.md -> HTML and publish to the top of the dest dir.
# Runs every publish so the docs stay in sync with the workflow version in use.
docs_md="${script_dir}/../docs/products_outputs.md"
if [[ -f "$docs_md" ]]; then
  if command -v pandoc >/dev/null 2>&1; then
    docs_html="$(mktemp /tmp/products_outputs.XXXXXX.html)"
    pandoc \
      --standalone \
      --metadata title="NMME Workflow: Products and Outputs" \
      --css "https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.5.1/github-markdown.min.css" \
      --variable "include-before=<div class=\"markdown-body\" style=\"max-width:960px;margin:40px auto;padding:0 20px\">" \
      --variable "include-after=</div>" \
      -o "${docs_html}" "${docs_md}"
    run_cmd scp -i "${ssh_key_expanded}" "${docs_html}" \
      "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/products_outputs.html"
    rm -f "${docs_html}"
    echo "[INFO] Published products_outputs.html to ${PUBLISH_DEST_DIR}/"
  else
    echo "[WARN] pandoc not found; skipping products_outputs.html publish"
  fi
else
  echo "[WARN] docs/products_outputs.md not found; skipping docs publish"
fi

# One-time: copy the HTML template to the destination if it doesn't exist yet.
# This allows a new dest_dir to work immediately without a manual copy.
_remote_html="${PUBLISH_DEST_DIR}/forecasts.html"
_local_html="${script_dir}/forecasts.remote.html"
if [[ -f "$_local_html" ]]; then
  if [[ "$PUBLISH_DRY_RUN" == "1" ]]; then
    echo "[DRY-RUN] would copy forecasts.remote.html -> ${_remote_html} if not present"
  elif ! ssh -i "${ssh_key_expanded}" "${PUBLISH_DEST_HOST}" "test -f '${_remote_html}'"; then
    run_cmd scp -i "${ssh_key_expanded}" "$_local_html" "${PUBLISH_DEST_HOST}:${_remote_html}"
    echo "[INFO] Initialized ${_remote_html}"
  fi
fi

# Always sync forecasts.html date list to match date directories that actually
# exist in images/ on the remote server.
_html_in="${script_dir}/forecasts.${fcstdate}.in.html"
_html_out="${script_dir}/forecasts.${fcstdate}.out.html"
if [[ "$PUBLISH_DRY_RUN" == "1" ]]; then
  echo "[DRY-RUN] would sync forecasts.html date list from ${PUBLISH_DEST_DIR}/images/"
else
  _remote_dates=$(ssh -i "${ssh_key_expanded}" "${PUBLISH_DEST_HOST}" \
    "ls '${PUBLISH_DEST_DIR}/images/' 2>/dev/null | grep -E '^[0-9]{6}$' | tr '\n' ' '" || true)
  if [[ -n "$_remote_dates" ]] && \
     scp -q -i "${ssh_key_expanded}" \
       "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/forecasts.html" "$_html_in" 2>/dev/null; then
    python3 "${script_dir}/updatehtmldates.py" \
      --date "${fcstdate}" \
      --dates "${_remote_dates}" \
      --input "$_html_in" \
      --output "$_html_out"
    scp -q -i "${ssh_key_expanded}" "$_html_out" \
      "${PUBLISH_DEST_HOST}:${PUBLISH_DEST_DIR}/forecasts.html"
    echo "[INFO] Synced forecasts.html date list: ${_remote_dates}"
  fi
  rm -f "$_html_in" "$_html_out"
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

#!/usr/bin/env bash
# NMME realtime ingest — ONLY recent months (windowed), strict parsing & robust timeouts.

set -Eeuo pipefail
shopt -s extglob
export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# -------------------------- Logger -------------------------- #
log() { echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"; }

# -------------------------- User knobs ---------------------- #
# How many recent months to consider (1 = only current calendar month)
RECENT_MONTHS=${RECENT_MONTHS:-1}

# Minimal bytes to accept a NetCDF as valid
MIN_BYTES=${MIN_BYTES:-4096}

# Root for downloads
DATA_ROOT="${DATA_ROOT:-/data/esplab/nmme-backup}"

# Config path (used for optional SFS settings)
NMME_CONFIG="${NMME_CONFIG:-confignmme.yaml}"
INIT_YYYYMM="${INIT_YYYYMM:-}"

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

NMME_LOG_LEVEL="${NMME_LOG_LEVEL:-$(cfg_get 'pipeline.log_level' 'info')}"
NMME_LOG_LEVEL="$(echo "$NMME_LOG_LEVEL" | tr '[:upper:]' '[:lower:]')"
case "$NMME_LOG_LEVEL" in
  debug|info|warn|error) ;;
  *) NMME_LOG_LEVEL="info" ;;
esac

log_debug() {
  if [[ "$NMME_LOG_LEVEL" == "debug" ]]; then
    log "$@"
  fi
}

# Networking
REQUEST_PAUSE=${REQUEST_PAUSE:-0.5}
SPIDER_TIMEOUT=${SPIDER_TIMEOUT:-4}
SPIDER_TRIES=${SPIDER_TRIES:-1}
DL_TIMEOUT=${DL_TIMEOUT:-30}
DL_TRIES=${DL_TRIES:-5}
HARD_TIMEOUT_PROBE=${HARD_TIMEOUT_PROBE:-12}
HARD_TIMEOUT_DOWNLOAD=${HARD_TIMEOUT_DOWNLOAD:-300}

# Restrict to one or more models while testing (space-separated), e.g.:
# export ONLY_MODELS="COLA-RSMAS-CCSM4 NCEP-CFSv2"
ONLY_MODELS="${ONLY_MODELS:-}"

# Optional SFS AWS forecast ingest
SFS_AWS_ENABLED="${SFS_AWS_ENABLED:-$(cfg_get 'pipeline.sfs.enabled' '1')}"
SFS_AWS_DRY_RUN="${SFS_AWS_DRY_RUN:-$(cfg_get 'pipeline.sfs.dry_run' '0')}"
SFS_VARS="${SFS_VARS:-$(cfg_get 'pipeline.sfs.variables' 'prec tref sst')}"

# Optional SFS reforecast sync (all vars in SFS_VARS)
SFS_REFORECAST_ENABLED="${SFS_REFORECAST_ENABLED:-$(cfg_get 'pipeline.sfs.reforecast_enabled' '1')}"
SFS_REFORECAST_DRY_RUN="${SFS_REFORECAST_DRY_RUN:-$(cfg_get 'pipeline.sfs.reforecast_dry_run' '0')}"
SFS_REFORECAST_SCRIPT="${SFS_REFORECAST_SCRIPT:-$(cfg_get 'pipeline.sfs.reforecast_script' "${SCRIPT_DIR}/download_sfs_reforecast_prec.sh")}"
SFS_REFORECAST_ROOT="${SFS_REFORECAST_ROOT:-$(cfg_get 'pipeline.sfs.reforecast_root' 's3://noaa-oar-sfsdev-pds/experiments/beta1/reforecast')}"
SFS_REFORECAST_MONTHS="${SFS_REFORECAST_MONTHS:-$(cfg_get 'pipeline.sfs.reforecast_months' '')}"
SFS_REFORECAST_START_YEAR="${SFS_REFORECAST_START_YEAR:-$(cfg_get 'pipeline.sfs.reforecast_start_year' '1991')}"
SFS_REFORECAST_END_YEAR="${SFS_REFORECAST_END_YEAR:-$(cfg_get 'pipeline.sfs.reforecast_end_year' '2100')}"
SFS_REFORECAST_MAX_DOWNLOADS="${SFS_REFORECAST_MAX_DOWNLOADS:-$(cfg_get 'pipeline.sfs.reforecast_max_downloads' '0')}"

# Optional SFS climatology refresh from local reforecast files (per-var)
SFS_CLIMO_ENABLED="${SFS_CLIMO_ENABLED:-$(cfg_get 'pipeline.sfs.climo_enabled' '1')}"
SFS_CLIMO_PYTHON="${SFS_CLIMO_PYTHON:-$(cfg_get 'pipeline.sfs.climo_python' 'python3')}"
SFS_CLIMO_SCRIPT="${SFS_CLIMO_SCRIPT:-$(cfg_get 'pipeline.sfs.climo_script' "${PROJECT_ROOT}/static/make_sfs_climo_from_reforecast.py")}"
SFS_CLIMO_INPUT_DIR="${SFS_CLIMO_INPUT_DIR:-$(cfg_get 'pipeline.sfs.climo_input_dir' "${DATA_ROOT}/NOAA-SFS/reforecast/prec")}" 
SFS_CLIMO_OUTPUT_DIR="${SFS_CLIMO_OUTPUT_DIR:-$(cfg_get 'pipeline.sfs.climo_output_dir' '/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020')}"
SFS_CLIMO_START_YEAR="${SFS_CLIMO_START_YEAR:-$(cfg_get 'pipeline.sfs.climo_start_year' '1991')}"
SFS_CLIMO_END_YEAR="${SFS_CLIMO_END_YEAR:-$(cfg_get 'pipeline.sfs.climo_end_year' '2020')}"

# Forecast file normalization
NMME_NORMALIZE_PYTHON="${NMME_NORMALIZE_PYTHON:-python3}"
NMME_NORMALIZE_SCRIPT="${NMME_NORMALIZE_SCRIPT:-./preprocess/normalize_nmme_forecast_vars.py}"

# --------------------- IRIDL roots & models ----------------- #
BASE_URL="https://iridl.ldeo.columbia.edu/SOURCES/.Models/.NMME"

declare -A MODELURL
MODELURL["NASA-GEOSS2S"]="${BASE_URL}/.NASA-GEOSS2S"
MODELURL["CanESM5"]="${BASE_URL}/.CanSIPS-IC4/.CanESM5"
MODELURL["GFDL-SPEAR"]="${BASE_URL}/.GFDL-SPEAR"
MODELURL["GEM5.2-NEMO"]="${BASE_URL}/.CanSIPS-IC4/.GEM5.2-NEMO"
MODELURL["NCEP-CFSv2"]="${BASE_URL}/.NCEP-CFSv2/.FORECAST/.EARLY_MONTH_SAMPLES"
MODELURL["NCAR-CESM1"]="${BASE_URL}/.NCAR-CESM1"
MODELURL["COLA-RSMAS-CCSM4"]="${BASE_URL}/.COLA-RSMAS-CCSM4"
MODELURL["COLA-RSMAS-CESM1"]="${BASE_URL}/.COLA-RSMAS-CESM1"

# Forecast-year bounds (sanity limits; not used for backfill here)
declare -A FCAST_START FCAST_END
FCAST_START["NASA-GEOSS2S"]=2017; FCAST_END["NASA-GEOSS2S"]=2026
FCAST_START["CanESM5"]=2024;     FCAST_END["CanESM5"]=2026
FCAST_START["GFDL-SPEAR"]=2020;  FCAST_END["GFDL-SPEAR"]=2026
FCAST_START["GEM5.2-NEMO"]=2024; FCAST_END["GEM5.2-NEMO"]=2026
FCAST_START["NCEP-CFSv2"]=2011;  FCAST_END["NCEP-CFSv2"]=2026
FCAST_START["NCAR-CESM1"]=2023;  FCAST_END["NCAR-CESM1"]=2026
FCAST_START["COLA-RSMAS-CCSM4"]=2011; FCAST_END["COLA-RSMAS-CCSM4"]=2026
FCAST_START["COLA-RSMAS-CESM1"]=2011; FCAST_END["COLA-RSMAS-CESM1"]=2026

# Known first available month (guard truly non-existent starts)
declare -A FIRST_MONTH
FIRST_MONTH["NCEP-CFSv2"]=3     # EARLY_MONTH_SAMPLES MONTHLY begins Mar 2011 [1](https://s2s.worldclimateservice.com/cfs2maps)[2](https://apdrc.soest.hawaii.edu/datadoc/cfsv2_mon_ts.php)

# Variables to fetch (heights mapped per model below)
variables=(prec olr tref sst h500 h200)

# ------------------------- Helpers -------------------------- #
valid_year()  { [[ "$1" =~ ^[0-9]{4}$ ]] && (( 10#$1 >= 1979 && 10#$1 <= curr_y )); }
valid_month() { [[ "$1" =~ ^[0-9]{1,2}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 12 )); }

month_name_uc() { date -d "$1-$2-01" +%b; }

# Pressure selector for heights (encoded parens)
p_selector_component() {
  local model="$1" var="$2"
  if [[ "$model" == "GEM5.2-NEMO" || "$model" == "CanESM5" || "$model" == "NCAR-CESM1" ]]; then
    case "$var" in
      h200) echo "/P/%28200%29VALUES"; return 0 ;;
      h500) echo "/P/%28500%29VALUES"; return 0 ;;
    esac
  fi
  echo ""
}

# Resolve branch, variable name on server, and any extra selector
resolve_var_and_branch() {
  local model="$1" var="$2"
  local branch var_for_url psel=""
  if [[ "$model" == "NCEP-CFSv2" ]]; then
    branch="/.MONTHLY"; var_for_url="$var"; echo "$branch|$var_for_url|$psel"; return 0
  fi
  if [[ "$model" == "CanESM5" || "$model" == "GEM5.2-NEMO" ]]; then
    branch="/.FORECAST/.MONTHLY"
    if [[ "$var" == "h200" || "$var" == "h500" ]]; then var_for_url="hgt"; psel="$(p_selector_component "$model" "$var")"
    else var_for_url="$var"; fi
    echo "$branch|$var_for_url|$psel"; return 0
  fi
  if [[ "$model" == "NCAR-CESM1" ]]; then
    branch="/.FORECAST/.MONTHLY"
    if [[ "$var" == "h200" || "$var" == "h500" ]]; then var_for_url="zg"; psel="$(p_selector_component "$model" "$var")"
    else var_for_url="$var"; fi
    echo "$branch|$var_for_url|$psel"; return 0
  fi
  if [[ "$model" =~ COLA ]]; then
    branch="/.MONTHLY"
    if [[ "$var" == "h200" ]]; then var_for_url="gz"; else var_for_url="$var"; fi
    echo "$branch|$var_for_url|$psel"; return 0
  fi
  branch="/.FORECAST/.MONTHLY"; var_for_url="$var"; echo "$branch|$var_for_url|$psel"
}

# Build data URL
build_url() {
  local model=$1 var=$2 y=$3 m=$4
  local base=${MODELURL[$model]}
  local mon=$(month_name_uc "$y" "$m")
  IFS='|' read -r branch var_for_url psel <<<"$(resolve_var_and_branch "$model" "$var")"
  echo "${base}${branch}/.${var_for_url}${psel}/S/%280000%201%20${mon}%20${y}%29VALUES/data.nc"
}

# Probe a model/month using a lightweight endpoint (prec @ MONTHLY start)
probe_month_available() {
  local model=$1 y=$2 m=$3
  local base=${MODELURL[$model]}
  local mon=$(month_name_uc "$y" "$m")
  IFS='|' read -r branch var_for_url psel <<<"$(resolve_var_and_branch "$model" "prec")"
  local url="${base}${branch}/.${var_for_url}/S/%280000%201%20${mon}%20${y}%29VALUES/"
  log_debug "PROBE: $model $y-$(printf '%02d' "$m") -> $url"
  if ! timeout ${HARD_TIMEOUT_PROBE}s wget --spider --timeout=$SPIDER_TIMEOUT --tries=$SPIDER_TRIES --quiet "$url"; then
    log_debug "MISS : $model $y-$(printf '%02d' "$m") not posted (or probe timeout)"
    return 1
  fi
  sleep "$REQUEST_PAUSE"
  return 0
}

# Strict: latest YYYYMM for (model,var) by end-of-name match only
latest_local_yyyymm_for_var() {
  local model=$1 var=$2
  local max_yyyymm=${3:-0}
  local path="${DATA_ROOT}/${model}/forecast/${var}"
  local latest=0
  [[ -d "$path" ]] || { echo 0; return; }

  while IFS= read -r f; do
    local base; base=$(basename "$f")
    if [[ "$base" =~ _([0-9]{4})_([0-9]{2})\.nc$ ]]; then
      local y=$((10#${BASH_REMATCH[1]}))
      local m=$((10#${BASH_REMATCH[2]}))
      (( m>=1 && m<=12 )) || continue
      local key=$((10#$y*100 + 10#$m))
      if (( max_yyyymm > 0 && key > max_yyyymm )); then
        continue
      fi
      (( key > latest )) && latest=$key
    fi
  done < <(find "$path" -maxdepth 1 -type f -name "${var}_${model}_*.nc" 2>/dev/null)

  echo "$latest"
}

# Clamp window start to earliest valid month for a model
apply_model_min_start() {
  local model=$1 y=$2 m=$3
  y=$((10#$y)); m=$((10#$m))
  local sy=$((10#${FCAST_START[$model]}))
  local sm=1
  if [[ -n "${FIRST_MONTH[$model]:-}" ]]; then
    sm=$((10#${FIRST_MONTH[$model]}))
  fi
  # If window start precedes the model's first available month in its start year
  if (( y<sy )); then y=$sy; m=$sm; fi
  if (( y==sy && m<sm )); then m=$sm; fi
  echo "$y $m"
}

# --------------------------- MAIN --------------------------- #
if [[ -n "$INIT_YYYYMM" ]]; then
  if [[ ! "$INIT_YYYYMM" =~ ^[0-9]{6}$ ]]; then
    echo "ERROR: INIT_YYYYMM must be YYYYMM" >&2
    exit 2
  fi
  curr_y=$((10#${INIT_YYYYMM:0:4}))
  curr_m=$((10#${INIT_YYYYMM:4:2}))
else
  curr_y=$((10#$(date +%Y)))
  curr_m=$((10#$(date +%m)))
fi
now=$((10#$curr_y*100 + 10#$curr_m))
log "NOW : curr_y=${curr_y} curr_m=${curr_m} now=${now} init_override=${INIT_YYYYMM:-none}"

# Compute the earliest month in the recent window
win_y=$curr_y; win_m=$curr_m
for (( i=1; i<RECENT_MONTHS; i++ )); do
  win_m=$((win_m-1)); if (( win_m<1 )); then win_m=12; win_y=$((win_y-1)); fi
done
log "WIN : start=${win_y}-$(printf '%02d' "$win_m")  end=${curr_y}-$(printf '%02d' "$curr_m")  (RECENT_MONTHS=${RECENT_MONTHS})"

for model in "${!MODELURL[@]}"; do
  # Optional filter for debugging specific models
  if [[ -n "$ONLY_MODELS" ]]; then
    case " $ONLY_MODELS " in *" $model "*) : ;; *) continue ;; esac
  fi

  echo "=================================================="
  echo "Model: $model"
  echo "=================================================="

  # Apply model minimum start within the computed recent window
  read y0 m0 <<<"$(apply_model_min_start "$model" "$win_y" "$win_m")"
  log "ADJ : window for ${model} -> ${y0}-$(printf '%02d' "$m0") .. ${curr_y}-$(printf '%02d' "$curr_m")"

  model_probe_miss=0
  model_available_months=0
  model_attempted=0
  model_exists=0
  model_saved=0
  model_small_drop=0
  model_download_fail=0
  model_normalize_fail=0

  # Per-var: compute earliest month we care about = max(window_start, next_after_latest)
  declare -A START_FOR_VAR
  for var in "${variables[@]}"; do
    latest=$(latest_local_yyyymm_for_var "$model" "$var" "$now")
    # next_after_latest
    if (( latest > 0 )); then
      vy=$((10#$latest/100)); vm=$((10#$latest%100))
      vm=$((vm+1)); if (( vm>12 )); then vm=1; vy=$((vy+1)); fi
    else
      vy=$y0; vm=$m0
    fi
    # clamp to window start (no backfill before window)
    key_v=$((10#$vy*100 + 10#$vm))
    key_w=$((10#$y0*100 + 10#$m0))
    if (( key_v < key_w )); then vy=$y0; vm=$m0; fi

    if ! valid_year "$vy" || ! valid_month "$vm"; then
      log_debug "START: SKIP invalid per-var start model=${model} var=${var} -> y=${vy} m=${vm}"
      unset START_FOR_VAR["$var"]; continue
    fi
    log_debug "START: model=${model} var=${var} -> y=${vy} m=${vm} (window-aware)"
    START_FOR_VAR["$var"]="${vy} ${vm}"
  done

  # Month loop: walk ONLY the window [y0,m0] .. [curr_y,curr_m]
  y=$y0; m=$m0
  while true; do
    # stop after we pass the window end
    (( y>curr_y || (y==curr_y && m>curr_m) )) && break

    # Probe (do not stop the whole model on a miss; just continue within the window)
    if ! probe_month_available "$model" "$y" "$m"; then
      model_probe_miss=$((model_probe_miss+1))
      m=$((m+1)); if (( m>12 )); then m=1; y=$((y+1)); fi
      continue
    fi
    model_available_months=$((model_available_months+1))
    log_debug "OK  : $model $y-$(printf '%02d' "$m") is available"

    # For each var, download if this month >= var's window-aware start and file doesn't exist
    for var in "${variables[@]}"; do
      if [[ ! -v START_FOR_VAR[$var] ]]; then continue; fi
      read -r vy vm <<<"${START_FOR_VAR[$var]}"; vy=$((10#$vy)); vm=$((10#$vm))
      if (( y<vy || (y==vy && m<vm) )); then continue; fi

      outdir="${DATA_ROOT}/${model}/forecast/${var}"
      printf -v mm "%02d" "$m"
      outfile="${outdir}/${var}_${model}_${y}_${mm}.nc"
      if [[ -f "$outfile" ]]; then
        model_exists=$((model_exists+1))
        log_debug "SKIP: exists  ${outfile}"
        continue
      fi

      mkdir -p "$outdir"
      url="$(build_url "$model" "$var" "$y" "$m")"
      model_attempted=$((model_attempted+1))
      log_debug "GET : [$var] $url -> $outfile"

      # HTTPS first (bounded), then HTTP fallback (bounded)
      if ! timeout ${HARD_TIMEOUT_DOWNLOAD}s wget -q --timeout=$DL_TIMEOUT --tries=$DL_TRIES -O "$outfile" "$url"; then
        url_http="${url/https:\/\//http://}"
        log_debug "ALT : [$var] fallback HTTP -> $url_http"
        if ! timeout ${HARD_TIMEOUT_DOWNLOAD}s wget -q --timeout=$DL_TIMEOUT --tries=$DL_TRIES -O "$outfile" "$url_http"; then
          log "FAIL: [$var] download (HTTPS & HTTP) -> skipping"
          model_download_fail=$((model_download_fail+1))
          rm -f "$outfile"
          continue
        fi
      fi

      size=$(stat -c%s "$outfile" 2>/dev/null || echo 0)
      if (( size < MIN_BYTES )); then
        log "DROP: [$var] too small (${size} bytes) -> removing ${outfile}"
        model_small_drop=$((model_small_drop+1))
        rm -f "$outfile"
        continue
      fi

      log_debug "SAVE: [$var] ${outfile} (${size} bytes)"

      if [[ "$var" == "h200" || "$var" == "h500" ]]; then
        if ! PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}" "$NMME_NORMALIZE_PYTHON" "$NMME_NORMALIZE_SCRIPT" \
          --file "$outfile" \
          --root "$DATA_ROOT" \
          --model "$model" \
          --requested-var "$var" \
          --write
        then
          log "FAIL: [$var] normalization failed -> removing ${outfile}"
          model_normalize_fail=$((model_normalize_fail+1))
          rm -f "$outfile"
          continue
        fi
      fi

      model_saved=$((model_saved+1))

      sleep "$REQUEST_PAUSE"
    done

    # next month within window
    m=$((m+1)); if (( m>12 )); then m=1; y=$((y+1)); fi
  done

  log "SUM : ${model} available_months=${model_available_months} probe_miss=${model_probe_miss} attempted=${model_attempted} saved=${model_saved} exists=${model_exists} drop_small=${model_small_drop} download_fail=${model_download_fail} normalize_fail=${model_normalize_fail}"
done

log "All model updates complete."

SFS_SCRIPT_VERBOSE=0
if [[ "$NMME_LOG_LEVEL" == "debug" ]]; then
  SFS_SCRIPT_VERBOSE=1
fi

if [[ "$SFS_AWS_ENABLED" == "1" ]]; then
  log "SFS : running latest AWS SFS forecast ingest (vars: ${SFS_VARS})"
  for sfs_var in $SFS_VARS; do
    if ! DATA_ROOT="$DATA_ROOT" DRY_RUN="$SFS_AWS_DRY_RUN" VERBOSE="$SFS_SCRIPT_VERBOSE" LOCAL_VAR="$sfs_var" "${SCRIPT_DIR}/download_sfs_forecast_latest_prec.sh"; then
      log "SFS : WARN latest AWS SFS forecast ingest failed var=${sfs_var}; continuing"
    fi
  done
else
  log "SFS : disabled (set SFS_AWS_ENABLED=1 to enable)"
fi

if [[ "$SFS_REFORECAST_ENABLED" == "1" ]]; then
  log "SFS : syncing reforecast vars from AWS (vars: ${SFS_VARS})"
  if [[ ! -f "$SFS_REFORECAST_SCRIPT" ]]; then
    log "SFS : WARN reforecast script not found: ${SFS_REFORECAST_SCRIPT}; continuing"
  else
    for sfs_var in $SFS_VARS; do
      if ! DATA_ROOT="$DATA_ROOT" \
        DRY_RUN="$SFS_REFORECAST_DRY_RUN" \
        VERBOSE="$SFS_SCRIPT_VERBOSE" \
        LOCAL_VAR="$sfs_var" \
        S3_REFORECAST_ROOT="$SFS_REFORECAST_ROOT" \
        MONTHS="$SFS_REFORECAST_MONTHS" \
        START_YEAR="$SFS_REFORECAST_START_YEAR" \
        END_YEAR="$SFS_REFORECAST_END_YEAR" \
        MAX_DOWNLOADS="$SFS_REFORECAST_MAX_DOWNLOADS" \
        "$SFS_REFORECAST_SCRIPT"; then
        log "SFS : WARN reforecast sync failed var=${sfs_var}; continuing"
      fi
    done
  fi
else
  log "SFS : reforecast sync disabled (set SFS_REFORECAST_ENABLED=1 to enable)"
fi

if [[ "$SFS_CLIMO_ENABLED" == "1" ]]; then
  log "SFS : refreshing climatology from local reforecast (vars: ${SFS_VARS})"
  if [[ ! -f "$SFS_CLIMO_SCRIPT" ]]; then
    log "SFS : WARN climo script not found: ${SFS_CLIMO_SCRIPT}; continuing"
  else
    for sfs_var in $SFS_VARS; do
      case "$sfs_var" in
        tref) sfs_lev="2m" ;;
        *) sfs_lev="sfc" ;;
      esac
      sfs_verbose_arg=()
      if [[ "$SFS_SCRIPT_VERBOSE" == "1" ]]; then
        sfs_verbose_arg+=("--verbose")
      fi
      sfs_in_dir="${DATA_ROOT}/NOAA-SFS/reforecast/${sfs_var}"
      sfs_out_file="${SFS_CLIMO_OUTPUT_DIR}/NOAA-SFS.${sfs_var}_${sfs_lev}.clim.1991-2020.nc"
      if ! PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}" "$SFS_CLIMO_PYTHON" "$SFS_CLIMO_SCRIPT" \
        --model "NOAA-SFS" \
        --local-var "$sfs_var" \
        --input-dir "$sfs_in_dir" \
        --output-file "$sfs_out_file" \
        --start-year "$SFS_CLIMO_START_YEAR" \
        --end-year "$SFS_CLIMO_END_YEAR" \
        "${sfs_verbose_arg[@]}"; then
        log "SFS : WARN climo refresh failed var=${sfs_var}; continuing"
      fi
    done
  fi
else
  log "SFS : climo refresh disabled (set SFS_CLIMO_ENABLED=1 to enable)"
fi
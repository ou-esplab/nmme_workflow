#!/usr/bin/env bash
# NMME realtime ingest — ONLY recent months (windowed), strict parsing & robust timeouts.

set -Eeuo pipefail
shopt -s extglob
export LC_ALL=C

# -------------------------- Logger -------------------------- #
log() { echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"; }

# -------------------------- User knobs ---------------------- #
# How many recent months to consider (1 = only current calendar month)
RECENT_MONTHS=${RECENT_MONTHS:-1}

# Minimal bytes to accept a NetCDF as valid
MIN_BYTES=${MIN_BYTES:-4096}

# Root for downloads
DATA_ROOT="${DATA_ROOT:-/data/esplab/nmme-backup}"

# Networking
REQUEST_PAUSE=${REQUEST_PAUSE:-0.2}
SPIDER_TIMEOUT=${SPIDER_TIMEOUT:-4}
SPIDER_TRIES=${SPIDER_TRIES:-1}
DL_TIMEOUT=${DL_TIMEOUT:-10}
DL_TRIES=${DL_TRIES:-2}
HARD_TIMEOUT_PROBE=${HARD_TIMEOUT_PROBE:-12}
HARD_TIMEOUT_DOWNLOAD=${HARD_TIMEOUT_DOWNLOAD:-180}

# Restrict to one or more models while testing (space-separated), e.g.:
# export ONLY_MODELS="COLA-RSMAS-CCSM4 NCEP-CFSv2"
ONLY_MODELS="${ONLY_MODELS:-}"

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

month_name_uc() { date -d "$1-$2-01" +%b | tr '[:lower:]' '[:upper:]'; }

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
  log "PROBE: $model $y-$(printf '%02d' "$m") -> $url"
  if ! timeout ${HARD_TIMEOUT_PROBE}s wget --spider --timeout=$SPIDER_TIMEOUT --tries=$SPIDER_TRIES --quiet "$url"; then
    log "MISS : $model $y-$(printf '%02d' "$m") not posted (or probe timeout)"
    return 1
  fi
  sleep "$REQUEST_PAUSE"
  return 0
}

# Strict: latest YYYYMM for (model,var) by end-of-name match only
latest_local_yyyymm_for_var() {
  local model=$1 var=$2
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
curr_y=$((10#$(date +%Y)))
curr_m=$((10#$(date +%m)))
now=$((10#$curr_y*100 + 10#$curr_m))
log "NOW : curr_y=${curr_y} curr_m=${curr_m} now=${now}"

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

  # Per-var: compute earliest month we care about = max(window_start, next_after_latest)
  declare -A START_FOR_VAR
  for var in "${variables[@]}"; do
    latest=$(latest_local_yyyymm_for_var "$model" "$var")
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
      log "START: SKIP invalid per-var start model=${model} var=${var} -> y=${vy} m=${vm}"
      unset START_FOR_VAR["$var"]; continue
    fi
    log "START: model=${model} var=${var} -> y=${vy} m=${vm} (window-aware)"
    START_FOR_VAR["$var"]="${vy} ${vm}"
  done

  # Month loop: walk ONLY the window [y0,m0] .. [curr_y,curr_m]
  y=$y0; m=$m0
  while true; do
    # stop after we pass the window end
    (( y>curr_y || (y==curr_y && m>curr_m) )) && break

    # Probe (do not stop the whole model on a miss; just continue within the window)
    if ! probe_month_available "$model" "$y" "$m"; then
      m=$((m+1)); if (( m>12 )); then m=1; y=$((y+1)); fi
      continue
    fi
    log "OK  : $model $y-$(printf '%02d' "$m") is available"

    # For each var, download if this month >= var's window-aware start and file doesn't exist
    for var in "${variables[@]}"; do
      if [[ ! -v START_FOR_VAR[$var] ]]; then continue; fi
      read -r vy vm <<<"${START_FOR_VAR[$var]}"; vy=$((10#$vy)); vm=$((10#$vm))
      if (( y<vy || (y==vy && m<vm) )); then continue; fi

      outdir="${DATA_ROOT}/${model}/forecast/${var}"
      printf -v mm "%02d" "$m"
      outfile="${outdir}/${var}_${model}_${y}_${mm}.nc"
      if [[ -f "$outfile" ]]; then
        log "SKIP: exists  ${outfile}"
        continue
      fi

      mkdir -p "$outdir"
      url="$(build_url "$model" "$var" "$y" "$m")"
      log "GET : [$var] $url -> $outfile"

      # HTTPS first (bounded), then HTTP fallback (bounded)
      if ! timeout ${HARD_TIMEOUT_DOWNLOAD}s wget -q --timeout=$DL_TIMEOUT --tries=$DL_TRIES -O "$outfile" "$url"; then
        url_http="${url/https:\/\//http://}"
        log "ALT : [$var] fallback HTTP -> $url_http"
        if ! timeout ${HARD_TIMEOUT_DOWNLOAD}s wget -q --timeout=$DL_TIMEOUT --tries=$DL_TRIES -O "$outfile" "$url_http"; then
          log "FAIL: [$var] download (HTTPS & HTTP) -> skipping"
          rm -f "$outfile"
          continue
        fi
      fi

      size=$(stat -c%s "$outfile" 2>/dev/null || echo 0)
      if (( size < MIN_BYTES )); then
        log "DROP: [$var] too small (${size} bytes) -> removing ${outfile}"
        rm -f "$outfile"
        continue
      fi

      log "SAVE: [$var] ${outfile} (${size} bytes)"
      sleep "$REQUEST_PAUSE"
    done

    # next month within window
    m=$((m+1)); if (( m>12 )); then m=1; y=$((y+1)); fi
  done
done

echo "All model updates complete."
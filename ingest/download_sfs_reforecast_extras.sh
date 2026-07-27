#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Run this script in screen/tmux for long backfill operations.
# Example:
#   screen -S sfs_reforecast_backfill
#   source ~/.bashrc && conda activate nmme_workflow_env
#   cd /home/kpegion/projects/nmme_workflow
#   NMME_CONFIG=confignmme.yaml ingest/download_sfs_reforecast_extras.sh

log() { echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"; }

NMME_CONFIG="${NMME_CONFIG:-${PROJECT_ROOT}/confignmme.yaml}"
DATA_ROOT="${DATA_ROOT:-/data/esplab/nmme-backup}"
DRY_RUN="${DRY_RUN:-0}"
VERBOSE="${VERBOSE:-0}"
MODEL="${MODEL:-NOAA-SFS}"
TYPE="${TYPE:-reforecast}"

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
    with open(cfg_path, "r", encoding="utf-8") as f:
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

SFS_REFORECAST_SCRIPT="${SFS_REFORECAST_SCRIPT:-$(cfg_get 'pipeline.sfs.reforecast_script' "${SCRIPT_DIR}/download_sfs_reforecast_prec.sh")}" 
SFS_REFORECAST_ROOT="${SFS_REFORECAST_ROOT:-$(cfg_get 'pipeline.sfs.reforecast_root' 's3://noaa-oar-sfsdev-pds/experiments/beta1/reforecast')}"
SFS_REFORECAST_MONTHS="${SFS_REFORECAST_MONTHS:-${MONTHS:-$(cfg_get 'pipeline.sfs.reforecast_months' '')}}"
SFS_REFORECAST_START_YEAR="${SFS_REFORECAST_START_YEAR:-${START_YEAR:-$(cfg_get 'pipeline.sfs.reforecast_start_year' '1991')}}"
SFS_REFORECAST_END_YEAR="${SFS_REFORECAST_END_YEAR:-${END_YEAR:-$(cfg_get 'pipeline.sfs.reforecast_end_year' '2100')}}"
SFS_REFORECAST_MAX_DOWNLOADS="${SFS_REFORECAST_MAX_DOWNLOADS:-${MAX_DOWNLOADS:-$(cfg_get 'pipeline.sfs.reforecast_max_downloads' '0')}}"
SFS_REFORECAST_CORE_VARS="${SFS_REFORECAST_CORE_VARS:-$(cfg_get 'pipeline.sfs.reforecast_variables' 'prec tref sst')}"
SFS_REFORECAST_COUPLING_VARS="${SFS_REFORECAST_COUPLING_VARS:-$(cfg_get 'pipeline.sfs.coupling_reforecast_variables' "$(cfg_get 'pipeline.sfs.reforecast_extra_variables' 'u10m v10m ssu ssv')")}" 

if [[ ! -f "$SFS_REFORECAST_SCRIPT" ]]; then
  log "ERROR: reforecast script not found: ${SFS_REFORECAST_SCRIPT}"
  exit 1
fi

log "SFS reforecast coupling backfill starting"
log "Config: ${NMME_CONFIG}"
log "Core vars (check-only): ${SFS_REFORECAST_CORE_VARS}"
log "Coupling vars (download): ${SFS_REFORECAST_COUPLING_VARS}"
log "Root: ${SFS_REFORECAST_ROOT}"
log "Year range: ${SFS_REFORECAST_START_YEAR}-${SFS_REFORECAST_END_YEAR}"
if [[ -n "$SFS_REFORECAST_MONTHS" ]]; then
  log "Months filter: ${SFS_REFORECAST_MONTHS}"
fi
if [[ "$DRY_RUN" == "1" ]]; then
  log "DRY_RUN enabled"
fi

within_filters() {
  local y="$1"
  local m="$2"

  if (( 10#$y < 10#$SFS_REFORECAST_START_YEAR || 10#$y > 10#$SFS_REFORECAST_END_YEAR )); then
    return 1
  fi

  if [[ -n "$SFS_REFORECAST_MONTHS" ]]; then
    local m2
    m2="$(printf '%02d' "$((10#$m))")"
    local found=1
    IFS=',' read -ra month_list <<< "$SFS_REFORECAST_MONTHS"
    for item in "${month_list[@]}"; do
      local item2
      item2="$(printf '%02d' "$((10#${item}))")"
      if [[ "$item2" == "$m2" ]]; then
        found=0
        break
      fi
    done
    return $found
  fi

  return 0
}

declare -A union_keys=()
declare -A var_keys=()

collect_var_keys() {
  local sfs_var="$1"
  local path="${DATA_ROOT}/${MODEL}/${TYPE}/${sfs_var}"
  local count=0

  if [[ ! -d "$path" ]]; then
    return 0
  fi

  while IFS= read -r base; do
    [[ -n "$base" ]] || continue
    if [[ "$base" =~ _([0-9]{4})_([0-9]{2})\.nc$ ]]; then
      local y="${BASH_REMATCH[1]}"
      local m="${BASH_REMATCH[2]}"
      if within_filters "$y" "$m"; then
        local key="${y}_${m}"
        union_keys["$key"]=1
        var_keys["${sfs_var}:${key}"]=1
        count=$((count+1))
      fi
    fi
  done < <(find "$path" -maxdepth 1 -type f -name "${sfs_var}_${MODEL}_*.nc" -printf "%f\n")

  return 0
}

for sfs_var in $SFS_REFORECAST_CORE_VARS; do
  collect_var_keys "$sfs_var"
done

missing_core=0
union_count=${#union_keys[@]}

if (( union_count == 0 )); then
  log "WARN: no core reforecast files found in filtered range; cannot perform gap audit"
else
  for sfs_var in $SFS_REFORECAST_CORE_VARS; do
    missing_for_var=0
    for key in "${!union_keys[@]}"; do
      if [[ -z "${var_keys[${sfs_var}:${key}]:-}" ]]; then
        key_y="${key%_*}"
        key_m="${key#*_}"
        log "MISSING(core): var=${sfs_var} key=${key} expected_file=${DATA_ROOT}/${MODEL}/${TYPE}/${sfs_var}/${sfs_var}_${MODEL}_${key_y}_${key_m}.nc"
        missing_for_var=$((missing_for_var+1))
      fi
    done

    if (( missing_for_var > 0 )); then
      log "WARN: core var=${sfs_var} missing=${missing_for_var} keys (check-only; no redownload)"
      missing_core=$((missing_core+missing_for_var))
    else
      log "OK: core var=${sfs_var} complete over union_keys=${union_count}"
    fi
  done
fi

download_fail_count=0
for sfs_var in $SFS_REFORECAST_COUPLING_VARS; do
  log "Backfill var=${sfs_var}"
  if ! DATA_ROOT="$DATA_ROOT" \
    DRY_RUN="$DRY_RUN" \
    VERBOSE="$VERBOSE" \
    LOCAL_VAR="$sfs_var" \
    S3_REFORECAST_ROOT="$SFS_REFORECAST_ROOT" \
    MONTHS="$SFS_REFORECAST_MONTHS" \
    START_YEAR="$SFS_REFORECAST_START_YEAR" \
    END_YEAR="$SFS_REFORECAST_END_YEAR" \
    MAX_DOWNLOADS="$SFS_REFORECAST_MAX_DOWNLOADS" \
    "$SFS_REFORECAST_SCRIPT"; then
    log "WARN: coupling backfill failed var=${sfs_var}"
    download_fail_count=$((download_fail_count+1))
  fi
done

if (( missing_core > 0 )); then
  log "WARN: core-variable audit found missing files total=${missing_core} (check-only)"
fi

if (( download_fail_count > 0 )); then
  log "Completed with coupling backfill failures: ${download_fail_count} variable(s)"
  exit 3
fi

if (( missing_core > 0 )); then
  log "Completed with missing core files (notification condition)"
  exit 2
fi

log "Completed successfully"

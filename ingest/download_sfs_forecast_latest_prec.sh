#!/usr/bin/env bash

set -euo pipefail

# Export the latest NOAA SFS beta1 forecast init from AWS Zarr
# to NetCDF named like existing model forecast files:
#   <var>_NOAA-SFS_YYYY_MM.nc
# Output layout:
#   ${DATA_ROOT}/NOAA-SFS/forecast/prec/

DATA_ROOT="${DATA_ROOT:-/data/esplab/nmme-backup}"
MODEL="${MODEL:-NOAA-SFS}"
TYPE="${TYPE:-forecast}"
LOCAL_VAR="${LOCAL_VAR:-prec}"
REMOTE_VAR="${REMOTE_VAR:-}"
ZARR_DATASET="${ZARR_DATASET:-}"
S3_FORECAST_ROOT="${S3_FORECAST_ROOT:-s3://noaa-oar-sfsdev-pds/experiments/beta1/forecast}"
SFS_CYCLE="${SFS_CYCLE:-}"
S3_ZARR_URL="${S3_ZARR_URL:-}"
DRY_RUN="${DRY_RUN:-0}"
FORCE_OVERWRITE="${FORCE_OVERWRITE:-0}"
VERBOSE="${VERBOSE:-0}"

# Always set OUTDIR based on LOCAL_VAR (no fallback to 'prec')
OUTDIR="${DATA_ROOT}/${MODEL}/${TYPE}/${LOCAL_VAR}"
MANIFEST=""

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

log_debug() {
    if [[ "$VERBOSE" == "1" ]]; then
        log "$@"
    fi
}

emit_python_output() {
    local py_text="$1"
    local status outfile
    if [[ "$VERBOSE" == "1" ]]; then
        echo "$py_text"
        return
    fi

    status="$(printf '%s\n' "$py_text" | awk -F= '/^STATUS=/{print $2; exit}')"
    outfile="$(printf '%s\n' "$py_text" | awk -F= '/^OUTFILE=/{print $2; exit}')"
    if [[ -n "$status" || -n "$outfile" ]]; then
        log "SFS forecast ${LOCAL_VAR}: status=${status:-unknown} file=${outfile:-n/a}"
    fi
}

check_python_deps() {
  python3 - <<'PY'
import importlib.util
import sys

mods = ["xarray", "numpy", "fsspec", "s3fs", "zarr", "netCDF4"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    print("MISSING:" + ",".join(missing))
    sys.exit(1)
PY
}

set_var_defaults() {
    if [[ -z "$REMOTE_VAR" || -z "$ZARR_DATASET" ]]; then
        case "$LOCAL_VAR" in
            prec)
                REMOTE_VAR="${REMOTE_VAR:-pratesfc}"
                ZARR_DATASET="${ZARR_DATASET:-atm_monthly.zarr}"
                ;;
            tref)
                REMOTE_VAR="${REMOTE_VAR:-tmp2m}"
                ZARR_DATASET="${ZARR_DATASET:-atm_monthly.zarr}"
                ;;
            sst)
                REMOTE_VAR="${REMOTE_VAR:-SST}"
                ZARR_DATASET="${ZARR_DATASET:-ocn_monthly.zarr}"
                ;;
            h200)
                REMOTE_VAR="${REMOTE_VAR:-z200}"
                ZARR_DATASET="${ZARR_DATASET:-atm_monthly.zarr}"
                ;;
            h500)
                REMOTE_VAR="${REMOTE_VAR:-z500}"
                ZARR_DATASET="${ZARR_DATASET:-atm_monthly.zarr}"
                ;;
            u10m)
                REMOTE_VAR="${REMOTE_VAR:-u10m}"
                ZARR_DATASET="${ZARR_DATASET:-atm_monthly.zarr}"
                ;;
            v10m)
                REMOTE_VAR="${REMOTE_VAR:-v10m}"
                ZARR_DATASET="${ZARR_DATASET:-atm_monthly.zarr}"
                ;;
            ssu)
                REMOTE_VAR="${REMOTE_VAR:-SSU}"
                ZARR_DATASET="${ZARR_DATASET:-ocn_monthly.zarr}"
                ;;
            ssv)
                REMOTE_VAR="${REMOTE_VAR:-SSV}"
                ZARR_DATASET="${ZARR_DATASET:-ocn_monthly.zarr}"
                ;;
            *)
                REMOTE_VAR="${REMOTE_VAR:-$LOCAL_VAR}"
                ZARR_DATASET="${ZARR_DATASET:-atm_monthly.zarr}"
                ;;
        esac
    fi
}

main() {
    log_debug "DEBUG: OUTDIR=$OUTDIR LOCAL_VAR=$LOCAL_VAR REMOTE_VAR=$REMOTE_VAR ZARR_DATASET=$ZARR_DATASET"
  command -v python3 >/dev/null 2>&1 || {
    echo "ERROR: python3 not found" >&2
    exit 1
  }

    set_var_defaults
  MANIFEST="${OUTDIR}/manifest_${REMOTE_VAR}_latest_nc.txt"
  check_python_deps
  mkdir -p "$OUTDIR"
  chgrp esplab "$OUTDIR" 2>/dev/null || true
  chmod 775 "$OUTDIR" 2>/dev/null || true

  local py_out
  if [[ "$DRY_RUN" == "1" ]]; then
    py_out="$(python3 - "$S3_FORECAST_ROOT" "$SFS_CYCLE" "$S3_ZARR_URL" "$REMOTE_VAR" "$ZARR_DATASET" <<'PY'
import sys
import s3fs
import xarray as xr

s3_root, cycle, zarr_url_override, remote_var, zarr_dataset = sys.argv[1:]

if zarr_url_override:
    zarr_url = zarr_url_override
    cycle_used = cycle if cycle else "unknown"
else:
    fs = s3fs.S3FileSystem(anon=True)
    root = s3_root.replace("s3://", "").rstrip("/")
    if cycle:
        cycle_used = cycle
    else:
        cycle_dirs = fs.ls(root)
        cycles = sorted(
            [
                p.rstrip("/").split("/")[-1]
                for p in cycle_dirs
                if p.rstrip("/").split("/")[-1].isdigit()
            ]
        )
        if not cycles:
            raise RuntimeError(f"No forecast cycles found under {s3_root}")
        cycle_used = cycles[-1]
    zarr_url = f"s3://{root}/{cycle_used}/{zarr_dataset}"

try:
    ds = xr.open_zarr(
        zarr_url,
        consolidated=True,
        storage_options={"anon": True},
    )
except Exception:
    ds = xr.open_zarr(
        zarr_url,
        consolidated=False,
        storage_options={"anon": True},
    )

if remote_var not in ds:
    raise KeyError(f"Variable not found in zarr: {remote_var}")

y = cycle_used[0:4]
m = cycle_used[4:6]

print(f"S3_ZARR_URL={zarr_url}")
print(f"CYCLE={cycle_used}")
print(f"YYYY={y}")
print(f"MM={m}")
PY
    )"
    log "DRY_RUN: latest forecast init metadata"
    emit_python_output "$py_out"

    return 0
    fi

  py_out="$(python3 - "$S3_FORECAST_ROOT" "$SFS_CYCLE" "$S3_ZARR_URL" "$REMOTE_VAR" "$ZARR_DATASET" "$LOCAL_VAR" "$OUTDIR" "$MODEL" "$FORCE_OVERWRITE" <<'PY'

import os
import sys
import s3fs
import numpy as np
import pandas as pd
import xarray as xr

s3_root, cycle, zarr_url_override, remote_var, zarr_dataset, local_var, outdir, model, force_overwrite = sys.argv[1:]
force_overwrite = str(force_overwrite) == "1"

if zarr_url_override:
    zarr_url = zarr_url_override
    cycle_used = cycle if cycle else "unknown"
else:
    fs = s3fs.S3FileSystem(anon=True)
    root = s3_root.replace("s3://", "").rstrip("/")
    if cycle:
        cycle_used = cycle
    else:
        cycle_dirs = fs.ls(root)
        cycles = sorted(
            [
                p.rstrip("/").split("/")[-1]
                for p in cycle_dirs
                if p.rstrip("/").split("/")[-1].isdigit()
            ]
        )
        if not cycles:
            raise RuntimeError(f"No forecast cycles found under {s3_root}")
        cycle_used = cycles[-1]
    zarr_url = f"s3://{root}/{cycle_used}/{zarr_dataset}"

try:
    ds = xr.open_zarr(
        zarr_url,
        consolidated=True,
        storage_options={"anon": True},
    )
except Exception:
    ds = xr.open_zarr(
        zarr_url,
        consolidated=False,
        storage_options={"anon": True},
    )

if remote_var not in ds:
    raise KeyError(f"Variable not found in zarr: {remote_var}")

y = cycle_used[0:4]
m = cycle_used[4:6]
init_day = f"{y}-{m}-01"

# Ensure output directory exists
os.makedirs(outdir, exist_ok=True)
outfile = os.path.join(outdir, f"{local_var}_{model}_{y}_{m}.nc")
if os.path.exists(outfile) and not force_overwrite:
    print(f"OUTFILE={outfile}")
    print(f"S3_ZARR_URL={zarr_url}")
    print(f"INIT_DAY={init_day}")
    print("STATUS=exists")
    raise SystemExit(10)

da = ds[remote_var]

# NOAA-SFS precip is a rate field; convert to mm/day for workflow consistency.
if local_var == "prec":
    da = da * 86400.0
    da.attrs["units"] = "mm/day"

# Ensure an init-like axis exists for downstream compatibility.
if "init" not in da.dims and "S" not in da.dims:
    da = da.expand_dims(init=[pd.Timestamp(init_day)])

out = da.to_dataset(name=local_var)
tmpfile = outfile + ".part"
encoding = {
    local_var: {
        "zlib": True,
        "complevel": 1,
        "_FillValue": np.float32(np.nan),
    }
}
out.to_netcdf(tmpfile, format="NETCDF4", encoding=encoding)
os.replace(tmpfile, outfile)

print(f"OUTFILE={outfile}")
print(f"S3_ZARR_URL={zarr_url}")
print(f"INIT_DAY={init_day}")
print("STATUS=written")
PY
  )" || rc=$?

  rc=${rc:-0}
  if [[ "$rc" -eq 10 ]]; then
        emit_python_output "$py_out"
  elif [[ "$rc" -ne 0 ]]; then
    echo "$py_out"
    return "$rc"
  else
        emit_python_output "$py_out"
        local outfile_written
        outfile_written="$(printf '%s\n' "$py_out" | awk -F= '/^OUTFILE=/{print $2; exit}')"
        if [[ -n "$outfile_written" && -f "$outfile_written" ]]; then
            chgrp esplab "$outfile_written" 2>/dev/null || true
            chmod 775 "$outfile_written" 2>/dev/null || true
        fi
  fi

  {
    echo "s3_forecast_root=${S3_FORECAST_ROOT}"
        echo "zarr_dataset=${ZARR_DATASET}"
    echo "s3_zarr_url_override=${S3_ZARR_URL}"
    echo "cycle_override=${SFS_CYCLE}"
    echo "remote_var=${REMOTE_VAR}"
    echo "local_layout=${OUTDIR}"
    echo "filename_pattern=${LOCAL_VAR}_${MODEL}_YYYY_MM.nc"
    echo "completed_utc=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  } > "$MANIFEST"

  log "Manifest: ${MANIFEST}"
}

main "$@"

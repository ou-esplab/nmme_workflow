#!/usr/bin/env bash

set -euo pipefail

# Sync NOAA-SFS beta1 reforecast variables from AWS Zarr month stores
# into local NetCDF files named like the existing convention:
#   <var>_NOAA-SFS_YYYY_MM.nc

DATA_ROOT="${DATA_ROOT:-/data/esplab/nmme-backup}"
MODEL="${MODEL:-NOAA-SFS}"
TYPE="${TYPE:-reforecast}"
LOCAL_VAR="${LOCAL_VAR:-prec}"
REMOTE_VAR="${REMOTE_VAR:-}"
ZARR_DATASET="${ZARR_DATASET:-}"
S3_REFORECAST_ROOT="${S3_REFORECAST_ROOT:-s3://noaa-oar-sfsdev-pds/experiments/beta1/reforecast}"
MONTHS="${MONTHS:-}"
START_YEAR="${START_YEAR:-1991}"
END_YEAR="${END_YEAR:-2100}"
DRY_RUN="${DRY_RUN:-0}"
FORCE_OVERWRITE="${FORCE_OVERWRITE:-0}"
MAX_DOWNLOADS="${MAX_DOWNLOADS:-0}"
VERBOSE="${VERBOSE:-0}"

OUTDIR="${DATA_ROOT}/${MODEL}/${TYPE}/${LOCAL_VAR}"
MANIFEST=""

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

emit_python_output() {
    local py_text="$1"
    if [[ "$VERBOSE" == "1" ]]; then
        echo "$py_text"
        return
    fi

    # Quiet mode: keep only summary and warnings/errors.
    printf '%s\n' "$py_text" | awk '/^SUMMARY / || /^WARN: / || /^ERROR: / {print}'
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
            *)
                REMOTE_VAR="${REMOTE_VAR:-$LOCAL_VAR}"
                ZARR_DATASET="${ZARR_DATASET:-atm_monthly.zarr}"
                ;;
        esac
    fi
}

main() {
  command -v python3 >/dev/null 2>&1 || {
    echo "ERROR: python3 not found" >&2
    exit 1
  }

    set_var_defaults
    MANIFEST="${OUTDIR}/manifest_${REMOTE_VAR}_reforecast_sync.txt"
  check_python_deps
  mkdir -p "$OUTDIR"

  local py_out
  py_out="$(python3 - \
    "$S3_REFORECAST_ROOT" \
    "$REMOTE_VAR" \
    "$ZARR_DATASET" \
    "$LOCAL_VAR" \
    "$OUTDIR" \
    "$MODEL" \
    "$MONTHS" \
    "$START_YEAR" \
    "$END_YEAR" \
    "$DRY_RUN" \
    "$FORCE_OVERWRITE" \
    "$MAX_DOWNLOADS" <<'PY'
import os
import sys
import s3fs
import numpy as np
import xarray as xr

(
    s3_root,
    remote_var,
    zarr_dataset,
    local_var,
    outdir,
    model,
    months_csv,
    start_year,
    end_year,
    dry_run,
    force_overwrite,
    max_downloads,
) = sys.argv[1:]

start_year = int(start_year)
end_year = int(end_year)
dry_run = str(dry_run) == "1"
force_overwrite = str(force_overwrite) == "1"
max_downloads = int(max_downloads)

fs = s3fs.S3FileSystem(anon=True)
root = s3_root.replace("s3://", "").rstrip("/")

if months_csv.strip():
    month_dirs = [m.strip().zfill(2) for m in months_csv.split(",") if m.strip()]
else:
    listed = fs.ls(root)
    month_dirs = sorted(
        [
            p.rstrip("/").split("/")[-1]
            for p in listed
            if p.rstrip("/").split("/")[-1].isdigit()
        ]
    )

requested = 0
downloaded = 0
existing = 0
failed = 0
processed_months = []

for month in month_dirs:
    zarr_url = f"s3://{root}/{month}/{zarr_dataset}"
    zarr_key = f"{root}/{month}/{zarr_dataset}"
    if not fs.exists(zarr_key):
        print(f"SKIP: month={month} dataset={zarr_dataset} not available")
        continue

    try:
        ds = xr.open_zarr(
            zarr_url,
            consolidated=True,
            storage_options={"anon": True},
        )
    except Exception as e:
        try:
            ds = xr.open_zarr(
                zarr_url,
                consolidated=False,
                storage_options={"anon": True},
            )
        except Exception as e2:
            print(f"WARN: skip month={month} open_zarr failed: {e} / fallback: {e2}")
            failed += 1
            continue

    if remote_var not in ds:
        print(f"WARN: skip month={month} missing var={remote_var}")
        failed += 1
        continue
    if "init" not in ds.coords:
        print(f"WARN: skip month={month} missing init coord")
        failed += 1
        continue

    processed_months.append(month)
    init_vals = np.array(ds["init"].values).astype("datetime64[ns]")

    for init_val in init_vals:
        init_day = np.datetime_as_string(init_val, unit="D")
        y = int(init_day[0:4])
        m = init_day[5:7]

        if y < start_year or y > end_year:
            continue

        outfile = os.path.join(outdir, f"{local_var}_{model}_{y}_{m}.nc")
        requested += 1

        if os.path.exists(outfile) and not force_overwrite:
            existing += 1
            continue

        if dry_run:
            print(f"DRY_RUN: would write {outfile} from {zarr_url} init={init_day}")
            downloaded += 1
            if max_downloads > 0 and downloaded >= max_downloads:
                break
            continue

        try:
            da = ds[remote_var].sel(init=init_val)

            # NOAA-SFS precip is a rate field; convert to mm/day for climo/anomaly consistency.
            if local_var == "prec":
                da = da * 86400.0
                da.attrs["units"] = "mm/day"

            if "init" in da.coords:
                da = da.expand_dims(init=[init_val])
            else:
                da = da.expand_dims(init=[np.datetime64(init_day)])

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
            print(f"WRITE: {outfile}")
            downloaded += 1
        except Exception as e:
            print(f"WARN: write failed month={month} init={init_day}: {e}")
            failed += 1
            try:
                os.remove(outfile + ".part")
            except Exception:
                pass

        if max_downloads > 0 and downloaded >= max_downloads:
            break

    if max_downloads > 0 and downloaded >= max_downloads:
        break

print(f"SUMMARY requested={requested} downloaded={downloaded} existing={existing} failed={failed} months={processed_months}")
PY
  )"

    emit_python_output "$py_out"

  {
    echo "s3_reforecast_root=${S3_REFORECAST_ROOT}"
    echo "zarr_dataset=${ZARR_DATASET}"
    echo "remote_var=${REMOTE_VAR}"
    echo "months=${MONTHS}"
    echo "start_year=${START_YEAR}"
    echo "end_year=${END_YEAR}"
    echo "dry_run=${DRY_RUN}"
    echo "max_downloads=${MAX_DOWNLOADS}"
    echo "local_layout=${OUTDIR}"
    echo "filename_pattern=${LOCAL_VAR}_${MODEL}_YYYY_MM.nc"
    echo "completed_utc=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  } > "$MANIFEST"

  log "Manifest: ${MANIFEST}"
}

main "$@"

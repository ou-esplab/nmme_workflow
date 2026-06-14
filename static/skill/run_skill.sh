#!/usr/bin/env bash
# Run RPSS skill computation for prec and tref.
# Runs from the project root: bash static/skill/run_skill.sh

set -euo pipefail

PYTHON="${PYTHON:-python3}"
CONFIG="${CONFIG:-confignmme.yaml}"
HROOT="${HROOT:-/data/esplab/nmme-backup}"
CROOT="${CROOT:-/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020}"
OUTDIR="${OUTDIR:-/data/esplab/shared/model/initialized/nmme/skill/1991-2020}"

OBS_PRECIP="/data/esplab/shared/obs/gridded/atm/precip/monthly/CHIRPSv2/chirps-v2.0.monthly.nc"
OBS_TREF="/data/esplab/shared/obs/gridded/atm/temperature/monthly/air.mon.mean.nc"
OBS_SST="/data/esplab/shared/obs/gridded/ocn/SST/monthly/NOAA-ERSSTv5/sst.mnmean.nc"

echo "=== Skill computation (RPSS + ACC) ==="
echo "Output: ${OUTDIR}"

for var in prec tref sst; do
    if [ "${var}" = "prec" ]; then
        obs_arg="--obs-precip ${OBS_PRECIP}"
    elif [ "${var}" = "sst" ]; then
        obs_arg="--obs-sst ${OBS_SST}"
    else
        obs_arg="--obs-tref ${OBS_TREF}"
    fi

    echo ""
    echo "--- RPSS var=${var} ---"
    ${PYTHON} static/skill/compute_rpss.py \
        --config "${CONFIG}" \
        --var "${var}" \
        --hindcast-root "${HROOT}" \
        --clim-root "${CROOT}" \
        ${obs_arg} \
        --outdir "${OUTDIR}" \
        --start-year 1991 \
        --end-year 2020 \
        --overwrite

    echo ""
    echo "--- ACC var=${var} ---"
    ${PYTHON} static/skill/compute_acc.py \
        --config "${CONFIG}" \
        --var "${var}" \
        --hindcast-root "${HROOT}" \
        --clim-root "${CROOT}" \
        ${obs_arg} \
        --outdir "${OUTDIR}" \
        --start-year 1991 \
        --end-year 2020 \
        --overwrite
done

echo ""
echo "=== Done ==="

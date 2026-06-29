#!/usr/bin/env bash
# Compute RPSS and ACC skill for operational NMME forecast archive.
# Uses the same 1991-2020 climatology as hindcast skill for anomaly computation.
# Run from project root: bash static/skill/run_forecast_skill.sh

set -euo pipefail

PYTHON="${PYTHON:-python3}"
CONFIG="${CONFIG:-confignmme.yaml}"
HROOT="${HROOT:-/data/esplab/nmme-backup}"
CROOT="${CROOT:-/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020}"
OUTDIR="${OUTDIR:-/data/esplab/shared/model/initialized/nmme/skill/forecast}"
START_YEAR="${START_YEAR:-2011}"
END_YEAR="${END_YEAR:-2026}"

OBS_PRECIP="/data/esplab/shared/obs/gridded/atm/precip/monthly/CHIRPSv2/chirps-v2.0.monthly.nc"
OBS_TREF="/data/esplab/shared/obs/gridded/atm/temperature/monthly/air.mon.mean.nc"
OBS_SST="/data/esplab/shared/obs/gridded/ocn/SST/monthly/NOAA-ERSSTv5/sst.mnmean.nc"

mkdir -p "${OUTDIR}"

echo "=== Forecast skill computation (RPSS + ACC) ==="
echo "Data root:  ${HROOT}"
echo "Clim root:  ${CROOT}"
echo "Output:     ${OUTDIR}"
echo "Years:      ${START_YEAR}-${END_YEAR}"

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
        --start-year "${START_YEAR}" \
        --end-year "${END_YEAR}" \
        --forecast \
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
        --start-year "${START_YEAR}" \
        --end-year "${END_YEAR}" \
        --forecast \
        --overwrite
done

echo ""
echo "=== Generating forecast skill plots ==="
SKILLDIR="${OUTDIR}" OUTDIR="${OUTDIR}/plots" VARS="prec tref sst" \
    bash static/skill/run_plots.sh

echo ""
echo "=== All done $(date) ==="

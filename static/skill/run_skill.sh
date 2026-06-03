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

echo "=== RPSS skill computation ==="
echo "Output: ${OUTDIR}"

for var in prec tref; do
    echo ""
    echo "--- var=${var} ---"
    if [ "${var}" = "prec" ]; then
        obs_arg="--obs-precip ${OBS_PRECIP}"
    else
        obs_arg="--obs-tref ${OBS_TREF}"
    fi

    ${PYTHON} static/skill/compute_rpss.py \
        --config "${CONFIG}" \
        --var "${var}" \
        --hindcast-root "${HROOT}" \
        --clim-root "${CROOT}" \
        ${obs_arg} \
        --outdir "${OUTDIR}" \
        --start-year 1991 \
        --end-year 2020
done

echo ""
echo "=== Done ==="

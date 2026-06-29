#!/usr/bin/env bash
# Complete remaining SST skill computation and generate all plots.
# Run from project root: bash static/skill/run_completion.sh

set -euo pipefail

PYTHON="${PYTHON:-python3}"
CONFIG="${CONFIG:-confignmme.yaml}"
HROOT="${HROOT:-/data/esplab/nmme-backup}"
CROOT="${CROOT:-/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020}"
OUTDIR="${OUTDIR:-/data/esplab/shared/model/initialized/nmme/skill/1991-2020}"
OBS_SST="/data/esplab/shared/obs/gridded/ocn/SST/monthly/NOAA-ERSSTv5/sst.mnmean.nc"

echo "=== Completing SST RPSS $(date) ==="
${PYTHON} static/skill/compute_rpss.py \
    --config "${CONFIG}" --var sst \
    --hindcast-root "${HROOT}" --clim-root "${CROOT}" \
    --obs-sst "${OBS_SST}" --outdir "${OUTDIR}" \
    --start-year 1991 --end-year 2020

echo ""
echo "=== Completing SST ACC $(date) ==="
${PYTHON} static/skill/compute_acc.py \
    --config "${CONFIG}" --var sst \
    --hindcast-root "${HROOT}" --clim-root "${CROOT}" \
    --obs-sst "${OBS_SST}" --outdir "${OUTDIR}" \
    --start-year 1991 --end-year 2020

echo ""
echo "=== Generating all plots $(date) ==="
VARS="prec tref sst" bash static/skill/run_plots.sh

echo ""
echo "=== All done $(date) ==="

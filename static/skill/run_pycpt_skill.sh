#!/usr/bin/env bash
# Compute RPSS and ACC skill for PyCPT (CCA) bias-corrected hindcasts.
# Precipitation only for now; temperature support is planned.
# Run from project root: bash static/skill/run_pycpt_skill.sh
#
# This loops over every region/season/init_month/model combination, each of
# which is a separate CPT subprocess call -- a full sweep is slow. Restrict
# scope for a first test, e.g.:
#   REGIONS=CONUS SEASONS=MAM MODELS=NASA-GEOSS2S bash static/skill/run_pycpt_skill.sh

set -euo pipefail

PYTHON="${PYTHON:-python3}"
CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"
ENV_NAME="${ENV_NAME:-pycpt-2.8.2}"
CONFIG="${CONFIG:-confignmme.yaml}"
OUTDIR="${OUTDIR:-/data/esplab/shared/model/initialized/nmme/skill/pycpt}"
START_YEAR="${START_YEAR:-1991}"
END_YEAR="${END_YEAR:-2020}"
MAX_LEAD="${MAX_LEAD:-9}"

REGIONS="${REGIONS:-ALL}"
SEASONS="${SEASONS:-ALL}"
MODELS="${MODELS:-ALL}"
INIT_MONTHS="${INIT_MONTHS:-ALL}"

mkdir -p "${OUTDIR}"

echo "=== PyCPT bias-corrected hindcast skill (RPSS + ACC, var=prec) ==="
echo "Output:      ${OUTDIR}"
echo "Years:       ${START_YEAR}-${END_YEAR}"
echo "Regions:     ${REGIONS}"
echo "Seasons:     ${SEASONS}"
echo "Models:      ${MODELS}"
echo "Init months: ${INIT_MONTHS}"

CONDA_SH="$CONDA_BASE/etc/profile.d/conda.sh"
if [[ -f "$CONDA_SH" ]]; then
    set +u
    # shellcheck source=/dev/null
    source "$CONDA_SH"
    conda activate "$ENV_NAME"
    set -u
fi

${PYTHON} static/skill/compute_pycpt_skill.py \
    --config "${CONFIG}" \
    --var prec \
    --regions "${REGIONS}" \
    --seasons "${SEASONS}" \
    --models "${MODELS}" \
    --init-months "${INIT_MONTHS}" \
    --outdir "${OUTDIR}" \
    --start-year "${START_YEAR}" \
    --end-year "${END_YEAR}" \
    --max-lead "${MAX_LEAD}" \
    --overwrite

echo ""
echo "=== Done $(date) ==="

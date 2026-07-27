#!/usr/bin/env bash
# Generate all PyCPT skill PNG plots (RPSS + ACC) for each region/season/init_month
# that has data in the pycpt skill directory.
# Run from the project root: bash static/skill/run_pycpt_plots.sh

set -euo pipefail

PYTHON="${PYTHON:-python3}"
CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"
ENV_NAME="${ENV_NAME:-nmme_workflow_env}"
PYCPTDIR="${PYCPTDIR:-/data/esplab/shared/model/initialized/nmme/skill/pycpt}"
OUTDIR="${OUTDIR:-${PYCPTDIR}/plots}"
VAR="${VAR:-prec}"
SCORE="${SCORE:-both}"

CONDA_SH="$CONDA_BASE/etc/profile.d/conda.sh"
if [[ -f "$CONDA_SH" ]]; then
    set +u
    # shellcheck source=/dev/null
    source "$CONDA_SH"
    conda activate "$ENV_NAME"
    set -u
fi

echo "=== PyCPT skill plots ==="
echo "PyCPT dir: ${PYCPTDIR}"
echo "Output:    ${OUTDIR}"

for region_dir in "${PYCPTDIR}"/*/; do
    region=$(basename "$region_dir")
    [[ "$region" == "plots" ]] && continue

    for season in JFM FMA MAM AMJ MJJ JJA JAS ASO SON OND NDJ DJF Apr-Jul Apr-Sep Oct-Jan; do
        for init_month in $(seq 1 12); do
            # Check if at least one model file exists for this combination
            pattern="${region_dir}MME.${VAR}.${region}.${season}.init$(printf '%02d' ${init_month}).pycpt_skill.*.nc"
            # shellcheck disable=SC2086
            files=( ${pattern} )
            [[ -f "${files[0]}" ]] || continue

            echo "  [PLOT] ${region} / ${season} / init=$(printf '%02d' ${init_month})"
            ${PYTHON} static/skill/plot_pycpt_skill.py \
                --region "${region}" \
                --season "${season}" \
                --init-month "${init_month}" \
                --var "${VAR}" \
                --score "${SCORE}" \
                --pycptdir "${PYCPTDIR}" \
                --outdir "${OUTDIR}"
        done
    done
done

echo ""
echo "=== Done $(date) ==="

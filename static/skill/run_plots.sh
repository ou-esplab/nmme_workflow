#!/usr/bin/env bash
# Regenerate all RPSS skill plots for prec (and optionally tref).
# Runs from the project root: bash static/skill/run_plots.sh
#
# For each var, iterates over all init months and season leads that have
# data in the skill directory, generating one PNG per combination.

set -euo pipefail

PYTHON="${PYTHON:-python3}"
SKILLDIR="${SKILLDIR:-/data/esplab/shared/model/initialized/nmme/skill/1991-2020}"
OUTDIR="${OUTDIR:-${SKILLDIR}/plots}"
VARS="${VARS:-prec tref sst}"      # space-separated: "prec tref sst"

echo "=== RPSS plot generation ==="
echo "Skilldir: ${SKILLDIR}"
echo "Outdir:   ${OUTDIR}"

for var in ${VARS}; do
    echo ""
    echo "--- var=${var} ---"
    for init_month in $(seq 1 12); do
        for season_lead in $(seq 1 7); do
            # Check if at least one model file has data for this init/lead
            has_data=0
            for nc in "${SKILLDIR}"/*.${var}.rpss.*.nc; do
                [ -f "${nc}" ] || continue
                if ${PYTHON} -c "
import xarray as xr, sys
ds = xr.open_dataset('${nc}')
r = ds['rpss']
if ${init_month} in r.init_month.values and ${season_lead} in r.season_lead.values:
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
                    has_data=1
                    break
                fi
            done

            if [ "${has_data}" -eq 1 ]; then
                echo "  Plotting: init=${init_month}  season_lead=${season_lead}"
                ${PYTHON} static/skill/plot_rpss.py \
                    --var "${var}" \
                    --init-month "${init_month}" \
                    --season-lead "${season_lead}" \
                    --skilldir "${SKILLDIR}" \
                    --outdir "${OUTDIR}"
            fi
        done
    done
done

echo ""
echo "=== Done ==="

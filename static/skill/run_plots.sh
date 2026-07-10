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

echo "=== Skill plot generation (RPSS + ACC) ==="
echo "Skilldir: ${SKILLDIR}"
echo "Outdir:   ${OUTDIR}"

_plot_score() {
    local score="$1" var="$2" plot_script="$3" metric="$4"
    echo ""
    echo "--- ${score} var=${var} ---"
    for init_month in $(seq 1 12); do
        for season_lead in $(seq 1 7); do
            has_data=0
            for nc in "${SKILLDIR}"/*.${var}.${score}.*.nc; do
                [ -f "${nc}" ] || continue
                if ${PYTHON} -c "
import xarray as xr, sys
ds = xr.open_dataset('${nc}')
r = ds['${metric}']
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
                ${PYTHON} "static/skill/${plot_script}" \
                    --var "${var}" \
                    --init-month "${init_month}" \
                    --season-lead "${season_lead}" \
                    --skilldir "${SKILLDIR}" \
                    --outdir "${OUTDIR}"
            fi
        done
    done
}

for var in ${VARS}; do
    _plot_score rpss "${var}" plot_rpss.py rpss
    _plot_score acc  "${var}" plot_acc.py  acc
done

# Regional subsets of the raw skill (one region × init_month × season_lead per PNG)
REGIONS="${REGIONS:-CONUS Mexico Iran Venezuela C.Asia}"

_plot_score_regional() {
    local score="$1" var="$2" plot_script="$3" metric="$4" region="$5"
    echo ""
    echo "--- ${score} var=${var} region=${region} ---"
    for init_month in $(seq 1 12); do
        for season_lead in $(seq 1 7); do
            has_data=0
            for nc in "${SKILLDIR}"/*.${var}.${score}.*.nc; do
                [ -f "${nc}" ] || continue
                if ${PYTHON} -c "
import xarray as xr, sys
ds = xr.open_dataset('${nc}')
r = ds['${metric}']
if ${init_month} in r.init_month.values and ${season_lead} in r.season_lead.values:
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
                    has_data=1
                    break
                fi
            done

            if [ "${has_data}" -eq 1 ]; then
                ${PYTHON} "static/skill/${plot_script}" \
                    --var "${var}" \
                    --init-month "${init_month}" \
                    --season-lead "${season_lead}" \
                    --region "${region}" \
                    --skilldir "${SKILLDIR}" \
                    --outdir "${OUTDIR}"
            fi
        done
    done
}

echo ""
echo "=== Regional raw skill plots ==="
for region in ${REGIONS}; do
    for var in ${VARS}; do
        _plot_score_regional rpss "${var}" plot_rpss.py rpss "${region}"
        _plot_score_regional acc  "${var}" plot_acc.py  acc  "${region}"
    done
done

echo ""
echo "=== Done ==="

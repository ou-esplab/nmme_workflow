#!/bin/bash
set -euo pipefail

# Ensure conda environment is initialized and activated in noninteractive shell
if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
  conda activate pycpt-2.8.2
else
  echo "[ERROR] conda initialization script not found: $HOME/miniconda3/etc/profile.d/conda.sh" >&2
  exit 1
fi

CONFIG="./config.yaml"
UTILS="./nmme_fcst_utils.py"

# Load Python function to call utils
py() {
    python3 - "$@" <<EOF
import sys
from nmme_fcst_utils import *
EOF
}

# ------------------------------
# Load configuration
# ------------------------------
eval "$(python3 - <<EOF
import yaml
cfg = yaml.safe_load(open("$CONFIG"))
for k,v in cfg.items():
    print(f'{k}="{v}"')
EOF
)"

# ------------------------------
# Setup logging & lock
# ------------------------------
mkdir -p "$log_dir"
timestamp=$(date "+%Y%m%d_%H%M")
logfile="$log_dir/pipeline_${timestamp}.log"
lockfile="$log_dir/pipeline.lock"

python3 - <<EOF
from nmme_fcst_utils import make_lock
make_lock("$lockfile")
EOF

cleanup() {
    python3 - <<EOF
from nmme_fcst_utils import remove_lock
remove_lock("$lockfile")
EOF
}
trap cleanup EXIT ERR INT TERM

echo "==== NMME PIPELINE START $(date) ====" | tee -a "$logfile"

# ------------------------------
# Step 1: UPDATE FORECASTS
# ------------------------------
echo "Running NMME updater..." | tee -a "$logfile"
python3 - <<EOF
from nmme_fcst_utils import run_nmme_update
run_nmme_update("./nmme_update_fcsts.sh", "$logfile")
EOF

# ------------------------------
# Step 2: FIND NEW DATE
# ------------------------------
latest=$(python3 - <<EOF
from nmme_fcst_utils import load_config, find_latest_forecast
cfg = load_config("$CONFIG")
d = find_latest_forecast(cfg["nmme_data_root"])
if d is None:
    raise SystemExit("Could not determine latest forecast date.")
print(d)
EOF
)

echo "New forecast date detected: $latest" | tee -a "$logfile"

# ------------------------------
# Step 3: MAKE FORECAST PRODUCTS
# ------------------------------
#if [[ "$run_nmme_fcst_plots" == "true" ]]; then
echo "Running makefcsts.sh..." | tee -a "$logfile"
python3 - <<EOF
from nmme_fcst_utils import run_makefcsts
run_makefcsts("./makefcsts.sh", "$latest", "$logfile")
EOF

# ------------------------------
# Step 4: RUN PYCPT (optional)
# ------------------------------
#if [[ "$run_pycpt" == "true" ]]; then
echo "Running PyCPT..." | tee -a "$logfile"

python3 - <<EOF
from nmme_fcst_utils import load_config, run_pycpt
cfg = load_config("$CONFIG")

for region in cfg["pycpt_regions"]:
    name = region["name"]
    lat0, lat1 = region["lat"]
    lon0, lon1 = region["lon"]
    season = region["season"]

    run_pycpt(
        "./pycpt-seasonal_rt.py",
        "$latest",
        name,
        season,
        lat0, lat1,
        lon0, lon1,
        "$logfile"
    )
EOF

#fi

echo "==== PIPELINE COMPLETE $(date) ====" | tee -a "$logfile"


# NMME Seasonal Forecast Workflow – User Run Guide

This README explains **how to run the full NMME seasonal forecast workflow** from a user/operator perspective.

---

## What This Workflow Does

- Uses raw NMME hindcasts and forecasts
- Generates standardized ensemble‑mean forecast products
- Runs **deterministic CPT (CCA)** post‑processing

Probabilistic CPT is intentionally deferred.

---

## Prerequisites

Before running this workflow, ensure:

1. Raw NMME model data are available locally:
   ```
   data/local/root/MODEL/hindcast/
   data/local/root/MODEL/forecast/
   ```

2. Forecast ensemble‑mean products exist:
   ```
   data/output/nmme_monthly/YYYYMM/data/*.emean.nc
   ```

3. Python environment with:
   - pycpt / cptcore / cptio
   - xarray, numpy, pandas

Project utilities are organized under `utils/`:
   - `utils/nmme_pycpt_utils.py`
   - `utils/nmme_products_utils.py`
   - `utils/pycpt_utils.py`

Recommended install (reproducible from known-good environment):

```bash
conda create -n nmme_workflow_env --file environment.from-pycpt-2.8.2.yml
conda activate nmme_workflow_env
```

Alternative install from environment.yml (may solve slowly):

```bash
conda config --set channel_priority strict
conda env create -f environment.yml -n nmme_workflow_env --solver=libmamba
conda activate nmme_workflow_env
```

If environment creation fails with missing CPT packages, install explicitly from the IRI channel:

```bash
conda config --add channels iri-nextgen
conda install -n nmme_workflow_env cptbin cptcore cptio cptdl cptextras
```

If you see `SafetyError` for `cptcore` or `pycpt`, clear local conda caches and retry:

```bash
conda clean --packages --tarballs -y
conda env create -f environment.yml -n nmme_workflow_env --solver=libmamba
```

If conda solver is slow or hangs, use the libmamba solver:

```bash
conda install -n base -c conda-forge conda-libmamba-solver -y
conda env create -f environment.yml -n nmme_workflow_env --solver=libmamba
```

After activation, verify CPT tooling is available:

```bash
which CPT.x
python -c "import cptcore, cptio; print('ok')"
```

---

## Basic Command

Preferred new command (runner):

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601
```

This runs the full end-to-end pipeline: ingest, products, pycpt.
Default stages are: ingest, preprocess, products, pycpt.
To include publish, add it explicitly in --stages.

### SFS AWS Forecast Ingest (prec, tref, sst)

The ingest stage now also pulls the latest NOAA SFS beta1 AWS forecast
for precipitation, 2m temperature, and SST into:

```bash
<DATA_ROOT>/NOAA-SFS/forecast/<var>/<var>_NOAA-SFS_YYYY_MM.nc
```

Controls:

```bash
# disable SFS ingest
SFS_AWS_ENABLED=0

# run SFS in metadata-only mode
SFS_AWS_DRY_RUN=1

# override forecast root or cycle
S3_FORECAST_ROOT=s3://noaa-oar-sfsdev-pds/experiments/beta1/forecast
SFS_CYCLE=202603

# disable/enable SFS climo refresh
SFS_CLIMO_ENABLED=1

# disable/enable SFS reforecast sync (prec only)
SFS_REFORECAST_ENABLED=1
SFS_REFORECAST_DRY_RUN=0

# variables to sync from SFS
SFS_VARS="prec tref sst"

# optional reforecast sync filters
SFS_REFORECAST_MONTHS="03,05"
SFS_REFORECAST_START_YEAR=1991
SFS_REFORECAST_END_YEAR=2100
SFS_REFORECAST_MAX_DOWNLOADS=0

# climo build controls (prec reforecast -> NOAA-SFS.prec_sfc climo)
SFS_CLIMO_INPUT_DIR=/data/esplab/nmme-backup/NOAA-SFS/reforecast/prec
SFS_CLIMO_OUTPUT_FILE=/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/NOAA-SFS.prec_sfc.clim.1991-2020.nc
SFS_CLIMO_START_YEAR=1991
SFS_CLIMO_END_YEAR=2020
```

These same SFS controls can be managed in `confignmme.yaml` under
`pipeline.sfs`. Environment variables still take precedence when set.

Reforecast sync runs before SFS climatology refresh so newly posted
reforecast files can be incorporated into the climo update in the same run.

SST mapping detail: SFS SST is sourced from
`ocn_monthly.zarr` variable `SST`.

### Example

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601
```

### Dry-Run Mode

Validate stages without side effects using runner flags.

PyCPT dry-run (skips CPT execution):

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages pycpt --pycpt-dry-run
```

Products dry-run (does not write products):

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages products --products-dry-run
```

Publish dry-run (no SSH/SCP side effects):

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601 --stages publish --publish-dry-run
```

---

## What Happens During a Run

1. Raw hindcasts are loaded for each model
2. A single lead is selected based on season and initialization
3. Ensemble means are computed **in memory**
4. Deterministic CPT (CCA) is trained
5. Forecast predictors are prepared

No forecast application is performed yet.

---

## Current Capabilities

✅ Deterministic CPT training
✅ Dynamic hindcast ensemble means
✅ Model‑by‑model handling with missing‑model tolerance

---

## Deferred Capabilities

- Probabilistic CPT (terciles)
- Applying CPT model to forecasts
- Writing CPT forecast output files

---

## Exit Status

A successful run ends with:

```
[INFO] CPT-Core CCA training complete.
```

Any earlier error indicates configuration or data issues.

---

## Automated Execution via Cron

To run the workflow automatically on a schedule, use the wrapper script with cron's flock-based mutual exclusion locking to prevent concurrent runs.

### Wrapper Script

The script [scripts/run_nmme_workflow.sh](scripts/run_nmme_workflow.sh) provides:
- File-based locking (flock) to prevent overlapping executions
- Unified CLI + cron support via `--cron` flag
- Automatic logging to `logs/YYYYMMDD_HHMMSS/workflow.log`
- Safe fallback on lock contention (skips run if previous still active)

### Usage Examples

**Test CLI execution:**

```bash
cd /home/kpegion/projects/nmme_workflow
./scripts/run_nmme_workflow.sh --init 202602
```

**With options:**

```bash
# Full pipeline with publish
./scripts/run_nmme_workflow.sh --init 202602 --stages ingest preprocess products pycpt publish

# Dry-run
./scripts/run_nmme_workflow.sh --init 202602 --products-dry-run
```

### Crontab Setup

Add to your user's crontab:

```bash
crontab -e
```

Example cron entries (adjust time and init dates to your schedule):

```bash
# Run monthly on the 15th at 03:10 UTC (mid-month forecast)
10 3 15 * * /home/kpegion/projects/nmme_workflow/scripts/run_nmme_workflow.sh --init 202602 --cron 2>/dev/null

# Run on the 1st of each month at 02:30 UTC (early-month forecast)
30 2 1 * * /home/kpegion/projects/nmme_workflow/scripts/run_nmme_workflow.sh --init 202603 --cron 2>/dev/null

# With publish stage
10 3 15 * * /home/kpegion/projects/nmme_workflow/scripts/run_nmme_workflow.sh --init 202602 --stages ingest preprocess products pycpt publish --cron 2>/dev/null
```

### How Locking Works

`flock -n /path/to/.nmme.lock` ensures only one workflow instance runs at a time:

- If the lock is available: acquires it, runs the workflow, releases lock on exit
- If locked (previous run still active): returns immediately with exit code 1, cron logs it silently (via `2>/dev/null`)

This prevents concurrent runs from corrupting output files or doubling resource usage if the previous month's ~75-minute workflow overlaps with the next scheduled start.

### Cron Log Monitoring

Logs are written to:
```
logs/YYYYMMDD_HHMMSS/workflow.log
```

Monitor recent runs:

```bash
ls -lrt logs/
tail -f logs/$(ls -t logs/ | head -1)/workflow.log
```

Check cron execution history (may vary by system):

```bash
# macOS
log show --predicate 'process == "cron"' --last 1h

# Linux (if syslog enabled)
grep CRON /var/log/syslog | tail -20
```

---

## Regression Checklist

For post-refactor validation steps, run the checklist in [docs/REGRESSION_CHECKLIST.md](docs/REGRESSION_CHECKLIST.md).


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
conda create -n nmme_workflow_env --file environment.from-pycpt-2.8.2.lock.txt
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

Legacy wrapper (still available):

```bash
./nmme_pipeline.sh
```

### Example

```bash
python runners/cli.py --system nmme --config confignmme.yaml --init 202601
```

### Dry-Run Mode (NEW)

Use `--dry-run` to validate inputs and processing steps without requiring `cpt` binary or producing CPT outputs:

```bash
./pycpt-seasonal_rt.py --dry-run confignmme.yaml 202601 --only Mexico
```

Dry-run behavior:
- Loads and subsets predictand and hindcast data for region
- Computes sub-seasonal ensemble means
- Transforms inputs into CPTv10 candidate arrays (`X_train_v10`, `Y_v10`)
- Skips `CPT` execution
- Exits with status `0` if all checks pass

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

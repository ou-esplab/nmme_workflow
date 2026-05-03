
# NMME Seasonal Forecast Workflow – Design Documentation

This document describes the **architecture and design intent** of the NMME workflow.

---

## Workflow Stages

```
Raw NMME Data
   ↓
[01] Ingest
   ↓
[02] Preprocess
   ↓
[03] Forecast Products (ensemble‑mean anomalies)
   ↓
[04] PyCPT Post‑Processing
   ↓
[05] Publish (optional)
```

Primary entrypoint:

- `python runners/cli.py --system nmme --config confignmme.yaml --init YYYYMM`

- Default stages run: `ingest`, `preprocess`, `products`, `pycpt`.
- Publish is optional and run explicitly via `--stages ... publish`.

---

## Design Principles

- Do **not** write hindcast ensemble means to disk
- Make all CPT assumptions explicit
- Separate training from forecast application
- Avoid CPT axis guessing

---

## Hindcast Handling

- Raw hindcasts retain dimensions: (S, L, M, Y, X)
- Exactly one lead (L) is selected
- Ensemble mean computed dynamically over M
- L coordinate dropped
- Models stacked along predictor axis C

---

## Forecast Handling

- Forecast ensemble means are written earlier in pipeline
- Stored under `data/output/nmme_monthly`
- Used only for CPT forecast application

---

## CPTv10 Compliance

All CPT inputs are explicitly prepared to satisfy CPTv10:

- Dimensions: (T, C, Y, X)
- Numeric C axis
- Required attributes:
  - `missing`
  - `units`
- Explicit axis mapping passed to CPT

---

## Configuration‑Driven Behavior

All runtime behavior is controlled by `confignmme.yaml`:

- Model list
- Region definitions
- Seasons
- Path patterns
- I/O locations

No hard‑coded assumptions.

## Code Organization

- Shared workflow helpers are under `utils/`.
- PyCPT helpers live in `utils/nmme_pycpt_utils.py`.
- Product build, plotting, and write modules are composed via `utils/nmme_products_utils.py`.

---

## Static Files & Dependencies

The workflow depends on **two types of precomputed static files** that must be available before running products:

### 1. Climatology Files

- Purpose: Baseline climate statistics (mean fields) for anomaly computation
- Source: `static/make_sfs_climo_from_reforecast.py` (builds from hindcast reforecasts)
- Location: `/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/`
- Naming: `{model}.{var}_{level}.clim.1991-2020.nc` (e.g., `NOAA-SFS.prec_sfc.clim.1991-2020.nc`)
- Generated: One-time setup or periodic refresh when reforecasts update
- Required: Before products stage (anomalies cannot be computed without climatology reference)

### 2. Tercile Threshold Files

- Purpose: Model-specific tercile thresholds (33rd and 66th percentiles) for tercile probability maps
- Source: `static/precompute_tercile_thresholds.py` (computed from hindcast climatology)
- Location: `tercile_thresholds/` (relative to project root)
- Naming: `{model}.{var}.{season}.terciles.1991-2020.nc` (e.g., `NOAA-SFS.prec.MAM.terciles.1991-2020.nc`)
- Generated: One-time setup or periodic refresh if models/regions/seasons change
- Dependency: Requires valid climatology files (terciles computed from climatology statistics)

### Validation & Refresh

Use `scripts/check_static_files.py` to validate existing files:
```bash
python scripts/check_static_files.py --config confignmme.yaml \
   --climatology-root /data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020 \
   --tercile-root tercile_thresholds --verbose
```

Generate or refresh using runner stages:
```bash
# Generate climatology (one-time, ~15-30 min)
./scripts/run_nmme_workflow.sh --stages climatology

# Precompute tercile thresholds (one-time, ~5-10 min)
./scripts/run_nmme_workflow.sh --stages terciles
```

---

## Deferred Components

- Probabilistic CPT
- Forecast application and output writing
- Refactoring into train/apply phases

---

## Rationale

This design prioritizes reproducibility, clarity, and scientific correctness over convenience or automation.

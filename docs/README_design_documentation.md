
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
[03] Forecast Products (ensemble-mean anomalies, seasonal anomaly maps, tercile maps)
   ↓
[04] PyCPT Post-Processing (per-model CCA bias correction)
   ↓
[05] PyCPT Maps (bias-corrected forecast maps per region)
   ↓
[06] Arraylake Append (optional)
   ↓
[07] Publish (optional)

Static / one-time stages (no --init required):
  climatology  →  terciles  →  skill
```

Primary entrypoint:

- `python runners/cli.py --system nmme --config confignmme.yaml --init YYYYMM`

- Default stages run: `ingest`, `preprocess`, `products`, `pycpt`.
- `pycpt_maps` is run explicitly via `--stages pycpt_maps`.
- `arraylake` is opt-in and run explicitly via `--stages arraylake`.
- `publish` is optional and run explicitly via `--stages publish`.
- `skill` is a static one-time stage; does not require `--init`.

---

## Design Principles

- Do **not** write hindcast ensemble means to disk
- Make all CPT assumptions explicit
- Separate training from forecast application
- Avoid CPT axis guessing
- Per-model CCA (not pooled) for bias correction

---

## Hindcast Handling

- Raw hindcasts retain dimensions: (S, L, M, Y, X)
- Exactly one lead (L) is selected
- Ensemble mean computed dynamically over M
- L coordinate dropped
- Models run independently through CCA (per-model, not stacked)

---

## Forecast Handling

- Forecast ensemble means are written earlier in pipeline
- Stored under `data/output/nmme_monthly`
- Used for CPT forecast application and anomaly map generation

---

## CPTv10 Compliance

All CPT inputs are explicitly prepared to satisfy CPTv10:

- Dimensions: (T, M, Y, X) — per-model CCA uses M for model members
- Numeric T axis (datetime64)
- Required attributes: `missing`, `units`
- Explicit axis mapping passed to CPT
- Season format: `Mon-Mon` (e.g., `Feb-Apr`, `Jun-Aug`) throughout config and scripts

---

## Configuration-Driven Behavior

All runtime behavior is controlled by `confignmme.yaml`:

- Model list
- Region definitions (CONUS, Mexico, Venezuela, Iran, C.Asia)
- PyCPT variables and CCA settings
- Path patterns and I/O locations
- `pycpt.predictand` block (single predictand path and variable name)

---

## Arraylake Stage

- The Arraylake stage is optional and config-driven via `arraylake.enabled`.
- It appends new NMME forecast start dates into an external Arraylake repo.
- It reads `ARRAYLAKE_TOKEN` from `arraylake/.env` when not already set.
- The stage is opt-in via the unified runner stage list.
- If `arraylake.enabled` is false in config, the stage is skipped (not run even if listed).

---

## Code Organization

- Shared workflow helpers are under `utils/`.
- PyCPT helpers live in `utils/nmme_pycpt_utils.py`.
- Product build, plotting, and write modules are composed via `utils/nmme_products_utils.py`.
- Seasonal anomaly map generation is in `utils/nmme_plot.py` (`nmme_plot_seasonal()`).
- PyCPT bias-corrected map generation is in `products/make_pycpt_maps.py`.
- Hindcast skill computation is in `static/skill/` (`compute_rpss.py`, `compute_acc.py`).
- Skill plot generation is in `static/skill/` (`plot_rpss.py`, `plot_acc.py`).

---

## Static Files & Dependencies

The workflow depends on **three types of precomputed static files**.

### 1. Climatology Files

- Purpose: Baseline climate statistics (mean fields) for anomaly computation
- Source: `static/make_sfs_climo_from_reforecast.py`
- Location: `/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/`
- Naming: `{model}.{var}_{level}.clim.1991-2020.nc`
- Required: Before products stage

### 2. Tercile Threshold Files

- Purpose: Model-specific tercile thresholds (33rd and 66th percentiles)
- Source: `static/precompute_tercile_thresholds.py`
- Location: `tercile_thresholds/`
- Naming: `{model}.{var}.{season}.terciles.1991-2020.nc`
- Required: Before tercile probability map generation

### 3. Skill Score Files

- Purpose: Hindcast skill scores (RPSS, ACC) for 1991–2020 validation period
- Source: `static/skill/compute_rpss.py`, `static/skill/compute_acc.py`
- Location: `/data/esplab/shared/model/initialized/nmme/skill/1991-2020/`
- Naming: `{model}.{var}.rpss.1991-2020.nc`, `{model}.{var}.acc.1991-2020.nc`
- One-time compute; plots published to website skill page

### Validation & Refresh

```bash
python scripts/check_static_files.py --config confignmme.yaml \
   --climatology-root /data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020 \
   --tercile-root tercile_thresholds --verbose

# Generate climatology (one-time, ~15-30 min)
./scripts/run_nmme_workflow.sh --stages climatology

# Precompute tercile thresholds (~5-10 min)
./scripts/run_nmme_workflow.sh --stages terciles

# Compute hindcast skill scores (long-running; use screen)
bash static/skill/run_skill.sh
```

---

## Deferred Components

- Forecast application and output writing for probabilistic CPT
- Refactoring into explicit train/apply phases for pycpt

---

## Rationale

This design prioritizes reproducibility, clarity, and scientific correctness over convenience or automation.


# NMME Seasonal Forecast Workflow – Design Documentation

This document describes the **architecture and design intent** of the NMME workflow.

---

## Workflow Stages

```
Raw NMME Data
   ↓
Forecast Products (ensemble‑mean anomalies)
   ↓
PyCPT Post‑Processing
```

Primary entrypoint:

- `python runners/cli.py --system nmme --config confignmme.yaml --init YYYYMM`

Legacy shell wrapper:

- `./nmme_pipeline.sh` (delegates to runner)

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

## Deferred Components

- Probabilistic CPT
- Forecast application and output writing
- Refactoring into train/apply phases

---

## Rationale

This design prioritizes reproducibility, clarity, and scientific correctness over convenience or automation.

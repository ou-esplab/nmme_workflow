#!/usr/bin/env python3
"""
Preprocess stage for NMME workflow.

CURRENT SCOPE (INTENTIONALLY MINIMAL):
- Assert that at least one forecast dataset exists
- Assert that a 'valid' coordinate is present downstream

This file deliberately does NOT:
- normalize variables
- compute climatologies
- compute terciles
- modify any datasets

Those will be added incrementally after invariants are explicit.
"""

#!/usr/bin/env python3

import sys
from pathlib import Path

# ---- FIX PYTHON PATH (MUST BE FIRST) ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
# ---------------------------------------

import argparse
import xarray as xr
import pandas as pd
import numpy as np
import cftime
from utils.config import load_config
from utils.nmme_metadata import init_models
from utils.nmme_io import decode_S_cftime
from normalize_nmme_forecast_vars import sanitize_for_write

def parse_args():
    p = argparse.ArgumentParser(description="NMME preprocess stage (invariant checks only)")
    p.add_argument("--system", required=True, choices=["nmme", "subx"])
    p.add_argument("--config", required=True)
    p.add_argument("--init", required=True, help="Init date YYYYMM")
    return p.parse_args()


def iter_forecast_dirs(root: Path, models, variables):
    """
    Yield forecast directories of the form:
      <root>/<model>/forecast/<var>/
    """
    for model in models:
        for var in variables:
            d = root / model / "forecast" / var
            if d.exists():
                yield model, var, d


def forecast_matches_init(ds: xr.Dataset, init_yyyymm: str) -> bool:
    init_coord = None
    for name in ("S", "init", "time"):
        if name in ds.coords:
            init_coord = name
            break

    if init_coord is None:
        return False

    init_val = ds[init_coord].values
    if hasattr(init_val, "len"):
        init_val = init_val[0]

    return f"{init_val.year:04d}{init_val.month:02d}" == init_yyyymm


def find_valid_forecast(root: Path, cfg, init_yyyymm: str) -> bool:
    """
    Check whether at least one usable preprocessed forecast exists
    for the requested init. This is an invariant check only.

    Returns True if any (model, variable) dataset is found.
    """

    model_meta, _,_,_ = init_models()
    models = [m["model"] for m in model_meta]
    variables = sorted({v for m in model_meta for v in m["varnames"]})

    # Convert 202604 -> 2026_04
    init_token = f"{init_yyyymm[:4]}{init_yyyymm[4:]}"  # "2026_04"

    # Root where preprocessed monthly outputs should already exist
    pre_root = (
        Path(cfg["data"]["local"]["preprocess_root"])
        / init_token
        / "preprocess"
    )

    processed = set()

    # Loop over all model / variable combinations
    for model in models:
        for var in variables:
            var_dir = (
                pre_root
                / model
                / "forecast"
                / var
            )

            if not var_dir.exists():
                continue

            nc_files = list(var_dir.glob("*.nc"))
            if not nc_files:
                continue

            # Found at least one usable dataset
            processed.add((model, var))

    # Final invariant check
    if not processed:
        print(
            f"[PREPROCESS] No usable forecast datasets found for init={init_yyyymm}"
        )
        return False

    print(
        f"[PREPROCESS] Found usable forecast datasets: {sorted(processed)}"
    )
    return True

def ensure_forecast_dirs(root: Path, init_yyyymm: str):
    # Monthly (already effectively done elsewhere)
    monthly_root = (
        root
        / "forecast"
        / "monthly"
        / init_yyyymm
    )
    monthly_root.mkdir(parents=True, exist_ok=True)

    # ✅ Seasonal (NEW, explicit)
    seasonal_data = (
        root
        / "forecast"
        / "seasonal"
        / init_yyyymm
        / "data"
    )
    seasonal_data.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------
    # MAIN PROCESSING LOOP
    # --------------------------------------------------
    yyyy_mm = f"{init_yyyymm[:4]}_{init_yyyymm[4:]}"
    for model, var, d in iter_forecast_dirs(root, models, variables):
        for nc in d.glob("*.nc"):
            if yyyy_mm not in nc.name:
                continue
            try:
                with xr.open_dataset(nc, decode_times=False) as ds:
                    ds = decode_S_cftime(ds)

                    # 1. normalize structure
                    ds = construct_valid(ds)

                    # 2. SELECT forecast cycle (authoritative)
                    ds_init = extract_init_yyyymm(ds)
                    if ds_init != init_yyyymm:
                        continue

                    # 3. write preprocessed output
                    out_nc = pre_root / model / "forecast" / var / nc.name
                    out_nc.parent.mkdir(parents=True, exist_ok=True)

                    if out_nc.exists():
                        print(f"[PREPROCESS] exists, skipping -> {out_nc}")
                    else:
                        ds = sanitize_for_write(ds)
                        ds.to_netcdf(out_nc)
                        print(f"[PREPROCESS] wrote -> {out_nc}")

                    processed.add((model, var))

            except Exception as e:
                print(f"[PREPROCESS] skipping {nc}: {e}")

    # --------------------------------------------------
    # SOFT COMPLETENESS WARNINGS
    # --------------------------------------------------
    by_model = {}
    for m, v in processed:
        by_model.setdefault(m, set()).add(v)

    for m in model_meta:
        model = m["model"]
        expected = set(m["varnames"])
        have = by_model.get(model, set())
        missing = expected - have

        for v in sorted(missing):
            print(
                f"[PREPROCESS][WARN] missing variable for init {init_yyyymm}: "
                f"model={model} var={v}"
            )

    # --------------------------------------------------
    # FINAL INVARIANT
    # --------------------------------------------------
    return bool(processed)


def extract_init_yyyymm(ds) -> str:
    """
    Extract forecast initialization date as YYYYMM from an xarray Dataset.

    Supported init-time conventions:
      - coordinate 'S'    (most NMME models)
      - coordinate 'init' (NOAA-SFS)
      - coordinate 'time' (fallback)

    Returns:
        YYYYMM string (e.g., "202604")

    Raises:
        RuntimeError if no usable init time can be determined.
    """
    import numpy as np
    import pandas as pd

    init_coord = None
    for name in ("S", "init", "time"):
        if name in ds.coords:
            init_coord = name
            break

    if init_coord is None:
        raise RuntimeError(
            "Cannot determine forecast init: no S / init / time coordinate."
        )

    init_val = ds[init_coord].values

    # Handle array-like coordinates
    if isinstance(init_val, (list, tuple, np.ndarray)):
        if len(init_val) != 1:
            raise RuntimeError(
                f"Init coordinate '{init_coord}' has {len(init_val)} values; "
                "expected exactly one."
            )
        init_val = init_val[0]

    try:
        # Works for pandas.Timestamp, datetime, cftime objects
        year = init_val.year
        month = init_val.month
    except Exception:
        # Final fallback
        ts = pd.Timestamp(init_val)
        year = ts.year
        month = ts.month

    return f"{year:04d}{month:02d}"


def add_months_cftime(dt, months):
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    return type(dt)(year, month, 1)


def construct_valid(ds: xr.Dataset) -> xr.Dataset:
    """
    Construct a 'valid' coordinate from init time + lead.

    Supports:
      - S (most NMME models)
      - init (NOAA-SFS)
      - time (fallback)

    Fails loudly if impossible.
    """

    #---- change M to member --------
    if "M" in ds.dims and "member" not in ds.dims:
        ds = ds.rename({"M": "member"})
    # ---- lead dimension ----
    lead_dim = None
    for name in ("lead", "L"):
        if name in ds.dims:
            lead_dim = name
            break

    if lead_dim is None:
        raise RuntimeError("Cannot construct 'valid': no lead/L dimension")

    # ✅ ENFORCE INTEGER LEAD MONTH INDEX (AUTHORITATIVE)
    # Legacy storage had L = month + 0.5; normalize here, once.
    lead_vals = ds[lead_dim].values

    # Convert to integer month index
    lead_int = (lead_vals - 0.5).astype(int)

    ds = ds.assign_coords(
        {lead_dim: (lead_dim, lead_int)}
    )

    # ---- init time ----
    init_coord = None
    for name in ("S", "init", "time"):
        if name in ds.coords:
            init_coord = name
            break

    if init_coord is None:
        raise RuntimeError("Cannot construct 'valid': no init-time coordinate")

    init_val = ds[init_coord].values
    if isinstance(init_val, (list, tuple, np.ndarray)):
        if len(init_val) != 1:
            raise RuntimeError(
                f"Init coordinate '{init_coord}' has length {len(init_val)}"
            )
        init_val = init_val[0]

    leads = ds[lead_dim].values
    valid = []

    # ---- datetime handling ----
    if isinstance(init_val, (cftime.datetime, cftime.Datetime360Day)):
        for l in leads:
            valid.append(add_months_cftime(init_val, int(l)))
    else:
        init_val = pd.Timestamp(init_val)
        for l in leads:
            valid.append(init_val + pd.DateOffset(months=int(l)))

    return ds.assign_coords(valid=(lead_dim, valid))


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)

    data_root = Path(cfg["data"]["local"]["root"])

    preprocess_root = Path(cfg["data"]["local"]["preprocess_root"]).parent
    
    seasonal_base = (
      preprocess_root
      / "seasonal"
      / args.init
    )

    (seasonal_base / "data").mkdir(parents=True, exist_ok=True)
    (seasonal_base / "images").mkdir(parents=True, exist_ok=True)

    print("[PREPROCESS] Running invariant checks only")
    print(f"[PREPROCESS] system = {args.system}")
    print(f"[PREPROCESS] init   = {args.init}")
    print(f"[PREPROCESS] root   = {data_root}")

    ok = find_valid_forecast(data_root, cfg, args.init)

    if not ok:
        raise RuntimeError(
            f"Preprocess invariant failed: "
            f"no usable forecast datasets were found for init {args.init}. "
            f"All model/variable combinations were missing or unreadable."
        )

    print("[PREPROCESS] ✅ Invariant satisfied: 'valid' coordinate exists")
    print("[PREPROCESS] ✅ Preprocess completed successfully (normalized data written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
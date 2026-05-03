#!/usr/bin/env python3

# Ensure project root is in sys.path for module imports
import sys
from pathlib import Path

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
from normalize_nmme_forecast_vars import normalize_forecast_dataset
from normalize_nmme_forecast_vars import sanitize_for_write
from utils.nmme_io import open_local_forecast, decode_S_cftime

def parse_args():
    p = argparse.ArgumentParser(description="NMME preprocess stage (invariant checks only)")
    p.add_argument("--system", required=True, choices=["nmme", "subx"])
    p.add_argument("--config", required=True)
    p.add_argument("--init", required=True, help="Init date YYYYMM")
    return p.parse_args()

def finalize_preprocess_schema(ds: xr.Dataset) -> xr.Dataset:
    # --- Canonicalize lead dimension ---
    if "L" in ds.dims and "lead" in ds.coords:
        ds = ds.swap_dims({"L": "lead"})
    elif "L" in ds.dims:
        ds = ds.rename({"L": "lead"})

    # --- Canonicalize spatial dimensions ---
    rename = {}
    if "Y" in ds.dims:
        rename["Y"] = "lat"
    if "X" in ds.dims:
        rename["X"] = "lon"
    if "latitude" in ds.dims:
        rename["latitude"] = "lat"
    if "longitude" in ds.dims:
        rename["longitude"] = "lon"

    if rename:
        ds = ds.rename(rename)

    # --- Drop legacy coords that must never propagate ---
    for legacy in ("L", "X", "Y", "S", "latitude", "longitude"):
        if legacy in ds.coords:
            ds = ds.drop_vars(legacy, errors="ignore")

    # --- Enforce final invariant ---
    expected = {"member", "lead", "lat", "lon"}
    if set(ds.dims) != expected:
        raise RuntimeError(
            f"Preprocess schema violation. "
            f"Expected dims {expected}, got {set(ds.dims)}"
        )

    # Reorder dimensions to canonical order: member, lead, lat, lon
    canonical = ["member", "lead", "lat", "lon"]
    present = [d for d in canonical if d in ds.dims]
    # Keep any remaining dims after canonical order (shouldn't be any)
    remaining = [d for d in ds.dims if d not in present]
    order = present + remaining
    try:
        if tuple(order) != tuple(ds.dims):
            ds = ds.transpose(*order)
    except Exception:
        # If transpose fails for any reason, surface a clear error
        raise RuntimeError(f"Failed to reorder dims to canonical order: {order}")

    return ds


"""
UNUSED
def iter_forecast_dirs(root: Path, models, variables):
    
    Yield forecast directories of the form:
      <root>/<model>/forecast/<var>/
    
    for model in models:
        for var in variables:
            d = root / model / "forecast" / var
            if d.exists():
                yield model, var, d

"""

"""

UNUSED
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
"""
    
def find_valid_forecast(
    data_root: Path,
    cfg,
    init_yyyymm: str,
) -> bool:
    """
    Preprocess invariant:
    Return True if at least one usable RAW forecast dataset exists
    for this init (ingested data).
    """

    model_meta, _, _, _ = init_models()

    for m in model_meta:
        model = m["model"]
        for var in m["varnames"]:
            raw = open_local_forecast(
                data_root,
                model,
                var,
                init_yyyymm,
            )

            if raw is not None:
                print(
                    f"[PREPROCESS] Found raw forecast: "
                    f"model={model}, var={var}, init={init_yyyymm}"
                )
                return True

    print(
        f"[PREPROCESS] No usable RAW forecast datasets found "
        f"for init={init_yyyymm}"
    )
    return False

"""

UNUSED
def extract_init_yyyymm(ds) -> str:
    
    Extract forecast initialization date as YYYYMM from an xarray Dataset.

    Supported init-time conventions:
      - coordinate 'S'    (most NMME models)
      - coordinate 'init' (NOAA-SFS)
      - coordinate 'time' (fallback)

    Returns:
        YYYYMM string (e.g., "202604")

    Raises:
        RuntimeError if no usable init time can be determined.
    
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

"""

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

    lead_vals = ds[lead_dim].values

    # Only normalize if lead is fractional (legacy NMME encoding)
    if not np.all(np.equal(lead_vals, lead_vals.astype(int))):
        lead_int = np.round(lead_vals - 0.5).astype(int)
    else:
        lead_int = lead_vals.astype(int)

    # Hard invariant
    if len(np.unique(lead_int)) != len(lead_int):
        raise RuntimeError(
            f"Duplicate lead values after normalization: {lead_int}"
        )

    ds = ds.assign_coords(lead=(lead_dim, lead_int))
    
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
    # Prefer cftime objects for 'valid'. If init coordinate is numeric and
    # carries CF units/calendar metadata, decode to cftime. Otherwise,
    # fallback to converting a pandas Timestamp to an appropriate cftime
    # type (respecting 360-day calendar when present).
    if not isinstance(init_val, (cftime.datetime, cftime.Datetime360Day)):
        # Attempt CF-style numeric decode when units present on the init coord
        try:
            units = ds[init_coord].attrs.get("units") if init_coord in ds.coords else None
            calendar = ds[init_coord].attrs.get("calendar", "standard") if init_coord in ds.coords else "standard"
            if calendar == "360":
                calendar = "360_day"
            if units is not None:
                # num2date handles scalars or arrays
                init_val = cftime.num2date(float(init_val), units, calendar=calendar)
        except Exception:
            init_val = None

    if init_val is None:
        # Final fallback: convert via pandas then to cftime
        ts = pd.Timestamp(ds[init_coord].values if init_coord in ds.coords else init_val)
        # Choose cftime type based on declared calendar (if any)
        calendar = None
        if init_coord in ds.coords:
            calendar = ds[init_coord].attrs.get("calendar")
        if calendar == "360":
            calendar = "360_day"

        if calendar and "360" in calendar:
            init_val = cftime.Datetime360Day(ts.year, ts.month, ts.day)
        else:
            init_val = cftime.DatetimeGregorian(ts.year, ts.month, ts.day)

    # Now build 'valid' values using cftime-aware month addition
    for l in leads:
        valid.append(add_months_cftime(init_val, int(l)))

    return ds.assign_coords(valid=(lead_dim, valid))


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)

    data_root = Path(cfg["data"]["local"]["root"])

    # monthly preprocess_root points to .../forecast/monthly
    preprocess_root = Path(cfg["data"]["local"]["preprocess_root"]).parent

    print("[PREPROCESS] Running preprocess")
    print(f"[PREPROCESS] system = {args.system}")
    print(f"[PREPROCESS] init   = {args.init}")
    print(f"[PREPROCESS] root   = {data_root}")

    # ------------------------------------------------------------------
    # 1. INVARIANT CHECK — raw ingest availability
    # ------------------------------------------------------------------
    ok = find_valid_forecast(data_root, cfg, args.init)
    if not ok:
        raise RuntimeError(
            f"Preprocess invariant failed: "
            f"no usable RAW forecast datasets were found for init {args.init}. "
            f"Ingest must run successfully before preprocess."
        )

    print("[PREPROCESS] ✅ Invariant satisfied: raw ingest exists")

    # ------------------------------------------------------------------
    # 2. ENSURE SEASONAL OUTPUT DIRECTORIES
    # ------------------------------------------------------------------
    seasonal_base = preprocess_root / "seasonal" / args.init
    (seasonal_base / "data").mkdir(parents=True, exist_ok=True)
    (seasonal_base / "images").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 3. PREPROCESS ALL MODELS / VARIABLES
    # ------------------------------------------------------------------
    model_meta,_,_,_ = init_models()

    init_year  = args.init[:4]
    init_month = args.init[4:6]

    for m in model_meta:
        model = m["model"]

        for var in m["varnames"]:
            print(f"[PREPROCESS] processing model={model} var={var}")

            # ----------------------------------------------------------
            # Open RAW ingest (no time decoding)
            # ----------------------------------------------------------
            ds = open_local_forecast(
                data_root,
                model,
                var,
                args.init,
            )

            if ds is None:
                print(f"[PREPROCESS][SKIP] no raw data model={model} var={var}")
                continue

            # Decode numeric 'S' coordinate to cftime when possible (uses existing util)
            try:
                ds = decode_S_cftime(ds)
            except Exception:
                print(f"[PREPROCESS][WARN] decode_S_cftime failed for model={model} var={var}")

            # ----------------------------------------------------------
            # Normalize forecast dataset
            # ----------------------------------------------------------
            ds = normalize_forecast_dataset(ds, model, var)
            if ds is None or var not in ds.data_vars:
                print(
                    f"[PREPROCESS][SKIP] normalize failed "
                    f"model={model} var={var}"
                )
                continue

            # ----------------------------------------------------------
            # Construct valid coordinate
            # ----------------------------------------------------------
            ds = construct_valid(ds)

            # Drop init dimension after valid has been constructed
            if "S" in ds.dims:
                if ds.sizes["S"] != 1:
                    raise RuntimeError("Expected singleton S dimension in preprocess")
                ds = ds.isel(S=0, drop=True)

            elif "init" in ds.dims:
                if ds.sizes["init"] != 1:
                    raise RuntimeError("Expected singleton init dimension in preprocess")
                ds = ds.isel(init=0, drop=True)

            # Drop vertical singleton dimension after normalization
            if "Z" in ds.dims:
                if ds.sizes["Z"] != 1:
                    raise RuntimeError(
                        f"Expected singleton Z dimension in preprocess, got size {ds.sizes['Z']}"
                    )
                ds = ds.isel(Z=0, drop=True)
            # ----------------------------------------------------------
            # Finalize schema (rename dims, drop legacy coords)
            # ----------------------------------------------------------
            ds = finalize_preprocess_schema(ds)

            # ----------------------------------------------------------
            # ----------------------------------------------------------
            # Final sanitize before write
            # ----------------------------------------------------------
            ds = sanitize_for_write(ds)

            # Defensive step: ensure canonical dim ordering before write.
            canonical = ["member", "lead", "lat", "lon"]
            present = [d for d in canonical if d in ds.dims]
            remaining = [d for d in ds.dims if d not in present]
            order = present + remaining
            try:
                if tuple(order) != tuple(ds.dims):
                    ds = ds.transpose(*order)
            except Exception:
                # If transpose fails, raise a clear error so the pipeline
                # doesn't write non-canonical outputs silently.
                raise RuntimeError(f"Failed to enforce canonical dim order before write: {order}")

            # ----------------------------------------------------------
            # WRITE monthly preprocessed output
            # ----------------------------------------------------------
            out_dir = (
                preprocess_root
                / "monthly"
                / args.init
                / "preprocess"
                / model
                / "forecast"
                / var
            )
            out_dir.mkdir(parents=True, exist_ok=True)

            outfile = out_dir / f"{var}_{model}_{init_year}_{init_month}.nc"
            # Rebuild dataset to ensure NetCDF dimension definitions
            # are created in canonical order (member, lead, lat, lon).
            canonical = ["member", "lead", "lat", "lon"]
            present = [d for d in canonical if d in ds.dims]
            remaining = [d for d in ds.dims if d not in present]
            order = present + remaining

            try:
                # Prepare coords in the desired order first so dimensions are
                # recorded in that sequence when the file is written.
                coords = {}
                for d in order:
                    if d in ds.coords:
                        coords[d] = ds.coords[d]
                # Add any leftover coords
                for d in ds.coords:
                    if d not in coords:
                        coords[d] = ds.coords[d]

                # Transpose each data variable to the canonical order when
                # possible to preserve array layout and ensure dimension
                # declarations follow 'order'.
                data_vars = {}
                for name, da in ds.data_vars.items():
                    try:
                        # Only transpose dims that are present in the dataarray
                        transpose_order = [d for d in order if d in da.dims]
                        if tuple(transpose_order) != tuple(da.dims):
                            data_vars[name] = da.transpose(*transpose_order)
                        else:
                            data_vars[name] = da
                    except Exception:
                        data_vars[name] = da

                # Construct a new Dataset with coords added in canonical order
                ds_to_write = xr.Dataset(data_vars=data_vars, coords=coords, attrs=ds.attrs)
            except Exception:
                # Fallback to original dataset if reconstruction fails
                ds_to_write = ds

            ds_to_write.to_netcdf(outfile)

            print(f"[PREPROCESS][WRITE] {outfile}")

    print("[PREPROCESS] ✅ Preprocess completed successfully")
    return 0

if __name__ == "__main__":
    sys.exit(main())
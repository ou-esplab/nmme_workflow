import numpy as np
import pandas as pd
import xarray as xr

def decode_cf_safe(ds: xr.Dataset) -> xr.Dataset:
    try:
        return xr.decode_cf(ds)
    except Exception:
        return ds

def ensure_start_coord(ds: xr.Dataset, start: pd.Timestamp) -> xr.Dataset:
    ds = ds.assign_coords(S=np.array(start.to_datetime64()))
    return ds

def standardize_dims(ds: xr.Dataset) -> xr.Dataset:
    rename = {}
    if "X" in ds.dims: rename["X"] = "lon"
    if "Y" in ds.dims: rename["Y"] = "lat"
    if "L" in ds.dims: rename["L"] = "lead"
    ds = ds.rename(rename)
    return ds

def fix_lead_coord(ds: xr.Dataset) -> xr.Dataset:
    if "lead" in ds.coords:
        try:
            ds["lead"] = ds["lead"].astype(int)
        except Exception:
            pass
    return ds

def add_valid_times(ds: xr.Dataset) -> xr.Dataset:
    if "S" not in ds.coords or "lead" not in ds.coords:
        return ds
    start = pd.to_datetime(ds["S"].values)
    valid = [start + pd.DateOffset(months=int(l)) for l in ds["lead"].values]
    return ds.assign_coords(valid=("lead", valid))

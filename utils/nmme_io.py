# utils/nmme_io.py

from pathlib import Path
from typing import Optional
import xarray as xr
import pandas as pd

from utils.nmme_var_names import (
    forecast_storage_var_candidates,
    normalize_forecast_dataset,
)


def open_local_forecast(
    root: Path,
    model: str,
    var: str,
    time: pd.Timestamp,
) -> Optional[xr.Dataset]:

    fpath = None
    for storage_var in forecast_storage_var_candidates(model, var):
        candidate = (
            root
            / model
            / "forecast"
            / storage_var
            / f"{storage_var}_{model}_{time.year}_{time.month:02d}.nc"
        )
        if candidate.exists():
            fpath = candidate
            break

    if fpath is None:
        return None

    # Open WITHOUT CF decoding
    ds = xr.open_dataset(fpath, decode_times=False)

    # Decode only S safely
    ds = decode_S_cftime(ds)

    # Normalize external forecast files (e.g., NOAA-SFS) into workflow dims.
    # Expected downstream dims are S, M, L, Y, X.
    rename_map = {}
    if "init" in ds.dims or "init" in ds.coords:
        rename_map["init"] = "S"
    if "member" in ds.dims or "member" in ds.coords:
        rename_map["member"] = "M"
    if "lead" in ds.dims or "lead" in ds.coords:
        rename_map["lead"] = "L"
    if "lat" in ds.dims or "lat" in ds.coords:
        rename_map["lat"] = "Y"
    if "latitude" in ds.dims or "latitude" in ds.coords:
        rename_map["latitude"] = "Y"
    if "lon" in ds.dims or "lon" in ds.coords:
        rename_map["lon"] = "X"
    if "longitude" in ds.dims or "longitude" in ds.coords:
        rename_map["longitude"] = "X"
    if rename_map:
        ds = ds.rename(rename_map)

    # Normalize lead centers → integer indices
    if "L" in ds.coords:
        ds = ds.assign_coords(L=ds["L"].astype(int))

    ds = normalize_forecast_dataset(ds, model, var)

    return ds


def open_monthly_climatology(
    clim_root: Path,
    model: str,
    var: str,
    lev: str,
    raw: xr.Dataset,
    time: pd.Timestamp,
) -> Optional[xr.Dataset]:
    """
    Open climatology file and select appropriate month.
    """

    # Only add underscore if lev is not empty
    if lev:
        fpath = clim_root / f"{model}.{var}_{lev}.clim.1991-2020.nc"
    else:
        fpath = clim_root / f"{model}.{var}.clim.1991-2020.nc"
    print(f"[DEBUG] Trying to open climatology file: {fpath}")
    print(f"[DEBUG] Trying to open climatology file: {fpath}")

    if not fpath.exists():
        return None

    ds = xr.open_dataset(fpath)

    if "month" in ds.coords:
        month_val = int(time.month)
        months = set(int(m) for m in ds["month"].values.tolist())
        if month_val not in months:
            return None
        return ds.sel(month=month_val)

    return ds

import cftime
import numpy as np

def decode_S_cftime(ds):
    """
    Decode S time coordinate using cftime without touching other variables.
    """

    if "S" not in ds:
        return ds

    units = ds["S"].attrs.get("units")
    calendar = ds["S"].attrs.get("calendar", "standard")

    # If S is already decoded datetime-like (or units are absent), keep as-is.
    if units is None:
        return ds

    # Normalize malformed calendar
    if calendar == "360":
        calendar = "360_day"
        ds["S"].attrs["calendar"] = calendar

    # Decode numeric S values to cftime objects
    s_vals = ds["S"].values

    decoded = np.array(
        cftime.num2date(
            s_vals,
            units=units,
            calendar=calendar,
            only_use_cftime_datetimes=True,
        )
    )

    ds = ds.assign_coords(S=("S", decoded))

    return ds
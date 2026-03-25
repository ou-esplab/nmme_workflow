# utils/nmme_io.py

from pathlib import Path
import xarray as xr
import pandas as pd


def open_local_forecast(
    root: Path,
    model: str,
    var: str,
    time: pd.Timestamp,
) -> xr.Dataset | None:

    fpath = (
        root
        / model
        / "forecast"
        / var
        / f"{var}_{model}_{time.year}_{time.month:02d}.nc"
    )

    if not fpath.exists():
        return None

    # Open WITHOUT CF decoding
    ds = xr.open_dataset(fpath, decode_times=False)

    # Decode only S safely
    ds = decode_S_cftime(ds)

    # Normalize lead centers → integer indices
    if "L" in ds.coords:
        ds = ds.assign_coords(L=ds["L"].astype(int))

    return ds


def open_monthly_climatology(
    clim_root: Path,
    model: str,
    var: str,
    lev: str,
    raw: xr.Dataset,
    time: pd.Timestamp,
) -> xr.Dataset | None:
    """
    Open climatology file and select appropriate month.
    """

    fpath = clim_root / f"{model}.{var}_{lev}.clim.1991-2020.nc"
    if not fpath.exists():
        return None

    ds = xr.open_dataset(fpath)

    if "month" in ds.coords:
        return ds.sel(month=int(time.month))

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
# utils/nmme_io.py

from pathlib import Path
from typing import Optional
import xarray as xr
import pandas as pd
import cftime
import numpy as np


from utils.nmme_normalize import (
    forecast_storage_var_candidates,
    normalize_forecast_dataset,
)

def open_local_forecast(
    root: Path,
    model: str,
    var: str,
    init_yyyymm: str,
) -> Optional[xr.Dataset]:
    """
    Open a RAW ingested forecast file for one model/variable/init.
    Time decoding is disabled intentionally.
    """

    year = init_yyyymm[:4]
    month = init_yyyymm[4:6]

    base_dir = Path(root) / model / "forecast" / var
    if not base_dir.exists():
        return None

    # Preferred canonical filename (used by some models, e.g., NASA)
    expected = base_dir / f"{var}_{model}_{year}_{month}.nc"
    print(f"[DEBUG] looking for forecast file: {expected}")

    if expected.exists():
        filepath = expected
    else:
        # Relaxed fallback: variable name is NOT guaranteed in filename
        matches = list(base_dir.glob(f"*{year}_{month}.nc"))
        if not matches:
            return None
        filepath = matches[0]

    return xr.open_dataset(filepath, decode_times=False)
    
def open_monthly_climatology(
    clim_root: Path,
    model: str,
    var: str,
    lev: str,
    raw: xr.Dataset,
) -> Optional[xr.Dataset]:

    print(
        "[DIAG] open_monthly_climatology received dims:",
        raw.dims,
        "coords:",
        list(raw.coords)
    )

    # Build file path (unchanged)
    if lev:
        fpath = clim_root / f"{model}.{var}_{lev}.clim.1991-2020.nc"
    else:
        fpath = clim_root / f"{model}.{var}.clim.1991-2020.nc"

    if not fpath.exists():
        return None

    ds = xr.open_dataset(fpath)

    # Select the appropriate variable from the climatology file
    clim_da = ds[var]
    
    # ----------------------------
    # Final safety checks
    # ----------------------------
    assert "lead" in clim_da.dims, "Climatology missing lead dimension"
    assert "latitude" in clim_da.dims or "lat" in clim_da.dims, "Climatology missing latitude"
    assert "longitude" in clim_da.dims or "lon" in clim_da.dims, "Climatology missing longitude"

    return clim_da


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
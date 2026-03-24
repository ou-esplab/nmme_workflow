from __future__ import annotations
from pathlib import Path
import xarray as xr
from .cpt_dims import rename_to_cpt_dims

def decode_cf_time(ds: xr.Dataset, time_var: str) -> xr.Dataset:
    if time_var not in ds.coords:
        return ds

    ds = ds.copy()
    cal = ds[time_var].attrs.get("calendar")
    if cal == "360":
        ds[time_var].attrs["calendar"] = "360_day"

    ds = xr.decode_cf(ds, decode_times=True, use_cftime=True)

    if not hasattr(ds[time_var], "dt"):
        raise ValueError(f"Failed to decode time variable '{time_var}'")

    return ds

def open_netcdf_variable(path_glob: Path, var: str) -> xr.DataArray:
    files = sorted(path_glob.parent.glob(path_glob.name))
    if not files:
        raise FileNotFoundError(f"No NetCDF files matching {path_glob}")

    ds = (
        xr.open_mfdataset(files, combine="by_coords", decode_times=False)
        if len(files) > 1
        else xr.open_dataset(files[0], decode_times=False)
    )

    if "T" in ds.coords:
        ds = decode_cf_time(ds, "T")
    elif "S" in ds.coords:
        ds = decode_cf_time(ds, "S")

    if var not in ds:
        raise KeyError(f"{var} not found in {files[0]}")

    return rename_to_cpt_dims(ds[var])

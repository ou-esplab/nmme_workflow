from pathlib import Path
import xarray as xr
import pandas as pd
import numpy as np

def open_local_forecast(
    root: Path, model: str, var: str, time: pd.Timestamp
) -> xr.Dataset | None:
    f = root / model / "forecast" / var / f"{var}_{model}_{time.year}_{time.month:02d}.nc"
    if not f.exists():
        return None
    return xr.open_dataset(f, decode_times=False)

def open_monthly_climatology(
    root: Path, model: str, var: str, lev: str, raw: xr.Dataset, time: pd.Timestamp
) -> xr.Dataset | None:
    f = root / f"{model}.{var}_{lev}.clim.1991-2020.nc"
    if not f.exists():
        return None
    ds = xr.open_dataset(f)
    try:
        return ds.sel(month=int(time.month))
    except Exception:
        return ds.isel(month=int(time.month)-1)

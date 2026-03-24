from pathlib import Path
import pandas as pd
import xarray as xr

from utils.nmme_io import (
    open_local_forecast,
    open_monthly_climatology,
)
from utils.nmme_normalize import (
    decode_cf_safe,
    ensure_start_coord,
    standardize_dims,
    fix_lead_coord,
    add_valid_times,
)


def model_anomalies_for_month(
    data_root: Path,
    clim_root: Path,
    model: str,
    varname: str,
    levstr: str,
    target: pd.Timestamp,
) -> xr.Dataset | None:
    """
    Load local NMME forecast data for one model/variable/init month,
    subtract the 1991–2020 monthly climatology, and return anomalies.
    """

    # --- Open forecast ---
    raw = open_local_forecast(data_root, model, varname, target)
    if raw is None:
        return None

    # --- Normalize ---
    raw = decode_cf_safe(raw)
    raw = ensure_start_coord(raw, target)
    raw = standardize_dims(raw)
    raw = fix_lead_coord(raw)

    # --- Open climatology ---
    clim = open_monthly_climatology(
        clim_root, model, varname, levstr, raw, target
    )
    if clim is None:
        return None

    # --- Subtract climatology ---
    da_anom = raw[varname] - clim[varname]
    ds_out = da_anom.to_dataset(name=varname)

    # --- Final coords ---
    ds_out = ensure_start_coord(ds_out, target)
    ds_out = add_valid_times(ds_out)

    return ds_out
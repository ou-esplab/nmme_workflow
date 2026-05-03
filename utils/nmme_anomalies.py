# utils/nmme_anomalies.py - CLEAN VERSION WITH CORRECT MONTH SELECTION

import os
import pandas as pd
import xarray as xr
from pathlib import Path
from typing import Optional
import numpy as np

from utils.nmme_io import (
    open_local_forecast,
    open_monthly_climatology,
)
from utils.nmme_utils import decode_cf_safe
from utils.nmme_normalize import normalize_forecast_dataset

DEBUG_ANOM = os.getenv("NMME_DEBUG", "0") == "1"


def _debug(*args, **kwargs):
    if DEBUG_ANOM:
        print(*args, **kwargs)

def normalize_forecast_for_anomaly_math(fcst_da: xr.DataArray) -> xr.DataArray:
    """
    Normalize forecast for anomaly math.
    Keeps ensemble member dimension.
    """

    # Drop init dimension (legacy S or modern init)
    if "S" in fcst_da.dims:
        fcst_da = fcst_da.isel(S=0)
    elif "init" in fcst_da.dims:
        fcst_da = fcst_da.isel(init=0)

    # --- Canonicalize lead dimension ---
    if "L" in fcst_da.dims and "lead" in fcst_da.coords:
        fcst_da = fcst_da.swap_dims({"L": "lead"})
    elif "L" in fcst_da.dims:
        fcst_da = fcst_da.rename({"L": "lead"})

    
    # --- Canonicalize spatial dimension names ---
    rename = {}
    if "latitude" in fcst_da.dims:
        rename["latitude"] = "lat"
    if "longitude" in fcst_da.dims:
        rename["longitude"] = "lon"
    if "Y" in fcst_da.dims:
        rename["Y"] = "lat"
    if "X" in fcst_da.dims:
        rename["X"] = "lon"

    if rename:
        fcst_da = fcst_da.rename(rename)


    # Drop vertical singleton
    if "Z" in fcst_da.dims:
        fcst_da = fcst_da.isel(Z=0, drop=True)

    # ENSURE member is preserved
    assert "member" in fcst_da.dims, (
        "BUG: anomaly computation lost member dimension"
    )

    # Safety check
    if not fcst_da["lead"].to_index().is_unique:
        raise RuntimeError(
            "Duplicate lead values detected in anomaly input. "
            "This indicates a preprocess bug."
        )

    # Canonical order
    fcst_da = fcst_da.transpose("member", "lead", "lat", "lon")

    return fcst_da

def _latlon_dims(da):
    lat_dim = "lat" if "lat" in da.sizes else "latitude" if "latitude" in da.sizes else None
    lon_dim = "lon" if "lon" in da.sizes else "longitude" if "longitude" in da.sizes else None
    return lat_dim, lon_dim

def model_anomalies_for_month(
    data_root: Path,
    clim_root: Path,
    model: str,
    varname: str,
    levstr: str,
    init_yyyymm: str,
) -> Optional[xr.Dataset]:
    """
    Compute anomalies for ONE (model, variable, init month).
    """
    
    # ---------------------------
    # Open forecast (by init only)
    # ---------------------------
    raw = open_local_forecast(
        data_root,
        model,
        varname,
        init_yyyymm,
    )

    if raw is None:
        print(
            f"[MISSING-FILE] model={model} "
            f"init={init_yyyymm} var={varname}"
        )
        return None

    raw = decode_cf_safe(raw)

    # ---------------------------
    # Variable existence
    # ---------------------------
    if model == "NOAA-SFS":
        _debug("[DEBUG NOAA-SFS BEFORE normalize]")
        _debug("dims:", raw.dims)
        _debug("vars:", list(raw.data_vars))
        _debug("coords:", list(raw.coords))

    raw = normalize_forecast_dataset(raw, model, varname)

    if model == "NOAA-SFS":
        if raw is None:
            _debug("[DEBUG NOAA-SFS AFTER normalize] raw is None")
        else:
            _debug("[DEBUG NOAA-SFS AFTER normalize]")
            _debug("dims:", raw.dims)
            _debug("vars:", list(raw.data_vars))

    if raw is None or varname not in raw.data_vars:
        available = [] if raw is None else list(raw.data_vars)
        print(
            f"[MISSING-VAR] model={model} "
            f"init={init_yyyymm} var={varname} "
            f"available={available}"
        )
        return None

    # ---------------------------
    # Open climatology
    # ---------------------------
    _debug(
        "[DIAG] climatology input dims:",
        raw.dims,
        "coords:",
        list(raw.coords)
    )
    
    clim = open_monthly_climatology(
        clim_root, model, varname, levstr, raw
    )

    if clim is None:
        print(
           f"[MISSING-CLIM] model={model} "
          f"init={init_yyyymm} var={varname}"
        )
        return None

    # ---------------------------
    # Prepare forecast array
    # ---------------------------
    fcst_da = raw[varname]

    # Collapse init for arithmetic
    if "init" in fcst_da.dims:
        fcst_da = fcst_da.isel(init=0)

    # ---------------------------
    # Prepare climatology array
    # ---------------------------
    clim_da = clim

    # Rename climatology dims to match forecast dims
    rename_map = {}
    if "L" in clim_da.dims:
        rename_map["L"] = "lead"
    if "Y" in clim_da.dims:
        rename_map["Y"] = "lat"
    if "X" in clim_da.dims:
        rename_map["X"] = "lon"

    if rename_map:
        clim_da = clim_da.rename(rename_map)

    # **CRITICAL: Select correct months from climatology based on valid times**
    # Climatology files may only have certain months (e.g., [3,4,5] for MAM)
    # The month dimension contains month VALUES (1-12), not indices
    if "month" in clim_da.dims and "valid" in raw.coords:
        valid_times = raw["valid"].values
        clim_months = clim_da["month"].values  # Available month values from climatology
        indices_to_select = []
        
        for vt in valid_times:
            # Convert valid time to calendar month (1-12)
            if isinstance(vt, (int, np.integer)):
                # Numeric days since init
                init_str = init_yyyymm
                init_date = pd.to_datetime(f"{init_str[:4]}-{init_str[4:]}-01")
                valid_dt = init_date + pd.Timedelta(days=int(vt))
                calendar_month = valid_dt.month  # Returns 1-12
            else:
                # cftime object
                calendar_month = vt.month  # Returns 1-12
            
            # Find the position of this month in the climatology's month dimension
            try:
                month_pos = np.where(clim_months == calendar_month)[0][0]
            except IndexError:
                # Month not in climatology - use closest available
                print(
                    f"[WARN] Month {calendar_month} not in climatology months {list(clim_months)}. "
                    f"Using closest available month."
                )
                month_pos = np.argmin(np.abs(clim_months - calendar_month))
            
            indices_to_select.append(month_pos)
        
        # Select using position indices in the month dimension
        clim_da = clim_da.isel(month=indices_to_select)
    elif "month" in clim_da.dims:
        # No valid times available - use first month
        clim_da = clim_da.isel(month=0)

    # Drop any remaining non-spatial/non-lead dims
    clim_da = clim_da.isel(
        {d: 0 for d in clim_da.dims if d not in ("lead", "lat", "lon", "latitude", "longitude")}
    ).squeeze(drop=True)

    # Standardize dimension names
    if "latitude" in clim_da.dims:
        clim_da = clim_da.rename({"latitude": "lat"})
    if "longitude" in clim_da.dims:
        clim_da = clim_da.rename({"longitude": "lon"})

    # ---------------------------
    #  Standardize + subtract positionally
    # ---------------------------

    fcst_da = normalize_forecast_for_anomaly_math(fcst_da)
    clim_da = clim_da.reset_coords(drop=True)

    fcst_da_aligned, clim_da_aligned = xr.align(
        fcst_da,
        clim_da,
        join="override"
    )

    # Safe subtraction
    da_anom = fcst_da_aligned - clim_da_aligned
 
    if varname == "sst":
        lat_dim, lon_dim = _latlon_dims(da_anom)

        _debug("\n[DIAG][ANOM SST]")
        _debug("dims:", da_anom.dims)
        _debug("shape:", da_anom.shape)

        if lat_dim and lon_dim:
            _debug("lat dim:", lat_dim, "size:", da_anom.sizes[lat_dim])
            _debug("lon dim:", lon_dim, "size:", da_anom.sizes[lon_dim])
            _debug("lon coord length:", len(da_anom[lon_dim]))
        else:
            _debug("❌ Missing lat/lon dims")

    # ---------------------------
    # Restore metadata AFTER math
    # ---------------------------
    if "init" in raw.coords:
        da_anom = da_anom.expand_dims(init=[raw["init"].values[0]])

    da_anom = da_anom.assign_coords(
        valid=("lead", raw["valid"].values)
    )

    assert "valid" in da_anom.coords

    return da_anom.to_dataset(name=varname)

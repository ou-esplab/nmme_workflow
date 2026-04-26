# utils/nmme_anomalies.py

from curses import raw
from pyexpat import model

import pandas as pd
import xarray as xr
from pathlib import Path
from typing import Optional

from utils.nmme_io import (
    open_local_forecast,
    open_monthly_climatology,
)
from utils.nmme_utils import decode_cf_safe
from utils.nmme_normalize import normalize_forecast_dataset

def normalize_forecast_for_anomaly_math(fcst_da: xr.DataArray) -> xr.DataArray:
    """
    Normalize forecast for anomaly math.
    Keeps ensemble member dimension.
    """

    # Drop init dimension only
    if "S" in fcst_da.dims:
        fcst_da = fcst_da.isel(S=0)

    # Rename lead
    rename = {}
    if "L" in fcst_da.dims:
        rename["L"] = "lead"

    # Rename spatial dims
    if "Y" in fcst_da.dims:
        rename["Y"] = "lat"
    if "X" in fcst_da.dims:
        rename["X"] = "lon"

    if rename:
        fcst_da = fcst_da.rename(rename)

    if "Z" in fcst_da.dims:
        fcst_da = fcst_da.isel(Z=0, drop=True)

    # ENSURE member is preserved
    assert "member" in fcst_da.dims, (
        "BUG: anomaly computation lost member dimension"
    )

    # Ensure canonical order
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
        print("[DEBUG NOAA-SFS BEFORE normalize]")
        print("dims:", raw.dims)
        print("vars:", list(raw.data_vars))
        print("coords:", list(raw.coords))


    raw = normalize_forecast_dataset(raw, model, varname)

    if model == "NOAA-SFS":
        if raw is None:
            print("[DEBUG NOAA-SFS AFTER normalize] raw is None")
        else:
            print("[DEBUG NOAA-SFS AFTER normalize]")
            print("dims:", raw.dims)
            print("vars:", list(raw.data_vars))

    if raw is None or varname not in raw.data_vars:
        available = [] if raw is None else list(raw.data_vars)
        print(
            f"[MISSING-VAR] model={model} "
            f"init={init_yyyymm} var={varname} "
            f"available={available}"
        )
        return None

    # ---------------------------
    # Open climatology (month from valid)
    # ---------------------------
    print(
        "[DIAG] climatology input dims:",
        raw.dims,
        "coords:",
        list(raw.coords)
    )
    assert ("init" in raw.coords) or ("S" in raw.coords),"ERROR: climatology selection received forecast without init/S"
    
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

    # Drop any non-math dims (keep only lead/lat/lon)
    clim_da = clim_da.isel(
        {d: 0 for d in clim_da.dims if d not in ("lead", "lat", "lon")}
    ).squeeze(drop=True)

    # ---------------------------
    # CRITICAL FIX: Standardize + subtract positionally
    # ---------------------------

    # Remove coords to avoid alignment by labels
    #fcst_da = fcst_da.reset_coords(drop=True)
    #clim_da = clim_da.reset_coords(drop=True)
    
    fcst_da = normalize_forecast_for_anomaly_math(fcst_da)
    clim_da = clim_da.reset_coords(drop=True)

    # Now dims are identical and canonical
    fcst_da_aligned, clim_da_aligned = xr.align(
        fcst_da,
        clim_da,
        join="override"
    )

    # Safe subtraction
    da_anom = fcst_da_aligned - clim_da_aligned
 
    if varname == "sst":
        lat_dim, lon_dim = _latlon_dims(da_anom)

        print("\n[DIAG][ANOM SST]")
        print("dims:", da_anom.dims)
        print("shape:", da_anom.shape)

        if lat_dim and lon_dim:
            print("lat dim:", lat_dim, "size:", da_anom.sizes[lat_dim])
            print("lon dim:", lon_dim, "size:", da_anom.sizes[lon_dim])
            print("lon coord length:", len(da_anom[lon_dim]))
        else:
            print("❌ Missing lat/lon dims")

        # HARD INVARIANT CHECK
        assert da_anom.sizes[lon_dim] == len(da_anom[lon_dim]),f"SST grid corrupted: data lon size={da_anom.sizes[lon_dim]}, coord lon length={len(da_anom[lon_dim])}"


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
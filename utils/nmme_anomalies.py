# utils/nmme_anomalies.py

import pandas as pd
import xarray as xr
from pathlib import Path
from typing import Optional

from utils.nmme_io import (
    open_local_forecast,
    open_monthly_climatology,
)
from utils.nmme_normalize import (
    decode_cf_safe,
#    add_valid_times,
)
from utils.nmme_var_names import normalize_forecast_dataset


def model_anomalies_for_month(
    data_root: Path,
    clim_root: Path,
    model: str,
    varname: str,
    levstr: str,
    target: pd.Timestamp,
) -> Optional[xr.Dataset]:
    """
    Compute anomalies for ONE (model, variable, init month).

    Anomalies are computed PER ENSEMBLE MEMBER (M).
    """

    # ---------------------------
    # Open forecast
    # ---------------------------
    raw = open_local_forecast(data_root, model, varname, target)

    if raw is None:
        print(
            f"[MISSING-FILE] model={model} "
            f"init={target:%Y%m} var={varname}"
        )
        return None

    raw = decode_cf_safe(raw)

    # ---------------------------
    # Use target init month as canonical scalar S
    # ---------------------------
    # Mixing cftime and pandas timestamp objects across models can break
    # xarray concat/alignment. We standardize S to the requested init month.
    S_scalar = pd.Timestamp(target.year, target.month, 1)

    # ---------------------------
    # Variable existence check
    # ---------------------------
    raw = normalize_forecast_dataset(raw, model, varname)
    if raw is None or varname not in raw.data_vars:
        available = [] if raw is None else list(raw.data_vars)
        print(
            f"[MISSING-VAR] model={model} "
            f"init={target:%Y%m} var={varname} "
            f"available={available}"
        )
        return None

    # ---------------------------
    # Open climatology
    # ---------------------------
    clim = open_monthly_climatology(
        clim_root, model, varname, levstr, raw, target
    )

    if clim is None or varname not in clim.data_vars:
        print(
            f"[MISSING-CLIM] model={model} "
            f"init={target:%Y%m} var={varname}"
        )
        return None

    # ---------------------------
    # Prepare forecast array
    # (S removed for arithmetic)
    # ---------------------------
    fcst_da = raw[varname]

    if "S" in fcst_da.dims:
        fcst_da = fcst_da.isel(S=0).drop_vars("S", errors="ignore")

    # Some model files include singleton metadata axes (e.g., Z, P).
    # Drop singleton non-forecast dims before validating expected shape.
    expected_fcst_dims = {"M", "L", "Y", "X"}
    for d in list(fcst_da.dims):
        if d not in expected_fcst_dims:
            if fcst_da.sizes.get(d, 0) == 1:
                fcst_da = fcst_da.isel({d: 0}).squeeze(drop=True)
            else:
                print(
                    f"[INVALID-DIMS] model={model} "
                    f"init={target:%Y%m} var={varname} "
                    f"unexpected_dim={d} size={fcst_da.sizes.get(d)} "
                    f"(skipping)"
                )
                return None

    # Enforce forecast dims
    if set(fcst_da.dims) != expected_fcst_dims:
        print(
            f"[INVALID-DIMS] model={model} "
            f"init={target:%Y%m} var={varname} "
            f"dims={fcst_da.dims} "
            f"expected={expected_fcst_dims} "
            f"(skipping)"
        )
        return None
    # ---------------------------
    # Prepare climatology array
    # ---------------------------
    clim_da = clim[varname]
    
    # --- Rename climatology dims to match forecast convention ---
    rename_map = {}
    if "lon" in clim_da.dims or "lon" in clim_da.coords:
        rename_map["lon"] = "X"
    if "longitude" in clim_da.dims or "longitude" in clim_da.coords:
        rename_map["longitude"] = "X"
    if "lat" in clim_da.dims or "lat" in clim_da.coords:
        rename_map["lat"] = "Y"
    if "latitude" in clim_da.dims or "latitude" in clim_da.coords:
        rename_map["latitude"] = "Y"
    if "lead" in clim_da.dims or "lead" in clim_da.coords:
        rename_map["lead"] = "L"

    if rename_map:
        clim_da = clim_da.rename(rename_map)

    for d in list(clim_da.dims):
        if d not in ("L", "Y", "X"):
            clim_da = clim_da.isel({d: 0}).squeeze(drop=True)

    expected_clim_dims = {"L", "Y", "X"}
    if set(clim_da.dims) != expected_clim_dims:
        raise ValueError(
            f"Unexpected climatology dims {clim_da.dims}, "
            f"expected {expected_clim_dims}"
        )

    # ---------------------------
    # Compute anomalies
    # ---------------------------
    da_anom = fcst_da - clim_da

    # ---------------------------
    # Restore S as metadata
    # ---------------------------
    da_anom = da_anom.expand_dims(S=[S_scalar])

    # Remove stray scalar coordinates that can differ across models
    # and break xarray concat (e.g., Z, P, month).
    keep_coords = {"S", "M", "L", "Y", "X"}
    extra_coords = [c for c in da_anom.coords if c not in keep_coords]
    if extra_coords:
        da_anom = da_anom.drop_vars(extra_coords, errors="ignore")

    # ---------------------------
    # Add valid time
    # ---------------------------
    #da_anom = add_valid_times(da_anom, S_scalar)

    return da_anom.to_dataset(name=varname)
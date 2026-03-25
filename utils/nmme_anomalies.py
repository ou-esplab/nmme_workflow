# utils/nmme_anomalies.py

import pandas as pd
import xarray as xr
from pathlib import Path

from utils.nmme_io import (
    open_local_forecast,
    open_monthly_climatology,
)
from utils.nmme_normalize import (
    decode_cf_safe,
    extract_S_scalar,
#    add_valid_times,
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
    # Extract scalar start time
    # ---------------------------
    S_scalar = extract_S_scalar(raw, fallback=target)

    # ---------------------------
    # Variable existence check
    # ---------------------------
    if varname not in raw.data_vars:
        print(
            f"[MISSING-VAR] model={model} "
            f"init={target:%Y%m} var={varname} "
            f"available={list(raw.data_vars)}"
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

    # Enforce forecast dims
    expected_fcst_dims = {"M", "L", "Y", "X"}
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
    clim_da = clim_da.rename({
        "lon": "X",
        "lat": "Y",
        "lead": "L",
    })

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

    # ---------------------------
    # Add valid time
    # ---------------------------
    #da_anom = add_valid_times(da_anom, S_scalar)

    return da_anom.to_dataset(name=varname)
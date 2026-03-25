# utils/nmme_normalize.py

from typing import Optional
import numpy as np
import pandas as pd
import xarray as xr


def decode_cf_safe(ds: xr.Dataset) -> xr.Dataset:
    """Decode CF times without throwing."""
    try:
        return xr.decode_cf(ds)
    except Exception:
        return ds


def extract_S_scalar(ds, fallback=None):
    """
    Extract a single scalar S value from the dataset.

    Returns:
      - cftime datetime if decoded with use_cftime=True
      - numpy.datetime64 if that is what the dataset uses
    """
    if "S" not in ds:
        if fallback is None:
            raise ValueError("Dataset has no S and no fallback provided")
        return fallback

    S_vals = ds["S"].values

    # scalar or length-1 array
    if S_vals.ndim == 0:
        return S_vals.item()
    if S_vals.size == 1:
        return S_vals.reshape(-1)[0]

    raise ValueError(f"S has unexpected shape {S_vals.shape}")
    
    

def add_valid_times(ds, S_ts):
    """
    Add valid time coordinate using pandas datetime arithmetic.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset with integer lead coordinate 'L'
    S_ts : pandas.Timestamp
        Forecast initialization date in Gregorian calendar

    Notes
    -----
    This function MUST NOT be called with cftime objects.
    """
    if not isinstance(S_ts, pd.Timestamp):
        raise TypeError(
            "add_valid_times requires pandas.Timestamp; "
            "convert cftime → pandas explicitly before calling."
        )

    valid = np.array(
        [
            S_ts + pd.DateOffset(months=int(l))
            for l in ds["L"].values
        ],
        dtype="datetime64[ns]",
    )

    return ds.assign_coords(valid=("L", valid))
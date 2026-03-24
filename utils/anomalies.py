from __future__ import annotations
import numpy as np
import xarray as xr

def hindcast_climatology(hc: xr.DataArray) -> xr.DataArray:
    return hc.mean("T")

def forecast_minus_hindcast_climo(
    fc: xr.DataArray, hc: xr.DataArray
) -> xr.DataArray:
    return fc - hindcast_climatology(hc)

def prepare_predictand_for_cpt(Y, season: str, hindcast_years):
    if "T" not in Y.dims or not hasattr(Y["T"], "dt"):
        raise ValueError("Predictand must have datetime-like 'T'")

    from utils.time_utils import _MONTH_MAP

    start, end = season.split("-")
    start_m = _MONTH_MAP[start]
    end_m = _MONTH_MAP[end]

    if end_m >= start_m:
        months = range(start_m, end_m + 1)
    else:
        months = list(range(start_m, 13)) + list(range(1, end_m + 1))

    Y_season = Y.where(Y["T"].dt.month.isin(months), drop=True)
    Y_year = Y_season.groupby("T.year").mean("T")

    hindcast_years = np.asarray(hindcast_years).astype(int)
    years = np.intersect1d(Y_year["year"], hindcast_years)
    if not len(years):
        raise ValueError("No overlapping years")

    Y = Y_year.sel(year=years)
    clim = Y.mean("year")
    Y = (Y - clim).rename({"year": "S"}).transpose("S", "Y", "X")

    return Y

"""
nmme_pycpt_utils.py

Shared utility functions for the NMME + PyCPT workflow.

This module contains:
  • YAML configuration helpers
  • Region and model resolution
  • Local data loading utilities
  • Anomaly computation helpers
  • Plotting and regridding helpers used by PyCPT workflows

Design goals:
  • No side effects
  • No CLI parsing
  • No hard-coded paths
  • Fully driven by configuration objects
"""

from __future__ import annotations

from pathlib import Path
import yaml
import numpy as np
import xarray as xr


# ============================================================
# YAML / CONFIG HELPERS
# ============================================================

def load_config(path: Path) -> dict:
    """
    Load a YAML configuration file.

    Parameters
    ----------
    path : Path
        Path to configuration YAML.

    Returns
    -------
    dict
        Parsed configuration dictionary.
    """
    with path.open("r") as f:
        return yaml.safe_load(f)


def get_region(regions: list[dict], name: str) -> dict:
    """
    Retrieve a single region definition by name.

    Raises if region not found.

    Parameters
    ----------
    regions : list of dict
        List of region definitions.
    name : str
        Region name.

    Returns
    -------
    dict
        Region definition.
    """
    for r in regions:
        if r.get("name") == name:
            return r
    raise ValueError(f"Region '{name}' not found in configuration.")


def resolve_models(global_models: list[str], region: dict) -> list[str]:
    """
    Resolve models for a region.

    Priority:
      1) region['models'] override (if present)
      2) top-level models

    Parameters
    ----------
    global_models : list[str]
        Top-level model list.
    region : dict
        Region definition.

    Returns
    -------
    list[str]
        Effective model list.
    """
    if "models" in region and region["models"]:
        return list(region["models"])
    return list(global_models)


# ============================================================
# LOCAL DATA LOADING
# ============================================================

def _rename_to_cpt_dims(da: xr.DataArray | xr.Dataset) -> xr.DataArray:
    """
    Normalize coordinate names to PyCPT conventions: X, Y, T.
    """
    if isinstance(da, xr.Dataset):
        da = da[list(da.data_vars)[0]]

    rename = {}
    for lat in ("lat", "latitude", "y", "Y"):
        if lat in da.coords:
            rename[lat] = "Y"
            break

    for lon in ("lon", "longitude", "x", "X"):
        if lon in da.coords:
            rename[lon] = "X"
            break

    for tim in ("time", "T", "year"):
        if tim in da.coords:
            rename[tim] = "T"
            break

    if rename:
        da = da.rename(rename)

    return da


def open_netcdf_variable(path_glob: Path, var: str) -> xr.DataArray:
    """
    Open one or more NetCDF files and return a DataArray.

    Parameters
    ----------
    path_glob : Path
        File path or glob pattern.
    var : str
        Variable name inside NetCDF.

    Returns
    -------
    xr.DataArray
    """
    files = sorted(path_glob.parent.glob(path_glob.name))
    if not files:
        raise FileNotFoundError(f"No NetCDF files found for {path_glob}")

    ds = xr.open_mfdataset(files, combine="by_coords") if len(files) > 1 else xr.open_dataset(files[0])

    if var not in ds:
        raise KeyError(f"Variable '{var}' not found. Available: {list(ds.data_vars)}")

    return _rename_to_cpt_dims(ds[var])


def load_predictand_local(root: Path, subdir: str, var: str) -> xr.DataArray:
    """
    Load local predictand (observations).

    Parameters
    ----------
    root : Path
        Base local data root.
    subdir : str
        Subdirectory under root/observations.
    var : str
        Variable name.

    Returns
    -------
    xr.DataArray
    """
    return open_netcdf_variable(root / "observations" / subdir / "*.nc", var)


def load_model_local(
    root: Path,
    model_base: str,
    init_yyyymm: str,
    var: str
) -> tuple[xr.DataArray, xr.DataArray]:
    """
    Load local hindcasts and forecast for a single model.

    Layout:
      hindcasts/<model_base>/<var>/*.nc
      forecasts/<model_base>/<var>/<YYYYMM>*.nc
    """
    hc = open_netcdf_variable(root / "hindcasts" / model_base / var / "*.nc", var)
    fc = open_netcdf_variable(root / "forecasts" / model_base / var / f"{init_yyyymm}*.nc", var)
    return hc, fc


# ============================================================
# ANOMALY COMPUTATION
# ============================================================

def hindcast_climatology(hc: xr.DataArray) -> xr.DataArray:
    """
    Compute hindcast climatology along T dimension.
    """
    return hc.mean("T")


def forecast_minus_hindcast_climo(fc: xr.DataArray, hc: xr.DataArray) -> xr.DataArray:
    """
    Compute forecast anomalies relative to hindcast climatology.
    """
    return fc - hindcast_climatology(hc)


# ============================================================
# TERCILE / PROBABILITY HELPERS
# ============================================================

def standardized_mos_prob(mos_prob: xr.DataArray) -> xr.DataArray:
    """
    Standardize MOS probabilities to categories [bn, nn, an] in percent.
    """
    if "T" in mos_prob.dims:
        mos_prob = mos_prob.isel(T=0)

    mos_prob = mos_prob.rename({"C": "cat"})
    mos_prob = mos_prob.assign_coords(cat=["bn", "nn", "an"])

    if mos_prob.max() <= 1.01:
        mos_prob = mos_prob * 100.0

    return mos_prob


def tercile_thresholds(hc_anom: xr.DataArray) -> tuple[xr.DataArray, xr.DataArray]:
    """
    Compute 33rd and 66th percentile tercile thresholds.
    """
    t33 = hc_anom.quantile(0.33, dim="T")
    t66 = hc_anom.quantile(0.66, dim="T")
    return t33.squeeze(), t66.squeeze()


def raw_tercile_masks(fc_anom: xr.DataArray, t33, t66) -> xr.DataArray:
    """
    One-hot tercile mask in percent.
    """
    bn = (fc_anom < t33) * 100.0
    an = (fc_anom >= t66) * 100.0
    nn = 100.0 - bn - an
    return xr.concat([bn, nn, an], dim="cat").assign_coords(cat=["bn", "nn", "an"])


# ============================================================
# GRID INTERPOLATION
# ============================================================

def interp_to_target_grid(src: xr.DataArray, target: xr.DataArray) -> xr.DataArray:
    """
    Interpolate source data to target grid safely.
    """
    src = _rename_to_cpt_dims(src)
    target = _rename_to_cpt_dims(target)
    return src.interp(X=target.X, Y=target.Y)
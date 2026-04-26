from typing import Iterable, Optional, Tuple

import xarray as xr
import numpy as np


def _ordered_unique(values: Iterable[str]) -> Tuple[str, ...]:
    seen = set()
    ordered = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def forecast_storage_var_candidates(model: str, var: str) -> Tuple[str, ...]:
    candidates = [var]

    # SFS: z200/z500 are aliases for h200/h500
    if model == "NOAA-SFS":
        if var == "h200":
            candidates.append("z200")
        if var == "h500":
            candidates.append("z500")

    if var == "h200" and model in {"COLA-RSMAS-CCSM4", "COLA-RSMAS-CESM1"}:
        candidates.append("gz")

    if var in {"h200", "h500"} and model in {"CanESM5", "GEM5.2-NEMO"}:
        candidates.append("hgt")

    if var in {"h200", "h500"} and model == "NCAR-CESM1":
        candidates.append("zg")

    return _ordered_unique(candidates)


def forecast_dataset_var_candidates(model: str, var: str) -> Tuple[str, ...]:
    candidates = [var]

    # SFS: z200/z500 are aliases for h200/h500
    if model == "NOAA-SFS":
        if var == "h200":
            candidates.append("z200")
        if var == "h500":
            candidates.append("z500")

    if var == "sst" and model == "GFDL-SPEAR":
        candidates.append("sst_regridded")

    if var == "h200":
        candidates.append("gz")

    if var in {"h200", "h500"}:
        candidates.extend(["hgt", "zg"])

    return _ordered_unique(candidates)


def target_pressure_level(var: str) -> Optional[int]:
    return {
        "h200": 200,
        "h500": 500,
    }.get(var)


def select_pressure_level(
    ds: xr.Dataset,
    var: str,
) -> Optional[xr.Dataset]:
    target_level = target_pressure_level(var)
    if target_level is None:
        return ds

    if "P" not in ds.coords and "P" not in ds.dims:
        return ds

    if "P" not in ds:
        return ds

    raw_values = ds["P"].values
    if getattr(raw_values, "size", 0) == 0:
        return None

    available = {int(value) for value in raw_values.reshape(-1).tolist()}
    if target_level not in available:
        return None

    ds = ds.sel(P=target_level, drop=True)
    if "P" in ds.dims and ds.sizes.get("P", 0) == 1:
        ds = ds.squeeze("P", drop=True)

    return ds


def resolve_forecast_dataset_var_name(
    model: str,
    requested_var: str,
    raw: xr.Dataset,
) -> Optional[str]:
    for candidate in forecast_dataset_var_candidates(model, requested_var):
        if candidate in raw.data_vars:
            return candidate
    return None


def normalize_forecast_dataset(
    raw: xr.Dataset,
    model: str,
    requested_var: str,
) -> Optional[xr.Dataset]:
    """
    Canonical normalization function for NMME forecast datasets.
    This is the single source of truth for all normalization logic in the workflow.
    All scripts and modules should use ONLY this function for normalization.

    Args:
        raw: The input xarray.Dataset to normalize.
        model: The model name (used for variable aliasing).
        requested_var: The canonical variable name to normalize to.

    Returns:
        Normalized xarray.Dataset with canonical variable names and dimensions, or None if not possible.
    """
    raw = select_pressure_level(raw, requested_var)
    if raw is None:
        return None

    source_var = resolve_forecast_dataset_var_name(model, requested_var, raw)
    if source_var is None:
        return None

    if source_var != requested_var:
        raw = raw.rename({source_var: requested_var})

    # Add 'valid' coordinate if possible
    lead_dim = None
    for candidate in ["lead", "L"]:
        if candidate in raw.dims:
            lead_dim = candidate
            break
    if lead_dim is not None:
        # Try to get init date from 'S' coordinate or attribute
        import pandas as pd
        S_val = None
        if "S" in raw.coords:
            S = raw["S"].values
            print(f"[DEBUG] 'S' coordinate value: {S}, type: {type(S)}")
            # Expect S to be a cftime object or array of cftime objects
            import cftime
            # Handle array of length 1
            if isinstance(S, (list, tuple, np.ndarray)) and len(S) == 1:
                S = S[0]
            if isinstance(S, cftime.Datetime360Day) or isinstance(S, cftime.datetime):
                S_val = S
            else:
                print(f"[ERROR] 'S' is not a cftime object: {S} (type: {type(S)})")
                S_val = None

        def add_months_cftime(dt, months):
            # Handles month overflow for cftime.Datetime360Day and similar
            year = dt.year + (dt.month - 1 + months) // 12
            month = (dt.month - 1 + months) % 12 + 1
            return type(dt)(year, month, 1)
        if S_val is not None:
            valid = [add_months_cftime(S_val, int(l)) for l in raw[lead_dim].values]
            raw = raw.assign_coords(valid=(lead_dim, valid))
            print(f"[DEBUG] Added 'valid' in normalization (lead_dim={lead_dim}): {valid}")
        else:
            print(f"[DEBUG] Skipped adding 'valid' in normalization: could not parse 'S' from {raw.coords}")
    else:
        print(f"[DEBUG] Skipped adding 'valid' in normalization: no lead dimension in {raw.dims}")

    return raw
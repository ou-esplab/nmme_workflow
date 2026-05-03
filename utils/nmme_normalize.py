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

            # If S is already a cftime/datetime object, use it.
            if isinstance(S, (cftime.Datetime360Day, cftime.datetime)):
                S_val = S
            else:
                # Attempt to decode numeric time coordinate using CF units/calendar
                S_val = None
                try:
                    units = raw["S"].attrs.get("units")
                    calendar = raw["S"].attrs.get("calendar", "standard")
                    if units is not None:
                        from cftime import num2date

                        # num2date accepts arrays or scalars; ensure float input
                        S_val = num2date(float(S), units, calendar=calendar)
                        print(f"[DEBUG] Decoded numeric 'S' via cftime.num2date -> {S_val}")
                except Exception:
                    S_val = None

                if S_val is None:
                    print(f"[ERROR] 'S' is not a cftime object: {S} (type: {type(S)})")

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

    # --- Canonicalize coordinate names (avoid duplicate helper funcs) ---
    rename = {}
    if "latitude" in raw.coords or "latitude" in raw.dims:
        rename["latitude"] = "lat"
    if "longitude" in raw.coords or "longitude" in raw.dims:
        rename["longitude"] = "lon"
    if "M" in raw.dims and "member" not in raw.dims:
        rename["M"] = "member"
    if "L" in raw.dims and "lead" not in raw.dims:
        rename["L"] = "lead"

    if rename:
        raw = raw.rename(rename)

    # --- Ensure 'valid' uses cftime objects (convert numpy datetime64 if present) ---
    if "valid" in raw.coords:
        try:
            v_dtype = str(raw.coords["valid"].dtype)
        except Exception:
            v_dtype = ""

        if "datetime64" in v_dtype:
            import pandas as pd
            import cftime as _cftime

            vals = raw.coords["valid"].values
            conv = []

            # Attempt to infer calendar from the original S/init coord or dataset
            calendar = None
            if "S" in raw.coords:
                calendar = raw["S"].attrs.get("calendar")
            if calendar is None:
                calendar = raw.attrs.get("calendar")
            if calendar == "360":
                calendar = "360_day"

            for v in vals:
                ts = pd.to_datetime(v)
                if calendar and "360" in str(calendar):
                    conv.append(_cftime.Datetime360Day(ts.year, ts.month, ts.day))
                else:
                    conv.append(_cftime.DatetimeGregorian(ts.year, ts.month, ts.day))

            # preserve lead-dimension association when reassigning
            lead_dim = raw.coords["valid"].dims[0] if raw.coords["valid"].dims else "lead"
            raw = raw.assign_coords(valid=(lead_dim, conv))

    # --- Enforce dtypes for member/lead/lat/lon ---
    try:
        if "member" in raw.coords:
            raw = raw.assign_coords(member=raw.coords["member"].astype("int32"))
    except Exception:
        pass

    try:
        if "lead" in raw.coords:
            raw = raw.assign_coords(lead=raw.coords["lead"].astype("int32"))
    except Exception:
        pass

    try:
        if "lat" in raw.coords and getattr(raw.coords["lat"].dtype, "kind", "f") != "f":
            raw = raw.assign_coords(lat=raw.coords["lat"].astype("float32"))
    except Exception:
        pass

    try:
        if "lon" in raw.coords and getattr(raw.coords["lon"].dtype, "kind", "f") != "f":
            raw = raw.assign_coords(lon=raw.coords["lon"].astype("float32"))
    except Exception:
        pass

    # --- Reorder dimensions to canonical order while preserving extras ---
    canonical = ["member", "lead", "lat", "lon"]
    present = [d for d in canonical if d in raw.dims]
    remaining = [d for d in raw.dims if d not in present]
    order = present + remaining
    if tuple(order) != tuple(raw.dims):
        try:
            raw = raw.transpose(*order)
        except Exception:
            # If transpose fails, leave as-is and let downstream checks handle it
            pass

    return raw
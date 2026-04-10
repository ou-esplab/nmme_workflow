from typing import Iterable, Optional, Tuple

import xarray as xr


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
    raw = select_pressure_level(raw, requested_var)
    if raw is None:
        return None

    source_var = resolve_forecast_dataset_var_name(model, requested_var, raw)
    if source_var is None:
        return None

    if source_var != requested_var:
        raw = raw.rename({source_var: requested_var})

    return raw
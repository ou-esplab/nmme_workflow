#!/usr/bin/env python3
# coding: utf-8
"""
nmme_products_utils.py
----------------------
User-defined helpers for the NMME "products" step used by MakeNMMEFcsts.py:
- Model metadata (variables, levels, units) via `init_models()`
- File IO helpers for local files and monthly climatologies
- CF/time/dimension normalization and labeling (`S`, `lead`, `valid`)
- Per-model anomaly assembly and MME build
- Plotting and writing functions (`nmme_plot`, `nmme_write`)

All functions are documented and imported by MakeNMMEFcsts.py so the script
is a thin CLI wrapper.
"""
from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path
import numpy as np
import pandas as pd
import xarray as xr
import os

# --------------------------- Model metadata --------------------------- #
def init_models() -> Tuple[List[Dict[str, Any]], Dict[str, str], List[str], Dict[str, str]]:
    """
    Return (models_list, vnames, levstrs_all, units) describing NMME variables.

    models_list is a list of dicts, one per model, e.g.
      [
        {"model":"NASA-GEOSS2S", "varnames":["prec","olr","tref","sst","h500","h200"], "levstrs":["sfc","toa","2m","sfc","500","200"]},
        ...
      ]

    Notes:
    - Matches the variables you download in nmme_update_fcsts.sh (prec, olr, tref, sst, h500, h200).
    - `levstrs` helps locate the proper climatology file naming.
    """
    models = [
        {"model":"NASA-GEOSS2S", "varnames":["prec","olr","tref","sst","h500","h200"],
         "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"CanESM5", "varnames":["prec","olr","tref","sst","h500","h200"],
         "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"GFDL-SPEAR", "varnames":["prec","olr","tref","sst","h500","h200"],
         "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"GEM5.2-NEMO", "varnames":["prec","olr","tref","sst","h500","h200"],
         "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"NCEP-CFSv2", "varnames":["prec","olr","tref","sst","h500","h200"],
         "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"COLA-RSMAS-CCSM4", "varnames":["prec","olr","tref","sst","h500","h200"],
         "levstrs":["sfc","toa","2m","sfc","500","200"]},
        {"model":"COLA-RSMAS-CESM1", "varnames":["prec","olr","tref","sst","h500","h200"],
         "levstrs":["sfc","toa","2m","sfc","500","200"]},
    ]
    vnames = {"prec":"prec", "olr":"olr", "tref":"tref", "sst":"sst", "h500":"h500", "h200":"h200"}
    levstrs_all = ["sfc","toa","2m","sfc","500","200"]
    units = {"prec":"mm/day", "olr":"W/m^2", "tref":"K", "sst":"K", "h500":"m", "h200":"m"}
    return models, vnames, levstrs_all, units

# --------------------------- CF/time helpers -------------------------- #
def decode_cf(ds: xr.Dataset, s_coord_name: str="S") -> xr.Dataset:
    """Best-effort CF decode for 'S' coordinate (safe no-op if not needed)."""
    try:
        return xr.decode_cf(ds)
    except Exception:
        return ds

def _to_scalar_timestamp(val, fallback_dt: pd.Timestamp) -> pd.Timestamp:
    """Robustly coerce any 'S' values to a scalar pandas.Timestamp."""
    if val is None:
        return pd.Timestamp(fallback_dt)
    try:
        if hasattr(val, "__len__") and not isinstance(val, (str, bytes)):
            if len(val) == 0:
                return pd.Timestamp(fallback_dt)
            return pd.to_datetime(val[0])
        return pd.to_datetime(val)
    except Exception:
        return pd.Timestamp(fallback_dt)

def ensure_start_coord(ds: xr.Dataset, start_time: pd.Timestamp) -> xr.Dataset:
    """Ensure a scalar 'S' coordinate exists and equals `start_time` (UTC)."""
    if "S" in ds.coords:
        try:
            S0 = _to_scalar_timestamp(ds["S"].values, start_time)
            ds = ds.assign_coords(S=np.array(S0.to_datetime64()))
        except Exception:
            ds = ds.assign_coords(S=np.array(pd.Timestamp(start_time).to_datetime64()))
    else:
        ds = ds.assign_coords(S=np.array(pd.Timestamp(start_time).to_datetime64()))
    return ds

def standardize_dims(ds: xr.Dataset) -> xr.Dataset:
    """Rename common dims to (lon,lat,lead); set canonical units."""
    rename_map = {}
    if "X" in ds.dims: rename_map["X"] = "lon"
    if "Y" in ds.dims: rename_map["Y"] = "lat"
    if "L" in ds.dims: rename_map["L"] = "lead"
    ds = ds.rename(rename_map)
    if "lead" in ds.dims:
        try:
            ds["lead"] = (ds["lead"] - 0.5).astype("int")
        except Exception:
            pass
    if "lon" in ds.coords: ds["lon"].attrs["units"] = "degrees_east"
    if "lat" in ds.coords: ds["lat"].attrs["units"] = "degrees_north"
    if "lead" in ds.coords: ds["lead"].attrs["units"] = "months"
    return ds

def fix_lead_coord(ds: xr.Dataset) -> xr.Dataset:
    """Resolve duplicate/non-integer 'lead' entries by converting to int index."""
    if "lead" not in ds.dims:
        return ds
    try:
        dup = pd.Index(ds["lead"].values).duplicated().any()
    except Exception:
        dup = False
    if dup:
        new_lead = np.arange(ds.sizes["lead"], dtype=int)
        ds = ds.assign_coords(lead=("lead", new_lead))
    else:
        try:
            ds["lead"] = ds["lead"].astype(int)
        except Exception:
            pass
    return ds

def add_valid_times(ds: xr.Dataset) -> xr.Dataset:
    """Add a 'valid' coordinate = S + lead months (if both exist)."""
    if "lead" not in ds.dims or "S" not in ds.coords:
        return ds
    S0 = _to_scalar_timestamp(ds["S"].values, pd.Timestamp("2000-01-01"))
    valid_list = [S0 + pd.DateOffset(months=int(l)) for l in ds["lead"].values]
    return ds.assign_coords(valid=("lead", valid_list))

def get_start_month(ds: xr.Dataset, start_time: pd.Timestamp) -> int:
    """Return forecast start month, preferring 'S' if present."""
    if "S" in ds.coords:
        S = _to_scalar_timestamp(ds["S"].values, start_time)
        return int(S.month)
    return int(start_time.month)

def select_yyyymm_lenient(ds: xr.Dataset, target_dt: pd.Timestamp,
                          y_from_name: int, m_from_name: int) -> Optional[xr.Dataset]:
    """
    Enforce target YYYYMM while being lenient about how 'S' is stored:
    - If 'S' is scalar, accept if month==target else accept if filename month==target.
    - If 'S' is a dim, select first matching month; else accept if filename month==target.
    - If 'S' missing, accept if filename month==target.
    Return ds (maybe reduced to one S), or None if we can prove it’s not the target month.
    """
    target_ym = target_dt.year * 100 + target_dt.month
    if "S" in ds.coords and "S" not in ds.dims:
        try:
            S0 = _to_scalar_timestamp(ds["S"].values, target_dt)
            ym = S0.year * 100 + S0.month
            if ym == target_ym: return ds
            if (y_from_name * 100 + m_from_name) == target_ym: return ds
            return None
        except Exception:
            return ds if (y_from_name * 100 + m_from_name) == target_ym else None
    if "S" in ds.dims:
        try:
            svals = pd.to_datetime(ds["S"].values)
            ym_vals = np.array([sv.year * 100 + sv.month for sv in svals])
            idxs = np.where(ym_vals == target_ym)[0]
            if idxs.size > 0:
                return ds.isel(S=int(idxs[0]), drop=True)
            if (y_from_name * 100 + m_from_name) == target_ym:
                return ds.isel(S=0, drop=True) if ds.sizes.get("S", 0) > 0 else ds
            return None
        except Exception:
            return ds if (y_from_name * 100 + m_from_name) == target_ym else None
    return ds if (y_from_name * 100 + m_from_name) == target_ym else None

# ---------------------------- IO helpers ------------------------------ #
def open_local_file(data_root: Path, model: str, varname: str, y: int, m: int) -> Optional[xr.Dataset]:
    """Open local file: {data_root}/{model}/forecast/{var}/{var}_{model}_{YYYY}_{MM}.nc"""
    fpath = data_root / model / "forecast" / varname / f"{varname}_{model}_{y}_{m:02d}.nc"
    if not fpath.exists():
        print(f"[WARN] Missing file: {fpath}")
        return None
    try:
        return xr.open_dataset(fpath, decode_times=False)
    except Exception as e:
        print(f"[WARN] Failed to open {fpath}: {e}")
        return None

def pick_internal_var_name(model: str, varname: str, ds: xr.Dataset) -> str:
    """
    Return the data variable name to use inside ds given target varname.
    Handle special cases (COLA h200→'gz', GFDL-SPEAR 'sst_regridded'→'sst').
    """
    if model in ["COLA-RSMAS-CCSM4", "COLA-RSMAS-CESM1"] and varname == "h200":
        if "gz" in ds.data_vars:
            return "gz"
    if model == "GFDL-SPEAR" and varname == "sst" and "sst_regridded" in ds.data_vars:
        return "sst_regridded"
    if varname in ds.data_vars:
        return varname
    for dv in ds.data_vars:
        return dv
    raise KeyError(f"No data variables found for model={model}, varname={varname}")

def get_nens(ds: xr.Dataset) -> int:
    """Infer ensemble size from one of common dims: M/ens/member/ensemble/E."""
    for cand in ["M", "ens", "member", "ensemble", "E"]:
        if cand in ds.dims:
            try:
                return int(ds.dims[cand])
            except Exception:
                return len(ds[cand])
    return 1

# ------------------------ Per-model anomalies ------------------------- #
def model_anomalies_for_month(data_root: Path, clim_root: Path,
                              model: str, varname: str, levstr: str,
                              target: pd.Timestamp) -> Optional[xr.Dataset]:
    """
    Load one model/variable for a YYYYMM init, subtract matching monthly climatology,
    return a one-variable dataset with normalized dims and coords.
    """
    raw = open_local_file(data_root, model, varname, target.year, target.month)
    if raw is None:
        return None
    try:
        raw = decode_cf(raw, "S")
    except Exception:
        pass
    raw = select_yyyymm_lenient(raw, target, target.year, target.month)
    if raw is None:
        return None
    raw = ensure_start_coord(raw, target)
    raw = standardize_dims(raw)
    raw = fix_lead_coord(raw)

    dv = pick_internal_var_name(model, varname, raw)
    if model == "GFDL-SPEAR" and dv == "sst_regridded":
        raw = raw.rename({"sst_regridded": "sst"})
        dv = "sst"
    if "Z" in raw.dims and raw.sizes.get("Z", 1) == 1:
        raw = raw.squeeze("Z", drop=True)
    if dv != varname:
        if dv in raw.data_vars:
            raw = raw.rename({dv: varname})
        else:
            return None
    if varname not in raw.data_vars:
        return None

    clim_file = clim_root / f"{model}.{varname}_{levstr}.clim.1991-2020.nc"
    if not clim_file.exists():
        print(f"[WARN] Missing climatology: {clim_file}")
        return None
    ds_clim = xr.open_dataset(clim_file)
    start_month = get_start_month(raw, target)
    try:
        clim_sel = ds_clim.sel(month=int(start_month))
    except Exception:
        if "month" in ds_clim.coords:
            idx = max(0, min(int(start_month) - 1, ds_clim.sizes["month"] - 1))
            clim_sel = ds_clim.isel(month=idx)
        else:
            raise KeyError(f"Climatology has no 'month' coordinate: {clim_file}")

    if varname not in clim_sel:
        print(f"[WARN] Variable '{varname}' not found in climatology {clim_file}")
        return None

    da = raw[varname]
    ds_anom = da - clim_sel[varname]
    out = ds_anom.to_dataset(name=varname)
    out = out.assign_coords(model=model, nens=get_nens(raw))
    out = standardize_dims(out)
    out = fix_lead_coord(out)
    out = ensure_start_coord(out, target)
    out = add_valid_times(out)
    return out

# ---------------------- Combine & MME assembly ------------------------ #
def build_mme_for_month(data_root: Path, clim_root: Path,
                        target: pd.Timestamp) -> xr.Dataset:
    """
    Loop all models & variables to build a dataset of per-model anomalies and the MME.
    """
    models_list, _, _, _ = init_models()
    per_model: List[xr.Dataset] = []
    for m in models_list:
        model, varnames, levs = m["model"], m["varnames"], m["levstrs"]
        anoms: List[xr.Dataset] = []
        for varname, levstr in zip(varnames, levs):
            ds = model_anomalies_for_month(data_root, clim_root, model, varname, levstr, target)
            if ds is not None:
                anoms.append(ds)
            else:
                print(f"[WARN] Skipping {model} {varname} for {target:%Y-%m}")
        if not anoms:
            print(f"[WARN] No usable variables for model {model}")
            continue
        per_model.append(xr.merge(anoms, compat="override"))

    if not per_model:
        raise RuntimeError("No models/variables available for this init date.")

    ds_models = xr.concat(per_model, dim="model", coords="minimal", compat="override", join="outer")
    if "M" in ds_models.dims:
        ds_models = ds_models.mean(dim="M", keep_attrs=True)

    ds_mme = ds_models.mean(dim="model", keep_attrs=True)
    ds_mme = ds_mme.assign_coords(model="MME", nens=ds_models["nens"].sum())
    out = xr.concat([ds_models, ds_mme], dim="model", coords="minimal",
                    compat="override", join="outer")
    return out

# -------------------- Plot & Write (customize later) ------------------ #
# --- replace the existing nmme_plot(...) with this version ---
def nmme_plot(ds_fcst: xr.Dataset, figpath: str) -> None:
    """
    Produce per-variable figure panels for quick QA/QC.

    Robust to variables that are not immediately 2-D (lat,lon): we try to
    reduce to a 2-D (lat,lon) slice by dropping/slicing non-spatial dims.
    If that’s not possible, we skip plotting that variable.
    """
    import os
    from pathlib import Path
    import pandas as pd
    import matplotlib.pyplot as plt

    os.makedirs(figpath, exist_ok=True)

    # Utility: find coordinate names
    def _lat_name(obj):
        for c in ("lat", "latitude", "y", "Y"):
            if c in obj.coords:
                return c
        return None

    def _lon_name(obj):
        for c in ("lon", "longitude", "x", "X"):
            if c in obj.coords:
                return c
        return None

    # Utility: reduce DataArray to 2-D (lat,lon) if possible
    def _to_latlon_2d(da: xr.DataArray):
        # Prefer first lead=0 if present
        if "lead" in da.dims and da.sizes.get("lead", 0) > 0:
            da = da.isel(lead=0)
        # Prefer first member dimension if present (M/ens/member/E)
        for mname in ("M", "ens", "member", "ensemble", "E"):
            if mname in da.dims and da.sizes.get(mname, 0) > 0:
                da = da.isel({mname: 0})
        # Prefer first time-like dims if present
        for tname in ("time", "T", "target"):
            if tname in da.dims and da.sizes.get(tname, 0) > 0:
                da = da.isel({tname: 0})
        # Squeeze singletons
        da = da.squeeze(drop=True)

        lat = _lat_name(da)
        lon = _lon_name(da)
        if lat is None or lon is None:
            return None  # no spatial coords

        # If not both dims present, try to drop extra dims
        if lat not in da.dims or lon not in da.dims:
            for d in list(da.dims):
                if d not in (lat, lon):
                    # take the first index along non-spatial dims
                    da = da.isel({d: 0})
            da = da.squeeze(drop=True)

        # Final check: truly 2-D
        return da if (lat in da.dims and lon in da.dims) else None

    init_str = None
    if "S" in ds_fcst.coords:
        try:
            init_str = pd.to_datetime(ds_fcst["S"].values).strftime("%Y-%m")
        except Exception:
            init_str = "unknown"

    for v in ds_fcst.data_vars:
        try:
            da = ds_fcst[v]
            da2 = _to_latlon_2d(da)
            if da2 is None:
                # Not a 2-D field we can plot; skip quietly
                continue

            lat = _lat_name(da2)
            lon = _lon_name(da2)

            # Use imshow to ensure add_colorbar is valid; specify axes by coord names
            h = da2.plot.imshow(x=lon, y=lat, add_colorbar=True)
            fig = h.figure
            ax = fig.axes[0]

            title_bits = [v]
            if init_str is not None:
                title_bits.append(f"init {init_str}")
            ax.set_title(" – ".join(title_bits))

            out = Path(figpath) / f"{v}_init{init_str.replace('-', '') if init_str else 'unknown'}.png"
            fig.savefig(out, dpi=150, bbox_inches="tight")
            plt.close(fig)
        except Exception as e:
            # Do not raise; plotting is best-effort
            # You can log if desired:
            # print(f"[WARN] plot skipped for {v}: {e}")
            continue
            
            
def nmme_write(ds_fcst: xr.Dataset, fcst_yyyymm: str) -> None:
    """
    Write a single NetCDF with per-model and MME anomalies for the month.
    Uses the same monthly tree your shell creates.
    """
    datapath = Path(f"/data/esplab/shared/model/initialized/nmme/forecast/monthly/{fcst_yyyymm}/data/")
    os.makedirs(datapath, exist_ok=True)
    out_nc = datapath / f"nmme_anoms_mme_{fcst_yyyymm}.nc"
    ds_fcst.to_netcdf(out_nc)
    print(f"[SAVE] {out_nc}")

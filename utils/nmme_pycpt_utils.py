"""
nmme_pycpt_utils.py

Local-only utilities for the NMME + PyCPT workflow.

This module provides:
  • YAML configuration helpers
  • Region and model resolution
  • Local data loading for predictand, hindcasts, and forecasts
  • Simple anomaly helpers
  • Safe grid interpolation
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import xarray as xr
import numpy as np
import yaml

from cptcore.base import CPT
from cptio import to_cptv10 as cptio_to_cptv10

# ============================================================
# YAML / CONFIG HELPERS
# ============================================================

def load_config(path: Path) -> dict:
    with path.open("r") as f:
        return yaml.safe_load(f)


def get_region(regions: List[dict], name: str) -> dict:
    for r in regions:
        if r.get("name") == name:
            return r
    raise ValueError(f"Region '{name}' not found in configuration.")


def resolve_models(global_models: List[str], region: dict) -> List[str]:
    if "models" in region and region["models"]:
        return list(region["models"])
    return list(global_models)


# ============================================================
# LOCAL DATA LOADING
# ============================================================

from datetime import datetime
import numpy as np

def load_emean_local(nmme_monthly_root, fcstdate, var, lev):
    """
    Load ensemble-mean monthly anomaly NMME product written by nmme_write.
    """
    from pathlib import Path
    import xarray as xr

    path = (
        Path(nmme_monthly_root)
        / fcstdate
        / "data"
        / f"NMME_fcst_{fcstdate}.anom.monthly.{var}_{lev}.emean.nc"
    )

    if not path.exists():
        raise FileNotFoundError(f"Missing ensemble-mean product: {path}")

    return xr.open_dataset(path)

def select_lead(fdate, season, L_coord):
    """
    Select a single representative forecast lead L based on forecast
    initialization date and target season, following PyCPT logic.

    Parameters
    ----------
    fdate : str or datetime
        Forecast initialization date (e.g., '2026-01-01').
    season : str
        Target season in 'Mon-Mon' format (e.g., 'Feb-Apr').
    L_coord : array-like
        Available lead values from predictor dataset (e.g., da['L'].values).

    Returns
    -------
    float
        Selected lead value from L_coord.
    """

    # -----------------------------
    # 1. Parse forecast init month
    # -----------------------------
    if isinstance(fdate, str):
        init_month = datetime.fromisoformat(fdate).month
    elif isinstance(fdate, datetime):
        init_month = fdate.month
    else:
        raise TypeError("fdate must be str or datetime")

    # -----------------------------
    # 2. Parse target season months
    # -----------------------------
    month_map = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
        "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
        "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }

    try:
        start_mon, end_mon = season.split("-")
        start_m = month_map[start_mon]
        end_m = month_map[end_mon]
    except Exception:
        raise ValueError("season must be like 'Feb-Apr'")

    # -----------------------------
    # 3. Build season month list
    #    (handles year crossing)
    # -----------------------------
    if end_m >= start_m:
        season_months = list(range(start_m, end_m + 1))
    else:
        # e.g., Nov-Feb
        season_months = list(range(start_m, 13)) + list(range(1, end_m + 1))

    # -----------------------------
    # 4. Convert season months to leads
    # -----------------------------
    # Lead is relative to initialization month
    leads = []
    for m in season_months:
        lead = m - init_month
        if lead <= 0:
            lead += 12
        leads.append(lead)

    # -----------------------------
    # 5. Choose central season month
    # -----------------------------
    center_lead = np.median(leads)

    # -----------------------------
    # 6. Match to available L values
    # -----------------------------
    L_coord = np.asarray(L_coord)

    # Prefer exact match, else nearest
    if center_lead in L_coord:
        return float(center_lead)

    idx = np.argmin(np.abs(L_coord - center_lead))
    return float(L_coord[idx])

def _rename_to_cpt_dims(da: xr.DataArray | xr.Dataset) -> xr.DataArray:
    if isinstance(da, xr.Dataset):
        da = da[list(da.data_vars)[0]]

    rename = {}
    for lat in ("lat", "latitude", "y", "Y"):
        if lat in da.coords or lat in da.dims:
            rename[lat] = "Y"
            break
    for lon in ("lon", "longitude", "x", "X"):
        if lon in da.coords or lon in da.dims:
            rename[lon] = "X"
            break
    for tim in ("time", "T", "year"):
        if tim in da.coords or tim in da.dims:
            rename[tim] = "T"
            break

    if rename:
        da = da.rename(rename)

    for c in ("Y", "X", "T"):
        if c in da.coords:
            da = da.sortby(c)

    return da


def open_netcdf_variable(path_glob: Path, var: str) -> xr.DataArray:
    """
    Open one or more NetCDF files, decode CF time correctly (T or S),
    and return a DataArray with normalized spatial dimensions.

    Time handling rules:
      - Open with decode_times=False for safety
      - Decode CF time explicitly using decode_cf_time
      - Decode 'T' if present, else decode 'S' if present
      - Leave calendars intact (no forced changes)
    """
    files = sorted(path_glob.parent.glob(path_glob.name))
    if not files:
        raise FileNotFoundError(f"No NetCDF files found matching: {path_glob}")

    # Validate inputs first. Some archives can contain non-NetCDF files with
    # .nc suffix; skip unreadable files rather than aborting all regions.
    valid_files = []
    skipped_files = []
    for fp in files:
        try:
            xr.open_dataset(fp, decode_times=False, engine="netcdf4").close()
            valid_files.append(fp)
        except Exception as exc:
            skipped_files.append((fp, exc))

    if skipped_files:
        for fp, exc in skipped_files:
            print(f"[WARN] Skipping unreadable NetCDF file: {fp} ({exc})")

    if not valid_files:
        raise RuntimeError(
            f"No readable NetCDF files found for pattern: {path_glob}; "
            f"skipped={len(skipped_files)}"
        )

    # Always open safely first
    ds = (
        xr.open_mfdataset(
            valid_files,
            combine="by_coords",
            decode_times=False,
            engine="netcdf4",
        )
        if len(valid_files) > 1
        else xr.open_dataset(valid_files[0], decode_times=False, engine="netcdf4")
    )

    # Decode CF time *explicitly* and *only* for valid time coords
    if "T" in ds.coords:
        ds = decode_cf_time(ds, time_var="T")
    elif "S" in ds.coords:
        ds = decode_cf_time(ds, time_var="S")

    if var not in ds.data_vars:
        raise KeyError(
            f"Variable '{var}' not found in {valid_files[0]} "
            f"(vars={list(ds.data_vars)})"
        )

    # Return DataArray with standardized spatial dims (no time guessing)
    return _rename_to_cpt_dims(ds[var])



def load_predictand_local(root: Path, subdir: str, var: str) -> xr.DataArray:
    """
    Load local predictand (observations) with calendar-aware time decoding.

    This MUST use decode_times=True so that `.dt.month` and `.dt.year`
    are available for seasonal aggregation.
    """
    files = sorted((root / "observations" / subdir).glob("*.nc"))
    if not files:
        raise FileNotFoundError(f"No predictand files found in {root / 'observations' / subdir}")

    ds = xr.open_mfdataset(
        files,
        combine="by_coords",
        decode_times=True,     # ✅ THIS IS THE FIX
        use_cftime=True        # ✅ ensures non‑standard calendars work
    )

    if var not in ds:
        raise KeyError(f"Predictand variable '{var}' not found (vars={list(ds.data_vars)})")

    da = ds[var]

    # Standardize dimension names (lat/lon/time → Y/X/T)
    da = _rename_to_cpt_dims(da)

    return da


def _yyyymm_parts(yyyymm: str) -> tuple[str, str]:
    if len(yyyymm) != 6 or not yyyymm.isdigit():
        raise ValueError(f"Expected YYYYMM, got: {yyyymm}")
    return yyyymm[:4], yyyymm[4:]


def _format_pattern(pattern: str, *, root: Path, model: str, var: str, yyyy: str | None, mm: str | None) -> Path:
    filled = pattern.format(
        root=str(root),
        model=model,
        var=var,
        yyyy=yyyy or "",
        mm=mm or "",
    )
    return Path(filled)


def load_model_local_with_patterns(
    root: Path,
    model_base: str,
    init_yyyymm: str,
    var: str,
    patterns: dict[str, str],
) -> Tuple[xr.DataArray, xr.DataArray]:
    yyyy, mm = _yyyymm_parts(init_yyyymm)

    hind_glob = _format_pattern(
        patterns["hindcast"], root=root, model=model_base, var=var, yyyy=None, mm=None
    )
    fore_glob = _format_pattern(
        patterns["forecast"], root=root, model=model_base, var=var, yyyy=yyyy, mm=mm
    )

    hc = open_netcdf_variable(hind_glob, var)
    fc = open_netcdf_variable(fore_glob, var)
    return hc, fc

def decode_cf_time(ds: xr.Dataset, time_var: str) -> xr.Dataset:
    """
    Decode a CF-compliant time coordinate safely, including non-standard
    calendars (e.g. '360_day').

    This function:
      - Decodes only the requested time variable
      - Normalizes known calendar aliases ('360' -> '360_day')
      - Uses cftime-aware decoding
      - Preserves the original calendar semantics

    Parameters
    ----------
    ds : xr.Dataset
        Dataset containing a time coordinate.
    time_var : str
        Name of the time coordinate to decode (e.g. 'T' or 'S').

    Returns
    -------
    xr.Dataset
        Dataset with decoded time coordinate (if applicable).
    """
    if time_var not in ds.coords:
        return ds

    # Work on a copy to avoid side effects
    ds = ds.copy()

    cal = ds[time_var].attrs.get("calendar")

    # Normalize non-CF calendar aliases
    if cal == "360":
        ds[time_var].attrs["calendar"] = "360_day"

    # Decode using CF conventions + cftime
    ds = xr.decode_cf(ds, decode_times=True, use_cftime=True)

    # Sanity check
    if not hasattr(ds[time_var], "dt"):
        raise ValueError(
            f"Failed to decode time coordinate '{time_var}' "
            f"to a datetime-like object."
        )

    return ds

# ============================================================
# ANOMALIES
# ============================================================

import numpy as np
import xarray as xr

def to_cptv10(X=None, Y=None):
    """
    Convert CPT-ready DataArrays into CPTv10-named DataArrays.

    Input expectations:
      X : (S, C, Y, X)  → predictor
      Y : (S, Y, X)     → predictand

    Returns:
      X_v10 : (T, C, row, col)
      Y_v10 : (T, row, col)
    """

    X_v10 = None
    Y_v10 = None

    if X is not None:
        if X.dims != ("S", "C", "Y", "X"):
            raise ValueError(f"X is not CPT-ready: dims={X.dims}")

        X_v10 = (
            X
            .rename({
                "S": "T",
                "Y": "row",
                "X": "col",
            })
            .transpose("T", "C", "row", "col")
        )

    if Y is not None:
        if Y.dims != ("S", "Y", "X"):
            raise ValueError(f"Y is not CPT-ready: dims={Y.dims}")

        Y_v10 = (
            Y
            .rename({
                "S": "T",
                "Y": "row",
                "X": "col",
            })
            .transpose("T", "row", "col")
        )

    return X_v10, Y_v10

def prepare_predictand_for_cpt(
    Y,
    season,
    hindcast_years,
):
    """
    Prepare predictand for CPT-Core CCA.

    This function:
      - Assumes Y has dims (T, Y, X) with T as datetime-like
      - Aggregates to seasonal mean for the target season
      - Computes anomalies relative to the hindcast-period climatology
      - Aligns samples to hindcast years
      - Returns CPT-ready predictand with dims (S, Y, X)

    Parameters
    ----------
    Y : xarray.DataArray
        Predictand data with dimensions (T, Y, X).
        T must be datetime-like (datetime64 or cftime).
    season : str
        Target season in 'Mon-Mon' format, e.g. 'Feb-Apr'.
    hindcast_years : array-like
        Calendar years corresponding to hindcast samples
        (typically X['S'].dt.year.values).

    Returns
    -------
    xarray.DataArray
        Seasonal-mean predictand anomalies with dims (S, Y, X),
        ready for CPT-Core.
    """

    # ---------------------------------------------------------
    # 0. Basic checks
    # ---------------------------------------------------------
    if "T" not in Y.dims:
        raise ValueError("Predictand must have dimension 'T' (time).")

    if not hasattr(Y["T"], "dt"):
        raise ValueError("Predictand time coordinate must be datetime-like.")

    hindcast_years = np.asarray(hindcast_years).astype(int)

    # ---------------------------------------------------------
    # 1. Parse season into months
    # ---------------------------------------------------------
    month_map = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
        "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
        "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }

    try:
        start_mon, end_mon = season.split("-")
        start_m = month_map[start_mon]
        end_m = month_map[end_mon]
    except Exception:
        raise ValueError("season must be in 'Mon-Mon' format, e.g. 'Feb-Apr'")

    # Handle seasons that cross year boundary (e.g., Nov-Feb)
    if end_m >= start_m:
        season_months = list(range(start_m, end_m + 1))
    else:
        season_months = list(range(start_m, 13)) + list(range(1, end_m + 1))

    # ---------------------------------------------------------
    # 2. Select season months
    # ---------------------------------------------------------
    Y_season = Y.where(
        Y["T"].dt.month.isin(season_months),
        drop=True,
    )

    if Y_season.sizes.get("T", 0) == 0:
        raise ValueError(f"No data found for season {season}")

    # ---------------------------------------------------------
    # 3. Compute seasonal mean per year
    # ---------------------------------------------------------
    # Group by calendar year AFTER selecting season months
    Y_seasonal = Y_season.groupby("T.year").mean("T")

    # ---------------------------------------------------------
    # 4. Align to hindcast years (intersection)
    # ---------------------------------------------------------
    available_years = Y_seasonal["year"].values.astype(int)
    common_years = np.intersect1d(available_years, hindcast_years)

    if common_years.size == 0:
        raise ValueError(
            f"No overlapping years between predictand ({available_years.min()}–{available_years.max()}) "
            f"and hindcasts ({hindcast_years.min()}–{hindcast_years.max()})"
        )

    Y_aligned = Y_seasonal.sel(year=common_years)

    # ---------------------------------------------------------
    # 5. Compute climatology over hindcast period
    # ---------------------------------------------------------
    clim = Y_aligned.mean("year")

    # ---------------------------------------------------------
    # 6. Convert to anomalies
    # ---------------------------------------------------------
    Y_anom = Y_aligned - clim

    # ---------------------------------------------------------
    # 7. Rename to CPT sample dimension and order dims
    # ---------------------------------------------------------
    Y_cpt = (
        Y_anom
        .rename({"year": "S"})
        .transpose("S", "Y", "X")
    )

    # ---------------------------------------------------------
    # 8. Final sanity checks
    # ---------------------------------------------------------
    if Y_cpt.dims != ("S", "Y", "X"):
        raise RuntimeError(f"Predictand has unexpected dims: {Y_cpt.dims}")

    return Y_cpt

def hindcast_climatology(hc: xr.DataArray) -> xr.DataArray:
    return hc.mean("T")


def forecast_minus_hindcast_climo(fc: xr.DataArray, hc: xr.DataArray) -> xr.DataArray:
    return fc - hindcast_climatology(hc)


# ============================================================
# GRID INTERPOLATION
# ============================================================

def interp_to_target_grid(src: xr.DataArray, target: xr.DataArray) -> xr.DataArray:
    src = _rename_to_cpt_dims(src)
    target = _rename_to_cpt_dims(target)
    return src.interp(X=target.X, Y=target.Y)


def season_to_months(season: str):
    """
    Convert season string to list of calendar months.

    Supports:
      - Climatological seasons: DJF, MAM, JJA, SON
      - Rolling seasons: FEB-APR, MAR-MAY, etc.

    Returns
    -------
    list[int]
        Months as integers 1–12
    """
    season = season.upper()

    # --- climatological shorthand ---
    clim_map = {
        "DJF": [12, 1, 2],
        "MAM": [3, 4, 5],
        "JJA": [6, 7, 8],
        "SON": [9, 10, 11],
    }

    if season in clim_map:
        return clim_map[season]

    # --- rolling seasons like FEB-APR ---
    if "-" in season:
        try:
            start, end = season.split("-")
            month_map = {
                "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
                "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
                "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
            }

            start_m = month_map[start]
            end_m = month_map[end]

            if start_m <= end_m:
                return list(range(start_m, end_m + 1))
            else:
                # wrap around year boundary (e.g. NOV-JAN)
                return list(range(start_m, 13)) + list(range(1, end_m + 1))

        except (KeyError, ValueError):
            pass

    raise ValueError(f"Unknown season '{season}'")
    

def run_deterministic_cca_only(
    X_train_v10,
    Y_v10,
):
    """
    Run deterministic CCA regression ONLY using CPT-Core.

    No validation
    No probabilistic logic
    No climatological category checks

    Parameters
    ----------
    X_train_v10 : xarray.DataArray
        Predictor in CPTv10 form with dims (T, C, Y, X)
    Y_v10 : xarray.DataArray
        Predictand in CPTv10 form with dims (T, Y, X)

    Returns
    -------
    det_fcst : xarray.DataArray
        Deterministic MOS forecast
    pev_fcst : xarray.DataArray
        Prediction error variance
    """

    # --- Mandatory CPT attributes ---
    for da in (X_train_v10, Y_v10):
        da.attrs["missing"] = -9999.0
        da.attrs["units"] = "unknown"

    # --- Check CPT executable availability (some installs expose CPT.x) ---
    import shutil
    cpt_exe = shutil.which("cpt") or shutil.which("CPT.x")
    if cpt_exe is None:
        raise RuntimeError(
            "CPT executable not found in PATH. "
            "Expected 'cpt' or 'CPT.x'. "
            "Install CPT packages from iri-nextgen and ensure the active conda environment is loaded. "
            "or ensure the command is available in the active environment."
        )

    # --- CPT instance (NO validation, NO skill) ---
    cpt = CPT(
        interactive=False,
        output_files={
            "original_predictor": "original_predictor",
            "original_predictand": "original_predictand",
            "deterministic_forecast": "deterministic_forecast",
            "prediction_error_variance": "prediction_error_variance",
        },
    )

    # --- Load predictor ---
    cptio_to_cptv10(
        X_train_v10,
        cpt.outputs["original_predictor"],
        row="Y",
        col="X",
        T="T",
        C="C",
    )

    # --- Load predictand ---
    cptio_to_cptv10(
        Y_v10,
        cpt.outputs["original_predictand"],
        row="Y",
        col="X",
        T="T",
    )

    # --- Deterministic CCA regression only ---
    cpt.write(10)   # select CCA
    cpt.write(1)    # deterministic output
    cpt.write(4)    # perform regression

    # --- Read outputs ---
    det_fcst = cpt.read(cpt.outputs["deterministic_forecast"])
    pev_fcst = cpt.read(cpt.outputs["prediction_error_variance"])

    return det_fcst, pev_fcst
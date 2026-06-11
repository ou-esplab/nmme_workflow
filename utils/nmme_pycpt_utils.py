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
    # 2. Parse target season months (handles MAM/JJA/NDJ and Feb-Apr style)
    # -----------------------------
    season_months = season_to_months(season)

    # -----------------------------
    # 3. Convert season months to leads
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
    # Normalize lead and member dimension names to L and M
    if "lead" in da.dims and "L" not in da.dims:
        rename["lead"] = "L"
    if "member" in da.dims and "M" not in da.dims:
        rename["member"] = "M"

    if rename:
        da = da.rename(rename)

    for c in ("Y", "X", "T"):
        if c in da.coords:
            da = da.sortby(c)

    # Normalize X (lon) from 0-360 to -180/180 if needed, then re-sort
    if "X" in da.coords and float(da["X"].max()) > 180.0:
        da = da.assign_coords(X=((da["X"] + 180) % 360) - 180)
        da = da.sortby("X")

    return da


def _open_files_with_year_from_name(files: list, var: str) -> xr.Dataset:
    """
    Fallback loader for model files that lack a usable time coordinate
    (e.g. NOAA-SFS reforecast files where init=0 in every file).

    Parses the year from each filename (second-to-last underscore-separated
    token, matching the NMME convention: {var}_{model}_{YYYY}_{MM}.nc).
    Renames the init/time-like dimension to 'S' and sets year as coordinate.
    """
    datasets = []
    for fp in sorted(files):
        stem_parts = Path(fp).stem.split("_")
        try:
            year = int(stem_parts[-2])
        except (ValueError, IndexError):
            raise ValueError(f"Cannot parse year from filename: {fp}")

        ds = xr.open_dataset(fp, decode_times=False, engine="netcdf4")

        # Find and rename the init/time-like dimension to 'S'
        for dim in ("init", "S", "T", "time"):
            if dim in ds.dims:
                ds = ds.assign_coords({dim: [year]})
                if dim != "S":
                    ds = ds.rename({dim: "S"})
                break

        datasets.append(ds)

    return xr.concat(datasets, dim="S")


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

    # Open files — use by_coords when possible, fall back to loading individually
    # (needed for models like NOAA-SFS whose files lack a usable time coordinate)
    if len(valid_files) == 1:
        ds = xr.open_dataset(valid_files[0], decode_times=False, engine="netcdf4")
    else:
        try:
            ds = xr.open_mfdataset(
                valid_files, combine="by_coords", decode_times=False, engine="netcdf4"
            )
        except Exception:
            ds = _open_files_with_year_from_name(valid_files, var)

    # Decode CF time only when the coordinate has a CF 'units' attribute
    # (plain integer year coords from the fallback loader don't need decoding)
    for tvar in ("T", "S"):
        if tvar in ds.coords and "units" in ds[tvar].attrs:
            ds = decode_cf_time(ds, time_var=tvar)
            break

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
    pattern_overrides: dict[str, dict[str, str]] | None = None,
) -> Tuple[xr.DataArray, xr.DataArray]:
    yyyy, mm = _yyyymm_parts(init_yyyymm)

    # Use per-model override if available, else fall back to global patterns
    effective = (pattern_overrides or {}).get(model_base, patterns)

    hind_glob = _format_pattern(
        effective["hindcast"], root=root, model=model_base, var=var, yyyy=None, mm=mm
    )
    fore_glob = _format_pattern(
        effective["forecast"], root=root, model=model_base, var=var, yyyy=yyyy, mm=mm
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
    # 1. Parse season into months (handles MAM/JJA/NDJ and Feb-Apr style)
    # ---------------------------------------------------------
    season_months = season_to_months(season)

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

def aggregate_predictand_to_season(
    Y: xr.DataArray,
    season: str,
    hindcast_years,
) -> xr.DataArray:
    """
    Aggregate observations to seasonal means aligned with hindcast years.

    Returns raw seasonal means (not anomalies) with dims (S, Y, X).
    Use this when passing Y to CPT with tailoring='Anomaly' so CPT
    computes anomalies internally.
    """
    if "T" not in Y.dims:
        raise ValueError("Predictand must have dimension 'T' (time).")
    if not hasattr(Y["T"], "dt"):
        raise ValueError("Predictand time coordinate must be datetime-like.")

    hindcast_years = np.asarray(hindcast_years).astype(int)
    season_months = season_to_months(season)

    Y_season = Y.where(Y["T"].dt.month.isin(season_months), drop=True)
    if Y_season.sizes.get("T", 0) == 0:
        raise ValueError(f"No data found for season {season}")

    Y_seasonal = Y_season.groupby("T.year").mean("T")

    available_years = Y_seasonal["year"].values.astype(int)
    common_years = np.intersect1d(available_years, hindcast_years)
    if common_years.size == 0:
        raise ValueError(
            f"No overlapping years between predictand ({available_years.min()}–{available_years.max()}) "
            f"and hindcasts ({hindcast_years.min()}–{hindcast_years.max()})"
        )

    Y_aligned = Y_seasonal.sel(year=common_years)
    return Y_aligned.rename({"year": "S"}).transpose("S", "Y", "X")


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

    # --- all 12 rolling 3-month season codes ---
    clim_map = {
        "DJF": [12, 1, 2],
        "JFM": [1, 2, 3],
        "FMA": [2, 3, 4],
        "MAM": [3, 4, 5],
        "AMJ": [4, 5, 6],
        "MJJ": [5, 6, 7],
        "JJA": [6, 7, 8],
        "JAS": [7, 8, 9],
        "ASO": [8, 9, 10],
        "SON": [9, 10, 11],
        "OND": [10, 11, 12],
        "NDJ": [11, 12, 1],
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


# ============================================================
# PYCPT OUTPUT HELPERS
# ============================================================

from datetime import date as _date
import os as _os


def prepare_forecast_predictor_v10(
    fc_list: list,
    model_names: list,
    fcst_year: int = 2000,
) -> "xr.DataArray":
    """
    Assemble per-model forecast ensemble means into a CPTv10-formatted
    forecast predictor array.

    Parameters
    ----------
    fc_list : list of xr.DataArray
        Per-model forecast ensemble means, each with dims (Y, X).
        Must be in the same spatial subset and lead as X_train.
    model_names : list of str
        Model names corresponding to fc_list entries.

    Returns
    -------
    X_fcst_v10 : xr.DataArray
        Dims (T=1, C, row, col) with numeric C index and required CPT attrs.
    """
    # Squeeze time/init dim (forecast has a single time step; we want (Y, X) per model)
    squeezed = []
    for da in fc_list:
        for dim in ("S", "T", "time", "init"):
            if dim in da.dims:
                da = da.squeeze(dim, drop=True)
        squeezed.append(da)

    # Stack models along M (not C — cptio adds clim_prob only for dim named 'C')
    X_fcst = xr.concat(squeezed, dim="M").assign_coords(M=model_names)
    fcst_dt = np.array([f"{fcst_year}-01-01"], dtype="datetime64[ns]")
    X_fcst = X_fcst.expand_dims("T").assign_coords(T=fcst_dt)
    X_fcst = X_fcst.transpose("T", "M", "Y", "X")

    # Numeric M index
    X_fcst = X_fcst.assign_coords(M=("M", np.arange(1, X_fcst.sizes["M"] + 1)))
    X_fcst.attrs["M_names"] = model_names
    X_fcst.attrs["missing"] = -9999.0
    X_fcst.attrs["units"] = "unknown"

    return X_fcst


def save_pycpt_results(
    results: dict,
    region_name: str,
    season: str,
    var: str,
    lev: str,
    fcstdate: str,
    output_root: str,
) -> None:
    """
    Write pycpt CCA results to NetCDF files matching the products convention.

    Parameters
    ----------
    results : dict
        Keys are model names + "MME". Values are xr.Dataset with variables
        "deterministic" and "probabilistic" as returned by
        canonical_correlation_analysis when F is provided.
    region_name, season, var, lev, fcstdate : str
        Used in directory and file naming.
    output_root : str
        Root directory (data.output.pycpt in config).
    """
    safe_season = season.replace("-", "")
    outdir = _os.path.join(output_root, fcstdate, region_name, safe_season, "data")
    _os.makedirs(outdir, exist_ok=True)

    det_vars = {}
    prob_vars = {}

    for model, fcst in results.items():
        safe_model = model.replace(".", "_").replace("-", "_")

        det = fcst["deterministic"]
        if "T" in det.dims:
            det = det.isel(T=0)
        # rename Y/X → lat/lon for output
        rename = {}
        if "Y" in det.dims: rename["Y"] = "lat"
        if "X" in det.dims: rename["X"] = "lon"
        if rename:
            det = det.rename(rename)
        det.attrs["long_name"] = f"{model} MOS deterministic anomaly"
        det_vars[safe_model] = det

        prob = fcst["probabilistic"]
        if "T" in prob.dims:
            prob = prob.isel(T=0)
        # C dim → cat dim with labels
        if "C" in prob.dims:
            prob = prob.rename({"C": "cat"}).assign_coords(cat=["bn", "nn", "an"])
        rename = {}
        if "Y" in prob.dims: rename["Y"] = "lat"
        if "X" in prob.dims: rename["X"] = "lon"
        if rename:
            prob = prob.rename(rename)
        # convert 0-1 to percent if needed
        if float(prob.max()) <= 1.01:
            prob = prob * 100.0
        prob.attrs["long_name"] = f"{model} MOS tercile probabilities (%)"
        prob_vars[safe_model] = prob

    # --- deterministic output ---
    ds_det = xr.Dataset(det_vars)
    _set_pycpt_attrs(ds_det, var, lev, fcstdate, season, region_name, "deterministic")
    det_path = _os.path.join(
        outdir,
        f"NMME_fcst_{fcstdate}.pycpt.det.anom.{var}_{lev}.{region_name}.{safe_season}.nc",
    )
    ds_det.to_netcdf(det_path)
    print(f"[SAVED] {det_path}")

    # --- probabilistic output ---
    ds_prob = xr.Dataset(prob_vars)
    _set_pycpt_attrs(ds_prob, var, lev, fcstdate, season, region_name, "probabilistic")
    prob_path = _os.path.join(
        outdir,
        f"NMME_fcst_{fcstdate}.pycpt.prob.{var}_{lev}.{region_name}.{safe_season}.nc",
    )
    ds_prob.to_netcdf(prob_path)
    print(f"[SAVED] {prob_path}")


def _set_pycpt_attrs(ds, var, lev, fcstdate, season, region, kind):
    ds.attrs["title"] = f"NMME PyCPT MOS {kind} forecast"
    ds.attrs["var"] = var
    ds.attrs["level"] = lev
    ds.attrs["fcst_date"] = fcstdate
    ds.attrs["target_season"] = season
    ds.attrs["region"] = region
    ds.attrs["units"] = "mm/day" if var == "prec" else "degC" if var in ("tref", "sst") else "unknown"
    ds.attrs["source"] = "nmme_workflow pycpt"
    ds.attrs["institution"] = "U. Oklahoma, School of Meteorology"
    ds.attrs["CreationDate"] = str(_date.today())
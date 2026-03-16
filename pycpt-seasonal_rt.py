#!/usr/bin/env python3
# coding: utf-8
"""
PyCPT v2 seasonal driver with dual CLI support:
  A) Legacy flags (still supported):
       --regname ... --lat_minmax a b --lon_minmax c d --training_season Mon-Mon --fcstdate YYYYMM
  B) Runner-friendly interface (new):
       pycpt-seasonal_rt.py confignmme.yaml YYYYMM [--only RegionName] [--models M1 M2 ...]

Model list resolution (per region), in this priority:
  1) region.models (in pycpt_regions)
  2) top-level models: models:
  3) CLI --models

Predictor names are auto-resolved against CPT-DL (dl.models.keys()):
  e.g., "NCEP-CFSv2" -> "CFSv2.PRCP", "NASA-GEOSS2S" -> "GEOSS2S.PRCP",
        "GEM5.2-NEMO" -> "GEM5.2-NEMO.PRCP" (or fallback to "GEMNEMO.PRCP" if needed)

Everything else (download_args, cpt_args, plotting, saving) is preserved from your notebook script.
"""

import argparse
import datetime as dt
import os
from pathlib import Path

import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature

# PyCPT & CPT-DL
import pycpt
import cptdl as dl
from cptextras import get_colors_bars


def load_predictand_local(predictand_path, var_name=None):
    """
    Load observed precip from single file, glob, or directory.
    For CHIRPS, var_name defaults to 'precip' if not provided.
    """
    from pathlib import Path
    import glob, xarray as xr

    p = Path(predictand_path)
    if p.is_dir():
        files = sorted(glob.glob(str(p / "*.nc")))
    else:
        files = sorted(glob.glob(str(p)))  # allow a single file or glob

    if not files:
        raise FileNotFoundError(f"No NetCDF files found for {predictand_path}")

    ds = xr.open_mfdataset(files, combine="by_coords", parallel=False)

    # Default intelligently if not provided
    if var_name is None:
        # Prefer 'precip' (CHIRPS), fallback to 'pr'
        if "precip" in ds.data_vars:
            var_name = "precip"
        elif "pr" in ds.data_vars:
            var_name = "pr"
        else:
            raise KeyError(f"Could not infer precip variable. Available: {list(ds.data_vars)}")

    if var_name not in ds.data_vars:
        raise KeyError(f"Variable '{var_name}' not found. Available: {list(ds.data_vars)}")

    da = ds[var_name]
    da.name = "precip"
    return da
# --------------------------
# Helpers you already had
# --------------------------
def standardize_mos_prob(mos_prob):
    if "T" in mos_prob.dims:
        mos_prob = mos_prob.isel(T=0)
    if "C" not in mos_prob.dims:
        raise ValueError(f"Expected dim 'C' in MOS probabilistic, got dims={mos_prob.dims}")
    mos_prob = mos_prob.rename({"C": "cat"}).assign_coords(cat=["bn","nn","an"]).sel(cat=["bn","nn","an"])
    if float(mos_prob.max()) <= 1.01:
        mos_prob = mos_prob * 100.0
    return mos_prob

def hindcast_terciles_from_anoms(hc_anom):
    if "T" not in hc_anom.dims:
        raise ValueError(f"Expected hindcast to have 'T' dim, got dims={hc_anom.dims}")
    t33 = hc_anom.quantile(0.33, dim="T")
    t66 = hc_anom.quantile(0.66, dim="T")
    if "quantile" in t33.dims: t33 = t33.isel(quantile=0, drop=True)
    if "quantile" in t66.dims: t66 = t66.isel(quantile=0, drop=True)
    if "quantile" in t33.coords: t33 = t33.reset_coords("quantile", drop=True)
    if "quantile" in t66.coords: t66 = t66.reset_coords("quantile", drop=True)
    return t33, t66

def raw_onehot_terciles_percent(fc_anom, t33, t66):
    if "T" in fc_anom.dims:
        fc_anom = fc_anom.isel(T=-1)
    bn = (fc_anom <  t33).astype(float) * 100.0
    an = (fc_anom >= t66).astype(float) * 100.0
    nn = 100.0 - bn - an
    out = xr.concat([bn, nn, an], dim="cat").assign_coords(cat=["bn","nn","an"])
    return out

def _latlon_names(da):
    lat_candidates = ["lat", "latitude", "Y"]
    lon_candidates = ["lon", "longitude", "X"]
    lat = next((c for c in lat_candidates if c in da.coords), None)
    lon = next((c for c in lon_candidates if c in da.coords), None)
    if lat is None or lon is None:
        raise ValueError(f"Could not infer lat/lon coordinate names. coords={list(da.coords)}")
    return lat, lon

def interp_to_target_grid_safe(src, target):
    if "cat" in target.dims:
        target2 = target.isel(cat=0, drop=True)
    else:
        target2 = target
    tlat, tlon = _latlon_names(target2)
    if "cat" in src.dims:
        outs = []
        for c in src["cat"].values:
            sl = src.sel(cat=c)
            slat, slon = _latlon_names(sl)
            out_c = sl.interp({slat: target2[tlat], slon: target2[tlon]}, method="linear")
            out_c = out_c.expand_dims(cat=[c])
            outs.append(out_c)
        out = xr.concat(outs, dim="cat").assign_coords(cat=src["cat"].values)
        return out
    slat, slon = _latlon_names(src)
    return src.interp({slat: target2[tlat], slon: target2[tlon]}, method="linear")

def _select_forecast_slice(da):
    if "target" in da.dims: da = da.isel(target=0)
    if "T" in da.dims:      da = da.isel(T=-1)
    if "time" in da.dims:   da = da.isel(time=-1)
    return da

def _hindcast_climatology(hc):
    if "target" in hc.dims: hc = hc.isel(target=0)
    if "T" in hc.dims:      return hc.mean("T")
    if "time" in hc.dims:   return hc.mean("time")
    raise ValueError(f"Can't find time/year dim in hindcast. dims={hc.dims}")

def compute_raw_anom(forecast_da, hindcast_da):
    raw_field = _select_forecast_slice(forecast_da)
    raw_clim  = _hindcast_climatology(hindcast_da)
    return raw_field - raw_clim

def safe_tag(name: str) -> str:
    return name.replace(".", "_").replace("/", "_").replace(" ", "_")

def _to_anomalies(
    da: xr.DataArray | xr.Dataset,
    *,
    hindcast: xr.DataArray | xr.Dataset | None = None,
    time_dim: str | None = None,
    group: str = "monthly",
    clim: xr.DataArray | xr.Dataset | None = None,
    prefer_coord: bool = True,
) -> xr.DataArray:
    """
    Convert data to anomalies (da - climatology).

    Recommended for PyCPT:
      - Use hindcast-based climatology for BOTH hindcasts and forecasts.
        fc_anom = fc - mean(hc, 'T')
        hc_anom = hc - mean(hc, 'T')

    Parameters
    ----------
    da : xr.DataArray | xr.Dataset
        Input field (hindcast or forecast). If Dataset, the first data var is used.
    hindcast : xr.DataArray | xr.Dataset | None, default None
        Hindcast dataset used to compute climatology. If provided, clim is ignored.
    time_dim : str | None, default None
        Name of the time dimension. If None, it attempts to infer it from ('T', 'time', 'year').
    group : {'monthly','seasonal','none'}, default 'monthly'
        How to compute climatology:
          - 'monthly'  : mean by calendar month
          - 'seasonal' : mean by 3-month rolling season anchored on the center month
          - 'none'     : plain mean along time_dim
    clim : xr.DataArray | xr.Dataset | None, default None
        Precomputed climatology to subtract (must broadcast against da). If provided,
        it overrides 'hindcast' and 'group'.
    prefer_coord : bool, default True
        Prefer grouping on a coord like 'time.month' if present.

    Returns
    -------
    xr.DataArray
        Anomaly field with the same shape as 'da'.

    Notes
    -----
    - If neither 'hindcast' nor 'clim' is provided, this falls back to da's own
      climatology (explicit warning). That is *not* recommended for MOS/CPT workflows.
    - The function attempts to rename common coord/dim names to ('X','Y','T') and back.
    """

    # ---- Select DataArray and normalize dims ----
    if isinstance(da, xr.Dataset):
        var = list(da.data_vars)[0]
        da = da[var]

    # Try to infer time dimension if not given
    if time_dim is None:
        for cand in ("T", "time", "year"):
            if cand in da.dims:
                time_dim = cand
                break
        if time_dim is None and "time" in da.coords:
            time_dim = "time"
    if time_dim is None:
        raise ValueError("Could not infer time dimension. Please set time_dim=('T','time', or 'year').")

    # ---- Helper: compute monthly/seasonal climatology for a DataArray ----
    def _climatology_from(source: xr.DataArray) -> xr.DataArray:
        if group == "monthly":
            # Prefer 'time.month' or '<time_dim>.month' if available
            if prefer_coord and time_dim in source.coords and hasattr(source[time_dim], "dt"):
                return source.groupby(f"{time_dim}.dt.month").mean(time_dim)
            # coordinate-less monthly grouping (fallback)
            if hasattr(source[time_dim], "dt"):
                return source.groupby(f"{time_dim}.dt.month").mean(time_dim)
            # If no datetime dtype, assume already monthly indexed by an integer month coord
            if "month" in source.coords:
                return source.groupby("month").mean(time_dim)
            # Last resort: plain mean (not ideal)
            return source.mean(time_dim)

        elif group == "seasonal":
            # 3-month centered rolling mean climatology, then mean across time
            # Create a rolling mean and average by the center month
            if hasattr(source[time_dim], "dt"):
                rolled = source.rolling({time_dim: 3}, center=True, min_periods=2).mean()
                return rolled.groupby(f"{time_dim}.dt.month").mean(time_dim)
            # If not datetime, fallback to plain mean
            return source.mean(time_dim)

        elif group == "none":
            return source.mean(time_dim)

        else:
            raise ValueError("group must be one of {'monthly','seasonal','none'}")

    # Convert Dataset climatology to DataArray if needed
    def _to_dataarray(x):
        if isinstance(x, xr.Dataset):
            return x[list(x.data_vars)[0]]
        return x

    # ---- Decide climatology to subtract ----
    if clim is not None:
        climatology = _to_dataarray(clim)

    elif hindcast is not None:
        hc = _to_dataarray(hindcast)
        # Ensure hindcast shares space dims with da
        # (Broadcasting will handle basic differences; align if necessary)
        try:
            hc, da_align = xr.align(hc, da, join="outer")
        except Exception:
            da_align = da  # fallback; broadcast may still work
        climatology = _climatology_from(hc)

    else:
        # Self-climatology fallback (OK for quick diagnostics; not for MOS/CPT)
        print("[WARN] _to_anomalies: using SELF-climatology (no hindcast/clim provided). "
              "This is not recommended for MOS/CPT training.")
        climatology = _climatology_from(da)

    # ---- Subtract climatology with grouping‑aware broadcast ----
    # If climatology is grouped by month (or season), we need to match the grouping key
    # of 'da' before subtraction.
    if "month" in climatology.dims or "month" in climatology.coords:
        # Try to get month from da's time coord
        if hasattr(da[time_dim], "dt"):
            anomalies = da.groupby(f"{time_dim}.dt.month") - climatology
        elif "month" in da.coords:
            anomalies = da.groupby("month") - climatology
        else:
            # Cannot group; last resort: plain broadcast subtraction
            anomalies = da - climatology
    else:
        anomalies = da - climatology

    return anomalies


# --------------------------
# NEW: dual CLI + YAML load
# --------------------------
def _load_yaml_if_given(config_path: str | None) -> dict:
    if not config_path: return {}
    p = Path(config_path).expanduser().resolve()
    if not p.exists(): raise FileNotFoundError(f"Config not found: {p}")
    import yaml
    with p.open("r") as f:
        return yaml.safe_load(f) or {}

def _region_from_yaml(cfg: dict, regname: str) -> dict:
    regs = cfg.get("pycpt_regions", [])
    for r in regs:
        if r.get("name") == regname:
            return r
    raise ValueError(f"Region '{regname}' not found in YAML.")

def _normalize_models(seq):
    if not seq: return None
    out = [str(m).strip() for m in seq if str(m).strip()]
    return out or None

def _models_for_region(cfg: dict, regname: str, cli_models: list[str] | None):
    r = _region_from_yaml(cfg, regname) if cfg else {}
    # 1) region override
    m = _normalize_models(r.get("models"))
    if m: return m
    # 2) top-level
    m = _normalize_models(cfg.get("models") if cfg else None)
    if m: return m
    # 3) CLI
    m = _normalize_models(cli_models)
    return m

def _resolve_predictor_names(
    models: list[str] | None,
    variable: str = "PRCP",
    cfg: dict | None = None
) -> list[str]:
    """
    Resolve your curated NMME model IDs to CPT-DL predictor keys that
    actually exist on THIS installation by probing dl.hindcasts/forecasts.

    Priority:
      1) YAML 'model_key_overrides' (exact base key, w/o .<variable>)
      2) Heuristic discovery: try multiple name variants, prefixes, and fuzzy contains
    Returns: ['<predictor_base>.<variable>', ...]
    """

    if not models:
        return []

    # 1) Build the available keys set from this CPT-DL install
    try:
        avail_h = set(dl.hindcasts.keys())
    except Exception:
        avail_h = set()
    try:
        avail_f = set(dl.forecasts.keys())
    except Exception:
        avail_f = set()
    avail = avail_h | avail_f  # names like 'CFSv2.PRCP', 'CanSIPS-IC4.CanESM5.PRCP', ...

    # Derive base-name set (without trailing '.PRCP', '.T2m', etc.)
    avail_bases = set()
    for k in avail:
        if "." in k:
            base, _var = k.rsplit(".", 1)
            avail_bases.add(base)
        else:
            avail_bases.add(k)

    # Lowercase maps for case-insensitive fuzzy lookup
    avail_bases_lc = {b.lower(): b for b in avail_bases}

    # Helper for normalization
    def norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())

    # Optional exact overrides from YAML
    overrides = {}
    if cfg and isinstance(cfg.get("model_key_overrides"), dict):
        overrides = {str(k): str(v) for k, v in cfg["model_key_overrides"].items()}

    # Heuristic candidate builder
    def candidates_for(model_id: str) -> list[str]:
        """
        Produce a list of reasonable predictor *bases* to try for a model ID.
        Try literal, punctuation-stripped, and common group prefixes.
        """
        raw = model_id.strip()
        bases: list[str] = []

        # Literal guesses
        bases += [raw, raw.replace(" ", ""), raw.replace("_", ""), raw.replace("-", ""), raw.replace(".", "")]

        # Common grouping prefixes seen in CPT-DL catalogs
        groups = [
            "CanSIPS-IC4.", "CanSIPSIC4.", "COLA-RSMAS-", "COLA.", "NCAR-", "NASA-", "ECCC-",
            "GFDL-", "NOAA-", "IRI-", "NMME.",  # keep this list small; fuzzy will backstop us
        ]
        for g in groups:
            bases.append(g + raw)
            bases.append(g + raw.replace("-", ""))
            bases.append(g + raw.replace(".", ""))

        # De-duplicate while preserving order
        seen = set(); out = []
        for b in bases:
            if b not in seen:
                out.append(b); seen.add(b)
        return out

    predictor_names: list[str] = []

    for m in models:
        # 2a) YAML override wins immediately (exact predictor base)
        if m in overrides:
            base = overrides[m]
            predictor_names.append(f"{base}.{variable}")
            continue

        # 2b) Try exact/form variants from candidates()
        picked: str | None = None
        cand = candidates_for(m)
        for base in cand:
            key = f"{base}.{variable}"
            if key in avail:
                picked = base
                break
            # try case-insensitive lookup of the base + variable
            base_lc = base.lower()
            if f"{base_lc}.{variable.lower()}" in {k.lower() for k in avail}:
                # recover original-cased base if present
                picked = avail_bases_lc.get(base_lc, base)
                # verify resulting key really exists with correct case
                if f"{picked}.{variable}" in avail:
                    break

        # 2c) Fuzzy fallback: any available base that *contains* the normalized token
        if picked is None:
            nm = norm(m)
            # rank candidates by shared token length
            scored: list[tuple[int, str]] = []
            for b_lc, b in avail_bases_lc.items():
                if nm and nm in norm(b):
                    scored.append((len(nm), b))
            if scored:
                # prefer longest token match (rough proxy for specificity)
                scored.sort(reverse=True)
                picked = scored[0][1]

        if picked is None:
            print(f"[WARN] No CPT-DL predictor base found for '{m}'. "
                  f"Add 'model_key_overrides: {{ {m}: <PredictorBase> }}' in YAML.")
            continue

        predictor_names.append(f"{picked}.{variable}")

    if predictor_names:
        print("[INFO] predictor_names:", ", ".join(predictor_names))
    else:
        print("[WARN] predictor_names resolved to empty set.")
    return predictor_names
# --------------------------
# Parse args (legacy + new)
# --------------------------
parser = argparse.ArgumentParser()
# NEW positional (runner interface):
parser.add_argument("config", nargs="?", help="Path to confignmme.yaml")
parser.add_argument("fcstdate_pos", nargs="?", help="Forecast date YYYYMM")
parser.add_argument("--only", nargs="+", default=None, help="Region name(s) to run (exact match in YAML)")
parser.add_argument("--models", nargs="+", default=None, help="Space-separated models to use (overrides if YAML lacks them)")
# legacy args (kept for compatibility)
parser.add_argument("--regname", nargs='?', default=None, help="Legacy: region name for labeling")
parser.add_argument("--lat_minmax", nargs=2, type=float)
parser.add_argument("--lon_minmax", nargs=2, type=float)
parser.add_argument("--training_season", nargs='?', default=None, help="e.g., Feb-Apr")
parser.add_argument("--fcstdate", nargs='?', default=None, help="Legacy YYYYMM or YYYYMMDD")
args = parser.parse_args()

# Decide which mode we’re in
cfg = _load_yaml_if_given(args.config) if args.config else {}

# Merge with defaults so we never NameError
LOCAL_ROOT = Path(cfg.get("LOCAL_ROOT", "/data/esplab"))
MODEL_BASE = Path(cfg.get("MODEL_BASE", "/data/esplab/nmme-backup"))
MODEL_PR_VAR = cfg.get("MODEL_PR_VAR", "pr")

# Pull from your config (fall back to your real defaults)
PREDICTAND_DIR = Path(cfg.get(
    "PREDICTAND_DIR",
    "/data/esplab/shared/obs/gridded/atm/precip/daily/chirps-v2.0/p05"
))
PREDICTAND_VAR = cfg.get("PREDICTAND_VAR", "precip")

if args.config and args.fcstdate_pos:
    # New runner mode
    fcstdate_str = args.fcstdate_pos
    # Choose exactly one region via --only
    if not args.only or len(args.only) != 1:
        raise SystemExit("[ERROR] Provide exactly one --only <RegionName> with YAML mode.")
    regname = args.only[0]
    r = _region_from_yaml(cfg, regname)
    # Fill legacy-style fields from YAML
    args.regname = regname
    args.lat_minmax = [float(r["lat"][0]), float(r["lat"][1])]
    args.lon_minmax = [float(r["lon"][0]), float(r["lon"][1])]
    args.training_season = str(r["season"])
    args.fcstdate = fcstdate_str
else:
    # Legacy mode requires these
    missing = []
    if not args.regname:          missing.append("--regname")
    if not args.lat_minmax:       missing.append("--lat_minmax a b")
    if not args.lon_minmax:       missing.append("--lon_minmax a b")
    if not args.training_season:  missing.append("--training_season Mon-Mon")
    if not (args.fcstdate or args.fcstdate_pos): missing.append("--fcstdate YYYYMM")
    if missing:
        raise SystemExit("[ERROR] Missing required arguments (legacy mode): " + ", ".join(missing))
    # Normalize fcstdate
    args.fcstdate = args.fcstdate or args.fcstdate_pos

# ------------- Directories & common settings -------------
# Default to local data unless overridden by caller/config
USE_LOCAL_DATA = True

case_dir = Path("/data/esplab/shared/model/initialized/nmme/forecast/pycpt") / args.fcstdate
files_root = case_dir
figs_dir   = case_dir / "figs"
os.makedirs(figs_dir, exist_ok=True)
path_pt = str(figs_dir)

MOS = 'CCA'  # or PCR
predictand_name = 'UCSB.PRCP'  # EDIT if needed

# Build download_args from (YAML-or-legacy) fields
download_args = {
    'fdate': dt.datetime.strptime(args.fcstdate[:6], "%Y%m"),
    'first_year': 1993,        # EDIT if needed
    'final_year': 2016,        # EDIT if needed
    'target': args.training_season,
    'predictor_extent': {
        'east':  args.lon_minmax[1],
        'west':  args.lon_minmax[0],
        'north': args.lat_minmax[1],
        'south': args.lat_minmax[0],
    },
    'predictand_extent': {
        'east':  args.lon_minmax[1],
        'west':  args.lon_minmax[0],
        'north': args.lat_minmax[1],
        'south': args.lat_minmax[0],
    },
    'filetype': 'cptv10.tsv',
}

cpt_args = {
    'transform_predictand': None,
    'tailoring': 'Anomaly',
    'cca_modes': (1,3),
    'x_eof_modes': (1,8),
    'y_eof_modes': (1,6),
    'validation': 'crossvalidation',
    'drymask': False,
    'scree': True,
    'crossvalidation_window': 5,
    'synchronous_predictors': True,
    # 'cpt_kwargs': {'interactive': True, 'outputdir': 'temp-outputs'},
}

#force_download = True

# ------------------ Resolve predictor_names dynamically ------------------
# New resolution: region -> top-level YAML -> CLI --models
resolved_models = _models_for_region(cfg, args.regname, args.models)
#predictor_names = _resolve_predictor_names(resolved_models, variable="PRCP")
predictor_names = _resolve_predictor_names(resolved_models, variable="PRCP", cfg=cfg)

if not predictor_names:
    # Safe fallback: keep your historical trio as last resort
    predictor_names = ["CFSv2.PRCP","GEOSS2S.PRCP","GEMNEMO.PRCP"]
    print("[WARN] Using fallback predictor_names:", predictor_names)

# ------------------ Run PyCPT as you had it ------------------
domain_dir = pycpt.setup(case_dir, download_args["predictor_extent"])

# Visualize domains (unchanged)
pycpt.plot_domains(download_args['predictor_extent'], download_args['predictand_extent'])

if USE_LOCAL_DATA:
    # 1) Build predictor_names from the predictor base keys you resolved earlier
    #    (If you’re using the YAML+resolver we added, you should already have `resolved_models`.)
    #    If you’re still using hard-coded predictor_names, keep them—but point to your local files below.
    #
    # Example from your resolved model IDs:
    #   resolved_models: ['NASA-GEOSS2S','CanESM5','GEM5.2-NEMO','NCEP-CFSv2','COLA-RSMAS-CCSM4']
    #
    predictor_names = []
    hindcast_data   = []
    forecast_data   = []

    # Map your curated model IDs to the *local* model_base (folder name) you use.
    # If your local folder names match the CPT-DL bases, keep as-is.
    MODEL_BASE = {
        "NASA-GEOSS2S"     : "GEOSS2S",
        "CanESM5"          : "CanSIPS-IC4.CanESM5",   # EDIT if your local base differs
        "GEM5.2-NEMO"      : "GEM5.2-NEMO",           # or "GEMNEMO"
        "NCEP-CFSv2"       : "CFSv2",
        "COLA-RSMAS-CCSM4" : "CCSM4",
        "NCAR-CESM1"       : "CESM1",
        "GFDL-SPEAR"       : "GFDL-SPEAR",            # or "SPEAR"
    }

    # Choose the version of the model list you’re using:
    #   - If you have 'resolved_models' already (from YAML/CLI), iterate that list.
    #   - Otherwise, derive from your previous predictor_names by stripping '.PRCP'.
    model_id_list = resolved_models if 'resolved_models' in globals() else [p.split(".")[0] for p in predictor_names]

    # Load predictand Y from local file (EDIT: path & format)
    # Example NetCDF file with obs anomalies over your predictand grid:
    PREDICTAND_FILE = LOCAL_ROOT / "observations" / "UCSB.PRCP" / "UCSB_PRCP_anom_1993-2020.nc"
    Y = load_predictand_local(PREDICTAND_FILE)

    for mid in model_id_list:
        if mid not in MODEL_BASE:
            print(f"[WARN] Skip model without mapping: {mid}")
            continue
        base = MODEL_BASE[mid]
        hc, fc = load_model_local(base, args.fcstdate, variable="PRCP")
        if (hc is None) or (fc is None):
            print(f"[WARN] Skipping model {mid} due to missing local data.")
            continue
        fc_anom = _to_anomalies(fc, hindcast=hc, time_dim="T", group="monthly")

        hindcast_data.append(hc)
        forecast_data.append(fc_anom)
        
        # Predictor name is the *predictor base* plus '.PRCP' (just for labeling downstream)
        predictor_names.append(f"{base}.PRCP")

    if len(predictor_names) == 0:
        raise RuntimeError("No local models loaded. Check LOCAL_ROOT, MODEL_BASE mapping, and file paths.")
        
else:
    # Original download path (unchanged)
    Y, hindcast_data, forecast_data = pycpt.download_data(
        predictand_name, None, predictor_names, download_args, files_root, force_download
    )

    
# Evaluate models (unchanged)
interactive = False
hcsts, fcsts, skill, pxs, pys = pycpt.evaluate_models(
    hindcast_data, MOS, Y, forecast_data, cpt_args, domain_dir, predictor_names, interactive
)

ic_title = download_args['fdate'].strftime("%m/%d/%Y")
ic_file  = download_args['fdate'].strftime("%m%d%Y")
skill_metrics = [
    "pearson",
    "roc_area_below_normal",
    "roc_area_above_normal",
    "rank_probability_skill_score"
]

pycpt.plot_skill(predictor_names, skill, MOS, domain_dir, skill_metrics)
print("Saving fig to:", os.path.join(path_pt, f"ens_skill_seas_{download_args['target']}_{args.regname}.png"))
plt.savefig(os.path.join(path_pt, f"ens_skill_seas_{download_args['target']}_{args.regname}.png"))

# ---- Probabilistic forecast (uses the *resolved* predictor_names) ----
raw_probs = {}
mos_probs = {}
for i, name in enumerate(predictor_names):
    mos_p = standardize_mos_prob(fcsts[i]["probabilistic"])
    mos_probs[name] = mos_p
    t33, t66 = hindcast_terciles_from_anoms(hindcast_data[i])
    raw_p = raw_onehot_terciles_percent(forecast_data[i], t33, t66)
    raw_probs[name] = interp_to_target_grid_safe(raw_p, mos_p)

raw_ensmean = xr.concat([raw_probs[n] for n in predictor_names], dim="model").mean("model")
mos_ensmean = xr.concat([mos_probs[n] for n in predictor_names], dim="model").mean("model")

# Probabilistic plotting (unchanged)
pmin, pmax = 0, 100
pticks = np.arange(0, 101, 20)
extent = [
    download_args["predictand_extent"]["west"],
    download_args["predictand_extent"]["east"],
    download_args["predictand_extent"]["south"],
    download_args["predictand_extent"]["north"],
]
cat_labels = {"bn": "Below Normal (BN)", "nn": "Near Normal (NN)", "an": "Above Normal (AN)"}
cats = ["bn", "nn", "an"]
cat_cmaps = {"bn": "Blues", "nn": "Greens", "an": "YlOrRd"}

fig, axes = plt.subplots(2, 3, figsize=(20, 10), subplot_kw={"projection": ccrs.PlateCarree()})
plt.subplots_adjust(left=0.05,right=0.95,bottom=0.07,top=0.88,wspace=0.18,hspace=0.28)

for j, cat in enumerate(cats):
    cmap = cat_cmaps[cat]
    # RAW row
    ax = axes[0, j]
    im_raw = raw_ensmean.sel(cat=cat).plot(ax=ax, transform=ccrs.PlateCarree(),
                                           cmap=cmap, vmin=pmin, vmax=pmax, add_colorbar=False)
    ax.set_extent(extent); ax.coastlines("10m", 1.0); ax.add_feature(cfeature.BORDERS, 0.8); ax.add_feature(cfeature.STATES, 0.5)
    ax.set_title(f"RAW | {cat_labels[cat]}", fontsize=13, fontweight="bold")
    cbar_raw = fig.colorbar(im_raw, ax=ax, orientation="vertical", shrink=0.82, pad=0.02, ticks=pticks)
    cbar_raw.set_label("Probability (%)", fontsize=11); cbar_raw.ax.tick_params(labelsize=9)
    # MOS row
    ax = axes[1, j]
    im_mos = mos_ensmean.sel(cat=cat).plot(ax=ax, transform=ccrs.PlateCarree(),
                                           cmap=cmap, vmin=pmin, vmax=pmax, add_colorbar=False)
    ax.set_extent(extent); ax.coastlines("10m", 1.0); ax.add_feature(cfeature.BORDERS, 0.8); ax.add_feature(cfeature.STATES, 0.5)
    ax.set_title(f"MOS | {cat_labels[cat]}", fontsize=13, fontweight="bold")
    cbar_mos = fig.colorbar(im_mos, ax=ax, orientation="vertical", shrink=0.82, pad=0.02, ticks=pticks)
    cbar_mos.set_label("Probability (%)", fontsize=11); cbar_mos.ax.tick_params(labelsize=9)

fig.suptitle(
    f"Multi-Model Tercile Probabilities (Ensemble Mean)\n"
    f"Initialization: {download_args['fdate']:%b %Y}   |   Target Season: {download_args['target']}",
    fontsize=16, fontweight="bold"
)

outpath = os.path.join(path_pt, f"tercile_probs_ensmean_raw_vs_mos_{download_args['target']}_init{download_args['fdate']:%Y%m%d}_{args.regname}.png")
plt.savefig(outpath, dpi=250); print("Saved:", outpath)

# Save ensemble probabilistic NetCDF (unchanged structure)
output_dir = case_dir; os.makedirs(output_dir, exist_ok=True)
ds_out = xr.Dataset()
ds_out["raw_prob_ensmean"] = raw_ensmean
ds_out["mos_prob_ensmean"] = mos_ensmean
ds_out.attrs["description"] = "Multi-model ensemble mean tercile probabilities"
ds_out.attrs["units"] = "percent"
ds_out.attrs["categories"] = "bn=Below Normal, nn=Near Normal, an=Above Normal"
ds_out.attrs["initialization_date"] = str(download_args["fdate"])
ds_out.attrs["target_season"] = str(download_args["target"])
ds_out.attrs["models"] = ", ".join(predictor_names)
ds_out["raw_prob_ensmean"].attrs["long_name"] = "RAW ensemble mean tercile probability"
ds_out["mos_prob_ensmean"].attrs["long_name"] = "MOS ensemble mean tercile probability"
ds_out["raw_prob_ensmean"].attrs["units"] = "%"
ds_out["mos_prob_ensmean"].attrs["units"] = "%"

outfile = os.path.join(output_dir, f"tercile_prob_ensmean_{download_args['target']}_init{download_args['fdate']:%Y%m%d}.nc")
ds_out.to_netcdf(outfile); print("Saved NetCDF:", outfile)

# ---------------- Deterministic forecast (uses predictor_names) ----------------
raw_anoms = {}
mos_anoms = {}
for i, name in enumerate(predictor_names):
    raw_anoms[name] = compute_raw_anom(forecast_data[i], hindcast_data[i])
    mos_anoms[name] = fcsts[i]["deterministic"].isel(T=0)

vmin, vmax = -120, 120
ticks = np.arange(-120, 121, 20)
extent = [
    download_args["predictand_extent"]["west"],
    download_args["predictand_extent"]["east"],
    download_args["predictand_extent"]["south"],
    download_args["predictand_extent"]["north"],
]

plot_dir = path_pt; os.makedirs(plot_dir, exist_ok=True)
mos_grid = mos_anoms[predictor_names[0]]
raw_regridded = {name: interp_to_target_grid_safe(raw_anoms[name], mos_grid) for name in predictor_names}

raw_ensmean = xr.concat([raw_regridded[name] for name in predictor_names], dim="model").mean("model")
mos_ensmean = xr.concat([mos_anoms[name]     for name in predictor_names], dim="model").mean("model")

plot_order = predictor_names + ["ENSEMBLE_MEAN"]
for name in plot_order:
    if name == "ENSEMBLE_MEAN":
        raw_plot = raw_ensmean
        mos_plot = mos_ensmean
        label = "ENSEMBLE_MEAN"
    else:
        raw_plot = raw_regridded[name]
        mos_plot = mos_anoms[name]
        label = name

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5),
                                   subplot_kw={"projection": ccrs.PlateCarree()},
                                   constrained_layout=True)
    p1 = raw_plot.plot(ax=ax1, transform=ccrs.PlateCarree(), cmap="BrBG", vmin=vmin, vmax=vmax, add_colorbar=False)
    ax1.coastlines("10m"); ax1.add_feature(cfeature.BORDERS, 0.8); ax1.add_feature(cfeature.STATES, 0.6)
    ax1.set_extent(extent)
    ax1.set_title(f"RAW Seasonal Forecast\n{label} | Init: {download_args['fdate']:%b %Y} | Target: {download_args['target']}")

    p2 = mos_plot.plot(ax=ax2, transform=ccrs.PlateCarree(), cmap="BrBG", vmin=vmin, vmax=vmax, add_colorbar=False)
    ax2.coastlines("10m"); ax2.add_feature(cfeature.BORDERS, 0.8); ax2.add_feature(cfeature.STATES, 0.6)
    ax2.set_extent(extent)
    ax2.set_title(f"MOS Downscaled Forecast \n{label} → {predictand_name} | Init: {download_args['fdate']:%b %Y} | Target: {download_args['target']}")

    cbar = fig.colorbar(p2, ax=[ax1, ax2], shrink=0.85, pad=0.02, ticks=ticks)
    cbar.set_label("Precipitation Anomaly")

    fname = f"row_raw_vs_mos_{safe_tag(label)}_{download_args['target']}_init{download_args['fdate']:%Y%m%d}_{args.regname}.png"
    outpath = os.path.join(plot_dir, fname)
    plt.savefig(outpath, dpi=200, bbox_inches="tight")
    print("Saved:", outpath)

# Save deterministic outputs
ds_out = xr.Dataset()
for name in predictor_names:
    safe_name = name.replace(".", "_")
    ds_out[f"raw_anom_{safe_name}"] = raw_regridded[name]
    ds_out[f"mos_anom_{safe_name}"] = mos_anoms[name]
ds_out["raw_anom_ensemble_mean"] = raw_ensmean
ds_out["mos_anom_ensemble_mean"] = mos_ensmean

ds_out.attrs["description"] = "Seasonal precipitation anomalies (RAW + MOS + ensemble mean)"
ds_out.attrs["initialization_date"] = str(download_args["fdate"])
ds_out.attrs["target_season"] = str(download_args["target"])
ds_out.attrs["models"] = ", ".join(predictor_names)

outfile = f"{case_dir}/multimodel_anomalies_{args.regname}_{download_args['fdate']:%Y%m}_{download_args['target']}.nc"
ds_out.to_netcdf(outfile); print("Saved to:", outfile)

# Final PyCPT plot (unchanged)
pycpt.plot_forecasts(cpt_args, predictand_name, fcsts, domain_dir, predictor_names, MOS)
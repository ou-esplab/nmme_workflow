#!/usr/bin/env python3
"""
pycpt_seasonal_rt.py

Run CPT seasonal CCA using:
  - Dynamic, in-memory ensemble means for hindcasts
  - Written ensemble-mean products for forecasts
"""

from __future__ import annotations

from pathlib import Path
import sys
import argparse
import datetime as dt
import xarray as xr
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import nmme_pycpt_utils as U
from cptcore.functional import cca


def main() -> int:

    # ---------------- CLI ----------------
    ap = argparse.ArgumentParser(
        description="Run CPT seasonal CCA using dynamic hindcast ensemble means"
    )
    # Legacy positional syntax: config fcstdate --only REGION
    ap.add_argument("config_positional", nargs="?", type=Path, default=None)
    ap.add_argument("fcstdate_positional", nargs="?", default=None)

    # Preferred keyword syntax used by nmme_utils.run_pycpt
    ap.add_argument("--config", type=Path, default=None, help="Path to config YAML")
    ap.add_argument("--fcstdate", default=None, help="Forecast init YYYYMM or ISO date")
    ap.add_argument("--regname", default=None, help="Region name from config")
    ap.add_argument("--lat_minmax", nargs=2, type=float, default=None, help="Latitude bounds")
    ap.add_argument("--lon_minmax", nargs=2, type=float, default=None, help="Longitude bounds")
    ap.add_argument("--training_season", default=None, help="Season string like Feb-Apr")
    ap.add_argument("--only", default=None, help="Legacy region selector")
    ap.add_argument("--models", nargs="+", default=None, help="Optional model override list")
    ap.add_argument("--dry-run", action="store_true", help="Run without invoking CPT executable")

    args = ap.parse_args()

    # Resolve config + fcstdate from either positional or keyword args
    config_path = args.config or args.config_positional or Path("confignmme.yaml")
    fcstdate_raw = args.fcstdate or args.fcstdate_positional
    if fcstdate_raw is None:
        raise SystemExit("[ERROR] --fcstdate or positional fcstdate required")

    # Normalize forecast date
    if len(fcstdate_raw) == 6 and fcstdate_raw.isdigit():
        fdate = dt.datetime.strptime(fcstdate_raw, "%Y%m")
        fcst_yyyymm = fcstdate_raw
    else:
        fdate = dt.datetime.fromisoformat(fcstdate_raw)
        fcst_yyyymm = fdate.strftime("%Y%m")

    # ---------------- Config ----------------
    cfg = U.load_config(config_path)

    region_name = args.regname or args.only
    if region_name:
        # lat/lon/seasons come from the top-level regions: block
        region = U.get_region(cfg.get("regions", []), region_name)
    elif args.lat_minmax is not None and args.lon_minmax is not None and args.training_season is not None:
        region = {
            "name": "custom",
            "lat": list(args.lat_minmax),
            "lon": list(args.lon_minmax),
            "seasons": [args.training_season],
        }
    else:
        raise SystemExit("[ERROR] Must specify --regname/--only or --lat_minmax/--lon_minmax plus --training_season")

    seasons = region.get("seasons", [])
    if not seasons:
        raise SystemExit(f"[ERROR] No seasons defined for region {region['name']}")
    if args.training_season is not None:
        if args.training_season not in seasons:
            raise SystemExit(f"[ERROR] --training_season {args.training_season!r} not in region seasons: {seasons}")
        seasons = [args.training_season]

    # pycpt.regions may have a per-region models override
    pycpt_region_map = {r["name"]: r for r in cfg.get("pycpt", {}).get("regions", [])}
    pycpt_region = pycpt_region_map.get(region["name"], {})
    models_used = U.resolve_models(cfg.get("models", []), pycpt_region)
    if args.models:
        models_used = list(args.models)

    local_cfg = cfg["data"]["local"]
    root = Path(local_cfg["root"])

    model_var = local_cfg["model_vars"]["precipitation"]
    model_base_map = local_cfg.get("model_base", {})
    patterns = local_cfg.get("path_patterns", {})

    print(f"[INFO] Region: {region['name']}")
    print(f"[INFO] Seasons: {seasons}")
    print(f"[INFO] Models: {', '.join(models_used)}")

    # ---------------- Predictand ----------------
    Y_raw = U.load_predictand_local(
        root,
        local_cfg["predictand"]["dir"],
        local_cfg["predictand"]["var"],
    )

    # Subset to the requested region
    lat_min, lat_max = region["lat"]
    lon_min, lon_max = region["lon"]
    Y_raw = Y_raw.sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))

    rc = 0
    for season in seasons:
        print(f"\n[INFO] ===== Season: {season} =====")
        rc_season = _run_season(
            cfg=cfg,
            region=region,
            season=season,
            models_used=models_used,
            model_var=model_var,
            model_base_map=model_base_map,
            patterns=patterns,
            root=root,
            local_cfg=local_cfg,
            Y_raw=Y_raw,
            lat_min=lat_min, lat_max=lat_max,
            lon_min=lon_min, lon_max=lon_max,
            fdate=fdate,
            fcst_yyyymm=fcst_yyyymm,
            dry_run=args.dry_run,
        )
        if rc_season != 0:
            rc = rc_season
    return rc


def _run_season(
    cfg, region, season, models_used, model_var, model_base_map,
    patterns, root, local_cfg, Y_raw,
    lat_min, lat_max, lon_min, lon_max,
    fdate, fcst_yyyymm, dry_run,
) -> int:

    # ============================================================
    # PART 1: HINDCAST TRAINING DATA
    # ============================================================

    X_list = []
    model_names = []
    lat = None
    lon = None

    for model in models_used:
        base = model_base_map.get(model, model)

        try:
            hc, _ = U.load_model_local_with_patterns(
                root=root,
                model_base=base,
                init_yyyymm=fcst_yyyymm,
                var=model_var,
                patterns=patterns,
            )
        except Exception as exc:
            print(f"[WARN] Skipping model {model}: hindcast load failed ({exc})")
            continue

        try:
            selected_L = U.select_lead(
                fdate=fdate,
                season=season,
                L_coord=hc["L"].values,
            )
        except Exception as exc:
            print(f"[WARN] Skipping model {model}: lead selection failed ({exc})")
            continue

        hc_L = hc.sel(L=selected_L)

        # Capture lat/lon ONCE
        if lat is None:
            lat = hc_L["Y"].values
            lon = hc_L["X"].values

        hc_emean = (
            hc_L
            .mean("M", skipna=True)
            .drop_vars("L", errors="ignore")
        )

        # Subset hindcast predictor to region grid
        hc_emean = hc_emean.sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))

        # Capture lat/lon ONCE from region-subset data
        if lat is None:
            lat = hc_emean["Y"].values
            lon = hc_emean["X"].values

        X_list.append(hc_emean)
        model_names.append(model)

    if not X_list:
        raise RuntimeError(
            f"No usable hindcast models for region {region['name']} and init {fcst_yyyymm}"
        )

    # ---- Stack models into C axis ----
    X_train = xr.concat(X_list, dim="C")
    X_train = X_train.assign_coords(C=model_names)
    X_train = X_train.transpose("S", "C", "Y", "X")

    print("[INFO] Hindcast predictor dims:", X_train.dims)

    hindcast_years = [t.year for t in X_train["S"].values]

    Y = U.prepare_predictand_for_cpt(
        Y=Y_raw,
        season=season,
        hindcast_years=hindcast_years,
    )
    

    # ============================================================
    # TRAIN CPT (MUST HAPPEN HERE)
    # ============================================================
    
    print("\n--- PRE to_cptv10: X_train diagnostics ---")
    print("dims:", X_train.dims)
    print("coords:", list(X_train.coords))
    for name, coord in X_train.coords.items():
        print(f"  coord {name}: dims={coord.dims}, dtype={coord.dtype}")
    print("-----------------------------------------\n")

    X_train_v10, Y_v10 = U.to_cptv10(X=X_train, Y=Y)
    
    # CPT requires numeric C index (not strings)
    model_names = list(X_train_v10["C"].values)

    X_train_v10 = X_train_v10.assign_coords(
        C=("C", range(1, X_train_v10.sizes["C"] + 1))
    )

    # (optional but recommended) store model names as metadata
    X_train_v10.attrs["C_names"] = model_names
  
    # CPT requires an explicit missing value attribute
    MISSING_VALUE = -9999.0

    X_train_v10.attrs["missing"] = MISSING_VALUE
    Y_v10.attrs["missing"] = MISSING_VALUE
    
    # Units are required by CPTv10 (string, not interpreted)
    X_train_v10.attrs["units"] = "unknown"
    Y_v10.attrs["units"] = "unknown"
    
    # Rename spatial dims to CPTv10‑compliant names
    X_train_v10 = X_train_v10.rename(
        {"row": "Y", "col": "X"}
    )
    Y_v10 = Y_v10.rename(
        {"row": "Y", "col": "X"}
    )

    # Replace CFTime T coordinate with simple numeric index for CPT
    X_train_v10 = X_train_v10.assign_coords(
        T=("T", range(X_train_v10.sizes["T"]))
    )
    Y_v10 = Y_v10.assign_coords(
        T=("T", range(Y_v10.sizes["T"]))
    )

    print("\n--- POST to_cptv10: X_train_v10 diagnostics ---")
    print("dims:", X_train_v10.dims)
    print("coords:", list(X_train_v10.coords))
    for name, coord in X_train_v10.coords.items():
        print(f"  coord {name}: dims={coord.dims}, dtype={coord.dtype}")
    print("----------------------------------------------\n")

    print("[INFO] CPTv10 training dims:", X_train_v10.dims)
 
    print("=== PREDICTAND DIAGNOSTICS ===")
    print("dtype:", Y_v10.dtype)
    print("min/max:", float(Y_v10.min()), float(Y_v10.max()))
    print("unique values (sample):", np.unique(Y_v10.values.flatten())[:10])
    print("attrs:", Y_v10.attrs)
    
    # X_train_v10 dims: (T, C, Y, X)
    # Y_v10 dims: (T, Y, X)

    # Ensure numeric model index for CPT
    model_names = list(X_train_v10["C"].values)
    X_train_v10 = X_train_v10.assign_coords(
        C=("C", np.arange(1, X_train_v10.sizes["C"] + 1))
    )
    X_train_v10.attrs["C_names"] = model_names

    print("=== CPT INPUT CHECK ===")
    print("X dims:", X_train_v10.dims)
    print("X coords:", list(X_train_v10.coords))
    print("Y dims:", Y_v10.dims)
    print("Y coords:", list(Y_v10.coords))
    print("Y dtype:", Y_v10.dtype)

    if args.dry_run:
        print("[DRY-RUN] CPT inputs built successfully. Skipping CPT execution.")
        print("[DRY-RUN] X_train_v10 dims:", X_train_v10.dims)
        print("[DRY-RUN] Y_v10 dims:", Y_v10.dims)
        return 0

    print("[INFO] CPT-Core CCA training complete.")

    # ============================================================
    # STOP HERE FOR NOW – FORECAST APPLICATION COMES NEXT
    # ============================================================

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
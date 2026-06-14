#!/usr/bin/env python3
"""
pycpt_seasonal_rt.py

Produce bias-corrected (CCA/MOS) seasonal forecasts using local NMME data.

For a given region, loops over all seasons defined in confignmme.yaml, runs
CCA independently per model, then averages model outputs for the MME.

Outputs deterministic anomaly and probabilistic tercile NetCDF files.
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


_SEASON_TO_MONMON = {
    "DJF": "Dec-Feb", "JFM": "Jan-Mar", "FMA": "Feb-Apr",
    "MAM": "Mar-May", "AMJ": "Apr-Jun", "MJJ": "May-Jul",
    "JJA": "Jun-Aug", "JAS": "Jul-Sep", "ASO": "Aug-Oct",
    "SON": "Sep-Nov", "OND": "Oct-Dec", "NDJ": "Nov-Jan",
}


def _pycpt_season(season: str) -> str:
    """Convert 3-char code (MAM) to Mon-Mon format (Mar-May) if needed."""
    return _SEASON_TO_MONMON.get(season, season)


def _build_cpt_args(cfg: dict) -> dict:
    raw = cfg.get("pycpt", {}).get("cpt_args", {})
    return {
        "tailoring":              raw.get("tailoring", "Anomaly"),
        "cca_modes":              tuple(raw.get("cca_modes", [1, 3])),
        "x_eof_modes":            tuple(raw.get("x_eof_modes", [1, 8])),
        "y_eof_modes":            tuple(raw.get("y_eof_modes", [1, 6])),
        "validation":             raw.get("validation", "crossvalidation"),
        "crossvalidation_window": int(raw.get("crossvalidation_window", 5)),
        "synchronous_predictors": bool(raw.get("synchronous_predictors", True)),
    }



def _years_to_dt64(coord) -> np.ndarray:
    """Convert S/T coordinate (cftime, datetime64, or integer years) to numpy datetime64."""
    vals = coord.values
    try:
        years = [int(v.year) for v in vals]
    except AttributeError:
        years = [int(v) for v in vals]
    return np.array([f"{y}-01-01" for y in years], dtype="datetime64[ns]")


def _to_cptv10_predictor(X: xr.DataArray, model_names: list) -> xr.DataArray:
    """Convert (S, M, Y, X) array to CPTv10 format with datetime64 T."""
    v10 = X.rename({"S": "T"}) if "S" in X.dims else X
    v10 = v10.assign_coords(T=("T", _years_to_dt64(v10["T"])))
    if "M" in v10.dims:
        v10 = v10.assign_coords(M=("M", np.arange(1, v10.sizes["M"] + 1)))
        v10.attrs["M_names"] = model_names
    v10.attrs["missing"] = -9999.0
    v10.attrs["units"] = "unknown"
    return v10


def _to_cptv10_predictand(Y: xr.DataArray) -> xr.DataArray:
    """Convert (S, Y, X) predictand array to CPTv10 format with datetime64 T."""
    v10 = Y.rename({"S": "T"}) if "S" in Y.dims else Y
    v10 = v10.assign_coords(T=("T", _years_to_dt64(v10["T"])))
    v10.attrs["missing"] = -9999.0
    v10.attrs["units"] = "unknown"
    return v10


def main() -> int:

    ap = argparse.ArgumentParser(
        description="Run CCA bias correction for all seasons for one region"
    )
    ap.add_argument("config_positional", nargs="?", type=Path, default=None)
    ap.add_argument("fcstdate_positional", nargs="?", default=None)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--fcstdate", default=None)
    ap.add_argument("--regname", default=None, help="Region name from config")
    ap.add_argument("--only", default=None, help="Legacy alias for --regname")
    ap.add_argument("--training_season", default=None, help="Run only this season")
    ap.add_argument("--models", nargs="+", default=None, help="Optional model override list")
    ap.add_argument("--dry-run", action="store_true", help="Build inputs only; skip CCA")


    args = ap.parse_args()

    config_path = args.config or args.config_positional or Path("confignmme.yaml")
    fcstdate_raw = args.fcstdate or args.fcstdate_positional
    if fcstdate_raw is None:
        print("[ERROR] --fcstdate or positional fcstdate required", file=sys.stderr)
        return 2

    if len(fcstdate_raw) == 6 and fcstdate_raw.isdigit():
        fdate = dt.datetime.strptime(fcstdate_raw, "%Y%m")
        fcst_yyyymm = fcstdate_raw
    else:
        fdate = dt.datetime.fromisoformat(fcstdate_raw)
        fcst_yyyymm = fdate.strftime("%Y%m")

    cfg = U.load_config(config_path)

    region_name = args.regname or args.only
    if region_name is None:
        raise SystemExit("[ERROR] --regname required")

    region = U.get_region(cfg.get("regions", []), region_name)
    seasons = region.get("seasons", [])
    if not seasons:
        raise SystemExit(f"[ERROR] No seasons defined for region {region_name}")
    if args.training_season is not None:
        seasons = [args.training_season]

    pycpt_region_map = {r["name"]: r for r in cfg.get("pycpt", {}).get("regions", [])}
    pycpt_region = pycpt_region_map.get(region["name"], {})
    models_used = U.resolve_models(cfg.get("models", []), pycpt_region)
    if args.models:
        models_used = list(args.models)

    print(f"[INFO] Region: {region['name']}")
    print(f"[INFO] Seasons: {seasons}")
    print(f"[INFO] Models: {', '.join(models_used)}")

    rc = 0
    for season in seasons:
        print(f"\n[INFO] ===== Season: {season} =====")
        rc_season = _run_season(
            cfg=cfg,
            region=region,
            season=season,
            models_used=models_used,
            fdate=fdate,
            fcst_yyyymm=fcst_yyyymm,
            dry_run=args.dry_run,
        )
        if rc_season != 0:
            rc = rc_season
    return rc


def _run_season(
    cfg, region, season, models_used,
    fdate, fcst_yyyymm, dry_run,
) -> int:

    region_name = region["name"]
    lat_min, lat_max = region["lat"]
    lon_min, lon_max = region["lon"]
    pycpt_season = _pycpt_season(season)

    local_cfg = cfg["data"]["local"]
    root = Path(local_cfg["root"])
    model_var = local_cfg["model_vars"]["precipitation"]
    model_base_map = local_cfg.get("model_base", {})
    patterns = local_cfg.get("path_patterns", {})
    model_path_patterns = local_cfg.get("model_path_patterns", {})
    output_root = cfg["data"]["output"]["pycpt"]

    var_cfg = cfg.get("pycpt", {}).get("variables", [{"var": "prec", "lev": "sfc"}])[0]
    obs_cfg = local_cfg.get("predictands", {}).get(var_cfg["var"])
    if obs_cfg is None:
        print(f"[WARN] No predictand configured for var={var_cfg['var']}; skipping.")
        return 0
    Y_raw = U.load_predictand_local(root, obs_cfg["dir"], obs_cfg["var"])
    Y_raw = Y_raw.sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))

    # ---- Load hindcast + forecast per model ----
    hc_list: list[xr.DataArray] = []
    fc_list: list[xr.DataArray] = []
    model_names: list[str] = []

    for model in models_used:
        base = model_base_map.get(model, model)
        model_patterns = {**patterns, **model_path_patterns.get(model, {})}
        try:
            hc, fc = U.load_model_local_with_patterns(
                root=root, model_base=base, init_yyyymm=fcst_yyyymm,
                var=model_var, patterns=model_patterns,
            )
        except Exception as exc:
            print(f"[WARN] Skipping {model}: data load failed ({exc})")
            continue

        try:
            selected_L = U.select_lead(fdate=fdate, season=pycpt_season, L_coord=hc["L"].values)
        except Exception as exc:
            print(f"[WARN] Skipping {model}: lead selection failed ({exc})")
            continue

        hc_emean = (
            hc.sel(L=selected_L).mean("M", skipna=True)
            .drop_vars("L", errors="ignore")
            .sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))
        )
        fc_L = fc.sel(L=selected_L) if "L" in fc.dims else fc
        fc_emean = (
            fc_L.mean("M", skipna=True)
            .drop_vars("L", errors="ignore")
            .sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))
        )

        hc_list.append(hc_emean)
        fc_list.append(fc_emean)
        model_names.append(model)

    if not hc_list:
        print(f"[ERROR] No usable models for {region_name}/{season}")
        return 1

    # ---- Align models to common hindcast years ----
    def _years(da):
        try:
            return set(int(y) for y in da["S"].dt.year.values)
        except AttributeError:
            return set(int(y) for y in da["S"].values)

    common_years = sorted(set.intersection(*[_years(hc) for hc in hc_list]))
    print(f"[INFO] Common years: {common_years[0]}–{common_years[-1]} ({len(common_years)} yrs)")

    def _sel_years(da, years):
        try:
            mask = da["S"].dt.year.isin(years)
            int_years = [int(t.year) for t in da["S"].values[mask.values]]
        except AttributeError:
            mask = da["S"].isin(years)
            int_years = [int(y) for y in da["S"].values[mask.values]]
        return da.sel(S=mask).assign_coords(S=("S", int_years))

    hc_list = [_sel_years(hc, common_years) for hc in hc_list]

    # ---- Prepare predictand ----
    Y = U.aggregate_predictand_to_season(
        Y=Y_raw, season=pycpt_season, hindcast_years=common_years,
    )
    Y_v10 = _to_cptv10_predictand(Y)

    print(f"[INFO] Predictand: {Y_v10.dims} {Y_v10.shape}")

    if dry_run:
        print("[DRY-RUN] Inputs built. Skipping CCA.")
        return 0

    # ---- CPT args from config ----
    cpt_args = _build_cpt_args(cfg)
    xy_dims = dict(
        x_lat_dim="Y", x_lon_dim="X", x_sample_dim="T", x_feature_dim="M",
        y_lat_dim="Y", y_lon_dim="X", y_sample_dim="T",
        f_lat_dim="Y", f_lon_dim="X", f_sample_dim="T", f_feature_dim="M",
    )

    # ---- CCA per model ----
    results: dict = {}
    for i, model in enumerate(model_names):
        print(f"[INFO] CCA: {model}")

        # Single-model training predictor: (S, M=1, Y, X)
        X_single = hc_list[i].expand_dims("M", axis=1).assign_coords(M=[model])
        X_v10 = _to_cptv10_predictor(X_single, [model])

        # Single-model forecast predictor: (T=1, M=1, Y, X)
        F_v10 = U.prepare_forecast_predictor_v10([fc_list[i]], [model], fcst_year=fdate.year)

        try:
            _, fcst, *_ = cca.canonical_correlation_analysis(
                X_v10, Y_v10, F=F_v10, **cpt_args, **xy_dims
            )
            if fcst is not None:
                results[model] = fcst
            else:
                print(f"[WARN] CCA returned no forecast for {model}")
        except Exception as exc:
            print(f"[WARN] CCA failed for {model}: {exc}")

    if not results:
        print("[ERROR] All CCA runs failed.")
        return 1

    # ---- MME = mean of individual model MOS forecasts ----
    det_vals = [r["deterministic"] for r in results.values() if "deterministic" in r]
    prob_vals = [r["probabilistic"]  for r in results.values() if "probabilistic"  in r]
    if det_vals and prob_vals:
        mme_det  = xr.concat(det_vals,  dim="model").mean("model")
        mme_prob = xr.concat(prob_vals, dim="model").mean("model")
        mme_det.name  = "deterministic"
        mme_prob.name = "probabilistic"
        results["MME"] = xr.merge([mme_det, mme_prob])
        print("[INFO] MME computed as mean of model MOS forecasts")

    # ---- Save outputs ----
    var_cfg = cfg.get("pycpt", {}).get("variables", [{"var": "prec", "lev": "sfc"}])[0]
    U.save_pycpt_results(
        results=results,
        region_name=region_name,
        season=season,
        var=var_cfg["var"],
        lev=var_cfg["lev"],
        fcstdate=fcst_yyyymm,
        output_root=output_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

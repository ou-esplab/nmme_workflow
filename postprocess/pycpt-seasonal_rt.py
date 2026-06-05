#!/usr/bin/env python3
"""
pycpt_seasonal_rt.py

Produce bias-corrected (CCA/MOS) seasonal forecasts using local NMME data.

For a given region (and optionally a single season), loops over:
  - All seasons defined in confignmme.yaml regions: block
  - All variables defined in confignmme.yaml pycpt.variables
  - Each individual model + MME

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


def _build_cpt_args(cfg: dict) -> dict:
    """Read CCA settings from config, with sensible defaults."""
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
    """Convert a coordinate (cftime, datetime64, or integer years) to
    numpy datetime64 at Jan-1 of each year — the format cptio expects."""
    vals = coord.values
    try:
        years = [int(v.year) for v in vals]        # cftime / datetime objects
    except AttributeError:
        years = [int(v) for v in vals]             # already integer years
    return np.array([f"{y}-01-01" for y in years], dtype="datetime64[ns]")


def _to_cptv10_predictor(X: xr.DataArray, model_names: list) -> xr.DataArray:
    """Convert (S, C, Y, X) training array to CPTv10 format.
    Keep Y/X dim names — cptcore identifies them via explicit dim hints.
    T coordinate is set to numpy datetime64 (Jan-1 of each hindcast year).
    """
    v10 = X.rename({"S": "T"}) if "S" in X.dims else X
    v10 = v10.assign_coords(T=("T", _years_to_dt64(v10["T"])))

    if "C" in v10.dims:
        # Rename C → M: cptio only adds cpt:clim_prob when dim name is literally 'C',
        # which causes CPT to reject multi-model files (6×0.333 ≠ 1).
        # 'M' is also recognised as the feature dimension by guess_cptv10_coords.
        v10 = v10.rename({"C": "M"})
        v10 = v10.assign_coords(M=("M", np.arange(1, v10.sizes["M"] + 1)))
        v10.attrs["M_names"] = model_names

    v10.attrs["missing"] = -9999.0
    v10.attrs["units"] = "unknown"
    return v10


def _to_cptv10_predictand(Y: xr.DataArray) -> xr.DataArray:
    """Convert (S, Y, X) predictand array to CPTv10 format.
    Keep Y/X dim names — cptcore identifies them via explicit dim hints.
    T coordinate is set to numpy datetime64 (Jan-1 of each obs year).
    """
    v10 = Y.rename({"S": "T"}) if "S" in Y.dims else Y
    v10 = v10.assign_coords(T=("T", _years_to_dt64(v10["T"])))
    v10.attrs["missing"] = -9999.0
    v10.attrs["units"] = "unknown"
    return v10


def run_one_season_variable(
    cfg: dict,
    region: dict,
    season: str,
    var_cfg: dict,
    fcst_yyyymm: str,
    fdate: dt.datetime,
    dry_run: bool,
    models_override: list | None,
) -> int:
    """
    Run CCA for one region × season × variable combination.

    Returns 0 on success, non-zero on failure.
    """
    var = var_cfg["var"]
    lev = var_cfg["lev"]
    region_name = region["name"]
    lat_min, lat_max = region["lat"]
    lon_min, lon_max = region["lon"]

    print(f"\n[INFO] === {region_name} | {season} | {var} ===")

    local_cfg = cfg["data"]["local"]
    root = Path(local_cfg["root"])
    model_var = local_cfg["model_vars"].get(
        "precipitation" if var == "prec" else var, var
    )
    model_base_map = local_cfg.get("model_base", {})
    patterns = local_cfg.get("path_patterns", {})
    pattern_overrides = local_cfg.get("path_pattern_overrides", {})
    output_root = cfg["data"]["output"]["pycpt"]

    # Observations path for this variable
    predictands_cfg = local_cfg.get("predictands", {})
    if var not in predictands_cfg:
        print(f"[WARN] No predictand configured for var={var}; skipping.")
        return 0
    obs_cfg = predictands_cfg[var]

    # Models: pycpt region override → global
    pycpt_region_map = {
        r["name"]: r
        for r in cfg.get("pycpt", {}).get("regions", [])
    }
    pycpt_region = pycpt_region_map.get(region_name, {})
    global_models = cfg.get("models", [])
    models_used = U.resolve_models(global_models, pycpt_region)
    if models_override:
        models_used = list(models_override)

    # ---- Load observations ----
    Y_raw = U.load_predictand_local(root, obs_cfg["dir"], obs_cfg["var"])
    Y_raw = Y_raw.sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))

    # ---- Load hindcast + forecast per model ----
    hc_list: list[xr.DataArray] = []
    fc_list: list[xr.DataArray] = []
    model_names: list[str] = []

    for model in models_used:
        base = model_base_map.get(model, model)
        try:
            hc, fc = U.load_model_local_with_patterns(
                root=root,
                model_base=base,
                init_yyyymm=fcst_yyyymm,
                var=model_var,
                patterns=patterns,
                pattern_overrides=pattern_overrides,
            )
        except Exception as exc:
            print(f"[WARN] Skipping {model}: data load failed ({exc})")
            continue

        try:
            selected_L = U.select_lead(
                fdate=fdate,
                season=season,
                L_coord=hc["L"].values,
            )
        except Exception as exc:
            print(f"[WARN] Skipping {model}: lead selection failed ({exc})")
            continue

        hc_L = hc.sel(L=selected_L)
        fc_L = fc.sel(L=selected_L) if "L" in fc.dims else fc

        hc_emean = (
            hc_L.mean("M", skipna=True)
            .drop_vars("L", errors="ignore")
            .sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))
        )
        fc_emean = (
            fc_L.mean("M", skipna=True)
            .drop_vars("L", errors="ignore")
            .sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))
        )

        hc_list.append(hc_emean)
        fc_list.append(fc_emean)
        model_names.append(model)

    if not hc_list:
        print(f"[ERROR] No usable models for {region_name}/{season}/{var}")
        return 1

    # ---- Align all models to their common hindcast years ----
    # Models have different year ranges; concat would pad shorter ones with NaN.
    # Only keep years present in every model.
    def _years(da):
        try:
            return set(int(y) for y in da["S"].dt.year.values)   # cftime/datetime
        except AttributeError:
            return set(int(y) for y in da["S"].values)            # integer years

    year_sets = [_years(hc) for hc in hc_list]
    common_years = sorted(set.intersection(*year_sets))
    print(f"[INFO] Common hindcast years: {common_years[0]}–{common_years[-1]} ({len(common_years)} years)")

    def _sel_and_normalize(da, years):
        """Select common years and normalize S to integer years for consistent concat."""
        try:
            mask = da["S"].dt.year.isin(years)
            int_years = [int(t.year) for t in da["S"].values[mask.values]]
        except AttributeError:
            mask = da["S"].isin(years)
            int_years = [int(y) for y in da["S"].values[mask.values]]
        return da.sel(S=mask).assign_coords(S=("S", int_years))

    hc_list = [_sel_and_normalize(hc, common_years) for hc in hc_list]

    # ---- Build MME training predictor (S, M, Y, X) ----
    # Use 'M' not 'C' so cptio skips writing cpt:clim_prob for predictor categories
    X_train = xr.concat(hc_list, dim="M").assign_coords(M=model_names)
    X_train = X_train.transpose("S", "M", "Y", "X")
    hindcast_years = [int(y) for y in X_train["S"].values]   # S is integer years after normalization

    # ---- Prepare predictand: seasonal means aligned to hindcast years ----
    # Pass raw seasonal means to CPT; CPT computes anomalies internally via tailoring='Anomaly'
    Y = U.aggregate_predictand_to_season(
        Y=Y_raw,
        season=season,
        hindcast_years=hindcast_years,
    )

    # ---- CPTv10 conversion ----
    X_train_v10 = _to_cptv10_predictor(X_train, model_names)
    Y_v10 = _to_cptv10_predictand(Y)

    # ---- Forecast predictor: (T=1, M, Y, X) ----
    X_fcst_v10 = U.prepare_forecast_predictor_v10(fc_list, model_names, fcst_year=fdate.year)

    print(f"[INFO] X_train: {X_train_v10.dims} {X_train_v10.shape}")
    print(f"[INFO] Y:       {Y_v10.dims} {Y_v10.shape}")
    print(f"[INFO] X_fcst:  {X_fcst_v10.dims} {X_fcst_v10.shape}")

    if dry_run:
        print("[DRY-RUN] CPT inputs built. Skipping CCA execution.")
        return 0

    cpt_args = _build_cpt_args(cfg)

    # Explicit dim name hints so cptcore doesn't have to guess from coordinate values.
    # Feature dim is 'M' (not 'C') so cptio skips writing cpt:clim_prob per category.
    xy_dims = dict(
        x_lat_dim="Y", x_lon_dim="X", x_sample_dim="T", x_feature_dim="M",
        y_lat_dim="Y", y_lon_dim="X", y_sample_dim="T",
        f_lat_dim="Y", f_lon_dim="X", f_sample_dim="T", f_feature_dim="M",
    )

    # ---- CCA: individual models ----
    results: dict = {}
    for i, model in enumerate(model_names):
        print(f"[INFO] Running CCA for model: {model}")
        # Slice single model; re-index M to [1] (CPT requires categories starting at 1)
        X_single = X_train_v10.isel(M=[i]).assign_coords(M=("M", [1]))
        F_single = X_fcst_v10.isel(M=[i]).assign_coords(M=("M", [1]))
        try:
            _, fcst, *_ = cca.canonical_correlation_analysis(
                X_single, Y_v10, F=F_single, **cpt_args, **xy_dims
            )
            results[model] = fcst
        except Exception as exc:
            print(f"[WARN] CCA failed for {model}: {exc}")

    # ---- MME = average of individual model MOS outputs ----
    if results:
        det_vals = [r["deterministic"] for r in results.values() if "deterministic" in r]
        prob_vals = [r["probabilistic"]  for r in results.values() if "probabilistic"  in r]
        if det_vals and prob_vals:
            import xarray as _xr
            mme_det  = _xr.concat(det_vals,  dim="model").mean("model")
            mme_prob = _xr.concat(prob_vals, dim="model").mean("model")
            mme_det.name  = "deterministic"
            mme_prob.name = "probabilistic"
            results["MME"] = _xr.merge([mme_det, mme_prob])
            print("[INFO] MME computed as mean of individual model MOS forecasts")

    if not results:
        print("[ERROR] All CCA runs failed.")
        return 1

    # ---- Save outputs ----
    U.save_pycpt_results(
        results=results,
        region_name=region_name,
        season=season,
        var=var,
        lev=lev,
        fcstdate=fcst_yyyymm,
        output_root=output_root,
    )

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run CCA bias correction for all seasons/variables for one region"
    )
    ap.add_argument("config_positional", nargs="?", type=Path, default=None)
    ap.add_argument("fcstdate_positional", nargs="?", default=None)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--fcstdate", default=None)
    ap.add_argument("--regname", "--only", default=None, dest="regname")
    ap.add_argument("--models", nargs="+", default=None)
    ap.add_argument("--season", default=None, help="Run only this season (default: all seasons for region)")
    ap.add_argument("--dry-run", action="store_true")
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

    if args.regname is None:
        print("[ERROR] --regname required", file=sys.stderr)
        return 2

    # Region definition comes from top-level regions: block
    top_regions = cfg.get("regions", [])
    region = U.get_region(top_regions, args.regname)

    seasons = region.get("seasons", [])
    if not seasons:
        print(f"[ERROR] No seasons defined for region {args.regname}", file=sys.stderr)
        return 3

    if args.season:
        if args.season not in seasons:
            print(f"[ERROR] Season {args.season!r} not in region seasons: {seasons}", file=sys.stderr)
            return 3
        seasons = [args.season]

    variables = cfg.get("pycpt", {}).get("variables", [{"var": "prec", "lev": "sfc"}])

    rc = 0
    for var_cfg in variables:
        for season in seasons:
            code = run_one_season_variable(
                cfg=cfg,
                region=region,
                season=season,
                var_cfg=var_cfg,
                fcst_yyyymm=fcst_yyyymm,
                fdate=fdate,
                dry_run=args.dry_run,
                models_override=args.models,
            )
            if code != 0:
                rc = code

    return rc


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Compute RPSS and ACC skill for PyCPT (CCA) bias-corrected NMME hindcasts.

For each region / season / init_month / model:
  - Load the ensemble-mean hindcast for every available hindcast year at the
    given init month, and select the representative lead for the target
    season (same logic as postprocess/pycpt-seasonal_rt.py's select_lead()).
  - Load the observed seasonal mean (predictand).
  - Run cptcore CCA with validation="double-crossvalidation". This differs
    from the "crossvalidation" setting used operationally in
    pycpt-seasonal_rt.py: probabilistic hindcast output and the RPSS skill
    metric are only emitted by cptcore in double-crossvalidation/retroactive
    mode (crossvalidation mode silently omits them).
  - Save ACC (Pearson correlation) and RPSS skill maps for comparison with
    the raw-hindcast skill computed by compute_rpss.py / compute_acc.py.

MME skill is the average of the individual models' skill maps, not the
skill of a pooled/averaged forecast (that would require reconciling each
model's CCA probability-category output, which is not done here).

No separate climatology files are needed: CPT computes anomalies internally
via tailoring="Anomaly" from the raw ensemble-mean values, same as the
operational pycpt pipeline.

Output: one NetCDF per (region, var, season, init_month, model) with
variables `acc` and `rpss` on the region's (lat, lon) grid.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import warnings
from pathlib import Path

import numpy as np
import xarray as xr

warnings.filterwarnings("ignore", message="All-NaN slice encountered")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import nmme_pycpt_utils as U
from cptcore.functional import cca

_VARKEY = {"prec": "precipitation", "tref": "temperature"}


# ---------------------------------------------------------------------------
# Season helpers (same mapping as postprocess/pycpt-seasonal_rt.py)
# ---------------------------------------------------------------------------

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
        "crossvalidation_window": int(raw.get("crossvalidation_window", 5)),
        "synchronous_predictors": bool(raw.get("synchronous_predictors", True)),
        # Skill assessment always uses double-crossvalidation: it's the only
        # mode in which cptcore emits probabilistic hindcasts + RPSS.
        "validation": "double-crossvalidation",
    }


def _years_to_dt64(coord) -> np.ndarray:
    vals = coord.values
    try:
        years = [int(v.year) for v in vals]
    except AttributeError:
        years = [int(v) for v in vals]
    return np.array([f"{y}-01-01" for y in years], dtype="datetime64[ns]")


def _to_cptv10_predictor(X: xr.DataArray, model_names: list) -> xr.DataArray:
    v10 = X.rename({"S": "T"}) if "S" in X.dims else X
    v10 = v10.assign_coords(T=("T", _years_to_dt64(v10["T"])))
    if "M" in v10.dims:
        v10 = v10.assign_coords(M=("M", np.arange(1, v10.sizes["M"] + 1)))
        v10.attrs["M_names"] = model_names
    v10.attrs["missing"] = -9999.0
    v10.attrs["units"] = "unknown"
    return v10


def _to_cptv10_predictand(Y: xr.DataArray) -> xr.DataArray:
    v10 = Y.rename({"S": "T"}) if "S" in Y.dims else Y
    v10 = v10.assign_coords(T=("T", _years_to_dt64(v10["T"])))
    v10.attrs["missing"] = -9999.0
    v10.attrs["units"] = "unknown"
    return v10


def _load_tref_obs(obs_file: str) -> xr.DataArray:
    """Load GHCN-CAMS monthly temperature, standardize to (T, Y, X) with -180/180 lon."""
    ds = xr.open_dataset(obs_file, decode_times=True)
    da = ds["air"]
    rmap = {}
    for old, new in [("time", "T"), ("lat", "Y"), ("lon", "X")]:
        if old in da.dims:
            rmap[old] = new
    if rmap:
        da = da.rename(rmap)
    if float(da["Y"].values[0]) > float(da["Y"].values[-1]):
        da = da.isel(Y=slice(None, None, -1))
    da = da.assign_coords(X=((da["X"] + 180) % 360 - 180)).sortby("X")
    return da


def _da_years(da: xr.DataArray) -> list[int]:
    coord = da["S"]
    try:
        return sorted(int(y) for y in coord.dt.year.values)
    except AttributeError:
        return sorted(int(y) for y in coord.values)


def _select_years(da: xr.DataArray, years: list[int]) -> xr.DataArray:
    coord = da["S"]
    try:
        mask = coord.dt.year.isin(years)
    except AttributeError:
        mask = coord.isin(years)
    return da.sel(S=mask)


def _init_months_for_season(season_monmon: str, max_lead: int) -> list[int]:
    """Init months whose central lead for this season falls within max_lead.

    Mirrors utils.nmme_pycpt_utils.select_lead()'s lead computation:
    lead = month - init_month, wrapped into [1, 12].
    """
    months = U.season_to_months(season_monmon)
    valid = []
    for init_month in range(1, 13):
        leads = []
        for m in months:
            lead = m - init_month
            if lead <= 0:
                lead += 12
            leads.append(lead)
        center = np.median(leads)
        if center <= max_lead:
            valid.append(init_month)
    return valid


# ---------------------------------------------------------------------------
# Per-model CCA skill
# ---------------------------------------------------------------------------

def _compute_model_skill(
    model: str,
    model_base: str,
    model_patterns: dict,
    root: Path,
    var: str,
    init_month: int,
    pycpt_season: str,
    lat_min: float, lat_max: float, lon_min: float, lon_max: float,
    obs_da: xr.DataArray,
    start_year: int,
    end_year: int,
    cpt_args: dict,
    xy_dims: dict,
) -> tuple[xr.DataArray | None, xr.DataArray | None, int]:
    """Returns (acc_map, rpss_map, n_years) for one model at one (init_month, season)."""

    hind_glob = Path(model_patterns["hindcast"].format(
        root=str(root), model=model_base, var=var, yyyy="", mm=f"{init_month:02d}",
    ))
    try:
        hc = U.open_netcdf_variable(hind_glob, var)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"  [SKIP] {model} init={init_month:02d}: {exc}")
        return None, None, 0

    try:
        fdate = dt.datetime(2000, init_month, 1)
        selected_L = U.select_lead(fdate=fdate, season=pycpt_season, L_coord=hc["L"].values)
    except Exception as exc:
        print(f"  [SKIP] {model} init={init_month:02d}: lead selection failed ({exc})")
        return None, None, 0

    hc_emean = (
        hc.sel(L=selected_L).mean("M", skipna=True)
        .drop_vars("L", errors="ignore")
        .sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))
    )

    years = [y for y in _da_years(hc_emean) if start_year <= y <= end_year]
    if len(years) < 10:
        print(f"  [SKIP] {model} init={init_month:02d}: only {len(years)} hindcast years in range")
        return None, None, 0
    hc_emean = _select_years(hc_emean, years)

    X_single = hc_emean.expand_dims("M", axis=1).assign_coords(M=[model])
    X_v10 = _to_cptv10_predictor(X_single, [model])

    try:
        Y = U.aggregate_predictand_to_season(
            Y=obs_da, season=pycpt_season, hindcast_years=np.array(years),
        )
    except ValueError as exc:
        print(f"  [SKIP] {model} init={init_month:02d}: predictand alignment failed ({exc})")
        return None, None, 0
    Y_v10 = _to_cptv10_predictand(Y)

    try:
        _, _, skill_values, *_ = cca.canonical_correlation_analysis(
            X_v10, Y_v10, **cpt_args, **xy_dims,
        )
    except Exception as exc:
        print(f"  [WARN] CCA failed for {model} init={init_month:02d}: {exc}")
        return None, None, 0

    acc_map = skill_values["pearson"].rename("acc") if "pearson" in skill_values else None
    # CPT outputs RPSS as a percentage (×100 of the standard [-∞,1] scale).
    # Divide by 100 so the output matches the convention used by compute_rpss.py.
    rpss_map = (
        (skill_values["rank_probability_skill_score"] / 100.0).rename("rpss")
        if "rank_probability_skill_score" in skill_values else None
    )
    if acc_map is None:
        print(f"  [WARN] {model} init={init_month:02d}: no pearson skill returned")
        return None, None, 0

    print(f"  [OK] {model} init={init_month:02d} season={pycpt_season} n_years={len(years)}")
    return acc_map, rpss_map, len(years)


# ---------------------------------------------------------------------------
# Save helper
# ---------------------------------------------------------------------------

def _save_skill(
    out_path: Path,
    acc_map: xr.DataArray,
    rpss_map: xr.DataArray | None,
    model: str, var: str, region: str, season: str, init_month: int,
    start_year: int, end_year: int,
    n_years: int | None = None,
    models_included: list[str] | None = None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Mask CPT's -9999 missing flag so output files use proper NaN fill.
    acc_map = acc_map.where(acc_map > -9000)
    if rpss_map is not None:
        rpss_map = rpss_map.where(rpss_map > -90)  # after /100, missing ≈ -99.99
    data_vars = {"acc": acc_map}
    if rpss_map is not None:
        data_vars["rpss"] = rpss_map
    ds = xr.Dataset(data_vars)
    ds.attrs.update({
        "model": model, "var": var, "region": region, "season": season,
        "init_month": init_month,
        "period": f"{start_year}-{end_year}",
        "method": "pycpt-cca-double-crossvalidation",
        "long_name_acc": "Anomaly Correlation Coefficient (Pearson, from cptcore CCA)",
        "long_name_rpss": "Ranked Probability Skill Score (from cptcore CCA, double-crossvalidation)",
    })
    if n_years is not None:
        ds.attrs["n_years"] = n_years
    if models_included is not None:
        ds.attrs["models_included"] = ",".join(models_included)
    encoding = {k: {"zlib": True, "complevel": 1} for k in data_vars}
    ds.to_netcdf(out_path, encoding=encoding)
    print(f"  [SAVED] {out_path.name}")


# ---------------------------------------------------------------------------
# Region/season driver
# ---------------------------------------------------------------------------

def _run_region_season(
    cfg: dict, region: dict, season: str, models_used: list[str], var: str,
    obs_da: xr.DataArray,
    start_year: int, end_year: int, max_lead: int,
    outdir: Path, overwrite: bool,
    init_month_filter: set[int] | None,
) -> None:
    region_name = region["name"]
    lat_min, lat_max = region["lat"]
    lon_min, lon_max = region["lon"]
    pycpt_season = _pycpt_season(season)

    local_cfg = cfg["data"]["local"]
    root = Path(local_cfg["root"])
    model_var = local_cfg["model_vars"][_VARKEY[var]]
    model_base_map = local_cfg.get("model_base", {})
    patterns = local_cfg.get("path_patterns", {})
    model_path_patterns = local_cfg.get("model_path_patterns", {})

    cpt_args = _build_cpt_args(cfg)
    xy_dims = dict(
        x_lat_dim="Y", x_lon_dim="X", x_sample_dim="T", x_feature_dim="M",
        y_lat_dim="Y", y_lon_dim="X", y_sample_dim="T",
    )

    init_months = _init_months_for_season(pycpt_season, max_lead)
    if init_month_filter is not None:
        init_months = [m for m in init_months if m in init_month_filter]
    if not init_months:
        print(f"[SKIP] {region_name}/{season}: no valid init months within max-lead={max_lead}")
        return

    for init_month in init_months:
        print(f"\n--- {region_name} / {season} / init={init_month:02d} ---")
        acc_maps: dict[str, xr.DataArray] = {}
        rpss_maps: dict[str, xr.DataArray] = {}

        for model in models_used:
            out_path = (
                outdir / region_name /
                f"{model}.{var}.{region_name}.{season}.init{init_month:02d}.pycpt_skill.{start_year}-{end_year}.nc"
            )
            if out_path.exists() and not overwrite:
                print(f"  [SKIP] {out_path.name} (exists)")
                continue

            base = model_base_map.get(model, model)
            model_patterns = {**patterns, **model_path_patterns.get(model, {})}

            acc_map, rpss_map, n_years = _compute_model_skill(
                model=model, model_base=base, model_patterns=model_patterns,
                root=root, var=model_var, init_month=init_month,
                pycpt_season=pycpt_season,
                lat_min=lat_min, lat_max=lat_max, lon_min=lon_min, lon_max=lon_max,
                obs_da=obs_da, start_year=start_year, end_year=end_year,
                cpt_args=cpt_args, xy_dims=xy_dims,
            )
            if acc_map is None:
                continue

            acc_maps[model] = acc_map
            if rpss_map is not None:
                rpss_maps[model] = rpss_map

            _save_skill(
                out_path, acc_map, rpss_map, model, var, region_name, season,
                init_month, start_year, end_year, n_years=n_years,
            )

        if not acc_maps:
            continue

        mme_path = (
            outdir / region_name /
            f"MME.{var}.{region_name}.{season}.init{init_month:02d}.pycpt_skill.{start_year}-{end_year}.nc"
        )
        if mme_path.exists() and not overwrite:
            print(f"  [SKIP] {mme_path.name} (exists)")
            continue

        mme_acc = xr.concat(list(acc_maps.values()), dim="_m").mean("_m").rename("acc")
        mme_rpss = (
            xr.concat(list(rpss_maps.values()), dim="_m").mean("_m").rename("rpss")
            if rpss_maps else None
        )
        _save_skill(
            mme_path, mme_acc, mme_rpss, "MME", var, region_name, season,
            init_month, start_year, end_year,
            models_included=list(acc_maps.keys()),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute PyCPT (CCA) bias-corrected hindcast skill (ACC, RPSS)."
    )
    p.add_argument("--config", default="confignmme.yaml")
    p.add_argument("--var", default="prec", choices=["prec", "tref"])
    p.add_argument("--regions", default="ALL", help="Comma-separated region names, or ALL")
    p.add_argument("--seasons", default="ALL",
                   help="Comma-separated season codes matching confignmme.yaml, or ALL")
    p.add_argument("--models", default="ALL",
                   help="Comma-separated model names, or ALL (also computes MME)")
    p.add_argument("--init-months", default="ALL",
                   help="Comma-separated init months 1-12, or ALL (auto-derived per season/max-lead)")
    p.add_argument("--outdir", required=True)
    p.add_argument("--start-year", type=int, default=1991)
    p.add_argument("--end-year", type=int, default=2020)
    p.add_argument("--max-lead", type=int, default=9)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg = U.load_config(Path(args.config))
    var = args.var
    outdir = Path(args.outdir)

    local_cfg = cfg["data"]["local"]
    root = Path(local_cfg["root"])
    if var == "prec":
        obs_cfg = local_cfg["predictand"]
        obs_full = U.load_predictand_local(root, obs_cfg["dir"], obs_cfg["var"])
    else:
        tref_cfg = local_cfg["predictand_tref"]
        obs_full = _load_tref_obs(tref_cfg["file"])

    all_regions = cfg.get("regions", [])
    region_filter = None if args.regions == "ALL" else set(args.regions.split(","))
    regions = [r for r in all_regions if region_filter is None or r["name"] in region_filter]
    if not regions:
        print("[ERROR] No matching regions")
        return 1

    pycpt_region_map = {r["name"]: r for r in cfg.get("pycpt", {}).get("regions", [])}
    season_filter = None if args.seasons == "ALL" else set(args.seasons.split(","))
    model_filter = None if args.models == "ALL" else set(args.models.split(","))
    init_month_filter = (
        None if args.init_months == "ALL"
        else {int(m) for m in args.init_months.split(",")}
    )

    for region in regions:
        region_name = region["name"]
        seasons = region.get("seasons", [])
        if season_filter is not None:
            seasons = [s for s in seasons if s in season_filter]
        if not seasons:
            print(f"[SKIP] {region_name}: no matching seasons")
            continue

        pycpt_region = pycpt_region_map.get(region_name, {})
        models_used = U.resolve_models(cfg.get("models", []), pycpt_region)
        if model_filter is not None:
            models_used = [m for m in models_used if m in model_filter]
        if not models_used:
            print(f"[SKIP] {region_name}: no matching models")
            continue

        lat_min, lat_max = region["lat"]
        lon_min, lon_max = region["lon"]
        obs_region = obs_full.sel(Y=slice(lat_min, lat_max), X=slice(lon_min, lon_max))

        for season in seasons:
            print(f"\n{'='*60}\n[REGION] {region_name}  [SEASON] {season}  [VAR] {var}\n{'='*60}")
            _run_region_season(
                cfg=cfg, region=region, season=season, models_used=models_used,
                var=var, obs_da=obs_region,
                start_year=args.start_year, end_year=args.end_year,
                max_lead=args.max_lead, outdir=outdir, overwrite=args.overwrite,
                init_month_filter=init_month_filter,
            )

    print("\n[DONE]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

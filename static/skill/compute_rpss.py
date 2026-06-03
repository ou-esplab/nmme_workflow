#!/usr/bin/env python3
"""
Compute RPSS (Ranked Probability Skill Score) for NMME hindcast tercile forecasts.

For each model, variable, initialization month, and season:
  1. Load hindcast data (1991-2020), compute seasonal mean anomalies per member/year.
  2. Derive pooled ensemble tercile thresholds from the full hindcast sample.
  3. Compute P(BN), P(NN), P(AN) for each year.
  4. Load observations, compute seasonal means and obs tercile thresholds.
  5. Compute RPSS = 1 - mean(RPS) / mean(RPS_clim) at each gridpoint.
  6. Write one NetCDF per (model, var, init_month, season).

Observation sources:
  prec : CHIRPS monthly (covers 50S-50N; RPSS will be NaN outside this range)
  tref : GHCN-CAMS monthly (land only; RPSS will be NaN over ocean)
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.config import load_config
from utils.paths import ensure_dir

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "products"))
from make_tercile_probability_maps import SEASON_MONTHS, get_season_leads

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VAR_LEV = {"prec": "sfc", "tref": "2m"}

# Models whose hindcast data lives under reforecast/ instead of hindcast/
SFS_MODELS = {"NOAA-SFS"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_0360(da: xr.DataArray) -> xr.DataArray:
    """Convert longitude coordinate to 0-360 range and sort."""
    lon_name = "lon" if "lon" in da.dims else "longitude"
    if da[lon_name].min() < 0:
        da = da.assign_coords({lon_name: (da[lon_name] + 360) % 360}).sortby(lon_name)
    return da


def _normalize_hindcast_dims(da: xr.DataArray) -> xr.DataArray:
    """Rename raw NMME SubX dims to standard names and squeeze singleton init."""
    rmap = {}
    for old, new in [("S", "init"), ("M", "member"), ("L", "lead"),
                     ("X", "lon"), ("Y", "lat")]:
        if old in da.dims and new not in da.dims:
            rmap[old] = new
    if rmap:
        da = da.rename(rmap)
    if "init" in da.dims and da.sizes["init"] == 1:
        da = da.squeeze("init", drop=True)
    if "Z" in da.dims:
        da = da.isel(Z=0, drop=True)
    # Convert float leads (0.5, 1.5, ...) to integers (0, 1, ...) so sel() works
    if "lead" in da.dims and not pd.api.types.is_integer_dtype(da["lead"].dtype):
        da = da.assign_coords(lead=da["lead"].values.astype(int))
    return da


def _hindcast_root(hindcast_root: Path, model: str, var: str) -> Path:
    if model in SFS_MODELS:
        return hindcast_root / model / "reforecast" / var
    return hindcast_root / model / "hindcast" / var


def _season_calendar_months(init_month: int, l0: int, l1: int) -> list:
    """Return ordered list of calendar months covered by the season leads."""
    return [((init_month - 1 + l) % 12) + 1 for l in range(l0, l1)]


def _valid_year(init_year: int, init_month: int, lead: int) -> int:
    """Return the calendar year for a given initialization year/month and lead."""
    return init_year + (init_month - 1 + lead) // 12


# ---------------------------------------------------------------------------
# Step 1-2: Load hindcast anomalies and compute pooled tercile thresholds
# ---------------------------------------------------------------------------

def load_hindcast_seasonal_anoms(
    hindcast_root: Path,
    clim_root: Path,
    model: str,
    var: str,
    lev: str,
    init_month: int,
    l0: int,
    l1: int,
    start_year: int,
    end_year: int,
) -> tuple[xr.DataArray | None, list[int]]:
    """
    Returns:
        all_anoms : DataArray (year, member, lat, lon) of seasonal mean anomalies
        years     : list of years that were successfully loaded
    """
    root = _hindcast_root(hindcast_root, model, var)
    clim_path = clim_root / f"{model}.{var}_{lev}.clim.1991-2020.nc"

    if not clim_path.exists():
        print(f"[SKIP] No climatology: {clim_path}")
        return None, []

    clim = xr.open_dataset(clim_path)[var]
    if not pd.api.types.is_integer_dtype(clim["lead"].dtype):
        clim = clim.assign_coords(lead=clim["lead"].astype(int))

    yearly_anoms = []
    years_found = []

    for year in range(start_year, end_year + 1):
        fp = root / f"{var}_{model}_{year}_{init_month:02d}.nc"
        if not fp.exists():
            continue

        try:
            ds = xr.open_dataset(fp, decode_times=False)
        except Exception as exc:
            print(f"[WARN] Cannot open {fp}: {exc}")
            continue

        da = ds[var]
        da = _normalize_hindcast_dims(da)
        da = _to_0360(da)

        # Average anomaly over season leads
        lead_anoms = []
        for l in range(l0, l1):
            if l not in da["lead"].values:
                continue
            valid_month = ((init_month - 1 + l) % 12) + 1
            if "month" in clim.dims and valid_month not in clim["month"].values:
                continue
            clim_sel = clim.sel(month=valid_month, lead=l) if "month" in clim.dims \
                else clim.sel(lead=l)
            da_lead = da.sel(lead=l)
            # Align spatial grids before subtraction
            da_lead, clim_sel = xr.align(da_lead, clim_sel, join="override")
            lead_anoms.append(da_lead - clim_sel)

        if not lead_anoms:
            continue

        seasonal_anom = xr.concat(lead_anoms, dim="_tmp_lead").mean("_tmp_lead")
        # Drop any leftover non-spatial/non-member coords
        seasonal_anom = seasonal_anom.reset_coords(drop=True)
        yearly_anoms.append(seasonal_anom.load())
        years_found.append(year)
        ds.close()

    if not yearly_anoms:
        return None, []

    all_anoms = xr.concat(yearly_anoms, dim="year")
    all_anoms = all_anoms.assign_coords(year=("year", years_found))
    return all_anoms, years_found


# ---------------------------------------------------------------------------
# Step 3: Forecast tercile probabilities
# ---------------------------------------------------------------------------

def compute_forecast_probs(
    all_anoms: xr.DataArray,
) -> tuple[xr.DataArray, xr.DataArray, xr.DataArray]:
    """
    Derive pooled tercile thresholds and compute P(BN), P(NN), P(AN) per year.

    all_anoms : (year, member, lat, lon)

    Returns P_BN, P_NN, P_AN each of shape (year, lat, lon).
    """
    # Pool all (year × member) values to compute thresholds
    pooled = all_anoms.stack(pool=("year", "member"))
    t33 = pooled.quantile(1 / 3, dim="pool").drop_vars("quantile", errors="ignore")
    t66 = pooled.quantile(2 / 3, dim="pool").drop_vars("quantile", errors="ignore")

    # Classify each member in each year
    p_bn = (all_anoms < t33).mean("member").astype(float)
    p_an = (all_anoms > t66).mean("member").astype(float)
    p_nn = 1.0 - p_bn - p_an
    p_nn = p_nn.clip(0.0, 1.0)

    return p_bn, p_nn, p_an


# ---------------------------------------------------------------------------
# Step 4-5: Observations
# ---------------------------------------------------------------------------

def _decode_obs_time(ds: xr.Dataset) -> xr.Dataset:
    try:
        return xr.decode_cf(ds)
    except Exception:
        return ds


def _obs_monthly_da(var: str, obs_precip: str, obs_tref: str) -> xr.DataArray:
    """Load the appropriate monthly obs dataset and return the DataArray."""
    if var == "prec":
        ds = _decode_obs_time(xr.open_dataset(obs_precip))
        da = ds["precip"].where(ds["precip"] > -9000)
    else:
        ds = _decode_obs_time(xr.open_dataset(obs_tref))
        da = ds["air"]

    # Standardize spatial dim names
    rmap = {}
    if "latitude" in da.dims:
        rmap["latitude"] = "lat"
    if "longitude" in da.dims:
        rmap["longitude"] = "lon"
    if rmap:
        da = da.rename(rmap)

    da = _to_0360(da)
    return da


def obs_seasonal_means(
    obs_da: xr.DataArray,
    init_month: int,
    l0: int,
    l1: int,
    start_year: int,
    end_year: int,
) -> tuple[xr.DataArray | None, list[int]]:
    """
    Compute seasonal mean for each init year.

    Returns:
        result : DataArray (year, lat, lon)
        years  : list of years with complete data
    """
    n_leads = l1 - l0
    yearly = []
    years = []

    for y in range(start_year, end_year + 1):
        month_slices = []
        for l in range(l0, l1):
            valid_month = ((init_month - 1 + l) % 12) + 1
            valid_year = _valid_year(y, init_month, l)
            try:
                sel = obs_da.sel(time=f"{valid_year}-{valid_month:02d}").squeeze()
                month_slices.append(sel)
            except (KeyError, IndexError):
                break

        if len(month_slices) == n_leads:
            seasonal = xr.concat(month_slices, dim="_mon").mean("_mon")
            yearly.append(seasonal.load())
            years.append(y)

    if not yearly:
        return None, []

    result = xr.concat(yearly, dim="year")
    result = result.assign_coords(year=("year", years))
    return result, years


def classify_obs(obs_seasonal: xr.DataArray) -> xr.DataArray:
    """
    Classify observed seasonal values into tercile categories.
    Returns DataArray (year, lat, lon) with values 0=BN, 1=NN, 2=AN, NaN where obs missing.
    """
    t33 = obs_seasonal.quantile(1 / 3, dim="year").drop_vars("quantile", errors="ignore")
    t66 = obs_seasonal.quantile(2 / 3, dim="year").drop_vars("quantile", errors="ignore")

    obs_cat = xr.where(obs_seasonal < t33, 0,
              xr.where(obs_seasonal > t66, 2, 1)).astype(float)

    # Mask gridpoints where obs had no valid data.  NaN comparisons silently
    # fall through to the else-branch, producing spurious category=1 at
    # gridpoints outside the obs domain (e.g. beyond CHIRPS ±50° lat).
    # Use "any valid year" so a single missing year does not discard a point.
    obs_valid = obs_seasonal.notnull().any("year")
    obs_cat = obs_cat.where(obs_valid)
    return obs_cat


# ---------------------------------------------------------------------------
# Core RPSS computation
# ---------------------------------------------------------------------------

def compute_rpss(
    p_bn: xr.DataArray,
    p_nn: xr.DataArray,
    obs_cat: xr.DataArray,
) -> xr.DataArray:
    """
    RPSS = 1 - mean(RPS_fcst) / mean(RPS_clim).

    All inputs have dims (year, lat, lon).
    Returns DataArray (lat, lon).
    """
    # Use .where() so NaN obs_cat (no-data gridpoints) propagates as NaN
    # rather than silently becoming 0 via the False branch of == comparison.
    valid = obs_cat.notnull()
    o_bn = (obs_cat == 0).where(valid)
    o_nn = (obs_cat == 1).where(valid)

    rps_fcst = (p_bn - o_bn) ** 2 + (p_bn + p_nn - o_bn - o_nn) ** 2
    rps_clim = (1.0 / 3 - o_bn) ** 2 + (2.0 / 3 - o_bn - o_nn) ** 2

    mean_rps = rps_fcst.mean("year")
    mean_rps_clim = rps_clim.mean("year")

    return (1.0 - mean_rps / mean_rps_clim).rename("rpss")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute RPSS skill for NMME tercile hindcasts."
    )
    p.add_argument("--config", default="confignmme.yaml")
    p.add_argument("--var", required=True, choices=["prec", "tref"],
                   help="Variable to process")
    p.add_argument("--hindcast-root", required=True,
                   help="Root containing per-model hindcast/reforecast directories")
    p.add_argument("--clim-root", required=True,
                   help="Root containing model climatology NetCDF files")
    p.add_argument("--obs-precip", default=None,
                   help="Path to CHIRPS monthly NetCDF (required for prec)")
    p.add_argument("--obs-tref", default=None,
                   help="Path to GHCN-CAMS monthly NetCDF (required for tref)")
    p.add_argument("--outdir", required=True,
                   help="Output directory for RPSS NetCDF files")
    p.add_argument("--start-year", type=int, default=1991)
    p.add_argument("--end-year", type=int, default=2020)
    p.add_argument("--models", default="ALL",
                   help="Comma-separated model names, or ALL")
    p.add_argument("--seasons", default="ALL",
                   help="Comma-separated season names, or ALL")
    p.add_argument("--init-months", default="ALL",
                   help="Comma-separated init months (1-12), or ALL")
    p.add_argument("--overwrite", action="store_true",
                   help="Overwrite existing output files")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    cfg = load_config(args.config)
    var = args.var
    lev = VAR_LEV[var]

    hindcast_root = Path(args.hindcast_root)
    clim_root = Path(args.clim_root)
    outdir = Path(args.outdir)
    ensure_dir(outdir)

    # Observation source
    if var == "prec":
        if not args.obs_precip:
            print("[ERROR] --obs-precip required for var=prec")
            return 1
        obs_path = args.obs_precip
    else:
        if not args.obs_tref:
            print("[ERROR] --obs-tref required for var=tref")
            return 1
        obs_path = args.obs_tref

    print(f"[INFO] Loading obs for {var} from {obs_path}")
    obs_da = _obs_monthly_da(var, obs_path, obs_path)

    # Filter model/season/init lists
    all_models = cfg["models"]
    models = all_models if args.models == "ALL" \
        else [m for m in args.models.split(",") if m in all_models]

    all_seasons = list(SEASON_MONTHS.keys())
    seasons = all_seasons if args.seasons == "ALL" \
        else [s for s in args.seasons.split(",") if s in all_seasons]

    all_init_months = list(range(1, 13))
    init_months = all_init_months if args.init_months == "ALL" \
        else [int(m) for m in args.init_months.split(",")]

    # ----------------------------------------------------------------
    # Loop over models, init months, seasons
    # ----------------------------------------------------------------
    for model in models:
        print(f"\n{'='*60}")
        print(f"[MODEL] {model}  var={var}")
        print(f"{'='*60}")

        for init_month in init_months:
            for season in seasons:
                out_path = outdir / f"{model}.{var}.init{init_month:02d}.{season}.rpss.{args.start_year}-{args.end_year}.nc"

                if out_path.exists() and not args.overwrite:
                    print(f"[SKIP] {out_path.name}")
                    continue

                # Determine season lead window
                try:
                    l0, l1 = get_season_leads(
                        f"{args.start_year}{init_month:02d}", season
                    )
                except RuntimeError:
                    continue  # season not reachable from this init month

                print(f"  init={init_month:02d}  season={season}  leads={l0}-{l1-1}")

                # Step 1: hindcast anomalies
                all_anoms, years = load_hindcast_seasonal_anoms(
                    hindcast_root, clim_root, model, var, lev,
                    init_month, l0, l1,
                    args.start_year, args.end_year,
                )
                if all_anoms is None or len(years) < 10:
                    print(f"  [SKIP] insufficient hindcast years ({len(years)})")
                    continue

                # Step 2-3: forecast probabilities
                p_bn, p_nn, p_an = compute_forecast_probs(all_anoms)

                # Step 4: obs seasonal means
                obs_seasonal, obs_years = obs_seasonal_means(
                    obs_da, init_month, l0, l1,
                    args.start_year, args.end_year,
                )
                if obs_seasonal is None or len(obs_years) < 10:
                    print(f"  [SKIP] insufficient obs years ({len(obs_years) if obs_years else 0})")
                    continue

                # Align hindcast and obs to common years
                common_years = sorted(set(years) & set(obs_years))
                if len(common_years) < 10:
                    print(f"  [SKIP] too few common years ({len(common_years)})")
                    continue

                p_bn_c = p_bn.sel(year=common_years)
                p_nn_c = p_nn.sel(year=common_years)
                obs_s_c = obs_seasonal.sel(year=common_years)

                # Regrid obs to model grid
                model_lat = p_bn_c["lat"].values
                model_lon = p_bn_c["lon"].values
                obs_interp = obs_s_c.interp(
                    lat=model_lat, lon=model_lon,
                    method="linear", kwargs={"fill_value": None},
                )

                # Step 5: classify obs
                obs_cat = classify_obs(obs_interp)

                # Step 6: RPSS
                rpss = compute_rpss(p_bn_c, p_nn_c, obs_cat)
                rpss.attrs.update({
                    "model": model,
                    "var": var,
                    "init_month": init_month,
                    "season": season,
                    "period": f"{args.start_year}-{args.end_year}",
                    "n_years": len(common_years),
                    "long_name": "Ranked Probability Skill Score",
                    "valid_range": "-inf to 1 (positive = skilful)",
                })

                ds_out = rpss.to_dataset(name="rpss")
                ds_out.to_netcdf(out_path, encoding={"rpss": {"zlib": True, "complevel": 1}})
                print(f"  [SAVED] {out_path.name}")

    # ----------------------------------------------------------------
    # MME: pool all models' anomalies, rerun steps 2-6
    # ----------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"[MODEL] MME  var={var}")
    print(f"{'='*60}")

    for init_month in init_months:
        for season in seasons:
            out_path = outdir / f"MME.{var}.init{init_month:02d}.{season}.rpss.{args.start_year}-{args.end_year}.nc"

            if out_path.exists() and not args.overwrite:
                print(f"[SKIP] {out_path.name}")
                continue

            try:
                l0, l1 = get_season_leads(f"{args.start_year}{init_month:02d}", season)
            except RuntimeError:
                continue

            print(f"  init={init_month:02d}  season={season}  leads={l0}-{l1-1}")

            # Collect per-model anomaly arrays; regrid each to first-model grid
            model_anoms = []
            ref_lat = ref_lon = None

            for model in models:
                anoms, _ = load_hindcast_seasonal_anoms(
                    hindcast_root, clim_root, model, var, lev,
                    init_month, l0, l1,
                    args.start_year, args.end_year,
                )
                if anoms is None:
                    continue

                # Use first model's grid as reference
                if ref_lat is None:
                    ref_lat = anoms["lat"].values
                    ref_lon = anoms["lon"].values
                    model_anoms.append(anoms)
                else:
                    anoms_r = anoms.interp(lat=ref_lat, lon=ref_lon, method="linear")
                    model_anoms.append(anoms_r)

            if not model_anoms:
                print("  [SKIP] no model data for MME")
                continue

            # Select common years across models
            common_years_mme = sorted(
                set.intersection(*[set(a["year"].values.tolist()) for a in model_anoms])
            )
            if len(common_years_mme) < 10:
                print(f"  [SKIP] too few common MME years ({len(common_years_mme)})")
                continue

            mme_pieces = [a.sel(year=common_years_mme) for a in model_anoms]
            # Concatenate along member dim to pool all models' members
            mme_all = xr.concat(mme_pieces, dim="member")

            p_bn_mme, p_nn_mme, _ = compute_forecast_probs(mme_all)

            # Obs (same grid as first model)
            obs_seasonal, obs_years = obs_seasonal_means(
                obs_da, init_month, l0, l1,
                args.start_year, args.end_year,
            )
            if obs_seasonal is None:
                continue

            common_obs_years = sorted(set(common_years_mme) & set(obs_years))
            if len(common_obs_years) < 10:
                continue

            p_bn_mme_c = p_bn_mme.sel(year=common_obs_years)
            p_nn_mme_c = p_nn_mme.sel(year=common_obs_years)
            obs_interp = obs_seasonal.sel(year=common_obs_years).interp(
                lat=ref_lat, lon=ref_lon, method="linear",
                kwargs={"fill_value": None},
            )
            obs_cat = classify_obs(obs_interp)
            rpss = compute_rpss(p_bn_mme_c, p_nn_mme_c, obs_cat)
            rpss.attrs.update({
                "model": "MME",
                "var": var,
                "init_month": init_month,
                "season": season,
                "period": f"{args.start_year}-{args.end_year}",
                "n_years": len(common_obs_years),
                "models_included": ",".join(models),
                "long_name": "Ranked Probability Skill Score",
                "valid_range": "-inf to 1 (positive = skilful)",
            })
            ds_out = rpss.to_dataset(name="rpss")
            ds_out.to_netcdf(out_path, encoding={"rpss": {"zlib": True, "complevel": 1}})
            print(f"  [SAVED] {out_path.name}")

    print("\n[DONE]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

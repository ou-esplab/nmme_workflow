#!/usr/bin/env python3
"""
Compute RPSS (Ranked Probability Skill Score) for NMME hindcast tercile forecasts.

For each model, variable, and season start lead:
  - For every init_month (1..12): load the 3-month seasonal mean hindcast anomaly
    (average of monthly anomalies at leads season_start_lead, +1, +2),
    load the corresponding 3-month observed seasonal mean, compute per-year RPS.

Output: one NetCDF per (model, var) with dims (init_month, season_lead, lat, lon)
where season_lead is the first lead of the 3-month season (1..max_lead-2).

Observation sources:
  prec : CHIRPS monthly  — covers 50S–50N land; NaN elsewhere
  tref : GHCN-CAMS monthly — land only; NaN over ocean
"""

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

warnings.filterwarnings("ignore", message="All-NaN slice encountered")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.config import load_config
from utils.paths import ensure_dir

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VAR_LEV = {"prec": "sfc", "tref": "2m", "sst": "sfc"}
SFS_MODELS = {"NOAA-SFS"}
N_SEASON = 3                          # months per season
MONTH_ABBR = list("JFMAMJJASOND")    # 0-indexed


# ---------------------------------------------------------------------------
# Season label helper
# ---------------------------------------------------------------------------

def _season_label(init_month: int, season_start_lead: int) -> str:
    """E.g. init_month=1, season_start_lead=1 → 'FMA'."""
    return "".join(
        MONTH_ABBR[(init_month - 1 + l) % 12]
        for l in range(season_start_lead, season_start_lead + N_SEASON)
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _to_0360(da: xr.DataArray) -> xr.DataArray:
    lon_name = "lon" if "lon" in da.dims else "longitude"
    if da[lon_name].min() < 0:
        da = da.assign_coords({lon_name: (da[lon_name] + 360) % 360}).sortby(lon_name)
    return da


def _normalize_hindcast_dims(da: xr.DataArray) -> xr.DataArray:
    rmap = {}
    for old, new in [("S", "init"), ("M", "member"), ("L", "lead"),
                     ("X", "lon"), ("Y", "lat"),
                     ("latitude", "lat"), ("longitude", "lon")]:
        if old in da.dims and new not in da.dims:
            rmap[old] = new
    if rmap:
        da = da.rename(rmap)
    if "init" in da.dims and da.sizes["init"] == 1:
        da = da.squeeze("init", drop=True)
    if "Z" in da.dims:
        da = da.isel(Z=0, drop=True)
    if "lead" in da.dims and not pd.api.types.is_integer_dtype(da["lead"].dtype):
        da = da.assign_coords(lead=da["lead"].values.astype(int))
    return da


def _hindcast_root(hindcast_root: Path, model: str, var: str) -> Path:
    if model in SFS_MODELS:
        return hindcast_root / model / "reforecast" / var
    return hindcast_root / model / "hindcast" / var


def _decode_obs_time(ds: xr.Dataset) -> xr.Dataset:
    try:
        return xr.decode_cf(ds)
    except Exception:
        return ds


def _obs_monthly_da(var: str, obs_precip: str, obs_tref: str, obs_sst: str = "") -> xr.DataArray:
    if var == "prec":
        ds = _decode_obs_time(xr.open_dataset(obs_precip))
        da = ds["precip"].where(ds["precip"] > -9000)
    elif var == "sst":
        ds = _decode_obs_time(xr.open_dataset(obs_sst))
        da = ds["sst"].where(ds["sst"] < 1e10)
    else:
        ds = _decode_obs_time(xr.open_dataset(obs_tref))
        da = ds["air"]
    rmap = {}
    if "latitude" in da.dims:
        rmap["latitude"] = "lat"
    if "longitude" in da.dims:
        rmap["longitude"] = "lon"
    if rmap:
        da = da.rename(rmap)
    return _to_0360(da)


# ---------------------------------------------------------------------------
# Hindcast loader — single lead (building block)
# ---------------------------------------------------------------------------

def load_hindcast_single_lead_anom(
    hindcast_root: Path,
    clim_root: Path,
    model: str,
    var: str,
    lev: str,
    init_month: int,
    lead: int,
    start_year: int,
    end_year: int,
) -> tuple[xr.DataArray | None, list[int]]:
    root = _hindcast_root(hindcast_root, model, var)
    clim_path = clim_root / f"{model}.{var}_{lev}.clim.1991-2020.nc"

    if not clim_path.exists():
        return None, []

    clim = xr.open_dataset(clim_path)[var]
    if not pd.api.types.is_integer_dtype(clim["lead"].dtype):
        clim = clim.assign_coords(lead=clim["lead"].astype(int))

    if lead not in clim["lead"].values:
        return None, []

    if "month" in clim.dims and init_month not in clim["month"].values:
        return None, []

    clim_sel = clim.sel(month=init_month, lead=lead) if "month" in clim.dims \
        else clim.sel(lead=lead)

    yearly_anoms: list[xr.DataArray] = []
    years_found: list[int] = []

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

        if lead not in da["lead"].values:
            ds.close()
            continue

        da_lead = da.sel(lead=lead)
        da_lead, clim_a = xr.align(da_lead, clim_sel, join="override")
        anom = (da_lead - clim_a).reset_coords(drop=True)
        if "Z" in anom.dims:
            anom = anom.squeeze("Z", drop=True)
        yearly_anoms.append(anom.load())
        years_found.append(year)
        ds.close()

    if not yearly_anoms:
        return None, []

    all_anoms = xr.concat(yearly_anoms, dim="year")
    all_anoms = all_anoms.assign_coords(year=("year", years_found))
    return all_anoms, years_found


# ---------------------------------------------------------------------------
# Hindcast loader — 3-month seasonal mean
# ---------------------------------------------------------------------------

def load_hindcast_seasonal_anom(
    hindcast_root: Path,
    clim_root: Path,
    model: str,
    var: str,
    lev: str,
    init_month: int,
    season_start_lead: int,
    start_year: int,
    end_year: int,
) -> tuple[xr.DataArray | None, list[int]]:
    """
    Average N_SEASON monthly anomalies starting at season_start_lead.
    Returns (DataArray(year, member, lat, lon), years) or (None, []).
    """
    per_lead: dict[int, tuple[xr.DataArray, list[int]]] = {}
    for lead in range(season_start_lead, season_start_lead + N_SEASON):
        anoms, years = load_hindcast_single_lead_anom(
            hindcast_root, clim_root, model, var, lev,
            init_month, lead, start_year, end_year,
        )
        if anoms is None:
            return None, []
        per_lead[lead] = (anoms, years)

    common = sorted(set.intersection(*[set(y) for _, y in per_lead.values()]))
    if not common:
        return None, []

    arrays = [
        per_lead[l][0].sel(year=common)
        for l in range(season_start_lead, season_start_lead + N_SEASON)
    ]
    seasonal = xr.concat(arrays, dim="_sl").mean("_sl")
    return seasonal, common


# ---------------------------------------------------------------------------
# Obs loader — single calendar month (building block)
# ---------------------------------------------------------------------------

def obs_single_month_means(
    obs_da: xr.DataArray,
    valid_month: int,
    start_year: int,
    end_year: int,
) -> tuple[xr.DataArray | None, list[int]]:
    slices: list[xr.DataArray] = []
    years: list[int] = []

    for y in range(start_year, end_year + 1):
        try:
            sl = obs_da.sel(time=f"{y}-{valid_month:02d}").squeeze()
            slices.append(sl.load())
            years.append(y)
        except (KeyError, IndexError):
            continue

    if not slices:
        return None, []

    result = xr.concat(slices, dim="year")
    result = result.assign_coords(year=("year", years))
    return result, years


# ---------------------------------------------------------------------------
# Obs loader — 3-month seasonal mean
# ---------------------------------------------------------------------------

def _load_seasonal_obs(
    obs_da: xr.DataArray,
    init_month: int,
    season_start_lead: int,
    start_year: int,
    end_year: int,
) -> tuple[xr.DataArray | None, list[int]]:
    """
    Load N_SEASON consecutive monthly obs and return their mean, indexed by init year.
    """
    slices: list[xr.DataArray] = []
    init_year_sets: list[set[int]] = []

    for lead in range(season_start_lead, season_start_lead + N_SEASON):
        valid_month = ((init_month - 1 + lead) % 12) + 1
        valid_yr_off = (init_month - 1 + lead) // 12
        obs_mo, obs_yrs = obs_single_month_means(
            obs_da, valid_month,
            start_year + valid_yr_off,
            end_year + valid_yr_off,
        )
        if obs_mo is None:
            return None, []
        init_yrs = [y - valid_yr_off for y in obs_yrs]
        slices.append(obs_mo.assign_coords(year=("year", init_yrs)))
        init_year_sets.append(set(init_yrs))

    common = sorted(set.intersection(*init_year_sets))
    if not common:
        return None, []

    seasonal_mean = xr.concat(
        [s.sel(year=common) for s in slices], dim="_m"
    ).mean("_m")
    return seasonal_mean, common


# ---------------------------------------------------------------------------
# Forecast tercile probabilities
# ---------------------------------------------------------------------------

def compute_forecast_probs(
    all_anoms: xr.DataArray,
) -> tuple[xr.DataArray, xr.DataArray, xr.DataArray]:
    pooled = all_anoms.stack(pool=("year", "member"))
    t33 = pooled.quantile(1 / 3, dim="pool").drop_vars("quantile", errors="ignore")
    t66 = pooled.quantile(2 / 3, dim="pool").drop_vars("quantile", errors="ignore")
    p_bn = (all_anoms < t33).mean("member").astype(float)
    p_an = (all_anoms > t66).mean("member").astype(float)
    p_nn = (1.0 - p_bn - p_an).clip(0.0, 1.0)
    return p_bn, p_nn, p_an


# ---------------------------------------------------------------------------
# RPS / RPSS
# ---------------------------------------------------------------------------

def _rps_per_year(
    p_bn: xr.DataArray,
    p_nn: xr.DataArray,
    obs_cat: xr.DataArray,
) -> tuple[xr.DataArray, xr.DataArray]:
    valid = obs_cat.notnull()
    o_bn = (obs_cat == 0).where(valid)
    o_nn = (obs_cat == 1).where(valid)
    rps      = (p_bn - o_bn) ** 2 + (p_bn + p_nn - o_bn - o_nn) ** 2
    rps_clim = (1.0 / 3 - o_bn) ** 2 + (2.0 / 3 - o_bn - o_nn) ** 2
    return rps, rps_clim


def compute_rpss(
    p_bn: xr.DataArray,
    p_nn: xr.DataArray,
    obs_cat: xr.DataArray,
) -> xr.DataArray:
    rps, rps_clim = _rps_per_year(p_bn, p_nn, obs_cat)
    return (1.0 - rps.mean("year") / rps_clim.mean("year")).rename("rpss")


# ---------------------------------------------------------------------------
# Obs classification
# ---------------------------------------------------------------------------

def _classify_obs(
    obs_interp: xr.DataArray,
    t33: xr.DataArray,
    t66: xr.DataArray,
) -> xr.DataArray:
    obs_cat = xr.where(obs_interp < t33, 0,
              xr.where(obs_interp > t66, 2, 1)).astype(float)
    obs_cat = obs_cat.where(obs_interp.notnull().any("year"))
    return obs_cat


# ---------------------------------------------------------------------------
# Per-(init_month, season_start_lead) RPSS helpers
# ---------------------------------------------------------------------------

def _rpss_from_probs(
    p_bn: xr.DataArray,
    p_nn: xr.DataArray,
    fcst_years: list[int],
    obs_da: xr.DataArray,
    init_month: int,
    season_start_lead: int,
    start_year: int,
    end_year: int,
    ref_lat: np.ndarray,
    ref_lon: np.ndarray,
    obs_thresh_cache: dict,
) -> xr.DataArray | None:
    """
    Compute RPSS(lat, lon) from pre-computed p_bn/p_nn.
    Observations are the N_SEASON-month seasonal mean.
    """
    obs_seasonal, obs_init_years = _load_seasonal_obs(
        obs_da, init_month, season_start_lead, start_year, end_year
    )
    if obs_seasonal is None:
        return None

    common = sorted(set(fcst_years) & set(obs_init_years))
    if len(common) < 5:
        return None

    p_bn_c = p_bn.sel(year=common)
    p_nn_c = p_nn.sel(year=common)
    obs_c  = obs_seasonal.sel(year=common)
    obs_interp = obs_c.interp(lat=ref_lat, lon=ref_lon,
                               method="linear", kwargs={"fill_value": None})

    # Cache key: tuple of valid calendar months for this season
    season_key = tuple(
        ((init_month - 1 + l) % 12) + 1
        for l in range(season_start_lead, season_start_lead + N_SEASON)
    )
    if season_key not in obs_thresh_cache:
        obs_full_i = obs_seasonal.interp(lat=ref_lat, lon=ref_lon,
                                          method="linear", kwargs={"fill_value": None})
        t33 = obs_full_i.quantile(1/3, dim="year").drop_vars("quantile", errors="ignore")
        t66 = obs_full_i.quantile(2/3, dim="year").drop_vars("quantile", errors="ignore")
        obs_thresh_cache[season_key] = (t33, t66)

    t33, t66 = obs_thresh_cache[season_key]
    obs_cat = _classify_obs(obs_interp, t33, t66)
    return compute_rpss(p_bn_c, p_nn_c, obs_cat)


def _rpss_one_cell(
    anoms: xr.DataArray,
    obs_da: xr.DataArray,
    init_month: int,
    season_start_lead: int,
    start_year: int,
    end_year: int,
    ref_lat: np.ndarray,
    ref_lon: np.ndarray,
    obs_thresh_cache: dict,
) -> xr.DataArray | None:
    p_bn, p_nn, _ = compute_forecast_probs(anoms)
    return _rpss_from_probs(
        p_bn, p_nn, anoms["year"].values.tolist(),
        obs_da, init_month, season_start_lead, start_year, end_year,
        ref_lat, ref_lon, obs_thresh_cache,
    )


def _rpss_one_cell_mme(
    model_anoms_list: list[xr.DataArray],
    obs_da: xr.DataArray,
    init_month: int,
    season_start_lead: int,
    start_year: int,
    end_year: int,
    ref_lat: np.ndarray,
    ref_lon: np.ndarray,
    obs_thresh_cache: dict,
) -> xr.DataArray | None:
    p_bn_list: list[xr.DataArray] = []
    p_nn_list: list[xr.DataArray] = []
    for anoms in model_anoms_list:
        p_bn, p_nn, _ = compute_forecast_probs(anoms)
        p_bn_list.append(p_bn)
        p_nn_list.append(p_nn)

    p_bn_mme = xr.concat(p_bn_list, dim="_m").mean("_m")
    p_nn_mme = xr.concat(p_nn_list, dim="_m").mean("_m")
    fcst_years = model_anoms_list[0]["year"].values.tolist()

    return _rpss_from_probs(
        p_bn_mme, p_nn_mme, fcst_years,
        obs_da, init_month, season_start_lead, start_year, end_year,
        ref_lat, ref_lon, obs_thresh_cache,
    )


# ---------------------------------------------------------------------------
# Per-model RPSS computation (init_month × season_lead)
# ---------------------------------------------------------------------------

def _rpss_for_model(
    model: str,
    var: str,
    lev: str,
    hindcast_root: Path,
    clim_root: Path,
    obs_da: xr.DataArray,
    max_lead: int,
    start_year: int,
    end_year: int,
) -> xr.DataArray | None:
    obs_thresh_cache: dict[tuple, tuple] = {}
    ref_lat: np.ndarray | None = None
    ref_lon: np.ndarray | None = None

    rpss_by_init: dict[int, xr.DataArray] = {}

    for init_month in range(1, 13):
        rpss_by_season: dict[int, xr.DataArray] = {}

        for season_start_lead in range(1, max_lead - N_SEASON + 2):
            anoms, _ = load_hindcast_seasonal_anom(
                hindcast_root, clim_root, model, var, lev,
                init_month, season_start_lead, start_year, end_year,
            )
            if anoms is None or anoms.sizes["year"] < 5:
                continue

            if ref_lat is None:
                ref_lat = anoms["lat"].values
                ref_lon = anoms["lon"].values

            rpss_cell = _rpss_one_cell(
                anoms, obs_da, init_month, season_start_lead,
                start_year, end_year,
                ref_lat, ref_lon, obs_thresh_cache,
            )
            if rpss_cell is not None:
                rpss_by_season[season_start_lead] = rpss_cell
                print(f"  init={init_month:02d}  season={_season_label(init_month, season_start_lead)}"
                      f"  n_years={anoms.sizes['year']}")

        if rpss_by_season:
            s_leads = sorted(rpss_by_season.keys())
            rpss_init = xr.concat([rpss_by_season[l] for l in s_leads], dim="season_lead")
            rpss_init = rpss_init.assign_coords(season_lead=("season_lead", s_leads))
            rpss_by_init[init_month] = rpss_init

    if not rpss_by_init:
        return None

    inits    = sorted(rpss_by_init.keys())
    rpss_all = xr.concat([rpss_by_init[i] for i in inits], dim="init_month")
    rpss_all = rpss_all.assign_coords(init_month=("init_month", inits))
    return rpss_all


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute seasonal RPSS skill for NMME hindcasts (3-month seasons)."
    )
    p.add_argument("--config", default="confignmme.yaml")
    p.add_argument("--var", required=True, choices=["prec", "tref", "sst"])
    p.add_argument("--hindcast-root", required=True)
    p.add_argument("--clim-root",     required=True)
    p.add_argument("--obs-precip", default=None)
    p.add_argument("--obs-tref",   default=None)
    p.add_argument("--obs-sst",    default=None)
    p.add_argument("--outdir",     required=True)
    p.add_argument("--start-year", type=int, default=1991)
    p.add_argument("--end-year",   type=int, default=2020)
    p.add_argument("--max-lead",   type=int, default=9)
    p.add_argument("--models",     default="ALL")
    p.add_argument("--overwrite",  action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg  = load_config(args.config)
    var  = args.var
    lev  = VAR_LEV[var]

    hindcast_root = Path(args.hindcast_root)
    clim_root     = Path(args.clim_root)
    outdir        = Path(args.outdir)
    ensure_dir(outdir)

    if var == "prec":
        if not args.obs_precip:
            print("[ERROR] --obs-precip required for var=prec")
            return 1
        obs_da = _obs_monthly_da(var, args.obs_precip, "", "")
    elif var == "sst":
        if not args.obs_sst:
            print("[ERROR] --obs-sst required for var=sst")
            return 1
        obs_da = _obs_monthly_da(var, "", "", args.obs_sst)
    else:
        if not args.obs_tref:
            print("[ERROR] --obs-tref required for var=tref")
            return 1
        obs_da = _obs_monthly_da(var, "", args.obs_tref, "")

    all_models = cfg["models"]
    requested  = set(args.models.split(",")) if args.models != "ALL" else {"ALL"}
    run_mme    = args.models == "ALL" or "MME" in requested
    indiv_requested = requested - {"MME", "ALL"}
    models = all_models if args.models == "ALL" or not indiv_requested \
        else [m for m in indiv_requested if m in all_models]
    mme_source_models = all_models

    # ----------------------------------------------------------------
    # Individual models
    # ----------------------------------------------------------------
    for model in models:
        print(f"\n{'='*60}")
        print(f"[MODEL] {model}  var={var}")
        print(f"{'='*60}")

        out_path = outdir / f"{model}.{var}.rpss.{args.start_year}-{args.end_year}.nc"
        if out_path.exists() and not args.overwrite:
            print(f"[SKIP] {out_path.name}")
            continue

        rpss_all = _rpss_for_model(
            model, var, lev,
            hindcast_root, clim_root, obs_da,
            args.max_lead, args.start_year, args.end_year,
        )
        if rpss_all is None:
            print(f"[SKIP] No data for {model}")
            continue

        rpss_all.attrs.update({
            "model": model, "var": var,
            "period": f"{args.start_year}-{args.end_year}",
            "long_name": "Ranked Probability Skill Score",
            "valid_range": "-inf to 1 (positive = skilful)",
            "season_def": f"{N_SEASON}-month seasonal means",
        })
        ds_out = rpss_all.to_dataset(name="rpss")
        ds_out.to_netcdf(out_path, encoding={"rpss": {"zlib": True, "complevel": 1}})
        print(f"[SAVED] {out_path.name}")

    # ----------------------------------------------------------------
    # MME: probability averaging across all models
    # ----------------------------------------------------------------
    if not run_mme:
        print("\n[DONE]")
        return 0

    print(f"\n{'='*60}")
    print(f"[MODEL] MME  var={var}")
    print(f"{'='*60}")

    out_path_mme = outdir / f"MME.{var}.rpss.{args.start_year}-{args.end_year}.nc"
    if out_path_mme.exists() and not args.overwrite:
        print(f"[SKIP] {out_path_mme.name}")
    else:
        obs_thresh_cache_mme: dict[tuple, tuple] = {}
        ref_lat = ref_lon = None
        rpss_by_init_mme: dict[int, xr.DataArray] = {}

        for init_month in range(1, 13):
            rpss_by_season_mme: dict[int, xr.DataArray] = {}

            for season_start_lead in range(1, args.max_lead - N_SEASON + 2):
                model_anoms: list[xr.DataArray] = []
                common_years_all: set[int] | None = None

                for model in mme_source_models:
                    anoms, fcst_years = load_hindcast_seasonal_anom(
                        hindcast_root, clim_root, model, var, lev,
                        init_month, season_start_lead,
                        args.start_year, args.end_year,
                    )
                    if anoms is None:
                        continue
                    if ref_lat is None:
                        ref_lat = anoms["lat"].values
                        ref_lon = anoms["lon"].values
                    if not np.array_equal(anoms["lat"].values, ref_lat) or \
                       not np.array_equal(anoms["lon"].values, ref_lon):
                        anoms = anoms.interp(lat=ref_lat, lon=ref_lon,
                                             method="linear",
                                             kwargs={"fill_value": None})
                    yr_set = set(fcst_years)
                    common_years_all = yr_set if common_years_all is None \
                        else common_years_all & yr_set
                    model_anoms.append(anoms)

                if not model_anoms or not common_years_all or len(common_years_all) < 5:
                    continue

                common_years    = sorted(common_years_all)
                model_anoms_sel = [a.sel(year=common_years) for a in model_anoms]

                rpss_cell = _rpss_one_cell_mme(
                    model_anoms_sel, obs_da, init_month, season_start_lead,
                    args.start_year, args.end_year,
                    ref_lat, ref_lon, obs_thresh_cache_mme,
                )
                if rpss_cell is not None:
                    rpss_by_season_mme[season_start_lead] = rpss_cell
                    print(f"  init={init_month:02d}  season={_season_label(init_month, season_start_lead)}"
                          f"  n_years={len(common_years)}")

            if rpss_by_season_mme:
                s_leads = sorted(rpss_by_season_mme.keys())
                rpss_init = xr.concat([rpss_by_season_mme[l] for l in s_leads], dim="season_lead")
                rpss_init = rpss_init.assign_coords(season_lead=("season_lead", s_leads))
                rpss_by_init_mme[init_month] = rpss_init

        if rpss_by_init_mme:
            inits    = sorted(rpss_by_init_mme.keys())
            rpss_all = xr.concat([rpss_by_init_mme[i] for i in inits], dim="init_month")
            rpss_all = rpss_all.assign_coords(init_month=("init_month", inits))
            rpss_all.attrs.update({
                "model": "MME", "var": var,
                "period": f"{args.start_year}-{args.end_year}",
                "models_included": ",".join(mme_source_models),
                "long_name": "Ranked Probability Skill Score",
                "valid_range": "-inf to 1 (positive = skilful)",
                "season_def": f"{N_SEASON}-month seasonal means",
            })
            ds_out = rpss_all.to_dataset(name="rpss")
            ds_out.to_netcdf(out_path_mme,
                             encoding={"rpss": {"zlib": True, "complevel": 1}})
            print(f"[SAVED] {out_path_mme.name}")
        else:
            print("[SKIP] MME: no data")

    print("\n[DONE]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

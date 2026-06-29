#!/usr/bin/env python3
"""
Compute ACC (Anomaly Correlation Coefficient) for NMME hindcast ensemble means.

For each model, variable, and season start lead:
  - For every init_month (1..12): load the 3-month seasonal mean hindcast anomaly
    (average of monthly anomalies at leads season_start_lead, +1, +2), take the
    ensemble mean, load the corresponding 3-month observed seasonal mean, and
    compute the Pearson correlation across hindcast years.

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
warnings.filterwarnings("ignore", message="Degrees of freedom <= 0 for slice")

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
# Shared helpers (verbatim from compute_rpss.py)
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
    # Ensure lat/lon are float64 so xarray doesn't create outer-product broadcasts
    # when comparing arrays from files that store coords as float32
    for dim in ("lat", "lon"):
        if dim in da.dims:
            da = da.assign_coords({dim: da[dim].values.astype(float)})
    return da


def _hindcast_root(hindcast_root: Path, model: str, var: str, use_forecast: bool = False) -> Path:
    if use_forecast:
        return hindcast_root / model / "forecast" / var
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
    use_forecast: bool = False,
) -> tuple[xr.DataArray | None, list[int]]:
    root = _hindcast_root(hindcast_root, model, var, use_forecast)
    clim_path = clim_root / f"{model}.{var}_{lev}.clim.1991-2020.nc"

    if not clim_path.exists():
        return None, []

    clim = xr.open_dataset(clim_path)[var]
    # Normalize lat/lon dim names (some clim files use latitude/longitude)
    _clim_rmap = {}
    if "latitude" in clim.dims and "lat" not in clim.dims:
        _clim_rmap["latitude"] = "lat"
    if "longitude" in clim.dims and "lon" not in clim.dims:
        _clim_rmap["longitude"] = "lon"
    if _clim_rmap:
        clim = clim.rename(_clim_rmap)
    if not pd.api.types.is_integer_dtype(clim["lead"].dtype):
        clim = clim.assign_coords(lead=clim["lead"].astype(int))
    for dim in ("lat", "lon"):
        if dim in clim.dims:
            clim = clim.assign_coords({dim: clim[dim].values.astype(float)})

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
            ds = xr.open_dataset(fp, decode_times=False, chunks={"lat": 30, "lon": 60})
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
        yearly_anoms.append(anom.load())  # compute in spatial chunks, then free file
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
    use_forecast: bool = False,
) -> tuple[xr.DataArray | None, list[int]]:
    """
    Average N_SEASON monthly anomalies starting at season_start_lead.
    Returns (DataArray(year, member, lat, lon), years) or (None, []).
    """
    per_lead: dict[int, tuple[xr.DataArray, list[int]]] = {}
    for lead in range(season_start_lead, season_start_lead + N_SEASON):
        anoms, years = load_hindcast_single_lead_anom(
            hindcast_root, clim_root, model, var, lev,
            init_month, lead, start_year, end_year, use_forecast,
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
# ACC computation
# ---------------------------------------------------------------------------

def compute_acc(
    anoms: xr.DataArray,
    obs_da: xr.DataArray,
    init_month: int,
    season_start_lead: int,
    start_year: int,
    end_year: int,
    ref_lat: np.ndarray,
    ref_lon: np.ndarray,
) -> xr.DataArray | None:
    """
    Pearson correlation between ensemble-mean hindcast anomaly and obs anomaly
    across years at each grid point.  Returns DataArray(lat, lon) or None.
    """
    obs_seasonal, obs_init_years = _load_seasonal_obs(
        obs_da, init_month, season_start_lead, start_year, end_year,
    )
    if obs_seasonal is None:
        return None

    common = sorted(set(anoms["year"].values.tolist()) & set(obs_init_years))
    if len(common) < 1:
        return None

    hc_emean = anoms.sel(year=common).mean("member")   # (year, lat, lon)

    obs_interp = obs_seasonal.sel(year=common).interp(
        lat=ref_lat, lon=ref_lon,
        method="linear", kwargs={"fill_value": None},
    )

    obs_anom = obs_interp - obs_interp.mean("year")
    hc_anom  = hc_emean  - hc_emean.mean("year")

    return xr.corr(hc_anom, obs_anom, dim="year").rename("acc")


# ---------------------------------------------------------------------------
# Per-model ACC computation (init_month × season_lead)
# ---------------------------------------------------------------------------

def _acc_for_model(
    model: str,
    var: str,
    lev: str,
    hindcast_root: Path,
    clim_root: Path,
    obs_da: xr.DataArray,
    max_lead: int,
    start_year: int,
    end_year: int,
    use_forecast: bool = False,
) -> tuple[xr.DataArray | None, list[int]]:
    ref_lat: np.ndarray | None = None
    ref_lon: np.ndarray | None = None

    acc_by_init: dict[int, xr.DataArray] = {}
    all_years: set[int] = set()

    for init_month in range(1, 13):
        acc_by_season: dict[int, xr.DataArray] = {}

        for season_start_lead in range(1, max_lead - N_SEASON + 2):
            anoms, years = load_hindcast_seasonal_anom(
                hindcast_root, clim_root, model, var, lev,
                init_month, season_start_lead, start_year, end_year, use_forecast,
            )
            if anoms is None or anoms.sizes["year"] < 1:
                continue

            all_years.update(years)

            if ref_lat is None:
                ref_lat = anoms["lat"].values
                ref_lon = anoms["lon"].values

            acc_cell = compute_acc(
                anoms, obs_da, init_month, season_start_lead,
                start_year, end_year, ref_lat, ref_lon,
            )
            if acc_cell is not None:
                acc_by_season[season_start_lead] = acc_cell
                print(f"  init={init_month:02d}  season={_season_label(init_month, season_start_lead)}"
                      f"  n_years={anoms.sizes['year']}")

        if acc_by_season:
            s_leads = sorted(acc_by_season.keys())
            acc_init = xr.concat([acc_by_season[l] for l in s_leads], dim="season_lead")
            acc_init = acc_init.assign_coords(season_lead=("season_lead", s_leads))
            acc_by_init[init_month] = acc_init

    if not acc_by_init:
        return None, []

    inits   = sorted(acc_by_init.keys())
    acc_all = xr.concat([acc_by_init[i] for i in inits], dim="init_month")
    acc_all = acc_all.assign_coords(init_month=("init_month", inits))
    return acc_all, sorted(all_years)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute seasonal ACC skill for NMME hindcasts (3-month seasons)."
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
    p.add_argument("--forecast",   action="store_true",
                   help="Use operational forecast data (forecast/) instead of hindcast/reforecast")
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

        out_path = outdir / f"{model}.{var}.acc.{args.start_year}-{args.end_year}.nc"
        if out_path.exists() and not args.overwrite:
            print(f"[SKIP] {out_path.name}")
            continue

        acc_all, actual_years = _acc_for_model(
            model, var, lev,
            hindcast_root, clim_root, obs_da,
            args.max_lead, args.start_year, args.end_year, args.forecast,
        )
        if acc_all is None:
            print(f"[SKIP] No data for {model}")
            continue

        acc_all.attrs.update({
            "model": model, "var": var,
            "period": f"{args.start_year}-{args.end_year}",
            "actual_start_year": actual_years[0] if actual_years else args.start_year,
            "actual_end_year": actual_years[-1] if actual_years else args.end_year,
            "n_years": len(set(actual_years)),
            "long_name": "Anomaly Correlation Coefficient",
            "valid_range": "-1 to 1 (positive = skilful)",
            "season_def": f"{N_SEASON}-month seasonal means",
        })
        ds_out = acc_all.to_dataset(name="acc")
        ds_out.to_netcdf(out_path, encoding={"acc": {"zlib": True, "complevel": 1}})
        print(f"[SAVED] {out_path.name}")

    # ----------------------------------------------------------------
    # MME: average ensemble means across models, then correlate
    # ----------------------------------------------------------------
    if not run_mme:
        print("\n[DONE]")
        return 0

    print(f"\n{'='*60}")
    print(f"[MODEL] MME  var={var}")
    print(f"{'='*60}")

    out_path_mme = outdir / f"MME.{var}.acc.{args.start_year}-{args.end_year}.nc"
    if out_path_mme.exists() and not args.overwrite:
        print(f"[SKIP] {out_path_mme.name}")
    else:
        ref_lat = ref_lon = None
        acc_by_init_mme: dict[int, xr.DataArray] = {}
        mme_all_years: set[int] = set()

        for init_month in range(1, 13):
            acc_by_season_mme: dict[int, xr.DataArray] = {}

            for season_start_lead in range(1, args.max_lead - N_SEASON + 2):
                model_emeans: list[xr.DataArray] = []
                common_years_all: set[int] | None = None

                for model in mme_source_models:
                    anoms, fcst_years = load_hindcast_seasonal_anom(
                        hindcast_root, clim_root, model, var, lev,
                        init_month, season_start_lead,
                        args.start_year, args.end_year, args.forecast,
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
                    model_emeans.append(anoms.mean("member"))

                if not model_emeans or not common_years_all or len(common_years_all) < 1:
                    continue

                common_years = sorted(common_years_all)
                mme_all_years.update(common_years)

                # Average ensemble means across models → one combined mean
                mme_emean = xr.concat(
                    [e.sel(year=common_years) for e in model_emeans], dim="_m"
                ).mean("_m")

                # Load obs and compute correlation
                obs_seasonal, obs_init_years = _load_seasonal_obs(
                    obs_da, init_month, season_start_lead,
                    args.start_year, args.end_year,
                )
                if obs_seasonal is None:
                    continue

                common2 = sorted(set(common_years) & set(obs_init_years))
                if len(common2) < 1:
                    continue

                obs_interp = obs_seasonal.sel(year=common2).interp(
                    lat=ref_lat, lon=ref_lon,
                    method="linear", kwargs={"fill_value": None},
                )
                obs_anom  = obs_interp - obs_interp.mean("year")
                hc_anom   = mme_emean.sel(year=common2) - mme_emean.sel(year=common2).mean("year")
                acc_cell  = xr.corr(hc_anom, obs_anom, dim="year").rename("acc")

                acc_by_season_mme[season_start_lead] = acc_cell
                print(f"  init={init_month:02d}  season={_season_label(init_month, season_start_lead)}"
                      f"  n_years={len(common2)}")

            if acc_by_season_mme:
                s_leads = sorted(acc_by_season_mme.keys())
                acc_init = xr.concat([acc_by_season_mme[l] for l in s_leads], dim="season_lead")
                acc_init = acc_init.assign_coords(season_lead=("season_lead", s_leads))
                acc_by_init_mme[init_month] = acc_init

        if acc_by_init_mme:
            inits   = sorted(acc_by_init_mme.keys())
            acc_all = xr.concat([acc_by_init_mme[i] for i in inits], dim="init_month")
            acc_all = acc_all.assign_coords(init_month=("init_month", inits))
            acc_all.attrs.update({
                "model": "MME", "var": var,
                "period": f"{args.start_year}-{args.end_year}",
                "actual_start_year": min(mme_all_years) if mme_all_years else args.start_year,
                "actual_end_year": max(mme_all_years) if mme_all_years else args.end_year,
                "n_years": len(mme_all_years),
                "models_included": ",".join(mme_source_models),
                "long_name": "Anomaly Correlation Coefficient",
                "valid_range": "-1 to 1 (positive = skilful)",
                "season_def": f"{N_SEASON}-month seasonal means",
            })
            ds_out = acc_all.to_dataset(name="acc")
            ds_out.to_netcdf(out_path_mme,
                             encoding={"acc": {"zlib": True, "complevel": 1}})
            print(f"[SAVED] {out_path_mme.name}")
        else:
            print("[SKIP] MME: no data")

    print("\n[DONE]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

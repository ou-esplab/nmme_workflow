#!/usr/bin/env python3
"""
Compute RPSS (Ranked Probability Skill Score) for NMME hindcast tercile forecasts.

For each model, variable, and lead offset (1..max_lead months ahead):
  - For every init_month (1..12): load the single-lead hindcast anomaly,
    load the corresponding observed calendar month, compute per-year RPS.
  - Pool all (init_month × year) pairs before computing the final RPSS.

Output: one NetCDF per (model, var) with dims (lead, lat, lon).

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

# All-NaN slices are expected at high-latitude / ocean gridpoints outside obs
# coverage (e.g. beyond CHIRPS ±50°N).  Suppress the per-quantile warning.
warnings.filterwarnings("ignore", message="All-NaN slice encountered")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.config import load_config
from utils.paths import ensure_dir

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VAR_LEV = {"prec": "sfc", "tref": "2m"}
SFS_MODELS = {"NOAA-SFS"}       # use reforecast/ path instead of hindcast/


# ---------------------------------------------------------------------------
# Shared helpers (unchanged from prior version)
# ---------------------------------------------------------------------------

def _to_0360(da: xr.DataArray) -> xr.DataArray:
    lon_name = "lon" if "lon" in da.dims else "longitude"
    if da[lon_name].min() < 0:
        da = da.assign_coords({lon_name: (da[lon_name] + 360) % 360}).sortby(lon_name)
    return da


def _normalize_hindcast_dims(da: xr.DataArray) -> xr.DataArray:
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
    # Convert float leads (0.5, 1.5, …) to integers so sel() works
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


def _obs_monthly_da(var: str, obs_precip: str, obs_tref: str) -> xr.DataArray:
    if var == "prec":
        ds = _decode_obs_time(xr.open_dataset(obs_precip))
        da = ds["precip"].where(ds["precip"] > -9000)
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
# Hindcast loader — single lead (new)
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
    """
    Load single-lead anomaly for all available hindcast years.

    Returns
    -------
    all_anoms : DataArray (year, member, lat, lon), or None
    years     : list of init years successfully loaded
    """
    root = _hindcast_root(hindcast_root, model, var)
    clim_path = clim_root / f"{model}.{var}_{lev}.clim.1991-2020.nc"

    if not clim_path.exists():
        return None, []

    clim = xr.open_dataset(clim_path)[var]
    if not pd.api.types.is_integer_dtype(clim["lead"].dtype):
        clim = clim.assign_coords(lead=clim["lead"].astype(int))

    if lead not in clim["lead"].values:
        return None, []

    valid_month = ((init_month - 1 + lead) % 12) + 1
    if "month" in clim.dims and valid_month not in clim["month"].values:
        return None, []

    clim_sel = clim.sel(month=valid_month, lead=lead) if "month" in clim.dims \
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
        yearly_anoms.append(anom.load())
        years_found.append(year)
        ds.close()

    if not yearly_anoms:
        return None, []

    all_anoms = xr.concat(yearly_anoms, dim="year")
    all_anoms = all_anoms.assign_coords(year=("year", years_found))
    return all_anoms, years_found


# ---------------------------------------------------------------------------
# Obs loader — single calendar month (new)
# ---------------------------------------------------------------------------

def obs_single_month_means(
    obs_da: xr.DataArray,
    valid_month: int,
    start_year: int,
    end_year: int,
) -> tuple[xr.DataArray | None, list[int]]:
    """
    Select all occurrences of valid_month across start_year..end_year.

    Returns
    -------
    result : DataArray (year, lat, lon) where year = the calendar year of the month
    years  : list of years successfully loaded
    """
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
# Forecast tercile probabilities (unchanged)
# ---------------------------------------------------------------------------

def compute_forecast_probs(
    all_anoms: xr.DataArray,
) -> tuple[xr.DataArray, xr.DataArray, xr.DataArray]:
    """Derive pooled thresholds and return P(BN), P(NN), P(AN) per year."""
    pooled = all_anoms.stack(pool=("year", "member"))
    t33 = pooled.quantile(1 / 3, dim="pool").drop_vars("quantile", errors="ignore")
    t66 = pooled.quantile(2 / 3, dim="pool").drop_vars("quantile", errors="ignore")
    p_bn = (all_anoms < t33).mean("member").astype(float)
    p_an = (all_anoms > t66).mean("member").astype(float)
    p_nn = (1.0 - p_bn - p_an).clip(0.0, 1.0)
    return p_bn, p_nn, p_an


# ---------------------------------------------------------------------------
# RPS / RPSS (refactored)
# ---------------------------------------------------------------------------

def _rps_per_year(
    p_bn: xr.DataArray,
    p_nn: xr.DataArray,
    obs_cat: xr.DataArray,
) -> tuple[xr.DataArray, xr.DataArray]:
    """
    Per-sample RPS and RPS_clim.  Leading dimension is 'year'.
    NaN where obs_cat is NaN (no obs data at that gridpoint).
    """
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
    """RPSS over the 'year' dimension."""
    rps, rps_clim = _rps_per_year(p_bn, p_nn, obs_cat)
    return (1.0 - rps.mean("year") / rps_clim.mean("year")).rename("rpss")


# ---------------------------------------------------------------------------
# Obs classification helper
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
# Per-(init_month, lead) RPSS helpers
# ---------------------------------------------------------------------------

def _rpss_one_cell(
    anoms: xr.DataArray,
    obs_da: xr.DataArray,
    init_month: int,
    lead: int,
    start_year: int,
    end_year: int,
    ref_lat: np.ndarray,
    ref_lon: np.ndarray,
    obs_thresh_cache: dict,
) -> xr.DataArray | None:
    """
    Compute RPSS(lat, lon) for one (init_month, lead) cell.
    Returns None if insufficient data.
    """
    valid_month    = ((init_month - 1 + lead) % 12) + 1
    valid_yr_off   = (init_month - 1 + lead) // 12

    p_bn, p_nn, _ = compute_forecast_probs(anoms)
    fcst_years     = anoms["year"].values.tolist()

    obs_start = start_year + valid_yr_off
    obs_end   = end_year   + valid_yr_off
    obs_month, obs_valid_years = obs_single_month_means(
        obs_da, valid_month, obs_start, obs_end,
    )
    if obs_month is None:
        return None

    obs_init_years = [y - valid_yr_off for y in obs_valid_years]
    obs_by_init    = obs_month.assign_coords(year=("year", obs_init_years))

    common = sorted(set(fcst_years) & set(obs_init_years))
    if len(common) < 5:
        return None

    p_bn_c = p_bn.sel(year=common)
    p_nn_c = p_nn.sel(year=common)
    obs_c  = obs_by_init.sel(year=common)
    obs_interp = obs_c.interp(lat=ref_lat, lon=ref_lon,
                               method="linear", kwargs={"fill_value": None})

    if valid_month not in obs_thresh_cache:
        obs_full, _ = obs_single_month_means(obs_da, valid_month, obs_start, obs_end)
        if obs_full is None:
            return None
        obs_full_i = obs_full.interp(lat=ref_lat, lon=ref_lon,
                                      method="linear", kwargs={"fill_value": None})
        t33 = obs_full_i.quantile(1/3, dim="year").drop_vars("quantile", errors="ignore")
        t66 = obs_full_i.quantile(2/3, dim="year").drop_vars("quantile", errors="ignore")
        obs_thresh_cache[valid_month] = (t33, t66)

    t33, t66 = obs_thresh_cache[valid_month]
    obs_cat  = _classify_obs(obs_interp, t33, t66)
    return compute_rpss(p_bn_c, p_nn_c, obs_cat)


# ---------------------------------------------------------------------------
# Per-model RPSS computation (init_month × lead)
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
    """
    Compute RPSS(init_month, lead, lat, lon) for one model.
    Each (init_month, lead) cell is independent; years are the sample dim.
    Returns None if no data was found.
    """
    # Obs threshold cache keyed by valid_month; reset per model (grids differ).
    obs_thresh_cache: dict[int, tuple] = {}
    ref_lat: np.ndarray | None = None
    ref_lon: np.ndarray | None = None

    rpss_by_init: dict[int, xr.DataArray] = {}

    for init_month in range(1, 13):
        rpss_by_lead: dict[int, xr.DataArray] = {}

        for lead in range(1, max_lead + 1):
            anoms, _ = load_hindcast_single_lead_anom(
                hindcast_root, clim_root, model, var, lev,
                init_month, lead, start_year, end_year,
            )
            if anoms is None or anoms.sizes["year"] < 5:
                continue

            if ref_lat is None:
                ref_lat = anoms["lat"].values
                ref_lon = anoms["lon"].values

            rpss_cell = _rpss_one_cell(
                anoms, obs_da, init_month, lead,
                start_year, end_year,
                ref_lat, ref_lon, obs_thresh_cache,
            )
            if rpss_cell is not None:
                rpss_by_lead[lead] = rpss_cell
                print(f"  init={init_month:02d}  lead={lead}  "
                      f"n_years={anoms.sizes['year']}")

        if rpss_by_lead:
            leads = sorted(rpss_by_lead.keys())
            rpss_init = xr.concat([rpss_by_lead[l] for l in leads], dim="lead")
            rpss_init = rpss_init.assign_coords(lead=("lead", leads))
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
        description="Compute lead-based RPSS skill for NMME hindcasts."
    )
    p.add_argument("--config", default="confignmme.yaml")
    p.add_argument("--var", required=True, choices=["prec", "tref"])
    p.add_argument("--hindcast-root", required=True)
    p.add_argument("--clim-root",     required=True)
    p.add_argument("--obs-precip", default=None)
    p.add_argument("--obs-tref",   default=None)
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
        obs_da = _obs_monthly_da(var, args.obs_precip, args.obs_precip)
    else:
        if not args.obs_tref:
            print("[ERROR] --obs-tref required for var=tref")
            return 1
        obs_da = _obs_monthly_da(var, args.obs_tref, args.obs_tref)

    all_models = cfg["models"]
    models = all_models if args.models == "ALL" \
        else [m for m in args.models.split(",") if m in all_models]

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
        })
        ds_out = rpss_all.to_dataset(name="rpss")
        ds_out.to_netcdf(out_path, encoding={"rpss": {"zlib": True, "complevel": 1}})
        print(f"[SAVED] {out_path.name}")

    # ----------------------------------------------------------------
    # MME: pool members across all models per (init_month, lead)
    # ----------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"[MODEL] MME  var={var}")
    print(f"{'='*60}")

    out_path_mme = outdir / f"MME.{var}.rpss.{args.start_year}-{args.end_year}.nc"
    if out_path_mme.exists() and not args.overwrite:
        print(f"[SKIP] {out_path_mme.name}")
    else:
        obs_thresh_cache_mme: dict[int, tuple] = {}
        ref_lat = ref_lon = None
        rpss_by_init_mme: dict[int, xr.DataArray] = {}

        for init_month in range(1, 13):
            rpss_by_lead_mme: dict[int, xr.DataArray] = {}

            for lead in range(1, args.max_lead + 1):
                valid_month     = ((init_month - 1 + lead) % 12) + 1
                valid_yr_offset = (init_month - 1 + lead) // 12

                # Collect per-model anomaly arrays for this (init_month, lead)
                model_anoms: list[xr.DataArray] = []
                common_years_all: set[int] | None = None

                for model in models:
                    anoms, fcst_years = load_hindcast_single_lead_anom(
                        hindcast_root, clim_root, model, var, lev,
                        init_month, lead, args.start_year, args.end_year,
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

                common_years = sorted(common_years_all)
                mme_pieces   = [a.sel(year=common_years) for a in model_anoms]
                mme_anoms    = xr.concat(mme_pieces, dim="member")

                rpss_cell = _rpss_one_cell(
                    mme_anoms, obs_da, init_month, lead,
                    args.start_year, args.end_year,
                    ref_lat, ref_lon, obs_thresh_cache_mme,
                )
                if rpss_cell is not None:
                    rpss_by_lead_mme[lead] = rpss_cell
                    print(f"  init={init_month:02d}  lead={lead}  "
                          f"n_years={len(common_years)}")

            if rpss_by_lead_mme:
                leads = sorted(rpss_by_lead_mme.keys())
                rpss_init = xr.concat([rpss_by_lead_mme[l] for l in leads], dim="lead")
                rpss_init = rpss_init.assign_coords(lead=("lead", leads))
                rpss_by_init_mme[init_month] = rpss_init

        if rpss_by_init_mme:
            inits    = sorted(rpss_by_init_mme.keys())
            rpss_all = xr.concat([rpss_by_init_mme[i] for i in inits], dim="init_month")
            rpss_all = rpss_all.assign_coords(init_month=("init_month", inits))
            rpss_all.attrs.update({
                "model": "MME", "var": var,
                "period": f"{args.start_year}-{args.end_year}",
                "models_included": ",".join(models),
                "long_name": "Ranked Probability Skill Score",
                "valid_range": "-inf to 1 (positive = skilful)",
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

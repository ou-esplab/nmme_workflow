#!/usr/bin/env python3
"""Build tercile probability products and maps for configured regions.

This script:
  1) loads monthly NMME forecast anomaly fields produced by makefcsts,
  2) computes model-agreement tercile probabilities (BN/NN/AN) against
     hindcast tercile thresholds,
  3) writes one NetCDF tercile-probability file per region-season-variable,
  4) writes the original 3-panel tercile probability map,
  5) writes additional plot products:
       - hindcast threshold maps (T33/T66) for prec and tref
       - most-likely tercile map for prec and tref
       - CPT-style dominant-category map for prec and tref
       - precip-only seasonal-total summary (mean/T33/T66)

Season lead windows are determined dynamically from the initialization month.
Assumes lead 0 corresponds to the initialization month.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Tuple

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature

# Ensure project root is in sys.path for module imports
sys.path.append(str(Path(__file__).resolve().parent.parent))

from utils.config import load_config


# Model names in forecast output -> hindcast directory names.
MODEL_DIR_MAP = {
    "NASA-GEOSS2S": "NASA-GEOSS2S",
    "CanESM5": "CanESM5",
    "GEM5.2-NEMO": "GEM5.2-NEMO",
    "NCEP-CFSv2": "NCEP-CFSv2",
    "NCAR-CESM1": "NCAR-CESM1",
    "COLA-RSMAS-CCSM4": "COLA-RSMAS-CCSM4",
    "COLA-RSMAS-CESM1": "COLA-RSMAS-CESM1",
    "NOAA-SFS": "NOAA-SFS",
}

FORECAST_VAR_TO_TERCILE_VAR = {
    "prec": "prec",
    "tref": "tref",
}

TERCILE_FORECAST_VARS = ("prec", "tref")

# Calendar-month definitions for target seasons.
# Lead windows are computed dynamically from init month.
# Seasons may span any number of consecutive months, including year boundaries.
SEASON_MONTHS: Dict[str, Tuple[int, ...]] = {
    # All 12 sliding 3-month seasons
    "JFM":     (1, 2, 3),
    "FMA":     (2, 3, 4),
    "MAM":     (3, 4, 5),
    "AMJ":     (4, 5, 6),
    "MJJ":     (5, 6, 7),
    "JJA":     (6, 7, 8),
    "JAS":     (7, 8, 9),
    "ASO":     (8, 9, 10),
    "SON":     (9, 10, 11),
    "OND":     (10, 11, 12),
    "NDJ":     (11, 12, 1),
    "DJF":     (12, 1, 2),
    # Extended seasons
    "Apr-Jul": (4, 5, 6, 7),
    "Apr-Sep": (4, 5, 6, 7, 8, 9),
    "Oct-Jan": (10, 11, 12, 1),
}

# Alias used by precompute_tercile_thresholds.py and runners/cli.py.
SEASON_LEADS = SEASON_MONTHS

VAR_META = {
    "prec": {
        "label": "Precipitation",
        "unit": "mm/day",
        "seasonal_total_unit": "mm/season",
        "threshold_cmaps": ("Blues", "YlOrRd"),
    },
    "tref": {
        "label": "2-m Temperature",
        "unit": "°C",
        "threshold_cmaps": ("Blues", "Reds"),
    },
}


def _to_percent_if_fraction(da: xr.DataArray, label: str) -> xr.DataArray:
    """Normalize probability units to percent if values are in 0..1 fraction space."""
    finite = da.where(np.isfinite(da), drop=True)
    if finite.size == 0:
        return da

    vmin = float(finite.min())
    vmax = float(finite.max())
    if -1e-6 <= vmin and vmax <= 1.000001:
        print(f"[INFO] {label}: detected fractional probabilities (0..1); converting to percent")
        return da * 100.0

    return da


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create tercile probability products for all regions")
    p.add_argument("--init", required=True, help="Forecast init YYYYMM, e.g., 202603")
    p.add_argument("--config", default="confignmme.yaml", help="Path to config YAML")
    p.add_argument(
        "--seasons",
        default="ALL",
        help=(
            "Comma-separated seasons from "
            "{MAM,AMJ,MJJ,JJA,ASO,NDJ,DJF}; "
            "use ALL for all"
        ),
    )
    p.add_argument(
        "--outdir",
        default=None,
        help=(
            "Optional image output directory; defaults to "
            "/data/.../forecast/seasonal/<init>/images"
        ),
    )
    p.add_argument(
        "--regions",
        default="ALL",
        help="Comma-separated region names from config; use ALL for all regions",
    )
    return p.parse_args()


def get_season_leads(init_yyyymm: str, season: str) -> Tuple[int, int]:
    """
    Return (l0, l1) where l1 is exclusive, based on initialization month
    and target season calendar months.

    Assumes lead 0 corresponds to the initialization month.
    Example:
        init=202603, season=AMJ -> (1, 4)
        init=202604, season=AMJ -> (0, 3)
    """
    if season not in SEASON_MONTHS:
        raise ValueError(f"Unsupported season: {season}. Allowed: {list(SEASON_MONTHS)}")

    init_month = int(init_yyyymm[4:])
    target_months = tuple(SEASON_MONTHS[season])
    n = len(target_months)

    # Search up to 24 months forward to allow next-year matches.
    for l0 in range(0, 24):
        candidate = tuple(
            ((init_month - 1 + l0 + k) % 12) + 1
            for k in range(n)
        )
        if candidate == target_months:
            return l0, l0 + n

    raise RuntimeError(
        f"Could not determine lead window for init={init_yyyymm}, season={season}"
    )


def to_0360(da: xr.DataArray) -> xr.DataArray:
    return da.assign_coords(lon=((da.lon + 360) % 360)).sortby("lon")


def subset_region(
    da: xr.DataArray,
    lat_bounds: Tuple[float, float],
    lon_bounds: Tuple[float, float],
) -> xr.DataArray:
    lat0, lat1 = lat_bounds
    lon0, lon1 = lon_bounds
    lon0 = (lon0 + 360) % 360
    lon1 = (lon1 + 360) % 360

    lat_slice = slice(min(lat0, lat1), max(lat0, lat1))

    if lon0 <= lon1:
        return da.sel(lat=lat_slice, lon=slice(lon0, lon1))

    # Dateline wrap case
    left = da.sel(lat=lat_slice, lon=slice(lon0, 360))
    right = da.sel(lat=lat_slice, lon=slice(0, lon1))
    return xr.concat([left, right], dim="lon")


def normalize_nmme_dims(da: xr.DataArray, squeeze_init: bool = False) -> xr.DataArray:
    """Normalize common NMME raw-file dimension names to init/ens/lead/lat/lon."""
    rename_map = {}
    if "S" in da.dims:
        rename_map["S"] = "init"
    if "M" in da.dims:
        rename_map["M"] = "ens"
    if "L" in da.dims:
        rename_map["L"] = "lead"
    if "Y" in da.dims:
        rename_map["Y"] = "lat"
    if "X" in da.dims:
        rename_map["X"] = "lon"

    da = da.rename(rename_map)

    if squeeze_init and "init" in da.dims and da.sizes["init"] == 1:
        da = da.squeeze("init")

    return da


def safe_contour_levels(da: xr.DataArray, n: int = 7) -> np.ndarray:
    vmin = float(da.min(skipna=True))
    vmax = float(da.max(skipna=True))

    if np.isnan(vmin) or np.isnan(vmax):
        return np.linspace(0.0, 1.0, n)

    if np.isclose(vmin, vmax):
        return np.linspace(vmin - 0.1, vmax + 0.1, n)

    return np.linspace(vmin, vmax, n)


def load_forecast(init_yyyymm: str, out_root: Path) -> Dict[str, xr.Dataset]:
    """Load makefcsts-produced anomaly ensemble-mean forecast files for configured vars."""
    def _load(var: str) -> xr.Dataset:
        file_var = FORECAST_VAR_TO_TERCILE_VAR.get(var, var)
        fpath = (
            out_root
            / init_yyyymm
            / "data"
            / "monthly"
            / f"NMME_fcst_{init_yyyymm}.anom.monthly.{file_var}.emean.nc"
        )
        if not fpath.exists():
            raise FileNotFoundError(f"Forecast file not found: {fpath}")
        ds = xr.open_dataset(fpath)
        # nmme_write stores monthly files with 'time' dim (valid dates); rename to
        # 'lead' so positional indexing in compute_region_probabilities works correctly.
        if "time" in ds.dims and "lead" not in ds.dims:
            ds = ds.rename({"time": "lead"})
        return ds

    return {var: _load(var) for var in TERCILE_FORECAST_VARS}



def hindcast_thresholds_for_model(
    model_name: str,
    season: str,
    lat_bounds: Tuple[float, float],
    lon_bounds: Tuple[float, float],
    hind_root: Path,
    sfs_hind_root: Path,
    var: str = "prec",
) -> Tuple[xr.DataArray, xr.DataArray]:
    """
    Load precomputed hindcast tercile threshold files and return regional T33/T66 maps.

    hind_root and sfs_hind_root are retained in the signature for compatibility
    with the existing workflow even though this function uses precomputed tercile
    files from tercile_dir.
    """
    tercile_dir = Path("/data/esplab/shared/model/initialized/nmme/terciles/1991-2020/")

    tercile_file = tercile_dir / f"{model_name}.{var}.{season}.terciles.1991-2020.nc"
    if not tercile_file.exists():
        raise FileNotFoundError(
            f"Tercile file not found for model={model_name}, season={season}, var={var}. "
            f"Expected: {tercile_file}"
        )

    ds = xr.open_dataset(tercile_file)
    t33 = ds["t33"]
    t66 = ds["t66"]

    # Remove any stray non-spatial dims.
    for dim in list(t33.dims):
        if dim not in ("lat", "lon"):
            t33 = t33.mean(dim=dim, skipna=True)
    for dim in list(t66.dims):
        if dim not in ("lat", "lon"):
            t66 = t66.mean(dim=dim, skipna=True)

    t33 = subset_region(t33, lat_bounds, lon_bounds)
    t66 = subset_region(t66, lat_bounds, lon_bounds)
    ds.close()

    return t33, t66


def compute_region_probabilities(
    ds_fc: xr.Dataset,
    init_yyyymm: str,
    forecast_var: str,
    season: str,
    lat_bounds: Tuple[float, float],
    lon_bounds: Tuple[float, float],
    hind_root: Path,
    sfs_hind_root: Path,
    model_names=None,
) -> Dict[str, xr.DataArray]:
    """
    Compute multimodel tercile probabilities from makefcsts-produced anomaly-mean
    forecast fields and precomputed hindcast tercile thresholds.
    """
    l0, l1 = get_season_leads(init_yyyymm, season)

    bn_model = []
    nn_model = []
    an_model = []

    # Authoritative target grid for tercile probabilities: 1-degree regional grid.
    # Only NOAA-SFS is interpolated; others are reindexed exactly.
    lat0, lat1 = sorted([float(lat_bounds[0]), float(lat_bounds[1])])
    lon0 = (float(lon_bounds[0]) + 360.0) % 360.0
    lon1 = (float(lon_bounds[1]) + 360.0) % 360.0

    # Round bounds to nearest integer degree so the target grid aligns with
    # model grids (which use integer lat/lon coordinates).  Non-integer bounds
    # (e.g. C.Asia lat=28.39) produce a target like [28.39, 29.39, ...] that
    # reindex cannot match to the model's [28, 29, ...] grid, yielding all NaN.
    lat0_r = round(lat0)
    lat1_r = round(lat1)
    lon0_r = round(lon0)
    lon1_r = round(lon1)

    target_lat = xr.DataArray(np.arange(lat0_r, lat1_r + 1e-6, 1.0), dims=("lat",), name="lat")
    if lon0_r <= lon1_r:
        target_lon_vals = np.arange(lon0_r, lon1_r + 1e-6, 1.0)
    else:
        left = np.arange(lon0_r, 360.0, 1.0)
        right = np.arange(0.0, lon1_r + 1e-6, 1.0)
        target_lon_vals = np.concatenate([left, right])
    target_lon = xr.DataArray(target_lon_vals, dims=("lon",), name="lon")

    tercile_var = FORECAST_VAR_TO_TERCILE_VAR[forecast_var]
    available_models = [name for name in ds_fc.data_vars if name != "MME"]
    candidate_models = list(model_names) if model_names is not None else available_models

    for model_name in candidate_models:
        if model_name not in ds_fc:
            print(f"[WARN] Forecast variable missing for {model_name}; skipping")
            continue

        try:
            t33, t66 = hindcast_thresholds_for_model(
                model_name,
                season,
                lat_bounds,
                lon_bounds,
                hind_root,
                sfs_hind_root,
                var=tercile_var,
            )
        except (FileNotFoundError, RuntimeError) as e:
            print(f"[WARN] Skipping {model_name}: {e}")
            continue

        da = ds_fc[model_name]
        if "lead" not in da.dims:
            raise ValueError(f"'lead' dimension not found in {model_name} data array (dims={da.dims})")

        fc = to_0360(da.isel(lead=slice(l0, l1)).mean("lead"))
        fc = subset_region(fc, lat_bounds, lon_bounds)

        # Trim NaN padding from union-grid concat before threshold comparison.
        fc = fc.dropna(dim="lat", how="all")
        fc = fc.dropna(dim="lon", how="all")
        if fc.sizes.get("lat", 0) == 0 or fc.sizes.get("lon", 0) == 0:
            print(f"[WARN] No regional data support after trim for {model_name}; skipping")
            continue

        if model_name == "NOAA-SFS":
            fc_t = fc.interp(lat=target_lat, lon=target_lon, method="linear")
            t33_t = t33.interp(lat=target_lat, lon=target_lon, method="linear")
            t66_t = t66.interp(lat=target_lat, lon=target_lon, method="linear")
        else:
            fc_t = fc.reindex(lat=target_lat.values, lon=target_lon.values)
            t33_t = t33.reindex(lat=target_lat.values, lon=target_lon.values)
            t66_t = t66.reindex(lat=target_lat.values, lon=target_lon.values)

        bn = (fc_t < t33_t).astype(float) * 100.0
        nn = ((fc_t >= t33_t) & (fc_t <= t66_t)).astype(float) * 100.0
        an = (fc_t > t66_t).astype(float) * 100.0

        bn_model.append(bn)
        nn_model.append(nn)
        an_model.append(an)

    if not bn_model:
        raise RuntimeError("No models were available to compute tercile probabilities")

    prob = {
        "BN": xr.concat(bn_model, dim="model", coords="minimal", compat="override").mean("model"),
        "NN": xr.concat(nn_model, dim="model", coords="minimal", compat="override").mean("model"),
        "AN": xr.concat(an_model, dim="model", coords="minimal", compat="override").mean("model"),
    }

    # Light smoothing for precip only.
    if forecast_var == "prec":
        prob = {
            k: v.rolling(lat=3, lon=3, center=True, min_periods=1).mean()
            for k, v in prob.items()
        }

    # Guard against regressions where probabilities are left in 0..1 space.
    prob = {k: _to_percent_if_fraction(v, f"{k}/{forecast_var}/{season}") for k, v in prob.items()}

    return prob


def compute_multimodel_thresholds_and_climo(
    forecast_var: str,
    season: str,
    init_yyyymm: str,
    lat_bounds: Tuple[float, float],
    lon_bounds: Tuple[float, float],
    hind_root: Path,
    sfs_hind_root: Path,
    model_names: Iterable[str],
    climo_dir: Path,
) -> Tuple[xr.DataArray, xr.DataArray, "xr.DataArray | None"]:
    """
    Compute multimodel-mean T33/T66 threshold maps from precomputed hindcast terciles,
    and (for prec only) the multimodel-mean climatological seasonal mean.

    Only models that have a climo file in climo_dir are included in any
    calculation, ensuring all products are computed from a consistent model set.

    Returns
    -------
    t33_mme, t66_mme : xr.DataArray
        Multimodel-mean anomaly thresholds (mm/day for prec, °C for tref).
    climo_mme : xr.DataArray or None
        Multimodel-mean climatological seasonal-mean daily rate (mm/day).
        None for variables other than prec.
    """
    tercile_var = FORECAST_VAR_TO_TERCILE_VAR[forecast_var]
    init_month = int(init_yyyymm[4:])
    l0, l1 = get_season_leads(init_yyyymm, season)

    t33_all: list = []
    t66_all: list = []
    climo_all: list = []

    for model_name in model_names:
        # Require a climo file for every model so all products are computed
        # from the same consistent set of models.
        climo_path = climo_dir / f"{model_name}.{tercile_var}.clim.1991-2020.nc"
        if not climo_path.exists():
            print(f"[INFO] Skipping {model_name}: no climo file ({climo_path.name})")
            continue

        try:
            t33, t66 = hindcast_thresholds_for_model(
                model_name,
                season,
                lat_bounds,
                lon_bounds,
                hind_root,
                sfs_hind_root,
                var=tercile_var,
            )
            t33_all.append(t33)
            t66_all.append(t66)

            # Load climatological seasonal mean for prec seasonal-total conversion.
            if forecast_var == "prec":
                ds_climo = xr.open_dataset(climo_path)
                # 'prec' is the variable name regardless of the level-suffix in the filename.
                climo_sea = (
                    ds_climo["prec"]
                    .sel(month=init_month)
                    .isel(lead=slice(l0, l1))
                    .mean("lead")
                )
                climo_sea = to_0360(climo_sea)
                climo_all.append(subset_region(climo_sea, lat_bounds, lon_bounds))
                ds_climo.close()

        except (FileNotFoundError, RuntimeError) as e:
            print(f"[WARN] Threshold map skipping {model_name}: {e}")

    if not t33_all:
        raise RuntimeError(
            f"No threshold maps available for var={forecast_var}, season={season}"
        )

    t33_mme = xr.concat(t33_all, dim="model", coords="minimal", compat="override").mean("model")
    t66_mme = xr.concat(t66_all, dim="model", coords="minimal", compat="override").mean("model")

    climo_mme = None
    if climo_all:
        climo_mme = xr.concat(climo_all, dim="model", coords="minimal", compat="override").mean("model")

    return t33_mme, t66_mme, climo_mme


def seasonal_total_from_thresholds(
    t33_anomaly: xr.DataArray,
    t66_anomaly: xr.DataArray,
    climo_mean: xr.DataArray,
    n_months: int,
    days_per_month: int = 30,
) -> Tuple[xr.DataArray, xr.DataArray, xr.DataArray]:
    """
    Convert anomaly thresholds (mm/day) + climatological mean (mm/day) to
    seasonal totals (mm/season).

    The anomaly T33/T66 thresholds represent the seasonal-mean daily rate at
    which the 33rd/66th percentile of hindcast years falls.  Adding the
    climatological mean converts from anomaly space to absolute space.
    Multiplying by (days_per_month × n_months) converts from daily rate to
    accumulated seasonal total.

    Returns
    -------
    mean_total : mean climatological seasonal total (mm/season)
    t33_total  : lower-tercile seasonal total (mm/season)
    t66_total  : upper-tercile seasonal total (mm/season)
    """
    scale = float(days_per_month * n_months)
    mean_total = climo_mean * scale
    t33_total = (t33_anomaly + climo_mean) * scale
    t66_total = (t66_anomaly + climo_mean) * scale
    return mean_total, t33_total, t66_total


def plot_probabilities(
    prob: Dict[str, xr.DataArray],
    region: str,
    season: str,
    init_yyyymm: str,
    lead_label: str,
    out_png: Path,
) -> None:
    """Original 3-panel BN/NN/AN tercile probability plot."""
    fig, axes = plt.subplots(
        1, 3, figsize=(18, 6),
        subplot_kw={"projection": ccrs.PlateCarree()}
    )
    # Finer bins reduce the chance of maps appearing uniformly colored when
    # probabilities occupy a narrow but valid range.
    levels = np.arange(0, 105, 5)

    panels = [
        ("BN", "Below Normal", "Blues"),
        ("NN", "Near Normal", "Greens"),
        ("AN", "Above Normal", "YlOrRd"),
    ]

    for ax, (key, title, cmap) in zip(axes, panels):
        da = _to_percent_if_fraction(prob[key], f"plot/{region}/{season}/{key}")
        ax.set_extent(
            [
                float(da.lon.min()),
                float(da.lon.max()),
                float(da.lat.min()),
                float(da.lat.max()),
            ],
            crs=ccrs.PlateCarree(),
        )
        m = ax.contourf(
            da["lon"],
            da["lat"],
            da,
            levels=levels,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            vmin=0,
            vmax=100,
            extend="neither",
        )
        fig.colorbar(m, ax=ax, label="Probability (%)", shrink=0.82, pad=0.03)
        ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, linewidth=0.6)
        ax.add_feature(cfeature.STATES, linewidth=0.4)
        ax.set_title(title)

    fig.suptitle(
        f"NMME {init_yyyymm} {season} Tercile Probabilities - {region} ({lead_label})",
        fontsize=14,
    )
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_threshold_maps(
    t33: xr.DataArray,
    t66: xr.DataArray,
    region: str,
    season: str,
    init_yyyymm: str,
    forecast_var: str,
    out_png: Path,
) -> None:
    """2-panel hindcast threshold map (T33/T66), for both prec and tref."""
    fig, axes = plt.subplots(
        1, 2, figsize=(14, 6),
        subplot_kw={"projection": ccrs.PlateCarree()}
    )

    meta = VAR_META[forecast_var]
    cmap_lo, cmap_hi = meta["threshold_cmaps"]

    fields = [
        (t33, "Lower Tercile Threshold (T33)", cmap_lo),
        (t66, "Upper Tercile Threshold (T66)", cmap_hi),
    ]

    for ax, (da, title, cmap) in zip(axes, fields):
        da.plot(
            ax=ax,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            cbar_kwargs=dict(label=f"{meta['label']} ({meta['unit']})"),
        )

        levels = safe_contour_levels(da, n=7)
        cs = ax.contour(
            da.lon,
            da.lat,
            da,
            levels=levels,
            colors="black",
            linewidths=0.8,
            transform=ccrs.PlateCarree(),
        )
        ax.clabel(cs, inline=True, fontsize=8, fmt="%.2f")

        ax.set_extent(
            [
                float(da.lon.min()),
                float(da.lon.max()),
                float(da.lat.min()),
                float(da.lat.max()),
            ],
            crs=ccrs.PlateCarree(),
        )
        ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, linewidth=0.6)
        ax.add_feature(cfeature.STATES, linewidth=0.4)
        ax.set_title(title)

    plt.suptitle(
        f"NMME {init_yyyymm} {season} Hindcast Tercile Thresholds - {region} ({forecast_var})",
        fontsize=14,
    )
    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_most_likely_from_prob(
    prob: Dict[str, xr.DataArray],
    region: str,
    season: str,
    init_yyyymm: str,
    forecast_var: str,
    out_png: Path,
    mask_threshold: float = 40.0,
) -> None:
    """Plot the most-likely tercile using existing BN/NN/AN probabilities."""
    stacked = xr.concat(
        [
            prob["BN"].reset_coords(drop=True),
            prob["NN"].reset_coords(drop=True),
            prob["AN"].reset_coords(drop=True),
        ],
        dim="tercile",
        coords="minimal",
    )

    most_likely = stacked.fillna(0.0).argmax(dim="tercile")
    prob_of_choice = stacked.max(dim="tercile")
    most_likely = most_likely.where(prob_of_choice > mask_threshold)

    fig, ax = plt.subplots(
        1, 1, figsize=(9, 7),
        subplot_kw={"projection": ccrs.PlateCarree()},
        constrained_layout=True,
    )

    cmap = ListedColormap([
        "#2166ac",  # Below Normal
        "#bdbdbd",  # Near Normal
        "#b2182b",  # Above Normal
    ])
    cmap.set_bad("white")

    im = most_likely.plot(
        ax=ax,
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        vmin=0,
        vmax=2,
        add_colorbar=True,
        cbar_kwargs=dict(
            ticks=[0, 1, 2],
            shrink=0.85,
            pad=0.03,
        ),
    )

    cbar = im.colorbar
    cbar.ax.set_yticklabels(["Below", "Near", "Above"])

    ref = prob["BN"]
    ax.set_extent(
        [
            float(ref.lon.min()),
            float(ref.lon.max()),
            float(ref.lat.min()),
            float(ref.lat.max()),
        ],
        crs=ccrs.PlateCarree(),
    )
    ax.add_feature(cfeature.COASTLINE, linewidth=0.9)
    ax.add_feature(cfeature.BORDERS, linewidth=0.7)
    ax.add_feature(cfeature.STATES, linewidth=0.4)

    fig.suptitle(
        f"NMME {init_yyyymm} {season} Most-Likely Tercile - {region}\n"
        f"{forecast_var}  |  Shown where probability > {mask_threshold:.0f}%",
        fontsize=12,
        weight="bold",
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _add_cpt_colorbars(fig, ax):
    """Add three CPT-style mini colorbars below the axis."""
    bbox = ax.get_position()
    y = bbox.y0 - 0.10
    h = 0.03
    gap = 0.02
    w = (bbox.width - 2 * gap) / 3

    bars = [
        ("BN (%)", plt.cm.Blues),
        ("NN (%)", plt.cm.Greens),
        ("AN (%)", plt.cm.YlOrRd),
    ]

    for i, (label, cmap) in enumerate(bars):
        cax = fig.add_axes([bbox.x0 + i * (w + gap), y, w, h])
        sm = mpl.cm.ScalarMappable(
            norm=mpl.colors.Normalize(vmin=40, vmax=100),
            cmap=cmap,
        )
        cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
        cb.set_label(label, fontsize=9)
        cb.set_ticks([40, 50, 60, 70, 80, 90, 100])
        cb.ax.tick_params(labelsize=8)


def plot_cpt_dominant_from_prob(
    prob: Dict[str, xr.DataArray],
    region: str,
    season: str,
    init_yyyymm: str,
    forecast_var: str,
    out_png: Path,
    mask_threshold: float = 40.0,
) -> None:
    """Plot a CPT-style dominant-category tercile map from existing BN/NN/AN probabilities."""
    prob_stack = xr.concat(
        [
            prob["BN"].reset_coords(drop=True),
            prob["NN"].reset_coords(drop=True),
            prob["AN"].reset_coords(drop=True),
        ],
        dim="category",
        coords="minimal",
    ) / 100.0

    prob_stack = prob_stack.assign_coords(category=["BN", "NN", "AN"])
    dominant_cat = prob_stack.fillna(0.0).argmax("category")
    dominant_val = prob_stack.max("category")
    mask = dominant_val >= (mask_threshold / 100.0)

    fig, ax = plt.subplots(
        figsize=(9, 6),
        subplot_kw=dict(projection=ccrs.PlateCarree()),
    )

    cmaps = {
        0: plt.cm.Blues,
        1: plt.cm.Greens,
        2: plt.cm.YlOrRd,
    }

    for idx, cmap in cmaps.items():
        field = prob_stack.isel(category=idx).where(mask & (dominant_cat == idx))
        field.plot(
            ax=ax,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            vmin=mask_threshold / 100.0,
            vmax=1.0,
            add_colorbar=False,
        )

    ref = prob["BN"]
    ax.set_extent(
        [
            float(ref.lon.min()),
            float(ref.lon.max()),
            float(ref.lat.min()),
            float(ref.lat.max()),
        ],
        crs=ccrs.PlateCarree(),
    )

    ax.set_title("")  # clear xarray's auto-title from the last isel plot call
    ax.add_feature(cfeature.COASTLINE, linewidth=0.9)
    ax.add_feature(cfeature.BORDERS, linewidth=0.7)
    ax.add_feature(cfeature.STATES, linewidth=0.4)

    fig.suptitle(
        f"NMME {init_yyyymm} {season} Dominant Tercile Probability - {region}\n"
        f"{forecast_var}",
        fontsize=12,
        weight="bold",
    )

    _add_cpt_colorbars(fig, ax)
    plt.subplots_adjust(top=0.88, bottom=0.18)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_precip_seasonal_total_summary(
    mean_field: xr.DataArray,
    t33: xr.DataArray,
    t66: xr.DataArray,
    region: str,
    season: str,
    init_yyyymm: str,
    out_png: Path,
) -> None:
    """Precip-only 3-panel seasonal-total summary (mean, T33, T66)."""
    fig, axes = plt.subplots(
        1, 3, figsize=(20, 6),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )

    fields = [
        (mean_field, "Mean Seasonal Total Precipitation", "Greens"),
        (t33, "Lower Tercile (Dry Season Total)", "Blues"),
        (t66, "Upper Tercile (Wet Season Total)", "Reds"),
    ]

    for ax, (da, title, cmap) in zip(axes, fields):
        da.plot(
            ax=ax,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            cbar_kwargs=dict(label="Seasonal Total Precipitation (mm/season)"),
        )

        levels = safe_contour_levels(da, n=7)
        cs = ax.contour(
            da.lon,
            da.lat,
            da,
            levels=levels,
            colors="black",
            linewidths=0.8,
            transform=ccrs.PlateCarree(),
        )
        ax.clabel(cs, fontsize=8, fmt="%.1f")

        ax.set_extent(
            [
                float(da.lon.min()),
                float(da.lon.max()),
                float(da.lat.min()),
                float(da.lat.max()),
            ],
            crs=ccrs.PlateCarree(),
        )
        ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, linewidth=0.6)
        ax.add_feature(cfeature.STATES, linewidth=0.4)
        ax.set_title(title)

    plt.suptitle(
        f"NMME {init_yyyymm} {season} Seasonal Total Precipitation - {region}",
        fontsize=14,
    )
    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def build_target_label(init_yyyymm: str, season: str) -> str:
    """Build human-readable lead label from init month and dynamic lead window."""
    init_dt = datetime.strptime(init_yyyymm, "%Y%m")
    l0, l1 = get_season_leads(init_yyyymm, season)

    month_labels = []
    for lead in range(l0, l1):
        year = init_dt.year + ((init_dt.month - 1 + lead) // 12)
        month = ((init_dt.month - 1 + lead) % 12) + 1
        month_labels.append(datetime(year, month, 1).strftime("%b %Y"))

    return f"Lead L{l0}-L{l1 - 1}; Target: {' - '.join(month_labels)}"


def main() -> int:
    args = parse_args()

    cfg = load_config(args.config)
    forecast_root = Path(cfg["data"]["output"]["nmme_forecast"])
    hind_root = Path("/data/esplab/shared/model/initialized/nmme/hindcast/monthly/prec/monthly/full")
    sfs_hind_root = (
        Path(
            cfg.get("pipeline", {})
            .get("sfs", {})
            .get("climo_input_dir", "/data/esplab/nmme-backup/NOAA-SFS/reforecast")
        )
        / "prec"
    )
    climo_dir = Path(
        cfg.get("pipeline", {})
        .get("sfs", {})
        .get("climo_output_dir", "/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020")
    )

    # Parse --seasons CLI override (used later to override per-region config seasons).
    if args.seasons.upper() != "ALL":
        seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
        bad = [s for s in seasons if s not in SEASON_MONTHS]
        if bad:
            raise ValueError(f"Unsupported seasons: {bad}. Allowed: {list(SEASON_MONTHS)}")
    else:
        seasons = []  # sentinel: use per-region config seasons

    ds_fc_dict = load_forecast(args.init, forecast_root)
    regions = cfg.get("regions", [])
    configured_models = cfg.get("models", [])
    if not regions:
        raise ValueError("No regions defined in config")
    if not configured_models:
        raise ValueError("No models listed in config")

    if args.regions.upper() != "ALL":
        wanted = {r.strip() for r in args.regions.split(",") if r.strip()}
        regions = [r for r in regions if r.get("name") in wanted]
        if not regions:
            raise ValueError(f"No matching regions for --regions={args.regions}")

    # NetCDF outputs go to the forecast tercile_probs data directory.
    tercile_outdir = forecast_root / args.init / "data" / "tercile_probs"
    tercile_outdir.mkdir(parents=True, exist_ok=True)

    # Image outputs can be redirected with --outdir.
    image_root = Path(args.outdir) if args.outdir else (forecast_root / args.init / "images")
    image_root.mkdir(parents=True, exist_ok=True)

    for var, ds_fc in ds_fc_dict.items():
        for reg in regions:
            rname = reg["name"]
            lat_bounds = tuple(reg["lat"])
            lon_bounds = tuple(reg["lon"])

            # Per-region seasons from config; --seasons CLI flag overrides if provided.
            if args.seasons.upper() == "ALL":
                region_seasons = reg.get("seasons", list(SEASON_MONTHS.keys()))
            else:
                region_seasons = seasons  # already parsed from CLI above

            for season in region_seasons:
                print(f"[INFO] Computing {rname} {season} {var}")
                lead_label = build_target_label(args.init, season)

                # -------------------------------------------------------------
                # Core tercile probabilities (existing workflow)
                # -------------------------------------------------------------
                try:
                    prob = compute_region_probabilities(
                        ds_fc,
                        args.init,
                        var,
                        season,
                        lat_bounds,
                        lon_bounds,
                        hind_root,
                        sfs_hind_root,
                        model_names=configured_models,
                    )
                except RuntimeError as e:
                    print(f"[WARN] Skipping {rname} {season} {var}: {e}")
                    continue

                out_nc = tercile_outdir / f"NMME_{args.init}_{rname}_{season}_{var}_tercile_probs.nc"
                prob_out = {
                    "BN": prob["BN"].assign_attrs(long_name="Below Normal probability", units="%"),
                    "NN": prob["NN"].assign_attrs(long_name="Near Normal probability", units="%"),
                    "AN": prob["AN"].assign_attrs(long_name="Above Normal probability", units="%"),
                }
                ds_out = xr.Dataset(prob_out)
                ds_out.attrs = {
                    "title": f"NMME tercile probabilities {args.init}",
                    "region": rname,
                    "season": season,
                    "variable": var,
                    "source": "NMME MME",
                }
                ds_out.to_netcdf(out_nc)
                print(f"[INFO] Wrote {out_nc}")

                # -------------------------------------------------------------
                # Existing 3-panel BN/NN/AN map
                # -------------------------------------------------------------
                out_png = (
                    image_root
                    / "tercile_probs"
                    / rname
                    / f"NMME_{args.init}_{rname}_{season}_{var}_tercile_probs.png"
                )
                plot_probabilities(prob, rname, season, args.init, lead_label, out_png)
                print(f"[INFO] Wrote {out_png}")

                # -------------------------------------------------------------
                # Threshold maps (anomaly units, prec + tref) and, for prec,
                # seasonal-total summary (absolute units).
                # Both are derived in one pass from precomputed tercile files
                # + climatology files — no raw hindcast reading required.
                # -------------------------------------------------------------
                try:
                    t33_map, t66_map, climo_mme = compute_multimodel_thresholds_and_climo(
                        var,
                        season,
                        args.init,
                        lat_bounds,
                        lon_bounds,
                        hind_root,
                        sfs_hind_root,
                        model_names=configured_models,
                        climo_dir=climo_dir,
                    )

                    out_png = (
                        image_root
                        / "threshold_maps"
                        / rname
                        / f"NMME_{args.init}_{rname}_{season}_{var}_thresholds.png"
                    )
                    plot_threshold_maps(
                        t33_map,
                        t66_map,
                        rname,
                        season,
                        args.init,
                        var,
                        out_png,
                    )
                    print(f"[INFO] Wrote {out_png}")

                    # Seasonal-total summary: convert anomaly thresholds to
                    # absolute mm/season using the climatological mean.
                    if var == "prec" and climo_mme is not None:
                        l0, l1 = get_season_leads(args.init, season)
                        mean_total, t33_total, t66_total = seasonal_total_from_thresholds(
                            t33_map, t66_map, climo_mme, n_months=l1 - l0
                        )
                        out_png = (
                            image_root
                            / "seasonal_total_summary"
                            / rname
                            / f"NMME_{args.init}_{rname}_{season}_{var}_seasonal_total_summary.png"
                        )
                        plot_precip_seasonal_total_summary(
                            mean_total,
                            t33_total,
                            t66_total,
                            rname,
                            season,
                            args.init,
                            out_png,
                        )
                        print(f"[INFO] Wrote {out_png}")

                except Exception as e:
                    print(f"[WARN] Failed threshold/seasonal-total maps for {rname} {season} {var}: {e}")

                # -------------------------------------------------------------
                # Most-likely tercile map (prec + tref)
                # -------------------------------------------------------------
                try:
                    out_png = (
                        image_root
                        / "most_likely"
                        / rname
                        / f"NMME_{args.init}_{rname}_{season}_{var}_most_likely.png"
                    )
                    plot_most_likely_from_prob(
                        prob,
                        rname,
                        season,
                        args.init,
                        var,
                        out_png,
                    )
                    print(f"[INFO] Wrote {out_png}")
                except Exception as e:
                    print(f"[WARN] Failed most-likely plot for {rname} {season} {var}: {e}")

                # -------------------------------------------------------------
                # CPT-style dominant-category map (prec + tref)
                # -------------------------------------------------------------
                try:
                    out_png = (
                        image_root
                        / "cpt_dominant"
                        / rname
                        / f"NMME_{args.init}_{rname}_{season}_{var}_cpt_dominant.png"
                    )
                    plot_cpt_dominant_from_prob(
                        prob,
                        rname,
                        season,
                        args.init,
                        var,
                        out_png,
                    )
                    print(f"[INFO] Wrote {out_png}")
                except Exception as e:
                    print(f"[WARN] Failed CPT dominant plot for {rname} {season} {var}: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
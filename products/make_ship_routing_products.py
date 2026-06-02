#!/usr/bin/env python3
"""
Ship-routing forecast products: global vector maps of 10m winds and surface
ocean currents from NOAA-SFS ensemble mean (full field, not anomaly).

Outputs:
  {forecast_root}/{YYYYMM}/images/ship_routing/winds/10mWindsGlobalMonth{N}.png
  {forecast_root}/{YYYYMM}/images/ship_routing/currents/SfcCurrentsGlobalMonth{N}.png
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import yaml
import pandas as pd

KT_TO_MS = 0.514444
WIND_THRESHOLD_MS = 30 * KT_TO_MS  # 30 knots in m/s


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ship-routing vector forecast products")
    p.add_argument("--init", required=True, help="Forecast init date YYYYMM")
    p.add_argument("--config", default="confignmme.yaml", help="Config YAML path")
    p.add_argument("--dry-run", action="store_true", help="Print paths without writing")
    return p.parse_args()


def _coord(da: xr.DataArray, names: list[str]) -> np.ndarray:
    """Return the first matching coordinate array from a list of candidate names."""
    for n in names:
        if n in da.coords:
            return da[n].values
    raise KeyError(f"None of {names} found in {list(da.coords)}")


def load_ensemble_mean(preprocess_root: Path, init_yyyymm: str, var: str) -> xr.DataArray | None:
    """Load preprocessed NOAA-SFS variable and return ensemble mean (lead, lat, lon)."""
    yyyy, mm = init_yyyymm[:4], init_yyyymm[4:]
    fpath = (preprocess_root / init_yyyymm / "preprocess" / "NOAA-SFS" / "forecast" / var
             / f"{var}_NOAA-SFS_{yyyy}_{mm}.nc")
    if not fpath.exists():
        print(f"[WARN] Preprocessed file not found: {fpath}")
        return None
    da = xr.open_dataset(fpath)[var]
    if "member" in da.dims:
        da = da.mean("member", skipna=True)
    return da  # dims: (lead, lat/latitude, lon/longitude)


def _apply_ocean_mask(u: np.ndarray, v: np.ndarray,
                      lat: np.ndarray, lon: np.ndarray,
                      land_mask_path: str) -> tuple[np.ndarray, np.ndarray]:
    """Set u/v to NaN over land grid points."""
    if not land_mask_path:
        return u, v
    ds_mask = xr.open_dataset(land_mask_path)
    lm = ds_mask["land"]
    # Align mask to data grid
    lm_on_grid = lm.interp(lat=xr.DataArray(lat, dims="lat"),
                            lon=xr.DataArray(lon, dims="lon"),
                            method="nearest").values
    land = lm_on_grid > 0.5
    u = np.where(land, np.nan, u)
    v = np.where(land, np.nan, v)
    return u, v


def plot_vector_field(u_da: xr.DataArray, v_da: xr.DataArray,
                      ilead: int, title: str, outpath: Path,
                      land_mask_path: str = "",
                      wind_threshold_ms: float | None = None,
                      stride: int = 10) -> None:
    """
    Plot u/v as quiver arrows on a global Robinson projection, ocean only.
    If wind_threshold_ms is set, arrows at or above the threshold are shown in red.
    """
    u_full = u_da.isel(lead=ilead).values
    v_full = v_da.isel(lead=ilead).values
    lat = _coord(u_da, ["lat", "latitude"])
    lon = _coord(u_da, ["lon", "longitude"])

    # Apply ocean mask before downsampling
    u_full, v_full = _apply_ocean_mask(u_full, v_full, lat, lon, land_mask_path)

    # Downsample for arrow density
    u_ds  = u_full[::stride, ::stride]
    v_ds  = v_full[::stride, ::stride]
    lat_ds = lat[::stride]
    lon_ds = lon[::stride]
    lon2d, lat2d = np.meshgrid(lon_ds, lat_ds)

    speed_ds = np.sqrt(u_ds**2 + v_ds**2)
    speed_full = np.sqrt(u_full**2 + v_full**2)

    # Scale based on 95th percentile of ocean speeds
    ocean_speeds = speed_full[~np.isnan(speed_full)]
    vmax = float(np.percentile(ocean_speeds, 95)) if ocean_speeds.size > 0 else 1.0
    vmax = max(vmax, 1e-3)
    scale = vmax * 40

    fig = plt.figure(figsize=(14, 7))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.Robinson())
    ax.set_global()
    ax.add_feature(cfeature.OCEAN, facecolor="white", zorder=0)
    ax.add_feature(cfeature.LAND,  facecolor="#d0c8b0", zorder=1)
    ax.add_feature(cfeature.COASTLINE, linewidth=0.5, zorder=2)
    ax.add_feature(cfeature.BORDERS,   linewidth=0.3, zorder=2)

    transform = ccrs.PlateCarree()

    if wind_threshold_ms is not None:
        # Below threshold: steel blue; at/above threshold: red
        below = speed_ds < wind_threshold_ms
        above = ~below & ~np.isnan(speed_ds)

        if below.any():
            ax.quiver(lon2d[below], lat2d[below], u_ds[below], v_ds[below],
                      color="steelblue", transform=transform,
                      scale=scale, width=0.0015, headwidth=3, zorder=3,
                      label=f"< 30 kt")
        if above.any():
            ax.quiver(lon2d[above], lat2d[above], u_ds[above], v_ds[above],
                      color="red", transform=transform,
                      scale=scale, width=0.002, headwidth=3, zorder=4,
                      label=f"≥ 30 kt")
        ax.legend(loc="lower left", fontsize=8, framealpha=0.7)
        # Speed colorbar not needed for two-colour scheme; add a text note instead
        ax.text(0.01, 0.01, "Blue < 30 kt  |  Red ≥ 30 kt",
                transform=ax.transAxes, fontsize=8,
                bbox=dict(facecolor="white", alpha=0.7, edgecolor="none"))
    else:
        cmap = plt.get_cmap("Blues")
        norm = mcolors.Normalize(vmin=0, vmax=vmax)
        q = ax.quiver(lon2d, lat2d, u_ds, v_ds, speed_ds,
                      cmap=cmap, norm=norm, transform=transform,
                      scale=scale, width=0.0015, headwidth=3, zorder=3)
        cb = plt.colorbar(q, ax=ax, orientation="horizontal", pad=0.04, shrink=0.6)
        units = u_da.attrs.get("units", "m/s")
        cb.set_label(f"Speed ({units})")

    ax.set_title(title, fontsize=11)
    fig.tight_layout()
    outpath.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[INFO] Wrote {outpath}")


def main() -> int:
    args = parse_args()
    init_yyyymm = args.init

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    forecast_root   = Path(cfg["data"]["output"]["nmme_forecast"])
    preprocess_root = Path(cfg["data"]["local"]["preprocess_root"])
    land_mask_path  = cfg.get("plotting", {}).get("land_ocean_mask", "")

    img_root = forecast_root / init_yyyymm / "images" / "ship_routing"
    wind_dir = img_root / "winds"
    curr_dir = img_root / "currents"

    p0 = pd.Period(init_yyyymm, freq="M")

    # ---- 10m winds ----
    u10m = load_ensemble_mean(preprocess_root, init_yyyymm, "u10m")
    v10m = load_ensemble_mean(preprocess_root, init_yyyymm, "v10m")

    if u10m is not None and v10m is not None:
        n_leads = min(9, u10m.sizes.get("lead", 0))
        for ilead in range(n_leads):
            month_str = str(p0 + ilead)
            title = f"NOAA-SFS 10m Winds — {month_str} (Lead {ilead})"
            outpath = wind_dir / f"10mWindsGlobalMonth{ilead}.png"
            if args.dry_run:
                print(f"[DRY-RUN] would write {outpath}")
            else:
                plot_vector_field(u10m, v10m, ilead, title, outpath,
                                  land_mask_path=land_mask_path,
                                  wind_threshold_ms=WIND_THRESHOLD_MS)
    else:
        print("[WARN] Skipping wind plots: u10m or v10m missing")

    # ---- Surface ocean currents ----
    ssu = load_ensemble_mean(preprocess_root, init_yyyymm, "ssu")
    ssv = load_ensemble_mean(preprocess_root, init_yyyymm, "ssv")

    if ssu is not None and ssv is not None:
        n_leads = min(9, ssu.sizes.get("lead", 0))
        for ilead in range(n_leads):
            month_str = str(p0 + ilead)
            title = f"NOAA-SFS Surface Ocean Currents — {month_str} (Lead {ilead})"
            outpath = curr_dir / f"SfcCurrentsGlobalMonth{ilead}.png"
            if args.dry_run:
                print(f"[DRY-RUN] would write {outpath}")
            else:
                plot_vector_field(ssu, ssv, ilead, title, outpath,
                                  land_mask_path=land_mask_path)
    else:
        print("[WARN] Skipping current plots: ssu or ssv missing")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

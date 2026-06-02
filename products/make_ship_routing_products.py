#!/usr/bin/env python3
"""
Ship-routing forecast products: global vector maps of 10m winds and surface
ocean currents from NOAA-SFS ensemble mean (full field, not anomaly).

Outputs:
  {forecast_root}/{YYYYMM}/images/ship_routing/winds/10mWindsGlobalMonth{N}.png
  {forecast_root}/{YYYYMM}/images/ship_routing/currents/SfcCurrentsGlobalMonth{N}.png
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ship-routing vector forecast products")
    p.add_argument("--init", required=True, help="Forecast init date YYYYMM")
    p.add_argument("--config", default="confignmme.yaml", help="Config YAML path")
    p.add_argument("--dry-run", action="store_true", help="Print paths without writing")
    return p.parse_args()


def load_ensemble_mean(preprocess_root: Path, init_yyyymm: str, var: str) -> xr.DataArray | None:
    """Load preprocessed NOAA-SFS variable and return ensemble mean (lead, lat, lon)."""
    yyyy, mm = init_yyyymm[:4], init_yyyymm[4:]
    fpath = preprocess_root / init_yyyymm / "preprocess" / "NOAA-SFS" / "forecast" / var / \
            f"{var}_NOAA-SFS_{yyyy}_{mm}.nc"
    if not fpath.exists():
        print(f"[WARN] Preprocessed file not found: {fpath}")
        return None
    ds = xr.open_dataset(fpath)
    da = ds[var]
    if "member" in da.dims:
        da = da.mean("member", skipna=True)
    return da  # dims: (lead, lat, lon)


def _speed_and_downsample(u: np.ndarray, v: np.ndarray, lat: np.ndarray, lon: np.ndarray,
                           stride: int):
    """Return downsampled u, v, lat, lon and the full-resolution speed."""
    speed = np.sqrt(u**2 + v**2)
    u_ds  = u[::stride, ::stride]
    v_ds  = v[::stride, ::stride]
    lat_ds = lat[::stride]
    lon_ds = lon[::stride]
    return u_ds, v_ds, lat_ds, lon_ds, speed


def plot_vector_field(u_da: xr.DataArray, v_da: xr.DataArray,
                      ilead: int, title: str, outpath: Path,
                      stride: int = 10) -> None:
    """
    Plot u/v vector field as quiver arrows colored by speed on a global
    Robinson projection.  stride controls arrow density (grid points to skip).
    """
    u = u_da.isel(lead=ilead).values
    v = v_da.isel(lead=ilead).values
    lat = u_da["lat"].values
    lon = u_da["lon"].values

    u_ds, v_ds, lat_ds, lon_ds, speed = _speed_and_downsample(u, v, lat, lon, stride)
    speed_ds = speed[::stride, ::stride]

    lon2d, lat2d = np.meshgrid(lon_ds, lat_ds)

    vmax = float(np.nanpercentile(speed, 95))
    vmax = max(vmax, 1e-3)

    fig = plt.figure(figsize=(14, 7))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.Robinson())
    ax.set_global()
    ax.add_feature(cfeature.LAND, facecolor="lightgray", zorder=1)
    ax.add_feature(cfeature.COASTLINE, linewidth=0.5, zorder=2)
    ax.add_feature(cfeature.BORDERS, linewidth=0.3, zorder=2)

    cmap = plt.get_cmap("YlOrRd")
    norm = mcolors.Normalize(vmin=0, vmax=vmax)

    q = ax.quiver(
        lon2d, lat2d, u_ds, v_ds,
        speed_ds,
        cmap=cmap, norm=norm,
        transform=ccrs.PlateCarree(),
        scale=vmax * 40,
        width=0.0015,
        headwidth=3,
        zorder=3,
    )

    cb = plt.colorbar(q, ax=ax, orientation="horizontal", pad=0.04, shrink=0.6)
    units = u_da.attrs.get("units", "")
    cb.set_label(f"Speed ({units})" if units else "Speed")

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

    forecast_root = Path(cfg["data"]["output"]["nmme_forecast"])
    preprocess_root = Path(cfg["data"]["local"]["preprocess_root"])

    img_root = forecast_root / init_yyyymm / "images" / "ship_routing"
    wind_dir = img_root / "winds"
    curr_dir = img_root / "currents"

    import pandas as pd
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
                plot_vector_field(u10m, v10m, ilead, title, outpath)
    else:
        print("[WARN] Skipping wind plots: u10m or v10m missing")

    # ---- Surface ocean currents (SSU/SSV) ----
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
                plot_vector_field(ssu, ssv, ilead, title, outpath)
    else:
        print("[WARN] Skipping current plots: ssu or ssv missing")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

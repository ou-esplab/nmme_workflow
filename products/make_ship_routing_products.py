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

MS_TO_KT = 1.0 / 0.514444          # conversion factor m/s -> knots
WIND_THRESHOLD_KT = 30.0            # marked on wind colorbar


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ship-routing vector forecast products")
    p.add_argument("--init", required=True, help="Forecast init date YYYYMM")
    p.add_argument("--config", default="confignmme.yaml", help="Config YAML path")
    p.add_argument("--dry-run", action="store_true", help="Print paths without writing")
    return p.parse_args()


def _coord(da: xr.DataArray, names: list[str]) -> np.ndarray:
    for n in names:
        if n in da.coords:
            return da[n].values
    raise KeyError(f"None of {names} found in {list(da.coords)}")


def load_ensemble_mean(preprocess_root: Path, init_yyyymm: str, var: str) -> xr.DataArray | None:
    yyyy, mm = init_yyyymm[:4], init_yyyymm[4:]
    fpath = (preprocess_root / init_yyyymm / "preprocess" / "NOAA-SFS" / "forecast" / var
             / f"{var}_NOAA-SFS_{yyyy}_{mm}.nc")
    if not fpath.exists():
        print(f"[WARN] Preprocessed file not found: {fpath}")
        return None
    da = xr.open_dataset(fpath)[var]
    if "member" in da.dims:
        da = da.mean("member", skipna=True)
    return da


def _apply_ocean_mask(u: np.ndarray, v: np.ndarray,
                      lat: np.ndarray, lon: np.ndarray,
                      land_mask_path: str) -> tuple[np.ndarray, np.ndarray]:
    if not land_mask_path:
        return u, v
    lm = xr.open_dataset(land_mask_path)["land"]
    lm_grid = lm.interp(lat=xr.DataArray(lat, dims="lat"),
                         lon=xr.DataArray(lon, dims="lon"),
                         method="nearest").values
    mask = lm_grid > 0.5
    return np.where(mask, np.nan, u), np.where(mask, np.nan, v)


def plot_vector_field(u_da: xr.DataArray, v_da: xr.DataArray,
                      ilead: int, title: str, outpath: Path,
                      land_mask_path: str = "",
                      units: str = "m/s",
                      cmap: str = "YlOrRd",
                      wind_threshold_ms: float | None = None,
                      stride: int = 6) -> None:
    """
    Plot u/v as quiver arrows colored by speed magnitude on a global Robinson
    projection, ocean points only.  A colorbar shows speed with units.
    If wind_threshold_ms is given, a dashed line marks that level on the colorbar.
    """
    u_full = u_da.isel(lead=ilead).values
    v_full = v_da.isel(lead=ilead).values
    lat = _coord(u_da, ["lat", "latitude"])
    lon = _coord(u_da, ["lon", "longitude"])

    u_full, v_full = _apply_ocean_mask(u_full, v_full, lat, lon, land_mask_path)

    # Convert m/s -> knots for plotting and colorbar
    u_full = u_full * MS_TO_KT
    v_full = v_full * MS_TO_KT

    u_ds = u_full[::stride, ::stride]
    v_ds = v_full[::stride, ::stride]
    lat_ds = lat[::stride]
    lon_ds = lon[::stride]
    lon2d, lat2d = np.meshgrid(lon_ds, lat_ds)
    speed_ds = np.sqrt(u_ds**2 + v_ds**2)

    ocean_speeds = speed_ds[~np.isnan(speed_ds)]
    vmax = float(np.percentile(ocean_speeds, 98)) if ocean_speeds.size > 0 else 1.0
    vmax = max(vmax, 1e-3)

    # Build colormap and norm
    if wind_threshold_ms is not None and 0 < wind_threshold_ms < vmax:
        # Two-segment colormap with a hard break at the threshold:
        # Blues (calm) below, Reds (danger) above — visually unmistakable.
        n = 128
        blues = plt.cm.Blues(np.linspace(0.25, 0.85, n))
        reds  = plt.cm.Reds( np.linspace(0.35, 1.00, n))
        colormap = mcolors.LinearSegmentedColormap.from_list(
            "wind_split", np.vstack([blues, reds]))
        norm = mcolors.TwoSlopeNorm(vmin=0, vcenter=wind_threshold_ms, vmax=vmax)
    else:
        colormap = plt.get_cmap(cmap)
        norm = mcolors.Normalize(vmin=0, vmax=vmax)

    scale = vmax * 40

    fig = plt.figure(figsize=(14, 7))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.Robinson())
    ax.set_global()
    ax.add_feature(cfeature.OCEAN, facecolor="white", zorder=0)
    ax.add_feature(cfeature.LAND,  facecolor="#d0c8b0", zorder=1)
    ax.add_feature(cfeature.COASTLINE, linewidth=0.5, zorder=2)
    ax.add_feature(cfeature.BORDERS,   linewidth=0.3, zorder=2)

    arrow_colors = colormap(norm(speed_ds.ravel()))
    ax.quiver(
        lon2d, lat2d, u_ds, v_ds,
        color=arrow_colors,
        transform=ccrs.PlateCarree(),
        scale=scale, width=0.0015, headwidth=3, zorder=3,
    )

    sm = plt.cm.ScalarMappable(cmap=colormap, norm=norm)
    sm.set_array([])
    cb = plt.colorbar(sm, ax=ax, orientation="horizontal", pad=0.04, shrink=0.6)
    cb.set_label(f"Speed ({units})", fontsize=10)

    if wind_threshold_ms is not None and wind_threshold_ms <= vmax:
        cb.ax.axvline(norm(wind_threshold_ms) * cb.ax.get_xlim()[1],
                      color="black", linewidth=1.5, linestyle="--")
        cb.ax.text(0.5, -0.6, f"Blue < {wind_threshold_ms:.0f} kt  |  Red ≥ {wind_threshold_ms:.0f} kt",
                   transform=cb.ax.transAxes, ha="center", va="top",
                   fontsize=8, color="black")

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
    p0 = pd.Period(init_yyyymm, freq="M")

    # ---- 10m winds ----
    u10m = load_ensemble_mean(preprocess_root, init_yyyymm, "u10m")
    v10m = load_ensemble_mean(preprocess_root, init_yyyymm, "v10m")

    if u10m is not None and v10m is not None:
        n_leads = min(9, u10m.sizes.get("lead", 0))
        for ilead in range(n_leads):
            month_str = str(p0 + ilead)
            title = f"NOAA-SFS 10m Wind Speed & Direction — {month_str} (Lead {ilead})"
            outpath = img_root / "winds" / f"10mWindsGlobalMonth{ilead}.png"
            if args.dry_run:
                print(f"[DRY-RUN] would write {outpath}")
            else:
                plot_vector_field(u10m, v10m, ilead, title, outpath,
                                  land_mask_path=land_mask_path,
                                  units="kt", cmap="YlOrRd",
                                  wind_threshold_ms=WIND_THRESHOLD_KT,
                                  stride=6)
    else:
        print("[WARN] Skipping wind plots: u10m or v10m missing")

    # ---- Surface ocean currents ----
    ssu = load_ensemble_mean(preprocess_root, init_yyyymm, "ssu")
    ssv = load_ensemble_mean(preprocess_root, init_yyyymm, "ssv")

    if ssu is not None and ssv is not None:
        n_leads = min(9, ssu.sizes.get("lead", 0))
        for ilead in range(n_leads):
            month_str = str(p0 + ilead)
            title = f"NOAA-SFS Surface Current Speed & Direction — {month_str} (Lead {ilead})"
            outpath = img_root / "currents" / f"SfcCurrentsGlobalMonth{ilead}.png"
            if args.dry_run:
                print(f"[DRY-RUN] would write {outpath}")
            else:
                plot_vector_field(ssu, ssv, ilead, title, outpath,
                                  land_mask_path=land_mask_path,
                                  units="kt", cmap="Blues",
                                  stride=4)
    else:
        print("[WARN] Skipping current plots: ssu or ssv missing")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Plot PyCPT (CCA bias-corrected) RPSS/ACC skill maps for NMME hindcasts.

One panel per model (plus MME) for a chosen region, season, and init month.
Output: PNG saved to --outdir/pycpt/.

Usage example:
    conda run -n nmme_workflow_env python static/skill/plot_pycpt_skill.py \
        --region CONUS --season MAM --init-month 1 --score rpss

"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import numpy as np
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature

PYCPTDIR_DEFAULT = "/data/esplab/shared/model/initialized/nmme/skill/pycpt"
OUTDIR_DEFAULT   = "/data/esplab/shared/model/initialized/nmme/skill/pycpt/plots"

PREFERRED_ORDER = [
    "NASA-GEOSS2S",
    "CanESM5",
    "GEM5.2-NEMO",
    "NCEP-CFSv2",
    "NCAR-CESM1",
    "COLA-RSMAS-CCSM4",
    "COLA-RSMAS-CESM1",
    "NOAA-SFS",
    "MME",
]

SCORE_META = {
    "rpss": {"var": "rpss", "label": "RPSS", "vmin": -0.5, "vmax": 0.5, "vcenter": 0.0, "cmap": "RdBu_r"},
    "acc":  {"var": "acc",  "label": "ACC",  "vmin": -1.0, "vmax": 1.0, "vcenter": 0.0, "cmap": "RdBu_r"},
}

VAR_LABEL = {"prec": "Precipitation", "tref": "2m Temperature"}


def _load_panels(pycptdir: Path, region: str, var: str, season: str, init_month: int, score: str):
    """Return list of (model_name, DataArray) sorted by PREFERRED_ORDER."""
    region_dir = pycptdir / region
    tag = f".{var}.{region}.{season}.init{init_month:02d}.pycpt_skill."
    files = sorted(region_dir.glob(f"*{tag}*.nc"))
    by_model: dict[str, xr.DataArray] = {}
    for f in files:
        model = f.name[: f.name.index(tag)]
        try:
            ds = xr.open_dataset(f)
            da = ds[score]
            by_model[model] = da.squeeze()
        except Exception as e:
            print(f"[SKIP] {f.name}: {e}")
    panels = [
        (m, by_model[m]) for m in PREFERRED_ORDER if m in by_model
    ] + [
        (m, by_model[m]) for m in sorted(by_model) if m not in PREFERRED_ORDER
    ]
    return panels


def _plot_panel(ax, da: xr.DataArray, title: str, extent: list, meta: dict) -> object:
    lon = da["X"].values if "X" in da.dims else da["lon"].values
    lat = da["Y"].values if "Y" in da.dims else da["lat"].values
    norm = TwoSlopeNorm(vmin=meta["vmin"], vcenter=meta["vcenter"], vmax=meta["vmax"])
    levels = np.linspace(meta["vmin"], meta["vmax"], 21)
    LON, LAT = np.meshgrid(lon, lat)
    m = ax.contourf(
        LON, LAT, da.values,
        levels=levels, cmap=meta["cmap"], norm=norm,
        transform=ccrs.PlateCarree(), extend="both",
    )
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.OCEAN, zorder=1, facecolor="0.85")
    ax.coastlines(linewidth=0.5, color="0.3", zorder=2)
    ax.add_feature(cfeature.BORDERS, linewidth=0.3, edgecolor="0.4", zorder=2)
    ax.add_feature(cfeature.STATES, linewidth=0.2, edgecolor="0.5", zorder=2)
    ax.set_title(title, fontsize=8)
    return m


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot PyCPT skill maps.")
    p.add_argument("--region",     required=True,
                   choices=["CONUS", "Mexico", "Iran", "Venezuela", "C.Asia"])
    p.add_argument("--season",     required=True)
    p.add_argument("--init-month", required=True, type=int, choices=range(1, 13), metavar="1-12")
    p.add_argument("--var",        default="prec", choices=["prec", "tref"])
    p.add_argument("--score",      default="both", choices=["rpss", "acc", "both"])
    p.add_argument("--pycptdir",   default=PYCPTDIR_DEFAULT)
    p.add_argument("--outdir",     default=OUTDIR_DEFAULT)
    return p.parse_args()


def _make_figure(panels, score_key: str, region: str, var: str, season: str,
                 init_month: int, extent: list, outdir: Path) -> None:
    meta = SCORE_META[score_key]
    n = len(panels)
    ncols = 3
    nrows = (n + ncols - 1) // ncols

    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    suptitle = (
        f"{meta['label']}  |  {VAR_LABEL.get(var, var)}  |  {region}  |  "
        f"Season: {season}  Init: {month_names[init_month - 1]}"
    )

    proj = ccrs.PlateCarree()
    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(5 * ncols, 3.5 * nrows + 1.0),
        subplot_kw={"projection": proj},
    )
    axes_flat = np.array(axes).flatten()

    mappable = None
    for i, (model, da) in enumerate(panels):
        mappable = _plot_panel(axes_flat[i], da, model, extent, meta)
    for j in range(len(panels), len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.suptitle(suptitle, fontsize=12, fontweight="bold", y=1.01)
    fig.subplots_adjust(bottom=0.10, hspace=0.20, wspace=0.05)
    if mappable is not None:
        cbar_ax = fig.add_axes([0.15, 0.04, 0.70, 0.025])
        cbar = fig.colorbar(mappable, cax=cbar_ax, orientation="horizontal")
        cbar.set_label(meta["label"], fontsize=10)
        cbar.ax.tick_params(labelsize=8)

    outdir.mkdir(parents=True, exist_ok=True)
    outname = f"{score_key}_{var}_{region}_init{init_month:02d}_{season}.png"
    outpath = outdir / outname
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"[SAVED] {outpath}")
    plt.close(fig)


def main() -> int:
    args = parse_args()
    pycptdir = Path(args.pycptdir)
    outdir = Path(args.outdir)

    # Region extent: [lon_min, lon_max, lat_min, lat_max]
    EXTENTS = {
        "CONUS":     [-125, -66,  24,  50],
        "Mexico":    [-118, -97,  20,  33],
        "Iran":      [  40,  64,  24,  40],
        "Venezuela": [ -73, -60,   0,  13],
        "C.Asia":    [  45,  88,  28,  56],
    }
    extent = EXTENTS[args.region]

    scores = ["rpss", "acc"] if args.score == "both" else [args.score]
    for score_key in scores:
        panels = _load_panels(pycptdir, args.region, args.var, args.season, args.init_month, score_key)
        if not panels:
            print(f"[SKIP] No data for {args.region}/{args.season}/init{args.init_month:02d}/{score_key}")
            continue
        _make_figure(panels, score_key, args.region, args.var, args.season,
                     args.init_month, extent, outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

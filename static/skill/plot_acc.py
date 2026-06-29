#!/usr/bin/env python3
"""
Plot ACC skill maps for NMME hindcasts.

One panel per model (plus MME) for a chosen variable, init month, and lead.
Output: PNG saved to --outdir (and optionally displayed with --show).

Usage example:
    conda run -n nmme_workflow_env python static/skill/plot_acc.py \
        --var prec --init-month 1 --season-lead 1

"""

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend; override below if --show
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.colors import TwoSlopeNorm
import numpy as np
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.util import add_cyclic_point

SKILLDIR_DEFAULT = "/data/esplab/shared/model/initialized/nmme/skill/1991-2020"
OUTDIR_DEFAULT   = "/data/esplab/shared/model/initialized/nmme/skill/1991-2020/plots"

MONTH_ABBR  = list("JFMAMJJASOND")   # 0-indexed, used for season labels
MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun",
               "Jul","Aug","Sep","Oct","Nov","Dec"]


def _season_label(init_month: int, season_start_lead: int, n: int = 3) -> str:
    return "".join(
        MONTH_ABBR[(init_month - 1 + l) % 12]
        for l in range(season_start_lead, season_start_lead + n)
    )

# Latitude extent per variable (matches obs data coverage)
VAR_LAT_EXTENT = {
    "prec": (-50, 50),   # CHIRPS: 50S–50N
    "tref": (-90, 90),   # GHCN-CAMS: global land
    "sst":  (-90, 90),   # ERSSTv5: global ocean
}

VAR_LABEL = {
    "prec": "Precipitation",
    "tref": "2m Temperature",
    "sst":  "SST",
}

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

# Diverging colormap: 0 = no correlation, positive = skilful, negative = anti-correlated
VMIN   = -0.8
VMAX   =  0.8
CMAP   = "RdBu_r"
NORM   = TwoSlopeNorm(vmin=VMIN, vcenter=0.0, vmax=VMAX)


def _to_crs180(da: xr.DataArray) -> tuple:
    """Convert geographic lons to PlateCarree(central_longitude=180) coords.

    Returns (data_cyc, lon_cyc, lat) ready for contourf with that transform.
    The CRS seam falls at geographic 0° (prime meridian = center of Robinson),
    so the dateline at geographic 180° is in the middle of the data range—no gap.
    """
    lon_geo = da["lon"].values
    if float(lon_geo.min()) < 0:
        lon_geo = (lon_geo + 360) % 360
    lon_crs = lon_geo - 180
    idx = np.argsort(lon_crs)
    lon_crs = lon_crs[idx]
    data_sorted = da.values[:, idx]
    data_cyc, lon_cyc = add_cyclic_point(data_sorted, coord=lon_crs)
    return data_cyc, lon_cyc, da["lat"].values


def _load_model(skilldir: Path, model: str, var: str) -> xr.DataArray | None:
    pattern = list(skilldir.glob(f"{model}.{var}.acc.*.nc"))
    if not pattern:
        return None
    ds = xr.open_dataset(pattern[0])
    return ds["acc"]


def _plot_panel(ax, da: xr.DataArray, title: str, lat_extent: tuple, var: str = "") -> object:
    """Draw one ACC panel on ax; return the mappable."""
    data_cyc, lon_cyc, lat = _to_crs180(da)
    levels = np.linspace(VMIN, VMAX, 21)
    m = ax.contourf(
        lon_cyc, lat, data_cyc,
        levels=levels,
        cmap=CMAP,
        norm=NORM,
        transform=ccrs.PlateCarree(central_longitude=180),
        extend="both",
    )
    if lat_extent == (-90, 90):
        ax.set_global()
    else:
        ax.set_extent([-180, 180, lat_extent[0], lat_extent[1]], crs=ccrs.PlateCarree())
    if var == "sst":
        ax.add_feature(cfeature.LAND, zorder=1, facecolor="0.85")
    else:
        ax.add_feature(cfeature.OCEAN, zorder=1, facecolor="0.85")
    ax.coastlines(linewidth=0.5, color="0.3", zorder=2)
    ax.add_feature(cfeature.BORDERS, linewidth=0.3, edgecolor="0.4", zorder=2)
    ax.set_title(title, fontsize=8)
    return m


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Plot ACC skill maps for NMME hindcasts."
    )
    p.add_argument("--var",        required=True, choices=["prec", "tref", "sst"])
    p.add_argument("--init-month", required=True, type=int, choices=range(1, 13),
                   metavar="1-12")
    p.add_argument("--season-lead", required=True, type=int, choices=range(1, 8),
                   metavar="1-7", dest="season_lead",
                   help="First lead of the 3-month season (1=leads 1-3, 7=leads 7-9)")
    p.add_argument("--skilldir",   default=SKILLDIR_DEFAULT)
    p.add_argument("--outdir",     default=OUTDIR_DEFAULT)
    p.add_argument("--models",     default="ALL",
                   help="Comma-separated list, or ALL")
    p.add_argument("--show",       action="store_true",
                   help="Display figure interactively (requires a display)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    skilldir = Path(args.skilldir)
    outdir   = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Resolve model list
    if args.models == "ALL":
        tag = f".{args.var}.acc."
        available = [
            p.name[: p.name.index(tag)]
            for p in skilldir.glob(f"*.{args.var}.acc.*.nc")
        ]
        models = [m for m in PREFERRED_ORDER if m in available]
        models += [m for m in available if m not in models]
    else:
        models = [m.strip() for m in args.models.split(",")]

    if not models:
        print(f"[ERROR] No ACC skill files found in {skilldir} for var={args.var}")
        return 1

    # Load data for each model
    panels: list[tuple[str, xr.DataArray]] = []
    for model in models:
        da = _load_model(skilldir, model, args.var)
        if da is None:
            print(f"[SKIP] {model}: file not found")
            continue
        if args.init_month not in da["init_month"].values:
            print(f"[SKIP] {model}: init_month={args.init_month} not in file")
            continue
        if args.season_lead not in da["season_lead"].values:
            print(f"[SKIP] {model}: season_lead={args.season_lead} not in file")
            continue
        slice2d = da.sel(init_month=args.init_month, season_lead=args.season_lead).squeeze()
        panels.append((model, slice2d))

    if not panels:
        print("[ERROR] No data to plot.")
        return 1

    n     = len(panels)
    ncols = 3
    nrows = (n + ncols - 1) // ncols

    season_lbl = _season_label(args.init_month, args.season_lead)
    var_label  = VAR_LABEL.get(args.var, args.var)
    suptitle   = (
        f"ACC  |  {var_label}  |  "
        f"Init: {MONTH_NAMES[args.init_month - 1]}  "
        f"Season: {season_lbl}"
    )

    proj = ccrs.Robinson()
    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(6 * ncols, 3.2 * nrows + 1.0),
        subplot_kw={"projection": proj},
    )
    axes_flat = np.array(axes).flatten()

    lat_extent = VAR_LAT_EXTENT.get(args.var, (-90, 90))

    mappable = None
    for i, (model, slice2d) in enumerate(panels):
        ax = axes_flat[i]
        mappable = _plot_panel(ax, slice2d, model, lat_extent, var=args.var)

    # Hide unused axes
    for j in range(len(panels), len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.suptitle(suptitle, fontsize=13, fontweight="bold", y=1.00)

    # Colorbar spanning the full figure width, below all panels
    fig.subplots_adjust(bottom=0.10, hspace=0.15, wspace=0.05)
    if mappable is not None:
        cbar_ax = fig.add_axes([0.15, 0.04, 0.70, 0.025])
        cbar = fig.colorbar(
            mappable, cax=cbar_ax,
            orientation="horizontal",
            ticks=[-0.8, -0.6, -0.4, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8],
        )
        cbar.set_label("ACC", fontsize=10)
        cbar.ax.tick_params(labelsize=8)

    outname = f"acc_{args.var}_init{args.init_month:02d}_{season_lbl}.png"
    outpath = outdir / outname
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"[SAVED] {outpath}")

    if args.show:
        matplotlib.use("TkAgg")
        plt.show()

    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build notebook-style tercile probability maps for all configured regions.

This script uses the monthly NMME forecast anomalies produced by makefcsts and
computes model-agreement tercile probabilities (BN/NN/AN) against hindcast
thresholds. It then writes one 3-panel map per region-season.
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple

import matplotlib.pyplot as plt
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature

from utils.config import load_config

# Model names in forecast output -> hindcast directory names.
MODEL_DIR_MAP = {
    "NASA-GEOSS2S": "NASA-GEOSS2S",
    "CanESM5": "CanESM5",
    "GEM5.2-NEMO": "GEM5-NEMO",
    "NCEP-CFSv2": "NCEP-CFSv2",
}

SEASON_LEADS: Dict[str, Tuple[int, int]] = {
    "MAM": (0, 3),
    "AMJ": (1, 4),
    "MJJ": (2, 5),
    "JJA": (3, 6),
    "ASO": (7, 10),
    "NDJ": (8, 11),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create tercile probability maps for all regions")
    p.add_argument("--init", required=True, help="Forecast init YYYYMM, e.g., 202603")
    p.add_argument("--config", default="confignmme.yaml", help="Path to config YAML")
    p.add_argument(
        "--seasons",
        default="MAM",
        help="Comma-separated seasons from {MAM,AMJ,MJJ,JJA,ASO,NDJ}; use ALL for all",
    )
    p.add_argument(
        "--outdir",
        default=None,
        help="Optional output directory; defaults to monthly/<init>/images/tercile_probs",
    )
    p.add_argument(
        "--regions",
        default="ALL",
        help="Comma-separated region names from config; use ALL for all regions",
    )
    return p.parse_args()


def to_0360(da: xr.DataArray) -> xr.DataArray:
    return da.assign_coords(lon=((da.lon + 360) % 360)).sortby("lon")


def subset_region(da: xr.DataArray, lat_bounds: Tuple[float, float], lon_bounds: Tuple[float, float]) -> xr.DataArray:
    lat0, lat1 = lat_bounds
    lon0, lon1 = lon_bounds
    lon0 = (lon0 + 360) % 360
    lon1 = (lon1 + 360) % 360
    if lon0 <= lon1:
        return da.sel(lat=slice(min(lat0, lat1), max(lat0, lat1)), lon=slice(lon0, lon1))

    # Dateline wrap case.
    left = da.sel(lat=slice(min(lat0, lat1), max(lat0, lat1)), lon=slice(lon0, 360))
    right = da.sel(lat=slice(min(lat0, lat1), max(lat0, lat1)), lon=slice(0, lon1))
    return xr.concat([left, right], dim="lon")


def load_forecast(init_yyyymm: str, out_root: Path) -> xr.Dataset:
    fpath = (
        out_root
        / init_yyyymm
        / "data"
        / f"NMME_fcst_{init_yyyymm}.anom.monthly.prec_sfc.emean.nc"
    )
    if not fpath.exists():
        raise FileNotFoundError(f"Forecast file not found: {fpath}")
    return xr.open_dataset(fpath)


def hindcast_thresholds_for_model(
    model_name: str,
    season: str,
    lat_bounds: Tuple[float, float],
    lon_bounds: Tuple[float, float],
    hind_root: Path,
) -> Tuple[xr.DataArray, xr.DataArray]:
    model_dir = MODEL_DIR_MAP[model_name]
    files = sorted(glob.glob(str(hind_root / model_dir / "*.nc")))
    if not files:
        raise FileNotFoundError(f"No hindcast files for {model_name} under {hind_root / model_dir}")

    l0, l1 = SEASON_LEADS[season]
    sample_arrays = []

    for fp in files:
        ds = xr.open_dataset(fp, decode_times=False)
        if "prec" not in ds:
            ds.close()
            continue

        da = ds["prec"]
        da = to_0360(da)
        da = subset_region(da, lat_bounds, lon_bounds)

        if "lead" in da.dims:
            da = da.isel(lead=slice(l0, l1)).mean("lead")

        if "ens" in da.dims:
            da = da.rename({"ens": "sample"})
        else:
            da = da.expand_dims(sample=[0])

        sample_arrays.append(da.load())
        ds.close()

    if not sample_arrays:
        raise RuntimeError(f"No usable hindcast arrays for {model_name}")

    all_samples = xr.concat(sample_arrays, dim="sample")
    t33 = all_samples.quantile(0.33, dim="sample").drop_vars("quantile", errors="ignore")
    t66 = all_samples.quantile(0.66, dim="sample").drop_vars("quantile", errors="ignore")
    return t33, t66


def compute_region_probabilities(
    ds_fc: xr.Dataset,
    season: str,
    lat_bounds: Tuple[float, float],
    lon_bounds: Tuple[float, float],
    hind_root: Path,
) -> Dict[str, xr.DataArray]:
    l0, l1 = SEASON_LEADS[season]

    bn_model = []
    nn_model = []
    an_model = []

    for model_name in MODEL_DIR_MAP:
        if model_name not in ds_fc:
            print(f"[WARN] Forecast variable missing for {model_name}; skipping")
            continue

        t33, t66 = hindcast_thresholds_for_model(model_name, season, lat_bounds, lon_bounds, hind_root)

        fc = to_0360(ds_fc[model_name].isel(L=slice(l0, l1)).mean("L"))
        fc = subset_region(fc, lat_bounds, lon_bounds)

        bn = (fc < t33).astype(float) * 100.0
        nn = ((fc >= t33) & (fc <= t66)).astype(float) * 100.0
        an = (fc > t66).astype(float) * 100.0

        bn_model.append(bn)
        nn_model.append(nn)
        an_model.append(an)

    if not bn_model:
        raise RuntimeError("No models were available to compute tercile probabilities")

    return {
        "BN": xr.concat(bn_model, dim="model", coords="minimal", compat="override").mean("model"),
        "NN": xr.concat(nn_model, dim="model", coords="minimal", compat="override").mean("model"),
        "AN": xr.concat(an_model, dim="model", coords="minimal", compat="override").mean("model"),
    }


def plot_probabilities(
    prob: Dict[str, xr.DataArray],
    region: str,
    season: str,
    init_yyyymm: str,
    lead_label: str,
    out_png: Path,
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), subplot_kw={"projection": ccrs.PlateCarree()})

    panels = [
        ("BN", "Below Normal", "Blues"),
        ("NN", "Near Normal", "Greens"),
        ("AN", "Above Normal", "YlOrRd"),
    ]

    for ax, (key, title, cmap) in zip(axes, panels):
        da = prob[key]
        ax.set_extent([float(da.lon.min()), float(da.lon.max()), float(da.lat.min()), float(da.lat.max())])
        da.plot(
            ax=ax,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            vmin=0,
            vmax=100,
            cbar_kwargs={"label": "Probability (%)", "shrink": 0.82, "pad": 0.03},
        )
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
    fig.savefig(out_png, dpi=120)
    plt.close(fig)


def build_target_label(init_yyyymm: str, season: str) -> str:
    init_dt = datetime.strptime(init_yyyymm, "%Y%m")
    l0, l1 = SEASON_LEADS[season]
    month_labels = []
    for lead in range(l0, l1):
        year = init_dt.year + ((init_dt.month - 1 + lead) // 12)
        month = ((init_dt.month - 1 + lead) % 12) + 1
        month_labels.append(datetime(year, month, 1).strftime("%b %Y"))
    return f"Lead L{l0}-L{l1 - 1}; Target: {' - '.join(month_labels)}"


def main() -> int:
    args = parse_args()

    cfg = load_config(args.config)
    out_root = Path(cfg["data"]["output"]["nmme_monthly"])
    hind_root = Path("/data/esplab/shared/model/initialized/nmme/hindcast/monthly/prec/monthly/full")

    if args.seasons.upper() == "ALL":
        seasons = list(SEASON_LEADS.keys())
    else:
        seasons = [s.strip().upper() for s in args.seasons.split(",") if s.strip()]
        bad = [s for s in seasons if s not in SEASON_LEADS]
        if bad:
            raise ValueError(f"Unsupported seasons: {bad}. Allowed: {list(SEASON_LEADS)}")

    if args.outdir:
        outdir = Path(args.outdir)
    else:
        outdir = out_root / args.init / "images" / "tercile_probs"

    ds_fc = load_forecast(args.init, out_root)
    regions = cfg.get("pycpt_regions", [])
    if not regions:
        raise ValueError("No pycpt_regions in config")

    if args.regions.upper() != "ALL":
        wanted = {r.strip() for r in args.regions.split(",") if r.strip()}
        regions = [r for r in regions if r.get("name") in wanted]
        if not regions:
            raise ValueError(f"No matching regions for --regions={args.regions}")

    for reg in regions:
        rname = reg["name"]
        lat_bounds = tuple(reg["lat"])
        lon_bounds = tuple(reg["lon"])

        for season in seasons:
            print(f"[INFO] Computing {rname} {season}")
            lead_label = build_target_label(args.init, season)
            prob = compute_region_probabilities(ds_fc, season, lat_bounds, lon_bounds, hind_root)
            out_png = outdir / rname / f"NMME_{args.init}_{rname}_{season}_tercile_probs.png"
            plot_probabilities(prob, rname, season, args.init, lead_label, out_png)
            print(f"[INFO] Wrote {out_png}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

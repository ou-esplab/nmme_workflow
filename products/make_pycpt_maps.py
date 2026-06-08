#!/usr/bin/env python3
"""
Produce bias-corrected forecast maps from PyCPT output files.

For each (region, season, variable) found in the pycpt output directory:
  1. Anomaly maps   — per-model + MME panels from .pycpt.det.anom.* files
  2. Tercile probs  — 3-panel BN/NN/AN from .pycpt.prob.* files (MME only)
  3. Most-likely    — dominant tercile map
  4. CPT-dominant   — CPT-style dominant-category map

Output goes to {nmme_forecast}/{init}/images/pycpt/{product}/{region}/
"""

import argparse
import re
import sys
from pathlib import Path
from math import ceil

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.config import load_config
from utils.paths import ensure_dir
from products.make_tercile_probability_maps import (
    plot_probabilities,
    plot_most_likely_from_prob,
    plot_cpt_dominant_from_prob,
    _to_percent_if_fraction,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANOM_META = {
    "prec": {
        "label": "Precipitation Anomaly",
        "unit": "mm/day",
        "levels": [-10, -6, -4, -2, -1, -0.5, -0.25, 0.25, 0.5, 1, 2, 4, 6, 10],
        "cmap": "BrBG",
    },
    "tref": {
        "label": "2-m Temperature Anomaly",
        "unit": "°C",
        "levels": [-4, -3, -2, -1, -0.5, -0.25, 0.25, 0.5, 1, 2, 3, 4],
        "cmap": "RdBu_r",
    },
}

# Reverse-sanitize model variable names for plot labels
_UNSANITIZE = {
    "NASA_GEOSS2S":     "NASA-GEOSS2S",
    "GEM5_2_NEMO":      "GEM5.2-NEMO",
    "NCEP_CFSv2":       "NCEP-CFSv2",
    "COLA_RSMAS_CCSM4": "COLA-RSMAS-CCSM4",
    "COLA_RSMAS_CESM1": "COLA-RSMAS-CESM1",
    "NOAA_SFS":         "NOAA-SFS",
}


def _model_label(var_name: str) -> str:
    return _UNSANITIZE.get(var_name, var_name)


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_cases(pycpt_root: Path, init: str) -> list[dict]:
    """
    Return list of dicts with keys: region, season, var, lev, det_path, prob_path.
    Discovers from det files; skips if corresponding prob file is missing.
    """
    pattern = f"NMME_fcst_{init}.pycpt.det.anom.*.nc"
    cases = []
    for det in sorted(pycpt_root.glob(f"{init}/*/*/data/{pattern}")):
        # Structure: {pycpt_root}/{init}/{region}/{season}/data/{file}
        season = det.parent.parent.name
        region = det.parent.parent.parent.name

        # parse var_lev from filename: ...det.anom.{var}_{lev}.{region}.{season}.nc
        m = re.search(r"\.pycpt\.det\.anom\.([^.]+)\..*\.nc$", det.name)
        if not m:
            continue
        var_lev = m.group(1)
        if "_" in var_lev:
            var, lev = var_lev.rsplit("_", 1)
        else:
            var, lev = var_lev, ""

        prob = det.parent / det.name.replace(".det.anom.", ".prob.")
        if not prob.exists():
            print(f"[WARN] prob file missing for {det.name}, skipping")
            continue

        cases.append(dict(region=region, season=season, var=var, lev=lev,
                          det_path=det, prob_path=prob))
    return cases


# ---------------------------------------------------------------------------
# Anomaly map
# ---------------------------------------------------------------------------

def plot_pycpt_anomalies(
    det_ds: xr.Dataset,
    region: str,
    season: str,
    init_yyyymm: str,
    var: str,
    out_png: Path,
) -> None:
    meta = ANOM_META.get(var, ANOM_META["tref"])
    model_vars = [v for v in det_ds.data_vars if v != "MME"]
    all_vars = model_vars + (["MME"] if "MME" in det_ds.data_vars else [])

    ncols = 3
    nrows = ceil(len(all_vars) / ncols)
    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(6 * ncols, 4 * nrows),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )
    axes_flat = np.array(axes).flatten()

    for i, vname in enumerate(all_vars):
        ax = axes_flat[i]
        da = det_ds[vname]
        ext = [float(da.lon.min()), float(da.lon.max()),
               float(da.lat.min()), float(da.lat.max())]
        ax.set_extent(ext, crs=ccrs.PlateCarree())
        cf = ax.contourf(
            da["lon"], da["lat"], da,
            levels=meta["levels"],
            cmap=meta["cmap"],
            extend="both",
            transform=ccrs.PlateCarree(),
        )
        fig.colorbar(cf, ax=ax, label=meta["unit"], shrink=0.85, pad=0.03)
        ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, linewidth=0.6)
        ax.add_feature(cfeature.STATES, linewidth=0.4)
        ax.set_title(_model_label(vname), fontsize=10, weight="bold")

    for j in range(len(all_vars), len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.suptitle(
        f"PyCPT {meta['label']}  |  {init_yyyymm}  {region}  {season}",
        fontsize=14, weight="bold",
    )
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Produce PyCPT bias-corrected forecast maps")
    p.add_argument("--init",      required=True, help="Forecast init YYYYMM")
    p.add_argument("--config",    default="confignmme.yaml")
    p.add_argument("--pycpt-root", default=None,
                   help="Override pycpt output root from config")
    p.add_argument("--outdir",    default=None,
                   help="Override image output root from config")
    p.add_argument("--regions",   default="ALL",
                   help="Comma-separated region names, or ALL")
    p.add_argument("--vars",      default="ALL",
                   help="Comma-separated variable names (prec/tref), or ALL")
    return p.parse_args()


def main() -> int:
    args  = parse_args()
    cfg   = load_config(args.config)
    init  = args.init

    pycpt_root = Path(args.pycpt_root or cfg["data"]["output"]["pycpt"])
    fcst_root  = Path(args.outdir or cfg["data"]["output"]["nmme_forecast"])
    img_root   = fcst_root / init / "images" / "pycpt"

    region_filter = set(args.regions.split(",")) if args.regions != "ALL" else None
    var_filter    = set(args.vars.split(","))    if args.vars != "ALL"    else None

    cases = discover_cases(pycpt_root, init)
    if not cases:
        print(f"[WARN] No pycpt output files found under {pycpt_root}/{init}/")
        return 0

    for c in cases:
        region, season, var = c["region"], c["season"], c["var"]

        if region_filter and region not in region_filter:
            continue
        if var_filter and var not in var_filter:
            continue

        print(f"\n[{region}  {season}  {var}]")

        stem = f"NMME_{init}_{region}_{season}_{var}"

        # ----------------------------------------------------------------
        # Anomaly maps (det file)
        # ----------------------------------------------------------------
        det_ds = xr.open_dataset(c["det_path"])
        out_anom = img_root / "anomalies" / region / f"{stem}_anomalies.png"
        plot_pycpt_anomalies(det_ds, region, season, init, var, out_anom)
        print(f"  [SAVED] {out_anom.name}")
        det_ds.close()

        # ----------------------------------------------------------------
        # Tercile probability maps (prob file)
        # ----------------------------------------------------------------
        prob_ds = xr.open_dataset(c["prob_path"])
        if "MME" not in prob_ds:
            print(f"  [WARN] MME not in prob file, skipping tercile maps")
            prob_ds.close()
            continue

        da_mme = prob_ds["MME"]   # (cat, lat, lon)
        prob = {
            "BN": _to_percent_if_fraction(
                da_mme.sel(cat="bn").drop_vars("cat"), f"{region}/{season}/BN"),
            "NN": _to_percent_if_fraction(
                da_mme.sel(cat="nn").drop_vars("cat"), f"{region}/{season}/NN"),
            "AN": _to_percent_if_fraction(
                da_mme.sel(cat="an").drop_vars("cat"), f"{region}/{season}/AN"),
        }

        lead_label = f"PyCPT MOS"

        out_tp = img_root / "tercile_probs" / region / f"{stem}_tercile_probs.png"
        plot_probabilities(prob, region, season, init, lead_label, out_tp)
        print(f"  [SAVED] {out_tp.name}")

        out_ml = img_root / "most_likely" / region / f"{stem}_most_likely.png"
        plot_most_likely_from_prob(prob, region, season, init, var, out_ml)
        print(f"  [SAVED] {out_ml.name}")

        out_cd = img_root / "cpt_dominant" / region / f"{stem}_cpt_dominant.png"
        plot_cpt_dominant_from_prob(prob, region, season, init, var, out_cd)
        print(f"  [SAVED] {out_cd.name}")

        prob_ds.close()

    print("\n[DONE]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

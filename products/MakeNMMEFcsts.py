#!/usr/bin/env python3
"""
Build NMME monthly forecast products.
Supports dry-run mode (no plots / no NetCDF).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
import warnings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_config
from utils.paths import ensure_dir
from utils.nmme_products_utils import (
    init_models,
    build_mme_for_month,
    nmme_plot,
    nmme_write,
)

warnings.filterwarnings("ignore")


def parse_args():
    p = argparse.ArgumentParser(
        description="Build NMME monthly forecast products"
    )
    p.add_argument(
        "--date",
        required=True,
        help="Forecast init date YYYYMM (e.g., 202603)",
    )
    p.add_argument(
        "--config",
        default="confignmme.yaml",
        help="Path to NMME config YAML",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without writing plots or NetCDF output",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # ---- Validate date ----
    try:
        fcstdate = datetime.strptime(args.date, "%Y%m")
    except ValueError:
        print("[ERROR] --date must be YYYYMM (e.g., 202603)", file=sys.stderr)
        return 2

    # ✅ DEFINE fcst_yyyymm FIRST
    fcst_yyyymm = fcstdate.strftime("%Y%m")

    cfg = load_config(args.config)
    land_ocean_mask = cfg.get("plotting", {}).get("land_ocean_mask")

    # ✅ NOW SAFE TO USE
    data_root = (
        Path(cfg["data"]["local"].get("preprocess_root"))
        / fcst_yyyymm
        / "preprocess"
    )

    clim_root = Path(
        cfg["data"]["local"].get(
            "climatology",
            "/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/",
        )
    )

    forecast_root = Path(cfg["data"]["output"]["nmme_forecast"])
    fcst_yyyymm = fcstdate.strftime("%Y%m")
    figpath = forecast_root / fcst_yyyymm / "images" / "anomalies"

    print(f"[INFO] Forecast init: {fcstdate:%Y-%m}")
    print(f"[INFO] Data root: {data_root}")
    print(f"[INFO] Climatology root: {clim_root}")
    print(f"[INFO] Output base: {forecast_root}")
    print(f"[INFO] Dry-run mode: {args.dry_run}")

    if not args.dry_run:
        ensure_dir(figpath)

    init_models()

    ds_fcst = build_mme_for_month(
        data_root=data_root,
        clim_root=clim_root,
        init_yyyymm=fcst_yyyymm,
        mask_path=land_ocean_mask,
    )

    if args.dry_run:
        print("[DRY-RUN] Products computed successfully")
        print("[DRY-RUN] Dataset summary:")
        print(ds_fcst)
    else:
        nmme_plot(ds_fcst, figpath, land_mask_path=land_ocean_mask)
        assert "valid" in ds_fcst.coords, ds_fcst.coords
        nmme_write(ds_fcst, fcst_yyyymm, forecast_root=str(forecast_root))
        print(f"[INFO] Products written for {fcst_yyyymm}")

    return 0


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# coding: utf-8
import xarray as xr
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
import argparse
import warnings
import sys
import os
from utils.nmme_products_utils import (
    init_models, build_mme_for_month, nmme_plot, nmme_write
)
warnings.filterwarnings("ignore")

parser = argparse.ArgumentParser(description="Build NMME forecast products from locally downloaded files.")
parser.add_argument("--date", required=True, help="Forecast init date YYYYMM (e.g., 202603)")
parser.add_argument("--data_root", default="/data/esplab/nmme-backup",
                    help="Root of locally downloaded NMME files")
parser.add_argument("--clim_path",
                    default="/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/",
                    help="Directory containing 1991-2020 monthly climatologies")
args = parser.parse_args()

try:
    fcstdate = datetime.strptime(args.date, "%Y%m")
except Exception:
    sys.exit("ERROR: --date must be YYYYMM (e.g., 202603)")

data_root = Path(args.data_root)
clim_root = Path(args.clim_path)
fcst_yyyymm = fcstdate.strftime("%Y%m")

print(f"[INFO] Using local NMME data from: {data_root}")
print(f"[INFO] Climatology path: {clim_root}")
print(f"[INFO] Forecast init (target): {fcstdate:%Y-%m}")

# Build per-model anomalies & MME
(ds_models, vnames, levs, units) = init_models()

ds_fcst = build_mme_for_month(data_root, clim_root, pd.Timestamp(fcstdate))

# Output locations
figpath = f"/data/esplab/shared/model/initialized/nmme/forecast/monthly/{fcst_yyyymm}/images/"
os.makedirs(figpath, exist_ok=True)
print(f"[INFO] Writing plots to: {figpath}")
nmme_plot(ds_fcst, figpath)

print(f"[INFO] Writing data for {fcst_yyyymm}")
nmme_write(ds_fcst, fcst_yyyymm)

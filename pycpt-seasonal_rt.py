#!/usr/bin/env python3
"""
pycpt-seasonal_rt.py

Thin PyCPT workflow driver.

Responsibilities:
  • Parse CLI arguments
  • Load configuration
  • Resolve region + models
  • Load data (local)
  • Call PyCPT routines
"""

from pathlib import Path
import argparse
import datetime as dt
import pycpt
import nmme_pycpt_utils as utils


# ============================================================
# CLI
# ============================================================

parser = argparse.ArgumentParser(description="Run PyCPT for a configured region")
parser.add_argument("config", type=Path)
parser.add_argument("fcstdate", help="Forecast initialization YYYYMM")
parser.add_argument("--only", required=True, help="Region name")
args = parser.parse_args()


# ============================================================
# LOAD CONFIG
# ============================================================

cfg = utils.load_config(args.config)

data_cfg = cfg["data"]
regions = cfg["pycpt_regions"]
models = cfg["models"]

region = utils.get_region(regions, args.only)
models_used = utils.resolve_models(models, region)

print(f"[INFO] Region: {args.only}")
print(f"[INFO] Models: {', '.join(models_used)}")


# ============================================================
# LOCAL DATA SETUP
# ============================================================

local_cfg = data_cfg["local"]
root = Path(local_cfg["root"])
pred_dir = local_cfg["predictand"]["dir"]
pred_var = local_cfg["predictand"]["var"]
model_base_map = local_cfg.get("model_base", {})
model_var = local_cfg["model_vars"]["precipitation"]


# ============================================================
# LOAD DATA
# ============================================================

Y = utils.load_predictand_local(root, pred_dir, pred_var)

hindcasts = []
forecasts = []
names = []

for m in models_used:
    base = model_base_map.get(m, m)
    hc, fc = utils.load_model_local(root, base, args.fcstdate, model_var)
    hindcasts.append(hc)
    forecasts.append(fc)
    names.append(f"{base}.PRCP")


# ============================================================
# PY-CPT RUN
# ============================================================

download_args = {
    "fdate": dt.datetime.strptime(args.fcstdate, "%Y%m"),
    "predictor_extent": {
        "north": region["lat"][1],
        "south": region["lat"][0],
        "east": region["lon"][1],
        "west": region["lon"][0],
    },
    "predictand_extent": {
        "north": region["lat"][1],
        "south": region["lat"][0],
        "east": region["lon"][1],
        "west": region["lon"][0],
    },
    "target": region["season"],
    "filetype": "cptv10.tsv",
}

domain_dir = pycpt.setup(
    Path(data_cfg["output"]["pycpt"]) / args.fcstdate,
    download_args["predictor_extent"],
)

hcsts, fcsts, skill, _, _ = pycpt.evaluate_models(
    hindcasts,
    "CCA",
    Y,
    forecasts,
    {},
    domain_dir,
    names,
    interactive=False,
)

print("[INFO] PyCPT run complete.")
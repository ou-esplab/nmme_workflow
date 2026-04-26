#!/usr/bin/env python3
# coding: utf-8
from __future__ import annotations
#!/usr/bin/env python3
# coding: utf-8
"""
nmme_utils.py
-------------
NMME pipeline utilities: config loading, date scanning, subprocess execution,
logging/locking helpers, and thin stage runners used by the shell pipeline.
"""
"""
nmme_utils.py
-------------
NMME pipeline utilities: config loading, date scanning, subprocess execution,
logging/locking helpers, and thin stage runners used by the shell pipeline.
"""
from typing import Optional, Dict, Any
import os, glob, subprocess, yaml

def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}

def find_latest_forecast(root: str) -> Optional[str]:
    latest_key, latest_y, latest_m = 0, 0, 0
    pattern = os.path.join(root, "**", "forecast", "**", "*.nc")
    for file in glob.glob(pattern, recursive=True):
        base = os.path.basename(file)  # var_model_YYYY_MM.nc
        parts = base.split("_")
        if len(parts) < 4:
            continue
        y, m = parts[-2], parts[-1].replace(".nc", "")
        if y.isdigit() and m.isdigit():
            key = int(y) * 100 + int(m)
            if key > latest_key:
                latest_key, latest_y, latest_m = key, int(y), int(m)
    return None if latest_key == 0 else f"{latest_y:04d}{latest_m:02d}"

def run_cmd(cmd: str, log: str) -> None:
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a") as f:
        f.write(f"\n[CMD] {cmd}\n")
        p = subprocess.Popen(cmd, shell=True, stdout=f, stderr=f, text=True)
        code = p.wait()
        if code != 0:
            f.write(f"[ERROR] Command failed with exit code {code}\n")
            raise RuntimeError(f"Command failed: {cmd}")

def ensure_dir(d: str) -> None:
    os.makedirs(d, exist_ok=True)

def make_lock(lockfile: str) -> None:
    if os.path.exists(lockfile):
        raise RuntimeError("Pipeline is already running.")
    with open(lockfile, "w") as f:
        f.write(str(os.getpid()))

def remove_lock(lockfile: str) -> None:
    if os.path.exists(lockfile):
        os.remove(lockfile)

def run_nmme_update(script_path: str, log: str) -> None:
    """Run NMME update shell script.

    Legacy wrapper used by nmme_pipeline.sh. New orchestrator should use
    runners/cli.py where possible.
    """
    run_cmd(f"{script_path}", log)


def run_makefcsts(script_path: str, fcstdate: str, log: str) -> None:
    """Run NMME products stage. Legacy wrapper. """
    run_cmd(f"{script_path} {fcstdate}", log)


def run_pycpt(script_path: str, fcstdate: str,
              region: str, season: str,
              lat0: float, lat1: float, lon0: float, lon1: float, log: str) -> None:
    """Run PyCPT for one region. Legacy wrapper, use run_pycpt_from_yaml.py instead."""
    cmd = (
        f"{script_path} "
        f"--config confignmme.yaml "
        f"--fcstdate {fcstdate} "
        f"--regname {region} "
        f"--lat_minmax {lat0} {lat1} "
        f"--lon_minmax {lon0} {lon1} "
        f"--training_season {season}"
    )
    run_cmd(cmd, log)

import pandas as pd

# ...existing code...

def decode_cf_safe(ds: xr.Dataset) -> xr.Dataset:
    """Decode CF times without throwing."""
    try:
        return xr.decode_cf(ds)
    except Exception:
        return ds

# ---- Add valid_times utility ----
def add_valid_times(ds: xr.Dataset, S_ts) -> xr.Dataset:
    """Add a 'valid' coordinate = S_ts + lead months (if both exist)."""
    if "lead" not in ds.dims:
        return ds
    valid_list = [S_ts + pd.DateOffset(months=int(l)) for l in ds["lead"].values]
    return ds.assign_coords(valid=("lead", valid_list))

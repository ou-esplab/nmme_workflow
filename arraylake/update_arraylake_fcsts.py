"""Append new NMME forecast start dates to Arraylake.

This NMME-specific updater mirrors the optional SubX Arraylake stage while
using the NMME config surface and input layout.
"""

from __future__ import annotations

import argparse
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import yaml
from arraylake import Client


def _load_token_from_env_file(env_file: Path) -> str | None:
    if not env_file.exists():
        return None
    with open(env_file, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "ARRAYLAKE_TOKEN":
                return value.strip().strip('"').strip("'")
    return None


def _normalize_date(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) == 8:
        return value[:6]
    if len(value) != 6:
        raise RuntimeError(f"Invalid --date value {value}; expected YYYYMM or YYYYMMDD")
    return value


def _model_entries(cfg: dict) -> list[dict]:
    entries = []
    for item in cfg.get("models") or []:
        if isinstance(item, str):
            entries.append({"name": item, "group": item})
        elif isinstance(item, dict) and item.get("name"):
            entries.append({"name": item["name"], "group": item.get("group", item["name"])})
    return entries


def _build_runtime_config() -> dict:
    parser = argparse.ArgumentParser(description="Update NMME Arraylake forecasts")
    parser.add_argument("--config", required=True, help="Path to workflow confignmme.yaml")
    parser.add_argument("--date", default=None, help="Optional target init date YYYYMM or YYYYMMDD")
    parser.add_argument("--dry-run", action="store_true", help="Scan pending updates without writing")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}

    al_cfg = cfg.get("arraylake") or {}
    models = _model_entries(cfg)
    if not models:
        raise RuntimeError("No models found in config.yaml under models")

    token = os.environ.get("ARRAYLAKE_TOKEN") or _load_token_from_env_file(Path(__file__).parent / ".env")
    if not token:
        raise RuntimeError("ARRAYLAKE_TOKEN not found in environment or arraylake/.env")

    runtime = {
        "token": token,
        "org_name": al_cfg.get("repo", "ou-subc/nmme-forecasts"),
        "input_path": al_cfg.get("input_root") or ((cfg.get("data") or {}).get("local") or {}).get("root"),
        "datatype": al_cfg.get("datatype", "forecast"),
        "branch_name": al_cfg.get("branch", "main"),
        "variables": al_cfg.get("variables") or ["prec", "tref", "sst", "h200", "h500"],
        "skip_models": set(al_cfg.get("skip_models") or []),
        "model_vars": al_cfg.get("model_vars") or {},
        "models": models,
        "date_filter": _normalize_date(args.date),
        "dry_run": bool(args.dry_run or os.environ.get("ARRAYLAKE_DRY_RUN") == "1"),
        "config_path": os.path.abspath(args.config),
    }
    if not runtime["input_path"]:
        raise RuntimeError("arraylake.input_root or data.local.root must be set in confignmme.yaml")
    return runtime


runtime = _build_runtime_config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

logger.info("=" * 70)
logger.info("Starting NMME Arraylake update at %s", datetime.now())
logger.info("=" * 70)

token = runtime["token"]
org_name = runtime["org_name"]
input_path = str(runtime["input_path"]).rstrip("/")
datatype = runtime["datatype"]
branch_name = runtime["branch_name"]
target_date = runtime["date_filter"]
dry_run = runtime["dry_run"]
cfg_variables = list(runtime["variables"])
allowed_variables = set(cfg_variables)
arraylake_model_vars = runtime.get("model_vars") or {}
skip_models = set(runtime.get("skip_models") or [])


def _group_name(model_name: str) -> str:
    return f"{model_name.lower()}-{datatype}"


def _candidate_files(model_name: str, variable: str):
    return sorted(Path(input_path, model_name, datatype, variable).glob(f"{variable}_{model_name}_*.nc"))


models_to_process = []
for model in runtime["models"]:
    name = model.get("name")
    if not name:
        continue
    if name in skip_models:
        logger.info("Skipping %s: listed in arraylake.skip_models", name)
        continue
    raw_model_vars = arraylake_model_vars.get(name, model.get("vars"))
    if raw_model_vars is None:
        effective_vars = list(cfg_variables)
    else:
        if isinstance(raw_model_vars, str):
            raw_model_vars = [raw_model_vars]
        effective_vars = [var for var in raw_model_vars if var in allowed_variables]
        if not effective_vars:
            logger.warning("Skipping model %s: no valid vars remain after filtering", name)
            continue
    models_to_process.append({"model": name, "group": model.get("group", name), "variables": effective_vars})

if not models_to_process:
    raise RuntimeError("No valid models to process after applying model and variable filtering")

logger.info("Config: %s", runtime["config_path"])
if target_date:
    logger.info("Date filter enabled: %s", target_date)
if dry_run:
    logger.info("Dry-run enabled: no Arraylake writes or commits will be performed")

try:
    client = Client(token=token)
except Exception as exc:  # pragma: no cover - runtime environment specific
    logger.error("Failed to initialize Arraylake client: %s", exc)
    logger.error(traceback.format_exc())
    raise SystemExit(1)


for entry in models_to_process:
    model_name = entry["model"]
    group_name = _group_name(model_name)

    logger.info("")
    logger.info("%s", "-" * 70)
    logger.info("Processing model: %s", model_name)
    logger.info("Arraylake group: %s", group_name)
    logger.info("%s", "-" * 70)

    try:
        repo = client.get_repo(org_name)
        branches = repo.list_branches()
    except Exception as exc:
        logger.error("Failed to open repository %s: %s", org_name, exc)
        continue

    if dry_run:
        logger.info("[DRY RUN] Would target branch '%s' in repo '%s'", branch_name, org_name)
    elif branch_name not in branches:
        logger.info("Creating branch '%s' from main...", branch_name)
        main_session = repo.readonly_session("main")
        repo.create_branch(branch_name, main_session.snapshot_id)

    read_session = repo.readonly_session("main")
    read_store = read_session.store

    try:
        existing_ds = xr.open_zarr(read_store, group=group_name, consolidated=False)
        group_exists = True
        existing_vars = list(existing_ds.data_vars)
        existing_times = pd.Index(existing_ds["S"].values)
    except FileNotFoundError:
        group_exists = False
        existing_vars = []
        existing_times = pd.Index([])
    except Exception as exc:
        logger.error("Failed to inspect existing group %s: %s", group_name, exc)
        logger.error(traceback.format_exc())
        continue

    ds_list = []
    total_files_found = 0
    total_files_read = 0
    total_files_skipped = 0

    for variable in entry["variables"]:
        files = _candidate_files(model_name, variable)
        total_files_found += len(files)
        if target_date:
            date_in_filename = f"{target_date[:4]}_{target_date[4:]}"
            files = [path for path in files if date_in_filename in path.name]
        if group_exists and variable in existing_vars:
            max_existing = pd.Timestamp(existing_times.max()).strftime("%Y%m") if len(existing_times) else "000000"
            def _file_yyyymm(p):
                parts = p.stem.split("_")
                return parts[-2] + parts[-1]  # e.g. "2026" + "06" → "202606"
            files = [path for path in files if _file_yyyymm(path) > max_existing]

        valid_ds_list = []
        for path in files:
            try:
                ds = xr.open_dataset(path, engine="netcdf4", chunks={}, decode_times=False)
                rename_dict = {}
                for old, new in (("lat", "Y"), ("latitude", "Y"), ("lon", "X"), ("longitude", "X"), ("init", "S"), ("member", "M"), ("lead", "L")):
                    if old in ds.dims or old in ds.coords:
                        rename_dict[old] = new
                if rename_dict:
                    ds = ds.rename(rename_dict)
                ds = ds[[variable]]
                if "S" in ds.coords:
                    s_var = ds["S"]
                    if np.issubdtype(s_var.dtype, np.integer) and "units" in s_var.attrs:
                        from xarray.coding.times import decode_cf_datetime
                        decoded = decode_cf_datetime(
                            s_var.values, s_var.attrs["units"],
                            s_var.attrs.get("calendar", "standard"),
                        )
                        ds = ds.assign_coords(S=pd.DatetimeIndex(decoded))
                    else:
                        ds["S"] = pd.to_datetime(s_var.values)
                if (~ds[variable].isel(M=0, L=0).isnull().all(dim=("Y", "X"))).compute().item():
                    valid_ds_list.append(ds)
                else:
                    total_files_skipped += 1
            except Exception as exc:
                total_files_skipped += 1
                logger.warning("Skipping %s: %s", path.name, exc)
        total_files_read += len(valid_ds_list)
        if valid_ds_list:
            ds_list.append(xr.concat(valid_ds_list, dim="S"))

    logger.info("File summary for %s: found=%d read=%d skipped=%d", model_name, total_files_found, total_files_read, total_files_skipped)

    if not ds_list:
        logger.info("No new data found for %s", model_name)
        continue

    ds_allvars = xr.merge(ds_list)
    for var in ds_allvars.data_vars:
        ds_allvars[var].attrs.pop("missing_value", None)
        ds_allvars[var].encoding = {}

    all_times = pd.Index(ds_allvars["S"].values)
    missing_vars = [var for var in entry["variables"] if var not in existing_vars]
    missing_times = all_times.difference(existing_times)

    if dry_run:
        logger.info("[DRY RUN] Would write %s with %d missing vars and %d new init times", group_name, len(missing_vars), len(missing_times))
        continue

    write_session = repo.writable_session(branch_name)
    write_store = write_session.store

    if not group_exists:
        ds_allvars.chunk({"L": len(ds_allvars["L"]), "Y": len(ds_allvars["Y"]), "X": len(ds_allvars["X"]), "M": 1, "S": 1}).to_zarr(write_store, zarr_format=3, consolidated=False, mode="w", group=group_name)
        write_session.commit(f"Initial write for {group_name}")
        continue

    if missing_vars and len(existing_times) > 0:
        subset_missing_vars = ds_allvars[missing_vars].reindex(S=existing_times)
        subset_missing_vars.chunk({"L": len(subset_missing_vars["L"]), "Y": len(subset_missing_vars["Y"]), "X": len(subset_missing_vars["X"]), "M": 1, "S": 1}).to_zarr(write_store, zarr_format=3, consolidated=False, mode="a", group=group_name)
        write_session.commit(f"Add missing variables for {group_name}")

    if len(missing_times) > 0 and len(existing_times) > 0:
        subset_missing_times = ds_allvars.reindex(S=missing_times)
        subset_missing_times.chunk({"L": len(subset_missing_times["L"]), "Y": len(subset_missing_times["Y"]), "X": len(subset_missing_times["X"]), "M": 1, "S": 1}).to_zarr(write_store, zarr_format=3, consolidated=False, mode="a", append_dim="S", group=group_name)
        write_session.commit(f"Append new init times for {group_name}")

logger.info("Arraylake update complete")
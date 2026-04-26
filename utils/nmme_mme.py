# utils/nmme_mme.py

from xml.parsers.expat import model

import xarray as xr
import pandas as pd
from pathlib import Path

from utils.nmme_metadata import init_models
from utils.nmme_anomalies import model_anomalies_for_month


def build_mme_for_month(
    data_root: Path,
    clim_root: Path,
    init_yyyymm: str,
) -> xr.Dataset:
    """
    Build per-model anomalies and the MME for one initialization month.

    Rules:
      - Operates on preprocessed data (init already selected)
      - Uses valid(L) everywhere; never computes time
      - Skips missing models/variables
      - Aborts only if NO data is produced at all
    """

    models, _, _, _ = init_models()

    per_model = []
    model_names = []

    any_data = False  # <-- global success flag

    # --------------------------------------------------
    # Loop over models
    # --------------------------------------------------
    for m in models:
        model = m["model"]
        varnames = m["varnames"]
        levs = m["levstrs"]

        var_datasets = []

        # -------------------------------
        # Loop over variables for model
        # -------------------------------
        for var, lev in zip(varnames, levs):
            ds_var = model_anomalies_for_month(
                data_root=data_root,
                clim_root=clim_root,
                model=model,
                varname=var,
                levstr=lev,
                init_yyyymm=init_yyyymm,
            )

            print(
                f"[DEBUG] model={model} var={var} "
                f"-> ds_var={'OK' if ds_var is not None else 'None'}"
            )

            if ds_var is None:
                continue

            # Per-model ensemble mean (variable-level)
            if "member" in ds_var.dims:
                ds_var[f"{var}_ensmean"] = ds_var[var].mean("member", skipna=True)
                ds_var[f"{var}_ensmean"].attrs["description"] = (
                    "Per-model ensemble mean"
                )

            var_datasets.append(ds_var)
            any_data = True  # <-- mark success

        # Skip model if no variables succeeded
        if not var_datasets:
            print(
                f"[WARN] No variables contributed for model={model}; skipping model"
            )
            continue

        # -------------------------------
        # Merge variables for this model
        # -------------------------------
        ds_model = xr.merge(var_datasets, compat="override")

        # Ensemble count ONCE per model
        if "member" in ds_model.dims:
            ds_model["nens"] = ds_model["member"].count("member")
            ds_model["nens"].attrs["description"] = (
                "Number of ensemble members contributing"
            )

        per_model.append(ds_model)
        model_names.append(model)

    # --------------------------------------------------
    # FINAL ABORT CHECK (ONLY PLACE IT BELONGS)
    # --------------------------------------------------
    if not any_data:
        raise RuntimeError(
            f"No valid NMME data available for init {init_yyyymm}"
        )

    # --------------------------------------------------
    # Combine models
    # --------------------------------------------------
    ds_models = xr.concat(
        per_model,
        dim=xr.IndexVariable("model", model_names),
    )

    # --- Compute and append MME (multi-model ensemble mean) ---
    mme_vars = {}
    for var in ds_models.data_vars:
        if var.endswith("_ensmean"):
            # Take mean across model dimension, skipna for robustness
            mme_vars[var] = ds_models[var].mean(dim="model", skipna=True)
    if mme_vars:
        mme_ds = xr.Dataset({k: v.expand_dims("model") for k, v in mme_vars.items()})
        mme_ds = mme_ds.assign_coords(model=(["model"], ["MME"]))
        # Copy over coordinates and attrs from ds_models
        for coord in ds_models.coords:
            if coord not in mme_ds.coords:
                mme_ds = mme_ds.assign_coords({coord: ds_models[coord]})
        mme_ds.attrs["init_yyyymm"] = init_yyyymm
        # Concatenate MME to the original models
        ds_models = xr.concat([ds_models, mme_ds], dim="model")

    ds_models.attrs["init_yyyymm"] = init_yyyymm

    # Diagnostic: check if NOAA-SFS is missing
    if "NOAA-SFS" not in ds_models["model"].values:
        print(f"[DIAG] WARNING: NOAA-SFS is missing from the final dataset for init {init_yyyymm}")
    else:
        print(f"[DIAG] NOAA-SFS is present in the final dataset for init {init_yyyymm}")

    return ds_models
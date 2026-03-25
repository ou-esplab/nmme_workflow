# utils/nmme_mme.py

import xarray as xr
import pandas as pd
from pathlib import Path

from utils.nmme_metadata import init_models
from utils.nmme_anomalies import model_anomalies_for_month
from utils.nmme_normalize import add_valid_times


def build_mme_for_month(
    data_root: Path,
    clim_root: Path,
    target: pd.Timestamp,
) -> xr.Dataset:
    """
    Build per-model anomalies and the MME for one init month.
    Retains ensemble members and per-model ensemble means.
    """

    models, _, _, _ = init_models()

    per_model = []
    model_names = []

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
                target=target,
            )

            if ds_var is None:
                continue

            # ✅ Per-model ensemble mean (variable-level)
            if "M" in ds_var.dims:
                ds_var[f"{var}_ensmean"] = ds_var[var].mean("M", skipna=True)
                ds_var[f"{var}_ensmean"].attrs["description"] = (
                    "Per-model ensemble mean"
                )

            var_datasets.append(ds_var)

        # Skip model if no variables succeeded
        if not var_datasets:
            continue

        # -------------------------------
        # Merge variables for this model
        # -------------------------------
        ds_model = xr.merge(var_datasets, compat="override")

        # ✅ Ensemble count ONCE per model
        if "M" in ds_model.dims:
            ds_model["nens"] = ds_model["M"].count("M")
            ds_model["nens"].attrs["description"] = (
                "Number of ensemble members contributing"
            )

        per_model.append(ds_model)
        model_names.append(model)

    if not per_model:
        raise RuntimeError(
            f"No valid NMME data available for {target:%Y%m}"
        )

    # --------------------------------------------------
    # Combine models
    # --------------------------------------------------
    ds_models = xr.concat(
        per_model,
        dim=xr.IndexVariable("model", model_names),
    )

    # --------------------------------------------------
    # Derive valid time ONCE globally
    # --------------------------------------------------
    S_cf = ds_models["S"].values[0]
    S_ts = pd.Timestamp(S_cf.year, S_cf.month, 1)
    ds_models = add_valid_times(ds_models, S_ts)

    # --------------------------------------------------
    # Compute MME from ensemble means ONLY
    # --------------------------------------------------
    ensmean_vars = [v for v in ds_models.data_vars if v.endswith("_ensmean")]

    ds_mme = ds_models[ensmean_vars].mean(
        "model", skipna=True, keep_attrs=True
    )
    ds_mme = ds_mme.assign_coords(model="MME")
    
    # Compute total ensemble count for MME as sum across models
    mme_nens = ds_models["nens"].sum("model", skipna=True)

    ds_mme["nens"] = mme_nens
    ds_mme["nens"].attrs["description"] = (
        "Total number of ensemble members contributing to MME"
    )

    # --------------------------------------------------
    # Final concat: models + MME
    # --------------------------------------------------
    return xr.concat(
        [ds_models, ds_mme],
        dim="model",
    )
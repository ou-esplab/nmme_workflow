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
        # Normalize variable grids within this model
        # -------------------------------
        # Some models (e.g., NOAA-SFS) can provide variables on different
        # native grids across variables. Interpolate all successful variables
        # to the coarsest available Y/X grid for stable merging.
        yx_candidates = [
            i for i, ds_i in enumerate(var_datasets)
            if "Y" in ds_i.sizes and "X" in ds_i.sizes
        ]
        if yx_candidates:
            target_idx = min(
                yx_candidates,
                key=lambda i: var_datasets[i].sizes["Y"] * var_datasets[i].sizes["X"],
            )
            target_Y = var_datasets[target_idx]["Y"].values
            target_X = var_datasets[target_idx]["X"].values

            normalized_vars = []
            for ds_i in var_datasets:
                if (
                    "Y" in ds_i.sizes and "X" in ds_i.sizes
                    and (
                        ds_i.sizes["Y"] != len(target_Y)
                        or ds_i.sizes["X"] != len(target_X)
                    )
                ):
                    ds_i = ds_i.interp(Y=target_Y, X=target_X, method="linear")
                normalized_vars.append(ds_i)
            var_datasets = normalized_vars

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
    # Normalize grids: regrid models with different Y/X
    # resolution to match the first model's grid.
    # (e.g., NOAA-SFS is 0.5° while other NMME models
    # are 1°; linear interpolation downsamples to 1°.)
    # --------------------------------------------------
    ref_Y = per_model[0]["Y"].values
    ref_X = per_model[0]["X"].values
    normalized = []
    for ds in per_model:
        if ds.sizes["Y"] != len(ref_Y) or ds.sizes["X"] != len(ref_X):
            ds = ds.interp(Y=ref_Y, X=ref_X, method="linear")
        normalized.append(ds)
    per_model = normalized

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
    if hasattr(S_cf, "year") and hasattr(S_cf, "month"):
        S_ts = pd.Timestamp(S_cf.year, S_cf.month, 1)
    else:
        S_parsed = pd.to_datetime(S_cf)
        S_ts = pd.Timestamp(S_parsed.year, S_parsed.month, 1)
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
import xarray as xr
import pandas as pd
from pathlib import Path

from utils.nmme_metadata import init_models
from utils.nmme_anomalies import model_anomalies_for_month

def build_mme_for_month(
    data_root: Path,
    clim_root: Path,
    target: pd.Timestamp,
) -> xr.Dataset:

    models, _, _, _ = init_models()
    per_model = []

    for m in models:
        ds_vars = []
        for v, lev in zip(m["varnames"], m["levstrs"]):
            ds = model_anomalies_for_month(
                data_root, clim_root, m["model"], v, lev, target
            )
            if ds is not None:
                ds_vars.append(ds)

        if ds_vars:
            per_model.append(xr.merge(ds_vars))

    if not per_model:
        raise RuntimeError("No models available")

    ds_models = xr.concat(per_model, dim="model")
    mme = ds_models.mean("model").assign_coords(model="MME")
    return xr.concat([ds_models, mme], dim="model")

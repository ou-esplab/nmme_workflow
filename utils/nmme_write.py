from mimetypes import init
from pathlib import Path
import pandas as pd
import xarray as xr
from utils.nmme_metadata import init_models
from pandas import Period


def rolling_seasonal_means(ds, window=3):
    """
    ds: Dataset or DataArray with dimension 'lead'
    returns Dataset with new dimension 'season_window'
    """
    out = []
    labels = []

    nlead = ds.sizes["lead"]

    for k in range(nlead - window + 1):
        w = ds.isel(lead=slice(k, k + window)).mean("lead")
        out.append(w)

        # season label 
        init = ds.attrs["init_yyyymm"]  # e.g. "202604"
        label = (pd.Period(init, freq="M") + k).strftime("%b")
        labels.append(label)

    ds_seas = xr.concat(out, dim="season_window")
    ds_seas = ds_seas.assign_coords(season=("season_window", labels))

    return ds_seas

def _set_global_attrs(ds: xr.Dataset, fcstdate: str, units: str) -> xr.Dataset:
    ds.attrs["title"] = "NMME monthly/seasonal ensemble-mean anomalies"
    ds.attrs["forecast_init"] = fcstdate
    ds.attrs["units"] = units
    ds.attrs["source"] = "nmme_workflow"
    return ds


def _normalize_spatial_coords(ds: xr.Dataset) -> xr.Dataset:
    rename = {}
    if "X" in ds.coords:
        rename["X"] = "lon"
    if "Y" in ds.coords:
        rename["Y"] = "lat"
    if rename:
        ds = ds.rename(rename)
    return ds


def nmme_write(ds_fcst: xr.Dataset, fcstdate: str):
    """
    Write monthly and seasonal NMME ensemble-mean products.

    Assumes:
      - ds_fcst contains valid(L)
      - ds_fcst already encodes which models/variables are available
    """

    print("WRITING DATA")

    # ---------------------------
    # Hard invariants
    # ---------------------------
    if "valid" not in ds_fcst.coords:
        raise KeyError(
            "nmme_write requires 'valid(L)' coordinate produced by preprocess"
        )

    if "L" in ds_fcst.dims:
        lead_dim = "L"
    elif "lead" in ds_fcst.dims:
        lead_dim = "lead"
    else:
        raise RuntimeError(
            f"No lead dimension in ds_fcst (dims={ds_fcst.dims})"
        )

    # Metadata
    models_meta, vname_map, _, unit_map = init_models()

    # ---------------------------
    # Drive products from DATA, not config
    # ---------------------------
    ensmean_vars = [
        v for v in ds_fcst.data_vars
        if v.endswith("_ensmean")
    ]

    if not ensmean_vars:
        raise RuntimeError("nmme_write: no ensemble-mean variables found")

    # ---------------------------
    # Variable loop (data-driven)
    # ---------------------------
    for v_ens in ensmean_vars:
        v = v_ens.replace("_ensmean", "")
        units = unit_map.get(v, "unknown")

        ds_model_list = []

        da = ds_fcst[v_ens]

        # ---------------------------
        # Model loop (data-driven)
        # ---------------------------
        for model in da["model"].values:
            ds = (
                da.sel(model=model)
                  .to_dataset(name=model)
                  .squeeze(drop=True)
            )

            ds[model].attrs["long_name"] = f"{model} {fcstdate}"
            ds[model].attrs["units"] = units
            ds_model_list.append(ds.reset_coords(drop=True))

        if not ds_model_list:
            # This should not happen logically, but keep it safe
            print(f"[nmme_write] {v} not written: no contributing models")
            continue

        # ---------------------------
        # Merge models
        # ---------------------------
        ds_models = xr.merge(ds_model_list, compat="override")
        ds_models = _set_global_attrs(ds_models, fcstdate, units)

        # ---------------------------
        # MONTHLY OUTPUT
        # ---------------------------
        
        if "lon" in ds_models.coords:
            ds_models = (
                ds_models
                .assign_coords(lon=(((ds_models["lon"] + 180) % 360) - 180))
                .sortby("lon")
            )

        ofname_mon = (
            f"/data/esplab/shared/model/initialized/nmme/forecast/monthly/"
            f"{fcstdate}/data/"
            f"NMME_fcst_{fcstdate}.anom.monthly.{v}.emean.nc"
        )
        ds_models.to_netcdf(ofname_mon)

        # ---------------------------
        # SEASONAL OUTPUT (derived from valid)
        # ---------------------------
        

        # ds_fcst contains anomalies as (lon, lat, lead)
        ds_seas = rolling_seasonal_means(ds_fcst, window=3)
        ds_seas["season"].attrs["long_name"] = "Forecast Valid Season"

        ofname_seas = (
            f"/data/esplab/shared/model/initialized/nmme/forecast/seasonal/"
            f"{fcstdate}/data/"
            f"NMME_fcst_{fcstdate}.anom.seas.{v}.emean.nc"
        )
        ds_seas.to_netcdf(ofname_seas)

        print(f"[nmme_write] wrote {v}")
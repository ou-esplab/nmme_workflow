from pathlib import Path
import xarray as xr
from utils.nmme_metadata import init_models


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

def nmme_write(ds_fcst, fcstdate):

    print("WRITING DATA")

    # Get model / variable metadata (unchanged)
    models_list, vname_map, all_plevstrs, unit_map = init_models()
    all_varnames = list(vname_map.keys())

    # Loop over variables
    for v, p in zip(all_varnames, all_plevstrs):
        u = unit_map.get(v, "unknown")

        ds_model_list = []

        # Loop over models
        for nmme_model in models_list:
            varnames = nmme_model["varnames"]
            model = nmme_model["model"]

            # Skip if this model does not provide this variable
            if v not in varnames:
                continue

            # ---------- Individual model ensemble mean ----------
            try:
                ds = (
                    ds_fcst[f"{v}_ensmean"]
                    .sel(model=model)
                    .to_dataset(name=model)
                    .squeeze(drop=True)
                )
            except KeyError:
                print(f"[nmme_write][WARN] {v}_ensmean missing for model {model}")
                continue

            ds[model].attrs["long_name"] = f"{model} {fcstdate}"
            ds[model].attrs["units"] = u
            ds_model_list.append(ds.reset_coords(drop=True))

        # ---------- MME ----------
        if "MME" in ds_fcst["model"].values:
            try:
                ds = (
                    ds_fcst[f"{v}_ensmean"]
                    .sel(model="MME")
                    .to_dataset(name="MME")
                    .squeeze(drop=True)
                )
                ds["MME"].attrs["long_name"] = f"MME {fcstdate}"
                ds["MME"].attrs["units"] = u
                ds_model_list.append(ds.reset_coords(drop=True))
            except KeyError:
                print(f"[nmme_write][WARN] {v}_ensmean missing for MME")

        # Skip variable if nothing to write
        if not ds_model_list:
            print(f"[nmme_write] {v} not written: no models available")
            continue

        # ---------- Output paths (UNCHANGED) ----------
        ofname_mon = (
            f"/data/esplab/shared/model/initialized/nmme/forecast/monthly/"
            f"{fcstdate}/data/"
            f"NMME_fcst_{fcstdate}.anom.monthly.{v}_{p}.emean.nc"
        )

        ofname_seas = (
            f"/data/esplab/shared/model/initialized/nmme/forecast/seasonal/"
            f"{fcstdate}/data/"
            f"NMME_fcst_{fcstdate}.anom.seas.{v}_{p}.emean.nc"
        )

        # ---------- Merge models ----------
        ds_models = xr.merge(ds_model_list, compat="override")

        # ---------- Global attributes ----------
        ds_models = _set_global_attrs(ds_models, fcstdate, u)

        # ---------- Time axis from valid months (unchanged) ----------
        ds_models["lead"] = ds_fcst["valid"].values
        ds_models = ds_models.rename({"lead": "time"})
        ds_models["time"].attrs["standard_name"] = "time"
        ds_models["time"].attrs["long_name"] = "Forecast Valid Month"

        # ---------- Longitude shift (unchanged) ----------
        ds_models = _normalize_spatial_coords(ds_models)
        if "lon" in ds_models.coords:
            ds_models = ds_models.assign_coords(
                lon=(((ds_models["lon"] + 180) % 360) - 180)
            )
            ds_models = ds_models.sortby(ds_models["lon"])

        # ---------- Write monthly ----------
        ds_models.to_netcdf(ofname_mon)

        # ---------- Write seasonal (unchanged logic) ----------
        ds_seas = ds_models.groupby("time.season").mean()
        ds_seas["season"].attrs["long_name"] = "Forecast Valid Season"
        ds_seas.to_netcdf(ofname_seas)
from pathlib import Path
import xarray as xr

def nmme_write(ds_fcst, fcstdate):

    print("WRITING DATA")

    # Get model / variable metadata (unchanged)
    models_list, all_varnames, all_plevstrs, all_units = initModels()

    # Loop over variables
    for v, p, u in zip(all_varnames, all_plevstrs, all_units):

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

        # ---------- Global attributes (unchanged) ----------
        ds_models = setattrs(ds_models, fcstdate, u)

        # ---------- Time axis from valid months (unchanged) ----------
        ds_models["lead"] = ds_fcst["valid"].values
        ds_models = ds_models.rename({"lead": "time"})
        ds_models["time"].attrs["standard_name"] = "time"
        ds_models["time"].attrs["long_name"] = "Forecast Valid Month"

        # ---------- Longitude shift (unchanged) ----------
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
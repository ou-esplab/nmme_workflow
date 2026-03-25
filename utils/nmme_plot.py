import os
import numpy as np
import xarray as xr
import pandas as pd
import cftime
import proplot as pplt
import cartopy.feature as cfeature

from utils.nmme_plot_params import initPlotParams
from utils.nmme_metadata import init_models

MODEL_PLOT_LOCS = {
"NASA-GEOSS2S": 0,
"CanESM5": 1,
"GFDL-SPEAR": 2,
"GEM5.2-NEMO": 3,
"NCEP-CFSv2": 4,
"COLA-RSMAS-CCSM4": 5,
"MME": 6,
}
    
# ============================================================
# Time helpers (robust to cftime / pandas)
# ============================================================

def _get_fcstdate_str(ds):
    """
    Return forecast initialization date as YYYYMM.
    Works for cftime and pandas-based S.
    """
    S = ds["S"].values[0]
    return f"{S.year:04d}{S.month:02d}"


def _valid_time_for_lead(ds, ilead):
    """
    Return pandas.Timestamp for valid time at a given lead index.
    """
    S = ds["S"].values[0]
    lead = int(ds["L"].values[ilead])

    if isinstance(S, cftime.datetime):
        base = pd.Timestamp(S.year, S.month, 1)
    else:
        base = pd.to_datetime(S)

    return base + pd.DateOffset(months=lead)


# ============================================================
# Selection / slicing helpers
# ============================================================

def _select_model_and_lead(ds, model, ilead):
    """
    Safely select one model and one lead.
    Returns None if the lead is unavailable.
    """
    ds_sel = (
        ds.sel(model=model)
          .dropna(dim="L", how="all")
          .squeeze()
    )

    if "L" not in ds_sel.dims:
        return None

    if ds_sel["L"].values.max() < ilead:
        return None

    return ds_sel.sel(L=ilead)


# ============================================================
# Plotting helpers
# ============================================================

def _plot_single_panel(
    ax, ds, var, sf, clevs, cmap, norm, title,
    mproj, lonreg, latreg, statescolor
):
    """
    Plot a single map panel (UNCHANGED plotting semantics).
    """
    data = data = ds[f"{var}_ensmean"].values * sf

    m = ax.contourf(
        ds["X"], ds["Y"], data,
        levels=clevs,
        cmap=cmap,
        extend="both",
        norm=norm
    )

    if mproj == "robin":
        ax.format(coast=True, grid=False, borders=True,
                  borderscolor='gray5', title=title)
    else:
        ax.format(coast=True, lonlim=lonreg, latlim=latreg,
                  grid=False, borders=True,
                  borderscolor='gray5', title=title)

    ax.add_feature(cfeature.STATES, edgecolor=statescolor)
    return m


# ============================================================
# Main plotting entry point
# ============================================================

def nmme_plot(ds, path):
    """
    Generate NMME forecast anomaly maps.
    Preserves original map layout and filenames.
    """
    var_params_dict, reg_params_dict = initPlotParams()
    models_meta, _, _, _ = init_models()
    model_names = [m["model"] for m in models_meta]

    MODEL_PLOT_LOCS = {
    "NASA-GEOSS2S": 0,
    "CanESM5": 1,
    "GFDL-SPEAR": 2,
    "GEM5.2-NEMO": 3,
    "NCEP-CFSv2": 4,
    "COLA-RSMAS-CCSM4": 5,
    "MME": 6,
    }

    os.makedirs(path, exist_ok=True)
    pplt.rc.savefigdpi = 100

    # ----------------------------------
    # Variable loop
    # ----------------------------------
    for var_params in var_params_dict:
        v = var_params["name"]
        print(f"Plotting variable: {v}")

        # Mask land for SST
        if v == "sst":
            ds_land = xr.open_dataset(
                "/data/esplab/shared/model/initialized/nmme/hindcast/monthly/land_cover.nc"
            )
            ds[v] = xr.where(ds_land["land"] == 1, np.nan, ds[v])

        # ----------------------------------
        # Region loop
        # ----------------------------------
        for reg_name in var_params["regions"]:
            reg = next(r for r in reg_params_dict if r["name"] == reg_name)
            figname = path / f"{var_params['outname']}{reg['name']}"

            _plot_variable_for_region(
                ds, v, var_params, reg,
                models_meta, figname
            )


def _plot_variable_for_region(
    ds, v, var_params, reg, models_list, figname
):
    """
    Plot all leads for one variable/region pair.
    """

    fcstdate = _get_fcstdate_str(ds)
    max_leads = 9

    for ilead, lead in enumerate(ds["L"].values[:max_leads]):
        grid = [[1, 2, 3],
                [4, 5, 6],
                [7, 0, 0]]

        f, axs = pplt.subplots(
            grid,
            proj=reg["mproj"],
            proj_kw={"lon_0": reg["clon"]},
            width=11,
            height=8.5
        )

        valid_ts = _valid_time_for_lead(ds, ilead)
        fcstmonth_str = valid_ts.strftime("%b")

        suptitle = (
            f"NMME Forecast {fcstmonth_str} {var_params['label']} "
            f"Anomalies ({var_params['units']}): "
            f"{lead} Months Lead"
        )

        sub_nens = 0

        # -------------------------------
        # Model loop
        # -------------------------------
        for model in ds["model"].values:
            
            # Get plot location for each model
            iplot = MODEL_PLOT_LOCS.get(model)
            if iplot is None:
                continue
            
            # Select model and lead
            ds_sel = _select_model_and_lead(ds, model, ilead)
            if ds_sel is None:
                continue
            print("LOOKLING FOR NENS: ",ds_sel)
            nens = int(ds_sel["nens"].values)

            if model != "MME":
                sub_nens += nens
                title = f"{model} (IC: {fcstdate}; {nens} Ens )"
            else:
                title = f"MME (IC: {fcstdate}; {sub_nens} Ens )"

            norm = pplt.Norm("diverging", vcenter=0)

            m = _plot_single_panel(
                axs[iplot], ds_sel, v,
                var_params["scale_factor"],
                var_params["clevs"],
                var_params["cmap"],
                norm,
                title,
                reg["mproj"],
                reg["lons"],
                reg["lats"],
                reg["state_colors"]
            )

        f.format(suptitle=suptitle)
        f.colorbar(m, loc="b", label=var_params["units"], length=0.7)

        out = f"{figname}Month{ilead}.png"
        print(f"Writing figure: {out}")
        f.save(out)
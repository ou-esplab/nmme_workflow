import os
import numpy as np
import xarray as xr
import pandas as pd
import cftime
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from pandas import Period

from utils.nmme_plot_params import initPlotParams
from utils.nmme_metadata import init_models

PREFERRED_MODEL_ORDER = [
    "NASA-GEOSS2S",
    "CanESM5",
    "GFDL-SPEAR",
    "GEM5.2-NEMO",
    "NCEP-CFSv2",
    "COLA-RSMAS-CCSM4",
    "COLA-RSMAS-CESM1",
    "NOAA-SFS",
    "MME",
]

# ============================================================
# Selection helpers
# ============================================================
def _cmap_from_name(name):
    cmap_map = {
        "ColdHot": "coolwarm",
        "DryWet": "BrBG",
        "NegPos": "RdBu_r",
    }
    return cmap_map.get(name, name)

def _select_model_and_lead(ds, model, ilead):
    """
    Safely select one model and one lead.
    """
    ds_sel = (
        ds.sel(model=model)
          .dropna(dim="lead", how="all")
    )

    if "lead" not in ds_sel.dims:
        return None

    if ds_sel["lead"].values.max() < ilead:
        return None

    print(f"[DIAG] ds_sel dims BEFORE isel: {ds_sel.dims}")
    print(f"[DIAG] vars BEFORE isel: {list(ds_sel.data_vars)}")

    tmp = ds_sel.isel(lead=ilead, drop=False)

    print(f"[DIAG] vars AFTER isel: {list(tmp.data_vars)}")
    print(f"[DIAG] nens AFTER isel? {'nens' in tmp}")

    return tmp
    #return ds_sel.sel(lead=ilead,drop=False)


# ============================================================
# Plotting helpers
# ============================================================

def _plot_single_panel(
    ax, ds, var, sf, clevs, cmap, norm, title,
    mproj, lonreg, latreg, statescolor, transform
):
    # ✅ Keep the DataArray
    da = ds[f"{var}_ensmean"]

    # ✅ Detect coordinate names robustly
    lat_dim = "lat" if "lat" in da.sizes else "latitude"
    lon_dim = "lon" if "lon" in da.sizes else "longitude"

    # ✅ Plot using the DataArray’s own coordinates
    m = ax.contourf(
        da[lon_dim],
        da[lat_dim],
        da.values * sf,
        levels=clevs,
        cmap=_cmap_from_name(cmap),
        norm=norm,
        extend="both",
        transform=transform,
    )

    if mproj == "robin":
        ax.set_global()
    else:
        ax.set_extent(
            [lonreg[0], lonreg[1], latreg[0], latreg[1]],
            crs=transform,
        )

    ax.coastlines(linewidth=0.8)
    ax.add_feature(cfeature.BORDERS, linewidth=0.6)

    if mproj != "robin":
        ax.add_feature(cfeature.STATES, linewidth=0.4, edgecolor=statescolor)

    ax.set_title(title, fontsize=9)
    return m


def _proj_from_region(reg):
    if reg["mproj"] == "robin":
        return ccrs.Robinson(reg.get("clon", 0))
    return ccrs.PlateCarree(reg.get("clon", 0))


def _build_model_plot_locs(ds):
    present = [str(m) for m in ds["model"].values]
    ordered = [m for m in PREFERRED_MODEL_ORDER if m in present]
    extras = [m for m in present if m not in ordered]
    ordered.extend(extras)
    return {m: i for i, m in enumerate(ordered[:9])}


def _balanced_levels_and_ticks(var_params):
    base_levels = np.asarray(var_params["clevs"], float)
    vmax = float(np.nanmax(np.abs(base_levels)))
    n = len(base_levels) | 1
    levels = np.linspace(-vmax, vmax, n)
    
    norm = TwoSlopeNorm(vmin=-vmax, vcenter=0.0, vmax=vmax)
    ticks = np.linspace(-vmax, vmax, 9)
    return levels, norm, ticks


# ============================================================
# Main plotting entry point
# ============================================================

def nmme_plot(ds, path):
    """
    Generate NMME forecast anomaly maps.
    """
    var_params_dict, reg_params_dict = initPlotParams()
    model_plot_locs = _build_model_plot_locs(ds)

    os.makedirs(path, exist_ok=True)
    fcstdate = ds.attrs["init_yyyymm"]

    for var_params in var_params_dict:
        v = var_params["name"]
        print(f"Plotting variable: {v}")

        for reg_name in var_params["regions"]:
            reg = next(r for r in reg_params_dict if r["name"] == reg_name)
            figname = path / f"{var_params['outname']}{reg['name']}"

            _plot_variable_for_region(
                ds, v, var_params, reg, figname, model_plot_locs, fcstdate
            )


def _plot_variable_for_region(
    ds, v, var_params, reg, figname, model_plot_locs, fcstdate
):
    max_leads = 9
    clevs, norm, ticks = _balanced_levels_and_ticks(var_params)
    proj = _proj_from_region(reg)
    data_crs = ccrs.PlateCarree()

    for ilead in range(min(max_leads, ds.sizes["lead"])):
        
        init = ds.attrs["init_yyyymm"]  # e.g. "202604"
        p0 = pd.Period(init, freq="M")
        fcstmonth_str = str(p0 + ilead)

        fig, axs = plt.subplots(
            3, 3,
            figsize=(11, 8.5),
            subplot_kw={"projection": proj},
            constrained_layout=True,
        )
        axs_flat = axs.flatten()

        for i, ax in enumerate(axs_flat):
            ax.set_visible(i in model_plot_locs.values())

        sub_nens = 0

        for model, iplot in model_plot_locs.items():
            print("\n[DIAG] Global lead coordinate:")
            print("  lead values:", ds["lead"].values)
            print("  lead dtype :", ds["lead"].dtype)
            print("  lead size  :", ds.sizes["lead"])
            ds_sel = _select_model_and_lead(ds, model, ilead)
            if ds_sel is None:
                continue

            # ✅ SST diagnostic belongs HERE
            if v == "sst":
                da = ds_sel[f"{v}_ensmean"]
                lat_dim = "lat" if "lat" in da.sizes else "latitude"
                lon_dim = "lon" if "lon" in da.sizes else "longitude"


            nens_val = ds_sel["nens"].values
            if np.isnan(nens_val):
                print(f"[DIAG] NaN nens detected: model={model}, variable={v}, lead={ilead}, region={reg['name']}, init={fcstdate}")
                nens = "NaN"
            else:
                nens = int(nens_val)

            if model != "MME":
                if isinstance(nens, int):
                    sub_nens += nens
                title = f"{model} (IC: {fcstdate}; {nens} Ens)"
            else:
                title = f"MME (IC: {fcstdate}; {sub_nens} Ens)"

            m = _plot_single_panel(
                axs_flat[iplot],
                ds_sel,
                v,
                var_params["scale_factor"],
                clevs,
                var_params["cmap"],
                norm,
                title,
                reg["mproj"],
                reg["lons"],
                reg["lats"],
                reg["state_colors"],
                data_crs,
            )

        fig.suptitle(
            f"NMME Forecast {fcstmonth_str} {var_params['label']} "
            f"Anomalies ({var_params['units']}): {ilead}‑Month Lead",
            fontsize=12,
        )

        fig.colorbar(
            m,
            ax=[axs_flat[i] for i in model_plot_locs.values()],
            orientation="horizontal",
            fraction=0.04,
            pad=0.04,
            ticks=ticks,
            label=var_params["units"],
        )

        out = f"{figname}Month{ilead}.png"
        print(f"Writing figure: {out}")
        fig.savefig(out, dpi=100)
        plt.close(fig)
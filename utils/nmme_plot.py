import os
import numpy as np
import xarray as xr
import pandas as pd
import cftime
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import cartopy.crs as ccrs
import cartopy.feature as cfeature

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
# Time helpers (robust to cftime / pandas)
# ============================================================

def _get_fcstdate_str(ds):
    """
    Return forecast initialization date as YYYYMM.
    Works for cftime and pandas-based S.
    """
    S = ds["S"].values[0]
    if isinstance(S, cftime.datetime):
        return f"{S.year:04d}{S.month:02d}"
    ts = pd.to_datetime(S)
    return f"{ts.year:04d}{ts.month:02d}"


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
    mproj, lonreg, latreg, statescolor, transform
):
    """
    Plot a single map panel (UNCHANGED plotting semantics).
    """
    data = ds[f"{var}_ensmean"].values * sf

    m = ax.contourf(
        ds["X"], ds["Y"], data,
        levels=clevs,
        cmap=cmap,
        extend="both",
        norm=norm,
        transform=transform,
    )

    if mproj == "robin":
        ax.set_global()
    else:
        ax.set_extent([lonreg[0], lonreg[1], latreg[0], latreg[1]], crs=transform)

    ax.coastlines(linewidth=0.8)
    ax.add_feature(cfeature.BORDERS, edgecolor="0.4", linewidth=0.6)
    ax.set_title(title, fontsize=9)

    state_edge = "gray" if statescolor == "gray5" else statescolor
    if mproj != "robin":
        ax.add_feature(cfeature.STATES, edgecolor=state_edge, linewidth=0.4)

    return m


def _proj_from_region(reg):
    mproj = reg["mproj"]
    clon = reg.get("clon", 0)
    if mproj == "robin":
        return ccrs.Robinson(central_longitude=clon)
    if mproj == "npstere":
        return ccrs.NorthPolarStereo(central_longitude=clon)
    return ccrs.PlateCarree(central_longitude=clon)


def _cmap_from_name(name):
    cmap_map = {
        "ColdHot": "coolwarm",
        "DryWet": "BrBG",
        "NegPos": "RdBu_r",
    }
    return cmap_map.get(name, name)


def _build_model_plot_locs(ds: xr.Dataset) -> dict[str, int]:
    """
    Build panel slot indices for models present in the dataset.
    Keeps a stable preferred order and supports NOAA-SFS.
    """
    present = [str(m) for m in ds["model"].values.tolist()]

    ordered = [m for m in PREFERRED_MODEL_ORDER if m in present]
    extras = [m for m in present if m not in ordered]
    ordered.extend(extras)

    # 3x3 panel grid supports at most 9 models.
    return {m: i for i, m in enumerate(ordered[:9])}


def _balanced_levels_and_ticks(var_params: dict) -> tuple[np.ndarray, TwoSlopeNorm, np.ndarray]:
    """
    Build symmetric contour levels/ticks centered on zero.
    """
    base_levels = np.asarray(var_params["clevs"], dtype=float)
    vmax = float(np.nanmax(np.abs(base_levels)))

    # Use an odd number of levels so 0 is represented exactly.
    n_levels = len(base_levels)
    if n_levels % 2 == 0:
        n_levels += 1

    levels = np.linspace(-vmax, vmax, n_levels)
    norm = TwoSlopeNorm(vmin=-vmax, vcenter=0.0, vmax=vmax)

    # Evenly spaced colorbar labels including zero.
    ticks = np.linspace(-vmax, vmax, 9)
    return levels, norm, ticks


def _align_longitudes_to_target(mask: xr.DataArray, target_x: xr.DataArray) -> xr.DataArray:
    """
    Align mask longitude coordinates to the target grid convention.
    Supports both 0..360 and -180..180 style grids.
    """
    target_min = float(target_x.min())
    target_max = float(target_x.max())

    if target_min < 0:
        mask = mask.assign_coords(X=(((mask["X"] + 180) % 360) - 180)).sortby("X")
    else:
        mask = mask.assign_coords(X=(mask["X"] % 360)).sortby("X")

    mask = mask.sel(X=slice(target_min, target_max))
    return mask


def _mask_land_for_sst(ds: xr.Dataset) -> xr.Dataset:
    """
    Mask SST land points for both member and ensemble-mean fields.
    """
    ds_land = xr.open_dataset(
        "/data/esplab/shared/model/initialized/nmme/hindcast/monthly/land_cover.nc"
    )
    land = ds_land["land"]

    dim_rename = {}
    if "lat" in land.dims:
        dim_rename["lat"] = "Y"
    if "lon" in land.dims:
        dim_rename["lon"] = "X"
    if dim_rename:
        land = land.rename(dim_rename)

    if not set(("Y", "X")).issubset(land.dims):
        return ds

    land = _align_longitudes_to_target(land, ds["X"])
    land = land.interp(Y=ds["Y"], X=ds["X"], method="nearest")

    for var_name in ("sst", "sst_ensmean"):
        if var_name in ds.data_vars:
            ds[var_name] = ds[var_name].where(land != 1)

    return ds


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
    model_plot_locs = _build_model_plot_locs(ds)

    os.makedirs(path, exist_ok=True)

    # ----------------------------------
    # Variable loop
    # ----------------------------------
    for var_params in var_params_dict:
        v = var_params["name"]
        print(f"Plotting variable: {v}")

        # Mask land for SST
        if v == "sst":
            ds = _mask_land_for_sst(ds)

        # ----------------------------------
        # Region loop
        # ----------------------------------
        for reg_name in var_params["regions"]:
            reg = next(r for r in reg_params_dict if r["name"] == reg_name)
            figname = path / f"{var_params['outname']}{reg['name']}"

            _plot_variable_for_region(
                ds, v, var_params, reg,
                models_meta, figname, model_plot_locs
            )


def _plot_variable_for_region(
    ds, v, var_params, reg, models_list, figname, model_plot_locs
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

        proj = _proj_from_region(reg)
        data_crs = ccrs.PlateCarree()
        fig, axs = plt.subplots(
            3, 3,
            figsize=(11, 8.5),
            subplot_kw={"projection": proj},
            constrained_layout=True,
        )
        axs_flat = axs.flatten()
        for i, ax in enumerate(axs_flat):
            ax.set_visible(i in model_plot_locs.values())

        valid_ts = _valid_time_for_lead(ds, ilead)
        fcstmonth_str = valid_ts.strftime("%b")

        suptitle = (
            f"NMME Forecast {fcstmonth_str} {var_params['label']} "
            f"Anomalies ({var_params['units']}): "
            f"{lead} Months Lead"
        )

        clevs, norm, ticks = _balanced_levels_and_ticks(var_params)

        sub_nens = 0

        # -------------------------------
        # Model loop
        # -------------------------------
        for model in ds["model"].values:
            
            # Get plot location for each model
            iplot = model_plot_locs.get(model)
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

            m = _plot_single_panel(
                axs_flat[iplot], ds_sel, v,
                var_params["scale_factor"],
                clevs,
                _cmap_from_name(var_params["cmap"]),
                norm,
                title,
                reg["mproj"],
                reg["lons"],
                reg["lats"],
                reg["state_colors"],
                data_crs,
            )

        fig.suptitle(suptitle, fontsize=12)
        if "m" in locals():
            used_axes = [axs_flat[i] for i in sorted(model_plot_locs.values())]
            fig.colorbar(
                m,
                ax=used_axes,
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
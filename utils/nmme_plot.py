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
    "NCAR-CESM1",
    "COLA-RSMAS-CCSM4",
    "COLA-RSMAS-CESM1",
    "NOAA-SFS",
    "MME",
]

DEBUG_PLOT = os.getenv("NMME_DEBUG", "0") == "1"


def _debug(*args, **kwargs):
    if DEBUG_PLOT:
        print(*args, **kwargs)

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

    _debug(f"[DIAG] ds_sel dims BEFORE isel: {ds_sel.dims}")
    _debug(f"[DIAG] vars BEFORE isel: {list(ds_sel.data_vars)}")

    tmp = ds_sel.isel(lead=ilead, drop=False)

    _debug(f"[DIAG] vars AFTER isel: {list(tmp.data_vars)}")
    _debug(f"[DIAG] nens AFTER isel? {'nens' in tmp}")

    return tmp
    #return ds_sel.sel(lead=ilead,drop=False)


# ============================================================
# Plotting helpers
# ============================================================

def _plot_single_panel(
    ax, ds, var, sf, clevs, cmap, norm, title,
    mproj, lonreg, latreg, statescolor, transform, land_mask=None
):
    # ✅ Keep the DataArray
    da = ds[f"{var}_ensmean"]

    # Models are concatenated on a union grid; non-native coordinates become NaN.
    # Trim NaN-only rows/cols so contouring uses each model's actual support.
    for dim in ("lat", "latitude"):
        if dim in da.dims:
            da = da.dropna(dim=dim, how="all")
            break
    for dim in ("lon", "longitude"):
        if dim in da.dims:
            da = da.dropna(dim=dim, how="all")
            break

    if da.size == 0 or np.isnan(da.values).all():
        ax.set_title(f"{title} (no data)", fontsize=9)
        return None

    # ✅ Detect coordinate names robustly
    lat_dim = "lat" if "lat" in da.sizes else "latitude"
    lon_dim = "lon" if "lon" in da.sizes else "longitude"

    if var == "sst" and land_mask is not None:
        da = _mask_sst_to_ocean(da, land_mask, lat_dim=lat_dim, lon_dim=lon_dim)

    if da.size == 0 or np.isnan(da.values).all():
        ax.set_title(f"{title} (no data)", fontsize=9)
        return None

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
    levels = np.asarray(var_params["clevs"], float)
    vmin = float(np.nanmin(levels))
    vmax = float(np.nanmax(levels))

    norm = TwoSlopeNorm(vmin=vmin, vcenter=0.0, vmax=vmax)
    ticks = levels
    return levels, norm, ticks


def _normalize_lon_to_target(mask_da, target_lon):
    target_has_neg = float(np.nanmin(target_lon.values)) < 0
    mask_has_neg = float(np.nanmin(mask_da["lon"].values)) < 0

    if target_has_neg and not mask_has_neg:
        return mask_da.assign_coords(
            lon=(((mask_da["lon"] + 180) % 360) - 180)
        ).sortby("lon")

    if (not target_has_neg) and mask_has_neg:
        return mask_da.assign_coords(lon=(mask_da["lon"] % 360)).sortby("lon")

    return mask_da


def _load_land_mask(mask_path):
    if not mask_path:
        return None
    try:
        ds_mask = xr.open_dataset(mask_path)
    except Exception as exc:
        print(f"[WARN] Could not open land/ocean mask '{mask_path}': {exc}")
        return None

    for candidate in ("land", "lsmask", "mask"):
        if candidate in ds_mask.data_vars:
            mask = ds_mask[candidate]
            break
    else:
        print(f"[WARN] No mask variable found in '{mask_path}'.")
        ds_mask.close()
        return None

    # Keep only mask and close the source dataset immediately.
    mask = mask.load()
    ds_mask.close()

    rename = {}
    if "latitude" in mask.dims and "lat" not in mask.dims:
        rename["latitude"] = "lat"
    if "longitude" in mask.dims and "lon" not in mask.dims:
        rename["longitude"] = "lon"
    if rename:
        mask = mask.rename(rename)

    if not {"lat", "lon"}.issubset(mask.dims):
        print(f"[WARN] Land/ocean mask missing lat/lon dims in '{mask_path}'.")
        return None

    return mask


def _mask_sst_to_ocean(da, land_mask, lat_dim, lon_dim):
    target_lat = da[lat_dim]
    target_lon = da[lon_dim]

    mask = _normalize_lon_to_target(land_mask, target_lon)
    mask_on_grid = mask.interp(lat=target_lat, lon=target_lon, method="nearest")

    # NMME land_cover.nc stores land as 1 and ocean as 0.
    ocean = mask_on_grid < 0.5
    return da.where(ocean)


# ============================================================
# Main plotting entry point
# ============================================================

def nmme_plot(ds, path, land_mask_path=None):
    """
    Generate NMME forecast anomaly maps.
    """
    var_params_dict, reg_params_dict = initPlotParams()
    model_plot_locs = _build_model_plot_locs(ds)
    land_mask = _load_land_mask(land_mask_path)

    os.makedirs(path, exist_ok=True)
    fcstdate = ds.attrs["init_yyyymm"]

    for var_params in var_params_dict:
        v = var_params["name"]
        print(f"Plotting variable: {v}")

        for reg_name in var_params["regions"]:
            reg = next(r for r in reg_params_dict if r["name"] == reg_name)
            reg_dir = path / reg["name"]
            os.makedirs(reg_dir, exist_ok=True)
            figname = reg_dir / f"{var_params['outname']}{reg['name']}"

            _plot_variable_for_region(
                ds, v, var_params, reg, figname, model_plot_locs, fcstdate, land_mask
            )


def _plot_variable_for_region(
    ds, v, var_params, reg, figname, model_plot_locs, fcstdate, land_mask
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
        mappable = None

        for model, iplot in model_plot_locs.items():
            _debug("\n[DIAG] Global lead coordinate:")
            _debug("  lead values:", ds["lead"].values)
            _debug("  lead dtype :", ds["lead"].dtype)
            _debug("  lead size  :", ds.sizes["lead"])
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
                _debug(f"[DIAG] NaN nens detected: model={model}, variable={v}, lead={ilead}, region={reg['name']}, init={fcstdate}")
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
                land_mask=land_mask,
            )
            if m is not None:
                mappable = m

        fig.suptitle(
            f"NMME Forecast {fcstmonth_str} {var_params['label']} "
            f"Anomalies ({var_params['units']}): {ilead}‑Month Lead",
            fontsize=12,
        )

        if mappable is not None:
            fig.colorbar(
                mappable,
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
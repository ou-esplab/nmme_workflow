#!/usr/bin/env python3
"""
Precompute tercile thresholds for all models, regions, and seasons.
Saves thresholds as NetCDF files for fast lookup.
"""

import argparse
import glob
from pathlib import Path
import xarray as xr
import yaml
import pandas as pd
from utils.nmme_io import open_monthly_climatology
from make_tercile_probability_maps import (
    MODEL_DIR_MAP, SEASON_LEADS, to_0360, subset_region
)

def main():
    parser = argparse.ArgumentParser(description="Precompute tercile thresholds for all models/regions/seasons.")
    parser.add_argument("--config", default="confignmme.yaml", help="Path to config YAML")
    parser.add_argument("--hindcast-root", required=True, help="Root directory for hindcast data")
    parser.add_argument("--sfs-hindcast-root", required=True, help="Root directory for SFS hindcast data")
    parser.add_argument("--outdir", required=True, help="Output directory for thresholds")
    args = parser.parse_args()

    with open(args.config, "r") as f:
        cfg = yaml.safe_load(f)
    hind_root = Path(args.hindcast_root)
    outdir = Path(args.outdir)
    sfs_hindcast_root = Path(args.sfs_hindcast_root)

    # Map variables to extraction names
    var_extract_map = {
        "prec": "prec",
        "tref": "tref",
        "sst": "sst",
        "SST": "SST",
    }

    # Map variables to climatology file variable names
    clim_var_map = {
        "prec": "prec",
        "tref": "tref",
        "sst": "sst",
        "SST": "SST",
    }

    # Loop over models, variables, and seasons
    for model_name in cfg["models"]:
        for var in ["prec", "tref", "sst"]:
            for season, leads in SEASON_LEADS.items():
                # Set up paths and file patterns
                if model_name == "NOAA-SFS":
                    root = sfs_hindcast_root
                else:
                    root = hind_root / MODEL_DIR_MAP.get(model_name, model_name) / "hindcast" / var
                # Find all files for this model/var/season
                pattern = str(root / f"*.nc")
                files = glob.glob(pattern)
                filtered_files = []
                for fp in files:
                    # Extract year and month from filename (assume format: ...YYYYMM...)
                    fname = Path(fp).name
                    yyyymm = "".join([c for c in fname if c.isdigit()])
                    if len(yyyymm) >= 6:
                        year = int(yyyymm[:4])
                        month = int(yyyymm[4:6])
                        filtered_files.append((fp, year, month))
                if not filtered_files:
                    print(f"[WARN] No files found for {model_name} {var} {season}")
                    continue
                sample_arrays = []
                # Set up climatology file info
                clim_root = Path("/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/")
                clim_file_var = var
                clim_var = clim_var_map[var]
                # Set level for climatology file naming (empty string if not needed)
                # Set level for climatology file naming
                if var == "prec":
                    clim_lev = "sfc"
                elif var == "tref":
                    clim_lev = "2m"
                elif var == "sst":
                    clim_lev = "sfc"
                else:
                    clim_lev = ""  # Set to e.g. '1000' if you have a level, else keep as empty string
                # Main processing loop (existing code)
                for fp, year, month in filtered_files:
                    ds = xr.open_dataset(fp, decode_times=False)
                    extract_var = var_extract_map[var]
                    # Allow for both 'SST' and 'sst' variable names
                    if extract_var not in ds:
                        if extract_var.lower() == "sst" and "SST" in ds:
                            da = ds["SST"]
                        elif extract_var.upper() == "SST" and "sst" in ds:
                            da = ds["sst"]
                        else:
                            print(f"[WARN] File {fp} for {model_name} {season} does not contain variable {extract_var}, skipping.")
                            ds.close()
                            continue
                    else:
                        da = ds[extract_var]
                    ren = {}
                    # Robustly handle longitude/latitude as dims or coords, and X/Y as well
                    if "longitude" in da.dims or "longitude" in da.coords:
                        ren["longitude"] = "lon"
                    if "latitude" in da.dims or "latitude" in da.coords:
                        ren["latitude"] = "lat"
                    if "X" in da.dims or "X" in da.coords:
                        ren["X"] = "lon"
                    if "Y" in da.dims or "Y" in da.coords:
                        ren["Y"] = "lat"
                    if "L" in da.dims:
                        ren["L"] = "lead"
                    if ren:
                        da = da.rename(ren)
                    # If still no 'lon', try to rename from coords
                    if "lon" not in da.dims and "lon" not in da.coords:
                        if "longitude" in da.coords:
                            da = da.rename({"longitude": "lon"})
                        elif "X" in da.coords:
                            da = da.rename({"X": "lon"})
                    if "lat" not in da.dims and "lat" not in da.coords:
                        if "latitude" in da.coords:
                            da = da.rename({"latitude": "lat"})
                        elif "Y" in da.coords:
                            da = da.rename({"Y": "lat"})
                    # Print filename for debugging before to_0360
                    if "lon" not in da.dims and "lon" not in da.coords:
                        print(f"[ERROR] File {fp} does not have a 'lon', 'longitude', or 'X' coordinate/dimension.")
                    for init_dim in ("init", "S", "time"):
                        if init_dim in da.dims and da.sizes.get(init_dim, 0) == 1:
                            da = da.isel({init_dim: 0}, drop=True)
                    da = to_0360(da)
                    # --- Vectorized lead-dependent anomaly ---
                    if "lead" in da.dims:
                        n_leads = da.sizes["lead"]
                        # Compute valid month for each lead
                        valid_months = [((month + int(lead) - 1) % 12) + 1 for lead in range(n_leads)]
                        # Load climatology ONCE for all leads/months
                        # Always include underscore before .clim, even if lev is empty
                        clim_path = clim_root / f"{model_name}.{clim_file_var}_{clim_lev}.clim.1991-2020.nc"
                        clim = xr.open_dataset(str(clim_path))
                        clim_da = clim[clim_var]
                        # Normalize lead and month dims
                        if "lead" in clim_da.dims and not pd.api.types.is_integer_dtype(clim_da["lead"].dtype):
                            clim_da = clim_da.assign_coords(lead=clim_da["lead"].astype(int))
                        # Build anomaly array for all leads
                        anom_leads = []
                        for i in range(n_leads):
                            m = valid_months[i]
                            # Select correct month and lead
                            clim_sel = clim_da.sel(month=m, lead=i)
                            anom = da.isel(lead=i) - clim_sel
                            anom_leads.append(anom)
                        da = xr.concat(anom_leads, dim="lead")
                        da = da.mean("lead")
                    else:
                        # No lead dimension, just subtract climatology for the month
                        # Always include underscore before .clim, even if lev is empty
                        clim_path = clim_root / f"{model_name}.{clim_file_var}_{clim_lev}.clim.1991-2020.nc"
                        clim = xr.open_dataset(str(clim_path))
                        clim_da = clim[clim_var]
                        if "month" in clim_da.dims:
                            clim_da = clim_da.sel(month=month)
                        if "lon" in clim_da.dims or "lon" in clim_da.coords:
                            clim_da = clim_da.rename({"lon": "lon"})
                        if "lat" in clim_da.dims or "lat" in clim_da.coords:
                            clim_da = clim_da.rename({"lat": "lat"})
                        da = da - clim_da
                        if "ens" in da.dims:
                            da = da.rename({"ens": "sample"})
                        elif "member" in da.dims:
                            da = da.rename({"member": "sample"})
                        elif "M" in da.dims:
                            da = da.rename({"M": "sample"})
                        else:
                            da = da.expand_dims(sample=[0])
                    sample_arrays.append(da.load())
                    ds.close()
                if not sample_arrays:
                    print(f"[WARN] No usable hindcast for {model_name} {season} {var}")
                    continue
                all_samples = xr.concat(sample_arrays, dim="sample")
                t33 = all_samples.quantile(0.33, dim="sample").drop_vars("quantile", errors="ignore")
                t66 = all_samples.quantile(0.66, dim="sample").drop_vars("quantile", errors="ignore")
                ds_out = xr.Dataset({"t33": t33, "t66": t66})
                out_path = outdir / f"{model_name}.{var}.{season}.terciles.1991-2020.nc"
                ds_out.to_netcdf(out_path)
                print(f"[INFO] Saved: {out_path}")

if __name__ == "__main__":
    main()

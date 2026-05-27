from utils.nmme_io import decode_S_cftime
#!/usr/bin/env python3
"""
Build NOAA-SFS monthly variable climatology from local reforecast files.

Reference method:
  - Follow CalculateNMMEClimos.ipynb logic: monthly mean by init month.
  - Write output compatible with existing NMME climatology file naming.
"""

import argparse
import os
import tempfile
from pathlib import Path
import xarray as xr


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build NOAA-SFS variable climatology from reforecast NetCDF files"
    )
    p.add_argument(
        "--model",
        default="NOAA-SFS",
        help="Model name used in file naming (e.g., NOAA-SFS)",
    )
    p.add_argument(
        "--local-var",
        default="prec",
        help="Local variable name to read/write (e.g., prec, tref, sst)",
    )
    p.add_argument(
        "--input-dir",
        default=None,
        help="Directory containing <var>_<model>_YYYY_MM.nc files. If not set, defaults to /data/esplab/nmme-backup/NOAA-SFS/reforecast/<local-var>",
    )
    p.add_argument(
        "--output-file",
        default=(
            "/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/"
            "NOAA-SFS.prec_sfc.clim.1991-2020.nc"
        ),
        help="Output climatology file path",
    )
    p.add_argument(
        "--start-year",
        type=int,
        default=1991,
        help="First year to include",
    )
    p.add_argument(
        "--end-year",
        type=int,
        default=2020,
        help="Last year to include",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-file debug details",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.input_dir is not None:
        input_dir = Path(args.input_dir)
    else:
        input_dir = Path(f"/data/esplab/nmme-backup/NOAA-SFS/reforecast/{args.local_var}")
    out_file = Path(args.output_file)
    model = str(args.model)
    local_var = str(args.local_var)

    pattern = f"{local_var}_{model}_????_??.nc"
    files = sorted(input_dir.glob(pattern))
    files = [
        f
        for f in files
        if args.start_year <= int(f.stem.split("_")[-2]) <= args.end_year
    ]

    if not files:
        raise FileNotFoundError(
            f"No matching files found in {input_dir} for pattern {pattern}"
        )


    if args.verbose:
        print("[DEBUG] Loading the following files:")
    else:
        print(f"[INFO] Loading {len(files)} files from {input_dir}")
    datasets = []
    for f in files:
        if args.verbose:
            print(f"  {f}")
        # Try standard time decoding first; fall back to manual cftime decoding
        # for models with non-standard calendars (e.g., COLA-RSMAS-CESM1 uses a
        # '360' calendar with 'months since' units that xarray cannot decode natively).
        try:
            ds_i = xr.open_dataset(f, decode_times=True)
        except Exception:
            ds_i = xr.open_dataset(f, decode_times=False)
            ds_i = decode_S_cftime(ds_i)
        if args.verbose:
            # Print the 'init' coordinate if present when debugging.
            if 'init' in ds_i.coords:
                print(f"    init: {ds_i['init'].values}")
            else:
                print("    [WARN] 'init' coordinate not found in file")
        datasets.append(ds_i)

    ds = xr.concat(datasets, dim="init")

    if local_var not in ds.data_vars:
        raise KeyError(
            f"Variable '{local_var}' not present (vars={list(ds.data_vars)})"
        )

    da = ds[local_var]

    # Match NMME climo workflow behavior: climatology is for ensemble-mean field.
    if "member" in da.dims:
        da = da.mean("member", skipna=True)

    # Notebook-equivalent monthly climatology (groupby init month).
    if "init" not in da.coords:
        raise KeyError("Expected 'init' coordinate for monthly grouping")
    da_clim = da.groupby("init.month").mean("init", skipna=True)

    if "lead" in da_clim.coords:
        da_clim = da_clim.assign_coords(lead=da_clim["lead"].astype(int))
        da_clim["lead"].attrs = {"units": "months"}
    if "lon" in da_clim.coords:
        da_clim["lon"].attrs["units"] = "degrees_east"
    if "lat" in da_clim.coords:
        da_clim["lat"].attrs["units"] = "degrees_north"

    ds_out = da_clim.to_dataset(name=local_var)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    encoding = {local_var: {"zlib": True, "complevel": 1}}
    # Write to a temp file in the same directory, then atomically replace the
    # target so that (a) a partially-written file is never visible and (b) we
    # avoid "Permission denied" when the existing file is open elsewhere.
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=out_file.parent, prefix=f".{out_file.name}.tmp"
    )
    try:
        os.close(tmp_fd)
        ds_out.to_netcdf(tmp_path, encoding=encoding)
        # Match group-write convention used by the rest of the climo directory.
        os.chmod(tmp_path, 0o664)
        os.replace(tmp_path, out_file)
    except Exception:
        # Clean up the temp file on failure; re-raise so the caller knows.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(f"[INFO] Wrote {out_file}")
    print(f"[INFO] Months in climatology: {ds_out['month'].values.tolist()}")
    print(f"[INFO] Output dims: {dict(ds_out.sizes)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

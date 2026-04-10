import xarray as xr
import sys

# Paths to the two forecast files (update these paths as needed)
file_202603 = "/data/esplab/nmme-backup/NOAA-SFS/forecast/prec/prec_NOAA-SFS_2026_03.nc"
file_202604 = "/data/esplab/nmme-backup/NOAA-SFS/forecast/prec/prec_NOAA-SFS_2026_04.nc"

for f in [file_202603, file_202604]:
    print(f"\n--- {f} ---")
    try:
        ds = xr.open_dataset(f)
        if "pratesfc" in ds:
            da = ds["pratesfc"]
        elif "prec" in ds:
            da = ds["prec"]
        else:
            print("Variable 'pratesfc' or 'prec' not found in file.")
            print(f"Variables in file: {list(ds.variables)}")
            continue
        print("Dimensions:", da.dims)
        print("Shape:", da.shape)
        print("Coordinates:", list(da.coords))
        print(da)
    except Exception as e:
        print(f"Error reading {f}: {e}")

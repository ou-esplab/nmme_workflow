import xarray as xr
import s3fs

# S3 base path and cycles to check
s3_base = "s3://noaa-oar-sfsdev-pds/experiments/beta1/forecast"
cycles = ["202603", "202604"]
zarr_name = "atm_monthly.zarr"
var_name = "pratesfc"

# Use anonymous access


for cycle in cycles:
    zarr_url = f"{s3_base}/{cycle}/{zarr_name}"
    print(f"\n--- {zarr_url} ---")
    try:
        ds = xr.open_zarr(zarr_url, storage_options={"anon": True})
        if var_name in ds:
            da = ds[var_name]
            print(f"Variable: {var_name}")
            print("Dimensions:", da.dims)
            print("Shape:", da.shape)
            print("Coordinates:", list(da.coords))
            print(da)
        else:
            print(f"Variable '{var_name}' not found in this Zarr dataset.")
            print(f"Variables in dataset: {list(ds.variables)}")
    except Exception as e:
        print(f"Error reading {zarr_url}: {e}")

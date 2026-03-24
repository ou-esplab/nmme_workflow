from __future__ import annotations
import xarray as xr

def rename_to_cpt_dims(da: xr.DataArray | xr.Dataset) -> xr.DataArray:
    if isinstance(da, xr.Dataset):
        da = da[list(da.data_vars)[0]]

    rename = {}
    for lat in ("lat", "latitude", "y", "Y"):
        if lat in da.coords or lat in da.dims:
            rename[lat] = "Y"
            break
    for lon in ("lon", "longitude", "x", "X"):
        if lon in da.coords or lon in da.dims:
            rename[lon] = "X"
            break
    for tim in ("time", "T", "year"):
        if tim in da.coords or tim in da.dims:
            rename[tim] = "T"
            break

    if rename:
        da = da.rename(rename)

    for c in ("Y", "X", "T"):
        if c in da.coords:
            da = da.sortby(c)

    return da

def to_cptv10(X=None, Y=None):
    X_v10 = Y_v10 = None

    if X is not None:
        if X.dims != ("S", "C", "Y", "X"):
            raise ValueError(f"X dims invalid: {X.dims}")
        X_v10 = (
            X.rename({"S": "T", "Y": "row", "X": "col"})
             .transpose("T", "C", "row", "col")
        )

    if Y is not None:
        if Y.dims != ("S", "Y", "X"):
            raise ValueError(f"Y dims invalid: {Y.dims}")
        Y_v10 = (
            Y.rename({"S": "T", "Y": "row", "X": "col"})
             .transpose("T", "row", "col")
        )

    return X_v10, Y_v10

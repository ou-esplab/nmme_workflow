from __future__ import annotations
import xarray as xr
from .cpt_dims import rename_to_cpt_dims

def interp_to_target_grid(
    src: xr.DataArray, target: xr.DataArray
) -> xr.DataArray:
    src = rename_to_cpt_dims(src)
    target = rename_to_cpt_dims(target)
    return src.interp(X=target.X, Y=target.Y)

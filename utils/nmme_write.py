from pathlib import Path
import xarray as xr

def nmme_write(ds: xr.Dataset, yyyymm: str) -> None:
    out = Path(
        f"/data/esplab/shared/model/initialized/nmme/forecast/monthly/{yyyymm}/data/"
    )
    out.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out / f"nmme_anoms_mme_{yyyymm}.nc")

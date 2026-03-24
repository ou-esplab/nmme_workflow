from pathlib import Path
import matplotlib.pyplot as plt
import xarray as xr
import pandas as pd

def nmme_plot(ds: xr.Dataset, figpath: Path) -> None:
    figpath.mkdir(parents=True, exist_ok=True)
    init = pd.to_datetime(ds["S"].values).strftime("%Y%m")

    for v in ds.data_vars:
        try:
            da = ds[v]
            if "lead" in da.dims:
                da = da.isel(lead=0)
            da.plot()
            plt.savefig(figpath / f"{v}_init{init}.png", dpi=150)
            plt.close()
        except Exception:
            continue

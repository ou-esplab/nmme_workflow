# NMME Workflow: Products and Static Stage Outputs

## Products Stage

The products stage runs two scripts: `products/MakeNMMEFcsts.py` and `products/make_tercile_probability_maps.py`.

All per-forecast outputs live under a single date directory:

```
/data/esplab/shared/model/initialized/nmme/forecast/{YYYYMM}/
  data/
    monthly/       ← monthly anomaly NetCDFs
    seasonal/      ← seasonal anomaly NetCDFs
    tercile_probs/ ← per-forecast tercile probability NetCDFs
  images/
    anomalies/     ← anomaly maps (by region)
    tercile_probs/ ← tercile probability maps (by region)
    threshold_maps/
    most_likely/
    cpt_dominant/
    seasonal_total_summary/
```

---

### MakeNMMEFcsts.py

Builds multi-model ensemble (MME) anomalies for one initialization month and writes anomaly maps and NetCDF files.

#### Data Files (NetCDF)

Base path: `/data/esplab/shared/model/initialized/nmme/forecast/{YYYYMM}/data/`

| Type | Path pattern | Dims | Contents |
|------|-------------|------|----------|
| Monthly anomaly | `monthly/NMME_fcst_{YYYYMM}.anom.monthly.{var}_{lev}.emean.nc` | `(model, time, lat, lon)` | Per-model and MME ensemble-mean anomaly. `time` coordinate is the forecast valid month. |
| Seasonal anomaly | `seasonal/NMME_fcst_{YYYYMM}.anom.seas.{var}_{lev}.emean.nc` | `(model, season_window, lat, lon)` | 3-month rolling means of the monthly anomalies. `season` coordinate labels each window (e.g., `Jun`). |

Variables written: `prec_sfc`, `tref_2m`, `sst_sfc` (and any others present in the preprocessed data).

Each file contains one variable per contributing model plus a `MME` entry (mean across models).

#### Images (PNG)

Base path: `/data/esplab/shared/model/initialized/nmme/forecast/{YYYYMM}/images/anomalies/`

| Region | Variables | Filename pattern |
|--------|-----------|-----------------|
| Global | tref, prec, sst | `Global/{VarLabel}GlobalMonth{N}.png` |
| NorthAmerica | tref, prec, sst | `NorthAmerica/{VarLabel}NorthAmericaMonth{N}.png` |
| (additional configured regions) | prec | `{Region}/{VarLabel}{Region}Month{N}.png` |

One image per lead month (Month0 = initialization month). Images show the MME ensemble-mean anomaly field.

---

### make_tercile_probability_maps.py

Computes tercile probabilities for configured regions and seasons against precomputed hindcast thresholds, and writes several map products.

Runs over: variables `prec`, `tref`; all configured regions; seasons `MAM`, `AMJ`, `MJJ`, `JJA`, `ASO`, `NDJ`.

#### Data Files (NetCDF)

Base path: `/data/esplab/shared/model/initialized/nmme/forecast/{YYYYMM}/data/tercile_probs/`

| Filename pattern | Contents |
|-----------------|----------|
| `NMME_{YYYYMM}_{Region}_{Season}_{var}_tercile_probs.nc` | BN, NN, AN probability fields (%) on a 1-degree regional grid. Multi-model mean. |

#### Images (PNG)

Base path: `/data/esplab/shared/model/initialized/nmme/forecast/{YYYYMM}/images/`

| Subdirectory | Filename pattern | Description |
|-------------|-----------------|-------------|
| `tercile_probs/{Region}/` | `NMME_{YYYYMM}_{Region}_{Season}_{var}_tercile_probs.png` | 3-panel map showing Below Normal / Near Normal / Above Normal probability (%) at each grid point. The primary tercile forecast product. |
| `threshold_maps/{Region}/` | `NMME_{YYYYMM}_{Region}_{Season}_{var}_thresholds.png` | 2-panel map of the multi-model-mean precomputed hindcast thresholds T33 and T66. Shows the climatological boundary values that separate BN, NN, and AN — e.g., the precipitation rate below which conditions are considered below-normal for this region and season. |
| `most_likely/{Region}/` | `NMME_{YYYYMM}_{Region}_{Season}_{var}_most_likely.png` | Single-panel map of the dominant tercile category (highest probability) at each grid point. Grid points where no category exceeds 40% are masked. Simpler to read than the 3-panel tercile probability map. |
| `cpt_dominant/{Region}/` | `NMME_{YYYYMM}_{Region}_{Season}_{var}_cpt_dominant.png` | CPT-style dominant-category map. Like `most_likely` but shaded using each category's own colormap (Blues=BN, Greens=NN, YlOrRd=AN) with color intensity proportional to probability strength. Masked where dominant probability < 40%. Familiar to users of CPT output. |
| `seasonal_total_summary/{Region}/` | `NMME_{YYYYMM}_{Region}_{Season}_prec_seasonal_total_summary.png` | Precip only. 3-panel map of multi-model-mean seasonal total precipitation (mm/season) computed from raw hindcasts: (1) mean seasonal total, (2) lower tercile total T33 (dry threshold), (3) upper tercile total T66 (wet threshold). Provides absolute context for the tercile probability forecasts. |

---

## Static Stage

The static stage is run once (or when hindcast data changes). It produces the climatology and tercile threshold files that the products stage depends on.

---

### make_sfs_climo_from_reforecast.py (via run_all_climos.sh)

Builds monthly climatologies (1991–2020) for each model and variable from raw hindcast/reforecast files.

#### Data Files (NetCDF)

Base path: `/data/esplab/shared/model/initialized/nmme/climatology/monthly/1991-2020/`

Filename pattern: `{Model}.{var}_{lev}.clim.1991-2020.nc`

| Dims | Contents |
|------|----------|
| `(month, lead, lat, lon)` | Ensemble-mean climatological value for each calendar month and lead. |

Models and variables:

| Model | Variables |
|-------|-----------|
| NASA-GEOSS2S, CanESM5, GEM5.2-NEMO, NCEP-CFSv2, NCAR-CESM1, COLA-RSMAS-CCSM4, COLA-RSMAS-CESM1 | prec, tref, sst, h500, h200 |
| NOAA-SFS | prec, tref, sst |

---

### precompute_tercile_thresholds.py

Computes 33rd and 66th percentile thresholds from hindcast anomalies for each model, variable, and season.

#### Data Files (NetCDF)

Base path: `/data/esplab/shared/model/initialized/nmme/terciles/1991-2020/`

Filename pattern: `{Model}.{var}.{Season}.terciles.1991-2020.nc`

| Dims | Contents |
|------|----------|
| `(lat, lon)` | `t33` and `t66` threshold fields for the given model/variable/season combination. |

Variables: `prec`, `tref`, `sst`
Seasons: `MAM`, `AMJ`, `MJJ`, `JJA`, `ASO`, `NDJ`

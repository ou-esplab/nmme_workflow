# NMME Seasonal Forecast Products Guide

This guide describes the forecast products available on this site and how to interpret them.

---

## Overview

Forecasts are produced monthly using the **North American Multi-Model Ensemble (NMME)**, a
collection of coupled climate models from NOAA, NASA, NCAR, and other centers. Combining
multiple models reduces the uncertainty of any single model and provides a more robust
probabilistic forecast.

Products are available for:

- **Variables:** Precipitation, 2-meter Temperature
- **Regions:** Venezuela, Iran, Mexico
- **Seasons:** MAM, AMJ, MJJ, JJA, ASO, NDJ (overlapping 3-month windows)
- **Climatological baseline:** 1991–2020

---

## Forecast Products

### 1. Tercile Probability Maps *(primary forecast product)*

**What it shows:** The probability (%) that the seasonal average will fall in each of three
categories relative to the 1991–2020 climatology:

| Category | Meaning |
|----------|---------|
| **Below Normal (BN)** | The driest/coldest third of historical years |
| **Near Normal (NN)** | The middle third of historical years |
| **Above Normal (AN)** | The wettest/warmest third of historical years |

Each category has a 33% climatological probability, so values above 40% indicate a meaningful
forecast signal.

**How to read it:** Three side-by-side maps, one per category. Darker shading means higher
probability. A grid point where AN shows 60% means the models collectively give a 60% chance
of above-normal conditions there for that season.

**Format:** 3-panel map (BN | NN | AN)

---

### 2. Most-Likely Tercile Map

**What it shows:** A simplified single-panel summary of the tercile probability map. Each
grid point is colored by whichever category has the highest probability. Grid points where
no category exceeds 40% are left blank (no clear signal).

**How to read it:** Blue = most likely Below Normal; gray = most likely Near Normal;
red = most likely Above Normal. This is the easiest map to scan for a quick regional picture,
but use the full tercile probability map to understand the actual probability values.

---

### 3. CPT-Style Dominant Category Map

**What it shows:** The same dominant-category information as the Most-Likely map, but color
intensity scales with probability strength — a deeper blue means the BN signal is stronger,
a deeper red means the AN signal is stronger. Grid points where no category exceeds 40%
are masked.

**How to read it:** The color tells you *which* category dominates; the shade tells you *how
strongly*. This style is familiar to users of the Climate Predictability Tool (CPT).

---

### 4. Anomaly Maps

**What it shows:** The multi-model ensemble mean forecast anomaly — how much warmer/cooler or
wetter/drier than the 1991–2020 average the models collectively predict for each month or
season.

**How to read it:** Positive anomalies (warm colors for temperature, green for precipitation)
indicate above-normal predicted conditions. Negative anomalies indicate below-normal
conditions. These maps do not convey uncertainty — use the tercile probability maps for that.

**Available for:** Monthly leads (Month 0 through Month 8) and 3-month seasonal means.
Global and regional maps are provided.

---

### 5. Threshold Maps

**What it shows:** The climatological tercile boundaries — the values that separate
Below Normal from Near Normal (T33) and Near Normal from Above Normal (T66) for each
grid point and season. These are derived from the 1991–2020 hindcast period.

**How to read it:** The two panels show T33 and T66 in anomaly units (mm/day departure from
climatology for precipitation; °C departure for temperature). A region with a large spread
between T33 and T66 has high year-to-year variability; a small spread means most years
cluster near the mean.

**Purpose:** These thresholds are what the forecast anomalies are compared against to
produce the tercile probabilities. Viewing them helps understand why a modest anomaly
forecast can translate to a strong BN or AN signal in some regions.

---

### 6. Seasonal Total Precipitation Summary *(precipitation only)*

**What it shows:** Absolute seasonal precipitation totals (mm/season) rather than anomalies.
Three panels show: (1) the mean seasonal total, (2) the lower-tercile boundary (T33 — the
dry threshold), and (3) the upper-tercile boundary (T66 — the wet threshold).

**How to read it:** These values give real-world context to the tercile probabilities. For
example, if T33 for a region is 150 mm/season, a BN forecast means the models expect less
than about 150 mm total precipitation that season.

**Purpose:** Complements the anomaly-based products by showing what "below normal" actually
means in physical units for each region and season.

---

## Regions

| Region | Coverage |
|--------|---------|
| CONUS | Contiguous United States (24°N–50°N, 125°W–66°W) |
| Venezuela | Northern Venezuela and surrounding area |
| Iran | Iran and neighboring countries |
| Mexico | Mexico and the southwestern United States |

---

## Seasons

Each season label is a 3-month window. Seasons are computed relative to the forecast
initialization month, so available seasons shift slightly each month.

| Label | Months |
|-------|--------|
| MAM | March – April – May |
| AMJ | April – May – June |
| MJJ | May – June – July |
| JJA | June – July – August |
| ASO | August – September – October |
| NDJ | November – December – January |

---

## Data Files

NetCDF files are provided for users who want to work with the forecast data directly.

| File type | Contents |
|-----------|---------|
| Monthly anomaly | Multi-model ensemble mean anomaly for each forecast month and model |
| Seasonal anomaly | 3-month rolling mean of the monthly anomalies |
| Tercile probabilities | BN, NN, AN probability fields (%) on a 1-degree regional grid |

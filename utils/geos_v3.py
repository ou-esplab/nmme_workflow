"""Shared helpers for NASA GMAO GEOS-S2S-3 (NASA-GEOSS2S3) NMME ingest.

GEOS-S2S-3 is fetched directly from NASA (not IRIDL), from two portals:

  Real-time:  https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/NRT/NMME/monthly/{mon}{yyyy}/
              {YYYYMMDD}.{token}.ens{N}.lead{L}.nc4 -- one monthly mean per file,
              native 0.5-deg grid (lat -90..90, lon -180..179.5), same as the
              hindcast. The folder is named for the forecast (S) month; the member
              inits are lagged 5-day starts from the previous month. Only some
              (init, ens) pairs run the full 9 months (10 of them, i.e. the NMME
              submission); the rest stop after 2 leads.
  Hindcast:   https://data.nas.nasa.gov/geos-s2s-3/NMME/ensembles/monthly_native/{mon}/{mon}.{token}.{period}.tar
              member-level, same file layout as the NRT folders (10 full-length
              members per forecast, 1991-2020). The old ensemble-mean-only files at
              .../NMME/{yyyy}/{mon}/{token}.nc are superseded.

Both are written to the same on-disk layout IRIDL-sourced models use, so every
downstream reader works unchanged:

  {root}/NASA-GEOSS2S3/{forecast,hindcast}/{var}/{var}_NASA-GEOSS2S3_{YYYY}_{MM}.nc
  {var}(S, L, M, Y, X) with S in "months since 1960-01-01" (360 calendar),
  L = 0.5..8.5 months, Y = -90..90, X = 0..359 (181x360 integer grid).

Mirrors subc_workflow's GEOS_V3 handling (static/download_geos_v3_hindcast.py,
utils/download_subx_direct.py:_download_gmao_v3_to_subx).
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import urlopen

import numpy as np
import xarray as xr

MODEL_ID = "NASA-GEOSS2S3"
NLEADS = 9

RT_URL = "https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/NRT/NMME/monthly/"
# Member-level hindcast archives (posted 2026-10-14):
#   {ENSEMBLE_HINDCAST_URL}{mon}/{mon}.{rt token}.{period}.tar
# Each tar holds {YYYYMMDD}.{token}.ens{N}.lead{L}.nc4 on the native 0.5-deg grid
# (361x720), like the real-time folders. {mon} is the forecast (S) month; inits are the lagged
# starts of the PRIOR month and lead1 is the S month.
ENSEMBLE_HINDCAST_URL = "https://data.nas.nasa.gov/geos-s2s-3/NMME/ensembles/monthly_native/"
HINDCAST_PERIODS = {"1991_2020": (1991, 2020)}  # one tar per variable and month on monthly_native

# local var -> (real-time file token, hindcast file token, in-file variable name).
# The hindcast tars use the same tokens as the NRT folders. None means the portal
# does not provide that variable. GMAO's in-file names are its raw GEOS diagnostic
# names (same convention as subc's _GMAO_V3_INFILE_VARNAME); confirmed 2026-10-27 on
# the monthly_native hindcast tars.
VAR_MAP: Dict[str, Tuple[Optional[str], Optional[str], str]] = {
    "prec": ("precip", "precip", "PRECTOTCORR"),
    "tref": ("at", "at", "T2M"),
    "sst":  ("sst", "sst", "TS"),
    "h500": ("z500", "z500", "H"),
    "h200": ("z200", "z200", "H"),
}

# Pressure level (hPa) a height field must actually carry, checked against the
# in-file `lev` value. GMAO's height files were mislabeled (2026-09-27: NRT z500 held
# lev=200, old hindcast h500/h200 held 450/150); the monthly_native hindcast tars were
# re-cut on 2026-10-19 and now carry lev=500/200. The guard stays so a mislabeled
# real-time posting is refused instead of written as the wrong level.
PRESSURE_LEVEL = {"h500": 500.0, "h200": 200.0}

DEFAULT_VARS = ["prec", "tref", "sst", "h500", "h200"]

UNITS = {"prec": "mm/day", "tref": "K", "sst": "K", "h500": "m", "h200": "m"}

MONTH_ABBR = ["jan", "feb", "mar", "apr", "may", "jun",
              "jul", "aug", "sep", "oct", "nov", "dec"]

_EXPECTED_LAT = np.arange(-90, 91, 1, dtype=float)
_EXPECTED_LON = np.arange(0, 360, 1, dtype=float)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def http_get(url: str, timeout: int = 60, retries: int = 4, sleep_secs: float = 5.0) -> Optional[str]:
    """Return the body of url, or None on HTTP 404 (not posted). Retries other errors."""
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urlopen(url, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            last_exc = exc
        except Exception as exc:
            last_exc = exc
        if attempt < retries - 1:
            time.sleep(sleep_secs * (2 ** attempt))
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts") from last_exc


def download_with_retry(url: str, dest: Path, timeout: int = 120, retries: int = 3,
                        sleep_secs: float = 3.0) -> bool:
    """Download url to dest with exponential-backoff retries. A 404 fails immediately."""
    for attempt in range(retries):
        try:
            with urlopen(url, timeout=timeout) as resp, open(dest, "wb") as fh:
                shutil.copyfileobj(resp, fh)
                expected = resp.headers.get("Content-Length")
            if expected is not None and dest.stat().st_size != int(expected):
                raise IOError(f"truncated download: got {dest.stat().st_size} of {expected} bytes")
            return True
        except HTTPError as exc:
            dest.unlink(missing_ok=True)
            if exc.code == 404:
                return False
            err: Exception = exc
        except Exception as exc:
            dest.unlink(missing_ok=True)
            err = exc
        if attempt < retries - 1:
            time.sleep(sleep_secs * (2 ** attempt))
        else:
            print(f"[GEOSV3][ERROR] Giving up on {url}: {err}")
    return False


# ── Real-time listing ─────────────────────────────────────────────────────────

def rt_month_dir(init_yyyymm: str) -> str:
    """Forecast month folder name on the NRT portal, e.g. 202609 -> 'sep2026'."""
    return f"{MONTH_ABBR[int(init_yyyymm[4:6]) - 1]}{init_yyyymm[:4]}"


def parse_rt_listing(html: str, token: str) -> Dict[Tuple[str, int], Dict[int, str]]:
    """Parse an NRT month-folder listing into {(init YYYYMMDD, ens): {lead: filename}}."""
    pat = re.compile(rf'href="((\d{{8}})\.{re.escape(token)}\.ens(\d+)\.lead(\d+)\.nc4)"')
    members: Dict[Tuple[str, int], Dict[int, str]] = {}
    for fname, init, ens, lead in pat.findall(html):
        members.setdefault((init, int(ens)), {})[int(lead)] = fname
    return members


def parse_member_names(names: List[str], token: str) -> Dict[Tuple[str, int], Dict[int, str]]:
    """Group archive member names into {(init YYYYMMDD, ens): {lead: name}} (basename match)."""
    pat = re.compile(rf'^(\d{{8}})\.{re.escape(token)}\.ens(\d+)\.lead(\d+)\.nc4$')
    members: Dict[Tuple[str, int], Dict[int, str]] = {}
    for name in names:
        m = pat.match(os.path.basename(name))
        if m:
            members.setdefault((m.group(1), int(m.group(2))), {})[int(m.group(3))] = name
    return members


def s_month_of_init(init_yyyymmdd: str) -> str:
    """Forecast (S) month YYYYMM of a member: the month after its init date (lead1 = S)."""
    y, mo = int(init_yyyymmdd[:4]), int(init_yyyymmdd[4:6])
    y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return f"{y}{mo:02d}"


def full_length_members(members: Dict[Tuple[str, int], Dict[int, str]],
                        nleads: int = NLEADS) -> List[Tuple[str, int]]:
    """(init, ens) pairs that run the full forecast length, sorted by init then ens.

    GMAO posts many short (2-lead) members alongside the full-length ones; only
    those reaching lead `nleads` form the NMME ensemble.
    """
    return sorted(k for k, leads in members.items() if max(leads) >= nleads)


# ── Regridding ────────────────────────────────────────────────────────────────

def _check_grid(ds: xr.Dataset, what: str) -> None:
    lat, lon = ds["lat"].values, ds["lon"].values
    if not (lat.shape == _EXPECTED_LAT.shape and lon.shape == _EXPECTED_LON.shape
            and np.allclose(lat, _EXPECTED_LAT) and np.allclose(lon, _EXPECTED_LON)):
        raise RuntimeError(
            f"{what} regrid did not produce the expected 181x360 grid; the input must be the "
            f"native 361x720 0.5-deg grid (got {lat.size}x{lon.size}, "
            f"lat={lat[:3]}..{lat[-3:]}, lon={lon[:3]}..{lon[-3:]})"
        )


def regrid_native_to_1deg(ds: xr.Dataset) -> xr.Dataset:
    """Subsample native 720x361 0.5-deg (-180..179.5) grid to 181x360 1-deg (0..359).

    The native grid is an exact 2:1 superset of the target grid, so no
    interpolation is needed. Longitude is snapped to the nearest 0.5 before the
    0-360 conversion, otherwise floating-point noise at lon~=0 wraps that point
    to ~359.999 and shifts every subsequent sample by one index.
    """
    lon_snapped = np.round(ds["lon"].values * 2.0) / 2.0
    ds = ds.assign_coords(lon=np.mod(lon_snapped, 360.0)).sortby("lon").sortby("lat")
    ds = ds.isel(lat=slice(0, None, 2), lon=slice(0, None, 2))
    ds = ds.assign_coords(lat=np.round(ds["lat"].values), lon=np.round(ds["lon"].values))
    _check_grid(ds, "GEOS-S2S-3")
    return ds


# ── Output ────────────────────────────────────────────────────────────────────

def s_value(init_yyyymm: str) -> float:
    """IRIDL-style S: months since 1960-01-01 (360 calendar), e.g. 202609 -> 800."""
    return float((int(init_yyyymm[:4]) - 1960) * 12 + int(init_yyyymm[4:6]) - 1)


def extract_field(ds: xr.Dataset, var: str) -> xr.DataArray:
    """Pull the in-file GMAO variable, drop any singleton level, convert units."""
    da = ds[VAR_MAP[var][2]]
    want = PRESSURE_LEVEL.get(var)
    if want is not None:
        got = np.atleast_1d(da["lev"].values) if "lev" in da.coords else np.array([])
        if got.size != 1 or not np.isclose(float(got[0]), want):
            raise ValueError(
                f"{var}: file holds lev={got.tolist()} hPa, expected {want:g} hPa "
                f"(GMAO level labeling); refusing to write mislabeled heights"
            )
    if "lev" in da.dims:
        da = da.squeeze("lev", drop=True)
    da = da.drop_vars([c for c in ("lev",) if c in da.coords])
    if var == "prec":
        da = da * 86400.0  # kg m-2 s-1 -> mm/day
    return da.astype(np.float32)


def to_nmme_dataset(data: np.ndarray, var: str, init_yyyymm: str, attrs: Optional[dict] = None) -> xr.Dataset:
    """Wrap a (L, M, Y, X) array as the IRIDL-style (S, L, M, Y, X) dataset."""
    nlead, nmem = data.shape[0], data.shape[1]
    coords = {
        "S": ("S", [s_value(init_yyyymm)], {
            "standard_name": "forecast_reference_time", "long_name": "Forecast Start Time",
            "units": "months since 1960-01-01", "calendar": "360"}),
        "L": ("L", np.arange(nlead, dtype=np.float32) + 0.5, {
            "standard_name": "forecast_period", "long_name": "Lead", "units": "months"}),
        "M": ("M", np.arange(1, nmem + 1, dtype=np.float32), {
            "standard_name": "realization", "long_name": "Ensemble Member", "units": "unitless"}),
        "Y": ("Y", _EXPECTED_LAT.astype(np.float32), {"standard_name": "latitude", "units": "degree_north"}),
        "X": ("X", _EXPECTED_LON.astype(np.float32), {"standard_name": "longitude", "units": "degree_east"}),
    }
    da = xr.DataArray(data[np.newaxis].astype(np.float32), dims=("S", "L", "M", "Y", "X"),
                      coords=coords, name=var, attrs={"units": UNITS[var], **(attrs or {})})
    ds = da.to_dataset()
    ds.attrs["source"] = f"NASA GMAO GEOS-S2S-3 ({MODEL_ID})"
    return ds


def write_atomic(ds: xr.Dataset, outfile: Path) -> None:
    """Write ds to outfile via a .part temp file, then set esplab group perms."""
    outfile.parent.mkdir(parents=True, exist_ok=True)
    tmp = outfile.with_name(outfile.name + ".part")
    var = next(iter(ds.data_vars))
    enc = {var: {"zlib": True, "complevel": 1, "_FillValue": np.float32(np.nan)}}
    ds.to_netcdf(tmp, format="NETCDF4", encoding=enc)
    os.replace(tmp, outfile)
    set_group_perms(outfile)


def set_group_perms(path: Path) -> None:
    try:
        shutil.chown(path, group="esplab")
    except Exception:
        pass
    try:
        os.chmod(path, 0o775)
    except Exception:
        pass

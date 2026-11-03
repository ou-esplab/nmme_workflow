#!/usr/bin/env python3
"""Download NASA GEOS-S2S-3 member-level NMME hindcasts (1991-2020) from the NAS portal.

Source: https://data.nas.nasa.gov/geos-s2s-3/NMME/ensembles/monthly_native/{mon}/{mon}.{token}.{period}.tar
({period} = 1991_2020; see utils/geos_v3.py). {mon} is the forecast (S)
month. Each tar holds {YYYYMMDD}.{token}.ens{N}.lead{L}.nc4 monthly means on the
native 0.5-deg grid (361x720; subsampled to the 181x360 1-deg grid); the inits are lagged 5-day starts of the prior month. Only the
(init, ens) pairs that run all 9 leads are kept -- 10 members per forecast, the
same selection as the real-time ingest. Written as

  {data_root}/NASA-GEOSS2S3/hindcast/{var}/{var}_NASA-GEOSS2S3_{YYYY}_{MM}.nc

with {YYYY}_{MM} the forecast (S) month (lead1) and dims (S, L=9, M=10, Y=181, X=360).

Usage
-----
  python static/download_geos_v3_nmme_hindcast.py --dry-run --months 1 --vars sst
  python static/download_geos_v3_nmme_hindcast.py                    # everything, skip existing
  python static/download_geos_v3_nmme_hindcast.py --vars "prec" --overwrite
"""
from __future__ import annotations

import argparse
import sys
import tarfile
import tempfile
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.config import load_config
from utils.geos_v3 import (
    DEFAULT_VARS, ENSEMBLE_HINDCAST_URL, HINDCAST_PERIODS, MODEL_ID, MONTH_ABBR, NLEADS, VAR_MAP,
    download_with_retry, extract_field, full_length_members, parse_member_names,
    regrid_native_to_1deg, s_month_of_init, to_nmme_dataset, write_atomic,
)

warnings.filterwarnings("ignore")


def log(msg: str) -> None:
    print(f"[GEOSV3-HC] {msg}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download GEOS-S2S-3 NMME member-level hindcasts")
    p.add_argument("--config", default="confignmme.yaml")
    p.add_argument("--data-root", default=None, help="Override data.local.root from config")
    p.add_argument("--hindcast-url", default=None, help="Override the ensemble hindcast base URL")
    p.add_argument("--vars", default=None, help="Space-separated vars (default: utils.geos_v3.DEFAULT_VARS)")
    p.add_argument("--start-year", type=int, default=1991)
    p.add_argument("--end-year", type=int, default=2020)
    p.add_argument("--months", default="1-12", help='Forecast (S) months, e.g. "1-12" or "1,4,7"')
    p.add_argument("--tmpdir", default=None,
                   help="Where each ~5 GB tar is staged (default: {data_root}/NASA-GEOSS2S3/.tmp; "
                        "not /tmp, which is RAM-backed)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def _parse_months(spec: str) -> List[int]:
    out: List[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(m for m in out if 1 <= m <= 12))


def _load_lead(path: Path, var: str) -> np.ndarray:
    with xr.open_dataset(path, decode_times=False) as ds:  # time is unused; decoding costs ~3 s/file
        ds.load()  # lazy strided subsampling of the compressed file is ~2 s/file
        da = extract_field(regrid_native_to_1deg(ds), var)
        if "time" in da.dims:
            da = da.isel(time=0)
        return da.transpose("lat", "lon").values


def process_tar(tar_path: Path, var: str, token: str, month: int, years: range,
                data_root: Path, overwrite: bool) -> Tuple[int, int]:
    """Convert one downloaded tar into per-year hindcast files. Returns (written, errors)."""
    n_ok = n_err = 0
    with tarfile.open(tar_path) as tf:
        members = parse_member_names(tf.getnames(), token)
        by_s: Dict[str, Dict[Tuple[str, int], Dict[int, str]]] = defaultdict(dict)
        for key, leads in members.items():
            by_s[s_month_of_init(key[0])][key] = leads

        for s_yyyymm in sorted(by_s):
            year = int(s_yyyymm[:4])
            if int(s_yyyymm[4:]) != month:
                log(f"WARN {tar_path.name}: init {sorted(by_s[s_yyyymm])[0][0]} gives S={s_yyyymm}, "
                    f"expected month {month:02d}; skipping")
                continue
            if year not in years:
                continue
            outfile = data_root / MODEL_ID / "hindcast" / var / f"{var}_{MODEL_ID}_{year}_{month:02d}.nc"
            if outfile.exists() and not overwrite:
                continue
            full = full_length_members(by_s[s_yyyymm])
            if not full:
                log(f"{var} {s_yyyymm}: no full-length members; skipping")
                n_err += 1
                continue
            try:
                data = np.full((NLEADS, len(full), 181, 360), np.nan, dtype=np.float32)
                with tempfile.TemporaryDirectory() as tmp:
                    for m_idx, key in enumerate(full):
                        for lead in range(1, NLEADS + 1):
                            name = by_s[s_yyyymm][key].get(lead)
                            if name is None:
                                log(f"{var} {s_yyyymm}: WARN {key[0]}/ens{key[1]} missing lead{lead}")
                                continue
                            tf.extract(name, tmp)
                            f = Path(tmp) / name
                            data[lead - 1, m_idx] = _load_lead(f, var)
                            f.unlink()
                if np.isnan(data).all():
                    raise RuntimeError("all fields NaN")
                attrs = {"members": " ".join(f"{i}.ens{e}" for i, e in full)}
                write_atomic(to_nmme_dataset(data, var, s_yyyymm, attrs=attrs), outfile)
                log(f"SAVE {outfile} ({len(full)} members)")
                n_ok += 1
            except Exception as exc:
                log(f"ERROR {var} {s_yyyymm}: {exc}")
                n_err += 1
    return n_ok, n_err


def main() -> int:
    args = parse_args()
    cfg: Dict = load_config(args.config) if Path(args.config).exists() else {}
    gcfg = (cfg.get("pipeline") or {}).get("geos_v3") or {}
    data_root = Path(args.data_root or (cfg.get("data") or {}).get("local", {}).get("root")
                     or "/data/esplab/nmme-backup")
    base = (args.hindcast_url or gcfg.get("hindcast_url") or ENSEMBLE_HINDCAST_URL).rstrip("/") + "/"
    varlist = (args.vars or gcfg.get("hindcast_variables") or " ".join(DEFAULT_VARS)).split()
    months = _parse_months(args.months)
    years = range(args.start_year, args.end_year + 1)

    jobs: List[Tuple[str, str, int, str, str]] = []
    for var in varlist:
        token = VAR_MAP.get(var, (None, None, None))[0]
        if token is None:
            log(f"{var}: not provided on the hindcast portal; skipping")
            continue
        for month in months:
            mon = MONTH_ABBR[month - 1]
            for period, (y0, y1) in HINDCAST_PERIODS.items():
                if y1 < args.start_year or y0 > args.end_year:
                    continue
                jobs.append((var, token, month, period, f"{base}{mon}/{mon}.{token}.{period}.tar"))

    log(f"source={base} root={data_root} vars={varlist} years={args.start_year}-{args.end_year} "
        f"months={months} tars={len(jobs)}")
    if args.dry_run:
        for var, _, month, _, url in jobs:
            log(f"[DRY RUN] {url}")
        return 0

    tmp_root = Path(args.tmpdir or data_root / MODEL_ID / ".tmp")
    tmp_root.mkdir(parents=True, exist_ok=True)
    tot_ok = tot_err = tot_missing = 0
    for var, token, month, period, url in jobs:
        y0, y1 = HINDCAST_PERIODS[period]
        want = [data_root / MODEL_ID / "hindcast" / var / f"{var}_{MODEL_ID}_{y}_{month:02d}.nc"
                for y in range(max(y0, args.start_year), min(y1, args.end_year) + 1)]
        if not args.overwrite and all(f.exists() for f in want):
            log(f"SKIP {var} {month:02d} {period}: all {len(want)} outputs exist")
            continue
        with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
            tar_path = Path(tmp) / "hc.tar"
            log(f"GET {url}")
            if not download_with_retry(url, tar_path, timeout=300):
                log(f"MISS {url}")
                tot_missing += 1
                continue
            try:
                ok, err = process_tar(tar_path, var, token, month, years, data_root, args.overwrite)
            except Exception as exc:  # e.g. tarfile.ReadError on a corrupt archive
                log(f"ERROR {url}: {exc}")
                ok, err = 0, 1
            tot_ok += ok
            tot_err += err

    log(f"SUM : written={tot_ok} missing_tars={tot_missing} errors={tot_err}")
    return 1 if tot_err or tot_missing else 0


if __name__ == "__main__":
    sys.exit(main())

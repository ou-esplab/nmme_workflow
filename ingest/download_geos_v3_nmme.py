#!/usr/bin/env python3
"""Download one month of NASA GEOS-S2S-3 real-time NMME forecasts from GMAO's NRT portal.

Source: {rt_url}/{mon}{yyyy}/{YYYYMMDD}.{token}.ens{N}.lead{L}.nc4 (see utils/geos_v3.py).
Only the (init, ens) pairs that run all 9 leads are kept -- that is the NMME
ensemble (10 members); GMAO's extra 2-lead members are ignored. Each field is
subsampled from the native 0.5-deg grid to the workflow's 181x360 grid and written as

  {data_root}/NASA-GEOSS2S3/forecast/{var}/{var}_NASA-GEOSS2S3_{YYYY}_{MM}.nc

A month folder that is not posted yet (HTTP 404) is logged and exits 0 so the
ingest pipeline carries on.

Usage
-----
  python ingest/download_geos_v3_nmme.py --config confignmme.yaml --init-yyyymm 202506
  python ingest/download_geos_v3_nmme.py --init-yyyymm 202609 --dry-run
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.config import load_config
from utils.geos_v3 import (
    DEFAULT_VARS, MODEL_ID, NLEADS, RT_URL, VAR_MAP,
    download_with_retry, extract_field, full_length_members, http_get,
    parse_rt_listing, regrid_native_to_1deg, rt_month_dir, to_nmme_dataset, write_atomic,
)

warnings.filterwarnings("ignore")


def log(msg: str) -> None:
    print(f"[GEOSV3] {msg}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download GEOS-S2S-3 real-time NMME forecasts")
    p.add_argument("--config", default="confignmme.yaml")
    p.add_argument("--init-yyyymm", default=date.today().strftime("%Y%m"),
                   help="Forecast (S) month, YYYYMM (default: current month)")
    p.add_argument("--data-root", default=None, help="Override data.local.root from config")
    p.add_argument("--vars", default=None, help="Space-separated vars (default: utils.geos_v3.DEFAULT_VARS)")
    p.add_argument("--rt-url", default=None, help="Override the NRT NMME monthly base URL")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="Overwrite existing output files")
    return p.parse_args()


def _load_lead(path: Path, var: str, probe: bool = False) -> np.ndarray:
    with xr.open_dataset(path, decode_times=False) as ds:  # time is unused; decoding costs ~3 s/file
        ds.load()  # lazy strided subsampling of the compressed file is ~2 s/file
        if probe:  # first file per variable: record what GMAO actually posted
            lev = ds["lev"].values.tolist() if "lev" in ds.coords else None
            log(f"{var}: first file {path.name} vars={list(ds.data_vars)} "
                f"grid={ds.sizes.get('lat')}x{ds.sizes.get('lon')} lev={lev}")
        da = extract_field(regrid_native_to_1deg(ds), var)
        if "time" in da.dims:
            da = da.isel(time=0)
        return da.transpose("lat", "lon").values


def build_var(base_url: str, listing: str, var: str, init_yyyymm: str, outfile: Path,
              workers: int, dry_run: bool) -> str:
    """Download and assemble one variable. Returns a status string."""
    token = VAR_MAP[var][0]
    if token is None:
        log(f"{var}: not provided on the NRT portal; skipping")
        return "unavailable"

    members = parse_rt_listing(listing, token)
    full = full_length_members(members)
    if not full:
        log(f"{var}: no full-length ({NLEADS}-lead) members in listing; skipping")
        return "no_members"
    log(f"{var}: {len(full)} full-length members: " + " ".join(f"{i}/ens{e}" for i, e in full))
    if len(full) != 10:
        log(f"{var}: WARN expected 10 full-length members, found {len(full)}; "
            f"GMAO's posting pattern may have changed")

    if dry_run:
        log(f"[DRY RUN] would write {outfile} ({NLEADS} leads x {len(full)} members)")
        return "dry_run"

    data = np.full((NLEADS, len(full), 181, 360), np.nan, dtype=np.float32)
    jobs: List[Tuple[int, int, str]] = []
    for m_idx, key in enumerate(full):
        for lead in range(1, NLEADS + 1):
            fname = members[key].get(lead)
            if fname is None:
                log(f"{var}: WARN {key[0]}/ens{key[1]} missing lead{lead}; NaN-padding")
                continue
            jobs.append((lead - 1, m_idx, fname))

    seen_first = False
    with tempfile.TemporaryDirectory() as tmpdir:
        # Downloads run in parallel, but files are opened on the main thread only:
        # HDF5 is not thread-safe here and threaded reads also trigger spurious
        # HDF5-DIAG output on the later to_netcdf.
        def _fetch(job: Tuple[int, int, str]) -> Tuple[int, int, Optional[Path]]:
            l_idx, m_idx, fname = job
            dest = Path(tmpdir) / fname
            return l_idx, m_idx, (dest if download_with_retry(base_url + fname, dest) else None)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for l_idx, m_idx, path in pool.map(_fetch, jobs):
                if path is None:
                    log(f"{var}: WARN download failed lead{l_idx + 1} member{m_idx + 1}; NaN-padding")
                    continue
                data[l_idx, m_idx] = _load_lead(path, var, probe=not seen_first)
                seen_first = True
                path.unlink(missing_ok=True)

    if np.isnan(data).all():
        log(f"{var}: all downloads failed; nothing written")
        return "failed"

    members_attr = " ".join(f"{i}.ens{e}" for i, e in full)
    ds = to_nmme_dataset(data, var, init_yyyymm, attrs={"members": members_attr})
    write_atomic(ds, outfile)
    log(f"{var}: wrote {outfile}")
    return "written"


def main() -> int:
    args = parse_args()
    cfg: Dict = {}
    if Path(args.config).exists():
        cfg = load_config(args.config)
    gcfg = (cfg.get("pipeline") or {}).get("geos_v3") or {}

    data_root = Path(args.data_root or (cfg.get("data") or {}).get("local", {}).get("root")
                     or "/data/esplab/nmme-backup")
    rt_url = (args.rt_url or gcfg.get("rt_url") or RT_URL).rstrip("/") + "/"
    varlist = (args.vars or gcfg.get("forecast_variables") or " ".join(DEFAULT_VARS)).split()
    init = args.init_yyyymm
    if len(init) != 6 or not init.isdigit():
        log(f"ERROR: --init-yyyymm must be YYYYMM (got {init!r})")
        return 2

    base_url = f"{rt_url}{rt_month_dir(init)}/"
    log(f"init={init} source={base_url} vars={varlist} root={data_root}")

    listing = http_get(base_url)
    if listing is None:
        log(f"{rt_month_dir(init)}/ not posted yet (404); nothing to do")
        return 0

    statuses = {}
    for var in varlist:
        if var not in VAR_MAP:
            log(f"{var}: unknown variable; skipping")
            continue
        outfile = data_root / MODEL_ID / "forecast" / var / f"{var}_{MODEL_ID}_{init[:4]}_{init[4:]}.nc"
        if outfile.exists() and not args.force:
            log(f"{var}: exists {outfile}; skipping (use --force to overwrite)")
            statuses[var] = "exists"
            continue
        try:
            statuses[var] = build_var(base_url, listing, var, init, outfile, args.workers, args.dry_run)
        except Exception as exc:
            log(f"{var}: ERROR {exc}")
            statuses[var] = "error"

    log("SUM : " + " ".join(f"{v}={s}" for v, s in statuses.items()))
    return 1 if any(s in ("error", "failed") for s in statuses.values()) else 0


if __name__ == "__main__":
    sys.exit(main())

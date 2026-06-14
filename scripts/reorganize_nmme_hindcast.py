#!/usr/bin/env python3
"""
Reorganize /data/esplab/shared/model/initialized/nmme from model-first layout
to the category-first layout used by nmme-old.

Source layout:
  {MODEL}/hindcast/{var}/{files}
  {MODEL}/reforecast/{var}/{files}   (NOAA-SFS only)

Target layout:
  hindcast/monthly/{var}/monthly/full/{MODEL}/{files}

Modes:
  dry-run  (default) — print planned operations, no writes
  copy     — create target dirs and hard-link files (falls back to copy2 if cross-device)
  verify   — compare source vs target file counts and sizes; exit non-zero on mismatch
  delete   — remove original {MODEL}/hindcast/ and {MODEL}/reforecast/ trees

Usage:
  python reorganize_nmme_hindcast.py [--mode dry-run|copy|verify|delete]
  python reorganize_nmme_hindcast.py --root /other/path --mode dry-run
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

NMME_ROOT = Path("/data/esplab/shared/model/initialized/nmme")

# Map model name → source subdir name (hindcast or reforecast)
MODEL_MAP = {
    "CanESM5":         "hindcast",
    "COLA-RSMAS-CCSM4": "hindcast",
    "COLA-RSMAS-CESM1": "hindcast",
    "GEM5.2-NEMO":     "hindcast",
    "GFDL-SPEAR":      "hindcast",
    "NASA-GEOSS2S":    "hindcast",
    "NCEP-CFSv2":      "hindcast",
    "NOAA-SFS":        "reforecast",
}


def iter_source_files(root: Path):
    """Yield (src_file, model, var) for every .nc file in the source layout."""
    for model, src_subdir in MODEL_MAP.items():
        src_base = root / model / src_subdir
        if not src_base.exists():
            print(f"  WARNING: {src_base} does not exist — skipping {model}", file=sys.stderr)
            continue
        for var_dir in sorted(src_base.iterdir()):
            if not var_dir.is_dir():
                continue
            var = var_dir.name
            for f in sorted(var_dir.iterdir()):
                if f.suffix == ".nc":
                    yield f, model, var


def dst_path(root: Path, model: str, var: str, filename: str) -> Path:
    return root / "hindcast" / "monthly" / var / "monthly" / "full" / model / filename


def mode_dry_run(root: Path):
    count = 0
    for src_file, model, var in iter_source_files(root):
        dst = dst_path(root, model, var, src_file.name)
        print(f"  {src_file}\n    -> {dst}")
        count += 1
    print(f"\nDry run complete: {count} files would be linked/copied.")


def mode_copy(root: Path):
    linked = copied = skipped = 0
    for src_file, model, var in iter_source_files(root):
        dst = dst_path(root, model, var, src_file.name)
        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists():
            skipped += 1
            continue

        try:
            os.link(src_file, dst)
            linked += 1
        except OSError:
            shutil.copy2(src_file, dst)
            copied += 1

    print(f"Copy complete: {linked} hard-linked, {copied} copied, {skipped} already present.")


def mode_verify(root: Path) -> bool:
    results = {}

    # Collect source stats
    for src_file, model, var in iter_source_files(root):
        key = (model, var)
        entry = results.setdefault(key, {"src_count": 0, "src_bytes": 0, "dst_count": 0, "dst_bytes": 0})
        entry["src_count"] += 1
        entry["src_bytes"] += src_file.stat().st_size

    # Collect target stats
    for model in MODEL_MAP:
        for var_dir in (root / "hindcast" / "monthly").iterdir():
            if not var_dir.is_dir():
                continue
            var = var_dir.name
            model_dir = var_dir / "monthly" / "full" / model
            if not model_dir.exists():
                continue
            key = (model, var)
            entry = results.setdefault(key, {"src_count": 0, "src_bytes": 0, "dst_count": 0, "dst_bytes": 0})
            for f in model_dir.iterdir():
                if f.suffix == ".nc":
                    entry["dst_count"] += 1
                    entry["dst_bytes"] += f.stat().st_size

    # Print table
    header = f"{'MODEL':<22} {'VAR':<8} {'SRC#':>6} {'DST#':>6} {'SRC_MB':>10} {'DST_MB':>10}  STATUS"
    print(header)
    print("-" * len(header))

    all_pass = True
    for (model, var), e in sorted(results.items()):
        src_mb = e["src_bytes"] / (1024 ** 2)
        dst_mb = e["dst_bytes"] / (1024 ** 2)
        ok = (e["src_count"] == e["dst_count"]) and (e["src_bytes"] == e["dst_bytes"])
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"{model:<22} {var:<8} {e['src_count']:>6} {e['dst_count']:>6} {src_mb:>10.1f} {dst_mb:>10.1f}  {status}")

    print()
    if all_pass:
        print("Verification PASSED — all file counts and sizes match.")
    else:
        print("Verification FAILED — see FAIL rows above.", file=sys.stderr)

    return all_pass


def mode_delete(root: Path):
    """Remove original {MODEL}/hindcast/ and {MODEL}/reforecast/ trees."""
    for model, src_subdir in MODEL_MAP.items():
        target = root / model / src_subdir
        if not target.exists():
            print(f"  SKIP (not found): {target}")
            continue
        print(f"  Removing {target} ...")
        shutil.rmtree(target)
        print(f"  Done: {target}")

    # Remove now-empty model dirs (only if they have no other content)
    for model in MODEL_MAP:
        model_dir = root / model
        if model_dir.exists():
            remaining = list(model_dir.iterdir())
            if not remaining:
                model_dir.rmdir()
                print(f"  Removed empty dir: {model_dir}")
            else:
                print(f"  Left intact (non-empty): {model_dir}  ({[r.name for r in remaining]})")

    print("Delete complete.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["dry-run", "copy", "verify", "delete"], default="dry-run")
    parser.add_argument("--root", type=Path, default=NMME_ROOT,
                        help="Path to nmme root directory (default: %(default)s)")
    args = parser.parse_args()

    root = args.root.resolve()
    print(f"NMME root: {root}")
    print(f"Mode:      {args.mode}\n")

    if args.mode == "dry-run":
        mode_dry_run(root)
    elif args.mode == "copy":
        mode_copy(root)
    elif args.mode == "verify":
        ok = mode_verify(root)
        sys.exit(0 if ok else 1)
    elif args.mode == "delete":
        ok = mode_verify(root)
        if not ok:
            print("\nAborting delete: verification failed. Fix mismatches first.", file=sys.stderr)
            sys.exit(1)
        print("\nVerification passed. Proceeding with delete...\n")
        mode_delete(root)


if __name__ == "__main__":
    main()

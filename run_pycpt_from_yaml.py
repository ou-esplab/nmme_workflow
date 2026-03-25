#!/usr/bin/env python3
"""
Run PyCPT (pycpt-seasonal_rt.py) for regions defined in confignmme.yaml.

This script is an ORCHESTRATOR:
- parses CLI arguments
- selects regions
- builds per-region PyCPT commands
- delegates all mechanics to utils
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from utils.config import load_config, get_region, resolve_models
from utils.exec import (
    build_pycpt_cmd,
    build_conda_shell_line,
    run_sequential,
    run_parallel,
)

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run PyCPT for all regions defined in confignmme.yaml"
    )
    ap.add_argument("config", type=Path, help="Path to confignmme.yaml")
    ap.add_argument("fcstdate", help="Forecast date YYYYMM")
    ap.add_argument("--only", nargs="+", default=None, help="Run only these regions")
    ap.add_argument("--models", nargs="+", default=None, help="Override models list")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-workers", type=int, default=1)
    ap.add_argument("--env-name", default="pycpt-2.8.2")
    ap.add_argument("--conda-init", type=Path, default=None)
    ap.add_argument("--use-mamba", action="store_true")
    args = ap.parse_args()

    cfg_path = args.config.resolve()
    if not cfg_path.exists():
        print(f"[ERROR] Config not found: {cfg_path}", file=sys.stderr)
        return 2

    cfg = load_config(cfg_path)
    regions = cfg.get("pycpt_regions", [])
    global_models = cfg.get("models", [])

    if not regions:
        print("[ERROR] No pycpt_regions defined in config.", file=sys.stderr)
        return 3

    # Select regions
    selected = (
        [get_region(regions, name) for name in args.only]
        if args.only
        else regions
    )

    shell_lines: List[str] = []

    for region in selected:
        models = resolve_models(global_models, region)

        if args.models:
            models = list(args.models)

        cmd = build_pycpt_cmd(
            script="./pycpt-seasonal_rt.py",
            config=cfg_path,
            fcstdate=args.fcstdate,
            region_name=region["name"],
            models=models,
        )

        shell_lines.append(
            build_conda_shell_line(
                cmd=cmd,
                env_name=args.env_name,
                conda_init=args.conda_init,
                use_mamba=args.use_mamba,
            )
        )

    if not shell_lines:
        print("[ERROR] No PyCPT commands generated", file=sys.stderr)
        return 4

    if args.max_workers > 1:
        return run_parallel(shell_lines, args.max_workers, args.dry_run)
    else:
        return run_sequential(shell_lines, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

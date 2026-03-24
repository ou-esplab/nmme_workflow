#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
from datetime import datetime
from pathlib import Path


def run_cmd(cmd_list, log_path):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(cmd_list, stdout=logf, stderr=logf)
        code = proc.wait()
    if code != 0:
        raise RuntimeError(
            f"Command failed ({code}): {' '.join(cmd_list)}; see {log_path}"
        )


def main() -> int:
    p = argparse.ArgumentParser(
        description="Unified runner for NMME / SubX workflows"
    )
    p.add_argument("--system", required=True, choices=["nmme", "subx"])
    p.add_argument("--config", required=True, help="Path to YAML config")
    p.add_argument(
        "--stages",
        nargs="+",
        default=["ingest", "products", "pycpt"],
        help="Stages to run in order",
    )
    p.add_argument(
        "--init",
        help="Init date: NMME=YYYYMM (or YYYYMMDD), SubX=YYYYMMDD",
    )

    # ---------- NEW FLAGS ----------
    p.add_argument(
        "--products-direct",
        action="store_true",
        help="Run products by calling MakeNMMEFcsts.py directly",
    )
    p.add_argument(
        "--products-dry-run",
        action="store_true",
        help="Run products stage without writing plots or NetCDF output",
    )
    # -------------------------------

    # PyCPT passthrough flags (unchanged)
    p.add_argument("--pycpt-env-name", default="pycpt-2.8.2")
    p.add_argument("--pycpt-conda-init", default=None)
    p.add_argument("--pycpt-use-mamba", action="store_true")
    p.add_argument("--pycpt-only", nargs="+", default=None)
    p.add_argument("--pycpt-max-workers", type=int, default=1)
    p.add_argument("--pycpt-dry-run", action="store_true")

    args = p.parse_args()

    # ---------------- Normalize init ----------------
    init_str = args.init
    if args.system == "nmme":
        if init_str is None:
            init_str = datetime.utcnow().strftime("%Y%m")
        if len(init_str) == 8:  # YYYYMMDD → YYYYMM
            init_str = init_str[:6]

    # ---------------- Logging ----------------
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    logdir = Path("logs") / stamp / args.system / init_str
    logdir.mkdir(parents=True, exist_ok=True)

    # ---------------- Dispatch ----------------
    stage_cmds = {}

    if args.system == "nmme":

        if "ingest" in args.stages:
            stage_cmds["ingest"] = ["./nmme_update_fcsts.sh"]

        if "products" in args.stages:
            if args.products_direct:
                cmd = [
                    "python",
                    "MakeNMMEFcsts.py",
                    "--date",
                    init_str,
                    "--config",
                    args.config,
                ]
                if args.products_dry_run:
                    cmd.append("--dry-run")
                stage_cmds["products"] = cmd
            else:
                suffix = " --dry-run" if args.products_dry_run else ""
                stage_cmds["products"] = [
                    "bash",
                    "-lc",
                    f"./makefcsts.sh {init_str}{suffix}",
                ]

        if "pycpt" in args.stages:
            cmd = [
                "./run_pycpt_from_yaml.py",
                args.config,
                init_str,
            ]
            if args.pycpt_only:
                cmd += ["--only", *args.pycpt_only]
            if args.pycpt_dry_run:
                cmd.append("--dry-run")
            if args.pycpt_max_workers and args.pycpt_max_workers > 1:
                cmd += ["--max-workers", str(args.pycpt_max_workers)]
            if args.pycpt_env_name:
                cmd += ["--env-name", args.pycpt_env_name]
            if args.pycpt_conda_init:
                cmd += ["--conda-init", args.pycpt_conda_init]
            if args.pycpt_use_mamba:
                cmd.append("--use-mamba")

            stage_cmds["pycpt"] = cmd

    # ---------------- Execute ----------------
    for i, stage in enumerate(args.stages, start=1):
        if stage not in stage_cmds:
            continue
        log_path = logdir / f"{i:02d}_{stage}.log"
        print(f"[RUN] {stage}: {' '.join(stage_cmds[stage])} -> {log_path}")
        run_cmd(stage_cmds[stage], str(log_path))

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
import os, sys, subprocess, json
from datetime import datetime, timedelta
import argparse, yaml
from pathlib import Path

def latest_thursday_utc():
    now = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (now - timedelta(days=(now.weekday()-3)%7)).strftime("%Y%m%d")

def first_of_month_utc(dt=None):
    dt = dt or datetime.utcnow()
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y%m%d")

def load_cfg(path):
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}

def run_cmd(cmd_list, log_path):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as log:
        proc = subprocess.Popen(cmd_list, stdout=log, stderr=subprocess.STDOUT, text=True)
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"Command failed ({code}): {' '.join(cmd_list)}; see {log_path}")
    return log_path


# --- existing helpers (e.g., run_cmd) remain unchanged ---


def main():
    p = argparse.ArgumentParser(description="Unified runner for NMME / SubX workflows")
    p.add_argument("--system", required=True, choices=["nmme", "subx"])
    p.add_argument("--config", required=True, help="Path to YAML config for the chosen system")
    p.add_argument("--stages", nargs="+", default=["ingest", "products", "pycpt"],
                   help="Stages to run in order (default: ingest products pycpt)")
    p.add_argument("--init", required=False, help="Init date: NMME=YYYYMM (or YYYYMMDD), SubX=YYYYMMDD")

    # --- NEW: PyCPT passthrough flags (work only for --system nmme and stage 'pycpt') ---
    p.add_argument("--pycpt-env-name", default=os.environ.get("PYCPT_ENV_NAME", "pycpt-2.8.2"),
                   help="Conda/Mamba env name for PyCPT (default: pycpt-2.8.2)")
    p.add_argument("--pycpt-conda-init", default=os.environ.get("PYCPT_CONDA_INIT"),
                   help="Path to conda init script (e.g., ~/miniconda3/etc/profile.d/conda.sh)")
    p.add_argument("--pycpt-use-mamba", action="store_true",
                   help="Use 'mamba activate' instead of 'conda activate'")
    p.add_argument("--pycpt-only", nargs="+", default=None,
                   help="Subset of regions to run (names must match confignmme.yaml)")
    p.add_argument("--pycpt-max-workers", type=int, default=int(os.environ.get("PYCPT_MAX_WORKERS", "1")),
                   help="Parallel region launches (>=1). Default=1.")
    p.add_argument("--pycpt-dry-run", action="store_true",
                   help="Print PyCPT commands, do not execute them")

    args = p.parse_args()

    # Normalize init for NMME: accept YYYYMM or YYYYMMDD, pass YYYYMM to downstream
    init_str = args.init
    if args.system == "nmme":
        if init_str is None:
            # default to current YYYYMM if not provided
            from datetime import datetime
            init_str = datetime.utcnow().strftime("%Y%m")
        # strip to YYYYMM if YYYYMMDD
        if len(init_str) == 8:
            init_str = init_str[:6]

    # Create log directory
    from datetime import datetime
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    logdir = os.path.join("logs", stamp, args.system, init_str)
    os.makedirs(logdir, exist_ok=True)

    def run_cmd(cmd_list, log_path):
        with open(log_path, "wb") as logf:
            proc = subprocess.Popen(cmd_list, stdout=logf, stderr=logf)
            code = proc.wait()
            if code != 0:
                raise RuntimeError(f"Command failed ({code}): {' '.join(cmd_list)}; see {log_path}")

    # --------------------- Stage dispatch --------------------- #
    stage_cmds = {}

    if args.system == "nmme":
        # 01) ingest
        if "ingest" in args.stages:
            stage_cmds["ingest"] = ["./nmme_update_fcsts.sh"]

        # 02) products
        if "products" in args.stages:
            # Keep your existing call chain (e.g., a shell or a python entry)
            # Assuming MakeNMMEFcsts.py is launched by your existing shell script or directly:
            # Prefer the same entry you currently use in the repo.
            stage_cmds["products"] = ["bash", "-lc", f"./makefcsts.sh {init_str}"]

        # 03) pycpt  (NEW: wire through run_pycpt_from_yaml.py with env activation & filters)
        if "pycpt" in args.stages:
            pycpt_cmd = [
                "./run_pycpt_from_yaml.py",
                args.config,
                init_str
            ]
            if args.pycpt_only:
                pycpt_cmd += ["--only", *args.pycpt_only]
            if args.pycpt_dry_run:
                pycpt_cmd += ["--dry-run"]
            if args.pycpt_max_workers and args.pycpt_max_workers > 1:
                pycpt_cmd += ["--max-workers", str(args.pycpt_max_workers)]
            if args.pycpt_env_name:
                pycpt_cmd += ["--env-name", args.pycpt_env_name]
            if args.pycpt_conda_init:
                pycpt_cmd += ["--conda-init", args.pycpt_conda_init]
            if args.pycpt_use_mamba:
                pycpt_cmd += ["--use-mamba"]

            stage_cmds["pycpt"] = pycpt_cmd

    elif args.system == "subx":
        # Keep existing subx stage wiring intact
        if "ingest" in args.stages:
            stage_cmds["ingest"] = ["./update_subx_fcsts.sh", args.config, init_str]
        if "products" in args.stages:
            stage_cmds["products"] = ["./make_fcsts.sh", args.config, init_str]
        if "pycpt" in args.stages:
            stage_cmds["pycpt"] = ["./pycpt_run.sh", args.config, init_str]

    # --------------------- Execute in order ------------------- #
    for i, stage in enumerate(args.stages, start=1):
        if stage not in stage_cmds:
            continue
        log_path = os.path.join(logdir, f"{i:02d}_{stage}.log")
        print(f"[RUN] {stage}: {' '.join(stage_cmds[stage])} -> {log_path}")
        run_cmd(stage_cmds[stage], log_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
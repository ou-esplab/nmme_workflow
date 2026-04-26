#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import shlex
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
        default=["ingest", "preprocess", "products", "pycpt"],
        help="Stages to run in order",
    )
    p.add_argument(
        "--init",
        help="Init date: NMME=YYYYMM (or YYYYMMDD), SubX=YYYYMMDD",
    )

    # ---------- Products flags ----------
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

    # ---------- PyCPT passthrough flags ----------
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

    def tercile_file_exists_and_not_all_nan(path):
        import xarray as xr
        from pathlib import Path

        if not Path(path).exists():
            return False
        try:
            ds = xr.open_dataset(path)
            all_nan = ds["t33"].isnull().all() and ds["t66"].isnull().all()
            ds.close()
            return not all_nan
        except Exception:
            return False

    # ---------- Special bootstrap stage (unchanged) ----------
    if args.system == "nmme":
        if "precompute_terciles" in args.stages:
            import yaml

            with open(args.config, "r") as f:
                cfg = yaml.safe_load(f)

            models = cfg.get("models", [])
            outdir = Path("tercile_thresholds")
            outdir.mkdir(exist_ok=True)

            hindcast_root = "/data/esplab/shared/model/initialized/nmme/hindcast/monthly/prec/monthly/full"
            sfs_hindcast_root = "/data/esplab/nmme-backup/NOAA-SFS/reforecast"

            from make_tercile_probability_maps import SEASON_LEADS

            vars = ["prec", "tref", "sst"]

            for model in models:
                for var in vars:
                    for season in SEASON_LEADS.keys():
                        tercile_path = outdir / f"{model}.{var}.{season}.terciles.1991-2020.nc"
                        if not tercile_file_exists_and_not_all_nan(tercile_path):
                            cmd = [
                                "python",
                                "precompute_tercile_thresholds.py",
                                "--config",
                                args.config,
                                "--hindcast-root",
                                hindcast_root,
                                "--sfs-hindcast-root",
                                sfs_hindcast_root,
                                "--outdir",
                                str(outdir),
                            ]
                            log_path = logdir / f"precompute_terciles_{model}{var}{season}.log"
                            print(f"[RUN] precompute_terciles: {' '.join(cmd)} -> {log_path}")
                            run_cmd(cmd, str(log_path))
                        else:
                            print(f"[SKIP] Tercile file exists and is valid: {tercile_path}")

    # ---------- Ingest ----------
    if "ingest" in args.stages:
        cfg_q = shlex.quote(args.config)
        init_q = shlex.quote(init_str)
        stage_cmds["ingest"] = [
            "bash",
            "-lc",
            f"NMME_CONFIG={cfg_q} INIT_YYYYMM={init_q} ingest/nmme_update_fcsts.sh",
        ]

    # ---------- Preprocess (new, thin by design) ----------
    if "preprocess" in args.stages:
        stage_cmds["preprocess"] = [
            "python",
            "preprocess/run_preprocess.py",
            "--system",
            args.system,
            "--config",
            args.config,
            "--init",
            init_str,
        ]

    # ---------- Products ----------
    if "products" in args.stages:
        if args.products_direct:
            cmd1 = [
                "python",
                "products/MakeNMMEFcsts.py",
                "--date",
                init_str,
                "--config",
                args.config,
            ]
            if args.products_dry_run:
                cmd1.append("--dry-run")

            cmd2 = [
                "python",
                "products/make_tercile_probability_maps.py",
                "--init",
                init_str,
                "--config",
                args.config,
            ]

            stage_cmds["products"] = [cmd1, cmd2]
        else:
            suffix = " --dry-run" if args.products_dry_run else ""
            stage_cmds["products"] = [
                ["bash", "-lc", f"products/makefcsts.sh {init_str}{suffix}"],
                [
                    "python",
                    "products/make_tercile_probability_maps.py",
                    "--init",
                    init_str,
                    "--config",
                    args.config,
                ],
            ]

    # ---------- PyCPT ----------
    if "pycpt" in args.stages:
        cmd = [
            "python",
            "postprocess/run_pycpt_from_yaml.py",
            args.config,
            init_str,
        ]
        if args.pycpt_only:
            cmd += ["--only", *args.pycpt_only]
        if args.pycpt_dry_run:
            cmd.append("--dry-run")
        if args.pycpt_max_workers > 1:
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

        cmds = stage_cmds[stage]
        if isinstance(cmds[0], list):
            for j, cmd in enumerate(cmds, start=1):
                log_path = logdir / f"{i:02d}{stage}{j}.log"
                print(f"[RUN] {stage} (step {j}): {' '.join(cmd)} -> {log_path}")
                run_cmd(cmd, str(log_path))
        else:
            log_path = logdir / f"{i:02d}_{stage}.log"
            print(f"[RUN] {stage}: {' '.join(cmds)} -> {log_path}")
            run_cmd(cmds, str(log_path))

    return 0


if __name__ == "__main__":
    sys.exit(main())

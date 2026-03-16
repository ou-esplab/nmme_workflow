#!/usr/bin/env python3
"""
Run PyCPT (pycpt-seasonal_rt.py) for all regions in confignmme.yaml,
activating a conda/mamba environment, with a proper dry-run mode.

Reads:
  - pycpt_regions: [{ name, lat:[min,max], lon:[min,max], season, (optional) models:[...] }, ...]
  - models: [ ... ]   # your existing global list

Calls (per region):
  ./pycpt-seasonal_rt.py confignmme.yaml YYYYMM [--only Region] [--models m1 m2 ...]
"""

import argparse, os, shlex, subprocess, sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except ImportError:
    print("PyYAML not found. Install with: pip install pyyaml", file=sys.stderr)
    raise


# ------------------------------- YAML loading ---------------------------------- #
def load_config(cfg_path: Path) -> Tuple[List[Dict[str, Any]], Optional[List[str]]]:
    with cfg_path.open("r") as f:
        cfg = yaml.safe_load(f) or {}
    regs = cfg.get("pycpt_regions", [])
    models = cfg.get("models", None)

    if not isinstance(regs, list) or not regs:
        raise ValueError("No 'pycpt_regions' list found in config.")

    required = {"name", "lat", "lon", "season"}
    for i, r in enumerate(regs):
        if not isinstance(r, dict):
            raise ValueError(f"Region #{i} is not a mapping: {r!r}")
        missing = required - set(r.keys())
        if missing:
            raise ValueError(f"Region '{r.get('name','?')}' missing keys: {missing}")
        lat = r["lat"]; lon = r["lon"]
        if not (isinstance(lat, (list, tuple)) and len(lat) == 2):
            raise ValueError(f"Region '{r['name']}' lat must be [min, max]")
        if not (isinstance(lon, (list, tuple)) and len(lon) == 2):
            raise ValueError(f"Region '{r['name']}' lon must be [min, max]")

    if models is not None:
        if not isinstance(models, list):
            raise ValueError("'models' must be a list if provided.")
        models = [str(m).strip() for m in models if str(m).strip()]

    return regs, models


# ------------------------------- Selection helpers ----------------------------- #
def iter_regions(regions: List[Dict[str, Any]], only: Optional[Iterable[str]]) -> Iterable[Dict[str, Any]]:
    if not only:
        yield from regions
        return
    allow = {o.strip().lower() for o in only}
    for r in regions:
        if r["name"].strip().lower() in allow:
            yield r


def effective_models_for_region(
    region: Dict[str, Any],
    cli_models: Optional[List[str]],
    yaml_models: Optional[List[str]],
) -> Optional[List[str]]:
    # Priority: region override > top-level YAML > CLI override
    if "models" in region and region["models"]:
        rm = region["models"]
        if not isinstance(rm, list):
            raise ValueError(f"Region '{region['name']}' models is not a list.")
        return [str(m).strip() for m in rm if str(m).strip()]
    if yaml_models:
        return list(yaml_models)
    if cli_models:
        return [str(m).strip() for m in cli_models if str(m).strip()]
    return None  # -> do not pass --models; pycpt-seasonal_rt.py will decide internally


# ----------------------------- Command construction ---------------------------- #
def build_models_arg(models: Optional[List[str]]) -> List[str]:
    # pycpt-seasonal_rt.py expects: --models m1 m2 ... (space-separated), not comma-separated
    if not models:
        return []
    return ["--models", *models]


def build_pycmd_for_region(
    config_path: Path,
    fcstdate: str,
    region_name: Optional[str],
    models_list: Optional[List[str]],
) -> List[str]:
    # ./pycpt-seasonal_rt.py confignmme.yaml YYYYMM [--only Region] [--models m1 m2 ...]
    cmd = ["./pycpt-seasonal_rt.py", str(config_path), str(fcstdate)]
    if region_name:
        cmd += ["--only", region_name]
    cmd += build_models_arg(models_list)
    return cmd


def build_shell_line(
    cmd_argv: List[str],
    conda_init: Optional[Path],
    env_name: str,
    use_mamba: bool,
) -> str:
    activator = "mamba" if use_mamba else "conda"
    candidates = [
        conda_init,
        Path(os.environ.get("CONDA_PREFIX", "")) / "etc/profile.d/conda.sh",
        Path.home() / "miniconda3/etc/profile.d/conda.sh",
        Path.home() / "anaconda3/etc/profile.d/conda.sh",
        Path("/opt/conda/etc/profile.d/conda.sh"),
    ]
    profile = next((p for p in candidates if p and p.exists()), None)
    source_line = f"source {shlex.quote(str(profile))}" if profile else ":"
    py_line = " ".join(shlex.quote(x) for x in cmd_argv)
    return f"{source_line} && {activator} activate {shlex.quote(env_name)} && {py_line}"


# ------------------------------- Execution helpers ----------------------------- #
def run_one(shell_line: str, dry_run: bool = False) -> int:
    print("RUN:", shell_line, flush=True)
    if dry_run:
        return 0
    proc = subprocess.run(["bash", "-lc", shell_line])
    return proc.returncode


def run_sequential(shell_lines: List[str], dry_run: bool = False) -> int:
    rc = 0
    for line in shell_lines:
        code = run_one(line, dry_run=dry_run)
        rc = code if code != 0 else rc
    return rc


def run_parallel(shell_lines: List[str], max_workers: int, dry_run: bool = False) -> int:
    if dry_run or max_workers <= 1:
        return run_sequential(shell_lines, dry_run=dry_run)
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
    except ImportError:
        return run_sequential(shell_lines, dry_run=dry_run)
    rc = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(run_one, line, dry_run): line for line in shell_lines}
        for fut in as_completed(futs):
            code = fut.result()
            rc = code if code != 0 else rc
    return rc


# --------------------------------------- Main ---------------------------------- #
def main() -> int:
    print("RUNNER FILE:", __file__)

    ap = argparse.ArgumentParser(
        description="Run pycpt-seasonal_rt.py for all regions in confignmme.yaml, using the top-level 'models:' list; supports dry-run."
    )
    ap.add_argument("config", type=Path, help="Path to confignmme.yaml")
    ap.add_argument("fcstdate", help="Forecast date, e.g., YYYYMM")

    ap.add_argument("--only", nargs="+", default=None, help="Only these region names (exact match)")
    ap.add_argument("--dry-run", action="store_true", help="Print commands but do not execute")
    ap.add_argument("--max-workers", type=int, default=1, help="Parallel launches (>=1)")

    ap.add_argument("--env-name", default="pycpt-2.8.2", help="Conda/Mamba env name to activate")
    ap.add_argument("--conda-init", type=Path, default=None, help="Path to conda init script (e.g., ~/miniconda3/etc/profile.d/conda.sh)")
    ap.add_argument("--use-mamba", action="store_true", help="Use 'mamba activate' instead of 'conda activate'")

    # Optional CLI override (lowest priority unless you want it to win)
    ap.add_argument("--models", nargs="+", default=None, help="Override models list (space-separated)")

    args = ap.parse_args()

    cfg_path: Path = args.config.resolve()
    if not cfg_path.exists():
        print(f"[ERROR] Config not found: {cfg_path}", file=sys.stderr)
        return 2

    try:
        regions, yaml_models = load_config(cfg_path)
    except Exception as e:
        print(f"[CONFIG ERROR] {e}", file=sys.stderr)
        return 3

    cli_models = [str(m).strip() for m in args.models] if args.models else None

    selected = list(iter_regions(regions, args.only))
    if not selected:
        print("[ERROR] No regions selected (check --only filter or config).", file=sys.stderr)
        return 4

    shell_lines: List[str] = []
    for r in selected:
        eff_models = effective_models_for_region(r, cli_models, yaml_models)  # may be None
        cmd = build_pycmd_for_region(
            config_path=cfg_path,
            fcstdate=args.fcstdate,
            region_name=r["name"],
            models_list=eff_models,
        )
        shell_lines.append(
            build_shell_line(
                cmd_argv=cmd,
                conda_init=args.conda_init,
                env_name=args.env_name,
                use_mamba=args.use_mamba,
            )
        )

    if not shell_lines:
        print("[ERROR] No commands were generated.", file=sys.stderr)
        return 5

    if args.max_workers > 1:
        return run_parallel(shell_lines, max_workers=args.max_workers, dry_run=args.dry_run)
    else:
        return run_sequential(shell_lines, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
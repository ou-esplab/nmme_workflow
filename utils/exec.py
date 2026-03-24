# utils/exec.py

import os, shlex, subprocess
from pathlib import Path
from typing import List, Optional

def build_pycpt_cmd(
    script: str,
    config: Path,
    fcstdate: str,
    region_name: str,
    models: Optional[List[str]] = None,
) -> List[str]:
    cmd = [script, str(config), fcstdate, "--only", region_name]
    if models:
        cmd += ["--models", *models]
    return cmd

def build_conda_shell_line(
    cmd: List[str],
    env_name: str,
    conda_init: Optional[Path],
    use_mamba: bool,
) -> str:
    activator = "mamba" if use_mamba else "conda"
    init = f"source {conda_init}" if conda_init else ":"
    quoted = " ".join(shlex.quote(x) for x in cmd)
    return f"{init} && {activator} activate {env_name} && {quoted}"

def run_one(line: str, dry_run: bool) -> int:
    print("RUN:", line, flush=True)
    if dry_run:
        return 0
    return subprocess.run(["bash", "-lc", line]).returncode

def run_sequential(lines: List[str], dry_run: bool) -> int:
    rc = 0
    for line in lines:
        code = run_one(line, dry_run)
        if code != 0:
            rc = code
    return rc

def run_parallel(lines: List[str], max_workers: int, dry_run: bool) -> int:
    if max_workers <= 1 or dry_run:
        return run_sequential(lines, dry_run)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    rc = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for fut in as_completed(ex.submit(run_one, l, dry_run) for l in lines):
            code = fut.result()
            if code != 0:
                rc = code
    return rc
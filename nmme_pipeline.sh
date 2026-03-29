#!/bin/bash
set -euo pipefail

# Legacy wrapper for backward compatibility.
# Prefer 'python runners/cli.py --system nmme --config config.yaml' directly.

CONFIG="./config.yaml"

if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
  conda activate pycpt-2.8.2
else
  echo "[ERROR] conda initialization script not found: $HOME/miniconda3/etc/profile.d/conda.sh" >&2
  exit 1
fi

if [ ! -f "runners/cli.py" ]; then
  echo "[ERROR] runners/cli.py not found. Ensure you're in nmme_workflow repository." >&2
  exit 1
fi

# Determine date for pipeline (fallback to current month if missing)
LATEST=$(python3 - <<'PY'
import yaml, sys
cfg = yaml.safe_load(open('config.yaml'))
if 'nmme_data_root' in cfg:
    # Either existing logic or default to now
    from pathlib import Path, PurePosixPath
    # fallback; this only sets init for CLI
print('')
PY
)

# Primary pipeline exec
python3 runners/cli.py --system nmme --config "$CONFIG" --stages ingest products pycpt --init "${LATEST:-$(date +%Y%m)}"

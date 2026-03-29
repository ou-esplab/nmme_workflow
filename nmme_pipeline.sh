#!/bin/bash
set -euo pipefail

echo "[DEPRECATED] nmme_pipeline.sh is deprecated." >&2
echo "[DEPRECATED] Use runners/cli.py directly." >&2

if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
    conda activate nmme_workflow_env
fi

INIT="${1:-$(date +%Y%m)}"
CONFIG="${2:-confignmme.yaml}"

exec python3 runners/cli.py --system nmme --config "$CONFIG" --init "$INIT"

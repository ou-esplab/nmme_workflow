#!/bin/bash
set -euo pipefail

# Usage: makefcsts.sh YYYYMM [--dry-run]

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 YYYYMM [--dry-run]" >&2
    exit 2
fi

FCSTDATE="$1"
shift
EXTRA_ARGS=("$@")

CONFIG="confignmme.yaml"

# Activate environment only
. /home/kpegion/miniconda3/etc/profile.d/conda.sh
conda activate subxnmme

# Delegate to Python
python MakeNMMEFcsts.py \
    --date "${FCSTDATE}" \
    --config "${CONFIG}" \
    "${EXTRA_ARGS[@]}"
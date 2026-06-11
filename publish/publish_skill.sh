#!/usr/bin/env bash
# Deploy RPSS skill plots to the web server.
# Run from the project root after generating plots with run_plots.sh.
# Safe to re-run; existing files on the server are overwritten.

set -euo pipefail

SSH_KEY="${PUBLISH_SSH_KEY:-~/.ssh/id_ed25519}"
HOST="${PUBLISH_DEST_HOST:-somclass23}"
DEST_DIR="${PUBLISH_DEST_DIR:-/data/web/kpegion/http/nmme/forecasts}"
SKILL_SRC="${SKILL_SRC:-/data/esplab/shared/model/initialized/nmme/skill/1991-2020/plots}"

ssh_key="${SSH_KEY/#\~/$HOME}"

echo "=== Deploying RPSS skill plots ==="
echo "Source : ${SKILL_SRC}"
echo "Dest   : ${HOST}:${DEST_DIR}/skill/plots/"
echo ""

# Create remote directory
ssh -i "${ssh_key}" "${HOST}" "mkdir -p '${DEST_DIR}/skill/plots'"

# Count and sync PNGs
n_plots=$(ls "${SKILL_SRC}"/*.png 2>/dev/null | wc -l)
echo "Syncing ${n_plots} PNG files..."
scp -i "${ssh_key}" "${SKILL_SRC}"/*.png "${HOST}:${DEST_DIR}/skill/plots/"

# Deploy skill.html
SKILL_HTML="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skill.html"
scp -i "${ssh_key}" "${SKILL_HTML}" "${HOST}:${DEST_DIR}/skill.html"

echo ""
echo "Done. Deployed to ${HOST}:${DEST_DIR}/skill/plots/ and ${DEST_DIR}/skill.html"

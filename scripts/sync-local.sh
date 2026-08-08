#!/usr/bin/env bash
set -euo pipefail

project_root="${HOME}/projects/web3-audit-dataset"
dataset_root="${HOME}/datasets/web3-audit-dataset"
mkdir -p "${dataset_root}/state"
exec 9>"${dataset_root}/state/sync.lock"
if ! flock -n 9; then
    echo "another scheduled sync holds the dataset lock"
    exit 0
fi
if pgrep -u "$(id -u)" -f "web3-dataset --root ${dataset_root} (solodit|github-search|github-clone|normalize-|export-rag)" >/dev/null; then
    echo "manual dataset collection is still active; skipping this scheduled run"
    exit 0
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    GITHUB_TOKEN="$(gh auth token)"
    export GITHUB_TOKEN
fi

exec "${project_root}/.venv/bin/web3-dataset" \
    --root "${dataset_root}" \
    sync --contract "${project_root}/config/solodit.json"
#!/usr/bin/env bash
set -euo pipefail

mode="${1:-}"
source_root="${2:-/mnt/hdd/web3-audit-dataset}"
destination_root="${3:-}"

if [[ "$mode" != "--dry-run" && "$mode" != "--execute" ]]; then
    echo "usage: $0 --dry-run|--execute SOURCE DESTINATION" >&2
    exit 2
fi
if [[ -z "$destination_root" || ! -d "$source_root" ]]; then
    echo "source must exist and destination must be specified" >&2
    exit 2
fi
if [[ "$source_root" == "$destination_root" ]]; then
    echo "source and destination must differ" >&2
    exit 2
fi
for command in flock rsync; do
    if ! command -v "$command" >/dev/null; then
        echo "$command is required" >&2
        exit 2
    fi
done

lock_file="$source_root/state/sync.lock"
if [[ ! -d "${lock_file%/*}" ]]; then
    echo "dataset state directory does not exist: ${lock_file%/*}" >&2
    exit 2
fi
exec 9>"$lock_file"
if ! flock -n 9; then
    echo "dataset sync lock is held; stop active writers before migration" >&2
    exit 1
fi
if pgrep -u "$(id -u)" -f "web3-dataset --root ${source_root} (solodit|github-|normalize-|export-rag|sync)" >/dev/null; then
    echo "a dataset process is active; stop it before migration" >&2
    exit 1
fi

database="$source_root/state/catalog.sqlite3"
if [[ -f "$database" ]]; then
    python3 - "$database" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1], timeout=60) as connection:
    checkpoint = tuple(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
    if checkpoint != (0, 0, 0):
        raise SystemExit(f"SQLite WAL checkpoint incomplete: {checkpoint}")
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
if result != "ok":
    raise SystemExit(f"SQLite integrity check failed: {result}")
print(f"SQLite checkpoint {checkpoint} and integrity check: ok")
PY
fi

mkdir -p "$destination_root"
rsync_options=(-aHAX --numeric-ids --info=progress2 --partial)
if [[ "$mode" == "--dry-run" ]]; then
    rsync "${rsync_options[@]}" --dry-run "$source_root/" "$destination_root/"
    exit 0
fi

rsync "${rsync_options[@]}" "$source_root/" "$destination_root/"
differences="$(mktemp)"
trap 'rm -f "$differences"' EXIT
rsync -aHAXnc --numeric-ids --delete --itemize-changes \
    "$source_root/" "$destination_root/" >"$differences"
if [[ -s "$differences" ]]; then
    cat "$differences" >&2
    echo "checksum verification failed; source was not modified" >&2
    exit 1
fi
echo "checksum verification: ok"
echo "source retained at $source_root"
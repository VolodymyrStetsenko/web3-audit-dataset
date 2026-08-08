# Operations

## Full synchronization

```bash
.venv/bin/web3-dataset \
  --root ~/datasets/web3-audit-dataset \
  sync --contract config/solodit.json
```

The run collects Solodit deltas, discovers and prunes GitHub candidates, clones accepted repositories, downloads linked reports, normalizes all sources, rebuilds FTS, and exports RAG chunks.

Use `--include-unlicensed` only for a private research corpus after accepting the resulting rights-review obligation.

## Individual stages

```bash
.venv/bin/web3-dataset solodit --contract config/solodit.json
.venv/bin/web3-dataset github-search --since 2015-01-01
.venv/bin/web3-dataset github-prune
.venv/bin/web3-dataset github-prune --execute
.venv/bin/web3-dataset github-clone
.venv/bin/web3-dataset download-solodit-reports
.venv/bin/web3-dataset normalize-solodit
.venv/bin/web3-dataset normalize-github
.venv/bin/web3-dataset normalize-reports
.venv/bin/web3-dataset export-rag
```

`github-prune` is a dry run unless `--execute` is supplied.

## Daily synchronization

```bash
mkdir -p ~/.config/systemd/user
cp ops/web3-audit-dataset.service ops/web3-audit-dataset.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now web3-audit-dataset.timer
systemctl --user list-timers web3-audit-dataset.timer
```

The timer is persistent and adds a randomized delay of up to 30 minutes. The wrapper acquires `state/sync.lock` and skips the run if a manual dataset process is active.

## Validation

```bash
python3 - ~/datasets/web3-audit-dataset/state/catalog.sqlite3 <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(connection.execute("PRAGMA integrity_check").fetchone()[0])
print(connection.execute("SELECT count(*) FROM documents").fetchone()[0])
print(connection.execute("SELECT count(*) FROM documents_fts").fetchone()[0])
connection.close()
PY
```

Document and FTS counts must match after normalization.

## SSD migration

Stop writers before creating a transfer snapshot:

```bash
systemctl --user stop web3-audit-dataset.timer web3-audit-dataset.service
./scripts/migrate-to-ssd.sh --dry-run SOURCE DESTINATION
./scripts/migrate-to-ssd.sh --execute SOURCE DESTINATION
```

The migration helper acquires the dataset lock, rejects active processes, truncates the SQLite WAL, runs `integrity_check`, copies with `rsync -aHAX`, and performs a checksum comparison. It never deletes the source.

Use ext4, XFS, or Btrfs when Unix permissions, ACLs, extended attributes, and symlinks must be preserved.
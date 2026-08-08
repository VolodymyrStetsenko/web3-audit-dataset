# Architecture

## Design goals

The pipeline is built around four requirements:

1. Collection must resume after interruption without restarting completed work.
2. Raw responses must remain independently inspectable.
3. Duplicate payloads must collapse without losing provenance.
4. Derived exports must be reproducible from the catalog and raw sources.

## Data flow

```text
Solodit API ----+
                |
GitHub API -----+--> raw storage --> normalization --> canonical documents
                |                                          |
Local reports --+                                          +--> SQLite FTS5
                                                           |
                                                           +--> chunk JSON
                                                           |
                                                           +--> RAG JSONL
```

### Collection

Collectors persist API pages and repository metadata before advancing their SQLite checkpoint. File writes use a temporary `.part` path followed by atomic replacement. GitHub discovery recursively splits date windows that reach the Search API result ceiling.

### Normalization

Solodit findings use stable finding IDs. Reports are grouped by raw SHA-256. GitHub files are grouped by raw SHA-256 across repositories and paths. The canonical GitHub document retains every accepted repository, path, commit, and URL in its provenance metadata.

Generated dependencies, build outputs, binary payloads, empty files, and files above the configured size limit are excluded from GitHub normalization.

### Indexing

SQLite stores canonical documents and an FTS5 index over title, body, and tags. Bulk normalization rebuilds FTS in one pass rather than mutating it per file. WAL mode supports readers while collection and normalization are active.

### Export

The exporter streams ordered documents from SQLite, creates stable overlapping chunks, writes individual chunk JSON, and atomically replaces the JSONL export. Large GitHub provenance arrays are not repeated in every chunk; `canonical_path`, `repository_count`, and `provenance_entries` point back to the canonical record.

## Failure model

- Interrupted raw writes leave only disposable `.part` files.
- Checkpoints advance after durable source writes.
- Normalization is idempotent and removes stale canonical rows for the processed source.
- Full synchronization records an incomplete run until export succeeds.
- The scheduled wrapper uses `flock` to prevent concurrent synchronization.

## Trust boundaries

Collected files are untrusted input. They are stored and indexed as data, never executed by the pipeline. Any downstream AI or agent system must treat retrieved source code and report text as untrusted context and must not expose shell, filesystem, wallet, or signing tools based solely on retrieved instructions.
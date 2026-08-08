# Schema

## SQLite catalog

### `checkpoints`

Stores the cursor and completion state for each source partition or orchestration stage.

| Column | Description |
| --- | --- |
| `source` | Collector or pipeline stage |
| `partition_key` | Stable partition identifier |
| `cursor` | Source-specific resume cursor |
| `completed` | Completion flag |
| `updated_at` | UTC update timestamp |

### `source_objects`

Records durable raw source objects with their path, SHA-256 digest, source timestamp, and retrieval timestamp.

### `repositories`

Tracks discovered GitHub repositories, default branch, current commit, SPDX identifier, size, local path, and clone state.

### `documents`

| Column | Description |
| --- | --- |
| `id` | Stable canonical document identifier |
| `source` | `solodit`, `github`, or `local-report` |
| `source_id` | Primary upstream identifier |
| `title` | Searchable document title |
| `body` | Normalized text |
| `metadata_json` | Source-specific provenance and rights metadata |
| `content_sha256` | SHA-256 of normalized content |
| `normalized_path` | Canonical JSON path |
| `updated_at` | UTC update timestamp |

### `documents_fts`

FTS5 virtual table over `title`, `body`, and `tags`. The canonical `id` is stored as an unindexed lookup key.

## Canonical identifiers

- Solodit: `solodit:<finding-id>`
- GitHub: `github:<raw-file-sha256>`
- Local report: stable hash-derived report ID
- Chunk: `<document-id>:<zero-padded-index>`

## RAG record

```json
{
  "id": "solodit:example-001:00000",
  "text": "Synthetic finding text.",
  "page_content": "Synthetic finding text.",
  "metadata": {
    "document_id": "solodit:example-001",
    "chunk_index": 0,
    "source": "solodit",
    "source_id": "example-001"
  }
}
```

`text` and `page_content` are intentionally identical compatibility fields. Additional metadata is source-specific and may include URLs, impact, tags, commits, repository counts, provenance counts, and rights status.

Machine-readable schemas are available in [`schemas/`](../schemas/).
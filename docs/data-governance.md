# Data Governance

## Repository boundary

This Git repository distributes the corpus-building software, schemas, synthetic examples, and operational documentation. It does not distribute a generated corpus.

The following paths are local artifacts and are permanently excluded from Git:

- `raw/`
- `normalized/`
- `exports/`
- `state/`
- `datasets/`
- SQLite databases, JSONL exports, partial files, and credentials

## Rights model

Each source has a different rights boundary:

| Source | Local use | Redistribution policy |
| --- | --- | --- |
| Solodit API | Subject to account and provider terms | Raw bulk redistribution disabled; review current provider terms |
| GitHub repositories | Subject to each repository and file license | Require source-level license and attribution review |
| Reports | Subject to publisher, author, and embedded-content rights | Require report-specific review |
| User-provided files | Determined by the operator | Operator must record the applicable rights |

Public repository visibility does not by itself permit copying, redistribution, commercial use, or model training. A repository license may also exclude embedded third-party reports, generated artifacts, vendored dependencies, or datasets.

## Safe defaults

- Full synchronization skips repositories without a declared SPDX license.
- `--include-unlicensed` is an explicit private-research override.
- Rights metadata is preserved in canonical documents.
- Report downloads record attempted URLs, selected sources, hashes, and review status.
- RAG chunks are not classified as training-cleared data.

## Publication gate

Before distributing any generated shard, an operator must:

1. Identify every source object represented by the shard.
2. Verify the applicable license at the file or report level.
3. Preserve copyright, attribution, license, and notice requirements.
4. Remove private, deleted, confidential, or access-controlled material.
5. Confirm provider terms allow bulk redistribution.
6. Scan for secrets, personal data, and local paths.
7. Publish a content manifest and cryptographic checksums.

If any step is unresolved, the shard remains private.

## Model training

Technical availability is not training permission. Training exports require a separate reviewed allowlist and must not be derived automatically from the RAG export.
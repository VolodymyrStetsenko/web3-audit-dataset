# Web3 Audit Dataset

Reproducible local corpus builder for Web3 audit findings, security reports, and exploit proof-of-concept repositories.

The project collects source material into durable raw storage, normalizes it into content-addressed documents, indexes it with SQLite FTS5, and exports retrieval-ready JSONL. It is designed for private security research, local RAG systems, and repeatable dataset engineering.

Within [Volodymyr Stetsenko's security practice](https://volodymyrstetsenko.github.io/VolodymyrStetsenko/), this repository is research infrastructure: it supports evidence retrieval and reproducible analysis but does not replace source-level security review.

> This repository contains the pipeline, schemas, tests, and operational tooling. It does not contain or distribute the generated corpus. Source licenses and provider terms remain attached to the collected material and must be reviewed before redistribution or model training.

## Capabilities

- Crash-resumable Solodit collection with stable-ID delta synchronization.
- Adaptive GitHub discovery that splits search windows at the 1,000-result limit.
- Relevance filtering, shallow partial clones, repository size limits, and clone timeouts.
- PDF validation, official-source fallback, and report provenance tracking.
- SHA-256 deduplication across reports and GitHub source files.
- Canonical JSON and attributed Markdown documents.
- SQLite WAL catalog, checkpoints, and FTS5 full-text index.
- Streaming LangChain/LlamaIndex-compatible JSONL export.
- Daily systemd synchronization and checksum-verified SSD migration.

## Validated Scale

The pipeline has been exercised on a private research corpus with the following validation snapshot:

| Metric | Count |
| --- | ---: |
| Solodit findings | 52,697 |
| Content-unique reports | 931 |
| Content-unique GitHub documents | 328,708 |
| Canonical documents / FTS rows | 382,336 |
| RAG chunks | 829,675 |
| Local storage | 89 GiB |

These numbers demonstrate tested scale; they are not bundled release contents and will change as upstream sources evolve.

## Architecture

```text
Upstream APIs and repositories
        |
        v
Atomic raw storage -----> SQLite checkpoints and provenance
        |
        v
Content normalization --> SHA-256 canonical documents
        |                         |
        |                         +--> SQLite FTS5
        v
Stable chunks ----------> Streaming RAG JSONL
```

Raw source material is the local source of truth. SQLite stores collection state, repository metadata, canonical documents, and FTS rows. Normalized files and JSONL are reproducible derivatives.

See [Architecture](docs/architecture.md), [Schema](docs/schema.md), and [Data Governance](docs/data-governance.md) for the system contracts.

## Requirements

- Python 3.11 or newer
- Git
- `rsync` for migration tooling
- A Solodit API key for Solodit collection
- Optional GitHub authentication for higher API limits

## Installation

```bash
git clone https://github.com/VolodymyrStetsenko/web3-audit-dataset.git
cd web3-audit-dataset
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Create the credential file outside the repository:

```bash
install -d -m 700 ~/.config/web3-audit-dataset
install -m 600 /dev/null ~/.config/web3-audit-dataset/credentials.env
printf 'SOLODIT_API_KEY=%s\n' 'YOUR_KEY' >> ~/.config/web3-audit-dataset/credentials.env
```

`GITHUB_TOKEN` is optional. The scheduled helper can also use an authenticated GitHub CLI session. Never commit credentials or place them under the dataset root.

## Quick Start

Initialize an external dataset directory:

```bash
.venv/bin/web3-dataset \
  --root ~/datasets/web3-audit-dataset \
  init
```

Validate the Solodit API contract before a long run:

```bash
.venv/bin/web3-dataset \
  validate-solodit \
  --contract config/solodit.json
```

Run the complete resumable pipeline:

```bash
.venv/bin/web3-dataset \
  --root ~/datasets/web3-audit-dataset \
  sync --contract config/solodit.json
```

By default, GitHub repositories without a declared SPDX license are not cloned. For private research only, they can be included explicitly:

```bash
.venv/bin/web3-dataset \
  --root ~/datasets/web3-audit-dataset \
  sync --contract config/solodit.json \
  --include-unlicensed
```

That option does not grant redistribution or training rights.

## Outputs

```text
raw/solodit/              Atomic Solodit API pages
raw/github/api/           GitHub search and repository metadata
raw/github/repos/         Shallow partial repository clones
raw/reports/              Locally authorized reports
state/catalog.sqlite3     WAL catalog, checkpoints, documents, FTS5
normalized/documents/     Canonical JSON and attributed Markdown
normalized/chunks/        Stable chunk JSON
exports/rag-chunks.jsonl  Streaming RAG export
```

Each JSONL record contains `id`, `text`, `page_content`, and `metadata`. `text` and `page_content` carry the same chunk for compatibility with common ingestion frameworks. Full merged GitHub provenance remains in the canonical document; chunk metadata stores a compact reference to it.

## Operations

Individual collection, normalization, pruning, report download, and export stages are available through `web3-dataset --help`. Daily systemd deployment and checksum-verified storage migration are documented in [Operations](docs/operations.md).

## Data Governance

Public visibility is not a license grant. Repository-level SPDX metadata may not cover embedded reports, vendored code, or third-party datasets. Solodit material is collected with attribution and provider-specific rights metadata; raw bulk redistribution is disabled by policy.

The generated corpus must be treated as a private research artifact unless every distributed item has passed source-specific rights review. See [Data Governance](docs/data-governance.md).

## Development

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/pytest -q
./scripts/check-public-tree.sh
```

Contributions must not include corpus payloads, credentials, local absolute paths, or generated SQLite/JSONL artifacts. See [Contributing](CONTRIBUTING.md) and [Security](SECURITY.md).

## License

The original code and documentation in this repository are licensed under the Apache License 2.0. Collected source material, generated corpus data, and third-party content are excluded and remain subject to their respective licenses and terms.
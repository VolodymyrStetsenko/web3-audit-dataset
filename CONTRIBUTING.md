# Contributing

## Development setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m compileall -q src tests
.venv/bin/pytest -q
./scripts/check-public-tree.sh
```

## Change requirements

- Preserve resumability and atomic file replacement.
- Keep source-specific provenance and rights metadata intact.
- Add focused tests for collector, normalization, schema, or CLI behavior changes.
- Keep corpus data outside the repository.
- Use synthetic fixtures; do not commit third-party findings, reports, repositories, or API responses.
- Do not weaken license filtering or rights-review markers without documenting the change.

## Security and privacy

Never commit credentials, tokens, private repository metadata, personal data, absolute local paths, SQLite catalogs, JSONL exports, or raw/normalized corpus files.

Run `scripts/check-public-tree.sh` before opening a pull request.
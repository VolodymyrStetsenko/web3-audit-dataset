import json
import sqlite3

import pytest

from web3_dataset.normalize import (
    chunk_markdown,
    export_rag,
    normalize_local_reports,
    normalize_repositories,
    normalize_solodit,
)
from web3_dataset.storage import Catalog, atomic_json


def test_normalization_indexes_and_exports(tmp_path) -> None:
    raw_path = tmp_path / "raw/solodit/full/page-00000001.json"
    atomic_json(
        raw_path,
        {
            "findings": [
                {
                    "id": 7,
                    "slug": "unsafe-call",
                    "title": "Unsafe call",
                    "content": "## Summary\nExternal call before state update.",
                    "impact": "HIGH",
                    "issues_issue_finders": [],
                    "issues_issuetagscore": [],
                }
            ]
        },
    )

    assert normalize_solodit(tmp_path) == 1
    catalog = Catalog(tmp_path / "state/catalog.sqlite3")
    original_updated_at = catalog.documents_by_source("solodit")["solodit:7"][
        "updated_at"
    ]
    markdown_inode = (tmp_path / "normalized/documents/solodit/7.md").stat().st_ino
    json_inode = (tmp_path / "normalized/documents/solodit/7.json").stat().st_ino
    assert normalize_solodit(tmp_path) == 1
    assert (
        catalog.documents_by_source("solodit")["solodit:7"]["updated_at"]
        == original_updated_at
    )
    with catalog.connect() as connection:
        assert connection.execute("SELECT count(*) FROM documents_fts").fetchone()[0] == 1
    assert (tmp_path / "normalized/documents/solodit/7.md").stat().st_ino == markdown_inode
    assert (tmp_path / "normalized/documents/solodit/7.json").stat().st_ino == json_inode
    assert export_rag(tmp_path, maximum=100, overlap=10) == 1

    with catalog.connect() as connection:
        match = connection.execute(
            "SELECT id FROM documents_fts WHERE documents_fts MATCH 'External'"
        ).fetchone()
    assert match["id"] == "solodit:7"
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
    row = json.loads((tmp_path / "exports/rag-chunks.jsonl").read_text())
    assert row["text"] == row["page_content"]
    assert row["metadata"]["rights"]["raw_redistribution"] is False


def test_chunking_rejects_invalid_overlap() -> None:
    try:
        chunk_markdown("text", maximum=100, overlap=100)
    except ValueError as error:
        assert "maximum" in str(error)
    else:
        raise AssertionError("invalid overlap was accepted")


def test_solodit_normalization_deduplicates_delta_overlap(tmp_path) -> None:
    finding = {
        "id": 7,
        "title": "Repeated finding",
        "content": "Same stable finding ID.",
    }
    atomic_json(tmp_path / "raw/solodit/full/page-00000001.json", {"findings": [finding]})
    atomic_json(tmp_path / "raw/solodit/delta/page-00000001.json", {"findings": [finding]})

    assert normalize_solodit(tmp_path) == 1
    assert len(Catalog(tmp_path / "state/catalog.sqlite3").documents_by_source("solodit")) == 1


def test_local_report_symlink_is_not_read(tmp_path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret outside dataset")
    reports = tmp_path / "raw/reports"
    reports.mkdir(parents=True)
    reports.joinpath("linked.md").symlink_to(outside)

    assert normalize_local_reports(tmp_path) == 0


def test_local_reports_deduplicate_content_and_merge_provenance(tmp_path) -> None:
    reports = tmp_path / "raw/reports/solodit"
    reports.mkdir(parents=True)
    reports.joinpath("a.md").write_text("# Same report\n\nFinding")
    reports.joinpath("b.md").write_text("# Same report\n\nFinding")
    atomic_json(
        tmp_path / "raw/solodit/report-manifest/a.json",
        {"url": "https://a.example/report", "finding_ids": ["1"]},
    )
    atomic_json(
        tmp_path / "raw/solodit/report-manifest/b.json",
        {"url": "https://b.example/report", "finding_ids": ["2"]},
    )

    assert normalize_local_reports(tmp_path) == 1
    documents = Catalog(tmp_path / "state/catalog.sqlite3").documents_by_source(
        "local-report"
    )
    assert len(documents) == 1
    metadata = json.loads(next(iter(documents.values()))["metadata_json"])
    assert metadata["source_ids"] == ["solodit/a.md", "solodit/b.md"]
    assert metadata["solodit_finding_ids"] == ["1", "2"]


def test_repository_normalization_skips_generated_and_empty_files(tmp_path) -> None:
    repository = tmp_path / "raw/github/repos/audit/repo"
    repository.joinpath(".git").mkdir(parents=True)
    repository.joinpath("src").mkdir()
    repository.joinpath("node_modules/package").mkdir(parents=True)
    repository.joinpath("src/Finding.sol").write_text("contract Finding {}")
    repository.joinpath("src/Empty.sol").write_text("  \n")
    repository.joinpath("node_modules/package/Dependency.sol").write_text(
        "contract Dependency {}"
    )
    metadata = {
        "id": 10,
        "full_name": "audit/repo",
        "clone_url": "https://github.com/audit/repo.git",
        "default_branch": "main",
        "size": 1,
        "license": {"spdx_id": "MIT"},
    }
    raw_path = tmp_path / "raw/github/api/repo.json"
    atomic_json(raw_path, metadata)
    catalog = Catalog(tmp_path / "state/catalog.sqlite3")
    catalog.initialize()
    catalog.record_repository(metadata, raw_path)
    catalog.update_repository_clone(
        10,
        status="cloned",
        local_path=repository,
        head_sha="abc123",
    )

    assert normalize_repositories(tmp_path) == 1


def test_repository_normalization_deduplicates_content(tmp_path) -> None:
    catalog = Catalog(tmp_path / "state/catalog.sqlite3")
    catalog.initialize()
    for github_id, full_name in ((11, "audit/one"), (12, "audit/two")):
        repository = tmp_path / "raw/github/repos" / full_name
        repository.joinpath(".git").mkdir(parents=True)
        repository.joinpath("Finding.sol").write_text("contract SameFinding {}")
        metadata = {
            "id": github_id,
            "full_name": full_name,
            "clone_url": f"https://github.com/{full_name}.git",
            "default_branch": "main",
            "size": 1,
            "license": {"spdx_id": "MIT"},
        }
        raw_path = tmp_path / f"raw/github/api/{github_id}.json"
        atomic_json(raw_path, metadata)
        catalog.record_repository(metadata, raw_path)
        catalog.update_repository_clone(
            github_id,
            status="cloned",
            local_path=repository,
            head_sha=f"sha{github_id}",
        )

    assert normalize_repositories(tmp_path) == 1
    documents = catalog.documents_by_source("github")
    assert len(documents) == 1
    metadata = json.loads(next(iter(documents.values()))["metadata_json"])
    assert metadata["source_count"] == 2
    assert metadata["repositories"] == ["audit/one", "audit/two"]
    assert export_rag(tmp_path) == 1
    chunk = json.loads((tmp_path / "exports/rag-chunks.jsonl").read_text())
    assert "sources" not in chunk["metadata"]
    assert chunk["metadata"]["repository_count"] == 2
    assert chunk["metadata"]["provenance_entries"] == 2
    assert not chunk["metadata"]["canonical_path"].startswith("/")
import asyncio
from datetime import date

from web3_dataset import github
from web3_dataset.github import DateWindow, is_relevant_repository, month_windows
from web3_dataset.storage import Catalog, atomic_json


def test_month_windows_cover_requested_range() -> None:
    assert month_windows(date(2024, 1, 30), date(2024, 3, 2)) == [
        DateWindow(date(2024, 1, 30), date(2024, 1, 31)),
        DateWindow(date(2024, 2, 1), date(2024, 2, 29)),
        DateWindow(date(2024, 3, 1), date(2024, 3, 2)),
    ]


def test_window_split_has_no_gap_or_overlap() -> None:
    window = DateWindow(date(2025, 1, 1), date(2025, 1, 10))
    left, right = window.split()
    assert left.end.toordinal() + 1 == right.start.toordinal()
    assert left.start == window.start
    assert right.end == window.end


def test_repository_relevance_rejects_generic_pentesting() -> None:
    assert is_relevant_repository(
        {
            "name": "reentrancy-poc",
            "description": "Solidity smart contract exploit",
            "owner": {"login": "researcher"},
            "language": "Solidity",
            "topics": ["ethereum"],
        }
    )
    assert is_relevant_repository(
        {
            "name": "2025-01-contest",
            "description": None,
            "owner": {"login": "code-423n4"},
            "language": "TypeScript",
            "topics": [],
        }
    )
    assert not is_relevant_repository(
        {
            "name": "PENTESTING-BIBLE",
            "description": "Web application and network penetration testing",
            "owner": {"login": "example"},
            "language": "Python",
            "topics": ["pentesting"],
        }
    )
    assert not is_relevant_repository(
        {
            "name": "2026-07-metric-dev-oyakhil-main--001",
            "owner": {"login": "example"},
            "language": "Solidity",
        }
    )
    assert not is_relevant_repository(
        {
            "name": "Web3ProjectFinder3-1--005",
            "owner": {"login": "example"},
            "language": "Solidity",
        }
    )


def test_clone_retries_remote_head_for_stale_default_branch(tmp_path, monkeypatch) -> None:
    metadata = {
        "id": 7,
        "full_name": "auditor/reports",
        "clone_url": "https://github.com/auditor/reports.git",
        "default_branch": "main",
        "size": 1,
        "license": {"spdx_id": "MIT"},
    }
    raw_path = tmp_path / "raw/github/api/repository.json"
    atomic_json(raw_path, metadata)
    catalog = Catalog(tmp_path / "state/catalog.sqlite3")
    catalog.initialize()
    catalog.record_repository(metadata, raw_path)
    calls: list[tuple[str, ...]] = []

    async def run_git(*arguments, cwd=None):
        calls.append(arguments)
        target = tmp_path / "raw/github/repos/auditor/reports"
        if "--branch" in arguments:
            target.mkdir(parents=True, exist_ok=True)
            raise RuntimeError("fatal: Remote branch main not found in upstream origin")
        if arguments[0] == "clone":
            (target / ".git").mkdir(parents=True)
        return "abc123" if arguments[:2] == ("rev-parse", "HEAD") else ""

    monkeypatch.setattr(github, "_run_git", run_git)

    result = asyncio.run(github.clone_repositories(tmp_path))

    assert result == {"cloned": 1, "updated": 0, "empty": 0, "failed": 0}
    assert calls[0][0] == "clone" and "--branch" in calls[0]
    assert calls[1][0] == "clone" and "--branch" not in calls[1]


def test_clone_classifies_repository_without_commits_as_empty(tmp_path, monkeypatch) -> None:
    metadata = {
        "id": 8,
        "full_name": "auditor/empty",
        "clone_url": "https://github.com/auditor/empty.git",
        "default_branch": "main",
        "size": 0,
        "license": {"spdx_id": "MIT"},
    }
    raw_path = tmp_path / "raw/github/api/empty.json"
    atomic_json(raw_path, metadata)
    catalog = Catalog(tmp_path / "state/catalog.sqlite3")
    catalog.initialize()
    catalog.record_repository(metadata, raw_path)

    async def run_git(*arguments, cwd=None):
        if arguments[:2] == ("rev-parse", "HEAD"):
            raise RuntimeError("fatal: ambiguous argument 'HEAD': unknown revision")
        (tmp_path / "raw/github/repos/auditor/empty/.git").mkdir(
            parents=True,
            exist_ok=True,
        )
        return ""

    monkeypatch.setattr(github, "_run_git", run_git)

    result = asyncio.run(github.clone_repositories(tmp_path))

    assert result == {"cloned": 1, "updated": 0, "empty": 1, "failed": 0}
    assert catalog.all_repositories()[0]["status"] == "empty"


def test_update_classifies_existing_repository_without_commits_as_empty(
    tmp_path, monkeypatch
) -> None:
    metadata = {
        "id": 9,
        "full_name": "auditor/existing-empty",
        "clone_url": "https://github.com/auditor/existing-empty.git",
        "default_branch": "main",
        "size": 0,
        "license": {"spdx_id": "MIT"},
    }
    raw_path = tmp_path / "raw/github/api/existing-empty.json"
    atomic_json(raw_path, metadata)
    catalog = Catalog(tmp_path / "state/catalog.sqlite3")
    catalog.initialize()
    catalog.record_repository(metadata, raw_path)
    target = tmp_path / "raw/github/repos/auditor/existing-empty"
    (target / ".git").mkdir(parents=True)

    async def run_git(*arguments, cwd=None):
        if arguments[0] == "reset":
            raise RuntimeError("")
        if arguments[:2] == ("rev-parse", "HEAD"):
            raise RuntimeError("fatal: ambiguous argument 'HEAD': unknown revision")
        return ""

    monkeypatch.setattr(github, "_run_git", run_git)

    result = asyncio.run(github.clone_repositories(tmp_path))

    assert result == {"cloned": 0, "updated": 0, "empty": 1, "failed": 0}
    assert catalog.all_repositories()[0]["status"] == "empty"
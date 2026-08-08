from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from .http import AsyncJsonClient
from .storage import Catalog, atomic_json


GITHUB_API = "https://api.github.com"
DEFAULT_QUERIES = (
    "org:code-423n4",
    "org:sherlock-audit",
    "org:CodeHawks-Contests",
    "org:cantina-competitions",
    "org:immunefi-team",
    "org:SunWeb3Sec",
    '"Immunefi-bug-bounty-writeups-list" in:name',
    "topic:smart-contract-audit",
    "topic:audit-reports blockchain",
    '"smart contract audit reports" in:name,description',
    '"blockchain security audit" reports in:name,description',
    '"security review" solidity in:name,description',
    "topic:smart-contract-hack",
    "topic:exploit topic:poc",
    '"smart contract" exploit poc in:name,description',
    "solidity exploit poc in:name,description",
    "solana exploit poc in:name,description language:Rust",
    "circom vulnerability poc in:name,description",
    "noir vulnerability poc in:name,description",
    "move exploit poc in:name,description",
)

CURATED_OWNERS = {
    "cantina-competitions",
    "code-423n4",
    "codehawks-contests",
    "immunefi-team",
    "sherlock-audit",
    "sunweb3sec",
}
RELEVANT_LANGUAGES = {"cairo", "circom", "move", "solidity", "vyper"}
RELEVANCE_PATTERN = re.compile(
    r"\b(?:blockchain|cairo|cantina|circom|code4rena|codehawks|defi|ethereum|evm|"
    r"foundry|hardhat|immunefi|noir|reentrancy|sherlock|smart[- ]?contract|solana|"
    r"solidity|starknet|vyper|web3)\b",
    re.IGNORECASE,
)
GENERATED_REPOSITORY_PATTERN = re.compile(
    r"^(?:2026-07-metric-dev-oyakhil-main--\d+|Web3ProjectFinder3-1--\d+)$",
    re.IGNORECASE,
)


def is_relevant_repository(repository: dict[str, Any]) -> bool:
    name = str(repository.get("name") or "")
    if GENERATED_REPOSITORY_PATTERN.fullmatch(name):
        return False
    owner = str((repository.get("owner") or {}).get("login") or "").lower()
    if owner in CURATED_OWNERS:
        return True
    language = str(repository.get("language") or "").lower()
    if language in RELEVANT_LANGUAGES:
        return True
    searchable = " ".join(
        [
            name,
            str(repository.get("description") or ""),
            " ".join(str(topic) for topic in repository.get("topics") or []),
        ]
    )
    return bool(RELEVANCE_PATTERN.search(searchable))


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date

    @property
    def key(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"

    def split(self) -> tuple["DateWindow", "DateWindow"]:
        if self.start >= self.end:
            raise ValueError(f"cannot split single-day window {self.key}")
        midpoint = self.start + (self.end - self.start) // 2
        return DateWindow(self.start, midpoint), DateWindow(midpoint + timedelta(days=1), self.end)


def month_windows(start: date, end: date) -> list[DateWindow]:
    windows: list[DateWindow] = []
    current = start
    while current <= end:
        month_end = date(current.year, current.month, monthrange(current.year, current.month)[1])
        window_end = min(month_end, end)
        windows.append(DateWindow(current, window_end))
        current = window_end + timedelta(days=1)
    return windows


def github_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "web3-audit-dataset/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def discover_repositories(
    root: Path,
    *,
    start: date,
    end: date,
    queries: tuple[str, ...] = DEFAULT_QUERIES,
    refresh_after: date | None = None,
) -> int:
    catalog = Catalog(root / "state" / "catalog.sqlite3")
    catalog.initialize()
    timeout = aiohttp.ClientTimeout(total=90, connect=20)
    connector = aiohttp.TCPConnector(limit=4)
    discovered: set[int] = set()

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        client = AsyncJsonClient(
            session,
            concurrency=1,
            minimum_interval=2.1 if os.environ.get("GITHUB_TOKEN") else 6.1,
        )

        async def fetch(query: str, window: DateWindow, page: int) -> dict[str, Any]:
            partition = hashlib.sha256(query.encode()).hexdigest()[:12]
            payload, _ = await client.request_json(
                "GET",
                f"{GITHUB_API}/search/repositories",
                headers=github_headers(),
                params={
                    "q": f"{query} pushed:{window.key} archived:false fork:false",
                    "sort": "updated",
                    "order": "asc",
                    "per_page": 100,
                    "page": page,
                },
            )
            raw_path = (
                root
                / "raw/github/api"
                / partition
                / window.key
                / f"page-{page:04d}.json"
            )
            digest = atomic_json(raw_path, payload)
            catalog.record_source_object(
                "github-search-page",
                f"{partition}:{window.key}:{page}",
                raw_path,
                digest,
            )
            return payload

        async def process_window(query: str, window: DateWindow) -> None:
            partition = f"{hashlib.sha256(query.encode()).hexdigest()[:12]}:{window.key}"
            checkpoint = catalog.checkpoint("github-search", partition)
            refresh = refresh_after is not None and window.end >= refresh_after
            if checkpoint and checkpoint["completed"] and not refresh:
                return
            first = await fetch(query, window, 1)
            total = int(first.get("total_count", 0))
            if total > 1000:
                if window.start == window.end:
                    raise RuntimeError(
                        f"GitHub search exceeds 1,000 results for single day {window.key}: {query}"
                    )
                left, right = window.split()
                await process_window(query, left)
                await process_window(query, right)
                catalog.save_checkpoint("github-search", partition, None, True)
                return
            total_pages = min((total + 99) // 100, 10)
            pages = [first]
            for page in range(2, total_pages + 1):
                pages.append(await fetch(query, window, page))
            for page_payload in pages:
                for repository in page_payload.get("items", []):
                    if not is_relevant_repository(repository):
                        continue
                    github_id = int(repository["id"])
                    raw_path = root / "raw/github/api/repos" / f"{github_id}.json"
                    digest = atomic_json(raw_path, repository)
                    catalog.record_source_object(
                        "github-repository", str(github_id), raw_path, digest, repository.get("updated_at")
                    )
                    catalog.record_repository(repository, raw_path)
                    discovered.add(github_id)
            catalog.save_checkpoint("github-search", partition, str(total_pages), True)

        for query in queries:
            await process_window(query, DateWindow(start, end))
    return len(discovered)


async def _run_git(*arguments: str, cwd: Path | None = None) -> str:
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_HTTP_LOW_SPEED_LIMIT": "1000",
        "GIT_HTTP_LOW_SPEED_TIME": "60",
    }
    process = await asyncio.create_subprocess_exec(
        "git",
        *arguments,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=900)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("git operation exceeded 900 seconds")
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-4000:])
    return stdout.decode().strip()


async def clone_repositories(
    root: Path,
    *,
    concurrency: int = 3,
    include_unlicensed: bool = False,
    maximum_size_kb: int = 1_000_000,
) -> dict[str, int]:
    catalog = Catalog(root / "state" / "catalog.sqlite3")
    catalog.initialize()
    repositories = catalog.repositories_to_clone(include_unlicensed, maximum_size_kb)
    semaphore = asyncio.Semaphore(concurrency)
    counts = {"cloned": 0, "updated": 0, "empty": 0, "failed": 0}

    async def clone_one(repository: Any) -> None:
        owner, name = repository["full_name"].split("/", 1)
        target = root / "raw/github/repos" / owner / name
        try:
            async with semaphore:
                if (target / ".git").is_dir():
                    try:
                        await _run_git(
                            "fetch",
                            "--depth=1",
                            "origin",
                            repository["default_branch"],
                            cwd=target,
                        )
                    except RuntimeError as error:
                        if "couldn't find remote ref" not in str(error):
                            raise
                        await _run_git("fetch", "--depth=1", "origin", cwd=target)
                    try:
                        await _run_git("reset", "--hard", "FETCH_HEAD", cwd=target)
                    except RuntimeError:
                        try:
                            await _run_git("rev-parse", "HEAD", cwd=target)
                        except RuntimeError as head_error:
                            if "ambiguous argument 'HEAD'" not in str(head_error):
                                raise
                            catalog.update_repository_clone(
                                repository["github_id"],
                                status="empty",
                                local_path=target,
                            )
                            counts["empty"] += 1
                            return
                        raise
                    counts["updated"] += 1
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        await _run_git(
                            "clone",
                            "--depth=1",
                            "--filter=blob:none",
                            "--single-branch",
                            "--branch",
                            repository["default_branch"],
                            repository["clone_url"],
                            str(target),
                        )
                    except RuntimeError as error:
                        if "Remote branch" not in str(error) or "not found" not in str(error):
                            raise
                        if target.exists():
                            shutil.rmtree(target)
                        await _run_git(
                            "clone",
                            "--depth=1",
                            "--filter=blob:none",
                            "--single-branch",
                            repository["clone_url"],
                            str(target),
                        )
                    counts["cloned"] += 1
                try:
                    head_sha = await _run_git("rev-parse", "HEAD", cwd=target)
                except RuntimeError as error:
                    if "ambiguous argument 'HEAD'" not in str(error):
                        raise
                    catalog.update_repository_clone(
                        repository["github_id"],
                        status="empty",
                        local_path=target,
                    )
                    counts["empty"] += 1
                    return
                catalog.update_repository_clone(
                    repository["github_id"],
                    status="cloned",
                    local_path=target,
                    head_sha=head_sha,
                )
        except Exception as error:
            counts["failed"] += 1
            catalog.update_repository_clone(repository["github_id"], status="failed")
            print(f"clone failed for {repository['full_name']}: {error}", file=sys.stderr)

    await asyncio.gather(*(clone_one(repository) for repository in repositories))
    return counts


def prune_irrelevant_repositories(root: Path, *, execute: bool = False) -> dict[str, int]:
    catalog = Catalog(root / "state/catalog.sqlite3")
    catalog.initialize()
    repository_root = (root / "raw/github/repos").resolve()
    counts = {"kept": 0, "removed": 0}
    for repository in catalog.all_repositories():
        raw_path = Path(repository["raw_path"])
        try:
            metadata = json.loads(raw_path.read_text())
        except (OSError, json.JSONDecodeError):
            metadata = {
                "name": repository["full_name"],
                "owner": {"login": repository["full_name"].split("/", 1)[0]},
            }
        if is_relevant_repository(metadata):
            counts["kept"] += 1
            if raw_path.is_file():
                catalog.record_repository(metadata, raw_path)
            continue
        counts["removed"] += 1
        if not execute:
            continue
        local_path_value = repository["local_path"]
        if local_path_value:
            local_path = Path(local_path_value).resolve()
        else:
            owner, name = repository["full_name"].split("/", 1)
            local_path = (repository_root / owner / name).resolve()
        if local_path.is_relative_to(repository_root) and local_path.exists():
            shutil.rmtree(local_path)
        raw_path.unlink(missing_ok=True)
        catalog.delete_repository(repository["github_id"])
    return counts


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()
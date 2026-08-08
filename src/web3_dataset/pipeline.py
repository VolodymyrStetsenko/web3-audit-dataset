from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .github import (
    clone_repositories,
    discover_repositories,
    prune_irrelevant_repositories,
)
from .normalize import export_rag, normalize_local_reports, normalize_repositories, normalize_solodit
from .reports import download_solodit_reports
from .solodit import SoloditContract, collect_solodit
from .storage import Catalog


async def synchronize(
    root: Path,
    contract: SoloditContract,
    *,
    solodit_concurrency: int = 4,
    github_initial_date: date = date(2015, 1, 1),
    include_unlicensed: bool = False,
) -> dict[str, Any]:
    catalog = Catalog(root / "state/catalog.sqlite3")
    catalog.initialize()
    active_run = catalog.checkpoint("sync-run", "daily")
    if active_run and not active_run["completed"] and active_run["cursor"]:
        run_cutoff = str(active_run["cursor"])
    else:
        run_cutoff = datetime.now(UTC).isoformat()
        catalog.save_checkpoint("sync-run", "daily", run_cutoff, False)

    solodit_cursor = catalog.checkpoint("sync", "solodit")
    if solodit_cursor and solodit_cursor["cursor"]:
        cursor_time = datetime.fromisoformat(str(solodit_cursor["cursor"]))
        solodit_since = (cursor_time - timedelta(days=2)).isoformat()
    else:
        solodit_since = None
    solodit_count = await collect_solodit(
        root,
        contract,
        since=solodit_since,
        concurrency=solodit_concurrency,
    )
    catalog.save_checkpoint("sync", "solodit", run_cutoff, True)

    github_cursor = catalog.checkpoint("sync", "github")
    if github_cursor and github_cursor["cursor"]:
        github_start = date.fromisoformat(str(github_cursor["cursor"])) - timedelta(days=2)
        refresh_after = github_start
    else:
        github_start = github_initial_date
        refresh_after = None
    today = date.today()
    github_count = await discover_repositories(
        root,
        start=github_start,
        end=today,
        refresh_after=refresh_after,
    )
    catalog.save_checkpoint("sync", "github", today.isoformat(), True)
    prune_counts = prune_irrelevant_repositories(root, execute=True)
    clone_counts = await clone_repositories(
        root,
        include_unlicensed=include_unlicensed,
    )
    report_counts = await download_solodit_reports(root)

    result = {
        "solodit_collected": solodit_count,
        "github_discovered": github_count,
        "github_pruned": prune_counts,
        "github_clones": clone_counts,
        "solodit_reports": report_counts,
        "solodit_normalized": normalize_solodit(root),
        "github_normalized": normalize_repositories(root),
        "reports_normalized": normalize_local_reports(root),
    }
    result["chunks"] = export_rag(root)
    catalog.save_checkpoint("sync-run", "daily", run_cutoff, True)
    return result
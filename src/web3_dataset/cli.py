from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import DEFAULT_CREDENTIALS, load_credentials
from .github import (
    clone_repositories,
    discover_repositories,
    parse_date,
    prune_irrelevant_repositories,
)
from .normalize import (
    export_rag,
    normalize_local_reports,
    normalize_repositories,
    normalize_solodit,
)
from .pipeline import synchronize
from .reports import download_solodit_reports
from .solodit import SoloditContract, collect_solodit, validate_contract
from .storage import Catalog


DATA_DIRS = (
    "raw/solodit",
    "raw/github/api",
    "raw/github/repos",
    "raw/reports",
    "normalized/documents",
    "normalized/chunks",
    "indexes",
    "exports",
    "state",
    "logs",
)


def initialize(root: Path) -> None:
    for relative in DATA_DIRS:
        (root / relative).mkdir(parents=True, exist_ok=True)

    Catalog(root / "state" / "catalog.sqlite3").initialize()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="web3-dataset")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.home() / "datasets/web3-audit-dataset",
        help="Dataset root (default: ~/datasets/web3-audit-dataset)",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS,
        help=f"Credential file (default: {DEFAULT_CREDENTIALS})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create directories and state database")
    validate = subparsers.add_parser(
        "validate-solodit", help="Validate a vendor-provided Solodit API contract"
    )
    validate.add_argument("--contract", type=Path, required=True)
    validate.add_argument("--since")
    solodit = subparsers.add_parser("solodit", help="Download Solodit API pages")
    solodit.add_argument("--contract", type=Path, required=True)
    solodit.add_argument("--since")
    solodit.add_argument("--concurrency", type=int, default=4)
    github_search = subparsers.add_parser(
        "github-search", help="Discover exploit PoC repositories through GitHub Search"
    )
    github_search.add_argument("--since", default="2015-01-01")
    github_search.add_argument("--until", default=str(__import__("datetime").date.today()))
    github_clone = subparsers.add_parser(
        "github-clone", help="Clone or update discovered PoC repositories"
    )
    github_clone.add_argument("--concurrency", type=int, default=3)
    github_clone.add_argument("--include-unlicensed", action="store_true")
    github_clone.add_argument("--maximum-size-kb", type=int, default=1_000_000)
    github_prune = subparsers.add_parser(
        "github-prune", help="Remove repositories unrelated to blockchain security"
    )
    github_prune.add_argument("--execute", action="store_true")
    subparsers.add_parser("normalize-solodit", help="Normalize downloaded Solodit pages")
    subparsers.add_parser("normalize-reports", help="Normalize Markdown, JSON, text, and PDF reports")
    normalize_github = subparsers.add_parser(
        "normalize-github", help="Normalize source files from cloned PoC repositories"
    )
    normalize_github.add_argument("--maximum-file-bytes", type=int, default=1_000_000)
    reports = subparsers.add_parser(
        "download-solodit-reports", help="Download unique PDF reports linked by Solodit"
    )
    reports.add_argument("--concurrency", type=int, default=2)
    reports.add_argument("--maximum-bytes", type=int, default=100_000_000)
    export = subparsers.add_parser("export-rag", help="Build stable chunks and RAG JSONL")
    export.add_argument("--chunk-chars", type=int, default=6000)
    export.add_argument("--overlap-chars", type=int, default=600)
    sync = subparsers.add_parser("sync", help="Run crash-resumable full or delta synchronization")
    sync.add_argument("--contract", type=Path, required=True)
    sync.add_argument("--solodit-concurrency", type=int, default=4)
    sync.add_argument("--include-unlicensed", action="store_true")
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    load_credentials(arguments.credentials)
    if arguments.command == "init":
        initialize(arguments.root)
        print(arguments.root)
    elif arguments.command == "validate-solodit":
        result = validate_contract(
            SoloditContract.load(arguments.contract), arguments.since
        )
        print(json.dumps(result, sort_keys=True))
    elif arguments.command == "solodit":
        initialize(arguments.root)
        count = asyncio.run(
            collect_solodit(
                arguments.root,
                SoloditContract.load(arguments.contract),
                since=arguments.since,
                concurrency=arguments.concurrency,
            )
        )
        print(json.dumps({"collected": count}))
    elif arguments.command == "github-search":
        initialize(arguments.root)
        count = asyncio.run(
            discover_repositories(
                arguments.root,
                start=parse_date(arguments.since),
                end=parse_date(arguments.until),
            )
        )
        print(json.dumps({"discovered": count}))
    elif arguments.command == "github-clone":
        initialize(arguments.root)
        result = asyncio.run(
            clone_repositories(
                arguments.root,
                concurrency=arguments.concurrency,
                include_unlicensed=arguments.include_unlicensed,
                maximum_size_kb=arguments.maximum_size_kb,
            )
        )
        print(json.dumps(result, sort_keys=True))
    elif arguments.command == "github-prune":
        initialize(arguments.root)
        print(
            json.dumps(
                prune_irrelevant_repositories(arguments.root, execute=arguments.execute),
                sort_keys=True,
            )
        )
    elif arguments.command == "normalize-solodit":
        initialize(arguments.root)
        print(json.dumps({"normalized": normalize_solodit(arguments.root)}))
    elif arguments.command == "normalize-reports":
        initialize(arguments.root)
        print(json.dumps({"normalized": normalize_local_reports(arguments.root)}))
    elif arguments.command == "normalize-github":
        initialize(arguments.root)
        print(
            json.dumps(
                {
                    "normalized": normalize_repositories(
                        arguments.root, arguments.maximum_file_bytes
                    )
                }
            )
        )
    elif arguments.command == "download-solodit-reports":
        initialize(arguments.root)
        result = asyncio.run(
            download_solodit_reports(
                arguments.root,
                concurrency=arguments.concurrency,
                maximum_bytes=arguments.maximum_bytes,
            )
        )
        print(json.dumps(result, sort_keys=True))
    elif arguments.command == "export-rag":
        initialize(arguments.root)
        count = export_rag(
            arguments.root,
            maximum=arguments.chunk_chars,
            overlap=arguments.overlap_chars,
        )
        print(json.dumps({"chunks": count}))
    elif arguments.command == "sync":
        initialize(arguments.root)
        result = asyncio.run(
            synchronize(
                arguments.root,
                SoloditContract.load(arguments.contract),
                solodit_concurrency=arguments.solodit_concurrency,
                include_unlicensed=arguments.include_unlicensed,
            )
        )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
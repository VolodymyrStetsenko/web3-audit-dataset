from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .http import AsyncJsonClient
from .storage import Catalog, atomic_bytes, atomic_json


KNOWN_DOWNLOAD_ALIASES = {
    "https://raw.githubusercontent.com/trailofbits/publications/master/reviews/2025-02-chainlinklabs-customsendersreceivers-securityreview.pdf":
        "https://raw.githubusercontent.com/trailofbits/publications/master/reviews/2025-02-chainlink-customsendersreceivers-securityreview.pdf",
}


def _public_http_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return parsed.hostname.lower() != "localhost"
    return address.is_global


def _download_url(value: str) -> str:
    parsed = urlparse(value)
    components = parsed.path.strip("/").split("/")
    if (
        parsed.hostname == "github.com"
        and len(components) >= 5
        and components[2] == "blob"
    ):
        owner, repository, _, reference, *path = components
        value = (
            f"https://raw.githubusercontent.com/{owner}/{repository}/"
            f"{reference}/{'/'.join(path)}"
        )
    return KNOWN_DOWNLOAD_ALIASES.get(value, value)


def solodit_report_links(root: Path) -> dict[str, set[str]]:
    links: dict[str, set[str]] = {}
    for raw_path in sorted((root / "raw/solodit").glob("**/page-*.json")):
        payload = json.loads(raw_path.read_text())
        for finding in payload.get("findings", []):
            url = finding.get("pdf_link")
            if isinstance(url, str) and _public_http_url(url):
                links.setdefault(url, set()).add(str(finding["id"]))
    return links


def _solodit_report_sources(root: Path) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for raw_path in sorted((root / "raw/solodit").glob("**/page-*.json")):
        payload = json.loads(raw_path.read_text())
        for finding in payload.get("findings", []):
            url = finding.get("pdf_link")
            if not isinstance(url, str) or not _public_http_url(url):
                continue
            source = sources.setdefault(url, {"finding_ids": set(), "fallbacks": set()})
            source["finding_ids"].add(str(finding["id"]))
            fallback = finding.get("source_link")
            if (
                isinstance(fallback, str)
                and fallback != url
                and urlparse(fallback).path.lower().endswith(".pdf")
                and _public_http_url(fallback)
            ):
                source["fallbacks"].add(fallback)
    return sources


async def download_solodit_reports(
    root: Path,
    *,
    concurrency: int = 2,
    maximum_bytes: int = 100_000_000,
) -> dict[str, int]:
    catalog = Catalog(root / "state/catalog.sqlite3")
    catalog.initialize()
    sources = _solodit_report_sources(root)
    counts = {"downloaded": 0, "existing": 0, "failed": 0}
    timeout = aiohttp.ClientTimeout(total=180, connect=30)
    connector = aiohttp.TCPConnector(limit=max(concurrency * 2, 4))
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        client = AsyncJsonClient(
            session,
            concurrency=concurrency,
            attempts=5,
            minimum_interval=0.5,
        )

        async def download(
            url: str, finding_ids: set[str], fallbacks: set[str]
        ) -> None:
            identifier = hashlib.sha256(url.encode()).hexdigest()
            target = root / "raw/reports/solodit" / f"{identifier}.pdf"
            manifest_path = root / "raw/solodit/report-manifest" / f"{identifier}.json"
            manifest = {
                "url": url,
                "download_urls": [_download_url(item) for item in [url, *sorted(fallbacks)]],
                "finding_ids": sorted(finding_ids),
                "rights_status": "source_terms_review_required",
            }
            if target.is_file():
                manifest["status"] = "existing"
                atomic_json(manifest_path, manifest)
                counts["existing"] += 1
                return
            errors: dict[str, str] = {}
            for download_url in manifest["download_urls"]:
                try:
                    payload, headers = await client.request_bytes(
                        "GET",
                        download_url,
                        headers={"Accept": "application/pdf", "User-Agent": "web3-audit-dataset/0.1"},
                        maximum_bytes=maximum_bytes,
                    )
                    content_type = headers.get("Content-Type", "").lower()
                    if not payload.startswith(b"%PDF-") or "html" in content_type:
                        raise ValueError("linked resource is not a PDF")
                    digest = atomic_bytes(target, payload)
                    manifest["status"] = "downloaded"
                    manifest["selected_url"] = download_url
                    manifest["content_sha256"] = digest
                    atomic_json(manifest_path, manifest)
                    catalog.record_source_object(
                        "solodit-report", identifier, target, digest
                    )
                    counts["downloaded"] += 1
                    return
                except Exception as error:
                    errors[download_url] = str(error)[:2000]
            manifest["status"] = "failed"
            manifest["errors"] = errors
            atomic_json(manifest_path, manifest)
            counts["failed"] += 1

        await asyncio.gather(
            *(
                download(url, source["finding_ids"], source["fallbacks"])
                for url, source in sources.items()
            )
        )
    return counts
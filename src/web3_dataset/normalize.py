from __future__ import annotations

import hashlib
import json
import os
import re
from itertools import groupby
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .storage import Catalog, atomic_json, atomic_text


def _nested_name(value: Any, *keys: str) -> str | None:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return str(current) if current is not None else None


def _solodit_metadata(finding: dict[str, Any]) -> dict[str, Any]:
    authors = sorted(
        {
            handle
            for item in finding.get("issues_issue_finders") or []
            if (handle := _nested_name(item, "wardens_warden", "handle"))
        }
    )
    tags = sorted(
        {
            title
            for item in finding.get("issues_issuetagscore") or []
            if (title := _nested_name(item, "tags_tag", "title"))
        }
    )
    categories = sorted(
        {
            title
            for item in (
                (finding.get("protocols_protocol") or {}).get(
                    "protocols_protocolcategoryscore", []
                )
            )
            if (title := _nested_name(item, "protocols_protocolcategory", "title"))
        }
    )
    slug = finding.get("slug")
    report_date = finding.get("report_date")
    if not isinstance(report_date, (str, int, float)):
        report_date = None
    return {
        "source": "solodit",
        "source_id": str(finding["id"]),
        "source_url": f"https://solodit.cyfrin.io/issues/{slug}" if slug else None,
        "source_link": finding.get("source_link"),
        "github_link": finding.get("github_link"),
        "pdf_link": finding.get("pdf_link"),
        "impact": finding.get("impact"),
        "report_date": report_date,
        "firm": finding.get("firm_name")
        or _nested_name(finding, "auditfirms_auditfirm", "name"),
        "protocol": finding.get("protocol_name")
        or _nested_name(finding, "protocols_protocol", "name"),
        "protocol_categories": categories,
        "authors": authors,
        "tags": tags,
        "quality_score": finding.get("quality_score"),
        "rarity_score": finding.get("general_score"),
        "content_format": "markdown",
        "rights": {
            "provider": "Solodit",
            "attribution_required": True,
            "raw_redistribution": False,
            "terms_url": "https://solodit.cyfrin.io/terms-of-service",
        },
    }


def _markdown_document(title: str, body: str, metadata: dict[str, Any]) -> str:
    attribution = "Data from Solodit (https://solodit.cyfrin.io/)" if metadata["source"] == "solodit" else f"Source: {metadata.get('source_url') or metadata['source_id']}"
    fields = [
        f"# {title}",
        "",
        f"> {attribution}",
        f"> Source ID: {metadata['source_id']}",
    ]
    if metadata.get("impact"):
        fields.append(f"> Impact: {metadata['impact']}")
    if metadata.get("tags"):
        fields.append(f"> Tags: {', '.join(metadata['tags'])}")
    fields.extend(("", body.strip(), ""))
    return "\n".join(fields)


def normalize_solodit(root: Path) -> int:
    catalog = Catalog(root / "state/catalog.sqlite3")
    catalog.initialize()
    existing_documents = catalog.documents_by_source("solodit")
    processed_ids: set[str] = set()
    count = 0
    pending = 0
    with catalog.connect() as connection:
        for raw_path in sorted((root / "raw/solodit").glob("**/page-*.json")):
            payload = json.loads(raw_path.read_text())
            for finding in payload.get("findings", []):
                source_id = str(finding["id"])
                document_id = f"solodit:{source_id}"
                if document_id in processed_ids:
                    continue
                processed_ids.add(document_id)
                title = str(finding.get("title") or document_id)
                body = str(finding.get("content") or finding.get("summary") or "")
                metadata = _solodit_metadata(finding)
                target = root / "normalized/documents/solodit" / source_id
                markdown_path = target.with_suffix(".md")
                json_path = target.with_suffix(".json")
                markdown = _markdown_document(title, body, metadata)
                content_sha256 = hashlib.sha256(markdown.encode()).hexdigest()
                canonical = {
                    "id": document_id,
                    "text": body,
                    "page_content": body,
                    "metadata": metadata,
                }
                existing = existing_documents.get(document_id)
                if (
                    existing is not None
                    and existing["title"] == title
                    and existing["body"] == body
                    and existing["metadata_json"]
                    == json.dumps(metadata, ensure_ascii=False, sort_keys=True)
                    and existing["content_sha256"] == content_sha256
                    and markdown_path.is_file()
                    and json_path.is_file()
                ):
                    count += 1
                    continue
                content_sha256 = atomic_text(markdown_path, markdown, durable=False)
                atomic_json(json_path, canonical, durable=False)
                catalog.upsert_document(
                    document_id=document_id,
                    source="solodit",
                    source_id=source_id,
                    title=title,
                    body=body,
                    metadata=metadata,
                    content_sha256=content_sha256,
                    normalized_path=json_path,
                    connection=connection,
                    update_fts=False,
                )
                count += 1
                pending += 1
                if pending == 500:
                    os.sync()
                    connection.commit()
                    pending = 0
        if pending:
            os.sync()
        catalog.rebuild_fts(connection)
    return count


def _extract_local(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        text = path.read_text(errors="replace")
    elif suffix == ".pdf":
        reader = PdfReader(path)
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    elif suffix == ".json":
        payload = json.loads(path.read_text())
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        raise ValueError(f"unsupported report format: {path}")
    title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return (title_match.group(1).strip() if title_match else path.stem), text


def normalize_local_reports(root: Path) -> int:
    catalog = Catalog(root / "state/catalog.sqlite3")
    catalog.initialize()
    existing_documents = catalog.documents_by_source("local-report")
    count = 0
    extensions = {".md", ".markdown", ".txt", ".pdf", ".json"}
    groups: dict[str, list[Path]] = {}
    for raw_path in sorted(root.joinpath("raw/reports").glob("**/*")):
        if (
            raw_path.is_symlink()
            or not raw_path.is_file()
            or raw_path.suffix.lower() not in extensions
        ):
            continue
        with raw_path.open("rb") as source:
            raw_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        groups.setdefault(raw_sha256, []).append(raw_path)

    retained_ids: set[str] = set()
    retained_stems: set[str] = set()
    for raw_sha256, raw_paths in sorted(groups.items()):
        raw_path = sorted(raw_paths)[0]
        relative = raw_path.relative_to(root / "raw/reports")
        source_id = relative.as_posix()
        document_id = f"local:{hashlib.sha256(source_id.encode()).hexdigest()}"
        retained_ids.add(document_id)
        retained_stems.add(document_id.split(":", 1)[1])
        existing = existing_documents.get(document_id)
        existing_metadata = json.loads(existing["metadata_json"]) if existing else {}
        if existing is not None and existing_metadata.get("raw_sha256") in {
            None,
            raw_sha256,
        }:
            title, body = str(existing["title"]), str(existing["body"])
        else:
            title, body = _extract_local(raw_path)
        source_ids: list[str] = []
        source_urls: set[str] = set()
        finding_ids: set[str] = set()
        rights_statuses: set[str] = set()
        for grouped_path in sorted(raw_paths):
            source_ids.append(
                grouped_path.relative_to(root / "raw/reports").as_posix()
            )
            manifest_path = (
                root
                / "raw/solodit/report-manifest"
                / f"{grouped_path.stem}.json"
            )
            if not manifest_path.is_file():
                rights_statuses.add("review_required")
                continue
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("url"):
                source_urls.add(str(manifest["url"]))
            finding_ids.update(str(value) for value in manifest.get("finding_ids", []))
            rights_statuses.add(
                str(manifest.get("rights_status", "review_required"))
            )
        metadata = {
            "source": "local-report",
            "source_id": source_id,
            "source_ids": source_ids,
            "source_url": min(source_urls) if source_urls else None,
            "source_urls": sorted(source_urls),
            "solodit_finding_ids": sorted(finding_ids),
            "raw_sha256": raw_sha256,
            "tags": [],
            "content_format": raw_path.suffix.lower().lstrip("."),
            "rights": {
                "status": (
                    next(iter(rights_statuses))
                    if len(rights_statuses) == 1
                    else "review_required"
                )
            },
        }
        target = root / "normalized/documents/local" / document_id.split(":", 1)[1]
        markdown_path = target.with_suffix(".md")
        json_path = target.with_suffix(".json")
        markdown = _markdown_document(title, body, metadata)
        expected_sha256 = hashlib.sha256(markdown.encode()).hexdigest()
        if (
            existing is not None
            and existing["metadata_json"]
            == json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            and existing["content_sha256"] == expected_sha256
            and markdown_path.is_file()
            and json_path.is_file()
        ):
            count += 1
            continue
        content_sha256 = atomic_text(markdown_path, markdown, durable=False)
        atomic_json(
            json_path,
            {"id": document_id, "text": body, "page_content": body, "metadata": metadata},
            durable=False,
        )
        catalog.upsert_document(
            document_id=document_id,
            source="local-report",
            source_id=source_id,
            title=title,
            body=body,
            metadata=metadata,
            content_sha256=content_sha256,
            normalized_path=json_path,
            update_fts=False,
        )
        count += 1
    document_root = root / "normalized/documents/local"
    for derived_path in document_root.glob("*.*"):
        if derived_path.suffix in {".md", ".json"} and derived_path.stem not in retained_stems:
            derived_path.unlink()
    os.sync()
    with catalog.connect() as connection:
        catalog.retain_documents("local-report", retained_ids, connection)
        catalog.rebuild_fts(connection)
    return count


POC_EXTENSIONS = {
    ".circom",
    ".js",
    ".json",
    ".md",
    ".move",
    ".nr",
    ".py",
    ".rs",
    ".sol",
    ".toml",
    ".ts",
    ".vy",
    ".yaml",
    ".yml",
}
GENERATED_PATH_PARTS = {
    ".next",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "cache",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "site-packages",
    "target",
    "venv",
}


def normalize_repositories(root: Path, maximum_file_bytes: int = 1_000_000) -> int:
    catalog = Catalog(root / "state/catalog.sqlite3")
    catalog.initialize()
    existing_documents = catalog.document_index_by_source("github", id_length=71)
    count = 0
    with catalog.connect() as connection:
        connection.execute(
            """
            CREATE TEMP TABLE github_normalize_sources (
                content_sha256 TEXT NOT NULL,
                full_name TEXT NOT NULL,
                github_id INTEGER NOT NULL,
                head_sha TEXT NOT NULL,
                license_spdx TEXT,
                local_path TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                extension TEXT NOT NULL
            )
            """
        )
        pending_sources: list[tuple[Any, ...]] = []
        for repository in catalog.cloned_repositories():
            repository_path = Path(repository["local_path"])
            if not repository_path.is_dir():
                continue
            for source_path in sorted(repository_path.glob("**/*")):
                if (
                    source_path.is_symlink()
                    or not source_path.is_file()
                    or ".git" in source_path.parts
                    or any(
                        part.lower() in GENERATED_PATH_PARTS
                        for part in source_path.parts
                    )
                    or source_path.suffix.lower() not in POC_EXTENSIONS
                    or source_path.stat().st_size > maximum_file_bytes
                ):
                    continue
                raw = source_path.read_bytes()
                if b"\x00" in raw or not raw.strip():
                    continue
                pending_sources.append(
                    (
                        hashlib.sha256(raw).hexdigest(),
                        repository["full_name"],
                        repository["github_id"],
                        repository["head_sha"],
                        repository["license_spdx"],
                        str(repository_path),
                        source_path.relative_to(repository_path).as_posix(),
                        source_path.suffix.lower(),
                    )
                )
                if len(pending_sources) == 1000:
                    connection.executemany(
                        "INSERT INTO github_normalize_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        pending_sources,
                    )
                    pending_sources.clear()
        if pending_sources:
            connection.executemany(
                "INSERT INTO github_normalize_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                pending_sources,
            )
        connection.execute(
            "CREATE INDEX github_normalize_content_idx ON github_normalize_sources(content_sha256)"
        )

        retained_ids: set[str] = set()
        retained_stems: set[str] = set()
        pending_documents = 0
        source_rows = connection.execute(
            """
            SELECT * FROM github_normalize_sources
            ORDER BY content_sha256, full_name, relative_path
            """
        )
        for digest, grouped_sources in groupby(
            source_rows,
            key=lambda row: str(row["content_sha256"]),
        ):
            sources = list(grouped_sources)
            representative = sources[0]
            source_path = (
                Path(representative["local_path"])
                / representative["relative_path"]
            )
            body = source_path.read_text(errors="replace")
            source_id = (
                f"{representative['full_name']}:{representative['relative_path']}"
            )
            source_url = (
                f"https://github.com/{representative['full_name']}/blob/"
                f"{representative['head_sha']}/{representative['relative_path']}"
            )
            licenses = sorted(
                {
                    str(source["license_spdx"])
                    for source in sources
                    if source["license_spdx"]
                }
            )
            extensions = sorted({str(source["extension"]) for source in sources})
            metadata = {
                "source": "github",
                "source_id": source_id,
                "source_url": source_url,
                "source_count": len(sources),
                "sources": [
                    {
                        "repository": source["full_name"],
                        "repository_id": source["github_id"],
                        "commit_sha": source["head_sha"],
                        "path": source["relative_path"],
                    }
                    for source in sources
                ],
                "repositories": sorted(
                    {str(source["full_name"]) for source in sources}
                ),
                "raw_sha256": digest,
                "language_extensions": extensions,
                "licenses_spdx": licenses,
                "tags": [
                    "exploit-poc",
                    *(extension.lstrip(".") for extension in extensions),
                ],
                "content_format": "source-code",
                "rights": {
                    "status": "license_review_required",
                    "licenses_spdx": licenses,
                },
            }
            document_id = f"github:{digest}"
            retained_ids.add(document_id)
            retained_stems.add(digest)
            target = root / "normalized/documents/github" / digest
            markdown_path = target.with_suffix(".md")
            json_path = target.with_suffix(".json")
            markdown = _markdown_document(source_id, body, metadata)
            expected_sha256 = hashlib.sha256(markdown.encode()).hexdigest()
            existing = existing_documents.get(document_id)
            if (
                existing is not None
                and existing["metadata_json"]
                == json.dumps(metadata, ensure_ascii=False, sort_keys=True)
                and existing["content_sha256"] == expected_sha256
                and markdown_path.is_file()
                and json_path.is_file()
            ):
                count += 1
                continue
            content_sha256 = atomic_text(markdown_path, markdown, durable=False)
            atomic_json(
                json_path,
                {
                    "id": document_id,
                    "text": body,
                    "page_content": body,
                    "metadata": metadata,
                },
                durable=False,
            )
            catalog.upsert_document(
                document_id=document_id,
                source="github",
                source_id=source_id,
                title=source_id,
                body=body,
                metadata=metadata,
                content_sha256=content_sha256,
                normalized_path=json_path,
                connection=connection,
                update_fts=False,
            )
            count += 1
            pending_documents += 1
            if pending_documents == 500:
                os.sync()
                connection.commit()
                pending_documents = 0
        if pending_documents:
            os.sync()
        catalog.retain_documents("github", retained_ids, connection)
        document_root = root / "normalized/documents/github"
        for derived_path in document_root.glob("*.*"):
            if (
                derived_path.suffix in {".md", ".json"}
                and derived_path.stem not in retained_stems
            ):
                derived_path.unlink()
        os.sync()
        catalog.rebuild_fts(connection)
    return count


def chunk_markdown(text: str, maximum: int = 6000, overlap: int = 600) -> list[str]:
    if maximum <= overlap or overlap < 0:
        raise ValueError("chunk maximum must be greater than non-negative overlap")
    sections = re.split(r"(?=^#{1,6}\s)", text, flags=re.MULTILINE)
    chunks: list[str] = []
    current = ""
    for section in sections:
        if not section:
            continue
        if len(current) + len(section) + 2 <= maximum:
            current = f"{current}\n\n{section}".strip()
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(section) > maximum:
            chunks.append(section[:maximum])
            section = section[maximum - overlap :]
        current = section
    if current:
        chunks.append(current)
    return chunks


def export_rag(root: Path, maximum: int = 6000, overlap: int = 600) -> int:
    catalog = Catalog(root / "state/catalog.sqlite3")
    catalog.initialize()
    chunk_root = root / "normalized/chunks"
    if chunk_root.exists():
        for stale in chunk_root.glob("*.json"):
            stale.unlink()
    chunk_root.mkdir(parents=True, exist_ok=True)
    export_path = root / "exports/rag-chunks.jsonl"
    temporary = export_path.with_suffix(".jsonl.part")
    export_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with temporary.open("w", encoding="utf-8") as output:
        with catalog.connect() as connection:
            documents = connection.execute("SELECT * FROM documents ORDER BY id")
            for document in documents:
                metadata = json.loads(document["metadata_json"])
                if metadata.get("source") == "github":
                    sources = metadata.pop("sources", [])
                    repositories = metadata.pop("repositories", [])
                    normalized_path = Path(document["normalized_path"])
                    try:
                        canonical_path = normalized_path.relative_to(root).as_posix()
                    except ValueError:
                        canonical_path = normalized_path.name
                    metadata["canonical_path"] = canonical_path
                    metadata["repository_count"] = len(repositories)
                    metadata["provenance_entries"] = len(sources)
                for index, chunk in enumerate(
                    chunk_markdown(document["body"], maximum, overlap)
                ):
                    chunk_id = f"{document['id']}:{index:05d}"
                    chunk_metadata = {
                        **metadata,
                        "document_id": document["id"],
                        "chunk_index": index,
                        "content_sha256": hashlib.sha256(chunk.encode()).hexdigest(),
                    }
                    item = {
                        "id": chunk_id,
                        "text": chunk,
                        "page_content": chunk,
                        "metadata": chunk_metadata,
                    }
                    atomic_json(
                        chunk_root
                        / f"{hashlib.sha256(chunk_id.encode()).hexdigest()}.json",
                        item,
                        durable=False,
                    )
                    output.write(
                        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                    )
                    count += 1
        output.flush()
        os.fsync(output.fileno())
    os.sync()
    temporary.replace(export_path)
    return count
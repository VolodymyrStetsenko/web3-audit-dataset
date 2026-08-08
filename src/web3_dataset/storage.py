from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: Any, *, durable: bool = True) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("wb") as output:
        output.write(payload)
        output.flush()
        if durable:
            os.fsync(output.fileno())
    temporary.replace(path)
    return hashlib.sha256(payload).hexdigest()


def atomic_text(path: Path, value: str, *, durable: bool = True) -> str:
    return atomic_bytes(path, value.encode(), durable=durable)


def atomic_bytes(path: Path, payload: bytes, *, durable: bool = True) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("wb") as output:
        output.write(payload)
        output.flush()
        if durable:
            os.fsync(output.fileno())
    temporary.replace(path)
    return hashlib.sha256(payload).hexdigest()


class Catalog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=60,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=60000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    source TEXT NOT NULL,
                    partition_key TEXT NOT NULL,
                    cursor TEXT,
                    completed INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, partition_key)
                );
                CREATE TABLE IF NOT EXISTS source_objects (
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    raw_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    source_updated_at TEXT,
                    retrieved_at TEXT NOT NULL,
                    PRIMARY KEY (source, source_id)
                );
                CREATE TABLE IF NOT EXISTS repositories (
                    github_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL UNIQUE,
                    clone_url TEXT NOT NULL,
                    default_branch TEXT NOT NULL,
                    head_sha TEXT,
                    license_spdx TEXT,
                    pushed_at TEXT,
                    size_kb INTEGER,
                    raw_path TEXT NOT NULL,
                    local_path TEXT,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    normalized_path TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    id UNINDEXED,
                    title,
                    body,
                    tags
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(repositories)")
            }
            if "size_kb" not in columns:
                connection.execute("ALTER TABLE repositories ADD COLUMN size_kb INTEGER")

    def checkpoint(self, source: str, partition: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM checkpoints WHERE source=? AND partition_key=?",
                (source, partition),
            ).fetchone()

    def save_checkpoint(
        self,
        source: str,
        partition: str,
        cursor: str | None,
        completed: bool,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO checkpoints(source, partition_key, cursor, completed, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, partition_key) DO UPDATE SET
                    cursor=excluded.cursor,
                    completed=excluded.completed,
                    updated_at=excluded.updated_at
                """,
                (source, partition, cursor, int(completed), utc_now()),
            )

    def record_source_object(
        self,
        source: str,
        source_id: str,
        raw_path: Path,
        content_sha256: str,
        source_updated_at: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_objects(
                    source, source_id, raw_path, content_sha256,
                    source_updated_at, retrieved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    raw_path=excluded.raw_path,
                    content_sha256=excluded.content_sha256,
                    source_updated_at=excluded.source_updated_at,
                    retrieved_at=excluded.retrieved_at
                """,
                (
                    source,
                    source_id,
                    str(raw_path),
                    content_sha256,
                    source_updated_at,
                    utc_now(),
                ),
            )

    def record_repository(self, repository: dict[str, Any], raw_path: Path) -> None:
        license_data = repository.get("license") or {}
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO repositories(
                    github_id, full_name, clone_url, default_branch, license_spdx,
                    pushed_at, size_kb, raw_path, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?)
                ON CONFLICT(github_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    clone_url=excluded.clone_url,
                    default_branch=excluded.default_branch,
                    license_spdx=excluded.license_spdx,
                    pushed_at=excluded.pushed_at,
                    size_kb=excluded.size_kb,
                    raw_path=excluded.raw_path,
                    updated_at=excluded.updated_at
                """,
                (
                    repository["id"],
                    repository["full_name"],
                    repository["clone_url"],
                    repository.get("default_branch") or "main",
                    license_data.get("spdx_id"),
                    repository.get("pushed_at"),
                    repository.get("size"),
                    str(raw_path),
                    utc_now(),
                ),
            )

    def repositories_to_clone(
        self, include_unlicensed: bool, maximum_size_kb: int
    ) -> list[sqlite3.Row]:
        license_clause = "" if include_unlicensed else "AND license_spdx IS NOT NULL AND license_spdx != 'NOASSERTION'"
        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT * FROM repositories
                WHERE status IN ('discovered', 'failed', 'cloned') {license_clause}
                  AND COALESCE(size_kb, 0) <= ?
                ORDER BY full_name
                """,
                (maximum_size_kb,),
            ).fetchall()

    def all_repositories(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM repositories ORDER BY full_name").fetchall()

    def delete_repository(self, github_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM repositories WHERE github_id=?", (github_id,))
            connection.execute(
                "DELETE FROM source_objects WHERE source='github-repository' AND source_id=?",
                (str(github_id),),
            )

    def cloned_repositories(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM repositories WHERE status='cloned' ORDER BY full_name"
            ).fetchall()

    def update_repository_clone(
        self,
        github_id: int,
        *,
        status: str,
        local_path: Path | None = None,
        head_sha: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE repositories
                SET status=?, local_path=COALESCE(?, local_path),
                    head_sha=COALESCE(?, head_sha), updated_at=?
                WHERE github_id=?
                """,
                (
                    status,
                    str(local_path) if local_path else None,
                    head_sha,
                    utc_now(),
                    github_id,
                ),
            )

    def upsert_document(
        self,
        *,
        document_id: str,
        source: str,
        source_id: str,
        title: str,
        body: str,
        metadata: dict[str, Any],
        content_sha256: str,
        normalized_path: Path,
        connection: sqlite3.Connection | None = None,
        update_fts: bool = True,
    ) -> None:
        metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        tags = " ".join(str(tag) for tag in metadata.get("tags", []))
        if connection is None:
            with self.connect() as managed_connection:
                self.upsert_document(
                    document_id=document_id,
                    source=source,
                    source_id=source_id,
                    title=title,
                    body=body,
                    metadata=metadata,
                    content_sha256=content_sha256,
                    normalized_path=normalized_path,
                    connection=managed_connection,
                    update_fts=update_fts,
                )
            return
        connection.execute(
                """
                INSERT INTO documents(
                    id, source, source_id, title, body, metadata_json,
                    content_sha256, normalized_path, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    body=excluded.body,
                    metadata_json=excluded.metadata_json,
                    content_sha256=excluded.content_sha256,
                    normalized_path=excluded.normalized_path,
                    updated_at=excluded.updated_at
                """,
                (
                    document_id,
                    source,
                    source_id,
                    title,
                    body,
                    metadata_json,
                    content_sha256,
                    str(normalized_path),
                    utc_now(),
                ),
            )
        if update_fts:
            connection.execute("DELETE FROM documents_fts WHERE id=?", (document_id,))
            connection.execute(
                "INSERT INTO documents_fts(id, title, body, tags) VALUES (?, ?, ?, ?)",
                (document_id, title, body, tags),
            )

    def rebuild_fts(self, connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM documents_fts")
        rows = connection.execute(
            "SELECT id, title, body, metadata_json FROM documents ORDER BY id"
        )
        connection.executemany(
            "INSERT INTO documents_fts(id, title, body, tags) VALUES (?, ?, ?, ?)",
            (
                (
                    row["id"],
                    row["title"],
                    row["body"],
                    " ".join(
                        str(tag)
                        for tag in json.loads(row["metadata_json"]).get("tags", [])
                    ),
                )
                for row in rows
            ),
        )

    def retain_documents(
        self,
        source: str,
        document_ids: set[str],
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            "CREATE TEMP TABLE retained_document_ids (id TEXT PRIMARY KEY)"
        )
        connection.executemany(
            "INSERT INTO retained_document_ids(id) VALUES (?)",
            ((document_id,) for document_id in document_ids),
        )
        connection.execute(
            """
            DELETE FROM documents
            WHERE source=?
              AND NOT EXISTS (
                  SELECT 1 FROM retained_document_ids WHERE id=documents.id
              )
            """,
            (source,),
        )
        connection.execute("DROP TABLE retained_document_ids")

    def all_documents(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM documents ORDER BY id").fetchall()

    def documents_by_source(self, source: str) -> dict[str, sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents WHERE source=?",
                (source,),
            ).fetchall()
        return {str(row["id"]): row for row in rows}

    def document_index_by_source(
        self,
        source: str,
        *,
        id_length: int | None = None,
    ) -> dict[str, sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, metadata_json, content_sha256, normalized_path
                FROM documents
                WHERE source=? {"AND length(id)=?" if id_length is not None else ""}
                """,
                (source, id_length) if id_length is not None else (source,),
            ).fetchall()
        return {str(row["id"]): row for row in rows}

from __future__ import annotations

import sqlite3
from pathlib import Path


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_sqlite_schema(path: Path) -> None:
    conn = _connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chunk_meta (
                chunk_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text_raw TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                user_id UNINDEXED,
                body,
                tokenize = 'unicode61 remove_diacritics 0'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_chunk(
    path: Path,
    *,
    user_id: str,
    chunk_id: str,
    doc_id: str,
    chunk_index: int,
    text_raw: str,
    body_segmented: str,
) -> None:
    init_sqlite_schema(path)
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM chunk_meta WHERE chunk_id = ?", (chunk_id,))
        conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
        conn.execute(
            """
            INSERT INTO chunk_meta(chunk_id, user_id, doc_id, chunk_index, text_raw)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chunk_id, user_id, doc_id, chunk_index, text_raw),
        )
        conn.execute(
            """
            INSERT INTO chunks_fts(chunk_id, user_id, body)
            VALUES (?, ?, ?)
            """,
            (chunk_id, user_id, body_segmented),
        )
        conn.commit()
    finally:
        conn.close()


def bm25_search(path: Path, *, user_id: str, match_query: str, limit: int = 20) -> list[tuple[str, float]]:
    if not match_query.strip():
        return []
    init_sqlite_schema(path)
    conn = _connect(path)
    try:
        cur = conn.execute(
            """
            SELECT chunk_id, bm25(chunks_fts) AS score
            FROM chunks_fts
            WHERE user_id = ? AND chunks_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (user_id, match_query, limit),
        )
        rows = cur.fetchall()
        return [(str(r["chunk_id"]), float(r["score"])) for r in rows]
    finally:
        conn.close()


def fetch_meta(path: Path, chunk_ids: list[str]) -> dict[str, str]:
    if not chunk_ids:
        return {}
    init_sqlite_schema(path)
    conn = _connect(path)
    try:
        qmarks = ",".join("?" for _ in chunk_ids)
        cur = conn.execute(
            f"SELECT chunk_id, text_raw FROM chunk_meta WHERE chunk_id IN ({qmarks})",
            chunk_ids,
        )
        return {str(r["chunk_id"]): str(r["text_raw"]) for r in cur.fetchall()}
    finally:
        conn.close()

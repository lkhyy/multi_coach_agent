from __future__ import annotations

from uuid import uuid4

from app.config import settings
from app.rag.chunking import chunk_text_chapters_then_tokens
from app.rag.chroma_store import chroma_upsert, new_doc_id
from app.rag.jieba_tokenize import segment_for_fts
from app.rag.sqlite_store import upsert_chunk
from app.users.paths import user_chroma_dir, user_sqlite_path


def ingest_plain_text(user_id: str, text: str, *, doc_id: str | None = None) -> str:
    """Chunk + persist to SQLite FTS5 + Chroma for one user."""
    did = doc_id or new_doc_id()
    sqlite_path = user_sqlite_path(user_id)
    chroma_path = user_chroma_dir(user_id)
    parts = chunk_text_chapters_then_tokens(
        text,
        max_tokens=max(32, settings.rag_chunk_max_tokens),
        overlap_tokens=max(0, settings.rag_chunk_overlap_tokens),
        encoding_name=settings.rag_chunk_encoding,
        split_on_section=settings.rag_split_on_section_lines,
    )
    for idx, piece in enumerate(parts):
        cid = str(uuid4())
        seg = segment_for_fts(piece)
        upsert_chunk(
            sqlite_path,
            user_id=user_id,
            chunk_id=cid,
            doc_id=did,
            chunk_index=idx,
            text_raw=piece,
            body_segmented=seg,
        )
        chroma_upsert(
            chroma_path,
            user_id=user_id,
            chunk_id=cid,
            doc_id=did,
            chunk_index=idx,
            text=piece,
        )
    return did

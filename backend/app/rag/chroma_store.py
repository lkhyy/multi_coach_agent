from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.services.embeddings import get_bge_m3_encoder


def _collection_name() -> str:
    return "chunks"


def get_chroma_collection(user_chroma_path: Path):
    import chromadb

    client = chromadb.PersistentClient(path=str(user_chroma_path))
    return client.get_or_create_collection(
        name=_collection_name(),
        metadata={"hnsw:space": "cosine"},
    )


def chroma_upsert(
    user_chroma_path: Path,
    *,
    user_id: str,
    chunk_id: str,
    doc_id: str,
    chunk_index: int,
    text: str,
) -> None:
    enc = get_bge_m3_encoder()
    vec = enc.encode([text])[0]
    col = get_chroma_collection(user_chroma_path)
    col.upsert(
        ids=[chunk_id],
        embeddings=[vec.tolist()],
        documents=[text],
        metadatas=[{"user_id": user_id, "doc_id": doc_id, "chunk_index": int(chunk_index)}],
    )


def chroma_query(user_chroma_path: Path, *, user_id: str, query: str, limit: int = 20) -> list[tuple[str, float]]:
    enc = get_bge_m3_encoder()
    vec = enc.encode([query])[0]
    col = get_chroma_collection(user_chroma_path)
    res = col.query(
        query_embeddings=[vec.tolist()],
        n_results=limit,
        where={"user_id": user_id},
        include=["distances", "metadatas"],
    )
    ids = (res.get("ids") or [[]])[0] or []
    dists = (res.get("distances") or [[]])[0] or []
    out: list[tuple[str, float]] = []
    for cid, d in zip(ids, dists):
        out.append((str(cid), float(d)))
    return out


def new_doc_id() -> str:
    return str(uuid4())

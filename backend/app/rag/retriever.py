from __future__ import annotations

from dataclasses import dataclass

from app.rag.chroma_store import chroma_query
from app.rag.jieba_tokenize import fts_match_query
from app.rag.sqlite_store import bm25_search, fetch_meta
from app.users.paths import user_chroma_dir, user_sqlite_path


def _rrf_fuse(
    bm25_ranked: list[str],
    vec_ranked: list[str],
    *,
    k: int = 60,
) -> list[str]:
    scores: dict[str, float] = {}
    for i, cid in enumerate(bm25_ranked, start=1):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + i)
    for i, cid in enumerate(vec_ranked, start=1):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + i)
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in ordered]


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float


class RrfRetriever:
    def __init__(self, *, user_id: str) -> None:
        self.user_id = user_id
        self.sqlite_path = user_sqlite_path(user_id)
        self.chroma_path = user_chroma_dir(user_id)

    def retrieve(self, query: str, *, limit: int = 8) -> list[RetrievedChunk]:
        mq = fts_match_query(query)
        bm25_hits = bm25_search(self.sqlite_path, user_id=self.user_id, match_query=mq, limit=30)
        bm25_ids = [cid for cid, _ in bm25_hits]

        vec_hits = chroma_query(self.chroma_path, user_id=self.user_id, query=query, limit=30)
        vec_ids = [cid for cid, _ in vec_hits]

        fused = _rrf_fuse(bm25_ids, vec_ids)[:limit]
        texts = fetch_meta(self.sqlite_path, fused)
        out: list[RetrievedChunk] = []
        for rank, cid in enumerate(fused, start=1):
            t = texts.get(cid, "")
            if t:
                out.append(RetrievedChunk(chunk_id=cid, text=t, score=1.0 / float(rank)))
        return out

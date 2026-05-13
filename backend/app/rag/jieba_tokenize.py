from __future__ import annotations

import jieba


def segment_for_fts(text: str) -> str:
    """Space-joined jieba tokens for FTS5 unicode61 tokenization."""
    tokens = [t.strip() for t in jieba.cut(text, cut_all=False) if t.strip()]
    return " ".join(tokens)


def fts_match_query(text: str, *, max_tokens: int = 24) -> str:
    """Build OR-concatenated token query; escape FTS5 specials lightly."""
    tokens = [t for t in jieba.cut(text, cut_all=False) if t.strip()]
    tokens = tokens[:max_tokens]
    parts: list[str] = []
    for t in tokens:
        safe = t.replace('"', '""')
        if safe:
            parts.append(f'"{safe}"')
    if not parts:
        return ""
    return " OR ".join(parts)

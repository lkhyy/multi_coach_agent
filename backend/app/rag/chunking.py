from __future__ import annotations

import re
from typing import Callable


def chunk_text(
    text: str,
    *,
    max_chars: int = 500,
    overlap: int = 80,
) -> list[str]:
    """兼容旧行为：纯按字符滑动窗口（不按章节）。"""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + max_chars)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(0, end - overlap)
    return chunks


_MD_HEADING = re.compile(r"^#{1,6}(?:\s+\S|\S)")
_ZH_CHAPTER = re.compile(r"^第[0-9零一二三四五六七八九十百千万]+[章节篇卷部]")
_ZH_SECTION = re.compile(r"^第[0-9零一二三四五六七八九十百千万]+节")
_EN_CHAPTER = re.compile(r"^Chapter\s+[\dIVXLC]+\b", re.IGNORECASE)


def _is_chapter_start_line(line: str, *, split_on_section: bool = True) -> bool:
    s = line.strip()
    if not s:
        return False
    if _MD_HEADING.match(s):
        return True
    if _ZH_CHAPTER.match(s):
        return True
    if split_on_section and _ZH_SECTION.match(s):
        return True
    if _EN_CHAPTER.match(s):
        return True
    return False


def split_into_chapters(text: str, *, split_on_section: bool = True) -> list[str]:
    """按行扫描：Markdown 标题 / 第X章 / 第X节 / Chapter N 作为新章节起点。"""
    text = text.strip()
    if not text:
        return []
    lines = text.split("\n")
    chapters: list[str] = []
    buf: list[str] = []
    for line in lines:
        if _is_chapter_start_line(line, split_on_section=split_on_section) and buf:
            piece = "\n".join(buf).strip()
            if piece:
                chapters.append(piece)
            buf = [line]
        else:
            buf.append(line)
    tail = "\n".join(buf).strip()
    if tail:
        chapters.append(tail)
    return chapters if chapters else [text]


def chunk_by_tokens(
    text: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
    encoding_name: str = "cl100k_base",
    encode_decode: Callable[[str, int, int, str], list[str]] | None = None,
) -> list[str]:
    """在一段文本内按 tiktoken 计数做滑动窗口切块。"""
    text = text.strip()
    if not text:
        return []
    if max_tokens <= 0:
        return [text]
    overlap_tokens = max(0, min(overlap_tokens, max_tokens - 1))

    if encode_decode is not None:
        return encode_decode(text, max_tokens, overlap_tokens, encoding_name)

    try:
        import tiktoken
    except ImportError:
        # 无 tiktoken 时退化为近似：中文偏稠密，按字符估算 ~0.5 token/字 保守用 2 字/token
        approx_chars = max(1, max_tokens * 2)
        approx_ov = max(0, min(overlap_tokens * 2, approx_chars - 1))
        return chunk_text(text, max_chars=approx_chars, overlap=approx_ov)

    enc = tiktoken.get_encoding(encoding_name)
    ids = enc.encode(text)
    if len(ids) <= max_tokens:
        return [text]

    step = max(1, max_tokens - overlap_tokens)
    out: list[str] = []
    start = 0
    while start < len(ids):
        end = min(len(ids), start + max_tokens)
        piece = enc.decode(ids[start:end]).strip()
        if piece:
            out.append(piece)
        if end >= len(ids):
            break
        start += step
    return out if out else [text]


def chunk_text_chapters_then_tokens(
    text: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
    encoding_name: str = "cl100k_base",
    split_on_section: bool = True,
) -> list[str]:
    """先按章节边界拆成多块，再对每一块按 token 长度切（章节标题保留在对应块开头）。"""
    chapters = split_into_chapters(text, split_on_section=split_on_section)
    all_chunks: list[str] = []
    for ch in chapters:
        all_chunks.extend(
            chunk_by_tokens(
                ch,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
                encoding_name=encoding_name,
            )
        )
    return all_chunks

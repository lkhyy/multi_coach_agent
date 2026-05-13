from __future__ import annotations

from pathlib import Path

from app.config import settings


def _sanitize_user_id(user_id: str) -> str:
    cleaned = "".join(ch for ch in user_id if ch.isalnum() or ch in ("-", "_"))
    return cleaned or "anonymous"


def user_root(user_id: str) -> Path:
    root = settings.data_dir / _sanitize_user_id(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_sqlite_path(user_id: str) -> Path:
    return user_root(user_id) / "rag.sqlite"


def user_chroma_dir(user_id: str) -> Path:
    p = user_root(user_id) / "chroma"
    p.mkdir(parents=True, exist_ok=True)
    return p

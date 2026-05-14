from __future__ import annotations

import json
from pathlib import Path

from app.users.paths import user_root


def _map_path(user_id: str) -> Path:
    p = user_root(user_id) / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p / "thread_learning_map.json"


def load_thread_learning_map(user_id: str) -> dict[str, str]:
    path = _map_path(user_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items() if str(k) and str(v)}
    return {}


def save_thread_learning_map(user_id: str, mapping: dict[str, str]) -> None:
    path = _map_path(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_thread_learning_content_id(user_id: str, thread_id: str) -> str | None:
    return load_thread_learning_map(user_id).get(thread_id)


def set_thread_learning_content_id(user_id: str, thread_id: str, content_id: str) -> None:
    m = load_thread_learning_map(user_id)
    m[thread_id] = content_id
    save_thread_learning_map(user_id, m)


def preferred_thread_for_content(user_id: str, content_id: str) -> str | None:
    """同一学习内容可能对应多个线程时，优先选会话文件最近修改的一个。"""
    m = load_thread_learning_map(user_id)
    candidates = [tid for tid, cid in m.items() if cid == content_id]
    if not candidates:
        return None
    from app.session.md_session_store import session_md_path

    best_tid: str | None = None
    best_mtime = -1.0
    for tid in candidates:
        p = session_md_path(user_id, tid)
        if p.is_file():
            try:
                mt = float(p.stat().st_mtime)
            except OSError:
                mt = 0.0
            if mt >= best_mtime:
                best_mtime = mt
                best_tid = tid
    return best_tid or candidates[0]

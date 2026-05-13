from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.api.messages import messages_to_client_list
from app.config import settings
from app.session.md_session_store import load_thread, session_md_path

router = APIRouter(tags=["sessions"])


@router.get("/sessions/{user_id}/{thread_id}")
def get_session(user_id: str, thread_id: str):
    """从本地 md 读取会话，供前端初始化展示（含 tool_calls / tool）。"""
    msgs = load_thread(user_id, thread_id)
    path = session_md_path(user_id, thread_id)
    storage: str | None = None
    try:
        storage = str(path.resolve().relative_to(Path(settings.data_dir).resolve()))
    except ValueError:
        storage = path.name
    return {
        "user_id": user_id,
        "thread_id": thread_id,
        "storage": storage,
        "exists": path.is_file(),
        "messages": messages_to_client_list(msgs),
    }

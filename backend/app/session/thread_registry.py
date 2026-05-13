from __future__ import annotations

"""记录每用户最近一次 WS 使用的 thread_id，供 idle 从对应 md 加载上下文。"""

_last_thread: dict[str, str] = {}


def set_last_thread(user_id: str, thread_id: str) -> None:
    _last_thread[user_id] = thread_id


def get_last_thread(user_id: str) -> str:
    return _last_thread.get(user_id) or "default"

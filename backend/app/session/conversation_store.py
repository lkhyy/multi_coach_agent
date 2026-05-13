from __future__ import annotations

from langchain_core.messages import BaseMessage

# 每用户最近一次主调度完整消息快照（用于无 WS 时空闲消化队列）
_snapshots: dict[str, list[BaseMessage]] = {}


def set_snapshot(user_id: str, messages: list[BaseMessage]) -> None:
    _snapshots[user_id] = list(messages)


def get_snapshot(user_id: str) -> list[BaseMessage] | None:
    snap = _snapshots.get(user_id)
    return list(snap) if snap else None

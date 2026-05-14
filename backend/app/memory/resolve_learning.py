from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage

from app.config import settings
from app.memory.learning_store import (
    LearningContentRecord,
    create_learning_content,
    load_learning_content,
    search_best_learning_content,
    title_hint_from_user_text,
)
from app.memory.thread_learning import get_thread_learning_content_id, set_thread_learning_content_id


def first_user_text(messages: list[BaseMessage], *, max_len: int = 4000) -> str:
    for m in messages:
        if isinstance(m, HumanMessage):
            return str(m.content or "").strip()[:max_len]
    return ""


def resolve_learning_content_for_thread(
    user_id: str,
    thread_id: str,
    new_messages: list[BaseMessage],
    *,
    explicit_content_id: str = "",
    learning_match_query: str = "",
) -> LearningContentRecord:
    """
    解析本线程应绑定的学习内容：
    1) 显式 learning_content_id；
    2) 已有线程绑定；
    3) 用匹配语句对已持久化的学习主题做检索；
    4) 新建一条学习内容并绑定。
    """
    explicit = (explicit_content_id or "").strip()
    if explicit:
        rec = load_learning_content(user_id, explicit)
        if rec is not None:
            set_thread_learning_content_id(user_id, thread_id, explicit)
            return rec

    bid = get_thread_learning_content_id(user_id, thread_id)
    if bid:
        loaded = load_learning_content(user_id, bid)
        if loaded is not None:
            return loaded

    q = (learning_match_query or "").strip() or first_user_text(new_messages)
    if q:
        matched = search_best_learning_content(
            user_id,
            q,
            min_score=float(settings.learning_match_min_score),
        )
        if matched is not None:
            set_thread_learning_content_id(user_id, thread_id, matched.content_id)
            return matched

    created = create_learning_content(user_id, title_hint=title_hint_from_user_text(q or "新对话"))
    set_thread_learning_content_id(user_id, thread_id, created.content_id)
    return created

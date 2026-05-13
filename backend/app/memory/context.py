from __future__ import annotations

from langchain_core.messages import BaseMessage, SystemMessage

from app.config import settings
from app.memory.store import LongTermMemory, load_long_term_memory

LONG_TERM_MEMORY_MARKER = "[LONG_TERM_MEMORY_V1]"


def build_runtime_context_messages(user_id: str, full_messages: list[BaseMessage]) -> list[BaseMessage]:
    """把完整会话压缩成短期上下文，并注入长期记忆系统消息。"""
    messages = _drop_old_long_term_memory_messages(full_messages)
    messages = _truncate_short_term(messages, max_messages=settings.short_term_max_messages)
    memory_msg = _long_term_memory_system_message(load_long_term_memory(user_id))
    insert_idx = 1 if messages and isinstance(messages[0], SystemMessage) else 0
    return [*messages[:insert_idx], memory_msg, *messages[insert_idx:]]


def strip_runtime_only_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    return _drop_old_long_term_memory_messages(messages)


def incremental_new_messages(
    before_runtime_context: list[BaseMessage],
    after_runtime_context: list[BaseMessage],
) -> list[BaseMessage]:
    if len(after_runtime_context) <= len(before_runtime_context):
        return []
    return list(after_runtime_context[len(before_runtime_context) :])


def _truncate_short_term(messages: list[BaseMessage], max_messages: int) -> list[BaseMessage]:
    if max_messages <= 0 or len(messages) <= max_messages:
        return list(messages)
    keep = list(messages[-max_messages:])
    if messages and isinstance(messages[0], SystemMessage) and messages[0] not in keep:
        if keep and isinstance(keep[0], SystemMessage):
            keep[0] = messages[0]
        else:
            keep = [messages[0], *keep[:-1]]
    return keep


def _drop_old_long_term_memory_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, SystemMessage) and LONG_TERM_MEMORY_MARKER in str(m.content or ""):
            continue
        out.append(m)
    return out


def _long_term_memory_system_message(mem: LongTermMemory) -> SystemMessage:
    lines: list[str] = [LONG_TERM_MEMORY_MARKER, "以下是用户长期记忆，请作为稳定偏好参考："]
    lines.append(f"- personality: {_fmt(mem.personality)}")
    lines.append(f"- style_preferences: {_fmt(mem.style_preferences)}")
    lines.append(f"- learning_goals: {_fmt(mem.learning_goals)}")
    lines.append(f"- stable_facts: {_fmt(mem.stable_facts)}")
    lines.append("- 规则：长期记忆用于个性化，不可覆盖本轮用户最新显式指令。")
    return SystemMessage(content="\n".join(lines))


def _fmt(items: list[str]) -> str:
    return "；".join(items) if items else "（暂无）"


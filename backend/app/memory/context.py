from __future__ import annotations

from langchain_core.messages import BaseMessage, SystemMessage

from app.config import settings
from app.memory.learning_store import LearningContentRecord
from app.memory.store import LongTermMemory, load_long_term_memory

LONG_TERM_MEMORY_MARKER = "[LONG_TERM_MEMORY_V1]"
LEARNING_CONTENT_MARKER = "[LEARNING_CONTENT_V1]"


def build_runtime_context_messages(
    user_id: str,
    full_messages: list[BaseMessage],
    *,
    learning_record: LearningContentRecord | None = None,
) -> list[BaseMessage]:
    """把完整会话压缩成短期上下文，并注入用户级长期记忆与可选的学习主题记忆。"""
    messages = _drop_old_runtime_memory_messages(full_messages)
    messages = _truncate_short_term(messages, max_messages=settings.short_term_max_messages)
    profile_msg = _long_term_memory_system_message(load_long_term_memory(user_id))
    injected: list[BaseMessage] = [profile_msg]
    if learning_record is not None:
        injected.append(_learning_content_system_message(learning_record))
    insert_idx = 1 if messages and isinstance(messages[0], SystemMessage) else 0
    return [*messages[:insert_idx], *injected, *messages[insert_idx:]]


def strip_runtime_only_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    return _drop_old_runtime_memory_messages(messages)


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


def _drop_old_runtime_memory_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            c = str(m.content or "")
            if LONG_TERM_MEMORY_MARKER in c or LEARNING_CONTENT_MARKER in c:
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


def _learning_content_system_message(rec: LearningContentRecord) -> SystemMessage:
    lines: list[str] = [
        LEARNING_CONTENT_MARKER,
        "以下是当前对话绑定的「学习内容」长期记忆（主题级），请据此延续进度：",
        f"- content_id: {rec.content_id}",
        f"- title: {rec.title}"
        + ("（课题名已锁定，勿建议改名）" if rec.subject_title_locked else ""),
        f"- summary: {rec.summary or '（暂无）'}",
        f"- keywords: {_fmt(rec.keywords)}",
        f"- key_points: {_fmt(rec.key_points)}",
        f"- learning_plan（已与用户核对）: {rec.learning_plan or '（尚未写入）'}",
        f"- plan_phases（阶段进度）: {_fmt_plan_phases(rec.plan_phases)}",
        f"- mastery_status: {rec.mastery_status or '（未标注）'}",
        f"- progress_summary: {rec.progress_summary or '（暂无）'}",
        "- 规则：与 RAG 检索互补；若用户最新表述与此冲突，以用户本轮指令为准。",
    ]
    return SystemMessage(content="\n".join(lines))


def _fmt(items: list[str]) -> str:
    return "；".join(items) if items else "（暂无）"


def _fmt_plan_phases(phases: list[dict[str, str]]) -> str:
    if not phases:
        return "（暂无）"
    lines: list[str] = []
    for i, p in enumerate(phases[:24], start=1):
        title = (p.get("title") or "").strip() or f"阶段{i}"
        st = (p.get("status") or "not_started").strip()
        cn = {"completed": "已完成", "in_progress": "进行中", "not_started": "未完成"}.get(st, st or "未完成")
        pct = (p.get("progress_pct") or "0").strip() or "0"
        lines.append(f"{i}. {title} — {cn} — 进度 {pct}%")
    return "\n".join(lines)


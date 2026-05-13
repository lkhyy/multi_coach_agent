from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage

from app.config import settings
from app.users.paths import user_root


@dataclass
class LongTermMemory:
    personality: list[str] = field(default_factory=list)
    style_preferences: list[str] = field(default_factory=list)
    learning_goals: list[str] = field(default_factory=list)
    stable_facts: list[str] = field(default_factory=list)
    updated_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def memory_path(user_id: str) -> Path:
    p = user_root(user_id) / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p / "long_term.json"


def load_long_term_memory(user_id: str) -> LongTermMemory:
    path = memory_path(user_id)
    if not path.is_file():
        return LongTermMemory(updated_at=_now_iso())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return LongTermMemory(updated_at=_now_iso())
    return LongTermMemory(
        personality=list(data.get("personality") or []),
        style_preferences=list(data.get("style_preferences") or []),
        learning_goals=list(data.get("learning_goals") or []),
        stable_facts=list(data.get("stable_facts") or []),
        updated_at=str(data.get("updated_at") or _now_iso()),
    )


def save_long_term_memory(user_id: str, memory: LongTermMemory) -> None:
    memory.updated_at = _now_iso()
    path = memory_path(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(memory), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_long_term_memory_from_messages(user_id: str, messages: list[BaseMessage]) -> LongTermMemory:
    """轻量规则抽取：不依赖额外模型，更新用户长期偏好。"""
    mem = load_long_term_memory(user_id)
    user_texts = [str(m.content or "") for m in messages if isinstance(m, HumanMessage)]
    for text in user_texts[-12:]:
        _ingest_text(mem, text)
    _cap_list(mem.personality, settings.long_term_list_max_items)
    _cap_list(mem.style_preferences, settings.long_term_list_max_items)
    _cap_list(mem.learning_goals, settings.long_term_list_max_items)
    _cap_list(mem.stable_facts, settings.long_term_list_max_items)
    save_long_term_memory(user_id, mem)
    return mem


def _ingest_text(mem: LongTermMemory, text: str) -> None:
    t = text.strip()
    if not t:
        return
    if any(k in t for k in ("请简洁", "简洁一点", "精简", "一句话", "简短")):
        _append_unique(mem.style_preferences, "偏好简洁回答")
    if any(k in t for k in ("详细", "展开讲", "多举例", "原理")):
        _append_unique(mem.style_preferences, "偏好详细解释与示例")
    if any(k in t for k in ("中文", "中文回答", "简体")):
        _append_unique(mem.style_preferences, "偏好中文沟通")
    if any(k in t for k in ("步骤", "分步骤", "step by step")):
        _append_unique(mem.style_preferences, "偏好分步骤结构化回答")
    if "我希望你" in t or "我想要" in t:
        _append_unique(mem.personality, "目标导向、会主动提出需求")
    for marker in ("我要", "我想", "目标是", "希望学会"):
        idx = t.find(marker)
        if idx >= 0:
            goal = t[idx : idx + 60].strip()
            _append_unique(mem.learning_goals, goal)
            break
    if "我是" in t and len(t) <= 80:
        _append_unique(mem.stable_facts, t)


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _cap_list(items: list[str], max_items: int) -> None:
    if len(items) > max_items:
        del items[: len(items) - max_items]


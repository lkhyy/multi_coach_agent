from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import settings
from app.memory.learning_store import (
    LearningContentRecord,
    load_learning_content,
    save_learning_content,
    score_user_text_vs_learning_record,
    title_hint_from_user_text,
)
from app.memory.store import LongTermMemory, load_long_term_memory, save_long_term_memory

logger = logging.getLogger(__name__)

# refresh_learning_content_with_llm 返回值
LEARN_REFRESH_MERGED = "merged"
LEARN_REFRESH_SKIPPED_IRRELEVANT = "skipped_irrelevant"
LEARN_REFRESH_SKIPPED_NO_DELTA = "skipped_no_delta"
LEARN_REFRESH_NO_LLM = "no_llm"
LEARN_REFRESH_FAILED = "failed"


class LongTermMemoryModel(BaseModel):
    personality: list[str] = Field(default_factory=list, description="性格与互动偏好，短句")
    style_preferences: list[str] = Field(default_factory=list, description="学习/沟通风格")
    learning_goals: list[str] = Field(default_factory=list, description="长期学习目标")
    stable_facts: list[str] = Field(default_factory=list, description="关于用户的稳定事实")


class LearningContentRefreshModel(BaseModel):
    should_merge_into_bound_topic: bool = Field(
        description=(
            "仅当最近对话与当前绑定的学习主题（title/summary/keywords/要点）直接相关、"
            "值得写入该主题的学习记忆时为 true；天气/闲聊/完全另一门课/与当前进度无关的泛问为 false。"
        )
    )
    title: str = Field(
        default="",
        description=(
            "学习主题短标题（<=40字）：必须是具体知识/技能名称（如「Transformer 自注意力」「PyTorch 分布式训练」），"
            "禁止用学习意图或元标签（如「我要学…」「路线图」「学习计划」「学习大模型」这类泛称）。"
        ),
    )
    summary: str = Field(default="", description="当前进度与主题的压缩摘要，<=200字")
    keywords: list[str] = Field(default_factory=list, description="检索关键词，<=12个")
    key_points: list[str] = Field(default_factory=list, description="已掌握要点、易错点，短句")


def _user_text_for_extract(messages: list[BaseMessage], *, last_n_user: int = 16) -> str:
    parts: list[str] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            parts.append(str(m.content or "").strip())
    tail = parts[-last_n_user:] if parts else []
    return "\n---\n".join(tail) if tail else ""


def _delta_text(messages: list[BaseMessage]) -> str:
    """最近若干轮用户与助手文本，供学习内容合并。"""
    lines: list[str] = []
    for m in messages[-40:]:
        if isinstance(m, HumanMessage):
            lines.append(f"用户: {str(m.content or '')[:2000]}")
        elif isinstance(m, AIMessage) and not (m.tool_calls or []):
            lines.append(f"助手: {str(m.content or '')[:2000]}")
    return "\n".join(lines)


def _strip_markdown_json_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    body: list[str] = []
    started = False
    for ln in lines:
        if not started:
            if ln.strip().startswith("```"):
                started = True
            continue
        if ln.strip() == "```":
            break
        body.append(ln)
    return "\n".join(body).strip()


def _json_object_from_model_text(raw: str) -> dict[str, object]:
    s = _strip_markdown_json_fence(raw).strip()
    start = s.find("{")
    if start < 0:
        raise ValueError("响应中未找到 JSON 对象")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(s[start:])
    if not isinstance(obj, dict):
        raise ValueError("JSON 根须为对象")
    return obj


def _invoke_pydantic_llm(llm: object, model_cls: type[BaseModel], system: str, human: str) -> BaseModel:
    """OpenAI 兼容端用 structured_output；DeepSeek 当前不支持该 response_format，改为要求模型只输出 JSON 再校验。"""
    base = (settings.openai_base_url or "").strip().lower()
    if "deepseek.com" not in base:
        structured = llm.with_structured_output(model_cls)  # type: ignore[union-attr]
        return structured.invoke([("system", system), ("human", human)])  # type: ignore[return-value]
    schema = model_cls.model_json_schema()
    sys_full = (
        f"{system}\n\n"
        "输出要求：只输出一个合法 UTF-8 JSON 对象，不要用 markdown 代码围栏，不要任何前缀或后缀说明文字。\n"
        "字段名、嵌套结构必须与下列 JSON Schema 一致（仅输出 JSON）：\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    resp = llm.invoke([SystemMessage(content=sys_full), HumanMessage(content=human)])  # type: ignore[union-attr]
    data = _json_object_from_model_text(str(resp.content or ""))
    return model_cls.model_validate(data)


def _recent_user_text_for_overlap(messages: list[BaseMessage], *, last_messages: int = 10) -> str:
    parts: list[str] = []
    for m in messages[-last_messages:]:
        if isinstance(m, HumanMessage):
            t = str(m.content or "").strip()
            if t:
                parts.append(t)
    return "\n".join(parts)


def refresh_user_profile_with_llm(user_id: str, messages: list[BaseMessage]) -> LongTermMemory | None:
    """用小模型合并、压缩用户级长期记忆；失败返回 None。"""
    if not (settings.openai_api_key or "").strip():
        return None
    recent = _user_text_for_extract(messages)
    if not recent.strip():
        return None
    current = load_long_term_memory(user_id)
    try:
        from app.services.llm import get_memory_extractor_llm

        llm = get_memory_extractor_llm()
        cap = max(4, int(settings.long_term_list_max_items))
        sys = (
            "你是用户档案整理助手。根据「既有档案」与「近期用户原话」，输出合并后的档案。"
            "要求：去重、保留高价值信息、每条短句化；每类最多 "
            f"{cap} 条；不要编造用户未表达的内容。"
        )
        human = (
            "【既有档案】\n"
            f"personality: {current.personality}\n"
            f"style_preferences: {current.style_preferences}\n"
            f"learning_goals: {current.learning_goals}\n"
            f"stable_facts: {current.stable_facts}\n\n"
            f"【近期用户原话】\n{recent}"
        )
        out = _invoke_pydantic_llm(llm, LongTermMemoryModel, sys, human)
        mem = LongTermMemory(
            personality=_cap(out.personality, cap),
            style_preferences=_cap(out.style_preferences, cap),
            learning_goals=_cap(out.learning_goals, cap),
            stable_facts=_cap(out.stable_facts, cap),
        )
        save_long_term_memory(user_id, mem)
        return mem
    except Exception:
        logger.exception("refresh_user_profile_with_llm failed user=%s", user_id)
        return None


def _cap(items: list[str], n: int) -> list[str]:
    cleaned = [x.strip() for x in items if x and str(x).strip()]
    return cleaned[-n:] if len(cleaned) > n else cleaned


_TITLE_META_FRAGMENTS = (
    "路线图",
    "学习路线",
    "学习路径",
    "学习计划",
    "学习规划",
    "整体计划",
    "整体学习",
    "学习主题",
    "学习安排",
)


def _is_meta_or_intent_title(t: str) -> bool:
    s = (t or "").strip()
    if len(s) < 2:
        return True
    if s in ("未命名学习主题", "新对话"):
        return True
    for m in _TITLE_META_FRAGMENTS:
        if m in s and len(s) <= len(m) + 8:
            return True
    if re.match(r"^我要(学习|学|了解|掌握)", s):
        return True
    if re.match(r"^(帮我|请帮我|麻烦你|麻烦)", s):
        return True
    if s.startswith("学习") and len(s) <= 5:
        return True
    return False


def _first_user_snippet_from_delta(delta: str, *, max_len: int = 48) -> str:
    for line in (delta or "").split("\n"):
        line = line.strip()
        if line.startswith("用户:"):
            return line.split(":", 1)[1].strip()[:max_len]
    return ""


def _pick_subject_learning_title(
    proposed: str,
    record: LearningContentRecord,
    llm_keywords: list[str],
    *,
    delta: str,
) -> str:
    for cand in ((proposed or "").strip(), (record.title or "").strip()):
        if cand and not _is_meta_or_intent_title(cand):
            return cand[:80]
    for kws in (llm_keywords, record.keywords):
        for k in kws or []:
            kk = str(k).strip()
            if kk and not _is_meta_or_intent_title(kk) and 2 <= len(kk) <= 80:
                return kk[:80]
    snippet = _first_user_snippet_from_delta(delta)
    if snippet:
        cleaned = title_hint_from_user_text(snippet, max_len=48)
        if cleaned and not _is_meta_or_intent_title(cleaned):
            return cleaned[:80]
    fallback = (proposed or record.title or "").strip()
    return (fallback[:80] if fallback else "未命名学习主题")


def refresh_learning_content_with_llm(
    user_id: str,
    record: LearningContentRecord,
    messages: list[BaseMessage],
) -> str:
    """
    尝试用小模型合并学习档案；若判定与当前绑定主题无关则跳过写入。
    返回 LEARN_REFRESH_* 常量。
    """
    if not (settings.openai_api_key or "").strip():
        return LEARN_REFRESH_NO_LLM
    delta = _delta_text(messages)
    if not delta.strip():
        return LEARN_REFRESH_SKIPPED_NO_DELTA
    try:
        from app.services.llm import get_memory_extractor_llm

        llm = get_memory_extractor_llm()
        cap_kp = max(6, int(settings.learning_content_max_key_points))
        cap_kw = 12
        sys = (
            "你是学习进度整理助手。根据「当前学习档案」与「最近对话摘录」判断：\n"
            "1) 若对话与当前绑定主题直接相关（巩固、追问、练习、纠错、延续进度），"
            "should_merge_into_bound_topic=true，并合并输出 title/summary/keywords/key_points（"
            f"key_points 最多 {cap_kp} 条，keywords 最多 {cap_kw} 个；不要编造）。\n"
            "title 必须是具体知识/技能名称；禁止「路线图」「学习计划」「我要学习…」「学习大模型」等意图句或元标签。\n"
            "若 subject_title_locked 为 true，则 title 字段只能与「当前 title」完全一致或留空，禁止改写课题名。\n"
            "2) 若主要是天气、闲聊、另一门课、与当前 title/要点明显无关的泛问，"
            "should_merge_into_bound_topic=false，其它字段可留空。"
        )
        human = (
            f"content_id: {record.content_id}\n"
            f"当前 title: {record.title}\n"
            f"subject_title_locked: {record.subject_title_locked}\n"
            f"当前 summary: {record.summary}\n"
            f"当前 keywords: {record.keywords}\n"
            f"当前 key_points: {record.key_points}\n\n"
            f"【最近对话摘录】\n{delta}"
        )
        out = _invoke_pydantic_llm(llm, LearningContentRefreshModel, sys, human)
        if not out.should_merge_into_bound_topic:
            logger.info(
                "learning content update skipped (off-topic) user=%s content=%s",
                user_id,
                record.content_id,
            )
            return LEARN_REFRESH_SKIPPED_IRRELEVANT
        title_locked = bool(record.subject_title_locked) or bool((record.plan_confirmed_at or "").strip()) or bool(
            record.plan_phases
        )
        if title_locked:
            merged_title = record.title
        else:
            merged_title = _pick_subject_learning_title(
                (out.title or "").strip(),
                record,
                list(out.keywords or []),
                delta=delta,
            )
        merged_title = (merged_title or "未命名学习主题")[:200]
        merged = LearningContentRecord(
            content_id=record.content_id,
            title=merged_title,
            summary=(out.summary or record.summary).strip()[:500],
            keywords=_cap(out.keywords, cap_kw) if out.keywords else list(record.keywords),
            key_points=_cap(out.key_points, cap_kp) if out.key_points else list(record.key_points),
            learning_plan=record.learning_plan,
            mastery_status=record.mastery_status,
            progress_summary=record.progress_summary,
            plan_confirmed_at=record.plan_confirmed_at,
            plan_phases=list(record.plan_phases),
            subject_title_locked=record.subject_title_locked,
        )
        save_learning_content(user_id, merged)
        return LEARN_REFRESH_MERGED
    except Exception:
        logger.exception("refresh_learning_content_with_llm failed user=%s", user_id)
        return LEARN_REFRESH_FAILED


def update_long_term_memory_pipeline(user_id: str, messages: list[BaseMessage]) -> LongTermMemory:
    """优先 LLM 合并用户档案；失败则回退规则抽取。"""
    llm_mem = refresh_user_profile_with_llm(user_id, messages)
    if llm_mem is not None:
        return llm_mem
    from app.memory.store import update_long_term_memory_heuristic

    return update_long_term_memory_heuristic(user_id, messages)


def _maybe_heuristic_append_learning(
    user_id: str,
    record: LearningContentRecord,
    messages: list[BaseMessage],
) -> None:
    """无 LLM 或 LLM 失败时：仅在与当前主题有足够词重叠时追加一条用户要点。"""
    blob = _recent_user_text_for_overlap(messages)
    if not blob.strip():
        return
    thr = float(settings.learning_update_heuristic_min_overlap)
    if score_user_text_vs_learning_record(record, blob) < thr:
        logger.debug(
            "learning heuristic append skipped (low overlap) user=%s content=%s",
            user_id,
            record.content_id,
        )
        return
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            t = str(m.content or "").strip()
            if 10 < len(t) < 400 and t not in record.key_points:
                record.key_points.append(t[:200])
                save_learning_content(user_id, record)
            break


def update_learning_content_pipeline(
    user_id: str,
    content_id: str,
    messages: list[BaseMessage],
) -> None:
    record = load_learning_content(user_id, content_id)
    if record is None:
        return
    code = refresh_learning_content_with_llm(user_id, record, messages)
    if code == LEARN_REFRESH_MERGED:
        return
    if code == LEARN_REFRESH_SKIPPED_IRRELEVANT:
        return
    _maybe_heuristic_append_learning(user_id, record, messages)

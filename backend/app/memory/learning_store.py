from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jieba

from app.users.paths import user_root


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def aggregate_plan_status_cn(phases: list[dict[str, Any]]) -> str:
    """由阶段状态推导整体进度文案（与前端「已完成 / 未完成 / 进行中」一致）。"""
    if not phases:
        return "未完成"
    statuses: list[str] = []
    for p in phases:
        s = str(p.get("status") or "not_started").strip()
        if s not in ("completed", "in_progress", "not_started"):
            s = "not_started"
        statuses.append(s)
    if all(x == "completed" for x in statuses):
        return "已完成"
    if any(x == "in_progress" for x in statuses):
        return "进行中"
    if any(x == "completed" for x in statuses):
        return "进行中"
    return "未完成"


def _canonical_phase_status(raw: str) -> str:
    s = (raw or "").strip()
    low = s.lower()
    if s in ("已完成",) or low in ("completed", "done", "complete"):
        return "completed"
    if s in ("进行中",) or low in ("in_progress", "doing", "active", "progress"):
        return "in_progress"
    if s in ("未完成", "未开始") or low in ("not_started", "pending", "todo"):
        return "not_started"
    if s in ("completed", "in_progress", "not_started"):
        return s
    return "not_started"


def _progress_pct_str(item: dict[str, Any], status: str) -> str:
    """与 status 一致：未开始 0%、已完成 100%；进行中为模型给出的 1–99 整数（缺省 50）。"""
    raw = item.get("progress_pct")
    if raw is None or str(raw).strip() == "":
        raw = item.get("progress")
    parsed: int | None = None
    if raw is not None and str(raw).strip() != "":
        try:
            parsed = int(round(float(str(raw))))
        except (TypeError, ValueError):
            parsed = None
    if status == "completed":
        return "100"
    if status == "not_started":
        return "0"
    if parsed is not None:
        return str(max(1, min(99, parsed)))
    return "50"


def parse_plan_phases_json_string(raw: str) -> list[dict[str, str]]:
    """解析工具入参 plan_phases_json。"""
    t = (raw or "").strip()
    if not t:
        return []
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()[:400]
        if not title:
            continue
        st = _canonical_phase_status(str(item.get("status") or "not_started"))
        pct = _progress_pct_str(item, st)
        out.append({"title": title, "status": st, "progress_pct": pct})
    return out[:48]


def learning_contents_dir(user_id: str) -> Path:
    p = user_root(user_id) / "memory" / "learning_contents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def learning_index_path(user_id: str) -> Path:
    return user_root(user_id) / "memory" / "learning_contents_index.json"


@dataclass
class LearningContentRecord:
    """某一学习主题下的浓缩记忆（与对话线程解耦，按 content_id 唯一）。"""

    content_id: str
    title: str
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    key_points: list[str] = field(default_factory=list)
    updated_at: str = ""
    # 与用户核对后写入的学习计划与进度（由 scheduler_save_learning_plan / scheduler_commit_learning_phases 更新）
    learning_plan: str = ""
    mastery_status: str = ""
    progress_summary: str = ""
    plan_confirmed_at: str = ""
    # 学习计划阶段：每项 title/status/progress_pct（0–100 字符串，与 status 语义一致）
    plan_phases: list[dict[str, str]] = field(default_factory=list)


@dataclass
class LearningIndexEntry:
    """索引条目（与 LearningContentRecord 同步，用于检索与侧栏展示）。"""

    content_id: str
    title: str
    summary: str
    keywords: list[str]
    updated_at: str
    has_learning_plan: bool = False
    mastery_status: str = ""
    plan_confirmed_at: str = ""
    aggregate_plan_status_cn: str = "未完成"
    plan_phases: list[dict[str, str]] = field(default_factory=list)


def _sanitize_content_id(raw: str) -> str:
    s = "".join(ch for ch in raw.strip() if ch.isalnum() or ch in ("-", "_"))
    return s[:128] if s else ""


def learning_content_path(user_id: str, content_id: str) -> Path:
    safe = _sanitize_content_id(content_id)
    if not safe:
        raise ValueError("invalid content_id")
    return learning_contents_dir(user_id) / f"{safe}.json"


def load_learning_content(user_id: str, content_id: str) -> LearningContentRecord | None:
    path = learning_content_path(user_id, content_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return LearningContentRecord(
        content_id=str(data.get("content_id") or content_id),
        title=str(data.get("title") or ""),
        summary=str(data.get("summary") or ""),
        keywords=list(data.get("keywords") or []),
        key_points=list(data.get("key_points") or []),
        updated_at=str(data.get("updated_at") or _now_iso()),
        learning_plan=str(data.get("learning_plan") or ""),
        mastery_status=str(data.get("mastery_status") or ""),
        progress_summary=str(data.get("progress_summary") or ""),
        plan_confirmed_at=str(data.get("plan_confirmed_at") or ""),
        plan_phases=_coerce_plan_phases(data.get("plan_phases")),
    )


def _index_aggregate_cn(e: dict[str, object]) -> str:
    raw = e.get("aggregate_plan_status_cn")
    agg = raw.strip() if isinstance(raw, str) else ""
    if agg in ("已完成", "未完成", "进行中"):
        return agg
    return aggregate_plan_status_cn(_coerce_plan_phases(e.get("plan_phases")))


def _coerce_plan_phases(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()[:400]
        if not title:
            continue
        st = _canonical_phase_status(str(item.get("status") or "not_started"))
        pct = _progress_pct_str(item, st)
        out.append({"title": title, "status": st, "progress_pct": pct})
    return out[:48]


def save_learning_content(user_id: str, record: LearningContentRecord) -> None:
    record.updated_at = _now_iso()
    path = learning_content_path(user_id, record.content_id)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    _upsert_index(user_id, record)


def _load_index_entries(user_id: str) -> list[LearningIndexEntry]:
    p = learning_index_path(user_id)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    out: list[LearningIndexEntry] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        cid = str(e.get("content_id") or "")
        if not cid:
            continue
        out.append(
            LearningIndexEntry(
                content_id=cid,
                title=str(e.get("title") or ""),
                summary=str(e.get("summary") or ""),
                keywords=list(e.get("keywords") or []),
                updated_at=str(e.get("updated_at") or ""),
                has_learning_plan=bool(e.get("has_learning_plan")),
                mastery_status=str(e.get("mastery_status") or ""),
                plan_confirmed_at=str(e.get("plan_confirmed_at") or ""),
                aggregate_plan_status_cn=_index_aggregate_cn(e),
                plan_phases=_coerce_plan_phases(e.get("plan_phases")),
            )
        )
    return out


def _save_index_entries(user_id: str, entries: list[LearningIndexEntry]) -> None:
    p = learning_index_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "entries": [asdict(e) for e in entries]}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _upsert_index(user_id: str, record: LearningContentRecord) -> None:
    entries = _load_index_entries(user_id)
    filtered = [e for e in entries if e.content_id != record.content_id]
    filtered.append(
        LearningIndexEntry(
            content_id=record.content_id,
            title=record.title,
            summary=record.summary,
            keywords=list(record.keywords),
            updated_at=record.updated_at or _now_iso(),
            has_learning_plan=bool((record.learning_plan or "").strip()),
            mastery_status=(record.mastery_status or "").strip()[:200],
            plan_confirmed_at=str(record.plan_confirmed_at or ""),
            aggregate_plan_status_cn=aggregate_plan_status_cn(list(record.plan_phases)),
            plan_phases=list(record.plan_phases) if record.plan_phases else [],
        )
    )
    _save_index_entries(user_id, filtered)


def list_learning_index(user_id: str) -> list[LearningIndexEntry]:
    return _load_index_entries(user_id)


def _token_set(text: str) -> set[str]:
    return {t.strip() for t in jieba.cut(text, cut_all=False) if t.strip()}


def search_best_learning_content(
    user_id: str,
    query: str,
    *,
    min_score: float,
) -> LearningContentRecord | None:
    """用分词重叠 + 子串相似做粗排，取最高分且过阈值的条目。"""
    q = (query or "").strip()
    if not q:
        return None
    entries = _load_index_entries(user_id)
    if not entries:
        return None
    q_tokens = _token_set(q)
    if not q_tokens:
        q_tokens = {q}
    best: tuple[float, LearningIndexEntry] | None = None
    for ent in entries:
        blob = f"{ent.title}\n{ent.summary}\n{' '.join(ent.keywords)}"
        d_tokens = _token_set(blob)
        inter = len(q_tokens & d_tokens)
        denom = (len(q_tokens) * max(len(d_tokens), 1)) ** 0.5 + 1e-6
        overlap_score = inter / denom
        sub = 0.15 if (ent.title and ent.title in q) or (q in ent.title) else 0.0
        score = overlap_score + sub
        if best is None or score > best[0]:
            best = (score, ent)
    if best is None or best[0] < min_score:
        return None
    loaded = load_learning_content(user_id, best[1].content_id)
    return loaded


def score_user_text_vs_learning_record(record: LearningContentRecord, user_text: str) -> float:
    """与检索相同的重叠打分，用于无 LLM 时的写入门控。"""
    q = (user_text or "").strip()
    if not q:
        return 0.0
    q_tokens = _token_set(q)
    if not q_tokens:
        q_tokens = {q}
    blob = f"{record.title}\n{record.summary}\n{' '.join(record.keywords)}\n{' '.join(record.key_points)}"
    d_tokens = _token_set(blob)
    inter = len(q_tokens & d_tokens)
    denom = (len(q_tokens) * max(len(d_tokens), 1)) ** 0.5 + 1e-6
    overlap_score = inter / denom
    sub = 0.15 if (record.title and record.title in q) or (q in record.title) else 0.0
    return overlap_score + sub


def create_learning_content(user_id: str, *, title_hint: str) -> LearningContentRecord:
    cid = str(uuid.uuid4())
    title = title_hint.strip()[:200] or "未命名学习主题"
    rec = LearningContentRecord(content_id=cid, title=title, summary="", keywords=[], key_points=[])
    save_learning_content(user_id, rec)
    return rec


def delete_learning_index_entry(user_id: str, content_id: str) -> None:
    entries = [e for e in _load_index_entries(user_id) if e.content_id != content_id]
    _save_index_entries(user_id, entries)


_WS_RE = re.compile(r"\s+")


def title_hint_from_user_text(text: str, *, max_len: int = 120) -> str:
    one_line = _WS_RE.sub(" ", text.strip())[:max_len]
    for pref in (
        "我要学习",
        "我想学习",
        "我想学",
        "我要学",
        "希望能学习",
        "帮我学习",
        "请帮我学习",
        "请帮我",
        "帮我",
        "学习一下",
        "想了解一下",
        "想了解",
        "想掌握",
    ):
        if one_line.startswith(pref):
            one_line = one_line[len(pref) :].lstrip(" ，,。的了下关于对:")
            break
    one_line = _WS_RE.sub(" ", one_line.strip())[:max_len].strip()
    return one_line or "未命名学习主题"

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.users.paths import user_root


def _sanitize_thread_id(thread_id: str) -> str:
    cleaned = "".join(ch for ch in thread_id if ch.isalnum() or ch in ("-", "_"))
    return cleaned or "default"


def session_md_path(user_id: str, thread_id: str) -> Path:
    tid = _sanitize_thread_id(thread_id)
    d = user_root(user_id) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{tid}.md"


def _parse_simple_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return {}, raw
    fm_block = raw[4:end]
    body = raw[end + 5 :]
    meta: dict[str, str] = {}
    for line in fm_block.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def _build_frontmatter(*, user_id: str, thread_id: str, message_count: int) -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        "---\n"
        f'thread_id: "{_sanitize_thread_id(thread_id)}"\n'
        f'user_id: "{user_id}"\n'
        f"updated_at: {now}\n"
        f"message_count: {message_count}\n"
        "---\n\n"
    )


def _header_line(role: str, **attrs: str) -> str:
    parts = [f"## [MSG] role={role}"]
    for k, v in attrs.items():
        if v:
            parts.append(f"{k}={v}")
    return " ".join(parts)


def _serialize_message(m: BaseMessage) -> str:
    if isinstance(m, SystemMessage):
        body = str(m.content or "")
        return _header_line("system") + "\n\n" + body + "\n\n"
    if isinstance(m, HumanMessage):
        body = str(m.content or "")
        return _header_line("user") + "\n\n" + body + "\n\n"
    if isinstance(m, ToolMessage):
        body = str(m.content or "")
        return (
            _header_line("tool", name=m.name or "tool", tool_call_id=m.tool_call_id or "")
            + "\n\n"
            + body
            + "\n\n"
        )
    if isinstance(m, AIMessage):
        body = str(m.content or "")
        out = _header_line("assistant") + "\n\n" + body.rstrip() + "\n"
        tcs = m.tool_calls or []
        if tcs:
            serializable: list[dict[str, Any]] = []
            for tc in tcs:
                if isinstance(tc, dict):
                    serializable.append(
                        {
                            "id": tc.get("id", ""),
                            "name": tc.get("name", ""),
                            "args": tc.get("args", {}),
                        }
                    )
                else:
                    serializable.append({"id": "", "name": "", "args": {}})
            out += "\n### tool_calls\n\n```json\n"
            out += json.dumps(serializable, ensure_ascii=False, indent=2)
            out += "\n```\n"
        out += "\n"
        return out
    return _header_line("user") + "\n\n" + str(getattr(m, "content", "") or "") + "\n\n"


def save_thread(user_id: str, thread_id: str, messages: list[BaseMessage]) -> None:
    path = session_md_path(user_id, thread_id)
    text = _build_frontmatter(
        user_id=user_id,
        thread_id=thread_id,
        message_count=len(messages),
    )
    for m in messages:
        text += _serialize_message(m)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_thread(user_id: str, thread_id: str) -> list[BaseMessage]:
    path = session_md_path(user_id, thread_id)
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8", errors="replace")
    _, body = _parse_simple_frontmatter(raw)
    return _parse_message_blocks(body)


def list_session_threads(user_id: str) -> list[dict[str, str]]:
    """列出用户 sessions 目录下的对话线程序列（按 md 修改时间新在前）。"""
    d = user_root(user_id) / "sessions"
    if not d.is_dir():
        return []
    rows: list[tuple[float, dict[str, str]]] = []
    for p in d.glob("*.md"):
        if not p.is_file():
            continue
        tid = p.stem
        if _sanitize_thread_id(tid) != tid:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        rows.append(
            (
                mtime,
                {
                    "thread_id": tid,
                    "updated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                },
            )
        )
    rows.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in rows]


# 标题必须单行：禁止 \s* 跨行吞掉正文（否则 user 块会把「hi」吃进 match）
_BLOCK_START = re.compile(r"^## \[MSG\] role=(\w+)([^\n]*)$", re.MULTILINE)


def _parse_message_blocks(body: str) -> list[BaseMessage]:
    body = body.strip()
    if not body:
        return []
    matches = list(_BLOCK_START.finditer(body))
    if not matches:
        return []
    out: list[BaseMessage] = []
    for i, m in enumerate(matches):
        role = m.group(1)
        attr_line = (m.group(2) or "").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        block = body[start:end].strip()
        attrs = _parse_attr_tokens(attr_line)
        out.append(_block_to_message(role, attrs, block))
    return out


def _parse_attr_tokens(s: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for tok in s.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            attrs[k.strip()] = v.strip()
    return attrs


def _block_to_message(role: str, attrs: dict[str, str], block: str) -> BaseMessage:
    if role == "system":
        return SystemMessage(content=block)
    if role == "user":
        return HumanMessage(content=block)
    if role == "tool":
        return ToolMessage(
            content=block,
            name=attrs.get("name") or "tool",
            tool_call_id=attrs.get("tool_call_id") or "",
        )
    if role == "assistant":
        if "\n### tool_calls\n" in block:
            main, _, tail = block.partition("\n### tool_calls\n")
            main = main.strip()
            tc_raw = tail.strip()
            if tc_raw.startswith("```"):
                lines = tc_raw.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                while lines and lines[-1].strip() == "```":
                    lines.pop()
                tc_raw = "\n".join(lines).strip()
            try:
                loaded = json.loads(tc_raw)
            except json.JSONDecodeError:
                loaded = []
            if isinstance(loaded, list):
                tool_calls = [_normalize_tool_call(x) for x in loaded if isinstance(x, dict)]
            else:
                tool_calls = []
            return AIMessage(content=main, tool_calls=tool_calls or [])
        return AIMessage(content=block)
    return HumanMessage(content=block)


def _normalize_tool_call(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(d.get("id", "")),
        "name": str(d.get("name", "")),
        "args": d.get("args") if isinstance(d.get("args"), dict) else {},
    }

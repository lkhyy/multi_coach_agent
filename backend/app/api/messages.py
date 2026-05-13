from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.prompts.scheduler_system import SCHEDULER_BOOTSTRAP_MARKER, SCHEDULER_SYSTEM_PROMPT


def lc_messages_from_client(messages: list[dict[str, Any]]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "user":
            out.append(HumanMessage(content=str(content)))
        elif role == "assistant":
            out.append(AIMessage(content=str(content)))
        elif role == "system":
            out.append(SystemMessage(content=str(content)))
        elif role == "tool":
            name = str(m.get("name") or "tool")
            tool_call_id = str(m.get("tool_call_id") or "")
            out.append(ToolMessage(content=str(content), name=name, tool_call_id=tool_call_id))
        else:
            out.append(HumanMessage(content=str(content)))
    return out


def messages_to_client_list(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """供 REST 返回：与 WS 客户端 `messages` 结构对齐，assistant 含 tool_calls。"""
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": str(m.content or "")})
        elif isinstance(m, HumanMessage):
            out.append({"role": "user", "content": str(m.content or "")})
        elif isinstance(m, AIMessage):
            d: dict[str, Any] = {"role": "assistant", "content": str(m.content or "")}
            if m.tool_calls:
                tcs: list[dict[str, Any]] = []
                for tc in m.tool_calls:
                    if isinstance(tc, dict):
                        tcs.append(
                            {
                                "id": tc.get("id", ""),
                                "name": tc.get("name", ""),
                                "args": tc.get("args") if isinstance(tc.get("args"), dict) else {},
                            }
                        )
                d["tool_calls"] = tcs
            out.append(d)
        elif isinstance(m, ToolMessage):
            out.append(
                {
                    "role": "tool",
                    "content": str(m.content or ""),
                    "name": m.name or "tool",
                    "tool_call_id": m.tool_call_id or "",
                }
            )
        else:
            out.append({"role": "user", "content": str(getattr(m, "content", "") or "")})
    return out


def ensure_scheduler_bootstrap(messages: list[BaseMessage]) -> list[BaseMessage]:
    """在消息列表前注入主调度系统提示（含 worker_prompt 模板），避免重复注入。"""
    for m in messages:
        if isinstance(m, SystemMessage) and SCHEDULER_BOOTSTRAP_MARKER in (m.content or ""):
            return messages
    return [SystemMessage(content=SCHEDULER_SYSTEM_PROMPT), *messages]

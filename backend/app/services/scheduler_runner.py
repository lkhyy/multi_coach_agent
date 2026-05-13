from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage

from app.config import settings
from app.graphs.runtime import get_scheduler_graph
from app.task_notify.broker import notification_broker
from app.task_notify.notify_messages import notification_system_message
from app.state.schemas import SchedulerState

logger = logging.getLogger(__name__)


async def run_scheduler_followup_cycles(
    msgs_acc: list[BaseMessage],
    *,
    user_id: str,
    ws: WebSocket | None,
) -> list[BaseMessage]:
    """与 WS 请求内相同的「多轮主图 + 队列 drain / 续跑」逻辑；ws=None 时不向前端推送。"""
    graph = get_scheduler_graph()
    cfg: dict[str, Any] = {
        "configurable": {"user_id": user_id},
        "recursion_limit": max(4, int(settings.scheduler_recursion_limit)),
    }
    max_rounds = max(1, int(settings.max_scheduler_auto_rounds))
    rounds_done = 0

    while rounds_done < max_rounds:
        init: SchedulerState = {"messages": msgs_acc, "pending_notifications": []}
        if ws is not None:
            await ws.send_text(
                json.dumps(
                    {"type": "run_start", "round": rounds_done + 1, "auto": rounds_done > 0},
                    ensure_ascii=False,
                )
            )
        msgs_acc = await stream_scheduler_round(ws, graph, init, cfg)
        rounds_done += 1

        leftover = await notification_broker.drain_all(user_id)
        if not leftover:
            break
        if rounds_done >= max_rounds:
            for xml in leftover:
                await notification_broker.push(user_id, xml)
            msg = (
                f"已达 max_scheduler_auto_rounds={max_rounds}，"
                "仍有 task-notification 未交给模型；已将其推回队列以待下次处理。"
            )
            if ws is not None:
                await ws.send_text(json.dumps({"type": "warning", "message": msg}, ensure_ascii=False))
            else:
                logger.warning("idle_processor user=%s: %s", user_id, msg)
            break
        msgs_acc = list(msgs_acc) + [notification_system_message(xml) for xml in leftover]

    if ws is not None:
        await ws.send_text(json.dumps({"type": "run_end"}, ensure_ascii=False))
    return msgs_acc


async def stream_scheduler_round(
    ws: WebSocket | None,
    graph,
    init: SchedulerState,
    cfg: dict[str, Any],
) -> list[BaseMessage]:
    last_values: dict[str, Any] | None = None
    async for ev in graph.astream(
        init,
        config=cfg,
        stream_mode=["messages", "values", "updates"],
        version="v2",
        subgraphs=False,
    ):
        et = ev.get("type")
        if et == "values":
            last_values = ev.get("data")
            continue
        if et == "messages":
            await _emit_messages_stream(ws, ev)
            continue
        if et == "updates":
            await _emit_updates_stream(ws, ev)
            continue

    if isinstance(last_values, dict) and isinstance(last_values.get("messages"), list):
        return list(last_values["messages"])
    return list(init.get("messages") or [])


async def _emit_messages_stream(ws: WebSocket | None, ev: dict[str, Any]) -> None:
    if ws is None:
        return
    if ev.get("ns"):
        return
    data = ev.get("data")
    if not isinstance(data, tuple) or not data:
        return
    chunk = data[0]
    meta = data[1] if len(data) > 1 else {}
    if not isinstance(meta, dict):
        meta = {}
    if meta.get("langgraph_node") != "agent":
        return
    if not isinstance(chunk, AIMessageChunk) or not chunk.content:
        return
    if isinstance(chunk.content, str):
        delta = chunk.content
    else:
        delta = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in chunk.content
        )
    if not delta:
        return
    await ws.send_text(json.dumps({"type": "token", "delta": delta}, ensure_ascii=False))


async def _emit_updates_stream(ws: WebSocket | None, ev: dict[str, Any]) -> None:
    if ws is None:
        return
    if ev.get("ns"):
        return
    data = ev.get("data")
    if not isinstance(data, dict):
        return
    for node_name, delta in data.items():
        if not isinstance(delta, dict):
            continue
        for m in delta.get("messages") or []:
            if node_name == "agent" and isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "tool_start",
                                "name": tc.get("name"),
                                "input": tc.get("args"),
                            },
                            ensure_ascii=False,
                        )
                    )
            if node_name == "tools" and isinstance(m, ToolMessage):
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "tool_end",
                            "name": m.name,
                            "output": _tool_output_to_text(m),
                        },
                        ensure_ascii=False,
                    )
                )


def _tool_output_to_text(out: Any) -> str:
    if out is None:
        return ""
    if isinstance(out, str):
        return out
    if isinstance(out, BaseMessage):
        return str(out.content)
    return str(out)

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.config import settings
from app.graphs.worker import extract_worker_answer
from app.task_notify.broker import notification_broker
from app.task_notify.xml_notify import build_task_notification

logger = logging.getLogger(__name__)


async def invoke_blocking_worker(
    *,
    user_id: str,
    worker_prompt: str,
    task_instruction: str,
) -> str:
    from app.graphs.runtime import get_worker_graph

    graph = get_worker_graph()
    tid = str(uuid4())
    cfg: RunnableConfig = {
        "configurable": {"user_id": user_id, "thread_id": tid},
        "recursion_limit": max(4, int(settings.worker_recursion_limit)),
    }
    seed = [
        SystemMessage(content=worker_prompt),
        HumanMessage(content=task_instruction),
    ]
    out = await graph.ainvoke({"messages": seed}, config=cfg)
    return extract_worker_answer(out["messages"]) or "(子智能体未产生文本答复)"


def spawn_nonblocking_worker(*, user_id: str, worker_prompt: str, task_instruction: str) -> str:
    from app.graphs.runtime import get_worker_graph

    graph = get_worker_graph()
    tid = str(uuid4())
    cfg: RunnableConfig = {
        "configurable": {"user_id": user_id, "thread_id": tid},
        "recursion_limit": max(4, int(settings.worker_recursion_limit)),
    }
    seed = [
        SystemMessage(content=worker_prompt),
        HumanMessage(content=task_instruction),
    ]

    async def _bg() -> None:
        try:
            out = await graph.ainvoke({"messages": seed}, config=cfg)
            summary = extract_worker_answer(out["messages"]) or "(无文本)"
            xml = build_task_notification(
                summary=summary,
                worker_thread_id=tid,
                status="completed",
            )
            await notification_broker.push(user_id, xml)
        except Exception as exc:  # noqa: BLE001
            logger.exception("non-blocking worker failed")
            xml = build_task_notification(
                summary=f"子智能体异常：{exc}",
                worker_thread_id=tid,
                status="failed",
            )
            await notification_broker.push(user_id, xml)

    asyncio.create_task(_bg())
    return tid

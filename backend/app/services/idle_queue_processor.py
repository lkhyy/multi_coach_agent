from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import HumanMessage

from app.api.messages import ensure_scheduler_bootstrap
from app.config import settings
from app.memory import (
    build_runtime_context_messages,
    incremental_new_messages,
    strip_runtime_only_messages,
)
from app.memory.store import update_long_term_memory_heuristic
from app.memory.extract import update_learning_content_pipeline
from app.memory.learning_store import load_learning_content
from app.memory.thread_learning import get_thread_learning_content_id
from app.task_notify.broker import notification_broker
from app.task_notify.notify_messages import notification_system_message
from app.services.scheduler_runner import run_scheduler_followup_cycles
from app.session.conversation_store import get_snapshot, set_snapshot
from app.session.md_session_store import load_thread, save_thread
from app.session.thread_registry import get_last_thread
from app.session.user_run_lock import acquire_user_scheduler, release_user_scheduler

logger = logging.getLogger(__name__)


def _idle_seed_messages() -> list:
    return ensure_scheduler_bootstrap(
        [
            HumanMessage(
                content=(
                    "（系统·空闲队列）当前没有前端会话上下文；"
                    "请仅根据随后附上的 task-notification 与可用工具，总结结果、更新计划或写入知识库。"
                )
            ),
        ]
    )


async def idle_queue_processor_loop(stop: asyncio.Event) -> None:
    """主助手无 WS 占用时，自动消化全局通知队列并写回会话快照。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=float(settings.idle_queue_poll_interval))
            return
        except TimeoutError:
            pass
        if not settings.idle_queue_processor_enabled:
            continue
        for user_id in notification_broker.iter_users_with_pending():
            await _process_user_burst(user_id)


async def _process_user_burst(user_id: str) -> None:
    if not await acquire_user_scheduler(user_id, blocking=False):
        return
    try:
        while notification_broker.has_pending(user_id):
            drained = await notification_broker.drain_all(user_id)
            if not drained:
                break
            tid = get_last_thread(user_id)
            base = get_snapshot(user_id) or load_thread(user_id, tid)
            if base:
                full_history = list(base) + [notification_system_message(xml) for xml in drained]
            else:
                full_history = list(_idle_seed_messages()) + [notification_system_message(xml) for xml in drained]
            full_history = ensure_scheduler_bootstrap(full_history)
            cid = get_thread_learning_content_id(user_id, tid)
            lr = load_learning_content(user_id, cid) if cid else None
            runtime_context = build_runtime_context_messages(
                user_id, full_history, learning_record=lr
            )
            runtime_out = await run_scheduler_followup_cycles(
                runtime_context,
                user_id=user_id,
                ws=None,
                thread_id=tid,
            )
            delta = incremental_new_messages(runtime_context, runtime_out)
            full_out = strip_runtime_only_messages(full_history + delta)
            update_long_term_memory_heuristic(user_id, full_out)
            if cid:
                update_learning_content_pipeline(user_id, cid, full_out)
            save_thread(user_id, tid, full_out)
            set_snapshot(user_id, full_out)
    except Exception:  # noqa: BLE001
        logger.exception("idle queue processor failed user=%s", user_id)
    finally:
        release_user_scheduler(user_id)

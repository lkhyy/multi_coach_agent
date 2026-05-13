from __future__ import annotations

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.api.messages import ensure_scheduler_bootstrap, lc_messages_from_client
from app.memory import (
    build_runtime_context_messages,
    incremental_new_messages,
    strip_runtime_only_messages,
    update_long_term_memory_from_messages,
)
from app.services.scheduler_runner import run_scheduler_followup_cycles
from app.session.conversation_store import set_snapshot
from app.session.md_session_store import load_thread, save_thread
from app.session.thread_registry import set_last_thread
from app.session.user_run_lock import acquire_user_scheduler, release_user_scheduler

logger = logging.getLogger(__name__)


async def scheduler_websocket_loop(ws: WebSocket, *, user_id: str) -> None:
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            payload = json.loads(raw)
            ptype = payload.get("type")
            if ptype == "ping":
                await ws.send_text(json.dumps({"type": "pong"}, ensure_ascii=False))
                continue
            if ptype != "chat":
                await ws.send_text(
                    json.dumps({"type": "error", "message": "unknown type"}, ensure_ascii=False)
                )
                continue

            new_msgs = lc_messages_from_client(list(payload.get("messages") or []))
            if not new_msgs:
                await ws.send_text(
                    json.dumps({"type": "error", "message": "messages 不能为空（请只发本轮新句）"}, ensure_ascii=False)
                )
                continue

            thread_id = str(payload.get("thread_id") or "default")
            set_last_thread(user_id, thread_id)

            prior = load_thread(user_id, thread_id)
            full_history = ensure_scheduler_bootstrap(list(prior) + new_msgs)
            runtime_context = build_runtime_context_messages(user_id, full_history)

            await acquire_user_scheduler(user_id, blocking=True)
            try:
                runtime_out = await run_scheduler_followup_cycles(
                    runtime_context,
                    user_id=user_id,
                    ws=ws,
                )
                delta = incremental_new_messages(runtime_context, runtime_out)
                full_out = strip_runtime_only_messages(full_history + delta)
                update_long_term_memory_from_messages(user_id, full_out)
                save_thread(user_id, thread_id, full_out)
                set_snapshot(user_id, full_out)
            except Exception as exc:  # noqa: BLE001
                logger.exception("graph run failed")
                await ws.send_text(
                    json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)
                )
            finally:
                release_user_scheduler(user_id)
    except WebSocketDisconnect:
        return

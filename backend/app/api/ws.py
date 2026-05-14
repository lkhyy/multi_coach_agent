from __future__ import annotations

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.api.messages import ensure_scheduler_bootstrap, lc_messages_from_client
from app.memory import (
    build_runtime_context_messages,
    incremental_new_messages,
    strip_runtime_only_messages,
)
from app.memory.store import update_long_term_memory_heuristic
from app.memory.extract import update_learning_content_pipeline
from app.memory.resolve_learning import resolve_learning_content_for_thread
from app.memory.thread_learning import get_thread_learning_content_id
from app.services.scheduler_runner import run_scheduler_followup_cycles
from app.session.conversation_store import set_snapshot
from app.session.md_session_store import load_thread, save_thread
from app.session.thread_registry import set_last_thread
from app.session.user_run_lock import acquire_user_scheduler, release_user_scheduler

logger = logging.getLogger(__name__)


async def scheduler_websocket_loop(ws: WebSocket, *, user_id: str, pre_accepted: bool = False) -> None: #这是一个WebSocket循环，用于处理用户与服务器的通信。它接收用户发送的消息，处理后发送响应。
    if not pre_accepted:
        await ws.accept()
    try:
        while True:         #这是一个无限循环，用于持续处理用户的消息。
            raw = await ws.receive_text()    #接收用户发送的消息。  
            payload = json.loads(raw)        #将接收到的消息转换为JSON格式。
            ptype = payload.get("type")       #获取消息的类型。
            if ptype == "ping":
                await ws.send_text(json.dumps({"type": "pong"}, ensure_ascii=False))   #发送响应消息。  
                continue
            if ptype != "chat":
                await ws.send_text(
                    json.dumps({"type": "error", "message": "unknown type"}, ensure_ascii=False)  #发送错误消息。  
                )
                continue

            new_msgs = lc_messages_from_client(list(payload.get("messages") or []))     #将接收到的消息转换为LangChain消息列表。
            if not new_msgs:
                await ws.send_text(
                    json.dumps({"type": "error", "message": "messages 不能为空（请只发本轮新句）"}, ensure_ascii=False)  #发送错误消息。  
                )
                continue
            thread_id = str(payload.get("thread_id") or "default")  #获取对话ID。
            set_last_thread(user_id, thread_id)  #更新最后一次对话ID。
            prior = load_thread(user_id, thread_id)  #加载对话历史。
            full_history = ensure_scheduler_bootstrap(list(prior) + new_msgs)  #确保对话历史符合调度器要求。
            learning_record = resolve_learning_content_for_thread(  #解析学习内容。
                user_id,
                thread_id,
                new_msgs,  #新消息。
                explicit_content_id=str(payload.get("learning_content_id") or ""),  #显式学习内容ID。
                learning_match_query=str(payload.get("learning_match_query") or ""),  #学习匹配查询。
            )
            runtime_context = build_runtime_context_messages(
                user_id, full_history, learning_record=learning_record
            )

            await acquire_user_scheduler(user_id, blocking=True)
            try:
                runtime_out = await run_scheduler_followup_cycles(
                    runtime_context,
                    user_id=user_id,
                    ws=ws,
                    thread_id=thread_id,
                )
                delta = incremental_new_messages(runtime_context, runtime_out)
                full_out = strip_runtime_only_messages(full_history + delta)
                update_long_term_memory_heuristic(user_id, full_out)
                cid = get_thread_learning_content_id(user_id, thread_id)
                if cid:
                    update_learning_content_pipeline(user_id, cid, full_out)
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

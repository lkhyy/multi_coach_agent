from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.api.messages import messages_to_client_list
from app.auth.deps import get_current_user_id
from app.config import settings
from app.memory.learning_store import aggregate_plan_status_cn, list_learning_index, load_learning_content
from app.memory.thread_learning import get_thread_learning_content_id, preferred_thread_for_content
from app.memory.store import load_long_term_memory
from app.rag.upload_jobs import create_rag_upload_job, get_job_for_user, patch_job, run_rag_upload_sync
from app.session.md_session_store import list_session_threads, load_thread, session_md_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["me"])


def _sniff_pdf_magic(data: bytes) -> bool:
    return len(data) >= 4 and data.startswith(b"%PDF")


_TEXT_UPLOAD_SUFFIX = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".log",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".html",
    ".htm",
    ".xml",
    ".yaml",
    ".yml",
    ".css",
    ".c",
    ".h",
    ".cpp",
    ".go",
    ".rs",
    ".java",
    ".sql",
    ".sh",
    ".env",
    ".pdf",
}


@router.post("/rag/upload")
async def upload_rag_document(
    user_id: Annotated[str, Depends(get_current_user_id)],
    file: UploadFile = File(...),
) -> JSONResponse:
    """接收文件后立即返回 job_id；解析与入库在后台线程执行，进度见 GET /rag/upload/jobs/{job_id}。"""
    fname = (file.filename or "upload").strip() or "upload"
    suf = Path(fname).suffix.lower()
    if suf and suf not in _TEXT_UPLOAD_SUFFIX:
        raise HTTPException(
            400,
            f"不支持的扩展名（支持 .pdf 与常见文本后缀），当前：{suf or '无'}",
        )
    raw = await file.read()
    max_bytes = int(max(1.0, min(256.0, float(settings.rag_upload_max_mb))) * 1024 * 1024)
    if len(raw) > max_bytes:
        raise HTTPException(413, f"文件超过上传上限（当前配置 {settings.rag_upload_max_mb:g} MB）")
    effective_suf = suf
    if effective_suf != ".pdf" and _sniff_pdf_magic(raw):
        effective_suf = ".pdf"
    if effective_suf != ".pdf":
        text = raw.decode("utf-8", errors="replace")
        if text.startswith("\ufeff"):
            text = text[1:]
        if not text.strip():
            raise HTTPException(400, "文件为空或无法解码为有效文本")

    job_id = create_rag_upload_job(user_id)
    patch_job(job_id, status="processing", progress=2, message="已接收，后台处理中…")

    async def _run() -> None:
        try:
            await asyncio.to_thread(run_rag_upload_sync, job_id, user_id, raw, fname, effective_suf)
        except Exception:
            logger.exception("rag upload background task failed job_id=%s", job_id)

    asyncio.create_task(_run())
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "message": "已受理，请轮询任务状态"},
    )


@router.get("/rag/upload/jobs/{job_id}")
def get_rag_upload_job(
    job_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    row = get_job_for_user(job_id, user_id)
    if row is None:
        raise HTTPException(404, "任务不存在或无权查看")
    return row


@router.get("/sessions")
def list_my_sessions(user_id: Annotated[str, Depends(get_current_user_id)]) -> dict:
    """列出当前用户已落盘的对话线程（sessions/*.md），按最近修改时间倒序。"""
    return {"items": list_session_threads(user_id)}


@router.get("/session/{thread_id}/learning-plan")
def get_session_learning_plan(
    thread_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    cid = get_thread_learning_content_id(user_id, thread_id)
    if not cid:
        return {
            "bound": False,
            "content_id": None,
            "title": "",
            "learning_plan": "",
            "plan_phases": [],
        }
    rec = load_learning_content(user_id, cid)
    if rec is None:
        return {
            "bound": False,
            "content_id": cid,
            "title": "",
            "learning_plan": "",
            "plan_phases": [],
        }
    return {
        "bound": True,
        "content_id": rec.content_id,
        "title": rec.title,
        "learning_plan": rec.learning_plan,
        "plan_phases": list(rec.plan_phases),
    }


@router.get("/session/{thread_id}")
def get_my_session(
    thread_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    msgs = load_thread(user_id, thread_id)
    path = session_md_path(user_id, thread_id)
    storage: str | None = None
    try:
        storage = str(path.resolve().relative_to(Path(settings.data_dir).resolve()))
    except ValueError:
        storage = path.name
    return {
        "user_id": user_id,
        "thread_id": thread_id,
        "storage": storage,
        "exists": path.is_file(),
        "messages": messages_to_client_list(msgs),
    }


@router.get("/memory/long-term")
def get_my_long_term_memory(user_id: Annotated[str, Depends(get_current_user_id)]) -> dict:
    mem = load_long_term_memory(user_id)
    return asdict(mem)


@router.get("/memory/learning-contents")
def list_my_learning_contents(user_id: Annotated[str, Depends(get_current_user_id)]) -> dict:
    """侧栏与列表始终以落盘的学习内容 JSON 为准，避免仅索引滞后导致与已保存计划不一致。"""
    entries = list_learning_index(user_id)
    merged: list = []
    for e in entries:
        rec = load_learning_content(user_id, e.content_id)
        if rec is None:
            merged.append(e)
            continue
        phases = list(rec.plan_phases) if rec.plan_phases else []
        has_plan = bool((rec.learning_plan or "").strip())
        agg = aggregate_plan_status_cn(phases) if phases else "未完成"
        merged.append(
            replace(
                e,
                title=rec.title or e.title,
                summary=rec.summary or e.summary,
                keywords=list(rec.keywords),
                updated_at=rec.updated_at or e.updated_at,
                has_learning_plan=has_plan,
                mastery_status=(rec.mastery_status or "").strip()[:200],
                plan_confirmed_at=str(rec.plan_confirmed_at or ""),
                plan_phases=phases,
                aggregate_plan_status_cn=agg,
            )
        )
    return {"items": [asdict(x) for x in merged]}


@router.get("/memory/learning-contents/{content_id}/preferred-thread")
def get_preferred_thread_for_learning_content(
    content_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    tid = preferred_thread_for_content(user_id, content_id)
    if not tid:
        raise HTTPException(404, "该学习内容尚无已绑定的对话线程，请先在对应学习对话中打开该主题。")
    return {"thread_id": tid}


@router.get("/memory/learning-contents/{content_id}")
def get_my_learning_content(
    content_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    rec = load_learning_content(user_id, content_id)
    if rec is None:
        raise HTTPException(404, "学习内容不存在")
    return asdict(rec)

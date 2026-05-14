from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from app.rag.ingest import ingest_plain_text
from app.rag.pdf_text import extract_text_from_pdf_bytes

logger = logging.getLogger(__name__)

_MAX_JOBS = 256
_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _evict_finished_if_full() -> None:
    if len(_jobs) < _MAX_JOBS:
        return
    for k, v in list(_jobs.items()):
        if v.get("status") in ("done", "error"):
            del _jobs[k]
            if len(_jobs) < _MAX_JOBS:
                return


def create_rag_upload_job(user_id: str) -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _evict_finished_if_full()
        _jobs[job_id] = {
            "user_id": user_id,
            "status": "queued",
            "progress": 0,
            "message": "排队中…",
            "result": None,
            "error": None,
        }
    return job_id


def patch_job(job_id: str, **kwargs: Any) -> None:
    with _lock:
        if job_id not in _jobs:
            return
        _jobs[job_id].update(kwargs)


def get_job_for_user(job_id: str, user_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _jobs.get(job_id)
        if not row or row.get("user_id") != user_id:
            return None
        return {
            "status": row["status"],
            "progress": int(row["progress"]),
            "message": str(row.get("message") or ""),
            "result": row.get("result"),
            "error": row.get("error"),
        }


def run_rag_upload_sync(job_id: str, user_id: str, raw: bytes, fname: str, effective_suf: str) -> None:
    """在 worker 线程中执行：解析文件并入库，期间更新 job 状态。"""
    ocr_used = False
    try:
        patch_job(job_id, status="processing", progress=5, message="处理中…")
        if effective_suf == ".pdf":
            patch_job(job_id, progress=10, message="解析 PDF…")
            pdf_res = extract_text_from_pdf_bytes(raw)
            text = pdf_res.text.strip()
            if not text:
                detail = pdf_res.ocr_hint or (
                    "PDF 中未提取到文本。若为扫描件，请安装 Tesseract 与语言包并开启 RAG_PDF_OCR_FALLBACK。"
                )
                raise ValueError(detail)
            ocr_used = pdf_res.ocr_used
        else:
            text = raw.decode("utf-8", errors="replace")
            if text.startswith("\ufeff"):
                text = text[1:]
            if not text.strip():
                raise ValueError("文件为空或无法解码为有效文本")

        patch_job(job_id, progress=22, message="切块并写入索引…")

        def on_chunk(i: int, n: int) -> None:
            p = 22 + int(72 * i / max(n, 1))
            patch_job(job_id, progress=min(p, 98), message=f"写入块 {i}/{n}…")

        res = ingest_plain_text(user_id, text, on_chunk_progress=on_chunk)
        out: dict[str, Any] = {
            "doc_id": res.doc_id,
            "chunk_count": res.chunk_count,
            "filename": fname,
        }
        if effective_suf == ".pdf":
            out["pdf_ocr_used"] = ocr_used
        patch_job(job_id, status="done", progress=100, message="完成", result=out, error=None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("rag upload job failed job_id=%s", job_id)
        patch_job(
            job_id,
            status="error",
            progress=0,
            message="失败",
            result=None,
            error=str(exc),
        )

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.rag.ingest import ingest_plain_text
from app.rag.retriever import RrfRetriever
from app.services.worker_runner import invoke_blocking_worker, spawn_nonblocking_worker


def build_scheduler_tools():
    @tool
    async def run_worker_blocking(
        worker_prompt: str,
        task_instruction: str,
        config: RunnableConfig,
    ) -> str:
        """前台阻塞：当前轮 await 子 LangGraph，结果作为 tool_message 立刻回到主调度。"""
        user_id = str(config["configurable"].get("user_id") or "")
        if not user_id:
            return "缺少 user_id。"
        return await invoke_blocking_worker(
            user_id=user_id,
            worker_prompt=worker_prompt,
            task_instruction=task_instruction,
        )

    @tool
    async def run_worker_nonblocking(
        worker_prompt: str,
        task_instruction: str,
        config: RunnableConfig,
    ) -> str:
        """后台非阻塞：子 LangGraph 完成后写入 task-notification XML，由下一轮主循环吸收。"""
        user_id = str(config["configurable"].get("user_id") or "")
        if not user_id:
            return "缺少 user_id。"
        tid = spawn_nonblocking_worker(
            user_id=user_id,
            worker_prompt=worker_prompt,
            task_instruction=task_instruction,
        )
        return (
            "已启动后台子智能体。"
            f" worker_thread_id={tid}。"
            "完成后将以 <task-notification> XML 进入全局命令队列。"
        )

    @tool
    async def scheduler_rag_search(query: str, config: RunnableConfig) -> str:
        """主调度专用：在当前用户隔离的知识库中 RRF 检索。"""
        user_id = str(config["configurable"].get("user_id") or "")
        if not user_id:
            return "缺少 user_id。"
        try:
            hits = RrfRetriever(user_id=user_id).retrieve(query, limit=8)
        except Exception as exc:  # noqa: BLE001
            return f"检索失败：{exc}"
        if not hits:
            return "知识库中未找到相关内容。"
        parts = [f"### {h.chunk_id}\n{h.text}\n" for h in hits]
        return "\n".join(parts)

    @tool
    async def scheduler_ingest_text(material_text: str, config: RunnableConfig) -> str:
        """将学习材料切块写入该用户的 SQLite FTS5 + Chroma（BGE-M3）。"""
        user_id = str(config["configurable"].get("user_id") or "")
        if not user_id:
            return "缺少 user_id。"
        if not material_text.strip():
            return "文本为空，已跳过入库。"
        try:
            did = ingest_plain_text(user_id, material_text)
        except Exception as exc:  # noqa: BLE001
            return f"入库失败：{exc}"
        return f"入库完成，doc_id={did}"

    return [
        run_worker_blocking,
        run_worker_nonblocking,
        scheduler_rag_search,
        scheduler_ingest_text,
    ]

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.memory.context import strip_runtime_only_messages
from app.memory.extract import update_long_term_memory_pipeline
from app.memory.learning_store import load_learning_content, parse_plan_phases_json_string, save_learning_content
from app.memory.thread_learning import get_thread_learning_content_id
from app.rag.ingest import ingest_plain_text
from app.rag.retriever import RrfRetriever
from app.state.schemas import SchedulerState

logger = logging.getLogger(__name__)


def build_scheduler_tools():
    from app.services.worker_runner import invoke_blocking_worker, spawn_nonblocking_worker

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
    async def merge_user_long_term_profile_blocking(
        rationale: str,
        state: Annotated[SchedulerState, InjectedState],
        config: RunnableConfig,
    ) -> str:
        """用小模型合并并写入用户级 long_term.json（人格/偏好/学习目标摘要/稳定事实）。
        当你需要在**继续推理或回复用户前**就依赖「已落盘的最新档案」时选用；会阻塞数秒。"""
        user_id = str(config["configurable"].get("user_id") or "")
        if not user_id:
            return "缺少 user_id。"
        msgs = strip_runtime_only_messages(list(state["messages"]))
        if not msgs:
            return "当前无可用消息，已跳过档案合并。"
        try:
            mem = await asyncio.to_thread(update_long_term_memory_pipeline, user_id, msgs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("blocking long-term merge failed user=%s", user_id)
            return f"档案合并失败：{exc}"
        n = len(mem.personality) + len(mem.style_preferences) + len(mem.learning_goals) + len(mem.stable_facts)
        return f"已阻塞写入用户档案（理由：{rationale}）。合并后条目约 {n} 条，可继续对话。"

    @tool
    async def queue_merge_user_long_term_profile(
        rationale: str,
        state: Annotated[SchedulerState, InjectedState],
        config: RunnableConfig,
    ) -> str:
        """将「小模型合并用户档案」排入后台线程，立即返回，不阻塞主对话。
        适用于本轮以回复速度优先、档案可稍后一致的场景（仍在本进程内执行，非子 LangGraph 队列）。"""
        user_id = str(config["configurable"].get("user_id") or "")
        if not user_id:
            return "缺少 user_id。"
        msgs = strip_runtime_only_messages(list(state["messages"]))
        if not msgs:
            return "当前无可用消息，已跳过排队。"

        async def _bg() -> None:
            try:
                await asyncio.to_thread(update_long_term_memory_pipeline, user_id, list(msgs))
            except Exception:
                logger.exception("queued long-term merge failed user=%s", user_id)

        asyncio.create_task(_bg())
        return f"已排队后台合并用户档案（理由：{rationale}）。你可继续回复用户。"

    @tool
    async def scheduler_save_learning_plan(
        learning_plan: str,
        mastery_status: str,
        progress_summary: str,
        rationale: str,
        config: RunnableConfig,
        plan_phases_json: str = "",
    ) -> str:
        """将已与用户核对的学习计划、掌握状态与进度摘要写入**当前对话线程**绑定的学习内容条目。
        仅在用户明确同意计划后调用；需非空的 learning_plan。
        plan_phases_json：可选 JSON 数组；与 `learning_plan` 一并写入。未传或无法解析出阶段时**不会**自动虚构阶段；侧栏阶段表须通过 `plan_phases_json` 或 **`scheduler_commit_learning_phases`** 写入。显式传入 `[]` 可清空阶段列表。"""
        user_id = str(config["configurable"].get("user_id") or "")
        thread_id = str(config["configurable"].get("thread_id") or "")
        if not user_id:
            return "缺少 user_id。"
        if not thread_id:
            return "缺少 thread_id，无法定位学习内容绑定。"
        cid = get_thread_learning_content_id(user_id, thread_id)
        if not cid:
            return "当前线程尚未绑定学习内容；请先通过对话创建/绑定学习主题后再保存计划。"
        plan = (learning_plan or "").strip()
        if not plan:
            return "learning_plan 不能为空。"
        rec = load_learning_content(user_id, cid)
        if rec is None:
            return f"未找到学习内容 content_id={cid}。"
        rec.learning_plan = plan[:8000]
        rec.mastery_status = (mastery_status or "").strip()[:200]
        rec.progress_summary = (progress_summary or "").strip()[:2000]
        rec.plan_confirmed_at = datetime.now(timezone.utc).isoformat()
        raw_json = (plan_phases_json or "").strip()
        phases = parse_plan_phases_json_string(plan_phases_json)
        if phases:
            rec.plan_phases = phases
        elif raw_json == "[]":
            rec.plan_phases = []
        save_learning_content(user_id, rec)
        return (
            f"已写入学习计划（content_id={cid}）。理由：{rationale}。"
            f"阶段数={len(rec.plan_phases)}。"
            "用户侧栏与对话内计划表将随之更新。"
        )

    @tool
    async def scheduler_commit_learning_phases(
        plan_phases_json: str,
        rationale: str,
        config: RunnableConfig,
        learning_plan: str = "",
        mastery_status: str = "",
        progress_summary: str = "",
    ) -> str:
        """将**结构化分阶段学习计划**写入当前线程绑定的学习内容（`plan_phases`），供侧栏「学习计划」按阶段展示。
        须在已与用户确认各阶段后调用；`plan_phases_json` 须为**非空** JSON 数组。每项含：
        - `title`：阶段名（建议含 P0/P1 等前缀便于阅读）
        - `status`：`已完成`/`进行中`/`未完成` 或 `completed`/`in_progress`/`not_started`
        - `progress_pct`：整数 0–100；**进行中**建议 1–99（未给则按规则默认）
        可选 `learning_plan`：与阶段互补的说明正文（可空）；`mastery_status` / `progress_summary` 可顺带更新。
        工具执行成功后，前端会在 WebSocket `tool_end` 上立刻拉取 `/memory/learning-contents` 刷新侧栏。"""
        user_id = str(config["configurable"].get("user_id") or "")
        thread_id = str(config["configurable"].get("thread_id") or "")
        if not user_id:
            return "缺少 user_id。"
        if not thread_id:
            return "缺少 thread_id，无法定位学习内容绑定。"
        cid = get_thread_learning_content_id(user_id, thread_id)
        if not cid:
            return "当前线程尚未绑定学习内容；请先创建/绑定学习主题后再提交阶段。"
        phases = parse_plan_phases_json_string(plan_phases_json)
        if not phases:
            return "plan_phases_json 须解析出至少一个阶段；请检查 JSON 是否为非空数组且每项含 title。"
        rec = load_learning_content(user_id, cid)
        if rec is None:
            return f"未找到学习内容 content_id={cid}。"
        rec.plan_phases = phases
        lp = (learning_plan or "").strip()
        if lp:
            rec.learning_plan = lp[:8000]
        if (mastery_status or "").strip():
            rec.mastery_status = (mastery_status or "").strip()[:200]
        if (progress_summary or "").strip():
            rec.progress_summary = (progress_summary or "").strip()[:2000]
        rec.plan_confirmed_at = datetime.now(timezone.utc).isoformat()
        save_learning_content(user_id, rec)
        return (
            f"已提交结构化学习阶段（content_id={cid}），共 {len(phases)} 项。理由：{rationale}。"
            "侧栏学习计划应在工具返回后立即刷新。"
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
            res = ingest_plain_text(user_id, material_text)
        except Exception as exc:  # noqa: BLE001
            return f"入库失败：{exc}"
        return f"入库完成，doc_id={res.doc_id}，分块数={res.chunk_count}"

    return [
        run_worker_blocking,
        run_worker_nonblocking,
        merge_user_long_term_profile_blocking,
        queue_merge_user_long_term_profile,
        scheduler_save_learning_plan,
        scheduler_commit_learning_phases,
        scheduler_rag_search,
        scheduler_ingest_text,
    ]

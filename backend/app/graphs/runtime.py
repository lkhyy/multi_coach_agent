from __future__ import annotations

import logging
from typing import Any

from app.graphs.scheduler import build_scheduler_graph
from app.graphs.worker import compile_worker_graph
from app.tools.mcp_bundle import build_mcp_merged_toolsets
from app.tools.scheduler_tools import build_scheduler_tools
from app.tools.worker_tools import build_worker_tools

logger = logging.getLogger(__name__)

_scheduler_graph: Any = None
_worker_graph: Any = None


async def bootstrap_graphs() -> None:
    """应用启动时编译主图与 Worker 图（合并 MCP + 本地并按角色过滤）。"""
    global _scheduler_graph, _worker_graph
    try:
        s_tools, w_tools = await build_mcp_merged_toolsets()
    except Exception:  # noqa: BLE001
        logger.exception("构建合并工具集失败，回退为仅本地工具。")
        s_tools = build_scheduler_tools()
        w_tools = build_worker_tools()
    _scheduler_graph = build_scheduler_graph(s_tools)
    _worker_graph = compile_worker_graph(w_tools)
    logger.info(
        "LangGraph 已编译：scheduler_tools=%s worker_tools=%s",
        len(s_tools),
        len(w_tools),
    )


def get_scheduler_graph() -> Any:
    if _scheduler_graph is None:
        return build_scheduler_graph(build_scheduler_tools())
    return _scheduler_graph


def get_worker_graph() -> Any:
    if _worker_graph is None:
        return compile_worker_graph()
    return _worker_graph

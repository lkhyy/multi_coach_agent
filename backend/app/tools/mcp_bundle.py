from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.tools import BaseTool

from app.config import settings
from app.tools.scheduler_tools import build_scheduler_tools
from app.tools.worker_tools import build_worker_tools

logger = logging.getLogger(__name__)


def _pick_mcp_tools_for_role(mcp_tools: Sequence[BaseTool], names_csv: str) -> list[BaseTool]:
    """按角色白名单从 MCP 工具集中挑选；空字符串表示该角色不挂载任何 MCP 工具；* 表示全部 MCP。"""
    raw = names_csv.strip()
    if not raw:
        return []
    if raw == "*":
        return list(mcp_tools)
    allowed = {n.strip() for n in raw.split(",") if n.strip()}
    return [t for t in mcp_tools if t.name in allowed]


async def load_mcp_langchain_tools() -> list[BaseTool]:
    """通过 langchain-mcp-adapters 拉取 MCP 工具；未配置或失败时返回空列表。"""
    raw = (settings.mcp_servers_json or "").strip()
    if not raw:
        return []
    try:
        servers: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("MCP_SERVERS_JSON 不是合法 JSON，已忽略 MCP：%s", exc)
        return []
    if not isinstance(servers, dict) or not servers:
        logger.warning("MCP_SERVERS_JSON 须为非空 JSON 对象，已忽略 MCP。")
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(servers)
        tools = await client.get_tools()
    except Exception:  # noqa: BLE001
        logger.exception("连接 MCP 并加载工具失败，已忽略 MCP。")
        return []
    logger.info("已从 MCP 加载 %s 个工具", len(tools))
    return list(tools)


async def build_mcp_merged_toolsets() -> tuple[list[BaseTool], list[BaseTool]]:
    """本地工具 + MCP 工具合并后，再按主调度 / Worker 各自白名单过滤。"""
    mcp_tools = await load_mcp_langchain_tools()
    sched_mcp = _pick_mcp_tools_for_role(mcp_tools, settings.scheduler_mcp_tool_names)
    work_mcp = _pick_mcp_tools_for_role(mcp_tools, settings.worker_mcp_tool_names)
    scheduler_tools = list(build_scheduler_tools()) + sched_mcp
    worker_tools = list(build_worker_tools()) + work_mcp
    return scheduler_tools, worker_tools

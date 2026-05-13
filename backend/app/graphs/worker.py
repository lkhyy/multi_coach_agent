from __future__ import annotations

from langchain_core.messages import AIMessage, AnyMessage
from langgraph.prebuilt import create_react_agent

from app.services.llm import get_worker_llm
from app.tools.worker_tools import build_worker_tools


def compile_worker_graph(tools: list | None = None):
    """tools 为 None 时使用仅本地工具；启动后由 runtime 注入合并 MCP 后的列表。"""
    merged = list(build_worker_tools()) if tools is None else list(tools)
    model = get_worker_llm()
    return create_react_agent(model, tools=merged)


def extract_worker_answer(messages: list[AnyMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not (m.tool_calls or []):
            if m.content:
                return str(m.content)
    return ""

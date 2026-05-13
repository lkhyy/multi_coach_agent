from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.rag.retriever import RrfRetriever


@tool
def lookup_learning_note(topic: str) -> str:
    """占位：记录/回忆与学习主题相关的简短要点（后续可接记忆系统）。"""
    return f"[占位笔记] 与「{topic}」相关的要点尚未持久化，这里返回空结果。"


@tool
def user_rag_snippet(query: str, config: RunnableConfig) -> str:
    """在当前用户的私有知识库中做 BM25+向量 RRF 召回，返回拼接片段。"""
    user_id = str(config["configurable"].get("user_id") or "")
    if not user_id:
        return "缺少 user_id，无法检索。"
    try:
        retriever = RrfRetriever(user_id=user_id)
        hits = retriever.retrieve(query, limit=6)
    except Exception as exc:  # noqa: BLE001 - surface to model
        return f"检索失败：{exc}"
    if not hits:
        return "知识库中未找到相关内容。"
    parts = []
    for h in hits:
        parts.append(f"### chunk {h.chunk_id}\n{h.text}\n")
    return "\n".join(parts)


def build_worker_tools():
    """Worker 子图绑定的本地工具（与 MCP 合并前的基础集合）。"""
    return [lookup_learning_note, user_rag_snippet]

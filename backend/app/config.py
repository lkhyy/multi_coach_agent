from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_base_url: str | None = None
    scheduler_model: str = "gpt-4o-mini"
    worker_model: str = "gpt-4o-mini"
    data_dir: Path = Path("./data")
    # 单次用户 WS 请求内，主图跑完后若队列仍有通知，最多自动续跑轮数（无需用户再发消息）
    max_scheduler_auto_rounds: int = 12  # 可用环境变量 MAX_SCHEDULER_AUTO_ROUNDS 覆盖

    # 图内 agent↔tool 循环上限（LangGraph recursion_limit），与上面「队列自动续跑轮次」无关
    scheduler_recursion_limit: int = 40
    worker_recursion_limit: int = 40

    # MCP：MultiServerMCPClient 的服务器配置 JSON（见 langchain-mcp-adapters）；空则仅本地工具
    mcp_servers_json: str = ""
    # 逗号分隔的 MCP 工具名；空=该角色不挂载 MCP；* = 挂载本次加载到的全部 MCP（勿与本地 tool 重名）
    scheduler_mcp_tool_names: str = ""
    worker_mcp_tool_names: str = ""

    # 无 WebSocket 会话占用时，后台轮询消化通知队列
    idle_queue_processor_enabled: bool = True
    idle_queue_poll_interval: float = 2.0
    # 短期记忆窗口（仅用于每次喂给模型的上下文）
    short_term_max_messages: int = 80
    # 长期记忆列表上限（每个字段）
    long_term_list_max_items: int = 20

    # RAG 切块：先按章节边界切，再在章内按 tiktoken 计数滑动窗口（见 app/rag/chunking.py）
    rag_chunk_encoding: str = "cl100k_base"
    rag_chunk_max_tokens: int = 512
    rag_chunk_overlap_tokens: int = 64
    # False 时「第X节」不再单独拆块（仍识别 Markdown / 第X章 / Chapter）
    rag_split_on_section_lines: bool = True


settings = Settings()

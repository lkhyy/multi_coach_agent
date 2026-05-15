from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 与启动方式无关，始终读取 backend 目录下的 .env
_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_base_url: str | None = None
    # DeepSeek thinking + 工具多轮时 API 要求回传 reasoning_content；通用 ChatOpenAI 易丢该字段导致 400。
    # 默认对已知的 DeepSeek 网关关闭 thinking；若使用 langchain-deepseek 等完整支持可设为 true。
    deepseek_thinking_enabled: bool = False
    scheduler_model: str = "deepseek-v4-pro"
    worker_model: str = "deepseek-v4-pro"
    # 默认固定在 backend 目录下，避免从 backend/app 启动时写到 app/data；相对路径在下方解析到 backend 下
    data_dir: Path = Field(default_factory=lambda: _BACKEND_DIR / "data")
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
    # 用户级长期记忆：用小模型结构化合并（可与 SCHEDULER_MODEL 不同）
    memory_extract_model: str = "deepseek-v4-pro"
    # 学习内容记忆：要点条数上限
    learning_content_max_key_points: int = 24
    # 新开对话时，与已有学习主题的检索匹配最低分（0~1 量级，越大越严）
    learning_match_min_score: float = 0.12
    # 无 LLM 或 LLM 失败时，用分词重叠决定是否写入学习档案（低于则跳过，避免跑题污染）
    learning_update_heuristic_min_overlap: float = 0.10

    # RAG 切块：先按章节边界切，再在章内按 tiktoken 计数滑动窗口（见 app/rag/chunking.py）
    rag_chunk_encoding: str = "cl100k_base"
    rag_chunk_max_tokens: int = 512
    rag_chunk_overlap_tokens: int = 64
    # False 时「第X节」不再单独拆块（仍识别 Markdown / 第X章 / Chapter）
    rag_split_on_section_lines: bool = True

    # RAG 上传：单文件大小上限（MB），含 PDF/文本；过大易超时或占内存
    rag_upload_max_mb: float = 32.0
    # PDF 为扫描件时：pypdf 提不出字则尝试 Tesseract OCR（需本机安装 tesseract 及语言包）
    rag_pdf_ocr_fallback: bool = True
    rag_pdf_ocr_max_pages: int = 40
    rag_pdf_ocr_lang: str = "chi_sim+eng"
    rag_pdf_ocr_zoom: float = 2.0

    # BGE-M3 嵌入：空则自动探测本机 HuggingFace 缓存；无法访问 Hub 时请设 BGE_M3_MODEL_PATH 为本地快照目录
    bge_m3_model_path: str = ""

    # Web UI：JWT 与账号（JSON 对象，键为登录名、值为明文密码；生产环境请改强密码并配合 HTTPS）
    jwt_secret: str = "dev-change-me"
    jwt_expire_hours: int = 168
    auth_users_json: str = '{"demo":"demo"}'

    @field_validator("data_dir", mode="after")
    @classmethod
    def _resolve_data_dir(cls, v: Path) -> Path:
        if v.is_absolute():
            return v.resolve()
        return (_BACKEND_DIR / v).resolve()

    @field_validator("rag_upload_max_mb", mode="after")
    @classmethod
    def _clamp_rag_upload_mb(cls, v: float) -> float:
        return max(1.0, min(256.0, float(v)))

    @field_validator("auth_users_json", mode="before")
    @classmethod
    def _auth_users_non_empty(cls, v: object) -> str:
        if v is None:
            return '{"demo":"demo"}'
        s = str(v).strip()
        return s if s else '{"demo":"demo"}'


settings = Settings()

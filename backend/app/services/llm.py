from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.config import settings


def _deepseek_extra_body() -> dict[str, object] | None:
    base = (settings.openai_base_url or "").strip().lower()
    if not base or "deepseek.com" not in base:
        return None
    if settings.deepseek_thinking_enabled:
        return None
    return {"thinking": {"type": "disabled"}}


def _chat_openai_kwargs(*, model: str) -> dict:
    kwargs: dict = {
        "model": model,
        "api_key": settings.openai_api_key or "missing-openai-api-key",
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    eb = _deepseek_extra_body()
    if eb is not None:
        kwargs["extra_body"] = eb
    return kwargs


@lru_cache(maxsize=1)
def get_scheduler_llm() -> ChatOpenAI:
    # 允许未配置密钥时完成导入/启动；真实调用前请在 .env 设置 OPENAI_API_KEY
    return ChatOpenAI(**_chat_openai_kwargs(model=settings.scheduler_model))


@lru_cache(maxsize=1)
def get_memory_extractor_llm() -> ChatOpenAI:
    """用于长期记忆结构化抽取与压缩（可与主模型分离配置）。"""
    return ChatOpenAI(**_chat_openai_kwargs(model=settings.memory_extract_model))


@lru_cache(maxsize=1)
def get_worker_llm() -> ChatOpenAI:
    return ChatOpenAI(**_chat_openai_kwargs(model=settings.worker_model))

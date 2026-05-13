from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.config import settings


@lru_cache(maxsize=1)
def get_scheduler_llm() -> ChatOpenAI:
    # 允许未配置密钥时完成导入/启动；真实调用前请在 .env 设置 OPENAI_API_KEY
    kwargs: dict = {
        "model": settings.scheduler_model,
        "api_key": settings.openai_api_key or "missing-openai-api-key",
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


@lru_cache(maxsize=1)
def get_worker_llm() -> ChatOpenAI:
    kwargs: dict = {
        "model": settings.worker_model,
        "api_key": settings.openai_api_key or "missing-openai-api-key",
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)

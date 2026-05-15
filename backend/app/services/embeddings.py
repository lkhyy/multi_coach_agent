from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

_encoder: object | None = None
_encoder_unavailable: bool = False

_DEFAULT_HF_REPO = "BAAI/bge-m3"
_HF_CACHE_REPO_DIR = "models--BAAI--bge-m3"


def _hf_hub_cache_root() -> Path:
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        return Path(hf_home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _is_usable_model_dir(path: Path) -> bool:
    return path.is_dir() and (path / "config.json").exists()


def _resolve_bge_m3_model_path() -> str:
    explicit = (settings.bge_m3_model_path or "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        if _is_usable_model_dir(p):
            return str(p.resolve())
        logger.warning("BGE_M3_MODEL_PATH 无效（缺少 config.json）：%s", p)

    snapshots = _hf_hub_cache_root() / _HF_CACHE_REPO_DIR / "snapshots"
    if snapshots.is_dir():
        candidates = [p for p in snapshots.iterdir() if _is_usable_model_dir(p)]
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            resolved = str(candidates[0].resolve())
            logger.info("BGE-M3 使用本机 HuggingFace 缓存：%s", resolved)
            return resolved

    logger.warning(
        "未找到本机 BGE-M3 缓存，将尝试从 HuggingFace Hub 拉取 %s（需可访问外网）；"
        "离线环境请设置 BGE_M3_MODEL_PATH 指向已下载的模型目录",
        _DEFAULT_HF_REPO,
    )
    return _DEFAULT_HF_REPO


def _bge_m3_load_kwargs(model_path: str) -> dict[str, Any]:
    p = Path(model_path)
    kwargs: dict[str, Any] = {"use_fp16": True}
    if p.is_dir():
        kwargs["local_files_only"] = True
        kwargs["use_safetensors"] = any(p.glob("*.safetensors"))
    else:
        kwargs["use_safetensors"] = True
    return kwargs


def _format_bge_init_error(exc: BaseException) -> str:
    msg = str(exc)
    lowered = msg.lower()
    if "10060" in msg or "timed out" in lowered or "connecttimeout" in lowered or "connection" in lowered:
        return (
            "BGE-M3 模型初始化失败，Chroma 向量部分将跳过（BM25 / SQLite 切块仍正常）。"
            "根因像是访问 HuggingFace Hub 超时（与 PyTorch 版本无关）。"
            "请在 backend/.env 设置 BGE_M3_MODEL_PATH 为本机模型目录（HF 缓存 snapshots 下的路径），"
            "或配置 HF 镜像后重试。详情："
            f"{exc}"
        )
    if "torch.load" in lowered or "cve-2025-32434" in lowered or "weights only" in lowered:
        return (
            "BGE-M3 模型初始化失败，Chroma 向量部分将跳过（BM25 / SQLite 切块仍正常）。"
            "常见原因：transformers 要求 torch>=2.6 才允许 torch.load 旧权重，或需使用 safetensors；"
            "可升级 PyTorch、下载带 .safetensors 的模型，或设置 BGE_M3_MODEL_PATH 指向本地快照。详情："
            f"{exc}"
        )
    return (
        "BGE-M3 模型初始化失败，Chroma 向量部分将跳过（BM25 / SQLite 切块仍正常）。"
        f"详情：{exc}"
    )


class _BgeM3Encoder:
    def __init__(self) -> None:
        from FlagEmbedding import BGEM3FlagModel  # heavy import

        model_path = _resolve_bge_m3_model_path()
        load_kwargs = _bge_m3_load_kwargs(model_path)
        logger.info("正在加载 BGE-M3：%s（%s）", model_path, load_kwargs)
        self._model = BGEM3FlagModel(model_path, **load_kwargs)

    def encode(self, texts: list[str]) -> np.ndarray:
        out: dict[str, Any] = self._model.encode(texts)
        vecs = out["dense_vecs"]
        return np.asarray(vecs, dtype=np.float32)


def get_bge_m3_encoder() -> _BgeM3Encoder | None:
    """返回 BGE-M3 编码器；依赖缺失或初始化失败时返回 None（SQLite BM25 仍可用，Chroma 向量跳过）。"""
    global _encoder, _encoder_unavailable
    if _encoder_unavailable:
        return None
    if _encoder is None:
        try:
            _encoder = _BgeM3Encoder()
        except ModuleNotFoundError as e:
            _encoder_unavailable = True
            logger.warning(
                "BGE-M3 / FlagEmbedding 不可用（%s），Chroma 向量检索与入库将跳过；"
                "需要完整向量 RAG 请安装：pip install FlagEmbedding",
                e,
            )
            return None
        except Exception as e:  # noqa: BLE001
            _encoder_unavailable = True
            logger.warning(_format_bge_init_error(e))
            return None
    return _encoder  # type: ignore[return-value]

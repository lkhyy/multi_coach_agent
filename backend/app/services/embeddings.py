from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

_encoder: object | None = None
_encoder_unavailable: bool = False


class _BgeM3Encoder:
    def __init__(self) -> None:
        from FlagEmbedding import BGEM3FlagModel  # heavy import

        model_name_or_path = settings.bge_m3_model_path.strip() or "BAAI/bge-m3"
        self._model = BGEM3FlagModel(
            model_name_or_path,
            use_fp16=True,
            use_safetensors=True,
        )

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
            logger.warning(
                "BGE-M3 模型初始化失败，Chroma 向量部分将跳过（BM25 / SQLite 切块仍正常）。"
                "常见原因：transformers 要求 torch>=2.6 才允许 torch.load 权重（CVE-2025-32434），"
                "或需使用带 safetensors 的缓存；可升级 PyTorch 或在本机单独建轻量环境跑后端。详情：%s",
                e,
            )
            return None
    return _encoder  # type: ignore[return-value]

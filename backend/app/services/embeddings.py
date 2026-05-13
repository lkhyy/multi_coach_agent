from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

from app.config import settings


class _BgeM3Encoder:
    def __init__(self) -> None:
        from FlagEmbedding import BGEM3FlagModel  # heavy import

        self._model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)

    def encode(self, texts: list[str]) -> np.ndarray:
        out: dict[str, Any] = self._model.encode(texts)
        vecs = out["dense_vecs"]
        return np.asarray(vecs, dtype=np.float32)


@lru_cache(maxsize=1)
def get_bge_m3_encoder() -> _BgeM3Encoder:
    _ = settings  # reserved for future cache dir / device flags
    return _BgeM3Encoder()

from __future__ import annotations

import time
from typing import Any

import jwt

if not hasattr(jwt, "encode"):
    raise ImportError(
        "当前环境中的 `jwt` 不是 PyJWT（例如误装了 PyPI 上的 `jwt` 包）。"
        "请执行：pip uninstall jwt -y && pip install PyJWT"
    )

from app.config import settings


def create_access_token(*, user_id: str) -> str:
    now = int(time.time())
    exp = now + int(settings.jwt_expire_hours) * 3600
    payload: dict[str, Any] = {"sub": user_id, "iat": now, "exp": exp}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token_subject(token: str) -> str | None:
    if not token or not token.strip():
        return None
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        sub = data.get("sub")
        if isinstance(sub, str) and sub:
            return sub
    except jwt.PyJWTError:
        return None
    return None

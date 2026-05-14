from __future__ import annotations

import json
import logging

from app.config import settings
from app.users.paths import _sanitize_user_id

logger = logging.getLogger(__name__)


def _load_password_map() -> dict[str, str]:
    raw: dict[str, object]
    try:
        parsed = json.loads(settings.auth_users_json or "{}")
        raw = parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        logger.warning("AUTH_USERS_JSON 解析失败，将视为无账号")
        raw = {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        uid = _sanitize_user_id(str(k))
        if uid and uid != "anonymous":
            out[uid] = str(v)
    return out


def verify_credentials(username: str, password: str) -> str | None:
    uid = _sanitize_user_id(username)
    if not uid or uid == "anonymous":
        return None
    m = _load_password_map()
    if password == m.get(uid):
        return uid
    return None

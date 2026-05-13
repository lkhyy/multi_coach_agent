from __future__ import annotations

import asyncio
from collections import defaultdict

_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _lock(user_id: str) -> asyncio.Lock:
    return _locks[user_id]


async def acquire_user_scheduler(user_id: str, *, blocking: bool) -> bool:
    """同一 user_id 下，WebSocket 会话与空闲队列消费者互斥。"""
    lock = _lock(user_id)
    if blocking:
        await lock.acquire()
        return True
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.0)
    except TimeoutError:
        return False
    return True


def release_user_scheduler(user_id: str) -> None:
    _lock(user_id).release()

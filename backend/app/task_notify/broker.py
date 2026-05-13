from __future__ import annotations

import asyncio
from collections import defaultdict


class NotificationBroker:
    """Per-user asyncio queues for non-blocking worker XML notifications."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[str]] = defaultdict(asyncio.Queue)

    async def push(self, user_id: str, xml_payload: str) -> None:
        await self._queues[user_id].put(xml_payload)

    def has_pending(self, user_id: str) -> bool:
        return not self._queues[user_id].empty()

    def iter_users_with_pending(self) -> list[str]:
        return [uid for uid, q in self._queues.items() if not q.empty()]

    async def drain_all(self, user_id: str) -> list[str]:
        q = self._queues[user_id]
        out: list[str] = []
        while True:
            try:
                out.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out


notification_broker = NotificationBroker()

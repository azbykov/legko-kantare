"""Кэш списка своих кампаний в памяти процесса.

Без него каждое нажатие клавиши в поиске обходило бы все кампании трекера. TTL короткий
(CAMPAIGNS_CACHE_TTL); после создания кампании кэш сбрасывается, `refresh=true` — принудительно.
"""

import threading
import time
from collections.abc import Awaitable, Callable

from app.keitaro.schemas import Campaign


class CampaignsCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._items: list[Campaign] | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    async def get(self, load: Callable[[], Awaitable[list[Campaign]]], refresh: bool = False) -> list[Campaign]:
        with self._lock:
            if not refresh and self._items is not None and time.monotonic() - self._at < self.ttl:
                return list(self._items)
        items = await load()  # вне блокировки: запрос в Keitaro не держит остальных
        with self._lock:
            self._items, self._at = items, time.monotonic()
        return list(items)

    def invalidate(self) -> None:
        with self._lock:
            self._items = None

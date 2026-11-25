"""Кэш справочников Keitaro в памяти процесса: домены, группы кампаний, источники.

Списки маленькие и почти не меняются, а без кэша каждое создание кампании
ходит в трекер три раза. Один запрос обновляет кэш на всех (asyncio.Lock).
TTL — REFERENCE_CACHE_TTL.
"""

import time
from asyncio import Lock
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.keitaro.schemas import Domain, Group, TrafficSource


@dataclass(frozen=True)
class References:
    domains: list[Domain]
    groups: list[Group]
    sources: list[TrafficSource]


class ReferenceCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._value: References | None = None
        self._at = 0.0
        self._lock = Lock()

    def _fresh(self) -> bool:
        return self._value is not None and time.monotonic() - self._at < self.ttl

    def _copy(self) -> References:
        assert self._value is not None
        return References(
            domains=list(self._value.domains),
            groups=list(self._value.groups),
            sources=list(self._value.sources),
        )

    async def get(self, load: Callable[[], Awaitable[References]], *, refresh: bool = False) -> tuple[References, bool]:
        """Справочники и флаг «взято из кэша, а не только что загружено»."""
        async with self._lock:
            hit = not refresh and self._fresh()
            if not hit:
                loaded = await load()
                self._value = References(
                    domains=list(loaded.domains), groups=list(loaded.groups), sources=list(loaded.sources)
                )
                self._at = time.monotonic()
            return self._copy(), hit

"""Тонкий async-клиент Admin API Keitaro: логирует каждый вызов, проверяет формат ответов."""

import logging
import time
from functools import lru_cache
from typing import Any, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from app.config import Settings
from app.keitaro.errors import KeitaroError
from app.keitaro.schemas import (
    Campaign,
    CampaignCreatePayload,
    Domain,
    Group,
    Offer,
    Stream,
    StreamCreatePayload,
    StreamOffer,
    TrafficSource,
)

log = logging.getLogger("app.keitaro")
T = TypeVar("T")
_BODY_LIMIT = 500
TIMEOUT = 20  # секунды на запрос к Admin API


@lru_cache
def _adapter(tp: Any) -> TypeAdapter:
    return TypeAdapter(tp)


def build_http(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """HTTP-клиент к Admin API: адрес, ключ и таймаут из настроек. Закрывает тот, кто создал (create_app)."""
    return httpx.AsyncClient(
        base_url=settings.keitaro_url.rstrip("/"),
        headers={"Api-Key": settings.keitaro_api_key.get_secret_value()},
        timeout=TIMEOUT,
        transport=transport,
    )


class KeitaroClient:
    """Только HTTP: шлёт запросы, логирует, разбирает JSON в модели. Про настройки не знает."""

    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def _request(self, method: str, path: str, tp: Any = None, **kw) -> Any:
        started = time.perf_counter()
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            ms = (time.perf_counter() - started) * 1000
            log.error("Keitaro %s %s -> network error (%.0f ms): %s", method, path, ms, e)
            raise KeitaroError(502, f"нет связи с Keitaro: {e}") from e
        ms = (time.perf_counter() - started) * 1000
        if r.status_code >= 400:
            log.warning("Keitaro %s %s -> %s (%.0f ms): %s", method, path, r.status_code, ms, r.text[:_BODY_LIMIT])
            try:
                detail = r.json()
            except ValueError:
                detail = r.text
            raise KeitaroError(r.status_code, detail)
        log.info("Keitaro %s %s -> %s (%.0f ms)", method, path, r.status_code, ms)
        if not r.content:
            return None
        try:
            data = r.json()
        except ValueError:
            log.error("Keitaro %s %s: ответ не JSON: %s", method, path, r.text[:_BODY_LIMIT])
            raise KeitaroError(502, f"Keitaro вернул не JSON на {method} {path}") from None
        if tp is None:
            return data
        try:
            return _adapter(tp).validate_python(data)
        except ValidationError as e:
            log.error("Keitaro %s %s: неожиданный формат ответа: %s; тело: %s", method, path, e, r.text[:_BODY_LIMIT])
            raise KeitaroError(502, f"неожиданный формат ответа Keitaro на {method} {path}") from None

    # справочники
    async def offers(self) -> list[Offer]:
        return await self._request("GET", "/offers", list[Offer])

    async def offer(self, offer_id: int) -> Offer:
        return await self._request("GET", f"/offers/{offer_id}", Offer)

    async def domains(self) -> list[Domain]:
        return await self._request("GET", "/domains", list[Domain])

    async def groups(self, type_: str = "campaigns") -> list[Group]:
        return await self._request("GET", "/groups", list[Group], params={"type": type_})

    async def traffic_sources(self) -> list[TrafficSource]:
        return await self._request("GET", "/traffic_sources", list[TrafficSource])

    # кампании
    async def campaigns(self, offset: int = 0, limit: int = 100) -> list[Campaign]:
        return await self._request("GET", "/campaigns", list[Campaign], params={"offset": offset, "limit": limit})

    async def campaign(self, campaign_id: int) -> Campaign:
        return await self._request("GET", f"/campaigns/{campaign_id}", Campaign)

    async def create_campaign(self, payload: CampaignCreatePayload) -> Campaign:
        return await self._request("POST", "/campaigns", Campaign, json=payload.to_json())

    async def delete_campaign(self, campaign_id: int) -> None:
        await self._request("DELETE", f"/campaigns/{campaign_id}")

    # потоки
    async def streams(self, campaign_id: int) -> list[Stream]:
        return await self._request("GET", f"/campaigns/{campaign_id}/streams", list[Stream])

    async def create_stream(self, payload: StreamCreatePayload) -> Stream:
        return await self._request("POST", "/streams", Stream, json=payload.to_json())

    async def update_stream_offers(self, stream_id: int, offers: list[StreamOffer]) -> Stream:
        return await self._request(
            "PUT", f"/streams/{stream_id}", Stream, json={"offers": [o.payload() for o in offers]}
        )

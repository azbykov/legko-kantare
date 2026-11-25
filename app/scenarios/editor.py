"""Сценарий «редактор кампании»: просмотр, добавление и удаление оффера с проверкой версии потока."""

import hashlib
import json
from typing import Any

from sqlalchemy.orm import sessionmaker

from app import audit
from app.config import Settings
from app.errors import (
    LastOfferInStream,
    OfferAlreadyInStream,
    OfferNotInStream,
    StaleStream,
    StreamNotEditable,
    StreamNotInCampaign,
)
from app.keitaro import KeitaroClient
from app.keitaro.schemas import Stream, StreamOffer
from app.offers_cache import OffersCatalog
from app.scenarios.common import own_campaign, reason
from app.shares import add_share, rebalance


def snapshot(offers: list[StreamOffer]) -> list[dict]:
    """Состав офферов для аудита и версии: без служебных полей Keitaro."""
    return [o.payload() for o in offers]


def stream_version(stream: Stream) -> str:
    """Версия потока: offers (без служебных полей) + updated_at, если Keitaro его отдаёт.

    Не полагаемся только на updated_at — неизвестно, обновляет ли его каждая версия Keitaro.
    """
    raw = json.dumps({"offers": snapshot(stream.offers), "updated_at": stream.updated_at}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class CampaignEditor:
    def __init__(self, keitaro: KeitaroClient, sessions: sessionmaker, settings: Settings, offers: OffersCatalog):
        self.kt = keitaro
        self.sessions = sessions  # для аудита — своя сессия
        self.settings = settings
        self.offers = offers

    async def view(self, campaign_id: int) -> dict:
        """Кампания с потоками; в каждом потоке офферы с названиями и версия потока."""
        campaign = await own_campaign(self.kt, self.settings, campaign_id)
        names = {o.id: o.name for o in await self.offers.list()}
        streams = []
        for s in await self.kt.streams(campaign_id):
            offers = [
                {
                    "offer_id": o.offer_id,
                    "name": names.get(o.offer_id, f"#{o.offer_id}"),
                    "share": o.share,
                    "state": o.state,
                }
                for o in s.offers
            ]
            streams.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "type": s.type,
                    "schema": s.schema_,
                    "editable": s.schema_ == "landings",
                    "version": stream_version(s),
                    "offers": offers,
                    "share_total": sum(o["share"] for o in offers if o["state"] == "active"),
                }
            )
        return {"id": campaign.id, "name": campaign.name, "state": campaign.state, "streams": streams}

    async def add_offer(self, campaign_id: int, stream_id: int, offer_id: int, version: str, actor: str) -> dict:
        async def run(state: dict) -> Stream:
            stream = await self._editable_stream(campaign_id, stream_id, version)
            state["before"] = snapshot(stream.offers)
            await self.kt.offer(offer_id)
            entries = [o.model_copy() for o in stream.offers]
            if any(e.offer_id == offer_id for e in entries):
                raise OfferAlreadyInStream("Оффер уже есть в потоке")
            active = [e for e in entries if e.state == "active"]
            shares = add_share([e.share for e in active])
            for e, sh in zip(active, shares, strict=False):
                e.share = sh
            entries.append(StreamOffer(offer_id=offer_id, share=shares[-1], state="active"))
            return await self.kt.update_stream_offers(stream_id, entries)

        updated = await self._audited(actor, "offer.add", campaign_id, stream_id, offer_id, run)
        return {"ok": True, "version": stream_version(updated)}

    async def remove_offer(self, campaign_id: int, stream_id: int, offer_id: int, version: str, actor: str) -> dict:
        async def run(state: dict) -> Stream:
            stream = await self._editable_stream(campaign_id, stream_id, version)
            state["before"] = snapshot(stream.offers)
            before = [o.model_copy() for o in stream.offers]
            entries = [e for e in before if e.offer_id != offer_id]
            if len(entries) == len(before):
                raise OfferNotInStream("Оффера нет в потоке")
            active = [e for e in entries if e.state == "active"]
            if any(e.state == "active" for e in before) and not active:
                raise LastOfferInStream(
                    "Нельзя удалить последний активный оффер — поток останется пустым. Сначала добавьте другой оффер"
                )
            for e, sh in zip(active, rebalance([e.share for e in active]), strict=True):
                e.share = sh
            return await self.kt.update_stream_offers(stream_id, entries)

        updated = await self._audited(actor, "offer.remove", campaign_id, stream_id, offer_id, run)
        return {"ok": True, "version": stream_version(updated)}

    # ---- шаги

    async def _editable_stream(self, campaign_id: int, stream_id: int, version: str | None = None) -> Stream:
        """Поток из списка потоков нашей кампании, со schema=landings — иначе PUT offers не делаем.

        Если передана версия с экрана, а поток с тех пор изменился (в админке, другой вкладке) — 409,
        чтобы не затереть чужую правку.
        """
        await own_campaign(self.kt, self.settings, campaign_id)
        stream = next((s for s in await self.kt.streams(campaign_id) if s.id == stream_id), None)
        if stream is None:
            raise StreamNotInCampaign(f"Поток #{stream_id} не принадлежит кампании #{campaign_id}")
        if stream.schema_ != "landings":
            raise StreamNotEditable("В этом потоке нет офферов (редирект)")
        if version is not None and version != stream_version(stream):
            raise StaleStream("Поток уже изменили (в админке Keitaro или в другой вкладке) — обновите страницу")
        return stream

    async def _audited(self, actor, action, campaign_id, stream_id, offer_id, fn) -> Stream:
        state: dict[str, Any] = {}
        where = {"campaign_id": campaign_id, "stream_id": stream_id, "offer_id": offer_id}
        try:
            updated = await fn(state)
        except Exception as exc:
            audit.write(self.sessions, actor, action, **where, before=state.get("before"), error=reason(exc))
            raise
        audit.write(self.sessions, actor, action, **where, before=state["before"], after=snapshot(updated.offers))
        return updated

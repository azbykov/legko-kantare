"""Кэш офферов в своей таблице. Читает и обновляет его только своя сессия,
поэтому commit кэша не затрагивает незавершённую правку в основной сессии запроса."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.keitaro import KeitaroClient
from app.keitaro.schemas import Offer
from app.models import OfferCache

log = logging.getLogger(__name__)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite отдаёт naive


class OffersCatalog:
    """Справочник офферов через кэш. Пишет своей сессией из sessionmaker, а не сессией запроса."""

    def __init__(self, keitaro: KeitaroClient, sessions: sessionmaker, ttl: int):
        self.kt = keitaro
        self.sessions = sessions
        self.ttl = ttl

    async def list(self, refresh: bool = False) -> list[Offer]:
        sm, ttl = self.sessions, self.ttl
        if not refresh:
            try:
                with sm() as s:
                    rows = s.scalars(select(OfferCache).order_by(OfferCache.id)).all()
                    if rows and min(_aware(r.fetched_at) for r in rows) > datetime.now(UTC) - timedelta(seconds=ttl):
                        return [Offer(id=r.id, name=r.name, country=r.country or []) for r in rows]
            except Exception:
                log.exception("Кэш офферов недоступен, иду в Keitaro")
        offers = await self.kt.offers()
        try:
            with sm() as s:
                now = datetime.now(UTC)
                s.execute(delete(OfferCache))
                s.add_all(OfferCache(id=o.id, name=o.name, country=o.country, fetched_at=now) for o in offers)
                s.commit()
        except Exception:
            log.exception("Не удалось обновить кэш офферов")
        return offers

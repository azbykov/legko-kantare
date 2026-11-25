from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class CampaignRecord(Base):
    """Журнал кампаний, созданных через сервис."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    countries: Mapped[list] = mapped_column(JSON)  # ISO-коды гео-фильтра, например ["US", "DE"]
    offer_id: Mapped[int] = mapped_column(Integer)
    # created | rollback_failed (в Keitaro осталась недосозданная кампания — удалить вручную)
    status: Mapped[str] = mapped_column(String(20), default="created", server_default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    """Кто, что, над чем сделал; снимок до/после и текст ошибки, если действие не удалось."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(32), index=True)  # campaign.create | offer.add | offer.remove
    campaign_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    stream_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    before: Mapped[Any] = mapped_column(JSON, nullable=True)
    after: Mapped[Any] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OfferCache(Base):
    """Кэш справочника офферов из Keitaro (TTL — OFFERS_CACHE_TTL)."""

    __tablename__ = "offers_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255))
    country: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

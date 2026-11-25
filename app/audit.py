"""Аудит пишется отдельной сессией: не зависит от основной транзакции и не роняет запрос."""

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog

log = logging.getLogger(__name__)


def get_actor(request: Request) -> str:
    user = (request.headers.get("X-User") or "").strip()
    if user:
        return user[:255]
    host = request.client.host if request.client else "unknown"
    return f"anonymous@{host}"


def write(
    sm: sessionmaker,
    actor: str,
    action: str,
    *,
    campaign_id: int | None = None,
    stream_id: int | None = None,
    offer_id: int | None = None,
    before: Any = None,
    after: Any = None,
    error: str | None = None,
) -> None:
    try:
        with sm() as s:
            s.add(
                AuditLog(
                    actor=actor,
                    action=action,
                    campaign_id=campaign_id,
                    stream_id=stream_id,
                    offer_id=offer_id,
                    before=before,
                    after=after,
                    error=error,
                )
            )
            s.commit()
    except Exception:
        log.exception("Не удалось записать аудит %s (campaign=%s, error=%s)", action, campaign_id, error)

"""Зависимости FastAPI. Всё берётся из app.state — его заполняет create_app(), глобальных синглтонов нет.

Сценарии собираются здесь; роутер получает готовый объект через алиас (`CreatorDep` и т. п.)
и не перечисляет клиент, сессии, настройки и кэши на каждом эндпоинте.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.audit import get_actor
from app.campaigns_cache import CampaignsCache
from app.config import Settings
from app.keitaro import KeitaroClient
from app.offers_cache import OffersCatalog
from app.references_cache import ReferenceCache
from app.scenarios import CampaignCatalog, CampaignCreator, CampaignEditor

# ---- ресурсы приложения


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_keitaro(request: Request) -> KeitaroClient:
    return request.app.state.keitaro


def get_sessionmaker(request: Request) -> sessionmaker:
    """Фабрика сессий. Аудит и кэш офферов пишут через свои, отдельные сессии."""
    return request.app.state.sessionmaker


def get_campaigns_cache(request: Request) -> CampaignsCache:
    return request.app.state.campaigns_cache


def get_references_cache(request: Request) -> ReferenceCache:
    return request.app.state.references_cache


async def get_db(sm: sessionmaker = Depends(get_sessionmaker)) -> AsyncIterator[Session]:
    """Основная сессия запроса (журнал созданных кампаний).

    Async-зависимость, чтобы сессия открывалась в том же потоке, где идёт запрос:
    синхронный генератор FastAPI выполняет в пуле потоков, и SQLite тогда откажется
    от соединения.
    """
    with sm() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
KeitaroDep = Annotated[KeitaroClient, Depends(get_keitaro)]
SessionsDep = Annotated[sessionmaker, Depends(get_sessionmaker)]
DbDep = Annotated[Session, Depends(get_db)]
CampaignsCacheDep = Annotated[CampaignsCache, Depends(get_campaigns_cache)]
ReferencesDep = Annotated[ReferenceCache, Depends(get_references_cache)]
ActorDep = Annotated[str, Depends(get_actor)]

# ---- сценарии


def get_offers_catalog(kt: KeitaroDep, sessions: SessionsDep, settings: SettingsDep) -> OffersCatalog:
    return OffersCatalog(kt, sessions, settings.offers_cache_ttl)


OffersDep = Annotated[OffersCatalog, Depends(get_offers_catalog)]


def get_catalog(kt: KeitaroDep, settings: SettingsDep, cache: CampaignsCacheDep) -> CampaignCatalog:
    return CampaignCatalog(kt, settings, cache)


def get_creator(
    kt: KeitaroDep,
    db: DbDep,
    sessions: SessionsDep,
    settings: SettingsDep,
    cache: CampaignsCacheDep,
    references: ReferencesDep,
) -> CampaignCreator:
    return CampaignCreator(kt, db, sessions, settings, cache, references)


def get_editor(kt: KeitaroDep, sessions: SessionsDep, settings: SettingsDep, offers: OffersDep) -> CampaignEditor:
    return CampaignEditor(kt, sessions, settings, offers)


CatalogDep = Annotated[CampaignCatalog, Depends(get_catalog)]
CreatorDep = Annotated[CampaignCreator, Depends(get_creator)]
EditorDep = Annotated[CampaignEditor, Depends(get_editor)]

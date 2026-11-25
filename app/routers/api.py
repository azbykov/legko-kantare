from fastapi import APIRouter, Query
from sqlalchemy import select

from app.deps import ActorDep, CatalogDep, CreatorDep, DbDep, EditorDep, OffersDep
from app.models import AuditLog
from app.schemas import CampaignCreate, OfferRef

router = APIRouter(prefix="/api")


@router.get("/offers")
async def list_offers(offers: OffersDep, refresh: bool = False):
    return [o.model_dump() for o in await offers.list(refresh)]


@router.get("/campaigns")
async def list_campaigns(
    catalog: CatalogDep,
    q: str = Query("", max_length=200, description="id кампании или часть имени"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    refresh: bool = False,
):
    """Только кампании с префиксом CAMPAIGN_PREFIX. Список кэшируется на CAMPAIGNS_CACHE_TTL секунд."""
    return await catalog.search(q, page, per_page, refresh)


@router.post("/campaigns", status_code=201)
async def create_campaign(body: CampaignCreate, creator: CreatorDep, actor: ActorDep):
    return await creator.create(body.name, body.countries, body.offer_id, actor)


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int, editor: EditorDep):
    """Кампания с потоками; в каждом потоке офферы с названиями."""
    return await editor.view(campaign_id)


@router.post("/campaigns/{campaign_id}/streams/{stream_id}/offers")
async def add_offer(campaign_id: int, stream_id: int, body: OfferRef, editor: EditorDep, actor: ActorDep):
    return await editor.add_offer(campaign_id, stream_id, body.offer_id, body.version, actor)


@router.delete("/campaigns/{campaign_id}/streams/{stream_id}/offers/{offer_id}")
async def remove_offer(
    campaign_id: int,
    stream_id: int,
    offer_id: int,
    editor: EditorDep,
    actor: ActorDep,
    version: str = Query(min_length=1, max_length=64, description="версия потока с экрана (из GET кампании)"),
):
    return await editor.remove_offer(campaign_id, stream_id, offer_id, version, actor)


@router.get("/audit")
async def list_audit(db: DbDep, campaign_id: int | None = None, limit: int = Query(100, ge=1, le=500)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if campaign_id is not None:
        stmt = stmt.where(AuditLog.campaign_id == campaign_id)
    return [
        {
            "id": a.id,
            "actor": a.actor,
            "action": a.action,
            "campaign_id": a.campaign_id,
            "stream_id": a.stream_id,
            "offer_id": a.offer_id,
            "before": a.before,
            "after": a.after,
            "error": a.error,
            "created_at": a.created_at.isoformat(),
        }
        for a in db.scalars(stmt)
    ]

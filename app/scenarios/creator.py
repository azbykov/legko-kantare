"""Сценарий «создать кампанию»: проверки, создание, откат, сверка сохранённого."""

import logging

from sqlalchemy.orm import Session, sessionmaker

from app import audit
from app.campaigns_cache import CampaignsCache
from app.config import Settings
from app.errors import BindingNotFound, CampaignNotCreated, InvalidName, RollbackFailed, ServiceError
from app.keitaro import KeitaroClient, KeitaroError
from app.keitaro.schemas import (
    Campaign,
    CampaignCreatePayload,
    Domain,
    Stream,
    StreamCreatePayload,
    StreamFilterPayload,
    StreamOffer,
)
from app.models import CampaignRecord
from app.references_cache import ReferenceCache, References
from app.scenarios.common import reason

log = logging.getLogger(__name__)

GOOGLE_URL = "https://google.com"


def _stream_payloads(cid: int, countries: list[str], offer_id: int) -> list[StreamCreatePayload]:
    return [
        StreamCreatePayload(
            campaign_id=cid,
            name=f"Geo {','.join(countries)} -> Google",
            type="regular",
            position=1,
            schema="redirect",
            action_type="http",
            action_payload=GOOGLE_URL,
            filters=[StreamFilterPayload(name="country", mode="accept", payload=countries)],
        ),
        StreamCreatePayload(
            campaign_id=cid,
            name="Офферы",
            type="default",
            position=2,
            schema="landings",
            offers=[StreamOffer(offer_id=offer_id, share=100, state="active")],
        ),
    ]


def _norm_filters(filters: list[dict]) -> list[dict]:
    return [{k: f.get(k) for k in ("name", "mode", "payload")} for f in filters]


def _norm_offers(offers: list) -> list[dict]:
    return [
        {k: (o[k] if isinstance(o, dict) else getattr(o, k)) for k in ("offer_id", "share", "state")} for o in offers
    ]


def _saved_stream_view(s: Stream) -> dict:
    return s.model_dump(by_alias=True) | {"filters": _norm_filters(s.filters), "offers": _norm_offers(s.offers)}


async def verify_saved(
    kt: KeitaroClient, cid: int, campaign_payload: CampaignCreatePayload, stream_payloads: list[StreamCreatePayload]
) -> tuple[Campaign, list]:
    """Перечитывает кампанию и потоки и сравнивает с отправленным. Возвращает (кампания, расхождения)."""
    sent_campaign = campaign_payload.to_json()
    sent_streams = [p.to_json() for p in stream_payloads]
    mismatches: list[dict] = []
    saved = await kt.campaign(cid)
    saved_view = saved.model_dump()
    for field, value in sent_campaign.items():
        if saved_view.get(field) != value:
            mismatches.append({"entity": "campaign", "field": field, "sent": value, "saved": saved_view.get(field)})
    by_pos = {s.position: s for s in await kt.streams(cid)}
    for sent in sent_streams:
        entity = f"stream #{sent['position']} «{sent['name']}»"
        stream = by_pos.get(sent["position"])
        if stream is None:
            mismatches.append({"entity": entity, "field": "*", "sent": "создан", "saved": "не найден"})
            continue
        view = _saved_stream_view(stream)
        for field, value in sent.items():
            if field == "filters":
                value = _norm_filters(value)
            elif field == "offers":
                value = _norm_offers(value)
            if view.get(field) != value:
                mismatches.append({"entity": entity, "field": field, "sent": value, "saved": view.get(field)})
    return saved, mismatches


class CampaignCreator:
    def __init__(
        self,
        keitaro: KeitaroClient,
        db: Session,
        sessions: sessionmaker,
        settings: Settings,
        cache: CampaignsCache,
        references: ReferenceCache,
    ):
        self.kt = keitaro
        self.db = db  # основная сессия запроса: журнал кампаний
        self.sessions = sessions  # для аудита — своя сессия
        self.settings = settings
        self.cache = cache
        self.references = references

    async def create(self, name: str, countries: list[str], offer_id: int, actor: str) -> dict:
        countries = [c.upper() for c in countries]
        request = {"name": name, "countries": countries, "offer_id": offer_id}
        try:
            result = await self._create(name, countries, offer_id)
        except Exception as exc:
            cid = getattr(exc, "campaign_id", None)
            audit.write(self.sessions, actor, "campaign.create", campaign_id=cid, before=request, error=reason(exc))
            raise
        self.cache.invalidate()  # новая кампания должна сразу появиться в поиске
        audit.write(self.sessions, actor, "campaign.create", campaign_id=result["id"], before=request, after=result)
        return result

    # ---- шаги

    def full_name(self, name: str) -> str:
        prefix = self.settings.campaign_prefix
        name = name.strip()
        full = name if name.startswith(prefix) else prefix + name
        if full == prefix:
            raise InvalidName("Название пустое — укажите что-то кроме префикса")
        return full

    async def _load_references(self) -> References:
        return References(
            domains=await self.kt.domains(),
            groups=await self.kt.groups(),
            sources=await self.kt.traffic_sources(),
        )

    def _match_bindings(self, refs: References) -> tuple[Domain | None, list[str]]:
        """Домен из кэша и список привязок, которых в нём нет."""
        s = self.settings
        domains = {d.id: d for d in refs.domains}
        known = {
            "домен": ("KEITARO_DOMAIN_ID", s.keitaro_domain_id, domains),
            "группа кампаний": ("KEITARO_GROUP_ID", s.keitaro_group_id, {g.id for g in refs.groups}),
            "источник трафика": (
                "KEITARO_TRAFFIC_SOURCE_ID",
                s.keitaro_traffic_source_id,
                {t.id for t in refs.sources},
            ),
        }
        missing = [f"{what} #{value} ({env})" for what, (env, value, ids) in known.items() if value not in ids]
        return domains.get(s.keitaro_domain_id), missing

    async def check_bindings(self) -> Domain:
        """Домен, группа и источник из конфига должны существовать — до POST /campaigns. Возвращает домен.

        Списки берутся из кэша. Если привязки в кэше нет, списки перечитываются один раз:
        её могли только что добавить в Keitaro.
        """
        refs, hit = await self.references.get(self._load_references)
        domain, missing = self._match_bindings(refs)
        if missing and hit:
            refs, _ = await self.references.get(self._load_references, refresh=True)
            domain, missing = self._match_bindings(refs)
        if missing or domain is None:
            raise BindingNotFound("В Keitaro не найдены: " + ", ".join(missing) + ". Проверьте .env")
        return domain

    def _campaign_payload(self, name: str) -> CampaignCreatePayload:
        return CampaignCreatePayload(
            name=name,
            domain_id=self.settings.keitaro_domain_id,
            group_id=self.settings.keitaro_group_id,
            traffic_source_id=self.settings.keitaro_traffic_source_id,
        )

    def _links(self, campaign: Campaign, domain: Domain) -> dict:
        public = None
        if campaign.alias and domain.name:
            scheme = "http" if domain.is_ssl is False else "https"
            public = f"{scheme}://{domain.name}/{campaign.alias}"
        return {"admin_url": f"{self.settings.admin_url}#!/campaigns/{campaign.id}", "public_url": public}

    async def _create(self, name: str, countries: list[str], offer_id: int) -> dict:
        kt, db = self.kt, self.db
        name = self.full_name(name)
        await kt.offer(offer_id)  # 404, если оффера нет — не создаём пустую кампанию
        domain = await self.check_bindings()
        sent_campaign = self._campaign_payload(name)
        campaign = await kt.create_campaign(sent_campaign)
        cid = campaign.id
        sent_streams = _stream_payloads(cid, countries, offer_id)
        record = {"keitaro_id": cid, "name": campaign.name, "countries": countries, "offer_id": offer_id}
        try:
            for payload in sent_streams:
                await kt.create_stream(payload)
            db.add(CampaignRecord(**record, status="created"))
            db.commit()
        except Exception as exc:
            db.rollback()
            await self._rollback(record, exc)

        # Кампания создана и записана. Сверка — информационная: её сбой не отменяет создание.
        try:
            campaign, mismatches = await verify_saved(kt, cid, sent_campaign, sent_streams)
            verify_error = None
        except Exception as exc:
            log.warning("Не удалось сверить сохранённую кампанию %s: %s", cid, reason(exc))
            mismatches, verify_error = [], reason(exc)
        return {
            "id": cid,
            "name": campaign.name,
            **self._links(campaign, domain),
            "mismatches": mismatches,
            "verify_error": verify_error,
        }

    async def _rollback(self, record: dict, exc: Exception) -> None:
        """Удаляет недосозданную кампанию и всегда бросает ошибку, честно описывающую итог."""
        kt, db = self.kt, self.db
        cid, why = record["keitaro_id"], reason(exc)
        try:
            await kt.delete_campaign(cid)
        except Exception as del_exc:
            log.exception("Откат кампании %s не удался (исходная ошибка: %s)", cid, why)
            try:
                db.add(CampaignRecord(**record, status="rollback_failed"))
                db.commit()
            except Exception:
                db.rollback()
                log.exception("Не удалось записать rollback_failed для кампании %s", cid)
            err = RollbackFailed(
                f"Кампания не создана: {why}. Удалить её из Keitaro не удалось ({reason(del_exc)}) — "
                f"в Keitaro осталась недосозданная кампания #{cid} «{record['name']}», удалите её вручную"
            )
            err.campaign_id = cid
            raise err from exc
        log.warning("Кампания %s откатана: %s", cid, why)
        err = CampaignNotCreated(f"Кампания не создана: {why}. Кампания в Keitaro удалена, ничего не осталось")
        err.campaign_id = cid
        if isinstance(exc, KeitaroError | ServiceError):
            err.status = exc.status if isinstance(exc, ServiceError) else 502
        raise err from exc

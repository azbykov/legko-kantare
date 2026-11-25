"""Сценарий «список своих кампаний»: поиск по id и имени, страницы, кэш."""

from app.campaigns_cache import CampaignsCache
from app.config import Settings
from app.errors import ForeignCampaign
from app.keitaro import KeitaroClient, KeitaroError
from app.keitaro.schemas import Campaign
from app.scenarios.common import is_own, own_campaign

PAGE = 100


class CampaignCatalog:
    def __init__(self, keitaro: KeitaroClient, settings: Settings, cache: CampaignsCache):
        self.kt = keitaro
        self.settings = settings
        self.cache = cache

    async def own_campaigns(self) -> list[Campaign]:
        """Все кампании с нашим префиксом (стенд общий — чужие не показываем)."""
        out, offset = [], 0
        while True:
            page = await self.kt.campaigns(offset=offset, limit=PAGE)
            out += [c for c in page if is_own(self.settings, c)]
            if len(page) < PAGE:
                return out
            offset += PAGE

    async def search(self, q: str, page: int, per_page: int, refresh: bool = False) -> dict:
        """Поиск по id (точно) и по имени (подстрока, без регистра) среди своих кампаний + страницы.

        Список берётся из кэша. Точный id, которого в кэше нет (кампанию только что создали в админке),
        проверяется в Keitaro напрямую — одним запросом.
        """
        items = await self.cache.get(self.own_campaigns, refresh)
        q = q.strip()
        if q:
            needle = q.lower()
            items = [c for c in items if (q.isdigit() and c.id == int(q)) or needle in c.name.lower()]
            if q.isdigit() and not any(c.id == int(q) for c in items):
                try:
                    items.append(await own_campaign(self.kt, self.settings, int(q)))
                except (ForeignCampaign, KeitaroError) as e:
                    if isinstance(e, KeitaroError) and e.status != 404:
                        raise
        items.sort(key=lambda c: c.id, reverse=True)
        start = (page - 1) * per_page
        return {
            "items": [{"id": c.id, "name": c.name, "state": c.state} for c in items[start : start + per_page]],
            "total": len(items),
            "page": page,
            "per_page": per_page,
        }

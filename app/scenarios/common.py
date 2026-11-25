"""Общее для сценариев: текст ошибки для ответа/аудита и проверка «кампания наша»."""

from app.config import Settings
from app.errors import ForeignCampaign, ServiceError
from app.keitaro import KeitaroClient, KeitaroError
from app.keitaro.schemas import Campaign


def reason(exc: BaseException) -> str:
    if isinstance(exc, KeitaroError):
        return f"Keitaro {exc.status}: {exc.detail}"
    if isinstance(exc, ServiceError):
        return exc.message
    return f"{type(exc).__name__}: {exc}"


def is_own(settings: Settings, campaign: Campaign) -> bool:
    return campaign.name.startswith(settings.campaign_prefix)


async def own_campaign(kt: KeitaroClient, settings: Settings, campaign_id: int) -> Campaign:
    campaign = await kt.campaign(campaign_id)
    if not is_own(settings, campaign):
        raise ForeignCampaign("Кампания создана не через этот сервис — редактировать нельзя")
    return campaign

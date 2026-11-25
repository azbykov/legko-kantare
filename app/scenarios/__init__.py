"""Сценарии: каждый — класс, зависимости в конструкторе, методы принимают только данные операции.
Собираются в app/deps.py."""

from app.scenarios.catalog import CampaignCatalog
from app.scenarios.creator import CampaignCreator
from app.scenarios.editor import CampaignEditor, stream_version

__all__ = ["CampaignCatalog", "CampaignCreator", "CampaignEditor", "stream_version"]

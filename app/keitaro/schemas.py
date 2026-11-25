"""Модели ответов Admin API Keitaro.

Лишние поля игнорируются. Исключение — оффер внутри потока: Keitaro принимает `offers` целиком,
поэтому неизвестные поля сохраняются и уходят обратно, а служебные (`id`, `stream_id`, даты) — нет.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SERVICE_FIELDS = frozenset({"id", "stream_id", "created_at", "updated_at"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def _none_to_list(v: Any) -> Any:
    return [] if v is None else v


class Offer(_Model):
    id: int
    name: str
    country: list[str] = []

    _country = field_validator("country", mode="before")(_none_to_list)


class Domain(_Model):
    id: int
    name: str = ""
    is_ssl: bool | None = None


class Group(_Model):
    id: int
    name: str = ""


class TrafficSource(_Model):
    id: int
    name: str = ""


class Campaign(_Model):
    id: int
    name: str
    alias: str | None = None
    type: str | None = None
    state: str | None = None
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None


class StreamOffer(BaseModel):
    model_config = ConfigDict(extra="allow")

    offer_id: int
    share: int
    state: str = "active"

    def payload(self) -> dict:
        """Что отправлять в PUT: всё, кроме служебных полей."""
        return {k: v for k, v in self.model_dump().items() if k not in SERVICE_FIELDS}


class Stream(_Model):
    id: int
    campaign_id: int | None = None
    name: str = ""
    type: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    position: int | None = None
    action_type: str | None = None
    action_payload: Any = None
    filters: list[dict] = []
    offers: list[StreamOffer] = []
    updated_at: str | None = None

    _lists = field_validator("filters", "offers", mode="before")(_none_to_list)


# ---------- запросы к Keitaro


class _Payload(BaseModel):
    """Тело запроса: опечатка в поле — ошибка сразу у нас, а не молчаливо проигнорированное поле в Keitaro."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def to_json(self) -> dict:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class CampaignCreatePayload(_Payload):
    name: str = Field(min_length=1)
    type: Literal["position", "weight"] = "position"
    domain_id: int
    group_id: int
    traffic_source_id: int
    state: Literal["active", "paused"] = "active"
    # стоимость не задаём — остаются значения Keitaro по умолчанию; поля здесь, чтобы задавать их типобезопасно
    cost_type: Literal["CPC", "CPUC", "CPM", "CPA", "CPS", "RevShare"] | None = None
    cost_value: float | None = None
    cost_currency: str | None = None
    cost_auto: bool | None = None


class StreamFilterPayload(_Payload):
    name: str
    mode: Literal["accept", "reject"]
    payload: list[str]


class StreamCreatePayload(_Payload):
    campaign_id: int
    name: str
    type: Literal["regular", "default", "forced"]
    position: int
    schema_: Literal["redirect", "landings", "action"] = Field(alias="schema")
    action_type: str | None = None
    action_payload: str | None = None
    filters: list[StreamFilterPayload] | None = None
    offers: list[StreamOffer] | None = None

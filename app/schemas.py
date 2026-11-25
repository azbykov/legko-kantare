from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.countries import ISO_ALPHA2

MAX_COUNTRIES = 30


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    countries: list[str] = Field(
        min_length=1, max_length=MAX_COUNTRIES, description='ISO 3166-1 alpha-2, например ["US", "DE"]'
    )
    offer_id: int = Field(gt=0)

    @model_validator(mode="before")
    @classmethod
    def _legacy_country(cls, data: Any) -> Any:
        """Старый формат `{"country": "US"}` тоже принимаем."""
        if isinstance(data, dict) and "country" in data and "countries" not in data:
            data = {**data, "countries": [data["country"]]}
            data.pop("country")
        return data

    @field_validator("countries")
    @classmethod
    def _iso(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v:
            code = raw.strip().upper() if isinstance(raw, str) else ""
            if code not in ISO_ALPHA2:
                raise ValueError(f"Неизвестный код страны «{raw}» — нужен ISO 3166-1 alpha-2, например US, DE, RU")
            if code not in out:
                out.append(code)
        return out

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Название не может быть пустым")
        return v


class OfferRef(BaseModel):
    offer_id: int = Field(gt=0)
    version: str = Field(min_length=1, max_length=64, description="версия потока с экрана (из GET кампании)")

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keitaro_url: str = "https://keitaro-for-tests.etolegko.com/admin_api/v1"
    keitaro_api_key: SecretStr = SecretStr("")  # SecretStr: не попадает в repr/логи
    # Адрес админки для ссылок; по умолчанию выводится из KEITARO_URL (…/admin_api/v1 → …/admin/)
    keitaro_admin_url: str | None = None
    keitaro_domain_id: int = 1813
    keitaro_group_id: int = 10332
    keitaro_traffic_source_id: int = 836
    campaign_prefix: str = "test_bykov_"
    database_url: str = "sqlite:///./keitaro.db"
    offers_cache_ttl: int = 300  # секунды
    campaigns_cache_ttl: int = 30  # секунды; список своих кампаний для поиска
    reference_cache_ttl: int = 300  # секунды; домены, группы кампаний, источники
    log_level: str = "INFO"

    @property
    def admin_url(self) -> str:
        if self.keitaro_admin_url:
            return self.keitaro_admin_url.rstrip("/") + "/"
        base = self.keitaro_url.rstrip("/")
        for tail in ("/admin_api/v1", "/admin_api"):
            if base.endswith(tail):
                base = base[: -len(tail)]
        return base + "/admin/"


def load_settings() -> Settings:
    """Настройки из окружения и .env. Приложение читает их один раз в create_app(), дальше — из app.state."""
    return Settings()

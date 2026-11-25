"""Admin API Keitaro: HTTP-клиент, его ошибка и схемы запросов/ответов."""

from app.keitaro.client import KeitaroClient, build_http
from app.keitaro.errors import KeitaroError

__all__ = ["KeitaroClient", "KeitaroError", "build_http"]

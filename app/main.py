"""Сборка приложения. Запуск: uvicorn --factory app.main:create_app"""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.campaigns_cache import CampaignsCache
from app.config import Settings, load_settings
from app.db import make_engine, make_sessionmaker
from app.deps import get_sessionmaker
from app.errors import ServiceError
from app.keitaro import KeitaroClient, KeitaroError, build_http
from app.references_cache import ReferenceCache
from app.routers import api

log = logging.getLogger("app.http")
STATIC = Path(__file__).resolve().parent.parent / "static"


def create_app(
    settings: Settings | None = None,
    *,
    keitaro: KeitaroClient | None = None,
    session_factory: sessionmaker | None = None,
) -> FastAPI:
    """Собирает приложение. Клиент Keitaro и фабрику сессий можно передать (тесты, скрипты);
    иначе они создаются из настроек. Созданное здесь закрывается при остановке."""
    settings = settings or load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s")

    owned_engine = None
    if session_factory is None:
        owned_engine = make_engine(settings.database_url)
        session_factory = make_sessionmaker(owned_engine)
    owned_http = None
    if keitaro is None:
        owned_http = build_http(settings)
        keitaro = KeitaroClient(owned_http)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if owned_http is not None:
            await owned_http.aclose()
        if owned_engine is not None:
            owned_engine.dispose()

    app = FastAPI(title="Keitaro wrapper", lifespan=lifespan)
    app.state.settings = settings
    app.state.keitaro = keitaro
    app.state.sessionmaker = session_factory
    app.state.campaigns_cache = CampaignsCache(settings.campaigns_cache_ttl)
    app.state.references_cache = ReferenceCache(settings.reference_cache_ttl)

    app.include_router(api.router)
    _install_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz(sm: sessionmaker = Depends(get_sessionmaker)):
        try:
            with sm() as s:
                s.execute(text("SELECT 1"))
        except Exception as e:
            log.error("healthz: БД недоступна: %s", e)
            return JSONResponse({"status": "error", "db": "unavailable"}, status_code=503)
        return {"status": "ok", "db": "ok"}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _error(request: Request, status: int, detail: str, code: str | None = None) -> JSONResponse:
    log.warning("%s %s -> %s: %s", request.method, request.url.path, status, detail)
    body = {"detail": detail} | ({"code": code} if code else {})
    return JSONResponse(body, status_code=status)


def _install_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - started) * 1000
        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        log.log(level, "%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code, ms)
        return response

    @app.exception_handler(KeitaroError)
    async def keitaro_error(request: Request, exc: KeitaroError):
        if exc.status in (401, 403):
            return _error(request, 502, "Keitaro отклонил запрос: проверьте KEITARO_API_KEY")
        status = exc.status if exc.status in (400, 404, 409, 422) else 502
        return _error(request, status, f"Keitaro: {exc.detail}")

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return _error(request, exc.status, exc.message, type(exc).__name__)

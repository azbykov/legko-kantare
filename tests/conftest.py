import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.keitaro import KeitaroClient, build_http
from app.main import create_app

BASE = "https://kt.test/admin_api/v1"
PREFIX = "/admin_api/v1"


class FakeKeitaro:
    """HTTP-эмуляция нужной части Admin API для httpx.MockTransport.

    Любой запрос, которого нет в таблице маршрутов, — ошибка теста (см. фикстуру `kt`).
    `calls` — все запросы по порядку, чтобы проверять, что лишних нет.
    """

    def __init__(self):
        self.offers_ = {i: {"id": i, "name": f"Offer {i}", "country": ["US"], "payout": 1.5} for i in (1, 2, 3, 4)}
        self.domains_ = [{"id": 1813, "name": "kt-emulator.test", "is_ssl": True, "state": "active"}]
        self.groups_ = [{"id": 10332, "name": "FORTESTS", "type": "campaigns"}]
        self.sources_ = [{"id": 836, "name": "FORTESTS"}]
        self.campaigns_: dict[int, dict] = {}
        self.streams_: dict[int, dict] = {}
        self.calls: list[str] = []
        self.bodies: dict[str, list] = {}
        self.unexpected: list[str] = []
        self.faults: list[dict] = []
        self.save_override: dict = {}  # что «трекер» сохранит иначе, чем прислали
        self._id = 100

    # ---- управление из тестов
    def fail(self, method, path, status=500, body="boom", skip=0, response=None):
        self.faults.append(
            {"call": f"{method} {path}", "status": status, "body": body, "skip": skip, "response": response}
        )

    def add_campaign(self, name, **kw):
        c = {"id": self._next(), "name": name, "alias": f"a{self._id}", "state": "active", **kw}
        self.campaigns_[c["id"]] = c
        return c

    def add_stream(self, campaign_id, **kw):
        s = {"id": self._next(), "campaign_id": campaign_id, "name": "s", "offers": [], "filters": [], **kw}
        self.streams_[s["id"]] = s
        return s

    def _next(self):
        self._id += 1
        return self._id

    # ---- транспорт
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(PREFIX)
        query = request.url.query.decode()
        call = f"{request.method} {path}" + (f"?{query}" if query else "")
        self.calls.append(call)
        body = json.loads(request.content) if request.content else None
        self.bodies.setdefault(call, []).append(body)
        for f in self.faults:
            if f["call"] == call:
                if f["skip"]:
                    f["skip"] -= 1
                    continue
                if f["response"] is not None:
                    return f["response"]
                return (
                    httpx.Response(f["status"], json=f["body"])
                    if not isinstance(f["body"], str)
                    else httpx.Response(f["status"], text=f["body"])
                )
        for method, pattern, fn in self._routes():
            m = re.fullmatch(pattern, path)
            if m and method == request.method:
                return fn(request, body, *[int(g) for g in m.groups()])
        self.unexpected.append(call)
        raise AssertionError(f"Неожиданный запрос в Keitaro: {call}")

    def _routes(self):
        return [
            ("GET", r"/offers", lambda r, b: self._ok(list(self.offers_.values()))),
            ("GET", r"/offers/(\d+)", lambda r, b, i: self._get(self.offers_, i)),
            ("GET", r"/domains", lambda r, b: self._ok(self.domains_)),
            ("GET", r"/groups", self._groups),
            ("GET", r"/traffic_sources", lambda r, b: self._ok(self.sources_)),
            ("GET", r"/campaigns", self._campaigns),
            ("POST", r"/campaigns", self._create_campaign),
            ("GET", r"/campaigns/(\d+)", lambda r, b, i: self._get(self.campaigns_, i)),
            ("DELETE", r"/campaigns/(\d+)", self._delete_campaign),
            ("GET", r"/campaigns/(\d+)/streams", self._streams),
            ("POST", r"/streams", self._create_stream),
            ("PUT", r"/streams/(\d+)", self._update_stream),
        ]

    @staticmethod
    def _ok(data, status=200):
        return httpx.Response(status, json=data)

    def _get(self, store, i):
        return self._ok(store[i]) if i in store else self._ok({"error": "Not found"}, 404)

    def _groups(self, r, b):
        assert r.url.params.get("type") == "campaigns"
        return self._ok(self.groups_)

    def _campaigns(self, r, b):
        offset, limit = int(r.url.params["offset"]), int(r.url.params["limit"])
        return self._ok(list(self.campaigns_.values())[offset : offset + limit])

    def _create_campaign(self, r, b):
        c = {"id": self._next(), "alias": f"alias{self._id}", "created_at": "2026-01-01", **b, **self.save_override}
        self.campaigns_[c["id"]] = c
        return self._ok(c)

    def _delete_campaign(self, r, b, i):
        self.campaigns_.pop(i, None)
        self.streams_ = {k: v for k, v in self.streams_.items() if v["campaign_id"] != i}
        return httpx.Response(204)

    def _streams(self, r, b, cid):
        items = sorted(
            (s for s in self.streams_.values() if s["campaign_id"] == cid), key=lambda s: s.get("position", 0)
        )
        return self._ok(items)

    def _with_service_fields(self, sid, offers):
        return [
            {"id": 9000 + n, "stream_id": sid, "created_at": "2026-01-01", "updated_at": "2026-01-02", **o}
            for n, o in enumerate(offers or [])
        ]

    def _create_stream(self, r, b):
        sid = self._next()
        s = {"id": sid, "filters": [], **b, "offers": self._with_service_fields(sid, b.get("offers"))}
        s["filters"] = [{"id": 1, "oid": sid, **f} for f in s["filters"]]
        self.streams_[sid] = s
        return self._ok(s)

    def _update_stream(self, r, b, sid):
        if sid not in self.streams_:
            return self._ok({"error": "Not found"}, 404)
        self.streams_[sid]["offers"] = self._with_service_fields(sid, b["offers"])
        return self._ok(self.streams_[sid])


@pytest.fixture
def kt():
    fake = FakeKeitaro()
    yield fake
    assert not fake.unexpected, f"лишние запросы в Keitaro: {fake.unexpected}"


@pytest.fixture
def settings():
    return Settings(_env_file=None, keitaro_url=BASE, keitaro_api_key="test-secret-key", database_url="sqlite://")


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def keitaro(kt, settings):
    """Настоящий KeitaroClient поверх HTTP-фейка."""
    return KeitaroClient(build_http(settings, transport=httpx.MockTransport(kt.handle)))


@pytest.fixture
def make_client(keitaro, settings):
    """Своё приложение на каждый вызов: зависимости передаются в create_app, без dependency_overrides."""

    def _make(sm):
        return TestClient(create_app(settings, keitaro=keitaro, session_factory=sm))

    return _make


@pytest.fixture
def client(make_client, session_factory, kt):
    c = make_client(session_factory)
    c.kt = kt  # помощникам тестов нужен фейк, чтобы посчитать версию потока
    return c

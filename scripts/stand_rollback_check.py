"""Откат на стенде: второй поток падает (сбой внедрён на клиенте), кампания должна исчезнуть из Keitaro.

Запуск из корня проекта: python scripts/stand_rollback_check.py (нужен KEITARO_API_KEY в .env).
"""

import os
import sys
import time

sys.path.insert(0, os.getcwd())

from fastapi.testclient import TestClient

from app.config import load_settings
from app.db import Base, make_engine, make_sessionmaker
from app.keitaro import KeitaroClient, KeitaroError
from app.main import create_app

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand.db")  # временная БД, удаляется в конце
engine = make_engine(f"sqlite:///{DB}")
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
settings = load_settings()  # KEITARO_* из .env
settings.log_level = os.environ.get("LOG_LEVEL", "WARNING")
app = create_app(settings, session_factory=make_sessionmaker(engine))
kt = app.state.keitaro

orig = KeitaroClient.create_stream
seen = {"n": 0, "cid": None}


async def flaky(self, payload):
    seen["n"] += 1
    seen["cid"] = payload.campaign_id
    if seen["n"] == 2:
        raise KeitaroError(500, "injected: второй поток не создался")
    return await orig(self, payload)


KeitaroClient.create_stream = flaky

with TestClient(app) as c:
    o1 = c.get("/api/offers").json()[0]["id"]
    r = c.post(
        "/api/campaigns", json={"name": f"stand_rollback_{int(time.time())}", "countries": ["DE"], "offer_id": o1}
    )
    print("ответ:", r.status_code, r.json()["detail"])
    cid = seen["cid"]
    try:
        c.portal.call(kt.campaign, cid)
        print("FAIL: кампания", cid, "осталась на стенде")
    except KeitaroError as e:
        print("OK: кампания", cid, "после отката ->", e.status)
    audit = c.get("/api/audit").json()
    print("аудит:", [(a["action"], a["campaign_id"], (a["error"] or "")[:60]) for a in audit])
    left = [x for x in c.portal.call(kt.campaigns, 0, 1000) if x.name.startswith("test_bykov_")]
    print("кампаний test_bykov_ на стенде:", [(x.id, x.name) for x in left])
os.remove(DB)

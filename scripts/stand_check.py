"""Сквозной прогон на стенде Keitaro через само приложение. Удаляет созданное в finally.

Запуск из корня проекта: python scripts/stand_check.py (нужен KEITARO_API_KEY в .env).
"""

import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())

from fastapi.testclient import TestClient

from app.config import load_settings
from app.db import Base, make_engine, make_sessionmaker
from app.main import create_app

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand.db")  # временная БД, удаляется в конце
engine = make_engine(f"sqlite:///{DB}")
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
settings = load_settings()  # KEITARO_* из .env
settings.log_level = os.environ.get("LOG_LEVEL", "WARNING")
app = create_app(settings, session_factory=make_sessionmaker(engine))
kt = app.state.keitaro

results: list[tuple[str, bool, str]] = []
created: list[int] = []


def check(name, ok, info=""):
    results.append((name, bool(ok), info))
    print(("OK  " if ok else "FAIL"), name, "—", info)


def raw(path):
    # Прямой вызов async-клиента в потоке event loop TestClient.
    return c.portal.call(kt._request, "GET", path)


H = {"X-User": "stand-run"}
name = f"stand_run_{int(time.time())}"

with TestClient(app) as c:
    try:
        offers = c.get("/api/offers").json()
        o1, o2, o3 = offers[0]["id"], offers[1]["id"], offers[2]["id"]

        # 1. создание
        r = c.post("/api/campaigns", json={"name": name, "countries": ["US", "DE"], "offer_id": o1}, headers=H)
        check("создание кампании", r.status_code == 201, f"{r.status_code} {r.text[:600]}")
        if r.status_code != 201:
            raise SystemExit
        res = r.json()
        cid = res["id"]
        created.append(cid)
        check(
            "сверка: Keitaro сохранил как отправлено",
            not res["mismatches"] and not res["verify_error"],
            json.dumps(res["mismatches"] or res["verify_error"], ensure_ascii=False)[:800],
        )
        check("ссылки", res["admin_url"] and res["public_url"], f"{res['admin_url']} | {res['public_url']}")
        geo_saved = next(x for x in c.portal.call(kt.streams, cid) if x.position == 1)
        check(
            "гео-фильтр на две страны",
            geo_saved.filters and geo_saved.filters[0]["payload"] == ["US", "DE"],
            str(geo_saved.filters),
        )

        # сырые ответы — посмотреть реальные поля
        rc = raw(f"/campaigns/{cid}")
        rs = raw(f"/campaigns/{cid}/streams")
        print("RAW campaign keys:", sorted(rc))
        print("RAW stream keys:", sorted(rs[0]) if rs else None)
        print("RAW streams:", json.dumps(rs, ensure_ascii=False)[:1500])

        # 2. редактор
        view = c.get(f"/api/campaigns/{cid}").json()
        landing = next(s for s in view["streams"] if s["editable"])
        geo = next(s for s in view["streams"] if not s["editable"])
        sid, v0 = landing["id"], landing["version"]
        check(
            "две потока: redirect + landings",
            len(view["streams"]) == 2 and geo["schema"] == "redirect",
            str([(s["name"], s["schema"], s["type"]) for s in view["streams"]]),
        )

        # 3. добавить оффер
        r = c.post(f"/api/campaigns/{cid}/streams/{sid}/offers", json={"offer_id": o2, "version": v0}, headers=H)
        check("добавить оффер", r.status_code == 200, r.text[:300])
        s = next(x for x in c.get(f"/api/campaigns/{cid}").json()["streams"] if x["id"] == sid)
        check("доли 50/50", [o["share"] for o in s["offers"]] == [50, 50], str(s["offers"]))
        v1 = s["version"]
        check("версия изменилась и совпадает с ответом", v1 != v0 and v1 == r.json()["version"], f"{v0} -> {v1}")

        # 4. устаревшая версия
        r = c.post(f"/api/campaigns/{cid}/streams/{sid}/offers", json={"offer_id": o3, "version": v0}, headers=H)
        check(
            "устаревшая версия → 409 StaleStream",
            r.status_code == 409 and r.json().get("code") == "StaleStream",
            r.text[:200],
        )

        # 5. третий оффер, пропорции
        r = c.post(f"/api/campaigns/{cid}/streams/{sid}/offers", json={"offer_id": o3, "version": v1}, headers=H)
        s = next(x for x in c.get(f"/api/campaigns/{cid}").json()["streams"] if x["id"] == sid)
        shares = [o["share"] for o in s["offers"]]
        check("третий оффер: 33/33/34, сумма 100", r.status_code == 200 and sum(shares) == 100, str(shares))

        # 6. повтор → 409
        r = c.post(
            f"/api/campaigns/{cid}/streams/{sid}/offers", json={"offer_id": o3, "version": s["version"]}, headers=H
        )
        check("повтор оффера → 409", r.status_code == 409, r.text[:200])

        # 7. удаление с пересчётом
        r = c.delete(f"/api/campaigns/{cid}/streams/{sid}/offers/{o1}", params={"version": s["version"]}, headers=H)
        s = next(x for x in c.get(f"/api/campaigns/{cid}").json()["streams"] if x["id"] == sid)
        check(
            "удалить оффер, остальные растянуты до 100",
            r.status_code == 200 and [o["offer_id"] for o in s["offers"]] == [o2, o3] and s["share_total"] == 100,
            str(s["offers"]),
        )
        r = c.delete(f"/api/campaigns/{cid}/streams/{sid}/offers/{o2}", params={"version": s["version"]}, headers=H)
        s = next(x for x in c.get(f"/api/campaigns/{cid}").json()["streams"] if x["id"] == sid)
        check(
            "ещё одно удаление → один оффер 100%",
            r.status_code == 200 and [(o["offer_id"], o["share"]) for o in s["offers"]] == [(o3, 100)],
            str(s["offers"]),
        )

        # 8. последний оффер
        r = c.delete(f"/api/campaigns/{cid}/streams/{sid}/offers/{o3}", params={"version": s["version"]}, headers=H)
        check(
            "последний оффер → 409 LastOfferInStream",
            r.status_code == 409 and r.json().get("code") == "LastOfferInStream",
            r.text[:200],
        )

        # 9. redirect-поток и чужой id потока
        r = c.post(
            f"/api/campaigns/{cid}/streams/{geo['id']}/offers",
            json={"offer_id": o1, "version": geo["version"]},
            headers=H,
        )
        check("redirect-поток → 409", r.status_code == 409, r.text[:200])
        r = c.post(f"/api/campaigns/{cid}/streams/999999999/offers", json={"offer_id": o1, "version": "x"}, headers=H)
        check("поток не этой кампании → 404", r.status_code == 404, r.text[:200])

        # 10. поиск
        by_id = c.get("/api/campaigns", params={"q": str(cid)}).json()
        by_name = c.get("/api/campaigns", params={"q": name.upper()}).json()
        check(
            "поиск по id и по имени",
            [x["id"] for x in by_id["items"]] == [cid] and cid in [x["id"] for x in by_name["items"]],
            f"{by_id['total']} / {by_name['total']}",
        )

        # 11. валидация до запросов
        r = c.post("/api/campaigns", json={"name": "x", "countries": ["US", "XX"], "offer_id": o1})
        check("страна XX → 422", r.status_code == 422, "")

        # 12. аудит
        audit = c.get("/api/audit", params={"campaign_id": cid}).json()
        actions = [(a["action"], bool(a["error"])) for a in reversed(audit)]
        check(
            "аудит: actor и ошибки записаны",
            all(a["actor"] == "stand-run" for a in audit) and len(audit) >= 8,
            str(actions),
        )
    finally:
        for cid in created:
            try:
                c.portal.call(kt.delete_campaign, cid)
                try:
                    after = raw(f"/campaigns/{cid}")
                    print(f"после DELETE кампания {cid} всё ещё отдаётся: state={after.get('state')}")
                    check("уборка: кампания удалена", after.get("state") in ("deleted", "archived"), str(after)[:200])
                except Exception as e:
                    check("уборка: кампания удалена", "404" in str(e), str(e)[:200])
            except Exception as e:
                check("уборка: кампания удалена", False, f"НЕ УДАЛЕНА {cid}: {e}")

os.remove(DB)
print()
print(f"ИТОГО: {sum(ok for _, ok, _ in results)}/{len(results)}")

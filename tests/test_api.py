import asyncio

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.countries import ISO_ALPHA2
from app.keitaro.schemas import Stream
from app.models import AuditLog, CampaignRecord
from app.scenarios import stream_version

# Ровно эти запросы делает успешное создание кампании (первая кампания получает id 101).
CREATE_CALLS = [
    "GET /offers/1",
    "GET /domains",
    "GET /groups?type=campaigns",
    "GET /traffic_sources",
    "POST /campaigns",
    "POST /streams",
    "POST /streams",
    "GET /campaigns/101",
    "GET /campaigns/101/streams",
]
BEFORE_CREATE = CREATE_CALLS[:4]


def _create(client, headers=None, **kw):
    body = {"name": "c1", "countries": ["us"], "offer_id": 1, **kw}
    return client.post("/api/campaigns", json=body, headers=headers or {})


def _ver(kt, sid):
    """Версия потока, как её увидел бы экран (без HTTP, чтобы не портить журнал запросов)."""
    return stream_version(Stream.model_validate(kt.streams_[sid]))


def _add(client, cid, sid, oid, version=None, **kw):
    body = {"offer_id": oid, "version": version or _ver(client.kt, sid)}
    return client.post(f"/api/campaigns/{cid}/streams/{sid}/offers", json=body, **kw)


def _rm(client, cid, sid, oid, version=None, **kw):
    params = {"version": version or _ver(client.kt, sid)}
    return client.delete(f"/api/campaigns/{cid}/streams/{sid}/offers/{oid}", params=params, **kw)


def _new(client, kt, **kw):
    """Создать кампанию и вернуть (cid, sid потока с офферами), очистив журнал запросов."""
    cid = _create(client, **kw).json()["id"]
    sid = next(s["id"] for s in kt.streams_.values() if s["campaign_id"] == cid and s["schema"] == "landings")
    kt.calls.clear()
    return cid, sid


def _audit(session_factory):
    with session_factory() as db:
        return db.query(AuditLog).order_by(AuditLog.id).all()


# ---------------- создание


def test_create_campaign_exact_requests_and_payloads(client, kt):
    r = _create(client)
    assert r.status_code == 201, r.text
    assert kt.calls == CREATE_CALLS
    data = r.json()
    assert data["name"] == "test_bykov_c1"
    assert kt.bodies["POST /campaigns"][0] == {
        "name": "test_bykov_c1",
        "type": "position",
        "domain_id": 1813,
        "group_id": 10332,
        "traffic_source_id": 836,
        "state": "active",
    }
    geo, offers = kt.bodies["POST /streams"]
    assert geo["filters"] == [{"name": "country", "mode": "accept", "payload": ["US"]}]
    assert (geo["schema"], geo["action_payload"], geo["position"]) == ("redirect", "https://google.com", 1)
    assert (offers["type"], offers["position"]) == ("default", 2)
    assert offers["offers"] == [{"offer_id": 1, "share": 100, "state": "active"}]


def test_create_returns_links_and_no_mismatches(client, kt):
    data = _create(client).json()
    assert data["admin_url"] == "https://kt.test/admin/#!/campaigns/101"
    assert data["public_url"] == "https://kt-emulator.test/alias101"
    assert data["mismatches"] == [] and data["verify_error"] is None


def test_create_reports_what_tracker_saved_differently(client, kt):
    kt.save_override = {"group_id": 1, "state": "paused"}
    data = _create(client).json()
    got = {(m["entity"], m["field"]): (m["sent"], m["saved"]) for m in data["mismatches"]}
    assert got == {("campaign", "group_id"): (10332, 1), ("campaign", "state"): ("active", "paused")}


def test_verify_failure_does_not_undo_creation(client, kt):
    kt.fail("GET", "/campaigns/101/streams", 500)
    r = _create(client)
    assert r.status_code == 201
    assert "500" in r.json()["verify_error"]
    assert 101 in kt.campaigns_


def test_prefix_not_doubled(client):
    assert _create(client, name="test_bykov_x").json()["name"] == "test_bykov_x"


@pytest.mark.parametrize("country", ["USA", "1!", "XX", "РУ", "U"])
def test_country_validated_before_any_request(client, kt, country):
    assert _create(client, countries=["US", country]).status_code == 422
    assert kt.calls == []


@pytest.mark.parametrize("countries", [[], sorted(ISO_ALPHA2)[:31]], ids=["empty", "31-countries"])
def test_countries_list_bounds(client, kt, countries):
    assert _create(client, countries=countries).status_code == 422
    assert kt.calls == []


def test_lowercase_iso_country_ok(client, kt):
    assert _create(client, countries=["gb"]).status_code == 201
    assert kt.bodies["POST /streams"][0]["filters"][0]["payload"] == ["GB"]


def test_several_countries_in_one_filter(client, kt, session_factory):
    r = _create(client, countries=["us", "DE", " fr ", "US"])
    assert r.status_code == 201 and r.json()["mismatches"] == []
    geo = kt.bodies["POST /streams"][0]
    assert geo["filters"] == [{"name": "country", "mode": "accept", "payload": ["US", "DE", "FR"]}]
    assert geo["name"] == "Geo US,DE,FR -> Google"
    with session_factory() as db:
        assert db.query(CampaignRecord).one().countries == ["US", "DE", "FR"]


def test_legacy_single_country_field_still_accepted(client, kt):
    r = client.post("/api/campaigns", json={"name": "old", "country": "de", "offer_id": 1})
    assert r.status_code == 201
    assert kt.bodies["POST /streams"][0]["filters"][0]["payload"] == ["DE"]


@pytest.mark.parametrize("name", ["  ", "test_bykov_"])
def test_empty_name_rejected_before_any_request(client, kt, name):
    assert _create(client, name=name).status_code == 422
    assert kt.calls == []


def test_unknown_offer_creates_nothing(client, kt):
    assert _create(client, offer_id=999).status_code == 404
    assert kt.calls == ["GET /offers/999"]


@pytest.mark.parametrize(
    "attr, env",
    [("domains_", "KEITARO_DOMAIN_ID"), ("groups_", "KEITARO_GROUP_ID"), ("sources_", "KEITARO_TRAFFIC_SOURCE_ID")],
)
def test_missing_binding_checked_before_post(client, kt, attr, env):
    setattr(kt, attr, [{"id": 1, "name": "other"}])
    r = _create(client)
    assert r.status_code == 500 and env in r.json()["detail"]
    assert kt.calls == BEFORE_CREATE


def test_reference_lists_are_cached_across_creates(client, kt):
    assert _create(client).status_code == 201
    kt.calls.clear()
    assert _create(client, name="c2").status_code == 201
    assert kt.calls[0] == "GET /offers/1"
    assert "GET /domains" not in kt.calls
    assert "GET /groups?type=campaigns" not in kt.calls
    assert "GET /traffic_sources" not in kt.calls


def test_reference_cache_ttl(client, kt):
    assert _create(client).status_code == 201
    client.app.state.references_cache.ttl = 0
    kt.calls.clear()
    assert _create(client, name="c2").status_code == 201
    assert kt.calls[:4] == BEFORE_CREATE


def test_cached_binding_miss_rereads_lists(client, kt, settings):
    """Привязки нет в уже тёплом кэше — списки читаются ещё раз (её могли только что добавить)."""
    assert _create(client).status_code == 201
    kt.domains_.append({"id": 9999, "name": "new.example", "is_ssl": True})
    settings.keitaro_domain_id = 9999
    kt.calls.clear()
    r = _create(client, name="c2")
    assert r.status_code == 201, r.text
    assert "GET /domains" in kt.calls
    assert kt.bodies["POST /campaigns"][-1]["domain_id"] == 9999


# ---------------- откат


def test_stream_failure_rolls_back(client, kt, session_factory):
    kt.fail("POST", "/streams", 500, skip=1)
    r = _create(client)
    assert r.status_code == 502
    assert "удалена" in r.json()["detail"] and "boom" in r.json()["detail"]
    assert kt.calls == [*BEFORE_CREATE, "POST /campaigns", "POST /streams", "POST /streams", "DELETE /campaigns/101"]
    assert not kt.campaigns_ and not kt.streams_
    with session_factory() as db:
        assert db.query(CampaignRecord).count() == 0


def test_failed_rollback_keeps_original_error_and_is_recorded(client, kt, session_factory):
    kt.fail("POST", "/streams", 500, body="stream boom", skip=1)
    kt.fail("DELETE", "/campaigns/101", 500, body="delete boom")
    r = _create(client)
    detail = r.json()["detail"]
    assert r.status_code == 502
    assert "stream boom" in detail and "delete boom" in detail
    assert "осталась недосозданная кампания #101" in detail and "удалена" not in detail
    assert kt.calls[-1] == "DELETE /campaigns/101"
    with session_factory() as db:
        rec = db.query(CampaignRecord).one()
        assert (rec.keitaro_id, rec.status) == (101, "rollback_failed")
    (a,) = _audit(session_factory)
    assert a.action == "campaign.create" and a.campaign_id == 101 and "delete boom" in a.error


def test_db_failure_rolls_back_campaign(make_client, kt, session_factory):
    class BrokenSession(Session):
        def commit(self):
            raise RuntimeError("db is down")

    client = make_client(sessionmaker(bind=session_factory.kw["bind"], class_=BrokenSession))
    r = _create(client)
    assert r.status_code == 500
    assert "db is down" in r.json()["detail"] and "удалена" in r.json()["detail"]
    assert kt.calls == [*BEFORE_CREATE, "POST /campaigns", "POST /streams", "POST /streams", "DELETE /campaigns/101"]
    assert not kt.campaigns_


# ---------------- список кампаний


def test_list_only_own_and_reads_all_keitaro_pages(client, kt):
    for i in range(105):
        kt.add_campaign(f"other_{i}")
    own = kt.add_campaign("test_bykov_mine")
    res = client.get("/api/campaigns").json()
    assert [c["id"] for c in res["items"]] == [own["id"]] and res["total"] == 1
    assert kt.calls == ["GET /campaigns?offset=0&limit=100", "GET /campaigns?offset=100&limit=100"]


def test_search_by_id_and_name(client, kt):
    a = kt.add_campaign("test_bykov_Alpha")
    kt.add_campaign("test_bykov_beta")
    kt.add_campaign("alpha_foreign")
    by_name = client.get("/api/campaigns", params={"q": "ALPHA"}).json()
    assert [c["id"] for c in by_name["items"]] == [a["id"]]
    by_id = client.get("/api/campaigns", params={"q": str(a["id"])}).json()
    assert [c["id"] for c in by_id["items"]] == [a["id"]]
    assert client.get("/api/campaigns", params={"q": "nothing"}).json()["total"] == 0


def test_campaign_pages(client, kt):
    ids = [kt.add_campaign(f"test_bykov_{i}")["id"] for i in range(5)]
    p1 = client.get("/api/campaigns", params={"page": 1, "per_page": 2}).json()
    p3 = client.get("/api/campaigns", params={"page": 3, "per_page": 2}).json()
    assert p1["total"] == 5 and [c["id"] for c in p1["items"]] == ids[::-1][:2]
    assert [c["id"] for c in p3["items"]] == [ids[0]]
    assert client.get("/api/campaigns", params={"per_page": 1000}).status_code == 422


def test_campaign_list_is_cached(client, kt, settings):
    kt.add_campaign("test_bykov_a")
    scan = ["GET /campaigns?offset=0&limit=100"]
    for q in ("", "a", "test", ""):  # набор текста в поиске
        client.get("/api/campaigns", params={"q": q})
    assert kt.calls == scan
    client.get("/api/campaigns", params={"refresh": True})
    assert kt.calls == scan * 2


def test_create_invalidates_campaign_cache(client, kt):
    client.get("/api/campaigns")
    cid = _create(client, name="fresh").json()["id"]
    kt.calls.clear()
    items = client.get("/api/campaigns").json()["items"]
    assert cid in [c["id"] for c in items] and kt.calls == ["GET /campaigns?offset=0&limit=100"]


def test_campaign_cache_ttl(client, kt, settings):
    client.get("/api/campaigns")
    client.app.state.campaigns_cache.ttl = 0
    client.get("/api/campaigns")
    assert kt.calls == ["GET /campaigns?offset=0&limit=100"] * 2


def test_exact_id_missing_from_cache_is_fetched_directly(client, kt):
    client.get("/api/campaigns")  # кэш пуст
    late = kt.add_campaign("test_bykov_created_in_admin")
    foreign = kt.add_campaign("someone_else")
    kt.calls.clear()
    assert [c["id"] for c in client.get("/api/campaigns", params={"q": str(late["id"])}).json()["items"]] == [
        late["id"]
    ]
    assert client.get("/api/campaigns", params={"q": str(foreign["id"])}).json()["items"] == []
    assert client.get("/api/campaigns", params={"q": "999999"}).json()["items"] == []
    assert kt.calls == [f"GET /campaigns/{late['id']}", f"GET /campaigns/{foreign['id']}", "GET /campaigns/999999"]


# ---------------- редактор


def test_add_offer_exact_requests_and_clean_put(client, kt):
    cid, sid = _new(client, kt)
    kt.streams_[sid]["offers"][0]["custom_flag"] = "keep-me"  # неизвестное поле оффера
    assert _add(client, cid, sid, 2).status_code == 200
    assert kt.calls == [
        f"GET /campaigns/{cid}",
        f"GET /campaigns/{cid}/streams",
        "GET /offers/2",
        f"PUT /streams/{sid}",
    ]
    (put,) = kt.bodies[f"PUT /streams/{sid}"]
    assert put == {
        "offers": [
            {"offer_id": 1, "share": 50, "state": "active", "custom_flag": "keep-me"},
            {"offer_id": 2, "share": 50, "state": "active"},
        ]
    }


def test_add_remove_keep_100(client, kt):
    cid, sid = _new(client, kt)
    for oid in (2, 3):
        assert _add(client, cid, sid, oid).status_code == 200
    stream = client.get(f"/api/campaigns/{cid}").json()["streams"][1]
    assert stream["share_total"] == 100 and len(stream["offers"]) == 3
    assert _add(client, cid, sid, 2).status_code == 409
    assert _rm(client, cid, sid, 1).status_code == 200
    assert _rm(client, cid, sid, 1).status_code == 404
    stream = client.get(f"/api/campaigns/{cid}").json()["streams"][1]
    assert [o["offer_id"] for o in stream["offers"]] == [2, 3] and stream["share_total"] == 100


def test_remove_really_removes(client, kt):
    cid, sid = _new(client, kt)
    _add(client, cid, sid, 2)
    assert _rm(client, cid, sid, 1).status_code == 200
    assert kt.bodies[f"PUT /streams/{sid}"][-1] == {"offers": [{"offer_id": 2, "share": 100, "state": "active"}]}


def test_cannot_remove_last_offer_of_default_stream(client, kt):
    cid, sid = _new(client, kt)
    assert _rm(client, cid, sid, 1).status_code == 409
    assert not any(c.startswith("PUT") for c in kt.calls)


def test_regular_landings_stream_is_never_emptied(client, kt):
    cid, _ = _new(client, kt)
    s = kt.add_stream(
        cid, type="regular", schema="landings", position=3, offers=[{"offer_id": 1, "share": 100, "state": "active"}]
    )
    r = _rm(client, cid, s["id"], 1)
    assert r.status_code == 409 and r.json()["code"] == "LastOfferInStream"
    assert _add(client, cid, s["id"], 2).status_code == 200  # сначала добавить другой —
    assert _rm(client, cid, s["id"], 1).status_code == 200  # тогда прежний можно убрать


def test_add_keeps_proportions(client, kt):
    cid, sid = _new(client, kt)
    kt.streams_[sid]["offers"] = [
        {"offer_id": 1, "share": 70, "state": "active"},
        {"offer_id": 2, "share": 30, "state": "active"},
    ]
    _add(client, cid, sid, 3)
    assert [o["share"] for o in kt.bodies[f"PUT /streams/{sid}"][0]["offers"]] == [47, 20, 33]


def test_more_than_100_active_offers_rejected(client, kt):
    cid, sid = _new(client, kt)
    for i in range(5, 106):
        kt.offers_[i] = {"id": i, "name": f"Offer {i}"}
    kt.streams_[sid]["offers"] = [{"offer_id": i, "share": 1, "state": "active"} for i in range(5, 105)]
    r = _add(client, cid, sid, 105)
    assert r.status_code == 409 and r.json()["code"] == "TooManyOffers"
    assert not any(c.startswith("PUT") for c in kt.calls)


# ---------------- версия потока (не затирать чужую правку)


def test_stale_version_is_rejected_without_put(client, kt, session_factory):
    cid, sid = _new(client, kt)
    seen = client.get(f"/api/campaigns/{cid}").json()["streams"][1]["version"]
    kt.calls.clear()
    kt.streams_[sid]["offers"][0]["share"] = 99  # кто-то поправил поток в админке
    for r in (_add(client, cid, sid, 2, version=seen), _rm(client, cid, sid, 1, version=seen)):
        assert r.status_code == 409 and r.json()["code"] == "StaleStream"
        assert "обновите страницу" in r.json()["detail"]
    assert not any(c.startswith("PUT") for c in kt.calls)
    assert "обновите страницу" in _audit(session_factory)[-1].error


def test_updated_at_change_is_a_conflict_even_if_offers_same(client, kt):
    cid, sid = _new(client, kt)
    kt.streams_[sid]["updated_at"] = "2026-01-01 10:00:00"
    seen = _ver(kt, sid)
    kt.streams_[sid]["updated_at"] = "2026-01-01 10:05:00"  # меняли что-то, кроме офферов
    assert _add(client, cid, sid, 2, version=seen).json()["code"] == "StaleStream"


def test_version_matches_view_and_changes_after_edit(client, kt):
    cid, sid = _new(client, kt)
    v1 = client.get(f"/api/campaigns/{cid}").json()["streams"][1]["version"]
    r = _add(client, cid, sid, 2, version=v1)
    v2 = client.get(f"/api/campaigns/{cid}").json()["streams"][1]["version"]
    assert r.status_code == 200 and v2 != v1 and r.json()["version"] == v2
    assert _add(client, cid, sid, 3, version=v1).status_code == 409  # старая версия больше не годится


def test_version_is_required(client, kt):
    cid, sid = _new(client, kt)
    assert client.post(f"/api/campaigns/{cid}/streams/{sid}/offers", json={"offer_id": 2}).status_code == 422
    assert client.delete(f"/api/campaigns/{cid}/streams/{sid}/offers/1").status_code == 422
    assert kt.calls == []


def test_paused_offers_keep_share_and_are_not_counted(client, kt):
    cid, sid = _new(client, kt)
    kt.streams_[sid]["offers"].append({"offer_id": 2, "share": 40, "state": "disabled"})
    _add(client, cid, sid, 3)
    sent = {o["offer_id"]: o for o in kt.bodies[f"PUT /streams/{sid}"][0]["offers"]}
    assert sent[2]["share"] == 40 and sent[1]["share"] + sent[3]["share"] == 100
    assert client.get(f"/api/campaigns/{cid}").json()["streams"][1]["share_total"] == 100


def test_foreign_campaign_is_read_only(client, kt):
    foreign = kt.add_campaign("someone_else")
    s = kt.add_stream(
        foreign["id"], type="default", schema="landings", offers=[{"offer_id": 1, "share": 100, "state": "active"}]
    )
    fid = foreign["id"]
    assert client.get(f"/api/campaigns/{fid}").status_code == 403
    assert _add(client, fid, s["id"], 2).status_code == 403
    assert _rm(client, fid, s["id"], 1).status_code == 403
    assert kt.calls == [f"GET /campaigns/{fid}"] * 3


def test_stream_of_other_campaign_rejected(client, kt):
    a, _ = _new(client, kt, name="a")
    _, sid_b = _new(client, kt, name="b")
    assert _add(client, a, sid_b, 2).status_code == 404
    assert _rm(client, a, sid_b, 1).status_code == 404
    assert kt.calls == [f"GET /campaigns/{a}", f"GET /campaigns/{a}/streams"] * 2


def test_redirect_stream_not_editable(client, kt):
    cid, _ = _new(client, kt)
    geo = next(s["id"] for s in kt.streams_.values() if s["campaign_id"] == cid and s["schema"] == "redirect")
    assert _add(client, cid, geo, 2).status_code == 409
    assert kt.calls == [f"GET /campaigns/{cid}", f"GET /campaigns/{cid}/streams"]


# ---------------- аудит


def test_audit_actor_snapshots_and_errors(client, kt, session_factory):
    cid, sid = _new(client, kt, headers={"X-User": "alice@example.com"})
    _add(client, cid, sid, 2, headers={"X-User": "bob"})
    _add(client, cid, sid, 2)  # повтор → 409, без X-User
    create, add, dup = _audit(session_factory)

    assert (create.actor, create.action, create.campaign_id, create.error) == (
        "alice@example.com",
        "campaign.create",
        cid,
        None,
    )
    assert create.after["id"] == cid

    assert (add.actor, add.action, add.stream_id, add.offer_id) == ("bob", "offer.add", sid, 2)
    assert add.before == [{"offer_id": 1, "share": 100, "state": "active"}]
    assert add.after == [
        {"offer_id": 1, "share": 50, "state": "active"},
        {"offer_id": 2, "share": 50, "state": "active"},
    ]

    assert dup.actor == "anonymous@testclient" and dup.error == "Оффер уже есть в потоке" and dup.after is None


def test_audit_records_failed_create(client, kt, session_factory):
    _create(client, offer_id=999)
    (a,) = _audit(session_factory)
    assert a.action == "campaign.create" and "404" in a.error and a.before["offer_id"] == 999
    assert a.before["countries"] == ["US"]


def test_audit_api(client, kt):
    cid, sid = _new(client, kt)
    _add(client, cid, sid, 2)
    items = client.get("/api/audit", params={"campaign_id": cid}).json()
    assert [i["action"] for i in items] == ["offer.add", "campaign.create"]


# ---------------- кэш офферов


def test_offers_cached_with_ttl(client, kt, settings):
    assert [o["id"] for o in client.get("/api/offers").json()] == [1, 2, 3, 4]
    client.get("/api/offers")
    assert kt.calls == ["GET /offers"]
    client.get("/api/offers", params={"refresh": True})
    assert kt.calls == ["GET /offers"] * 2
    settings.offers_cache_ttl = 0
    client.get("/api/offers")
    assert kt.calls == ["GET /offers"] * 3


def test_offers_cache_uses_own_session(kt, settings, session_factory):
    """Обновление кэша коммитит свою сессию и не трогает чужую незавершённую транзакцию."""
    import httpx

    from app.keitaro import KeitaroClient, build_http
    from app.offers_cache import OffersCatalog

    opened: list[Session] = []

    class Tracking(Session):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.commits = 0
            opened.append(self)

        def commit(self):
            self.commits += 1
            super().commit()

    sm = sessionmaker(bind=session_factory.kw["bind"], class_=Tracking, expire_on_commit=False)
    main = sm()
    main.add(CampaignRecord(keitaro_id=1, name="pending", countries=["US"], offer_id=1))
    kc = KeitaroClient(build_http(settings, transport=httpx.MockTransport(kt.handle)))

    async def go():
        try:
            await OffersCatalog(kc, sm, ttl=300).list()
        finally:
            await kc._http.aclose()

    asyncio.run(go())
    assert main.commits == 0 and main.new  # правка в основной сессии не закоммичена
    assert sum(s.commits for s in opened if s is not main) == 1
    main.rollback()
    with session_factory() as db:
        assert db.query(CampaignRecord).count() == 0


# ---------------- кривые ответы Keitaro и служебное


def test_bad_json_is_502_not_500(client, kt, caplog):
    import httpx

    kt.fail("GET", "/offers", response=httpx.Response(200, content=b"<html>oops"))
    r = client.get("/api/offers")
    assert r.status_code == 502 and "не JSON" in r.json()["detail"]
    assert "ответ не JSON" in caplog.text


def test_unexpected_shape_is_502(client, kt, caplog):
    import httpx

    kt.fail("GET", "/offers", response=httpx.Response(200, json=[{"id": "x"}]))
    r = client.get("/api/offers")
    assert r.status_code == 502 and "формат" in r.json()["detail"]
    assert "неожиданный формат" in caplog.text


def test_healthz(client, make_client, session_factory):
    assert client.get("/healthz").json() == {"status": "ok", "db": "ok"}

    class Down(Session):
        def execute(self, *a, **kw):
            raise RuntimeError("down")

    assert make_client(sessionmaker(class_=Down)).get("/healthz").status_code == 503

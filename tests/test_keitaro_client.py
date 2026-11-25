import asyncio
import logging

import httpx
import pytest

from app.config import Settings
from app.keitaro import KeitaroClient, KeitaroError, build_http
from app.keitaro.schemas import StreamOffer

KEY = "super-secret-key"


def _client(handler):
    s = Settings(keitaro_url="https://kt.test/admin_api/v1/", keitaro_api_key=KEY, _env_file=None)
    return KeitaroClient(build_http(s, transport=httpx.MockTransport(handler)))


def _run(handler, call):
    async def go():
        kt = _client(handler)
        try:
            return await call(kt)
        finally:
            await kt._http.aclose()

    return asyncio.run(go())


def test_sends_api_key_and_base_url():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, str(req.url), req.headers["Api-Key"]))
        return httpx.Response(200, json=[{"id": 1, "name": "o", "country": None, "extra": 1}])

    offers = _run(handler, lambda kt: kt.offers())
    assert seen == [("GET", "https://kt.test/admin_api/v1/offers", KEY)]
    assert offers[0].country == [] and not hasattr(offers[0], "extra")


def test_key_is_secret_and_not_logged(caplog):
    caplog.set_level(logging.DEBUG)
    s = Settings(keitaro_api_key=KEY, _env_file=None)
    assert KEY not in repr(s) and KEY not in str(s.model_dump())
    with pytest.raises(KeitaroError):
        _run(lambda req: httpx.Response(500, text="boom"), lambda kt: kt.offers())
    assert "GET /offers -> 500" in caplog.text and "boom" in caplog.text
    assert KEY not in caplog.text


def test_success_logged_with_status_and_time(caplog):
    caplog.set_level(logging.INFO, logger="app.keitaro")
    _run(lambda req: httpx.Response(200, json=[]), lambda kt: kt.offers())
    assert any("GET /offers -> 200" in r.getMessage() and "ms" in r.getMessage() for r in caplog.records)


def test_http_error_with_json_detail():
    with pytest.raises(KeitaroError) as e:
        _run(lambda req: httpx.Response(404, json={"error": "Not found"}), lambda kt: kt.offer(1))
    assert (e.value.status, e.value.detail) == (404, {"error": "Not found"})


def test_http_error_with_text_detail():
    with pytest.raises(KeitaroError) as e:
        _run(lambda req: httpx.Response(500, text="oops"), lambda kt: kt.campaign(1))
    assert (e.value.status, e.value.detail) == (500, "oops")


def test_network_error_becomes_502():
    def handler(req):
        raise httpx.ConnectError("down", request=req)

    with pytest.raises(KeitaroError) as e:
        _run(handler, lambda kt: kt.offers())
    assert e.value.status == 502


@pytest.mark.parametrize("response", [httpx.Response(200, content=b"not json"), httpx.Response(200, json={"no": "id"})])
def test_bad_body_becomes_502(response):
    with pytest.raises(KeitaroError) as e:
        _run(lambda req: response, lambda kt: kt.campaign(1))
    assert e.value.status == 502


def test_empty_body_returns_none():
    assert _run(lambda req: httpx.Response(204), lambda kt: kt.delete_campaign(1)) is None


def test_put_offers_strips_service_fields_keeps_unknown():
    sent = {}

    def handler(req):
        import json

        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"id": 5, "offers": []})

    offer = StreamOffer.model_validate(
        {"id": 1, "stream_id": 5, "created_at": "x", "updated_at": "y", "offer_id": 7, "share": 100, "weight": 3}
    )
    _run(handler, lambda kt: kt.update_stream_offers(5, [offer]))
    assert sent == {"offers": [{"offer_id": 7, "share": 100, "state": "active", "weight": 3}]}


def test_bad_api_key_message(client, kt):
    kt.fail("GET", "/offers", 401, body={"error": "Unauthorized"})
    r = client.get("/api/offers")
    assert r.status_code == 502 and "KEITARO_API_KEY" in r.json()["detail"]

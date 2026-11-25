import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.keitaro import KeitaroClient, build_http
from app.keitaro.schemas import CampaignCreatePayload, StreamCreatePayload
from app.main import create_app


def _settings(**kw):
    return Settings(_env_file=None, keitaro_url="https://kt.test/admin_api/v1", database_url="sqlite://", **kw)


def test_import_has_no_side_effects():
    import app.db
    import app.main

    assert not hasattr(app.main, "app") and not hasattr(app.db, "engine")


def test_apps_are_independent(session_factory):
    a = create_app(_settings(campaign_prefix="a_"), session_factory=session_factory)
    b = create_app(_settings(campaign_prefix="b_"), session_factory=session_factory)
    try:
        assert a.state.settings.campaign_prefix == "a_" and b.state.settings.campaign_prefix == "b_"
        assert a.state.keitaro is not b.state.keitaro and a.state.campaigns_cache is not b.state.campaigns_cache
        assert a.state.references_cache is not b.state.references_cache
    finally:
        asyncio.run(a.state.keitaro._http.aclose())
        asyncio.run(b.state.keitaro._http.aclose())


def test_owned_resources_closed_on_shutdown():
    app = create_app(_settings())
    with TestClient(app) as c:
        assert c.get("/healthz").json()["db"] == "ok"  # своя SQLite в памяти
    assert app.state.keitaro._http.is_closed


def test_passed_client_is_not_closed(session_factory):
    kc = KeitaroClient(build_http(_settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))))
    with TestClient(create_app(_settings(), keitaro=kc, session_factory=session_factory)):
        pass
    assert not kc._http.is_closed
    asyncio.run(kc._http.aclose())


def test_payloads_reject_unknown_fields_and_bad_values():
    with pytest.raises(ValidationError):
        CampaignCreatePayload(name="x", domain_id=1, group_id=1, traffic_source_id=1, grup_id=2)
    with pytest.raises(ValidationError):
        CampaignCreatePayload(name="x", domain_id=1, group_id=1, traffic_source_id=1, cost_type="CPX")
    with pytest.raises(ValidationError):
        StreamCreatePayload(campaign_id=1, name="s", type="regular", position=1, schema="bogus")


def test_payload_json_uses_keitaro_names_and_skips_empty():
    p = StreamCreatePayload(campaign_id=1, name="s", type="default", position=2, schema="landings")
    assert p.to_json() == {"campaign_id": 1, "name": "s", "type": "default", "position": 2, "schema": "landings"}
    c = CampaignCreatePayload(name="x", domain_id=1, group_id=2, traffic_source_id=3, cost_type="CPC", cost_auto=True)
    assert c.to_json() == {
        "name": "x",
        "type": "position",
        "domain_id": 1,
        "group_id": 2,
        "traffic_source_id": 3,
        "state": "active",
        "cost_type": "CPC",
        "cost_auto": True,
    }

import json

import pytest
import responses

from ghl_kpi.client import BASE_URL, GHLClient, GHLError, parse_ts
from ghl_kpi.mock_api import MOCK_TOKEN, MockGHLSession


def test_contacts_paginate_through_all_pages(client, mock_session):
    contacts = list(client.contacts())
    assert len(contacts) == len(mock_session.data["contacts"]) == 180
    assert len({c["id"] for c in contacts}) == 180
    assert mock_session.log.count("POST /contacts/search") == 2  # 100 + 80


def test_opportunities_follow_cursor(client, mock_session):
    opps = list(client.opportunities())
    assert len(opps) == len(mock_session.data["opportunities"])
    assert len({o["id"] for o in opps}) == len(opps)
    assert mock_session.log.count("GET /opportunities/search") == 2


def test_retries_on_429_and_500_then_succeeds():
    sess = MockGHLSession(fail_plan=[429, 502])
    slept = []
    c = GHLClient(MOCK_TOKEN, sess.location_id, session=sess, sleep=slept.append)
    assert c.pipelines()[0]["name"] == "Sales Pipeline"
    assert len(slept) == 2 and c.calls == 3


def test_gives_up_after_max_retries():
    sess = MockGHLSession(fail_plan=[429] * 10)
    c = GHLClient(MOCK_TOKEN, sess.location_id, session=sess, sleep=lambda s: None, max_retries=2)
    with pytest.raises(GHLError) as e:
        c.pipelines()
    assert e.value.status == 429


def test_bad_token_raises_without_leaking_token():
    sess = MockGHLSession()
    c = GHLClient("pit-wrong-secret-value", sess.location_id, session=sess)
    with pytest.raises(GHLError) as e:
        c.pipelines()
    assert e.value.status == 401
    assert "pit-wrong-secret-value" not in str(e.value)
    assert "pit-wrong-secret-value" not in repr(c)


def test_missing_config_is_a_clear_error():
    with pytest.raises(ValueError):
        GHLClient("", "loc")
    with pytest.raises(ValueError):
        GHLClient("tok", "")


@responses.activate
def test_real_http_shape_headers_and_calendar_params():
    """Checks the exact wire format with the `responses` HTTP mock rather than the in-process fake."""
    responses.get(f"{BASE_URL}/calendars/", json={"calendars": [{"id": "cal_1"}]})
    responses.get(f"{BASE_URL}/calendars/events", json={"events": [{"id": "ev_1", "startTime": "2026-10-01T10:00:00Z"}]})
    c = GHLClient("tok", "loc_1")
    evs = list(c.appointments(parse_ts("2026-10-01T00:00:00Z"), parse_ts("2026-10-02T00:00:00Z")))
    assert evs == [{"id": "ev_1", "startTime": "2026-10-01T10:00:00Z", "calendarId": "cal_1"}]
    req = responses.calls[1].request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["Version"] == "2021-04-15"
    assert "startTime=1790812800000" in req.url and "calendarId=cal_1" in req.url


@responses.activate
def test_contacts_search_body_and_version():
    responses.post(f"{BASE_URL}/contacts/search", json={"contacts": [{"id": "c1"}], "total": 1})
    assert [c["id"] for c in GHLClient("tok", "loc_1").contacts()] == ["c1"]
    req = responses.calls[0].request
    assert json.loads(req.body) == {"locationId": "loc_1", "page": 1, "pageLimit": 100}
    assert req.headers["Version"] == "2021-07-28"


@responses.activate
def test_opportunity_cursor_that_does_not_move_stops():
    meta = {"startAfterId": "x", "startAfter": 1, "nextPageUrl": "u"}
    responses.get(f"{BASE_URL}/opportunities/search", json={"opportunities": [{"id": "x"}], "meta": meta})
    assert len(list(GHLClient("tok", "loc_1").opportunities())) == 2  # first page + one repeat, then stop


def test_first_outbound_message(client):
    assert parse_ts(client.first_outbound_message_at("ct_DEMO_0001")) is not None
    assert client.first_outbound_message_at("ct_DOES_NOT_EXIST") is None


@pytest.mark.parametrize("raw", ["2026-10-01T10:00:00.123Z", "2026-10-01T14:00:00+04:00", 1790848800000, "1790848800000"])
def test_parse_ts_formats(raw):
    assert parse_ts(raw).isoformat().startswith("2026-10-01T10:00:00")

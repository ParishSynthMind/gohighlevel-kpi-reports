from datetime import datetime, timedelta, timezone

import pytest

from ghl_kpi.client import GHLClient
from ghl_kpi.db import Appointment, Contact, Opportunity, OpportunitySnapshot, Stage, SyncRun
from ghl_kpi.kpis import compute_kpis, week_start
from ghl_kpi.mock_api import MOCK_TOKEN, MockGHLSession
from ghl_kpi.sync import run_sync

ANCHOR = datetime(2026, 10, 30, 17, 0, tzinfo=timezone.utc)


def test_sync_stores_everything_and_is_idempotent(client, Session):
    r1 = run_sync(client, Session, with_conversations=True, now=ANCHOR)
    assert r1.status == "ok"
    assert r1.counts["contacts"] == 180 and r1.counts["opportunities"] == 140 and r1.counts["stages"] == 5
    assert r1.counts["first_replies_found"] > 100
    r2 = run_sync(client, Session, now=ANCHOR + timedelta(days=1))
    with Session() as s:
        assert s.query(Contact).count() == 180
        assert s.query(Opportunity).count() == 140
        assert s.query(OpportunitySnapshot).count() == 280  # one per opportunity per sync
        # speed-to-lead results survive a later sync that doesn't re-check conversations
        assert s.query(Contact).filter(Contact.first_outbound_at.isnot(None)).count() == r1.counts["first_replies_found"]
        assert [r.status for r in s.query(SyncRun).order_by(SyncRun.id)] == ["ok", "ok"]


def test_failed_sync_is_recorded(Session):
    sess = MockGHLSession(fail_plan=[500] * 20)
    c = GHLClient(MOCK_TOKEN, sess.location_id, session=sess, sleep=lambda s: None, max_retries=1)
    with pytest.raises(Exception):
        run_sync(c, Session, now=ANCHOR)
    with Session() as s:
        run = s.query(SyncRun).one()
        assert run.status == "failed" and "500" in run.error


def _seed(Session):
    now = ANCHOR
    with Session() as s:
        for i, name in enumerate(["New", "Contacted", "Won-ready"]):
            s.add(Stage(id=f"s{i}", pipeline_id="p", pipeline_name="P", name=name, position=i))
        # 3 leads this week (2 Facebook, 1 Google), 1 last week
        for cid, src, days_ago, reply_min in [("a", "Facebook", 1, 3), ("b", "Facebook", 2, 30),
                                              ("c", "Google", 3, None), ("d", "Google", 9, 120)]:
            added = now - timedelta(days=days_ago)
            s.add(Contact(id=cid, location_id="l", source=src, date_added=added,
                          first_outbound_at=added + timedelta(minutes=reply_min) if reply_min else None))
        s.add_all([
            Opportunity(id="o1", contact_id="a", pipeline_id="p", stage_id="s2", status="won", monetary_value=1000,
                        created_at=now - timedelta(days=1), last_status_change_at=now - timedelta(hours=5)),
            Opportunity(id="o2", contact_id="b", pipeline_id="p", stage_id="s1", status="lost", monetary_value=500,
                        created_at=now - timedelta(days=2), last_status_change_at=now - timedelta(hours=6)),
            Opportunity(id="o3", contact_id="c", pipeline_id="p", stage_id="s0", status="open", monetary_value=250,
                        created_at=now - timedelta(days=3)),
            Opportunity(id="o4", contact_id="d", pipeline_id="p", stage_id="s0", status="open", monetary_value=750,
                        created_at=now - timedelta(days=9)),
        ])
        # o4 was seen at stage s1 in an earlier sync, then moved back: it still "reached" s1
        s.add(SyncRun(id=1, location_id="l", started_at=now, status="ok"))
        s.flush()  # the snapshot's foreign key needs the run row first (Postgres enforces it, SQLite doesn't)
        s.add(OpportunitySnapshot(run_id=1, opportunity_id="o4", stage_id="s1", status="open", captured_at=now - timedelta(days=5)))
        s.add_all([
            Appointment(id="e1", contact_id="a", status="showed", start_time=now - timedelta(days=1)),
            Appointment(id="e2", contact_id="b", status="noshow", start_time=now - timedelta(days=1)),
            Appointment(id="e3", contact_id="c", status="confirmed", start_time=now + timedelta(days=2)),
        ])
        s.commit()


def test_kpis_exact_numbers(Session):
    _seed(Session)
    with Session() as s:
        k = compute_kpis(s, ANCHOR)
    h = k["headline"]
    assert h["new_leads_7d"] == 3 and h["new_leads_prev_7d"] == 1
    assert h["open_pipeline_value"] == 1000 and h["open_opportunities"] == 2
    assert h["win_rate_window_pct"] == 50.0 and h["won_value_7d"] == 1000
    assert h["show_rate_pct"] == 50.0 and h["upcoming_appointments_7d"] == 1
    assert h["lead_to_appointment_pct"] == 75.0  # a, b, c booked out of 4
    assert k["leads_weekly"][-1] == {"week": week_start(ANCHOR).date().isoformat(), "leads": 3}
    assert k["sources"][0] == {"source": "Facebook", "leads": 2, "won": 1, "win_rate_pct": 50.0}
    stages = k["pipelines"][0]["stages"]
    assert [r["reached"] for r in stages] == [4, 3, 1]  # o1 won, o2 lost at s1, o4 via snapshot
    assert stages[1]["conversion_from_prev_pct"] == 75.0
    sp = k["speed_to_lead"]
    assert sp["measured_leads"] == 3 and sp["median_minutes"] == 30.0 and sp["within_5_min_pct"] == 33.3
    assert [b["leads"] for b in sp["buckets"]] == [1, 1, 1, 0, 1]


def test_kpis_ignore_future_data(Session):
    _seed(Session)
    with Session() as s:
        k = compute_kpis(s, ANCHOR - timedelta(days=4))
    assert k["headline"]["new_leads_7d"] == 1  # only d is older than 4 days


def test_empty_database_gives_nulls_not_crashes(Session):
    with Session() as s:
        k = compute_kpis(s, ANCHOR)
    assert k["headline"]["new_leads_7d"] == 0
    assert k["headline"]["win_rate_window_pct"] is None
    assert k["speed_to_lead"]["median_minutes"] is None

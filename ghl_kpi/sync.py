"""Pull everything from GHL into the database in one sync run."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import sessionmaker

from .client import GHLClient, parse_ts
from .db import Appointment, Contact, Opportunity, OpportunitySnapshot, Stage, SyncRun, now_utc

log = logging.getLogger(__name__)

APPT_DAYS_BACK = 120
APPT_DAYS_FORWARD = 30
SPEED_CHECK_MAX_PER_RUN = 200  # each check is 2+ API calls; keep well under the rate limit
SPEED_CHECK_GIVE_UP_DAYS = 7    # stop re-checking a contact that still has no reply after a week


def _name(c: dict) -> Optional[str]:
    full = " ".join(p for p in [c.get("firstName"), c.get("lastName")] if p)
    return full or c.get("contactName") or c.get("name")


def run_sync(client: GHLClient, Session: sessionmaker, with_conversations: bool = False,
             now: Optional[datetime] = None) -> SyncRun:
    now = now or now_utc()
    with Session() as s:
        run = SyncRun(location_id=client.location_id, started_at=now_utc(), status="running", counts={})
        s.add(run)
        s.commit()
        run_id = run.id

    counts = {}
    try:
        with Session() as s:
            # stages
            n = 0
            for p in client.pipelines():
                for i, st in enumerate(p.get("stages") or []):
                    s.merge(Stage(id=st["id"], pipeline_id=p["id"], pipeline_name=p.get("name", ""),
                                  name=st.get("name", st["id"]), position=st.get("position", i)))
                    n += 1
            counts["stages"] = n

            # contacts
            n = 0
            for c in client.contacts():
                existing = s.get(Contact, c["id"])
                row = Contact(id=c["id"], location_id=client.location_id, name=_name(c), email=c.get("email"),
                              source=c.get("source") or (c.get("attributionSource") or {}).get("sessionSource"),
                              tags=c.get("tags") or [], date_added=parse_ts(c.get("dateAdded")))
                if existing:
                    row.first_outbound_at = existing.first_outbound_at
                    row.speed_checked = existing.speed_checked
                s.merge(row)
                n += 1
            counts["contacts"] = n

            # opportunities + snapshot
            n = 0
            for o in client.opportunities():
                s.merge(Opportunity(
                    id=o["id"], contact_id=o.get("contactId") or (o.get("contact") or {}).get("id"),
                    pipeline_id=o.get("pipelineId"), stage_id=o.get("pipelineStageId"), status=o.get("status"),
                    monetary_value=float(o.get("monetaryValue") or 0), source=o.get("source"),
                    created_at=parse_ts(o.get("createdAt")), last_stage_change_at=parse_ts(o.get("lastStageChangeAt")),
                    last_status_change_at=parse_ts(o.get("lastStatusChangeAt"))))
                s.add(OpportunitySnapshot(run_id=run_id, opportunity_id=o["id"], stage_id=o.get("pipelineStageId"),
                                          status=o.get("status"), monetary_value=float(o.get("monetaryValue") or 0),
                                          captured_at=now))
                n += 1
            counts["opportunities"] = n

            # appointments
            n = 0
            for ev in client.appointments(now - timedelta(days=APPT_DAYS_BACK), now + timedelta(days=APPT_DAYS_FORWARD)):
                s.merge(Appointment(id=ev["id"], calendar_id=ev.get("calendarId"), contact_id=ev.get("contactId"),
                                    title=ev.get("title"), status=ev.get("appointmentStatus") or ev.get("status"),
                                    start_time=parse_ts(ev.get("startTime")), date_added=parse_ts(ev.get("dateAdded"))))
                n += 1
            counts["appointments"] = n
            s.commit()

            # speed to lead (optional: needs conversations read scopes, costs 2+ calls per contact)
            if with_conversations:
                todo = (s.query(Contact).filter(Contact.speed_checked.is_(False))
                        .order_by(Contact.date_added.desc()).limit(SPEED_CHECK_MAX_PER_RUN).all())
                found = 0
                for c in todo:
                    first = client.first_outbound_message_at(c.id)
                    if first:
                        c.first_outbound_at = parse_ts(first)
                        c.speed_checked = True
                        found += 1
                    elif c.date_added and now - c.date_added > timedelta(days=SPEED_CHECK_GIVE_UP_DAYS):
                        c.speed_checked = True
                counts["speed_checked"] = len(todo)
                counts["first_replies_found"] = found
                s.commit()

        with Session() as s:
            run = s.get(SyncRun, run_id)
            run.status, run.finished_at, run.counts, run.api_calls = "ok", now_utc(), counts, client.calls
            s.commit()
            return run
    except Exception as exc:
        with Session() as s:
            run = s.get(SyncRun, run_id)
            run.status, run.finished_at, run.counts, run.api_calls = "failed", now_utc(), counts, client.calls
            run.error = f"{type(exc).__name__}: {exc}"[:2000]
            s.commit()
        raise

"""KPI maths over the stored snapshot. Pure functions of the database plus an as-of date."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from .db import Appointment, Contact, Opportunity, OpportunitySnapshot, Stage

WEEKS_SHOWN = 8


def week_start(dt: datetime) -> datetime:
    d = dt.astimezone(timezone.utc)
    d = d - timedelta(days=d.weekday())
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _pct(num: float, den: float) -> Optional[float]:
    return round(100.0 * num / den, 1) if den else None


def compute_kpis(s: Session, as_of: datetime, weeks: int = WEEKS_SHOWN) -> Dict:
    as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    this_week = week_start(as_of)
    weeks_list = [this_week - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    window_start = weeks_list[0]
    last_7 = as_of - timedelta(days=7)
    prev_7 = as_of - timedelta(days=14)

    contacts = [c for c in s.query(Contact).all() if c.date_added and c.date_added <= as_of]
    opps = [o for o in s.query(Opportunity).all() if not o.created_at or o.created_at <= as_of]
    stages = s.query(Stage).order_by(Stage.pipeline_id, Stage.position).all()
    appts = s.query(Appointment).all()

    # ---- new leads per week ----
    per_week = Counter(week_start(c.date_added) for c in contacts if c.date_added >= window_start)
    leads_weekly = [{"week": w.date().isoformat(), "leads": per_week.get(w, 0)} for w in weeks_list]
    leads_7 = sum(1 for c in contacts if c.date_added > last_7)
    leads_prev_7 = sum(1 for c in contacts if prev_7 < c.date_added <= last_7)

    # ---- lead sources (window) with conversion to won ----
    won_contacts = {o.contact_id for o in opps if o.status == "won"}
    src = defaultdict(lambda: {"leads": 0, "won": 0})
    for c in contacts:
        if c.date_added >= window_start:
            key = c.source or "Unknown"
            src[key]["leads"] += 1
            src[key]["won"] += 1 if c.id in won_contacts else 0
    sources = sorted(({"source": k, **v, "win_rate_pct": _pct(v["won"], v["leads"])} for k, v in src.items()),
                     key=lambda r: -r["leads"])

    # ---- pipeline, per pipeline ----
    pos = {st.id: st.position for st in stages}
    furthest: Dict[str, int] = {}
    for snap in s.query(OpportunitySnapshot).filter(OpportunitySnapshot.captured_at <= as_of).all():
        if snap.stage_id in pos:
            furthest[snap.opportunity_id] = max(furthest.get(snap.opportunity_id, -1), pos[snap.stage_id])
    pipelines: List[Dict] = []
    for pid in dict.fromkeys(st.pipeline_id for st in stages):
        p_stages = [st for st in stages if st.pipeline_id == pid]
        p_opps = [o for o in opps if o.pipeline_id == pid]
        rows = []
        for st in p_stages:
            at_stage_open = [o for o in p_opps if o.stage_id == st.id and o.status == "open"]
            reached = sum(1 for o in p_opps
                          if max(furthest.get(o.id, -1), pos.get(o.stage_id, -1)) >= st.position
                          or o.status == "won")
            rows.append({"stage": st.name, "open_count": len(at_stage_open),
                         "open_value": round(sum(o.monetary_value for o in at_stage_open), 2), "reached": reached})
        for i, r in enumerate(rows):
            r["conversion_from_prev_pct"] = None if i == 0 else _pct(r["reached"], rows[i - 1]["reached"])
        pipelines.append({"pipeline": p_stages[0].pipeline_name, "stages": rows})

    open_opps = [o for o in opps if o.status == "open"]
    closed_window = [o for o in opps if o.status in ("won", "lost") and o.last_status_change_at
                     and window_start <= o.last_status_change_at <= as_of]
    won_window = [o for o in closed_window if o.status == "won"]
    won_7 = [o for o in opps if o.status == "won" and o.last_status_change_at and o.last_status_change_at > last_7]

    # ---- appointments ----
    appts_past = [a for a in appts if a.start_time and window_start <= a.start_time <= as_of]
    appt_weekly_c = Counter(week_start(a.start_time) for a in appts_past)
    appt_weekly = [{"week": w.date().isoformat(), "appointments": appt_weekly_c.get(w, 0)} for w in weeks_list]
    showed = sum(1 for a in appts_past if (a.status or "").lower() == "showed")
    noshow = sum(1 for a in appts_past if (a.status or "").lower() in ("noshow", "no_show", "no-show"))
    upcoming = sum(1 for a in appts if a.start_time and as_of < a.start_time <= as_of + timedelta(days=7)
                   and (a.status or "").lower() not in ("cancelled", "invalid"))
    window_contacts = {c.id for c in contacts if c.date_added >= window_start}
    booked_contacts = {a.contact_id for a in appts if a.contact_id in window_contacts}

    # ---- speed to lead ----
    gaps = [(c.first_outbound_at - c.date_added).total_seconds() / 60 for c in contacts
            if c.first_outbound_at and c.date_added >= window_start and c.first_outbound_at >= c.date_added]
    speed = {
        "measured_leads": len(gaps),
        "window_leads": len(window_contacts),
        "median_minutes": round(median(gaps), 1) if gaps else None,
        "within_5_min_pct": _pct(sum(1 for g in gaps if g <= 5), len(gaps)),
        "over_1_hour_pct": _pct(sum(1 for g in gaps if g > 60), len(gaps)),
        "buckets": [
            {"bucket": "Under 5 min", "leads": sum(1 for g in gaps if g <= 5)},
            {"bucket": "5 to 60 min", "leads": sum(1 for g in gaps if 5 < g <= 60)},
            {"bucket": "1 to 24 hours", "leads": sum(1 for g in gaps if 60 < g <= 1440)},
            {"bucket": "Over 24 hours", "leads": sum(1 for g in gaps if g > 1440)},
            {"bucket": "No reply found", "leads": len(window_contacts) - len(gaps)},
        ],
    }

    return {
        "as_of": as_of.isoformat(),
        "window_start": window_start.date().isoformat(),
        "weeks": weeks,
        "headline": {
            "new_leads_7d": leads_7,
            "new_leads_prev_7d": leads_prev_7,
            "new_leads_window": len(window_contacts),
            "open_pipeline_value": round(sum(o.monetary_value for o in open_opps), 2),
            "open_opportunities": len(open_opps),
            "won_value_7d": round(sum(o.monetary_value for o in won_7), 2),
            "won_count_window": len(won_window),
            "won_value_window": round(sum(o.monetary_value for o in won_window), 2),
            "win_rate_window_pct": _pct(len(won_window), len(closed_window)),
            "lead_to_appointment_pct": _pct(len(booked_contacts), len(window_contacts)),
            "show_rate_pct": _pct(showed, showed + noshow),
            "upcoming_appointments_7d": upcoming,
            "median_speed_to_lead_min": speed["median_minutes"],
        },
        "leads_weekly": leads_weekly,
        "sources": sources,
        "pipelines": pipelines,
        "appointments_weekly": appt_weekly,
        "speed_to_lead": speed,
    }

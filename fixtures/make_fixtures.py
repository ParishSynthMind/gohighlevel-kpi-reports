"""Generate fixtures/ghl_demo.json: a fictional sub-account shaped like real GHL API v2 responses.

Seeded, so the file is reproducible. Every name is "Test Lead NNN" and every email is @example.com.
Run: python fixtures/make_fixtures.py
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20261101
ANCHOR = datetime(2026, 10, 30, 17, 0, tzinfo=timezone.utc)  # "today" for the fixture data
WEEKS = 12
LOCATION = "loc_DEMO0000000000001"

SOURCES = [  # (source, weight, chance an opportunity is won)
    ("Facebook Ads", 30, 0.14),
    ("Google Ads", 22, 0.22),
    ("Website Form", 18, 0.25),
    ("Referral", 8, 0.45),
    ("Instagram", 12, 0.10),
    ("Organic Search", 10, 0.20),
]
STAGES = ["New Lead", "Contacted", "Qualified", "Proposal Sent", "Negotiation"]
PIPELINE_ID = "pl_DEMO_SALES"


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def main():
    rng = random.Random(SEED)
    stages = [{"id": f"st_DEMO_{i+1}", "name": n, "position": i} for i, n in enumerate(STAGES)]
    pipelines = [{"id": PIPELINE_ID, "name": "Sales Pipeline", "locationId": LOCATION, "stages": stages,
                  "showInFunnel": True, "showInPieChart": True}]

    contacts, opportunities, events, messages = [], [], [], {}
    start = ANCHOR - timedelta(weeks=WEEKS)
    n = 0
    for week in range(WEEKS):
        # gentle growth plus noise, so the trend chart has something to say
        weekly = int(10 + week * 0.9 + rng.randint(-3, 4))
        for _ in range(weekly):
            n += 1
            added = start + timedelta(weeks=week, seconds=rng.randint(0, 7 * 86400 - 1))
            if added > ANCHOR:
                continue
            source = rng.choices([s[0] for s in SOURCES], weights=[s[1] for s in SOURCES])[0]
            win_p = dict((s[0], s[2]) for s in SOURCES)[source]
            cid = f"ct_DEMO_{n:04d}"
            contacts.append({
                "id": cid, "locationId": LOCATION,
                "firstName": "Test", "lastName": f"Lead {n:03d}",
                "firstNameLowerCase": "test", "lastNameLowerCase": f"lead {n:03d}",
                "email": f"test.lead{n:03d}@example.com", "phone": None,
                "source": source, "tags": [source.lower().replace(" ", "-")],
                "dateAdded": iso(added), "dateUpdated": iso(added + timedelta(hours=2)),
            })
            # speed to lead: most get a reply inside an hour, a tail takes much longer, some never
            r = rng.random()
            if r < 0.85:
                mins = rng.choice([rng.uniform(1, 5), rng.uniform(5, 60), rng.uniform(60, 60 * 24)]) if r > 0.3 else rng.uniform(1, 5)
                first = added + timedelta(minutes=mins)
                if first < ANCHOR:
                    messages[cid] = [
                        {"id": f"msg_{cid}_in", "direction": "inbound", "dateAdded": iso(added + timedelta(seconds=5)), "messageType": "TYPE_SMS"},
                        {"id": f"msg_{cid}_out", "direction": "outbound", "dateAdded": iso(first), "messageType": "TYPE_SMS"},
                    ]
            if rng.random() > 0.75:
                continue  # not every contact becomes an opportunity
            # how far it got
            furthest = 0
            while furthest < len(STAGES) - 1 and rng.random() < 0.62:
                furthest += 1
            age_days = (ANCHOR - added).days
            status = "open"
            if age_days > 10 and rng.random() < 0.6:
                status = "won" if rng.random() < win_p * (1 + furthest) else "lost"
            value = rng.choice([1500, 2500, 3500, 5000, 7500, 12000])
            created = added + timedelta(hours=rng.uniform(0.2, 30))
            changed = min(created + timedelta(days=rng.uniform(1, max(1.5, age_days * 0.8))), ANCHOR)
            opportunities.append({
                "id": f"op_DEMO_{n:04d}", "name": f"Test Lead {n:03d} - Website rebuild",
                "monetaryValue": value, "pipelineId": PIPELINE_ID, "pipelineStageId": stages[furthest]["id"],
                "assignedTo": "usr_DEMO_1", "status": status, "source": source,
                "lastStatusChangeAt": iso(changed if status != "open" else created),
                "lastStageChangeAt": iso(changed), "createdAt": iso(created), "updatedAt": iso(changed),
                "contactId": cid, "locationId": LOCATION,
                "contact": {"id": cid, "name": f"Test Lead {n:03d}", "email": f"test.lead{n:03d}@example.com", "tags": []},
            })
            if furthest >= 2 or rng.random() < 0.25:
                appt_start = added + timedelta(days=rng.uniform(1, 9))
                appt_start = appt_start.replace(minute=0, second=0, microsecond=0)
                status_a = "confirmed" if appt_start > ANCHOR else rng.choices(["showed", "noshow", "cancelled"], [0.72, 0.18, 0.10])[0]
                events.append({
                    "id": f"ev_DEMO_{n:04d}", "calendarId": "cal_DEMO_DISCOVERY", "locationId": LOCATION,
                    "contactId": cid, "title": f"Discovery call - Test Lead {n:03d}",
                    "appointmentStatus": status_a,
                    "startTime": iso(appt_start), "endTime": iso(appt_start + timedelta(minutes=30)),
                    "dateAdded": iso(added + timedelta(hours=rng.uniform(0.5, 20))),
                })

    data = {
        "_about": "Fictional demo data shaped like GoHighLevel API v2 responses. No real people or accounts.",
        "anchor": iso(ANCHOR), "locationId": LOCATION,
        "pipelines": pipelines,
        "calendars": [{"id": "cal_DEMO_DISCOVERY", "name": "Discovery Call", "locationId": LOCATION}],
        "contacts": contacts, "opportunities": opportunities, "events": events, "messages": messages,
    }
    out = Path(__file__).with_name("ghl_demo.json")
    out.write_text(json.dumps(data, indent=1))
    print(f"wrote {out.name}: {len(contacts)} contacts, {len(opportunities)} opportunities, "
          f"{len(events)} appointments, {len(messages)} conversations")


if __name__ == "__main__":
    main()

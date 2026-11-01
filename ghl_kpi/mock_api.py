"""An in-process stand-in for the GHL API, served from fixtures/ghl_demo.json.

It behaves like the real endpoints this job calls: bearer token and Version
header required, page-numbered contact search, startAfter/startAfterId cursors
on opportunities, calendar events filtered by calendar and time window.
Used by `--mock` runs and by the tests, so nothing needs an account.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from .client import parse_ts

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "ghl_demo.json"
MOCK_TOKEN = "pit-mock-token"


class MockResponse:
    def __init__(self, status: int, body: Dict, headers: Optional[Dict] = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class MockGHLSession:
    def __init__(self, fixture_path: Path = FIXTURE, token: str = MOCK_TOKEN, fail_plan: Optional[List[int]] = None):
        self.data = json.loads(Path(fixture_path).read_text())
        self.token = token
        self.fail_plan = list(fail_plan or [])  # status codes to return before serving normally
        self.log: List[str] = []

    @property
    def location_id(self) -> str:
        return self.data["locationId"]

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        path = urlparse(url).path
        params = params or {}
        self.log.append(f"{method} {path}")
        headers = headers or {}
        if self.fail_plan:
            code = self.fail_plan.pop(0)
            return MockResponse(code, {"message": "simulated failure"}, {"Retry-After": "0"})
        if headers.get("Authorization") != f"Bearer {self.token}":
            return MockResponse(401, {"message": "Invalid JWT"})
        if not headers.get("Version"):
            return MockResponse(400, {"message": "Version header was not found"})

        d = self.data
        loc = params.get("locationId") or params.get("location_id") or (json or {}).get("locationId")
        scoped_by_id = path.startswith("/conversations/") and path.endswith("/messages")
        if not scoped_by_id and loc != d["locationId"]:
            return MockResponse(403, {"message": "The token does not have access to this location"})

        if method == "GET" and path == "/opportunities/pipelines":
            return MockResponse(200, {"pipelines": d["pipelines"]})

        if method == "POST" and path == "/contacts/search":
            page, size = int(json.get("page", 1)), int(json.get("pageLimit", 20))
            rows = d["contacts"][(page - 1) * size: page * size]
            return MockResponse(200, {"contacts": rows, "total": len(d["contacts"])})

        if method == "GET" and path == "/opportunities/search":
            limit = min(int(params.get("limit", 20)), 100)
            rows = sorted(d["opportunities"], key=lambda o: (o["createdAt"], o["id"]))
            after_id = params.get("startAfterId")
            if after_id:
                idx = next(i for i, o in enumerate(rows) if o["id"] == after_id)
                rows = rows[idx + 1:]
            page = rows[:limit]
            more = len(rows) > limit
            last = page[-1] if page else None
            meta = {
                "total": len(d["opportunities"]),
                "startAfterId": last["id"] if (last and more) else None,
                "startAfter": int(parse_ts(last["createdAt"]).timestamp() * 1000) if (last and more) else None,
                "nextPageUrl": "https://services.leadconnectorhq.com/opportunities/search?..." if more else None,
            }
            return MockResponse(200, {"opportunities": page, "meta": meta})

        if method == "GET" and path == "/calendars/":
            return MockResponse(200, {"calendars": d["calendars"]})

        if method == "GET" and path == "/calendars/events":
            if not params.get("calendarId"):
                return MockResponse(422, {"message": ["userId, groupId or calendarId is required"]})
            lo, hi = int(params["startTime"]), int(params["endTime"])
            rows = [e for e in d["events"] if e["calendarId"] == params["calendarId"]
                    and lo <= parse_ts(e["startTime"]).timestamp() * 1000 <= hi]
            return MockResponse(200, {"events": rows})

        if method == "GET" and path == "/conversations/search":
            cid = params.get("contactId")
            convs = [{"id": f"cv_{cid}", "contactId": cid}] if cid in d["messages"] else []
            return MockResponse(200, {"conversations": convs, "total": len(convs)})

        if method == "GET" and path.startswith("/conversations/") and path.endswith("/messages"):
            cid = path.split("/")[2][3:]
            return MockResponse(200, {"messages": {"messages": d["messages"].get(cid, []), "nextPage": False}})

        return MockResponse(404, {"message": f"mock has no route for {method} {path}"})

"""Read-only GoHighLevel API v2 client (services.leadconnectorhq.com).

Only the calls this job needs: contacts, pipelines, opportunities, calendars,
appointments and (optionally) conversation messages for speed to lead.
Retries on 429 and 5xx, follows each endpoint's own pagination style, and
never logs the token.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

import requests

BASE_URL = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
CALENDAR_VERSION = "2021-04-15"  # the calendars endpoints document this version
PAGE_SIZE = 100
MAX_PAGES = 500  # hard stop so a pagination bug can never loop forever


class GHLError(RuntimeError):
    def __init__(self, status: int, path: str, message: str):
        super().__init__(f"GHL API {status} on {path}: {message}")
        self.status = status
        self.path = path


class GHLClient:
    def __init__(
        self,
        token: str,
        location_id: str,
        session: Optional[Any] = None,
        base_url: str = BASE_URL,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ):
        if not token:
            raise ValueError("GHL token is missing (set GHL_TOKEN)")
        if not location_id:
            raise ValueError("GHL location ID is missing (set GHL_LOCATION_ID)")
        self._token = token
        self.location_id = location_id
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.sleep = sleep
        self.timeout = timeout
        self.calls = 0

    def __repr__(self) -> str:  # keep the token out of any traceback or log
        return f"GHLClient(location_id={self.location_id!r})"

    # ---------- transport ----------

    def _request(self, method: str, path: str, params=None, json=None, version: str = VERSION) -> Dict:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Version": version,
            "Accept": "application/json",
        }
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            self.calls += 1
            resp = self.session.request(method, url, params=params, json=json, headers=headers, timeout=self.timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    break
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 2 ** attempt
                self.sleep(min(wait, 30.0))
                continue
            if resp.status_code >= 400:
                raise GHLError(resp.status_code, path, _error_message(resp))
            return resp.json() if resp.content else {}
        raise GHLError(resp.status_code, path, "gave up after retries: " + _error_message(resp))

    # ---------- endpoints ----------

    def pipelines(self) -> List[Dict]:
        data = self._request("GET", "/opportunities/pipelines", params={"locationId": self.location_id})
        return data.get("pipelines", [])

    def contacts(self) -> Iterator[Dict]:
        """POST /contacts/search, page-numbered."""
        page = 1
        seen = 0
        while page <= MAX_PAGES:
            body = {"locationId": self.location_id, "page": page, "pageLimit": PAGE_SIZE}
            data = self._request("POST", "/contacts/search", json=body)
            batch = data.get("contacts", [])
            for c in batch:
                yield c
            seen += len(batch)
            total = data.get("total")
            if not batch or len(batch) < PAGE_SIZE or (total is not None and seen >= total):
                return
            page += 1

    def opportunities(self) -> Iterator[Dict]:
        """GET /opportunities/search with startAfter/startAfterId cursors from meta."""
        params: Dict[str, Any] = {"location_id": self.location_id, "status": "all", "limit": PAGE_SIZE}
        for _ in range(MAX_PAGES):
            data = self._request("GET", "/opportunities/search", params=params)
            batch = data.get("opportunities", [])
            for o in batch:
                yield o
            meta = data.get("meta") or {}
            if not batch or not meta.get("startAfterId") or not meta.get("nextPageUrl"):
                return
            if meta.get("startAfterId") == params.get("startAfterId"):
                return  # cursor did not move; stop rather than loop
            params = dict(params, startAfter=meta.get("startAfter"), startAfterId=meta.get("startAfterId"))

    def calendars(self) -> List[Dict]:
        data = self._request("GET", "/calendars/", params={"locationId": self.location_id}, version=CALENDAR_VERSION)
        return data.get("calendars", [])

    def appointments(self, start: datetime, end: datetime) -> Iterator[Dict]:
        """GET /calendars/events per calendar. The API needs a calendar, user or group filter."""
        for cal in self.calendars():
            params = {
                "locationId": self.location_id,
                "calendarId": cal["id"],
                "startTime": _ms(start),
                "endTime": _ms(end),
            }
            data = self._request("GET", "/calendars/events", params=params, version=CALENDAR_VERSION)
            for ev in data.get("events", []):
                ev.setdefault("calendarId", cal["id"])
                yield ev

    def first_outbound_message_at(self, contact_id: str) -> Optional[str]:
        """Earliest outbound message to a contact, for speed to lead. Needs conversations read scopes."""
        data = self._request("GET", "/conversations/search", params={"locationId": self.location_id, "contactId": contact_id})
        earliest: Optional[str] = None
        for conv in data.get("conversations", []):
            msgs = self._request("GET", f"/conversations/{conv['id']}/messages", params={"limit": 100})
            box = msgs.get("messages", {})
            items = box.get("messages", []) if isinstance(box, dict) else box
            for m in items:
                if not is_real_outbound(m):
                    continue
                if m.get("dateAdded"):
                    if earliest is None or parse_ts(m["dateAdded"]) < parse_ts(earliest):
                        earliest = m["dateAdded"]
        return earliest


# GHL writes system events into the conversation as "outbound" messages, e.g.
# TYPE_ACTIVITY_OPPORTUNITY "Opportunity created". They are not a reply to the lead.
NOT_A_REPLY_TYPES = {"TYPE_NO_SHOW"}


def is_real_outbound(m: Dict) -> bool:
    mtype = str(m.get("messageType") or "")
    return (m.get("direction") == "outbound" and "ACTIVITY" not in mtype
            and mtype not in NOT_A_REPLY_TYPES)


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _error_message(resp) -> str:
    try:
        body = resp.json()
        msg = body.get("message") or body.get("error") or body
        if isinstance(msg, list):
            msg = "; ".join(map(str, msg))
        return str(msg)[:300]
    except Exception:
        return (resp.text or "")[:300]


def parse_ts(value: Any) -> Optional[datetime]:
    """GHL returns ISO strings in most places and epoch millis in a few. Normalise to aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    s = str(value)
    if s.isdigit():
        return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

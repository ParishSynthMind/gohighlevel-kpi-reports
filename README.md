# GoHighLevel KPI Reports

A scheduled Python job that pulls contacts, pipeline stages, opportunities and appointments out of a GoHighLevel sub-account through the GHL API v2, keeps snapshots in a database (SQLite or Postgres), works out the numbers an agency owner actually asks for, has Claude write a short plain-English summary, and produces a PDF report plus a one-file HTML dashboard. If a sync fails or stops running, it sends an alert instead of going quiet.

![Dashboard built from the demo data](media/dashboard-light.png)

*The dashboard from the bundled demo data: 180 fictional leads over 12 weeks. The PDF report carries the same numbers ([page 1](media/pdf-report-page1.png)); the dashboard also has a [dark mode](media/dashboard-dark.png).*

## What it reports

| KPI | How it is counted |
|---|---|
| New leads per week | Contacts by `dateAdded`, weeks starting Monday, last 8 weeks, latest week to date |
| Lead source breakdown | Leads per source in the window, how many became a won deal, lead-to-won rate |
| Stage conversion | Deals that reached each stage and the conversion from the stage before. "Reached" uses the furthest stage a deal has been seen at across all syncs, so a deal moved backwards still counts |
| Pipeline value | Open value per stage, total open value, won value in the last 7 days and the window |
| Win rate | Won / (won + lost) for deals closed inside the window |
| Appointments | Per week, lead-to-appointment rate, show rate, booked for the next 7 days |
| Speed to lead | Minutes from contact created to the first outbound message (from Conversations), median, % inside 5 minutes, % over an hour, and leads with no reply found |

## How it works

```mermaid
flowchart LR
    S[Scheduler<br/>launchd / cron] --> A[ghl_kpi run]
    A -->|read-only token| G[(GHL API v2<br/>services.leadconnectorhq.com)]
    G -->|contacts, pipelines,<br/>opportunities, calendars,<br/>events, conversations| A
    A --> D[(SQLite or Postgres<br/>via SQLAlchemy)]
    D --> K[KPI maths]
    K -->|numbers only| C[Claude<br/>weekly summary]
    K --> P[PDF report]
    K --> H[HTML dashboard]
    C --> P & H
    A -. failure, empty sync,<br/>or sync gone stale .-> X[Alert: email / webhook]
```

1. **Sync** (`python -m ghl_kpi sync`) reads every contact (`POST /contacts/search`, page by page), the pipelines and their stages, every opportunity (`GET /opportunities/search`, following the `startAfter`/`startAfterId` cursor), each calendar's events for the last 120 days and next 30, and optionally the first outbound message per new contact. Current state is upserted; one snapshot row per opportunity per sync builds up stage history the API itself doesn't keep.
2. **Report** (`python -m ghl_kpi report`) computes the KPIs, asks Claude for a summary, and writes `ghl-kpi-report-<date>.pdf`, `dashboard.html` and `kpis.json`. With `--email` it sends the PDF and dashboard over SMTP.
3. **Check** (`python -m ghl_kpi check`) exits non-zero and alerts if the last sync failed or the last good one is older than `STALE_AFTER_HOURS`.

## Design choices

- **Read-only on purpose.** The job only ever reads. The GHL Private Integration needs just `contacts.readonly`, `opportunities.readonly`, `calendars.readonly`, `calendars/events.readonly`, plus `conversations.readonly` and `conversations/message.readonly` if you want speed to lead. A reporting tool has no business holding a write token.
- **Polite to the API.** GHL allows 100 requests per 10 seconds per app per location. The client retries 429 and 5xx with backoff (honouring `Retry-After`), stops if a pagination cursor stops moving, and caps speed-to-lead checks at 200 contacts per run, skipping contacts it has already measured.
- **Never silent.** A failed sync is recorded in `sync_runs` with the error and triggers an alert. A sync that comes back with zero contacts and zero opportunities is treated as a problem, not as "quiet week". `check` catches the case where the scheduler itself stopped.
- **The AI only writes words, never numbers.** Claude gets the computed KPI JSON and is told to use only those figures. Every number in its summary is then checked against the data; anything that doesn't appear there (usually a simple difference such as "up 3") is listed under the report's notes for a person to glance at. If the AI call fails, a template summary is used and the report still goes out.
- **One portable dashboard file.** Charts are inline SVG with hover tooltips and a "show as table" view under each one. No CDN scripts, so it opens offline and survives being emailed. Light and dark mode follow the viewer's system setting.
- **SQLite or Postgres, same code.** Storage goes through SQLAlchemy, so `DATABASE_URL` decides. The test suite runs on both.

## Run it without a GHL account

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

python -m ghl_kpi run --mock --no-ai --with-conversations   # fake API + fixture data, template summary
export ANTHROPIC_API_KEY=...                                 # optional
python -m ghl_kpi report --mock                              # same data, Claude writes the summary
open out/dashboard.html out/ghl-kpi-report-*.pdf
pytest                                                       # 28 tests, GHL and Claude mocked
```

`--mock` swaps in `ghl_kpi/mock_api.py`, an in-process stand-in that answers the same endpoints the real API does (bearer token and `Version` header required, page and cursor pagination, calendar time windows) from `fixtures/ghl_demo.json`. That file is generated by `fixtures/make_fixtures.py`: 180 contacts called "Test Lead NNN" with `@example.com` emails, 140 opportunities in a five-stage pipeline, 97 appointments and 158 conversations. No real people or accounts.

## Run it on a real sub-account

1. In GHL, open the sub-account, go to **Settings > Private Integrations > Create new integration**, give it the read-only scopes above, and copy the token (it is shown once).
2. Put it somewhere safe. On macOS: `security add-generic-password -s ghl-kpi-token -a "$USER" -w` (paste when asked).
3. `cp .env.example .env` and fill in `GHL_LOCATION_ID` (the ID after `/location/` in the GHL URL), plus `DATABASE_URL`, `SMTP_*` and `ALERT_WEBHOOK_URL` if you want them.
4. Run it once by hand: `examples/run.sh run --with-conversations`. The script reads the token from the Keychain at run time, so it never sits in a file.
5. Schedule it: `examples/com.example.ghl-kpi.plist` (macOS launchd, Mondays 07:00) or `examples/crontab.txt` (daily sync, weekly report, staleness check every 6 hours).

For Postgres: `pip install "psycopg[binary]"` and set `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname`.

`tools/seed_sandbox.py` puts up to 25 clearly fake leads into a sandbox or trial sub-account so the report has something to show, using a separate short-lived write token. Every record it creates goes into a manifest, and `--delete` removes exactly those.

## Tests

`pytest` runs 28 tests with no network and no accounts:

- **API client:** headers and `Version` values, contact paging, opportunity cursors, a cursor that stops moving, retry on 429/502, giving up after retries, a bad token raising a clear error without the token in it, the exact calendar query (epoch milliseconds, `calendarId`), timestamp parsing (ISO, offsets, epoch ms).
- **Sync:** everything stored, a second sync doesn't duplicate, snapshots accumulate, speed-to-lead results survive later syncs, a failed sync is recorded as failed.
- **KPIs:** a small hand-built dataset with every number checked exactly (leads per week, sources, conversion including a deal that moved backwards, win rate, show rate, speed buckets), data after the report date ignored, an empty database giving "n/a" rather than crashing.
- **Outputs:** the Claude call (model, low effort, numbers-only instruction) with a fake client, template fallback when the AI fails, invented numbers flagged, "Page N of M" on every PDF page with the date top right of page 1 only, dashboard escaping and no external scripts, staleness rules, webhook alert, and the CLI end to end including a failing sync raising an alert.

Set `TEST_DATABASE_URL` (for example `postgresql+psycopg://user@localhost:5432/ghl_kpi_test`) to run the same tests on Postgres.

## Status

Tested on 1 November 2026:

- 28 of 28 tests pass on SQLite (Python 3.9) and on a local Postgres 16 server.
- Full mock run: 180 contacts, 140 opportunities, 97 appointments and 158 first replies synced in 345 API calls; the PDF (3 pages) and dashboard were checked by eye. The same run on Postgres produced identical KPIs.
- Live Claude call (Sonnet 5.5) on the demo KPIs: it wrote the summary shown on the dashboard above, and every figure in it matched the data.
- **Live GoHighLevel run: not yet done.** The code follows GHL's published API v2 reference, but until it has run against a real sub-account, treat the endpoint shapes as checked against the docs, not against the live API.

Honest limits:

- Contacts or deals deleted in GHL stay in the local database; there is no delete detection yet.
- Stage history only exists from the first sync onwards. GHL's API gives the current stage, not past ones, so conversion figures get more accurate the longer the job runs.
- Speed to lead depends on Conversations scopes and costs two API calls per new contact; on big accounts, run it daily so each run only checks new leads.
- Amounts are shown without a currency symbol; GHL's API returns plain numbers.
- Not built: multi-location (agency-wide) roll-ups, OAuth marketplace app install, Looker Studio or Google Sheets output.

## Stack

Python · GoHighLevel API v2 (Private Integration token) · SQLAlchemy (SQLite / Postgres) · Anthropic Claude API · ReportLab + Matplotlib (PDF) · inline SVG (dashboard) · pytest + responses

## Licence

MIT

---
Part of a small portfolio of tested automation/AI builds; see [ParishSynthMind](https://github.com/ParishSynthMind) for the others.

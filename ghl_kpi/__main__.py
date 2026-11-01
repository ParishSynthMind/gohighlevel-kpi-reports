"""Command line.

  python -m ghl_kpi sync     pull GHL data into the database
  python -m ghl_kpi report   compute KPIs, write PDF + HTML dashboard (+ email if configured)
  python -m ghl_kpi run      sync then report (what the scheduler calls)
  python -m ghl_kpi check    alert if syncing has stopped or the last sync failed

Add --mock to use the built-in fake API and fixture data (no account or token needed).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import alerts
from .client import GHLClient, parse_ts
from .dashboard import build_dashboard
from .db import make_session_factory, now_utc
from .kpis import compute_kpis
from .report_pdf import build_pdf
from .summary import unverified_figures, write_summary
from .sync import run_sync

log = logging.getLogger("ghl_kpi")


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def make_client(args) -> GHLClient:
    if args.mock:
        from .mock_api import MOCK_TOKEN, MockGHLSession
        sess = MockGHLSession()
        return GHLClient(MOCK_TOKEN, sess.location_id, session=sess, sleep=lambda s: None)
    return GHLClient(os.environ.get("GHL_TOKEN", ""), os.environ.get("GHL_LOCATION_ID", ""))


def as_of_for(args, client) -> datetime:
    if args.as_of:
        return parse_ts(args.as_of + ("T23:59:59Z" if len(args.as_of) == 10 else ""))
    if args.mock:  # fixture data has its own "today"
        return parse_ts(client.session.data["anchor"])
    return now_utc()


def do_sync(args, Session, client):
    try:
        run = run_sync(client, Session, with_conversations=args.with_conversations, now=as_of_for(args, client))
    except Exception as exc:
        alerts.alert("GHL KPI sync FAILED", f"{type(exc).__name__}: {exc}")
        raise
    if not run.counts.get("contacts") and not run.counts.get("opportunities"):
        # an empty answer is treated as a problem, never as "nothing new"
        alerts.alert("GHL KPI sync returned no data", f"Run {run.id} finished but fetched 0 contacts and 0 opportunities.")
    print(f"sync ok: run {run.id}, {json.dumps(run.counts)}, {run.api_calls} API calls")
    return run


def do_report(args, Session, client):
    as_of = as_of_for(args, client)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    business = os.environ.get("BUSINESS_NAME", "Demo Sub-account")
    with Session() as s:
        k = compute_kpis(s, as_of)
    summary, source = write_summary(k, use_ai=not args.no_ai)
    flags = unverified_figures(summary, k) if source == "claude" else []
    stamp = as_of.strftime("%Y-%m-%d")
    (out / "kpis.json").write_text(json.dumps(k, indent=1, default=str))
    (out / "summary.txt").write_text(summary + "\n")
    notes = []
    if flags:
        notes.append("Figures in the summary that are not copied directly from the data (usually simple differences, "
                     "worth a glance): " + ", ".join(flags) + ".")
    pdf = build_pdf(k, summary, source, str(out / f"ghl-kpi-report-{stamp}.pdf"), business, datetime.now(timezone.utc), notes)
    html = build_dashboard(k, summary, str(out / "dashboard.html"), business, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print(f"summary ({source}):\n{summary}\n")
    if flags:
        print(f"figures not found verbatim in the KPI data (review): {flags}")
    print(f"wrote {pdf}\nwrote {html}\nwrote {out / 'kpis.json'}")
    if args.email:
        sent = alerts.send_email(f"Weekly CRM KPI report, {stamp}", summary, [pdf, html])
        print("emailed report" if sent else "email not configured, skipped")
    return k


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ghl_kpi", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["sync", "report", "run", "check"])
    ap.add_argument("--mock", action="store_true", help="use the built-in fake GHL API and fixture data")
    ap.add_argument("--db", help="SQLAlchemy URL (default: $DATABASE_URL or sqlite:///ghl_kpi.db)")
    ap.add_argument("--out", default="out", help="output folder for the PDF, dashboard and kpis.json")
    ap.add_argument("--as-of", help="report date YYYY-MM-DD (default: now; fixture date with --mock)")
    ap.add_argument("--no-ai", action="store_true", help="skip Claude, use the template summary")
    ap.add_argument("--with-conversations", action="store_true", help="also measure speed to lead from conversations")
    ap.add_argument("--email", action="store_true", help="email the report if SMTP_* is configured")
    ap.add_argument("--stale-hours", type=float, default=None)
    args = ap.parse_args(argv)

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    db_url = args.db or os.environ.get("DATABASE_URL") or ("sqlite:///ghl_kpi_mock.db" if args.mock else "sqlite:///ghl_kpi.db")
    Session = make_session_factory(db_url)

    if args.command == "check":
        hours = args.stale_hours or float(os.environ.get("STALE_AFTER_HOURS", "26"))
        with Session() as s:
            problem = alerts.stale_problem(s, hours)
        if problem:
            alerts.alert("GHL KPI sync has stopped", problem)
            print("PROBLEM: " + problem)
            return 1
        print("sync healthy")
        return 0

    client = make_client(args)
    if args.command in ("sync", "run"):
        do_sync(args, Session, client)
    if args.command in ("report", "run"):
        try:
            do_report(args, Session, client)
        except Exception as exc:
            alerts.alert("GHL KPI report FAILED", f"{type(exc).__name__}: {exc}")
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

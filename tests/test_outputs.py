import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from ghl_kpi import __main__ as cli
from ghl_kpi.alerts import alert, stale_problem
from ghl_kpi.dashboard import build_dashboard
from ghl_kpi.db import SyncRun
from ghl_kpi.kpis import compute_kpis
from ghl_kpi.report_pdf import build_pdf
from ghl_kpi.summary import template_summary, unverified_figures, write_summary
from ghl_kpi.sync import run_sync

ANCHOR = datetime(2026, 10, 30, 17, 0, tzinfo=timezone.utc)


@pytest.fixture
def kpis(client, Session):
    run_sync(client, Session, with_conversations=True, now=ANCHOR)
    with Session() as s:
        return compute_kpis(s, ANCHOR)


class FakeClaude:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=self.text)])


def test_claude_summary_uses_model_and_kpi_json(kpis):
    fake = FakeClaude("Steady week.\nGood: x\nWatch: y\nNext step: z")
    text, src = write_summary(kpis, client=fake)
    assert src == "claude" and text.startswith("Steady week")
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert "never invent" in call["system"]
    assert call["output_config"] == {"effort": "low"} and call["max_tokens"] >= 2000
    assert str(kpis["headline"]["new_leads_7d"]) in call["messages"][0]["content"]


def test_summary_falls_back_when_ai_fails(kpis):
    text, src = write_summary(kpis, client=FakeClaude(exc=RuntimeError("boom")))
    assert src == "template" and "AI summary unavailable" in text
    text, src = write_summary(kpis, use_ai=False)
    assert src == "template" and text == template_summary(kpis)


def test_unverified_figures_flags_invented_numbers(kpis):
    h = kpis["headline"]
    ok = f"{h['new_leads_7d']} new leads, pipeline {h['open_pipeline_value']:,.0f}."
    assert unverified_figures(ok, kpis) == []
    assert unverified_figures(ok + " Revenue will hit 987,654.", kpis) == ["987,654"]


def test_pdf_has_page_numbers_on_every_page_and_date_on_first(kpis, tmp_path):
    path = build_pdf(kpis, template_summary(kpis), "template", str(tmp_path / "r.pdf"), "Demo", datetime(2026, 11, 1))
    pages = PdfReader(path).pages
    n = len(pages)
    assert n >= 2
    for i, p in enumerate(pages, 1):
        assert f"Page {i} of {n}" in p.extract_text()
    assert "01 November 2026" in pages[0].extract_text()
    assert "01 November 2026" not in pages[1].extract_text()


def test_dashboard_is_self_contained_and_escaped(kpis, tmp_path):
    path = build_dashboard(kpis, "Summary with <script>alert(1)</script>", str(tmp_path / "d.html"), "Demo & Co", "now")
    html = open(path).read()
    assert html.count("<svg") >= 5
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html
    assert "Demo &amp; Co" in html
    assert not re.search(r'<script[^>]+src=', html)  # no external scripts


def test_stale_detection(Session):
    with Session() as s:
        assert "ever run" in stale_problem(s, 26, ANCHOR)
        s.add(SyncRun(location_id="l", started_at=ANCHOR - timedelta(hours=30), finished_at=ANCHOR - timedelta(hours=30), status="ok"))
        s.commit()
        assert "30.0 hours ago" in stale_problem(s, 26, ANCHOR)
        assert stale_problem(s, 48, ANCHOR) is None
        s.add(SyncRun(location_id="l", started_at=ANCHOR, finished_at=ANCHOR, status="failed", error="GHLError: 401"))
        s.commit()
        assert "failed" in stale_problem(s, 48, ANCHOR)


def test_alert_goes_to_webhook(monkeypatch):
    sent = []
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    monkeypatch.setattr("ghl_kpi.alerts.post_webhook", lambda text: sent.append(text) or True)
    assert alert("Sync failed", "details") == ["webhook"]
    assert "Sync failed" in sent[0]


def test_cli_end_to_end_mock(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    db = f"sqlite:///{tmp_path / 'cli.db'}"
    assert cli.main(["run", "--mock", "--no-ai", "--with-conversations", "--db", db, "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "sync ok" in out and "ghl-kpi-report-2026-10-30.pdf" in out
    assert (tmp_path / "out" / "dashboard.html").exists() and (tmp_path / "out" / "kpis.json").exists()
    assert cli.main(["check", "--db", db, "--stale-hours", "100000"]) == 0
    assert cli.main(["check", "--db", f"sqlite:///{tmp_path / 'empty.db'}"]) == 1


def test_cli_sync_failure_raises_and_alerts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    alerts_sent = []
    monkeypatch.setattr("ghl_kpi.alerts.alert", lambda subj, body: alerts_sent.append(subj) or [])
    monkeypatch.setenv("GHL_TOKEN", "bad")
    monkeypatch.setenv("GHL_LOCATION_ID", "loc")

    def boom(*a, **k):
        raise ConnectionError("network down")
    monkeypatch.setattr("ghl_kpi.__main__.run_sync", boom)
    with pytest.raises(ConnectionError):
        cli.main(["sync", "--db", f"sqlite:///{tmp_path / 'x.db'}"])
    assert alerts_sent == ["GHL KPI sync FAILED"]

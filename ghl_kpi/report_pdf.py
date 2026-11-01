"""PDF report: summary, KPI tiles, charts and tables. 'Page N of M' on every page, date top right on page 1."""
from __future__ import annotations

import io
from datetime import datetime
from typing import Dict, List

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas as rl_canvas  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,  # noqa: E402
                                Table, TableStyle)

BLUE = "#2a78d6"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"


class NumberedCanvas(rl_canvas.Canvas):
    """Two-pass canvas so every page knows the total page count."""

    report_date = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int):
        w, h = A4
        self.setFont("Helvetica", 8.5)
        self.setFillColor(colors.HexColor(INK_2))
        self.drawCentredString(w / 2, 10 * mm, f"Page {self._pageNumber} of {total}")
        if self._pageNumber == 1:
            self.setFont("Helvetica", 10)
            self.drawRightString(w - 18 * mm, h - 14 * mm, self.report_date)


def _style_axes(ax):
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def bar_chart(labels: List[str], values: List[float], title: str) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    bars = ax.bar(labels, values, color=BLUE, width=0.6)
    _style_axes(ax)
    ax.set_title(title, loc="left", fontsize=10, color=INK, pad=10)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v:g}", ha="center", va="bottom", fontsize=7.5, color=INK_2)
    return _png(fig)


def hbar_chart(labels: List[str], values: List[float], title: str, fmt=lambda v: f"{v:g}") -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(7.2, 0.35 * len(labels) + 0.9))
    y = list(range(len(labels)))[::-1]
    ax.barh(y, values, color=BLUE, height=0.6)
    ax.set_yticks(y, labels)
    _style_axes(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_title(title, loc="left", fontsize=10, color=INK, pad=10)
    vmax = max(values) if values else 1
    for yi, v in zip(y, values):
        ax.text(v + vmax * 0.01, yi, fmt(v), va="center", fontsize=7.5, color=INK_2)
    ax.set_xlim(0, vmax * 1.15 or 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    return _png(fig)


def _fmt_money(v) -> str:
    return f"{v:,.0f}"


def _na(v, suffix=""):
    return "n/a" if v is None else f"{v:g}{suffix}"


def build_pdf(k: Dict, summary: str, summary_source: str, path: str, business: str, report_date: datetime,
              notes: List[str] = ()) -> str:
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], alignment=TA_LEFT, fontSize=18, textColor=colors.HexColor(INK), spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor=colors.HexColor(INK), spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14, textColor=colors.HexColor(INK))
    small = ParagraphStyle("s", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor(INK_2))
    h = k["headline"]

    story = [Paragraph("Weekly CRM KPI report", h1),
             Paragraph(f"{business} &middot; data as of {k['as_of'][:10]} &middot; trends cover {k['weeks']} weeks from {k['window_start']}", small),
             Spacer(1, 8), Paragraph("Summary", h2)]
    for line in summary.splitlines():
        if line.strip():
            story.append(Paragraph(line.strip().replace("&", "&amp;").replace("<", "&lt;"), body))
    story.append(Paragraph("Written by Claude from the numbers below." if summary_source == "claude"
                           else "Template summary (no AI key configured).", small))

    tiles = [
        ("New leads, last 7 days", f"{h['new_leads_7d']}", f"previous 7 days: {h['new_leads_prev_7d']}"),
        ("Open pipeline value", _fmt_money(h["open_pipeline_value"]), f"{h['open_opportunities']} open opportunities"),
        ("Win rate (closed in window)", _na(h["win_rate_window_pct"], "%"), f"{h['won_count_window']} won, {_fmt_money(h['won_value_window'])}"),
        ("Lead to appointment", _na(h["lead_to_appointment_pct"], "%"), f"show rate {_na(h['show_rate_pct'], '%')}"),
        ("Median speed to lead", _na(h["median_speed_to_lead_min"], " min"),
         f"{k['speed_to_lead']['measured_leads']} of {k['speed_to_lead']['window_leads']} leads measured"),
        ("Appointments next 7 days", f"{h['upcoming_appointments_7d']}", "booked, not cancelled"),
    ]
    cells = [[Paragraph(f"<font size=8 color='{INK_2}'>{t}</font><br/><font size=15>{v}</font><br/><font size=7.5 color='{INK_2}'>{sub}</font>", body)
              for t, v, sub in tiles[i:i + 3]] for i in (0, 3)]
    tile_table = Table(cells, colWidths=[58 * mm] * 3, rowHeights=[20 * mm] * 2)
    tile_table.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(GRID)),
                                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor(GRID)),
                                    ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
    story += [Spacer(1, 6), Paragraph("Headline numbers", h2), tile_table]

    lw = k["leads_weekly"]
    story += [Paragraph("Leads", h2),
              Image(bar_chart([w["week"][5:] for w in lw], [w["leads"] for w in lw], "New leads per week (week starting; latest week is to date)"),
                    width=170 * mm, height=57 * mm)]
    if k["sources"]:
        src_chart = (Image(hbar_chart([r["source"] for r in k["sources"]], [r["leads"] for r in k["sources"]],
                                      f"Leads by source, last {k['weeks']} weeks"),
                           width=170 * mm, height=(0.35 * len(k["sources"]) + 0.9) * 170 / 7.2 * mm))
        src_rows = [["Source", "Leads", "Won", "Lead to won"]] + [
            [r["source"], r["leads"], r["won"], _na(r["win_rate_pct"], "%")] for r in k["sources"]]
        story.append(KeepTogether([src_chart, Spacer(1, 4), _table(src_rows)]))

    for p in k["pipelines"]:
        rows = [["Stage", "Reached", "Conversion from previous", "Open now", "Open value"]] + [
            [r["stage"], r["reached"], _na(r["conversion_from_prev_pct"], "%"), r["open_count"], _fmt_money(r["open_value"])]
            for r in p["stages"]]
        chart = hbar_chart([r["stage"] for r in p["stages"]], [r["open_value"] for r in p["stages"]],
                           f"{p['pipeline']}: open value by stage", fmt=_fmt_money)
        story.append(KeepTogether([Paragraph(f"Pipeline: {p['pipeline']}", h2), _table(rows), Spacer(1, 6),
                                   Image(chart, width=170 * mm, height=(0.35 * len(p["stages"]) + 0.9) * 170 / 7.2 * mm)]))

    aw = k["appointments_weekly"]
    story.append(KeepTogether([Paragraph("Appointments", h2),
                               Image(bar_chart([w["week"][5:] for w in aw], [w["appointments"] for w in aw],
                                               "Appointments held or scheduled per week (latest week is to date)"), width=170 * mm, height=57 * mm)]))

    sp = k["speed_to_lead"]
    story.append(KeepTogether([Paragraph("Speed to lead", h2), Paragraph(
        f"Time from a contact being created to the first outbound message. Measured for {sp['measured_leads']} of "
        f"{sp['window_leads']} leads in the window. Median {_na(sp['median_minutes'], ' minutes')}; "
        f"{_na(sp['within_5_min_pct'], '%')} answered within 5 minutes; {_na(sp['over_1_hour_pct'], '%')} waited over an hour.", body)]))
    story.append(KeepTogether([Spacer(1, 8), Paragraph("How these are counted", h2)] + [Paragraph(n, small) for n in [
        "Weeks start on Monday (UTC). 'Reached' counts an opportunity at the furthest stage it has been seen in "
        "across all syncs; won deals count as reaching every stage. Win rate uses deals marked won or lost inside the window.",
        *notes]]))

    NumberedCanvas.report_date = report_date.strftime("%d %B %Y")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
                            title="Weekly CRM KPI report", author="gohighlevel-kpi-reports")
    doc.build(story, canvasmaker=NumberedCanvas)
    return path


def _table(rows):
    t = Table(rows, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(INK_2)),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(GRID)),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t

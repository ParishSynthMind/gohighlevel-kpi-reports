"""One static HTML file: KPI tiles plus inline SVG charts. No external scripts, works offline and in email attachments."""
from __future__ import annotations

import html
import json
from typing import Dict, List

CSS = """
.viz-root{color-scheme:light;--surface-0:#f4f3f0;--surface-1:#fcfcfb;--text-primary:#0b0b0b;--text-secondary:#52514e;
--text-muted:#7a7974;--grid:#e4e3df;--series-1:#2a78d6;--series-1-hover:#1c5cab}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-0:#111110;
--surface-1:#1a1a19;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#94938b;--grid:#2e2d2b;--series-1:#3987e5;--series-1-hover:#6da7ec}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-0:#111110;--surface-1:#1a1a19;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--text-muted:#94938b;--grid:#2e2d2b;--series-1:#3987e5;--series-1-hover:#6da7ec}
*{box-sizing:border-box}body{margin:0}
.viz-root{background:var(--surface-0);color:var(--text-primary);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
min-height:100vh;padding:28px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:0 0 2px}.sub{color:var(--text-secondary);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:20px 0}
.tile,.card{background:var(--surface-1);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}
.tile .k{color:var(--text-secondary);font-size:12px}.tile .v{font-size:26px;font-weight:600;margin:2px 0}
.tile .s{color:var(--text-muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:12px}
.summary{white-space:pre-line;margin:8px 0 0}
svg text{fill:var(--text-secondary);font-size:11px}svg .val{fill:var(--text-primary)}
svg .bar{fill:var(--series-1)}svg .hit:hover+.bar,svg .bar:hover{fill:var(--series-1-hover)}
svg .gl{stroke:var(--grid);stroke-width:1}
details{margin-top:8px;color:var(--text-secondary);font-size:12px}table{border-collapse:collapse;margin-top:6px}
td,th{padding:3px 10px 3px 0;text-align:right;border-bottom:1px solid var(--grid)}td:first-child,th:first-child{text-align:left}
#tip{position:fixed;pointer-events:none;background:var(--surface-1);color:var(--text-primary);border:1px solid var(--grid);
border-radius:6px;padding:6px 9px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none}
.foot{color:var(--text-muted);font-size:12px;margin-top:18px}
"""

JS = """
const tip=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el=>{
 el.addEventListener('mousemove',e=>{tip.textContent=el.dataset.tip;tip.style.display='block';
  tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px'});
 el.addEventListener('mouseleave',()=>{tip.style.display='none'});});
"""


def _e(s) -> str:
    return html.escape(str(s))


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    import math
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def svg_columns(labels: List[str], values: List[float], unit: str, w: int = 480, h: int = 220) -> str:
    left, right, top, bottom = 34, 8, 12, 28
    pw, ph = w - left - right, h - top - bottom
    vmax = _nice_max(max(values) if values else 1)
    step = pw / max(len(values), 1)
    bw = min(step * 0.6, 40)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    for i in range(5):
        y = top + ph - ph * i / 4
        out.append(f'<line class="gl" x1="{left}" x2="{w-right}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text x="{left-6}" y="{y+4:.1f}" text-anchor="end">{vmax*i/4:g}</text>')
    for i, (lab, v) in enumerate(zip(labels, values)):
        x = left + step * i + (step - bw) / 2
        bh = ph * v / vmax
        y = top + ph - bh
        r = min(4, bw / 2, bh)
        path = (f"M{x:.1f},{top+ph:.1f} V{y+r:.1f} Q{x:.1f},{y:.1f} {x+r:.1f},{y:.1f} H{x+bw-r:.1f} "
                f"Q{x+bw:.1f},{y:.1f} {x+bw:.1f},{y+r:.1f} V{top+ph:.1f} Z") if bh > 0 else ""
        tipt = _e(f"{lab}: {v:g} {unit}")
        out.append(f'<rect class="hit" x="{left+step*i:.1f}" y="{top}" width="{step:.1f}" height="{ph}" fill="transparent" data-tip="{tipt}"/>')
        if path:
            out.append(f'<path class="bar" d="{path}" data-tip="{tipt}"/>')
        out.append(f'<text x="{x+bw/2:.1f}" y="{h-10}" text-anchor="middle">{_e(lab)}</text>')
    if values:  # label only the latest value, not every bar
        i = len(values) - 1
        x = left + step * i + step / 2
        out.append(f'<text class="val" x="{x:.1f}" y="{top+ph-ph*values[-1]/vmax-5:.1f}" text-anchor="middle">{values[-1]:g}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_rows(labels: List[str], values: List[float], fmt=lambda v: f"{v:g}", w: int = 480) -> str:
    row_h, left, right = 26, 120, 64
    h = row_h * len(labels) + 8
    vmax = max(values) if values and max(values) > 0 else 1
    pw = w - left - right
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = 4 + i * row_h
        bw = pw * v / vmax
        bh = row_h - 10
        r = min(4, bw, bh / 2)
        tipt = _e(f"{lab}: {fmt(v)}")
        out.append(f'<text x="{left-8}" y="{y+bh/2+4:.1f}" text-anchor="end">{_e(lab)}</text>')
        out.append(f'<rect class="hit" x="0" y="{y}" width="{w}" height="{row_h}" fill="transparent" data-tip="{tipt}"/>')
        if bw > 0:
            out.append(f'<path class="bar" data-tip="{tipt}" d="M{left},{y} H{left+bw-r:.1f} Q{left+bw:.1f},{y} {left+bw:.1f},{y+r:.1f} '
                       f'V{y+bh-r:.1f} Q{left+bw:.1f},{y+bh:.1f} {left+bw-r:.1f},{y+bh:.1f} H{left} Z"/>')
        out.append(f'<text class="val" x="{left+bw+6:.1f}" y="{y+bh/2+4:.1f}">{_e(fmt(v))}</text>')
    out.append("</svg>")
    return "".join(out)


def _table(head: List[str], rows: List[List]) -> str:
    th = "".join(f"<th>{_e(x)}</th>" for x in head)
    trs = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<details><summary>Show as table</summary><table><tr>{th}</tr>{trs}</table></details>"


def _na(v, suffix=""):
    return "n/a" if v is None else f"{v:g}{suffix}"


def build_dashboard(k: Dict, summary: str, path: str, business: str, generated: str) -> str:
    h = k["headline"]
    money = lambda v: f"{v:,.0f}"  # noqa: E731
    tiles = [
        ("New leads, last 7 days", h["new_leads_7d"], f"previous 7 days: {h['new_leads_prev_7d']}"),
        ("Open pipeline value", money(h["open_pipeline_value"]), f"{h['open_opportunities']} open opportunities"),
        ("Win rate", _na(h["win_rate_window_pct"], "%"), f"{h['won_count_window']} won in {k['weeks']} weeks"),
        ("Lead to appointment", _na(h["lead_to_appointment_pct"], "%"), f"show rate {_na(h['show_rate_pct'], '%')}"),
        ("Median speed to lead", _na(h["median_speed_to_lead_min"], " min"),
         f"{_na(k['speed_to_lead']['within_5_min_pct'], '%')} within 5 min"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="k">{_e(a)}</div><div class="v">{_e(b)}</div><div class="s">{_e(c)}</div></div>'
                         for a, b, c in tiles)

    lw, aw, src = k["leads_weekly"], k["appointments_weekly"], k["sources"]
    cards = [
        ("New leads per week", "Week starting Monday; latest week is to date",
         svg_columns([w["week"][5:] for w in lw], [w["leads"] for w in lw], "leads"),
         _table(["Week", "Leads"], [[w["week"], w["leads"]] for w in lw])),
        ("Leads by source", f"Last {k['weeks']} weeks",
         svg_rows([r["source"] for r in src], [r["leads"] for r in src]),
         _table(["Source", "Leads", "Won", "Lead to won"], [[r["source"], r["leads"], r["won"], _na(r["win_rate_pct"], "%")] for r in src])),
    ]
    for p in k["pipelines"]:
        st = p["stages"]
        if not any(r["reached"] for r in st):
            continue  # an empty pipeline gets no cards, just noise otherwise
        cards.append((f"{p['pipeline']}: deals reaching each stage", "Conversion from the previous stage in the table",
                      svg_rows([r["stage"] for r in st], [r["reached"] for r in st]),
                      _table(["Stage", "Reached", "Conversion", "Open now", "Open value"],
                             [[r["stage"], r["reached"], _na(r["conversion_from_prev_pct"], "%"), r["open_count"], money(r["open_value"])] for r in st])))
        cards.append((f"{p['pipeline']}: open value by stage", "Open deals only",
                      svg_rows([r["stage"] for r in st], [r["open_value"] for r in st], fmt=money), ""))
    cards.append(("Appointments per week", "Held or scheduled, by start date",
                  svg_columns([w["week"][5:] for w in aw], [w["appointments"] for w in aw], "appointments"),
                  _table(["Week", "Appointments"], [[w["week"], w["appointments"]] for w in aw])))
    sp = k["speed_to_lead"]
    cards.append(("Speed to first reply", f"Leads from the last {k['weeks']} weeks, contact created to first outbound message",
                  svg_rows([b["bucket"] for b in sp["buckets"]], [b["leads"] for b in sp["buckets"]]),
                  _table(["Time to first reply", "Leads"], [[b["bucket"], b["leads"]] for b in sp["buckets"]])))
    cards_html = "".join(f'<div class="card"><h2>{_e(t)}</h2><div class="sub">{_e(s)}</div>{svg}{tbl}</div>'
                         for t, s, svg, tbl in cards)

    data_json = json.dumps(k, default=str).replace("</", "<\\/")
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CRM KPI dashboard - {_e(business)}</title><style>{CSS}</style></head>
<body><div class="viz-root"><div class="wrap">
<h1>CRM KPI dashboard</h1><div class="sub">{_e(business)} &middot; data as of {_e(k['as_of'][:10])} &middot; generated {_e(generated)}</div>
<div class="card" style="margin-top:16px"><h2>This week in plain English</h2><p class="summary">{_e(summary)}</p></div>
<div class="tiles">{tiles_html}</div><div class="grid">{cards_html}</div>
<div class="foot">Built by gohighlevel-kpi-reports from GoHighLevel API data. Raw numbers: kpis.json next to this file.</div>
</div></div><div id="tip"></div><script>{JS}</script>
<script type="application/json" id="kpi-data">{data_json}</script></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path

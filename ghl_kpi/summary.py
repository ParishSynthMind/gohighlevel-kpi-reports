"""Weekly summary in plain English. Claude writes it from the KPI numbers; a template is the fallback."""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional, Tuple

DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM = (
    "You write the weekly summary at the top of a small business's CRM report. "
    "Use ONLY the numbers in the KPI JSON you are given; never invent, estimate or extrapolate a figure. "
    "If a number is null, say it is not available rather than guessing. "
    "Write for a busy owner: plain English, no jargon, no hype, no emojis, no markdown headings. "
    "Format: one opening sentence on how the week went, then exactly three short lines starting with "
    "'Good:', 'Watch:' and 'Next step:'. Keep it under 130 words. Money is in the account's currency; "
    "write it with a thousands separator and no currency symbol unless one is given."
)


def template_summary(k: Dict) -> str:
    h = k["headline"]
    change = h["new_leads_7d"] - h["new_leads_prev_7d"]
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    top = k["sources"][0]["source"] if k["sources"] else "n/a"
    win = f"{h['win_rate_window_pct']}%" if h["win_rate_window_pct"] is not None else "n/a"
    speed = f"{h['median_speed_to_lead_min']} min" if h["median_speed_to_lead_min"] is not None else "not measured"
    return (
        f"{h['new_leads_7d']} new leads in the last 7 days ({direction} {abs(change)} on the week before). "
        f"Open pipeline is {h['open_pipeline_value']:,.0f} across {h['open_opportunities']} opportunities.\n"
        f"Good: {top} is the biggest lead source over the last {k['weeks']} weeks.\n"
        f"Watch: win rate on closed deals is {win}; median speed to first reply is {speed}.\n"
        + (f"Next step: follow up the {h['upcoming_appointments_7d']} appointments booked for the coming week."
           if h["upcoming_appointments_7d"] else
           "Next step: nothing is booked for the coming week, so the open pipeline needs follow-up calls.")
    )


def _norm(n: str) -> str:
    n = n.replace(",", "").rstrip(".")
    return str(float(n)).rstrip("0").rstrip(".") if n else n


def unverified_figures(text: str, k: Dict):
    """Numbers in the summary that don't appear anywhere in the KPI data (rounded or not).

    Derived figures such as a week-on-week difference also land here, so this is a review
    flag printed with the report, not a hard block.
    """
    data_nums = set()
    for n in re.findall(r"-?\d+\.?\d*", json.dumps(k, default=str)):
        n = n.lstrip("-")
        data_nums.add(_norm(n))
        data_nums.add(_norm(str(round(float(n)))))
    found = [m for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text) if _norm(m) not in data_nums]
    return sorted(set(found))


def claude_summary(k: Dict, api_key: str, model: Optional[str] = None, client=None) -> str:
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL,
        # Sonnet 5.5 thinks by default and thinking counts toward max_tokens, so leave room
        # and keep effort low: this is a short, numbers-only writing job.
        max_tokens=4000,
        output_config={"effort": "low"},
        system=SYSTEM,
        messages=[{"role": "user", "content": "KPI JSON:\n" + json.dumps(k, default=str)}],
    )
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude stopped with {msg.stop_reason}")
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def write_summary(k: Dict, use_ai: bool = True, client=None) -> Tuple[str, str]:
    """Return (summary_text, source) where source is 'claude' or 'template'."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if use_ai and (key or client):
        try:
            text = claude_summary(k, key or "", client=client)
            if text:
                return text, "claude"
        except Exception as exc:  # the report must still go out if the AI call fails
            return template_summary(k) + f"\n(AI summary unavailable: {type(exc).__name__})", "template"
    return template_summary(k), "template"

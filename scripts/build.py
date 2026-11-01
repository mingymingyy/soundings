"""Fetch the feeds and render the static site into _site/.

Numbers are rendered into the HTML at build time, so the first paint already
carries real data, works without JavaScript and is visible to crawlers.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feeds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"
SITE_URL = os.environ.get("SITE_URL", "https://mingymingyy.github.io/soundings").rstrip("/")
SGT = feeds.SGT
TITLE = "Soundings: live global macro dashboard, read from Singapore"
DESCRIPTION = (
    "Rates, FX, commodities, equities, central banks and the macro calendar on one live page, "
    "with SGT timestamps and a named public source on every panel."
)
COVER_WIDTH = 480
PANELS = [("rates", "Rates"), ("fx", "FX"), ("commodities", "Commodities"), ("equities", "Equities and volatility"), ("banks", "Central banks"), ("calendar", "Calendar")]
e = lambda v: html.escape(str(v), quote=True)  # noqa: E731

LOGO = (
    '<svg class="mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">'
    '<path d="M16 2v20" /><path d="M3 10c3.2-3 6.5-3 9.7 0s6.500 3 9.700 0 4.500-2.800 6.600-1.400" />'
    '<path d="M3 16.500c3.200-3 6.500-3 9.700 0s6.500 3 9.700 0 4.500-2.800 6.600-1.400" />'
    '<path class="lead" d="M16 22l3 4-3 4-3-4z" /></svg>'
)


def when(stamp: str | None, with_year: bool = True) -> str:
    """Readable SGT time. A date-only stamp stays a date: the source gave no time of day."""
    if not stamp:
        return "time not published"
    fmt_day = "{d} %b %Y" if with_year else "{d} %b"
    if len(stamp) == 10:
        day = dt.date.fromisoformat(stamp)
        return day.strftime(fmt_day).format(d=day.day)
    moment = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(SGT)
    return f"{moment:%H:%M} SGT, " + moment.strftime(fmt_day).format(d=moment.day)


def day_label(iso_day: str) -> str:
    day = dt.date.fromisoformat(iso_day)
    return f"{day.day} {day:%b %Y}"


def days_until(iso_day: str, today: dt.date) -> str:
    delta = (dt.date.fromisoformat(iso_day) - today).days
    return "today" if delta == 0 else ("tomorrow" if delta == 1 else f"in {delta} days")


def head(title: str, description: str, path: str, prefix: str, robots: str, extra: str = "") -> str:
    canonical = f"{SITE_URL}/{path}"
    return f"""<!doctype html>
<html lang="en-SG" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; worker-src 'self' blob:; font-src 'self' data:; connect-src 'self' blob:; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'none'">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{e(canonical)}">
<meta name="theme-color" content="#0b1017">
<meta name="color-scheme" content="dark light">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Soundings">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:url" content="{e(canonical)}">
<meta property="og:image" content="{SITE_URL}/assets/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Soundings logo over a line tracing a yield curve like a seabed profile">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(description)}">
<meta name="twitter:image" content="{SITE_URL}/assets/og.png">
<link rel="icon" href="{prefix}assets/favicon.svg" type="image/svg+xml">
<link rel="icon" href="{prefix}assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="{prefix}assets/apple-touch-icon.png">
<link rel="manifest" href="{prefix}manifest.webmanifest">
<link rel="preload" href="{prefix}assets/fonts/playfair.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{prefix}assets/fonts/inter.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{prefix}assets/site.css">
<script src="{prefix}assets/chrome.js"></script>
{extra}</head>
"""


def header(generated: str | None, prefix: str, active: str) -> str:
    stamp = ""
    if generated:
        moment = when(generated)
        clock, _, date = moment.partition(", ")
        stamp = (
            f'<p class="stamp" id="stamp"><span class="stamp-k">Last updated</span> '
            f'<time id="updated" datetime="{e(generated)}"><span class="stamp-time">{e(clock)}</span><span class="stamp-date">, {e(date)}</span></time>'
            f'<span class="stamp-note" id="stamp-note" role="status"></span></p>'
        )
    pages = [("dashboard", "Dashboard", prefix or "./"), ("research", "Research", f"{prefix}research/"), ("about", "About", f"{prefix}about/")]
    nav = "".join(
        f'<li><a href="{e(href)}"{" aria-current=" + chr(34) + "page" + chr(34) if key == active else ""}>{label}</a></li>' for key, label, href in pages
    )
    panel_links = "".join(
        f'<li><a href="{prefix}#{sid}"><span class="idx">{i:02d}</span>{e(label)}</a></li>' for i, (sid, label) in enumerate(PANELS, 1)
    )
    return f"""<a class="skip" href="#main">Skip to content</a>
<div class="progress" aria-hidden="true"><span id="progress"></span></div>
<header class="bar">
  <a class="brand" href="{prefix or './'}" aria-label="Soundings, dashboard home">{LOGO}<span class="word">Soundings<small>Global macro, Singapore</small></span></a>
  {stamp}
  <nav class="top-nav" aria-label="Main"><ul>{nav}</ul></nav>
  <div class="bar-actions">
    <button class="icon-btn" id="theme" type="button" aria-pressed="false" aria-label="Switch to light theme"><span class="theme-dot" aria-hidden="true"></span></button>
    <button class="menu-btn" id="menu-open" type="button" aria-haspopup="dialog" aria-controls="menu" aria-label="Open menu"><span class="menu-lines" aria-hidden="true"></span></button>
  </div>
</header>
<dialog class="menu" id="menu" aria-label="Site menu">
  <div class="menu-top">
    <span class="word">Soundings</span>
    <button class="btn ghost" id="menu-close" type="button">Close</button>
  </div>
  <nav aria-label="Pages"><ul class="menu-pages">{nav}</ul></nav>
  <nav aria-label="Dashboard panels"><ol class="menu-list">{panel_links}</ol></nav>
</dialog>
"""


def footer(prefix: str) -> str:
    return f"""<footer class="foot">
  <div class="foot-grid">
    <div>
      <p class="foot-mark">{LOGO}<span class="word">Soundings</span></p>
      <p>A sounding is a measured depth. This page takes the market's, from Singapore, and shows where each reading came from.</p>
    </div>
    <div>
      <h2>Method</h2>
      <ul>
        <li>Built from free public sources with no API keys. Refreshes are scheduled every 15 minutes, but GitHub can delay them by hours, so check the last updated time.</li>
        <li>Yahoo Finance is an unofficial, exchange-delayed feed. Treat it as indicative.</li>
        <li>A market-implied Fed path is left out because no free public source publishes one.</li>
        <li>A failed feed shows a labelled empty state, or its last good value marked stale for up to 7 days.</li>
      </ul>
    </div>
    <div>
      <h2>Go to</h2>
      <ul>
        <li><a href="{prefix or './'}">Dashboard</a></li>
        <li><a href="{prefix}research/">Research</a></li>
        <li><a href="{prefix}about/">About</a></li>
        <li><a href="{prefix}data/market.json">Raw data as JSON</a></li>
      </ul>
    </div>
  </div>
  <p class="foot-legal">Information only. Not investment advice.</p>
</footer>
"""


def freshness_attrs(row: dict) -> str:
    """What the page needs to flag a series that is older than its expected update frequency."""
    if row["id"] == "fftarget" or row.get("status") == "empty":
        return ""  # a policy rate holds until the next decision, so an old date is not late
    expect = "monthly" if row.get("freq") == "monthly" else "daily"
    return f' data-obs="{e(row["lastObs"])}" data-expect="{expect}"'


def change_cell(change: dict, label: str, full: str, freq: str) -> str:
    period = f'<span class="l" aria-hidden="true">{label}</span>'
    if change["d"] == "na":
        why = "not available" + (" for a monthly series" if freq == "monthly" else "")
        return f'<td class="chg na">{period}<span class="v" title="{why}"><span class="sr">{full}: </span>n/a</span></td>'
    ref = f' title="Against {e(day_label(change["ref"]))}"' if change.get("ref") else ""
    return f'<td class="chg {change["d"]}"{ref}>{period}<span class="v"><span class="sr">{full}: </span>{e(change["t"])}</span></td>'


def sparkline(row: dict) -> str:
    sp = row.get("spark") or feeds.spark(row["series"])
    fmt = lambda v: f"{v:,.{row['dp']}f}"  # noqa: E731
    direction = row["change"]["m1"]["d"]
    start, end = row["series"][0][0], row["series"][-1][0]
    label = (
        f"{row['label']} from {day_label(start)} to {day_label(end)}: "
        f"low {fmt(row['low'][1])} on {day_label(row['low'][0])}, high {fmt(row['high'][1])} on {day_label(row['high'][0])}."
    )
    return (
        f'<svg class="spark" data-id="{e(row["id"])}" viewBox="0 0 {sp["w"]} {sp["h"]}" role="img" aria-label="{e(label)}" preserveAspectRatio="none">'
        f'<path d="{sp["d"]}" /><circle class="{direction}" cx="{sp["end"][0]}" cy="{sp["end"][1]}" r="3.5" /></svg>'
    )


def table_row(row: dict) -> str:
    status = row["status"]
    tag = ""
    if row.get("freq") == "monthly":
        tag += '<span class="tag">Monthly</span>'
    if status == "stale":
        tag += f'<span class="tag warn">Stale, feed down since {e(when(row.get("fetchedAt")))}</span>'
    note = row.get("note")
    if status == "stale" and row.get("reason"):
        note = row["reason"]
    note_html = f'<small class="note">{e(note)}</small>' if note else ""
    name = f'<th scope="row"><span class="s-name">{e(row["label"])}</span><small>{e(row["sub"])}</small><span class="late-slot"></span>{tag}{note_html}</th>'
    if status == "empty":
        return (
            f'<tr id="row-{e(row["id"])}" class="is-empty" data-status="empty">{name}'
            f'<td colspan="5" class="nodata"><strong>No data</strong> <span>{e(row["reason"])}. '
            f'Source: <a href="{e(row["source"]["url"])}" rel="noopener" target="_blank">{e(row["source"]["name"])}</a>.</span></td></tr>'
        )
    change = row["change"]
    number = ""
    if not row["display"].count("–"):
        number = f' data-num="{row["value"]}" data-dp="{row["dp"]}" data-suffix="{e(row.get("suffix", ""))}"'
    return (
        f'<tr id="row-{e(row["id"])}" data-status="{status}"{freshness_attrs(row)}>{name}'
        f'<td class="val"{number}>{e(row["display"])}</td>'
        f'{change_cell(change["d1"], "1D", "One day change", row["freq"])}'
        f'{change_cell(change["w1"], "1W", "One week change", row["freq"])}'
        f'{change_cell(change["m1"], "1M", "One month change" if row["freq"] == "daily" else "Change on previous month", row["freq"])}'
        f'<td class="spk">{sparkline(row)}</td></tr>'
    )


def provenance(sources: list[dict], generated: str) -> str:
    items = "".join(
        f'<li><a href="{e(s["url"])}" rel="noopener" target="_blank">{e(s["name"])}</a>'
        + (f' <time>{e(when(s["asOf"]))}</time>' if s.get("asOf") else "")
        + "</li>"
        for s in sources
    )
    items = items or "<li>No source responded on this run.</li>"
    return f'<div class="prov"><h3 class="sr">Source and data time</h3><ul>{items}</ul><p>Checked <time>{e(when(generated))}</time></p></div>'


def panel(idx: int, pid: str, title: str, body: str, sources: str) -> str:
    is_open = " open" if pid == "rates" else ""
    return f"""<section class="panel reveal{is_open}" id="{pid}" aria-labelledby="{pid}-h">
  <div class="side">
    <p class="idx" aria-hidden="true">{idx:02d}</p>
    <h2 id="{pid}-h"><button class="toggle" type="button" aria-expanded="{'true' if is_open else 'false'}" aria-controls="{pid}-body"><span>{e(title)}</span><span class="chev" aria-hidden="true"></span></button></h2>
    {sources}
    <div class="linked" data-panel="{pid}"><span class="skeleton sk-line"></span></div>
  </div>
  <div class="main" id="{pid}-body">{body}</div>
</section>
"""


def market_panel(idx: int, pid: str, data: dict, generated: str, curve: str = "") -> str:
    rows = "".join(table_row(r) for r in data["rows"])
    body = f"""<table class="tbl" data-swap>
  <caption class="sr">{e(data['title'])}: last value, change over one day, one week and one month, and a three month trend</caption>
  <thead><tr><th scope="col">Series</th><th scope="col" class="r">Last</th><th scope="col" class="r">1D</th><th scope="col" class="r">1W</th><th scope="col" class="r">1M</th><th scope="col" class="r">3M trend</th></tr></thead>
  <tbody>{rows}</tbody>
</table>{curve}"""
    return panel(idx, pid, data["title"], body, provenance(data["sources"], generated))


def banks_panel(idx: int, banks: list[dict], today: dt.date, generated: str) -> str:
    rows = ""
    for b in banks:
        if b["status"] == "empty":
            rows += (
                f'<tr class="is-empty" data-status="empty"><th scope="row"><span class="s-name">{e(b["short"])}</span><small>{e(b["name"])}</small></th>'
                f'<td colspan="3" class="nodata"><strong>No data</strong> <span>{e(b["reason"])}.</span></td></tr>'
            )
            continue
        stale = f'<span class="tag warn">Stale, feed down since {e(when(b.get("fetchedAt")))}</span>' if b["status"] == "stale" else ""
        nxt = b.get("next")
        if nxt:
            prefix = "By " if nxt.get("nlt") else ""
            span = day_label(nxt["date"])
            if nxt.get("start") and nxt["start"] != nxt["date"]:
                start = dt.date.fromisoformat(nxt["start"])
                same_month = start.month == dt.date.fromisoformat(nxt["date"]).month
                span = f"{start.day}–{span}" if same_month else f"{start.day} {start:%b}–{span}"
            next_html = (
                f'<span class="cb-date"><a href="{e(nxt["url"])}" rel="noopener" target="_blank">{prefix}{e(span)}</a></span>'
                f'<small data-until="{e(nxt["date"])}">{e(days_until(nxt["date"], today))}</small><small>{e(nxt["label"])}</small>'
            )
        else:
            next_html = '<span class="nodata"><strong>Not published</strong></span>'
        rows += (
            f'<tr data-status="{b["status"]}"><th scope="row"><span class="s-name">{e(b["short"])}</span><small>{e(b["name"])}</small>{stale}</th>'
            f'<td class="cb-rate"><span class="cb-val">{e(b["rate"])}</span><small>{e(b["rateLabel"])}</small></td>'
            f'<td class="cb-last"><span class="l" aria-hidden="true">Last decision</span><span class="cb-date"><a href="{e(b["last"]["url"])}" rel="noopener" target="_blank">{e(day_label(b["last"]["date"]))}</a></span><small class="cb-text">{e(b["last"]["text"])}</small></td>'
            f'<td class="cb-next"><span class="l" aria-hidden="true">Next</span>{next_html}</td></tr>'
        )
    body = f"""<table class="tbl tbl-cb" data-swap>
  <caption class="sr">Central bank tracker: policy setting, last decision and next meeting</caption>
  <thead><tr><th scope="col">Bank</th><th scope="col" class="r">Policy</th><th scope="col">Last decision</th><th scope="col">Next</th></tr></thead>
  <tbody>{rows}</tbody>
</table>"""
    sources = [{**b["source"], "asOf": b.get("last", {}).get("date")} for b in banks if b["status"] != "empty"]
    return panel(idx, "banks", "Central banks", body, provenance(sources, generated))


def calendar_panel(idx: int, events: list[dict], gaps: list[dict], today: dt.date, generated: str) -> str:
    items = ""
    for ev in events:
        day = dt.date.fromisoformat(ev["date"])
        by = '<span class="by">By</span>' if ev.get("nlt") else ""
        lead = ""
        extras = []
        if ev.get("start") and ev["start"] != ev["date"]:
            first = dt.date.fromisoformat(ev["start"])
            if first.month == day.month:
                lead = f"{first.day}–"
            else:
                extras.append(f"Starts {first.day} {first:%b}")
        if ev.get("timeSgt"):
            sgt_day = dt.date.fromisoformat(ev["dateSgt"])
            extras.append(f'{ev["timeSgt"]} SGT' + ("" if sgt_day == day else f", {sgt_day.day} {sgt_day:%b}"))
        ecb_note = ""
        if ev.get("ecbTimeSgt"):
            sgt_day = dt.date.fromisoformat(ev["ecbDateSgt"])
            stamp = f'{ev["ecbTimeSgt"]} SGT' + ("" if sgt_day == day else f", {sgt_day.day} {sgt_day:%b}")
            ecb_note = f'<small>ECB seasonally adjusted series: <a href="{e(ev["ecbUrl"])}" rel="noopener" target="_blank">{e(stamp)}</a></small>'
        sub = f'<small>{e(" · ".join(extras))}</small>' if extras else ""
        items += (
            f'<li class="ev" data-date="{e(ev["date"])}">'
            f'<p class="ev-date">{by}<span class="ev-day">{lead}{day.day}</span><span class="ev-mon">{day:%b %Y}</span></p>'
            f'<div class="ev-body"><p class="ev-type">{e(ev["type"])}</p><p class="ev-title">{e(ev["title"])}</p>{sub}{ecb_note}</div>'
            f'<p class="ev-meta"><small data-until="{e(ev["date"])}">{e(days_until(ev["date"], today))}</small>'
            f'<a href="{e(ev["source"]["url"])}" rel="noopener" target="_blank">{e(ev["source"]["name"])}</a></p></li>'
        )
    if not items:
        items = '<li class="nodata"><strong>No data</strong> <span>No official release dates could be read on this run.</span></li>'
    gap_html = "".join(f'<p class="nodata"><strong>{e(g["type"])} unavailable</strong> <span>{e(g["reason"])}.</span></p>' for g in gaps)
    verified = next((ev.get("verified") for ev in events if ev.get("verified")), None)
    notes = ""
    if verified:
        notes += f"<li>BLS blocks automated access, so its dates are copied from its schedule pages, last verified <time>{e(day_label(verified))}</time>.</li>"
    copies = {ev["copy"]["name"]: ev["copy"]["verified"] for ev in events if ev.get("copy")}
    for name, checked in copies.items():
        notes += f"<li>{e(name)} blocks this site's build server, so its dates come from a copy of its calendar, last verified <time>{e(day_label(checked))}</time>.</li>"
    notes_html = f'<ul class="cal-notes">{notes}</ul>' if notes else ""
    body = f'<div data-swap>{gap_html}<ol class="events">{items}</ol>{notes_html}</div>'
    names = {}
    for ev in events:
        names.setdefault(ev["source"]["name"], ev["source"])
    sources = [{**s, "asOf": None} for s in names.values()]
    return panel(idx, "calendar", "Calendar", body, provenance(sources, generated))


def curve_figure(curve: dict | None, css_class: str = "", gradient: str = "seabed") -> str:
    if not curve or not curve.get("points"):
        return (
            f'<figure class="curve curve-empty {css_class}"><figcaption class="nodata"><strong>No data</strong> '
            "<span>The US Treasury curve could not be fetched on this run.</span></figcaption></figure>"
        )
    pts = curve["points"]
    width, height, left, right, top, base = 760, 232, 18, 34, 30, 192
    low = min(v for _, v in pts)
    high = max(v for _, v in pts)
    span = (high - low) or 1
    step = (width - left - right) / (len(pts) - 1)
    xy = [(round(left + i * step, 1), round(base - (v - low) / span * (base - top), 1)) for i, (_, v) in enumerate(pts)]
    line = "M" + " ".join(f"{x} {y}" for x, y in xy)
    fill = f"{line} L{xy[-1][0]} {height - 24} L{xy[0][0]} {height - 24} Z"
    marks = ""
    for (tenor, value), (x, y) in zip(pts, xy):
        marks += f'<line class="depth" x1="{x}" x2="{x}" y1="{y}" y2="{height - 24}" />'
        marks += f'<text class="tenor" x="{x}" y="{height - 6}" text-anchor="middle">{e(tenor)}</text>'
        if tenor in ("2Y", "10Y", "30Y", "1M"):
            anchor = "end" if tenor == "30Y" else ("start" if tenor == "1M" else "middle")
            marks += f'<text class="yld" x="{x}" y="{y - 12}" text-anchor="{anchor}">{value:.2f}</text>'
        marks += f'<circle class="node" cx="{x}" cy="{y}" r="3.5" />'
        marks += f'<rect class="hit" x="{x - step / 2}" y="0" width="{step}" height="{height}" data-tip="{e(tenor)}: {value:.2f}%"><title>{e(tenor)}: {value:.2f}%</title></rect>'
    summary = ", ".join(f"{t} {v:.2f}%" for t, v in pts)
    return f"""<figure class="curve {css_class}">
  <svg viewBox="0 0 {width} {height}" role="img" aria-label="US Treasury par yield curve on {e(day_label(curve['date']))}: {e(summary)}">
    <defs><linearGradient id="{gradient}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="bed-a"/><stop offset="1" class="bed-b"/></linearGradient></defs>
    <path class="bed" d="{fill}" fill="url(#{gradient})"/>
    <path class="line" d="{line}"/>
    {marks}
  </svg>
  <figcaption>US Treasury par curve, % yield. <time>{e(day_label(curve['date']))}</time>. Source: <a href="{e(curve['source']['url'])}" rel="noopener" target="_blank">{e(curve['source']['name'])}</a></figcaption>
</figure>"""


def tape(panels: dict) -> str:
    rows = {r["id"]: r for p in panels.values() for r in p["rows"]}
    tiles = ""
    for rid in ("us10y", "dxy", "usdsgd", "brent", "spx", "vix"):
        row = rows.get(rid)
        if not row:
            continue
        if row["status"] == "empty":
            tiles += f'<li class="tile"><a href="#row-{rid}"><span class="t-name">{e(row["label"])}</span><span class="nodata"><strong>No data</strong></span></a></li>'
            continue
        one = row["change"]["d1"]
        tiles += (
            f'<li class="tile" data-tile="{rid}"{freshness_attrs(row)}><a href="#row-{rid}"><span class="t-name">{e(row["label"])}<span class="late-slot"></span></span>'
            f'<span class="t-val" data-num="{row["value"]}" data-dp="{row["dp"]}" data-suffix="{e(row.get("suffix", ""))}">{e(row["display"])}</span>'
            f'<span class="chg t-chg {one["d"]}"><span class="v"><span class="sr">One day change: </span>{e(one["t"])}</span></span></a></li>'
        )
    return f'<ul class="tape" aria-label="Key readings, change over one day">{tiles}</ul>'


def jsonld() -> str:
    """Data blocks are not executed, so the page's script-src CSP leaves them alone."""
    data = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Soundings",
        "url": f"{SITE_URL}/",
        "description": DESCRIPTION,
        "inLanguage": "en-SG",
    }
    return f'<script type="application/ld+json">{json.dumps(data)}</script>\n'


def render_dashboard(data: dict) -> str:
    gen = data["generated"]
    today = dt.datetime.fromisoformat(gen.replace("Z", "+00:00")).astimezone(SGT).date()
    p = data["panels"]
    extra = '<script src="assets/dash.js" defer></script>\n' + jsonld()
    jump = "".join(f'<li><a href="#{sid}">{e(label)}</a></li>' for sid, label in PANELS)
    return (
        head(TITLE, DESCRIPTION, "", "", "index,follow,max-image-preview:large", extra)
        + f'<body data-generated="{e(gen)}">\n'
        + header(gen, "", "dashboard")
        + f"""<main id="main">
<section class="hero" aria-labelledby="hero-h">
  {tape(p)}
  <div class="hero-grid">
    <div class="hero-copy">
      <h1 id="hero-h">The world's tape, <em>read from Singapore.</em></h1>
      <p>Rates, FX, commodities, equities, central banks and the release calendar. Every number carries its source and its time.</p>
    </div>
    {curve_figure(data.get('curve'), 'curve-hero', 'seabed')}
  </div>
  <nav class="jump" aria-label="Dashboard panels"><ul>{jump}</ul></nav>
</section>
"""
        + market_panel(1, "rates", p["rates"], gen, curve_figure(data.get("curve"), "curve-inline", "seabed-m"))
        + market_panel(2, "fx", p["fx"], gen)
        + market_panel(3, "commodities", p["commodities"], gen)
        + market_panel(4, "equities", p["equities"], gen)
        + banks_panel(5, data["banks"], today, gen)
        + calendar_panel(6, data["calendar"], data.get("calendarGaps", []), today, gen)
        + "</main>\n"
        + footer("")
        + '<div class="tip" id="tip" role="status" hidden></div>\n</body>\n</html>\n'
    )


def render_research() -> str:
    extra = '<script src="../assets/markdown.js" defer></script>\n<script src="../assets/research.js" defer></script>\n'
    chips = "".join(f'<button type="button" class="chip" data-tag="{t}" aria-pressed="false">{t}<span class="count"></span></button>' for t in ("Rates", "FX", "Commodities", "Equities"))
    tags = "".join(f'<option>{t}</option>' for t in ("Rates", "FX", "Commodities", "Equities"))
    panels = "".join(f'<label class="check"><input type="checkbox" name="panels" value="{pid}"><span>{label}</span></label>' for pid, label in PANELS)
    skeleton_rows = '<li class="row-skeleton" aria-hidden="true"><span class="skeleton sk-line"></span></li>' * 3
    return (
        head("Research | Soundings", "Private research notes.", "research/", "../", "noindex,nofollow", extra)
        + "<body>\n"
        + header(None, "../", "research")
        + f"""<main id="main" class="research">
<div id="library">
  <header class="lib-head">
    <div>
      <h1 id="lib-h" tabindex="-1">Research</h1>
      <p class="ledger-status" id="status" role="status">Loading reports</p>
    </div>
    <div class="lib-actions">
      <button type="button" class="btn" id="new-note">New note</button>
      <button type="button" class="btn" id="export">Export</button>
      <button type="button" class="btn" id="import">Import</button>
      <input type="file" id="import-file" accept=".zip,.json,application/zip,application/json" hidden>
    </div>
  </header>
  <section class="latest" id="latest" aria-label="Latest issue">
    <div class="latest-cover skeleton" aria-hidden="true"></div>
    <div class="latest-copy" aria-hidden="true"><span class="skeleton sk-line sk-short"></span><span class="skeleton sk-title"></span><span class="skeleton sk-line"></span><span class="skeleton sk-line"></span></div>
  </section>
  <section class="archive" aria-labelledby="archive-h">
    <div class="archive-controls">
      <div class="controls-top">
        <h2 id="archive-h">Archive</h2>
        <p class="count-line" role="status"><span id="result-count"></span> <button type="button" class="link-btn" id="reset" hidden>Reset filters</button></p>
      </div>
      <div class="controls-row">
        <label class="search"><span class="sr">Search reports</span><input type="search" id="search" placeholder="Search titles and text" autocomplete="off"></label>
        <div class="chips" role="group" aria-label="Filter by asset class">
          <button type="button" class="chip" data-tag="" aria-pressed="true">All<span class="count"></span></button>
          {chips}
        </div>
      </div>
    </div>
    <div class="row-head" aria-hidden="true"><span>No.</span><span>Title</span><span>Date</span><span>Tag</span></div>
    <ol class="note-list" id="list" aria-label="Reports">{skeleton_rows}</ol>
  </section>
</div>
<article class="report" id="report" hidden></article>
<dialog class="editor" id="editor" aria-labelledby="editor-h">
  <form id="editor-form" method="dialog" novalidate>
    <header class="editor-top">
      <h2 id="editor-h">New note</h2>
      <button type="button" class="btn ghost" id="editor-cancel">Cancel</button>
    </header>
    <div class="editor-fields">
      <label class="field title-field"><span>Title</span><input name="title" required maxlength="160" autocomplete="off"></label>
      <label class="field"><span>Date published</span><input name="date" type="date" required></label>
      <label class="field"><span>Asset class</span><select name="tag" required>{tags}</select></label>
    </div>
    <label class="field"><span>Summary, shown on the library page</span><input name="summary" maxlength="400" autocomplete="off"></label>
    <fieldset class="field panels-field"><legend>Linked dashboard panels</legend><div class="checks">{panels}</div></fieldset>
    <div class="editor-split">
      <label class="field"><span>Text, in Markdown</span><textarea name="body" rows="14" spellcheck="true"></textarea></label>
      <div class="field"><span id="preview-label">Preview</span><div class="md preview" id="preview" aria-labelledby="preview-label"></div></div>
    </div>
    <div class="field">
      <span id="pdf-label">PDF attachments</span>
      <div class="drop" id="drop">
        <label class="btn" for="pdf-input">Attach PDF</label>
        <input id="pdf-input" class="sr" type="file" accept="application/pdf,.pdf" multiple aria-labelledby="pdf-label">
        <span>or drop files here, up to 50 MB each</span>
      </div>
      <ul class="files" id="pdf-files"></ul>
      <p class="form-error" id="form-error" role="alert" hidden></p>
    </div>
    <footer class="editor-foot">
      <button type="button" class="btn danger" id="editor-delete" hidden>Delete note</button>
      <button type="submit" class="btn primary" id="editor-save">Save in this browser</button>
    </footer>
  </form>
</dialog>
</main>
<noscript><p class="noscript">The research library needs JavaScript to read reports.</p></noscript>
"""
        + footer("../")
        + "</body>\n</html>\n"
    )


def render_about() -> str:
    about = json.loads((ROOT / "data" / "about.json").read_text(encoding="utf-8"))
    bio = "".join(f"<p>{e(par)}</p>" for par in about["bio"])
    links = []
    if about.get("linkedin"):
        links.append(f'<li><a href="{e(about["linkedin"])}" rel="me noopener" target="_blank">LinkedIn</a></li>')
    if about.get("email"):
        links.append(f'<li><a href="mailto:{e(about["email"])}">{e(about["email"])}</a></li>')
    contact = f'<h2>Contact</h2><ul class="about-links">{"".join(links)}</ul>' if links else ""
    return (
        head("About | Soundings", f"About {about['name']} and the Soundings macro dashboard.", "about/", "../", "index,follow")
        + "<body>\n"
        + header(None, "../", "about")
        + f"""<main id="main" class="about">
  <h1>About</h1>
  <p class="about-name">{e(about['name'])}</p>
  <div class="prose">{bio}</div>
  {contact}
  <h2>This site</h2>
  <div class="prose">
    <p>Soundings puts rates, FX, commodities, equities, central banks and the release calendar on one page. Every number names its public source and its time, and a feed that fails says so instead of showing a stand-in.</p>
    <p>A sounding is a measured depth. <a href="../research/">Research</a> holds the written reports, and the <a href="../">dashboard</a> holds the readings behind them.</p>
  </div>
</main>
"""
        + footer("../")
        + "</body>\n</html>\n"
    )


def render_404() -> str:
    return (
        head("Page not found | Soundings", "This page does not exist.", "404.html", "/soundings/", "noindex")
        + "<body>\n"
        + '<main id="main" class="lost"><h1>No bottom at this depth.</h1><p><a href="/soundings/">Back to the dashboard</a></p></main>\n</body>\n</html>\n'
    )


def make_covers(entries: list[dict]) -> None:
    """Render page one of each report's first PDF, so the library never has to load a PDF to show a cover."""
    try:
        import pymupdf
    except ImportError:
        print("pymupdf is not installed, so reports get a text cover")
        return
    covers = OUT / "data" / "research" / "covers"
    covers.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        attachments = entry.get("attachments") or []
        if not attachments:
            continue
        try:
            with pymupdf.open(ROOT / "data" / "research" / attachments[0]["file"]) as doc:
                page = doc[0]
                zoom = COVER_WIDTH / page.rect.width
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                name = f'{entry["id"]}.jpg'
                pix.save(covers / name, jpg_quality=72)
                entry["cover"] = {"file": f"covers/{name}", "w": pix.width, "h": pix.height}
                entry["pages"] = doc.page_count
        except Exception as exc:  # noqa: BLE001 - a text cover is the fallback
            print(f"no cover for {entry['id']}: {exc}")


def previous_snapshot() -> dict | None:
    """The live site's last snapshot lets a failed feed keep its value, labelled stale."""
    url = os.environ.get("LAST_GOOD_URL", f"{SITE_URL}/data/market.json")
    local = ROOT / ".tmp" / "last_market.json"
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": feeds.UA}), timeout=20).read())
    except Exception:  # noqa: BLE001 - nothing published yet, or the site is unreachable
        return None


def main() -> None:
    data = feeds.collect(previous_snapshot())
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "site", OUT)
    (OUT / "index.html").write_text(render_dashboard(data), encoding="utf-8")
    (OUT / "data").mkdir(exist_ok=True)
    for panel_data in data["panels"].values():  # drawn into the HTML, so the JSON only needs the numbers
        for row in panel_data["rows"]:
            row.pop("spark", None)
    (OUT / "data" / "market.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shutil.copytree(ROOT / "data" / "research", OUT / "data" / "research")
    entries = json.loads((ROOT / "data" / "research" / "entries.json").read_text(encoding="utf-8"))
    make_covers(entries)
    (OUT / "data" / "research" / "entries.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "research").mkdir(exist_ok=True)
    (OUT / "research" / "index.html").write_text(render_research(), encoding="utf-8")
    (OUT / "about").mkdir(exist_ok=True)
    (OUT / "about" / "index.html").write_text(render_about(), encoding="utf-8")
    (OUT / "404.html").write_text(render_404(), encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    lastmod = data["generated"][:10]
    (OUT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{SITE_URL}/</loc><lastmod>{lastmod}</lastmod><changefreq>hourly</changefreq></url>"
        f"<url><loc>{SITE_URL}/about/</loc><lastmod>{lastmod}</lastmod></url></urlset>\n",
        encoding="utf-8",
    )
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    missing = [r["id"] for p in data["panels"].values() for r in p["rows"] if r["status"] == "empty"]
    print(f"built {data['generated']}; empty rows: {missing or 'none'}; banks empty: {[b['id'] for b in data['banks'] if b['status'] == 'empty'] or 'none'}")


if __name__ == "__main__":
    main()

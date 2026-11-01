"""Fetch every Soundings series from free public sources and normalise it.

Rules enforced here, not in the page:
  * A value only ever comes from a parsed source response. There are no defaults.
  * A failed feed keeps its last good value for at most STALE_DAYS, flagged stale.
    After that the row is an explicit empty state.
  * Stdlib only, plus pypdf for the BoJ statement, so CI needs no API keys.
"""
from __future__ import annotations

import calendar as cal
import csv
import datetime as dt
import html
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
UTC = dt.timezone.utc
SGT = dt.timezone(dt.timedelta(hours=8), "SGT")
STALE_DAYS = 7
SPARK_POINTS = 66  # about three months of daily closes

MAS_SGS_URL = "https://eservices.mas.gov.sg/statistics/fdanet/BenchmarkPricesAndYields.aspx"
MAS_ARC_URL = "https://www.mas.gov.sg/monetary-policy/advance-release-calendar"
SINGSTAT_ARC_URL = "https://www.singstat.gov.sg/data-tools-services/advance-release-calendar"
SINGSTAT_RATES_URL = "https://tablebuilder.singstat.gov.sg/api/table/tabledata/M700071?limit=2000"
SINGSTAT_RATES_PAGE = "https://tablebuilder.singstat.gov.sg/table/TS/M700071"
TREASURY_PAGE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"
FED_OPEN_MARKET = "https://www.federalreserve.gov/monetarypolicy/openmarket.htm"
FOMC_CALENDAR = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED_FEED = "https://www.federalreserve.gov/feeds/press_monetary.xml"
ECB_FEED = "https://www.ecb.europa.eu/rss/press.html"
ECB_CALENDAR = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
BOJ_SCHEDULE = "https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm"
LPR_JSON = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/bk-lpr.json"
LPR_PAGE = "https://www.chinamoney.com.cn/english/bmklpr/"
MAS_MPS_LIST = "https://www.mas.gov.sg/monetary-policy/past-monetary-policy-decisions"
BEA_SCHEDULE = "https://www.bea.gov/news/schedule"
BEA_NEXT_YEAR = "https://www.bea.gov/news/schedule/next-year"
EUROSTAT_CALENDAR = "https://ec.europa.eu/eurostat/news/release-calendar"
EUROSTAT_EVENTS = "https://ec.europa.eu/eurostat/o/calendars/eventsJson"
ECB_HICP_CALENDAR = "https://www.ecb.europa.eu/press/calendars/statscal/ges/html/sthicp.en.html"


class FeedError(Exception):
    """A source could not produce usable data. The message is shown to the reader."""


def http(url: str, data: dict | None = None, timeout: int = 30, headers: dict | None = None) -> bytes:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    hdrs = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-SG,en;q=0.9"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def text_of(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style).*?</\1>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def date_as_of(day: dt.date) -> str:
    """As-of for a series published by date only: close of that date in UTC terms."""
    return day.isoformat()


# --- market data -----------------------------------------------------------

def yahoo(symbol: str, intraday_daily: bool = False) -> tuple[list[tuple[dt.date, float]], str]:
    """Daily closes and last trade time from Yahoo Finance's public chart endpoint."""
    interval, rng = ("60m", "3mo") if intraday_daily else ("1d", "6mo")
    last_err: Exception | None = None
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={rng}&interval={interval}"
        try:
            result = json.loads(http(url, timeout=20))["chart"]["result"][0]
            break
        except Exception as exc:  # noqa: BLE001 - try the mirror host
            last_err = exc
    else:
        raise FeedError(f"Yahoo Finance unavailable for {symbol}: {last_err}")

    meta = result["meta"]
    # Daily bars are stamped at local midnight, but gmtoffset is today's offset. Across a DST
    # change that would push bars into the previous day, so the extra two hours absorb it.
    offset = dt.timedelta(seconds=meta.get("gmtoffset", 0)) + dt.timedelta(hours=2)
    closes = result["indicators"]["quote"][0]["close"]
    by_day: dict[dt.date, tuple[float, int]] = {}
    for ts, close in zip(result.get("timestamp", []), closes):
        if close is None:
            continue
        day = (dt.datetime.fromtimestamp(ts, UTC) + offset).date()
        if day.weekday() >= 5:  # FX vendors emit thin weekend prints; they are not sessions
            continue
        by_day[day] = (float(close), ts)
    if not by_day:
        raise FeedError(f"Yahoo Finance returned no closes for {symbol}")

    days = sorted(by_day)
    obs = [(d, by_day[d][0]) for d in days]
    last_day = days[-1]
    market_ts = meta.get("regularMarketTime") or by_day[last_day][1]
    market_price = meta.get("regularMarketPrice")
    market_day = (dt.datetime.fromtimestamp(market_ts, UTC) + offset).date()
    if market_day == last_day and market_price is not None:
        obs[-1] = (last_day, float(market_price))
        return obs, iso(dt.datetime.fromtimestamp(market_ts, UTC))
    # The vendor's last print is outside a session, so all we can claim is the session date.
    return obs, last_day.isoformat()


def fred(series: str) -> list[tuple[dt.date, float]]:
    """Keyless FRED CSV, used only as a fallback when the primary feed fails."""
    start = (dt.date.today() - dt.timedelta(days=200)).isoformat()
    raw = http(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}", timeout=20)
    obs = []
    for row in list(csv.reader(io.StringIO(raw.decode())))[1:]:
        try:
            obs.append((dt.date.fromisoformat(row[0]), float(row[1])))
        except (ValueError, IndexError):
            continue
    if not obs:
        raise FeedError(f"FRED returned no observations for {series}")
    return obs


def treasury() -> dict[str, list[tuple[dt.date, float]]]:
    """US Treasury daily par yield curve, keyed by tenor label."""
    year = dt.date.today().year
    rows: list[dict] = []
    for y in (year - 1, year):
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            f"daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv"
        )
        rows += list(csv.DictReader(io.StringIO(http(url).decode("utf-8-sig"))))
    out: dict[str, list[tuple[dt.date, float]]] = {}
    for row in rows:
        day = dt.datetime.strptime(row["Date"], "%m/%d/%Y").date()
        for tenor, value in row.items():
            if tenor == "Date" or not value:
                continue
            out.setdefault(tenor, []).append((day, float(value)))
    for series in out.values():
        series.sort()
    if "2 Yr" not in out or "10 Yr" not in out:
        raise FeedError("Treasury CSV is missing the 2Y or 10Y column")
    return out


MONTHS = {m: i for i, m in enumerate(cal.month_name) if m}
MON = {m: i for i, m in enumerate(cal.month_abbr) if m}


def fed_target() -> dict:
    """Fed funds target range history from the Board's open market operations page."""
    page = http(FED_OPEN_MARKET).decode("utf-8", "ignore")
    changes: list[tuple[dt.date, float, float]] = []
    for year, block in re.findall(r"<h4>(\d{4})</h4>(.*?)(?=<h4>|\Z)", page, flags=re.S):
        for label, level in re.findall(
            r"<td[^>]*>\s*([A-Z][a-z]+ \d{1,2})\s*</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>\s*([\d.]+)-([\d.]+)".replace("([\\d.]+)-([\\d.]+)", "([\\d.]+-[\\d.]+)"),
            block,
        ):
            month, day = label.split()
            lo, hi = level.split("-")
            changes.append((dt.date(int(year), MONTHS[month], int(day)), float(lo), float(hi)))
    if not changes:
        raise FeedError("Could not parse the Fed target range table")
    changes.sort()
    today = dt.date.today()
    obs: list[tuple[dt.date, float]] = []
    day = today - dt.timedelta(days=190)
    while day <= today:
        live = [c for c in changes if c[0] <= day]
        if live and day.weekday() < 5:
            obs.append((day, live[-1][2]))
        day += dt.timedelta(days=1)
    eff, lo, hi = changes[-1]
    return {"obs": obs, "lo": lo, "hi": hi, "effective": eff}


# --- MAS statistics (ASP.NET forms) ----------------------------------------

def _mas_page(url: str) -> str:
    page = http(url, timeout=30).decode("utf-8", "ignore")
    if "scheduled maintenance" in page.lower():
        raise FeedError("MAS statistics site is under scheduled maintenance")
    return page


def _hidden_fields(page: str) -> dict:
    fields = {}
    for tag in re.findall(r"<input[^>]+type=\"hidden\"[^>]*>", page):
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name:
            fields[name.group(1)] = html.unescape(value.group(1)) if value else ""
    return fields


def _select_value(page: str, name_part: str, want: str) -> tuple[str, str]:
    """Return (field name, option value) for the option whose label or value matches want."""
    match = re.search(rf'<select[^>]+name="([^"]*{name_part}[^"]*)"[^>]*>(.*?)</select>', page, flags=re.S)
    if not match:
        raise FeedError(f"MAS form changed: no {name_part} selector")
    want_l = want.lower()
    for value, label in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)', match.group(2)):
        if want_l in (value.lower(), label.strip().lower()) or label.strip().lower().startswith(want_l):
            return match.group(1), value
    raise FeedError(f"MAS form changed: {want} not offered in {name_part}")


def _mas_form(page: str, checks: list[str], button: str, extra: dict | None = None) -> dict:
    today = dt.date.today()
    start = today - dt.timedelta(days=200)
    form = _hidden_fields(page)
    for part, want in (
        ("StartYear", str(start.year)),
        ("EndYear", str(today.year)),
        ("StartMonth", cal.month_abbr[start.month]),
        ("EndMonth", cal.month_abbr[today.month]),
    ):
        name, value = _select_value(page, part, want)
        form[name] = value
    for name in checks:
        form[name] = "on"
    form[button] = "Display"
    form.update(extra or {})
    return form


def _table_obs(page: str, wanted: dict[str, str]) -> dict[str, list[tuple[dt.date, float]]]:
    """Parse a MAS results table: carries year and month down through merged cells."""
    out: dict[str, list[tuple[dt.date, float]]] = {key: [] for key in wanted}
    for table in re.findall(r"<table.*?</table>", page, flags=re.S | re.I):
        rows = []
        for tr in re.findall(r"<tr.*?</tr>", table, flags=re.S | re.I):
            cells = [text_of(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S | re.I)]
            if cells:
                rows.append(cells)
        header = next((r for r in rows if any(all(w.lower() in c.lower() for w in next(iter(wanted.values())).split("|")) for c in r)), None)
        if not header:
            continue
        cols = {}
        for key, needle in wanted.items():
            for i, cell in enumerate(header):
                if all(w.lower() in cell.lower() for w in needle.split("|")):
                    cols[key] = len(header) - i
                    break
        year = month = None
        for row in rows[rows.index(header) + 1:]:
            lead = " ".join(row[: max(1, len(row) - min(cols.values()))])
            m_full = re.search(r"(\d{1,2})\s+([A-Z][a-z]{2})[a-z]*\s+(\d{4})", lead)
            if m_full:
                day_n, month, year = int(m_full.group(1)), MON[m_full.group(2)], int(m_full.group(3))
            else:
                y = re.search(r"\b(19|20)\d{2}\b", lead)
                if y:
                    year = int(y.group(0))
                mo = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", lead)
                if mo:
                    month = MON[mo.group(1)]
                rest = re.sub(r"\b(19|20)\d{2}\b|[A-Za-z]+", " ", lead)
                d = re.search(r"\b(\d{1,2})\b", rest)
                if not (d and year and month):
                    continue
                day_n = int(d.group(1))
            for key, from_end in cols.items():
                if from_end > len(row):
                    continue
                try:
                    out[key].append((dt.date(year, month, day_n), float(row[-from_end].replace(",", ""))))
                except ValueError:
                    continue
    return out


def mas_sgs() -> dict[str, list[tuple[dt.date, float]]]:
    page = _mas_page(MAS_SGS_URL)
    name, daily = _select_value(page, "FrequencyDropDownList", "Daily")
    prefix = "ctl00$ContentPlaceHolder1$"
    form = _mas_form(
        page,
        [prefix + "TwoYearBondYieldCheckBox", prefix + "TenYearBondYieldCheckBox"],
        prefix + "DisplayButton",
        {name: daily},
    )
    result = http(MAS_SGS_URL, data=form, timeout=40).decode("utf-8", "ignore")
    obs = _table_obs(result, {"sgs2y": "2-Year|Yield", "sgs10y": "10-Year|Yield"})
    if not obs["sgs2y"] or not obs["sgs10y"]:
        raise FeedError("MAS returned no SGS benchmark yield rows")
    return {k: sorted(set(v)) for k, v in obs.items()}


def singstat_sgs() -> dict:
    """Month-end SGS yields from SingStat, the fallback when the MAS site is down."""
    data = json.loads(http(SINGSTAT_RATES_URL))["Data"]
    out: dict = {"updated": data.get("dataLastUpdated")}
    for row in data["row"]:
        for key, label in (("sgs2y", "2-Year Bond Yield"), ("sgs10y", "10-Year Bond Yield")):
            if label in row["rowText"]:
                obs = []
                for col in row["columns"][-14:]:
                    year, month = col["key"].split()
                    last = cal.monthrange(int(year), MON[month])[1]
                    obs.append((dt.date(int(year), MON[month], last), float(col["value"])))
                out[key] = obs
    if "sgs2y" not in out or "sgs10y" not in out:
        raise FeedError("SingStat table M700071 is missing SGS yields")
    return out


# --- row construction ------------------------------------------------------

def _reference(obs, target: dt.date):
    candidates = [o for o in obs if o[0] <= target]
    return candidates[-1] if candidates else None


def _month_back(day: dt.date) -> dt.date:
    year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return dt.date(year, month, min(day.day, cal.monthrange(year, month)[1]))


def _fmt_change(last: float, ref: float, kind: str, dp: int) -> tuple[float, str]:
    sign = lambda v: "+" if v > 0 else ("−" if v < 0 else "")  # noqa: E731
    if kind == "pct":
        val = (last / ref - 1) * 100
        return round(val, 4), f"{sign(round(val, 2))}{abs(val):.2f}%"
    if kind == "bp":
        val = (last - ref) * 100
        return round(val, 2), f"{sign(round(val, 1))}{abs(val):.0f} bp" if abs(val - round(val)) < 0.05 else f"{sign(round(val, 1))}{abs(val):.1f} bp"
    if kind == "bpv":
        val = last - ref
        return round(val, 2), f"{sign(round(val, 1))}{abs(val):.0f} bp"
    val = last - ref
    return round(val, 4), f"{sign(round(val, dp))}{abs(val):,.{dp}f}"


def _change(obs, ref, kind, dp):
    if ref is None:
        return {"t": "n/a", "d": "na"}
    value, label = _fmt_change(obs[-1][1], ref[1], kind, dp)
    direction = "up" if value > 1e-9 else ("down" if value < -1e-9 else "flat")
    return {"t": label if direction != "flat" else ("0 bp" if kind in ("bp", "bpv") else label), "d": direction, "v": value, "ref": ref[0].isoformat()}


def spark(obs, width: int = 132, height: int = 34, pad: int = 4) -> dict:
    tail = obs[-SPARK_POINTS:]
    values = [v for _, v in tail]
    low, high = min(values), max(values)
    span = (high - low) or 1
    step = (width - 2 * pad) / max(1, len(tail) - 1)
    pts = [(round(pad + i * step, 2), round(height - pad - (v - low) / span * (height - 2 * pad), 2)) for i, v in enumerate(values)]
    return {
        "d": "M" + " ".join(f"{x} {y}" for x, y in pts),
        "end": pts[-1],
        "w": width,
        "h": height,
    }


def make_row(meta: dict, obs, source: dict, as_of: str, display: str | None = None, freq: str = "daily", note: str | None = None) -> dict:
    kind, dp = meta["kind"], meta["dp"]
    last_day, last_value = obs[-1]
    prior = obs[-2] if len(obs) > 1 else None
    week = _reference(obs, last_day - dt.timedelta(days=7))
    month = _reference(obs, _month_back(last_day))
    changes = {
        "d1": _change(obs, prior if freq == "daily" else None, kind, dp),
        "w1": _change(obs, week if freq == "daily" else None, kind, dp),
        "m1": _change(obs, month if freq == "daily" else prior, kind, dp),
    }
    tail = obs[-SPARK_POINTS:]
    low = min(tail, key=lambda o: o[1])
    high = max(tail, key=lambda o: o[1])
    number = f"{last_value:,.{dp}f}"
    return {
        **meta,
        "status": "ok",
        "value": round(last_value, 6),
        "display": display or (number + meta.get("suffix", "")),
        "change": changes,
        "spark": spark(obs),
        "series": [[d.isoformat(), round(v, 6)] for d, v in tail],
        "low": [low[0].isoformat(), round(low[1], 6)],
        "high": [high[0].isoformat(), round(high[1], 6)],
        "asOf": as_of,
        "lastObs": last_day.isoformat(),
        "freq": freq,
        "source": source,
        "note": note,
    }


def empty_row(meta: dict, reason: str, source: dict) -> dict:
    return {**meta, "status": "empty", "reason": reason, "source": source}


SRC_YAHOO = {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/"}
SRC_FRED = {"name": "FRED, St. Louis Fed", "url": "https://fred.stlouisfed.org/"}
SRC_TREASURY = {"name": "US Treasury", "url": TREASURY_PAGE}
SRC_FED = {"name": "Federal Reserve Board", "url": FED_OPEN_MARKET}
SRC_MAS_SGS = {"name": "MAS", "url": MAS_SGS_URL}
SRC_SINGSTAT = {"name": "SingStat", "url": SINGSTAT_RATES_PAGE}

MARKET = {
    "fx": [
        ("dxy", "DXY", "US dollar index", "DX-Y.NYB", "pct", 2, None),
        ("usdsgd", "USD/SGD", "Spot", "SGD=X", "pct", 4, "DEXSIUS"),
        ("usdjpy", "USD/JPY", "Spot", "JPY=X", "pct", 2, "DEXJPUS"),
        ("eurusd", "EUR/USD", "Spot", "EURUSD=X", "pct", 4, "DEXUSEU"),
        ("usdcnh", "USD/CNH", "Offshore yuan, hourly feed closed by day", "CNH=X", "pct", 4, None),
    ],
    "commodities": [
        ("brent", "Brent", "ICE front-month, USD/bbl", "BZ=F", "pct", 2, "DCOILBRENTEU"),
        ("wti", "WTI", "NYMEX front-month, USD/bbl", "CL=F", "pct", 2, "DCOILWTICO"),
        ("gold", "Gold", "COMEX front-month, USD/oz", "GC=F", "pct", 2, None),
        ("copper", "Copper", "COMEX front-month, USD/lb", "HG=F", "pct", 3, None),
    ],
    "equities": [
        ("spx", "S&P 500", "United States", "^GSPC", "pct", 2, "SP500"),
        ("sti", "STI", "Singapore", "^STI", "pct", 2, None),
        ("nky", "Nikkei 225", "Japan", "^N225", "pct", 2, "NIKKEI225"),
        ("hsi", "Hang Seng", "Hong Kong", "^HSI", "pct", 2, None),
        ("vix", "VIX", "Cboe implied volatility, points", "^VIX", "pts", 2, "VIXCLS"),
    ],
}


def market_row(spec) -> dict:
    rid, label, sub, symbol, kind, dp, fred_id = spec
    meta = {"id": rid, "label": label, "sub": sub, "kind": kind, "dp": dp}
    intraday = rid == "usdcnh"
    try:
        obs, as_of = yahoo(symbol, intraday_daily=intraday)
        return make_row(meta, obs, SRC_YAHOO, as_of)
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
    if fred_id:
        try:
            obs = fred(fred_id)
            return make_row(meta, obs, {**SRC_FRED, "url": f"https://fred.stlouisfed.org/series/{fred_id}"}, date_as_of(obs[-1][0]), note="Yahoo feed down, using the FRED daily series")
        except Exception as exc:  # noqa: BLE001
            reason += f"; FRED fallback failed: {exc}"
    return empty_row(meta, reason, SRC_YAHOO)


def rates_rows() -> tuple[list[dict], dict | None]:
    meta = {
        "us2y": {"id": "us2y", "label": "US 2Y", "sub": "Treasury par yield", "kind": "bp", "dp": 2, "suffix": "%"},
        "us10y": {"id": "us10y", "label": "US 10Y", "sub": "Treasury par yield", "kind": "bp", "dp": 2, "suffix": "%"},
        "us2s10s": {"id": "us2s10s", "label": "US 2s10s", "sub": "10Y minus 2Y", "kind": "bpv", "dp": 0, "suffix": " bp"},
        "fftarget": {"id": "fftarget", "label": "Fed funds target", "sub": "Upper bound drives the changes", "kind": "bp", "dp": 2},
        "sgs2y": {"id": "sgs2y", "label": "SGS 2Y", "sub": "Benchmark yield", "kind": "bp", "dp": 2, "suffix": "%"},
        "sgs10y": {"id": "sgs10y", "label": "SGS 10Y", "sub": "Benchmark yield", "kind": "bp", "dp": 2, "suffix": "%"},
    }
    rows: dict[str, dict] = {}
    curve = None
    try:
        tre = treasury()
        two, ten = tre["2 Yr"], tre["10 Yr"]
        rows["us2y"] = make_row(meta["us2y"], two, SRC_TREASURY, date_as_of(two[-1][0]))
        rows["us10y"] = make_row(meta["us10y"], ten, SRC_TREASURY, date_as_of(ten[-1][0]))
        twos = dict(two)
        spread = [(d, round((v - twos[d]) * 100, 2)) for d, v in ten if d in twos]
        rows["us2s10s"] = make_row(meta["us2s10s"], spread, SRC_TREASURY, date_as_of(spread[-1][0]))
        tenors = ["1 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr"]
        last_day = ten[-1][0]
        curve = {
            "date": last_day.isoformat(),
            "points": [[t.replace(" Mo", "M").replace(" Yr", "Y"), dict(tre[t])[last_day]] for t in tenors if last_day in dict(tre.get(t, []))],
            "source": SRC_TREASURY,
        }
    except Exception as exc:  # noqa: BLE001
        reason = f"US Treasury feed failed: {exc}"
        fallback = {}
        for key, series in (("us2y", "DGS2"), ("us10y", "DGS10"), ("us2s10s", "T10Y2Y")):
            try:
                obs = fred(series)
                if key == "us2s10s":
                    obs = [(d, round(v * 100, 2)) for d, v in obs]
                fallback[key] = make_row(meta[key], obs, {**SRC_FRED, "url": f"https://fred.stlouisfed.org/series/{series}"}, date_as_of(obs[-1][0]), note="Treasury feed down, using FRED")
            except Exception:  # noqa: BLE001
                fallback[key] = empty_row(meta[key], reason, SRC_TREASURY)
        rows.update(fallback)

    try:
        fed = fed_target()
        rows["fftarget"] = make_row(
            meta["fftarget"], fed["obs"], SRC_FED, date_as_of(fed["effective"]),
            display=f"{fed['lo']:.2f}–{fed['hi']:.2f}%",
            note=f"Range in effect since {fed['effective'].isoformat()}",
        )
    except Exception as exc:  # noqa: BLE001
        rows["fftarget"] = empty_row(meta["fftarget"], f"Federal Reserve page failed: {exc}", SRC_FED)

    try:
        sgs = mas_sgs()
        for key in ("sgs2y", "sgs10y"):
            rows[key] = make_row(meta[key], sgs[key], SRC_MAS_SGS, date_as_of(sgs[key][-1][0]))
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
        try:
            monthly = singstat_sgs()
            for key in ("sgs2y", "sgs10y"):
                rows[key] = make_row(
                    meta[key], monthly[key], SRC_SINGSTAT, date_as_of(monthly[key][-1][0]), freq="monthly",
                    note=f"Month-end print. Daily MAS feed unavailable: {reason}",
                )
        except Exception as inner:  # noqa: BLE001
            for key in ("sgs2y", "sgs10y"):
                rows[key] = empty_row(meta[key], f"{reason}; SingStat fallback failed: {inner}", SRC_MAS_SGS)

    return [rows[k] for k in ("us2y", "us10y", "us2s10s", "fftarget", "sgs2y", "sgs10y")], curve


# --- schedules -------------------------------------------------------------

def _next_after(dates, today):
    return next((d for d in sorted(dates, key=lambda x: x["date"]) if d["date"] >= today.isoformat()), None)


def fomc_dates() -> list[dict]:
    page = http(FOMC_CALENDAR).decode("utf-8", "ignore")
    out = []
    for year, block in re.findall(r"(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|\Z)", page, flags=re.S):
        parts = re.findall(r"fomc-meeting__(?:month|date)[^>]*>(?:\s*<strong>)?\s*([^<]+)", block)
        for month, days in zip(parts[0::2], parts[1::2]):
            sep = "*" in days
            days = days.replace("*", "").strip()
            if "/" in month:
                m1, m2 = month.split("/")
            else:
                m1 = m2 = month
            if m1.strip() not in MONTHS or not re.match(r"^\d+(-\d+)?$", days):
                continue
            first, last = (days.split("-") + [days])[:2]
            out.append({
                "start": dt.date(int(year), MONTHS[m1.strip()], int(first)).isoformat(),
                "date": dt.date(int(year), MONTHS[m2.strip()], int(last)).isoformat(),
                "sep": sep,
            })
    if not out:
        raise FeedError("Could not parse the FOMC calendar")
    return out


def ecb_dates() -> list[dict]:
    page = text_of(http(ECB_CALENDAR).decode("utf-8", "ignore"))
    pairs = re.findall(r"(\d{2})/(\d{2})/(\d{4}) Governing Council of the ECB: monetary policy meeting[^0-9]*?\(Day (\d)", page)
    out = []
    first_day = None
    for d, m, y, day in pairs:
        if day == "1":
            first_day = f"{y}-{m}-{d}"
        elif day == "2":
            out.append({"date": f"{y}-{m}-{d}", "start": first_day or f"{y}-{m}-{d}"})
    if not out:
        raise FeedError("Could not parse the ECB calendar")
    return out


def boj_dates() -> list[dict]:
    page = http(BOJ_SCHEDULE).decode("utf-8", "ignore")
    out = []
    for year, block in re.findall(r"<h\d[^>]*>[^<]*?(20\d{2})[^<]*</h\d>.*?(<table.*?</table>)", page, flags=re.S):
        for tr in re.findall(r"<tr.*?</tr>", block, flags=re.S):
            cell = re.search(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)
            if not cell:
                continue
            m = re.match(r"([A-Z][a-z]+)\.? (\d{1,2}) \(\w+\.?\)(?:, (\d{1,2}) \(\w+\.?\))?", text_of(cell.group(1)))
            if m:
                month = MON.get(m.group(1)[:3])
                day = int(m.group(3) or m.group(2))
                if month:
                    out.append({"date": dt.date(int(year), month, day).isoformat()})
    if not out:
        raise FeedError("Could not parse the BoJ schedule")
    return out


def mas_arc() -> dict:
    page = text_of(http(MAS_ARC_URL).decode("utf-8", "ignore"))
    mps = re.search(r"Quarterly (NLT )?(\d{2} [A-Z][a-z]{2} \d{4}) Statement for ([A-Z][a-z]{2} \d{4})", page)
    if not mps:
        raise FeedError("Could not find the MPS date in the MAS calendar")
    day = dt.datetime.strptime(mps.group(2), "%d %b %Y").date()
    return {"date": day.isoformat(), "nlt": bool(mps.group(1)), "edition": mps.group(3)}


def singstat_arc() -> list[dict]:
    page = http(SINGSTAT_ARC_URL).decode("utf-8", "ignore").replace('\\"', '"').replace("\\u0026", "&")
    out = []
    for match in re.finditer(r'"title":"((?:CPI For General Households|GDP), [^"]+)","state":"([^"]*)","description":([^,]*),"frequency":"[^"]*","release_date":"(\d{4}-\d{2}-\d{2})"', page):
        title, _, desc, day = match.groups()
        kind = "SG CPI" if title.startswith("CPI") else "SG GDP"
        nlt = "not later" in desc.lower()
        out.append({"type": kind, "title": title.strip(), "date": day, "nlt": nlt})
    if not out:
        raise FeedError("Could not parse the SingStat release calendar")
    unique = {(e["type"], e["date"]): e for e in out}
    return sorted(unique.values(), key=lambda e: e["date"])


def bea_pce() -> list[dict]:
    """Personal Income and Outlays release dates, the release that carries the PCE price index."""
    out = []
    for url in (BEA_SCHEDULE, BEA_NEXT_YEAR):
        page = text_of(http(url).decode("utf-8", "ignore"))
        for month, day, clock, reference in re.findall(
            r"([A-Z][a-z]+) (\d{1,2}) (\d{1,2}:\d{2} [AP]M) N ?ews Personal Income and Outlays, ([A-Z][a-z]+ \d{4})", page
        ):
            ref_month, ref_year = reference.split()
            if month not in MONTHS or ref_month not in MONTHS:
                continue
            year = int(ref_year) + (1 if MONTHS[month] <= MONTHS[ref_month] else 0)
            hour, minute = clock.split()[0].split(":")
            hour_24 = int(hour) % 12 + (12 if clock.endswith("PM") else 0)
            out.append({"reference": reference, "date": dt.date(year, MONTHS[month], int(day)).isoformat(), "et": f"{hour_24:02d}:{minute}"})
    if not out:
        raise FeedError("Could not parse the BEA release schedule")
    return sorted({e["date"]: e for e in out}.values(), key=lambda e: e["date"])


def _cet_offset(day: dt.date) -> int:
    """UTC offset of Central European time: summer time runs from March's last Sunday to October's."""
    last_sunday = lambda m: [d for d in cal.Calendar().itermonthdates(day.year, m) if d.month == m and d.weekday() == 6][-1]  # noqa: E731
    return 2 if last_sunday(3) <= day < last_sunday(10) else 1


def ecb_hicp() -> dict[str, dict]:
    """ECB seasonally adjusted HICP release dates and times, keyed by date."""
    page = text_of(http(ECB_HICP_CALENDAR).decode("utf-8", "ignore"))
    out = {}
    for d, m, y, clock, name, reference in re.findall(
        r"(\d{2})/(\d{2})/(\d{4}) (\d{2}:\d{2}) Euro area seasonally adjusted HICP( flash estimate)? \(Dataset: HICP\) Reference period: ([A-Z][a-z]{2}-\d{4})", page
    ):
        day = dt.date(int(y), int(m), int(d))
        hour, minute = clock.split(":")
        release = dt.datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=dt.timezone(dt.timedelta(hours=_cet_offset(day))))
        sgt = release.astimezone(SGT)
        out[day.isoformat()] = {"flash": bool(name), "reference": reference.replace("-", " "), "timeSgt": sgt.strftime("%H:%M"), "dateSgt": sgt.date().isoformat()}
    if not out:
        raise FeedError("Could not parse the ECB HICP calendar")
    return out


def eurostat_hicp(today: dt.date) -> list[dict]:
    """Headline HICP dates. Eurostat stamps every calendar entry 11:00 and all-day, so only the date is usable."""
    query = urllib.parse.urlencode({
        "theme": 0, "category": 0, "keywords": "", "isEuroindicator": "", "authorInclude": "", "authorExclude": "",
        "start": f"{today.isoformat()}T00:00:00+01:00",
        "end": f"{(today + dt.timedelta(days=150)).isoformat()}T00:00:00+01:00",
        "timeZone": "Europe/Luxembourg",
    })
    out = []
    for entry in json.loads(http(f"{EUROSTAT_EVENTS}?{query}")):
        title = entry.get("title", "").strip()
        if title == "Flash estimate inflation euro area":
            flash = True
        elif title == "Inflation (HICP)":
            flash = False
        else:
            continue
        out.append({"flash": flash, "date": entry["start"][:10], "reference": entry.get("period", "")})
    if not out:
        raise FeedError("Eurostat calendar lists no HICP releases")
    return out


def _us_eastern_offset(day: dt.date) -> int:
    """UTC offset of US Eastern on a date: DST runs from March's 2nd Sunday to November's 1st."""
    second_sun_mar = [d for d in cal.Calendar().itermonthdates(day.year, 3) if d.month == 3 and d.weekday() == 6][1]
    first_sun_nov = [d for d in cal.Calendar().itermonthdates(day.year, 11) if d.month == 11 and d.weekday() == 6][0]
    return -4 if second_sun_mar <= day < first_sun_nov else -5


def _snapshot(name: str) -> dict:
    return json.loads((ROOT / "data" / "curated" / name).read_text(encoding="utf-8"))


def bls_events(today: dt.date) -> list[dict]:
    curated = json.loads((ROOT / "data" / "curated" / "bls_schedule.json").read_text(encoding="utf-8"))
    out = []
    for kind, label, key, url in (
        ("US CPI", "Consumer Price Index", "cpi", curated["source_cpi"]),
        ("US NFP", "Employment Situation", "nfp", curated["source_nfp"]),
    ):
        for reference, day in curated[key]:
            date = dt.date.fromisoformat(day)
            hour, minute = curated["release_et"].split(":")
            release = dt.datetime(date.year, date.month, date.day, int(hour), int(minute), tzinfo=dt.timezone(dt.timedelta(hours=_us_eastern_offset(date))))
            sgt = release.astimezone(SGT)
            out.append({
                "type": kind,
                "title": f"{label}, {reference}",
                "date": day,
                "timeSgt": sgt.strftime("%H:%M"),
                "dateSgt": sgt.date().isoformat(),
                "source": {"name": "BLS release schedule", "url": url},
                "verified": curated["verified"],
            })
    return [e for e in out if e["date"] >= today.isoformat()]


# --- central banks ---------------------------------------------------------

def _rss_items(xml: str) -> list[dict]:
    items = []
    for block in re.findall(r"<item>(.*?)</item>", xml, flags=re.S):
        get = lambda tag: (re.search(rf"<{tag}>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{tag}>", block, flags=re.S) or [None, ""])[1].strip()  # noqa: E731
        items.append({"title": html.unescape(get("title")), "link": get("link"), "pubDate": get("pubDate")})
    return items


def _rss_date(stamp: str) -> dt.date:
    return dt.datetime.strptime(" ".join(stamp.split()[1:4]), "%d %b %Y").date()


def bank_fed(today) -> dict:
    latest = next(i for i in _rss_items(http(FED_FEED).decode("utf-8", "ignore")) if "issues FOMC statement" in i["title"])
    text = text_of(http(latest["link"]).decode("utf-8", "ignore"))
    decision = re.search(r"The Committee decided to [^.]*?target range for the federal funds rate[^.]*\.", text)
    vote = re.search(r"by a (\d+\s*[–-]\s*\d+) vote", text)
    if not decision:
        raise FeedError("FOMC statement wording not recognised")
    sentence = decision.group(0).split(", in support")[0].rstrip(".") + "."
    if vote:
        sentence += f" Vote {re.sub(r'\s*[–-]\s*', chr(0x2013), vote.group(1))}."
    dates = fomc_dates()
    nxt = _next_after([d for d in dates if d["date"] > _rss_date(latest["pubDate"]).isoformat()], today)
    fed = fed_target()
    return {
        "id": "fed", "name": "Federal Reserve", "short": "Fed",
        "rate": f"{fed['lo']:.2f}–{fed['hi']:.2f}%", "rateLabel": "Fed funds target",
        "last": {"date": _rss_date(latest["pubDate"]).isoformat(), "text": sentence, "url": latest["link"]},
        "next": ({"date": nxt["date"], "start": nxt["start"], "label": "FOMC meeting" + (", with projections" if nxt["sep"] else ""), "url": FOMC_CALENDAR} if nxt else None),
        "source": {"name": "Federal Reserve Board", "url": FOMC_CALENDAR},
    }


def bank_ecb(today) -> dict:
    latest = next(i for i in _rss_items(http(ECB_FEED).decode("utf-8", "ignore")) if i["title"].strip() == "Monetary policy decisions")
    link = latest["link"].replace("europa.eu//", "europa.eu/")
    text = text_of(http(link).decode("utf-8", "ignore"))
    decision = re.search(r"The Governing Council today decided to [^.]*\.", text)
    rates = re.search(r"deposit facility, the main refinancing operations and the marginal lending facility[^.]*?(\d+\.\d+)%, (\d+\.\d+)% and (\d+\.\d+)%", text)
    if not decision or not rates:
        raise FeedError("ECB decision wording not recognised")
    nxt = _next_after(ecb_dates(), today)
    return {
        "id": "ecb", "name": "European Central Bank", "short": "ECB",
        "rate": f"{rates.group(1)}%", "rateLabel": "Deposit facility",
        "last": {"date": _rss_date(latest["pubDate"]).isoformat(), "text": f"{decision.group(0).replace('The Governing Council today decided', 'Governing Council decided')} DFR {rates.group(1)}%, MRO {rates.group(2)}%, MLF {rates.group(3)}%.", "url": link},
        "next": ({"date": nxt["date"], "start": nxt["start"], "label": "Monetary policy meeting", "url": ECB_CALENDAR} if nxt else None),
        "source": {"name": "European Central Bank", "url": ECB_CALENDAR},
    }


def bank_boj(today) -> dict:
    from pypdf import PdfReader

    year = today.year
    release = None
    for y in (year, year - 1):
        index = f"https://www.boj.or.jp/en/mopo/mpmdeci/mpr_{y}/index.htm"
        page = http(index).decode("utf-8", "ignore")
        for tr in re.findall(r"<tr.*?</tr>", page, flags=re.S):
            if "Statement on Monetary Policy" in text_of(tr):
                href = re.search(r'href="([^"]+\.pdf)"', tr)
                if href:
                    release = "https://www.boj.or.jp" + href.group(1)
                    break
        if release:
            break
    if not release:
        raise FeedError("BoJ statement not found")
    first = PdfReader(io.BytesIO(http(release))).pages[0].extract_text()
    first = re.sub(r"\s+", " ", first)
    level = re.search(r"remain at around ([\d.]+) percent", first)
    vote = re.search(r"\((?:an? )?([\d-]+ majority|unanimous) vote\)", first)
    stamp = re.search(r"([A-Z][a-z]+ \d{1,2}, ?20\s?\d\s?\d)", first)
    if not level or not stamp:
        raise FeedError("BoJ statement wording not recognised")
    day = dt.datetime.strptime(re.sub(r"(?<=\d)\s+(?=\d)", "", stamp.group(1)).replace(", ", ",").replace(",", ", "), "%B %d, %Y").date()
    voted = ""
    if vote:
        voted = " Unanimous vote." if vote.group(1) == "unanimous" else f" Vote {vote.group(1).replace(' majority', '').replace('-', chr(0x2013))}."
    nxt = _next_after([d for d in boj_dates() if d["date"] > day.isoformat()], today)
    return {
        "id": "boj", "name": "Bank of Japan", "short": "BoJ",
        "rate": f"{float(level.group(1)):.2f}%", "rateLabel": "Overnight call rate guide",
        "last": {"date": day.isoformat(), "text": f"Guided the uncollateralised overnight call rate to remain around {level.group(1)}%.{voted}", "url": release},
        "next": ({"date": nxt["date"], "label": "Monetary Policy Meeting, final day", "url": BOJ_SCHEDULE} if nxt else None),
        "source": {"name": "Bank of Japan", "url": BOJ_SCHEDULE},
    }


def bank_pboc(today) -> dict:
    data = json.loads(http(LPR_JSON))
    records = {r["termCode"]: r for r in data["records"]}
    stamp = data["data"]["showDateCN"].split()[0]
    one, five = records["1Y"]["shibor"], records["5Y"]["shibor"]
    day = dt.date.fromisoformat(stamp)
    following = dt.date(day.year + (day.month == 12), day.month % 12 + 1, 20)
    while following.weekday() >= 5:
        following += dt.timedelta(days=1)
    return {
        "id": "pboc", "name": "People's Bank of China", "short": "PBoC",
        "rate": f"{one}%", "rateLabel": "1Y Loan Prime Rate",
        "last": {"date": stamp, "text": f"LPR fixing: 1Y {one}%, 5Y {five}%.", "url": LPR_PAGE},
        "next": {"date": following.isoformat(), "label": "LPR fixing, 20th by rule. PBoC does not pre-announce policy meetings", "url": LPR_PAGE, "rule": True},
        "source": {"name": "China Foreign Exchange Trade System", "url": LPR_PAGE},
    }


def bank_mas(today) -> dict:
    listing = http(MAS_MPS_LIST).decode("utf-8", "ignore")
    links = re.findall(r'href="(/news/monetary-policy-statements/\d{4}/[^"]+)"', listing)
    if not links:
        raise FeedError("MAS statement list not recognised")
    url = "https://www.mas.gov.sg" + links[0]
    text = text_of(http(url).decode("utf-8", "ignore"))
    published = re.search(r"Published Date:\s*(\d{1,2}\s+[A-Z][a-z]+\s+\d{4})", text)
    decision = re.search(r"MAS will (?:therefore )?[^.]*policy band\s*\.[^.]*(?:width of the band|centred)[^.]*\.", text)
    if not published or not decision:
        raise FeedError("MPS wording not recognised")
    sentence = re.sub(r"\s+\.", ".", decision.group(0))
    day = dt.datetime.strptime(re.sub(r"\s+", " ", published.group(1)), "%d %B %Y").date()
    arc = mas_arc()
    return {
        "id": "mas", "name": "Monetary Authority of Singapore", "short": "MAS",
        "rate": "S$NEER band", "rateLabel": "Exchange rate policy",
        "last": {"date": day.isoformat(), "text": sentence, "url": url},
        "next": {"date": arc["date"], "nlt": arc["nlt"], "label": f"Monetary Policy Statement, {arc['edition']}", "url": MAS_ARC_URL},
        "source": {"name": "Monetary Authority of Singapore", "url": MAS_ARC_URL},
    }


BANKS = (("fed", "Federal Reserve", "Fed", bank_fed), ("ecb", "European Central Bank", "ECB", bank_ecb), ("boj", "Bank of Japan", "BoJ", bank_boj), ("pboc", "People's Bank of China", "PBoC", bank_pboc), ("mas", "Monetary Authority of Singapore", "MAS", bank_mas))


def banks(today, stamp) -> list[dict]:
    out = []
    for bank_id, name, short, fn in BANKS:
        try:
            entry = fn(today)
            entry.update(status="ok", asOf=stamp)
        except Exception as exc:  # noqa: BLE001
            entry = {"id": bank_id, "name": name, "short": short, "status": "empty", "reason": str(exc)}
        out.append(entry)
    return out


def calendar_events(today, bank_list) -> tuple[list[dict], list[dict]]:
    events: list[dict] = []
    gaps: list[dict] = []

    def attempt(label, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            gaps.append({"type": label, "reason": str(exc)})

    def add_fomc():
        for d in fomc_dates():
            if d["date"] >= today.isoformat():
                events.append({"type": "FOMC", "title": "FOMC meeting" + (" with projections" if d["sep"] else ""), "date": d["date"], "start": d["start"], "source": {"name": "Federal Reserve Board", "url": FOMC_CALENDAR}})

    def add_mps():
        arc = mas_arc()
        if arc["date"] >= today.isoformat():
            events.append({"type": "MAS MPS", "title": f"MAS Monetary Policy Statement, {arc['edition']}", "date": arc["date"], "nlt": arc["nlt"], "source": {"name": "MAS release calendar", "url": MAS_ARC_URL}})

    def add_singstat():
        copy = {}
        try:
            release_dates = singstat_arc()
        except Exception:  # noqa: BLE001 - SingStat blocks cloud runners, so use the committed copy
            snapshot = _snapshot("singstat_schedule.json")
            release_dates, copy = snapshot["events"], {"copy": {"name": "SingStat", "verified": snapshot["verified"]}}
        for e in release_dates:
            if e["date"] >= today.isoformat():
                events.append({**e, "source": {"name": "SingStat release calendar", "url": SINGSTAT_ARC_URL}, **copy})

    def add_pce():
        copy = {}
        try:
            releases = bea_pce()
        except Exception:  # noqa: BLE001 - fall back to the committed copy of BEA's schedule
            snapshot = _snapshot("bea_schedule.json")
            releases, copy = snapshot["events"], {"copy": {"name": "BEA", "verified": snapshot["verified"]}}
        for e in releases:
            if e["date"] < today.isoformat():
                continue
            day = dt.date.fromisoformat(e["date"])
            hour, minute = e["et"].split(":")
            release = dt.datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=dt.timezone(dt.timedelta(hours=_us_eastern_offset(day))))
            sgt = release.astimezone(SGT)
            events.append({
                "type": "US PCE",
                "title": f"PCE price index, in Personal Income and Outlays, {e['reference']}",
                "date": e["date"],
                "timeSgt": sgt.strftime("%H:%M"),
                "dateSgt": sgt.date().isoformat(),
                "source": {"name": "BEA release schedule", "url": BEA_SCHEDULE},
                **copy,
            })

    def add_ecb_meetings():
        for d in ecb_dates():
            if d["date"] >= today.isoformat():
                events.append({"type": "ECB", "title": "ECB monetary policy meeting", "date": d["date"], "start": d["start"], "source": {"name": "ECB Governing Council calendar", "url": ECB_CALENDAR}})

    def add_hicp():
        try:
            ecb_times = ecb_hicp()
        except Exception:  # noqa: BLE001 - the headline dates below still stand without the ECB times
            ecb_times = {}
        try:
            headline = eurostat_hicp(today)
        except Exception:  # noqa: BLE001 - the ECB series is published on the same days
            if not ecb_times:
                raise
            for day, e in ecb_times.items():
                if day >= today.isoformat():
                    label = "flash estimate, " if e["flash"] else ""
                    events.append({
                        "type": "EA HICP flash" if e["flash"] else "EA HICP final",
                        "title": f"Euro area seasonally adjusted HICP, {label}{e['reference']}",
                        "date": day, "timeSgt": e["timeSgt"], "dateSgt": e["dateSgt"],
                        "source": {"name": "ECB statistical calendar", "url": ECB_HICP_CALENDAR},
                    })
            return
        for e in headline:
            if e["date"] < today.isoformat():
                continue
            ecb = ecb_times.get(e["date"])
            events.append({
                "type": "EA HICP flash" if e["flash"] else "EA HICP final",
                "title": f"Euro area HICP{' flash estimate' if e['flash'] else ''}, {e['reference']}",
                "date": e["date"],
                "source": {"name": "Eurostat release calendar", "url": EUROSTAT_CALENDAR},
                **({"ecbTimeSgt": ecb["timeSgt"], "ecbDateSgt": ecb["dateSgt"], "ecbUrl": ECB_HICP_CALENDAR} if ecb else {}),
            })

    attempt("FOMC", add_fomc)
    attempt("MAS MPS", add_mps)
    attempt("US CPI and NFP", lambda: events.extend(bls_events(today)))
    attempt("SG CPI and GDP", add_singstat)
    attempt("US PCE", add_pce)
    attempt("ECB meetings", add_ecb_meetings)
    attempt("Euro area HICP", add_hicp)

    capped: list[dict] = []
    per_type: dict[str, int] = {}
    for event in sorted(events, key=lambda e: e["date"]):
        per_type[event["type"]] = per_type.get(event["type"], 0) + 1
        within = event["date"] <= (today + dt.timedelta(days=100)).isoformat()
        if per_type[event["type"]] == 1 or (within and per_type[event["type"]] <= 3):
            capped.append(event)
    return capped, gaps


# --- assembly --------------------------------------------------------------

def _keep_stale(row: dict, previous: dict | None, now: dt.datetime) -> dict:
    """Use the last good value for a failed feed, labelled, for at most STALE_DAYS."""
    if row["status"] != "empty" or not previous or previous.get("status") not in ("ok", "stale"):
        return row
    fetched = previous.get("fetchedAt")
    if not fetched:
        return row
    age = now - dt.datetime.fromisoformat(fetched.replace("Z", "+00:00"))
    if age > dt.timedelta(days=STALE_DAYS):
        return row
    return {**previous, "status": "stale", "reason": row.get("reason")}


def _panel_sources(rows: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for row in rows:
        if row.get("status") == "empty":
            continue
        name = row["source"]["name"]
        best = seen.get(name)
        if not best or str(row.get("asOf")) > str(best["asOf"]):
            seen[name] = {**row["source"], "asOf": row.get("asOf")}
    return list(seen.values())


def collect(previous: dict | None = None) -> dict:
    now = dt.datetime.now(UTC)
    today = now.astimezone(SGT).date()
    stamp = iso(now)
    previous = previous or {}
    prior_rows = {r["id"]: r for p in previous.get("panels", {}).values() for r in p.get("rows", [])}

    with ThreadPoolExecutor(max_workers=8) as pool:
        rate_future = pool.submit(rates_rows)
        market_futures = {panel: [pool.submit(market_row, spec) for spec in specs] for panel, specs in MARKET.items()}
        bank_future = pool.submit(banks, today, stamp)
        rate_rows, curve = rate_future.result()
        market = {panel: [f.result() for f in futures] for panel, futures in market_futures.items()}
        bank_list = bank_future.result()

    panels = {}
    for key, title, rows in (
        ("rates", "Rates", rate_rows),
        ("fx", "FX", market["fx"]),
        ("commodities", "Commodities", market["commodities"]),
        ("equities", "Equities and volatility", market["equities"]),
    ):
        kept = []
        for row in rows:
            if row["status"] == "ok":
                row["fetchedAt"] = stamp
            kept.append(_keep_stale(row, prior_rows.get(row["id"]), now))
        panels[key] = {"title": title, "rows": kept, "sources": _panel_sources(kept)}

    prior_banks = {b["id"]: b for b in previous.get("banks", [])}
    final_banks = []
    for bank in bank_list:
        if bank["status"] == "ok":
            bank["fetchedAt"] = stamp
        final_banks.append(_keep_stale(bank, prior_banks.get(bank["id"]), now))

    events, gaps = calendar_events(today, final_banks)
    return {
        "generated": stamp,
        "panels": panels,
        "banks": final_banks,
        "calendar": events,
        "calendarGaps": gaps,
        "curve": curve or previous.get("curve"),
    }


if __name__ == "__main__":
    if "--refresh-schedules" in sys.argv:
        for name, fetch in (("singstat_schedule.json", singstat_arc), ("bea_schedule.json", bea_pce)):
            target = ROOT / "data" / "curated" / name
            current = json.loads(target.read_text(encoding="utf-8"))
            current.update(events=fetch(), verified=dt.datetime.now(SGT).date().isoformat())
            target.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    else:
        json.dump(collect(), sys.stdout, indent=1)

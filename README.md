# Soundings

A live global macro dashboard and a research ledger, read from Singapore. It is a static site: GitHub Actions
fetches the feeds about every 15 minutes, so no API key or third-party request ever reaches the browser.

## Data

| Panel | Source | Notes |
| --- | --- | --- |
| US 2Y, 10Y, 2s10s | US Treasury daily par yield CSV | Daily, published after the US close |
| Fed funds target | Federal Reserve Board, open market operations page | Range in effect, carried forward daily |
| SGS 2Y, 10Y | MAS benchmark prices and yields | Falls back to SingStat M700071 month-end prints, labelled Monthly |
| FX, commodities, equities, VIX | Yahoo Finance chart endpoint | Unofficial and exchange-delayed. FRED's keyless CSV is the fallback where a series exists |
| Central banks | Fed, ECB, BoJ and MAS statements, ChinaMoney LPR | Parsed from the official documents on every run |
| Calendar | Fed, ECB, MAS, SingStat, BEA and Eurostat calendars, BLS schedule | BLS blocks scripted access, see below |

Rules enforced in `scripts/feeds.py`: a value only comes from a parsed response, and a failed feed keeps its last
good value for at most 7 days, marked stale, then becomes a labelled empty state. A market-implied Fed path is left
out because no free public source publishes one.

**Manual files.** `data/curated/bls_schedule.json` holds the US CPI and NFP dates, copied from the BLS schedule pages
and running to December 2027. Update it and its `verified` date if BLS revises the calendar.
`data/curated/singstat_schedule.json` and `bea_schedule.json` back up those two calendars for when they refuse cloud
runners. Refresh both from a normal connection with `python scripts/feeds.py --refresh-schedules`.

**Euro area HICP times.** Eurostat's calendar feed stamps every entry 11:00 and all-day, so that hour is not a real
release time and is not shown. The time on those rows is from the ECB calendar and applies to its seasonally adjusted
series.

## Research

Notes live in `data/research/entries.json`, with PDFs in `data/research/attachments/`. Edits on the research page
save to the browser's IndexedDB first, and the page shows how many are not yet in the repo.

To move them into the repo:

1. Press **Export** on the research page.
2. Unzip the file into `data/research/`, replacing what is there.
3. Commit and push. The next deploy publishes them.

The repo is public, so anything committed here, PDFs included, is public too. The page is marked `noindex`, which
keeps it out of search results but does not make it private. Keep sensitive notes local.

## Run locally

```bash
pip install -r requirements.txt
```

```bash
python scripts/build.py
```

```bash
python -m http.server 8123 --directory _site
```

**About page.** Edit `data/about.json`. LinkedIn and email appear once they are filled in.

**PDF viewer.** `site/assets/vendor/` holds PDF.js 4.10.38 (Apache 2.0), loaded only when a report opens. Covers are rendered at build time with PyMuPDF.

`scripts/make_assets.py` regenerates the favicon, icons and OG image. It is not part of the build and needs Pillow.

#!/usr/bin/env python3
"""
G3-A exogenous data STAGING download (STAGE_G3_0_GOVERNANCE.md §10 / §68.9).

SCOPE AND LIMITS — read before extending this file.
--------------------------------------------------
This is *audit staging only*. Per the governance:
  - "Report what must be procured. Stop if a required source is unavailable at
     any price; report substitutes WITHOUT adopting them." (§10 G3-A)
  - "No code beyond the data audit." (governance header)

Therefore this script ONLY downloads raw CSV to data/exogenous/ and records a
manifest with per-series PIT metadata. It fits nothing, reads no development
returns, adopts no series into any forecast, and does not touch xsmom.db or the
holdout. Every manifest row carries `"adopted": false`. Adoption is gated behind
delegate review at the spec stage (Order of work step 4).

Each series is one of the §68.9 M1 cross-asset inputs (ES, NQ, VIX, US 2Y/10Y,
DXY, gold) or a documented free substitute for one that is proprietary. The
substitute is STAGED, not ADOPTED — the distinction is the whole point of G3-A.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from dataclasses import dataclass, asdict, field
from datetime import date

import requests

OUT_DIR = "data/exogenous"

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}&coed={end}"
CBOE_VIX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

# Full available history is staged. The governance DEVELOPMENT window is
# 2020-01-01 -> 2024-12-31 and 2025-01 -> 2026-07 is SEALED; that seal is
# enforced at model-read time (PIT views), NOT by truncating the raw archive.
# The manifest records the boundaries so no fit ever crosses them.
COSD = "1990-01-01"
COED = date.today().isoformat()


@dataclass
class Series:
    key: str                 # local filename stem
    instrument: str          # which §68.9 M1 input this serves
    source: str
    url: str
    role: str                # "primary" | "substitute"
    granularity: str
    cost: str
    first_public: str        # first-public-timestamp / availability semantics
    pit_usable: str          # the exact observation usable at a decision time
    notes: str = ""
    # filled after fetch:
    adopted: bool = False     # ALWAYS false at this stage
    ok: bool = False
    rows: int = 0
    coverage_start: str = ""
    coverage_end: str = ""
    sha256: str = ""
    reason: str = ""


def _fred(id_: str) -> str:
    return FRED_CSV.format(id=id_, start=COSD, end=COED)


SERIES: list[Series] = [
    Series(
        key="fred_DGS2", instrument="US 2Y", source="FRED (Federal Reserve H.15)",
        url=_fred("DGS2"), role="primary", granularity="daily (business days)",
        cost="free, public domain",
        first_public="H.15 released each business day ~16:15 ET for the PRIOR "
                     "business day's trade date.",
        pit_usable="value labelled date D is first knowable ~D+1 16:15 ET "
                   "(~D+1 20:15 UTC). Use the most recent release known at t.",
        notes="Constant-maturity yield, percent. NaN on non-trading days.",
    ),
    Series(
        key="fred_DGS10", instrument="US 10Y", source="FRED (Federal Reserve H.15)",
        url=_fred("DGS10"), role="primary", granularity="daily (business days)",
        cost="free, public domain",
        first_public="H.15 released each business day ~16:15 ET for the PRIOR "
                     "business day's trade date.",
        pit_usable="value labelled date D first knowable ~D+1 16:15 ET.",
        notes="Constant-maturity yield, percent.",
    ),
    Series(
        key="fred_DTWEXBGS", instrument="DXY (SUBSTITUTE)",
        source="FRED (Federal Reserve H.10 broad dollar)",
        url=_fred("DTWEXBGS"), role="substitute", granularity="daily (business days)",
        cost="free, public domain",
        first_public="H.10 daily broad index, released next business day.",
        pit_usable="value date D knowable ~D+1. Use most recent release at t.",
        notes="NOT ICE U.S. Dollar Index (DXY). ICE DXY is proprietary/licensed. "
              "This is the Fed nominal broad trade-weighted USD index — a "
              "documented free substitute, STAGED NOT ADOPTED. Basket and "
              "weights differ from DXY (adds CNY, MXN, etc.).",
    ),
    Series(
        key="cboe_VIX", instrument="VIX", source="CBOE (official index history)",
        url=CBOE_VIX, role="primary", granularity="daily OHLC",
        cost="free (CBOE public CSV)",
        first_public="Official daily close published by CBOE at end of the US "
                     "cash session (~16:15 ET / ~20:15 UTC) for date D.",
        pit_usable="close for date D knowable ~D 20:15 UTC (same day). "
                   "Intraday VIX is disseminated live but only the close is here.",
        notes="Authoritative source, history to 1990. OHLC.",
    ),
    Series(
        key="fred_VIXCLS", instrument="VIX (cross-check)",
        source="FRED (CBOE via FRED)", url=_fred("VIXCLS"), role="substitute",
        granularity="daily close", cost="free, public domain",
        first_public="Mirrors CBOE close; FRED disseminates next morning.",
        pit_usable="close date D; if pulled live from FRED, arrives D+1 AM.",
        notes="Close-only cross-check against cboe_VIX. Prefer CBOE for OHLC.",
    ),
    Series(
        key="fred_SP500", instrument="ES (SUBSTITUTE: S&P 500 cash index)",
        source="FRED (S&P Dow Jones Indices)", url=_fred("SP500"),
        role="substitute", granularity="daily close",
        cost="free, but ROLLING ~10-year window only (licensing)",
        first_public="Index close for date D known at US cash close ~16:00 ET; "
                     "FRED series updates next morning.",
        pit_usable="cash close date D knowable ~D 20:00 UTC. NOTE: cash index "
                   "has NO overnight/Globex session, so it misses moves that "
                   "occur between US close and the crypto decision cut.",
        notes="NOT the ES future. CME ES futures (which trade ~23h and are the "
              "state variable actually named in §68.9) are proprietary. Cash "
              "index is a free substitute, STAGED NOT ADOPTED. Rolling 10y "
              "window means pre-~2016 history is unavailable from this feed.",
    ),
    Series(
        key="fred_NASDAQ100", instrument="NQ (SUBSTITUTE: Nasdaq-100 cash index)",
        source="FRED (Nasdaq)", url=_fred("NASDAQ100"), role="substitute",
        granularity="daily close", cost="free",
        first_public="Index close for date D known at US cash close; FRED "
                     "updates next morning.",
        pit_usable="cash close date D knowable ~D 20:00 UTC. Same overnight-gap "
                   "caveat as SP500.",
        notes="NOT the NQ future. CME NQ futures proprietary. Cash index is a "
              "free substitute, STAGED NOT ADOPTED. History reaches ~2015 here.",
    ),
]


def _session() -> requests.Session:
    s = requests.Session()
    # FRED stalls (read-timeout) on library/browser User-Agents but serves a
    # curl UA immediately; we fetch the same public CSVs curl does. Diagnosed
    # 2026-09-01: 'xsmom-g3a-audit/1.0' -> ReadTimeout, 'curl/8.5.0' -> 200 0.6s.
    s.headers.update({"User-Agent": "curl/8.5.0"})
    return s


def _fetch(session: requests.Session, url: str, retries: int = 3) -> bytes:
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def _coverage(raw: bytes) -> tuple[int, str, str]:
    """Rows with a parseable numeric value, and first/last such dates."""
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return 0, "", ""
    body = rows[1:]  # drop header
    dates = []
    for r in body:
        if len(r) < 2:
            continue
        val = r[-1].strip()
        if val in ("", "."):   # FRED marks missing with "."
            continue
        try:
            float(val)
        except ValueError:
            continue
        dates.append(r[0].strip())
    if not dates:
        return 0, "", ""
    return len(dates), dates[0], dates[-1]


def main() -> None:
    import os
    os.makedirs(OUT_DIR, exist_ok=True)
    session = _session()
    print(f"G3-A exogenous staging -> {OUT_DIR}/  (ADOPTED=false, audit only)\n")

    for s in SERIES:
        print(f"  {s.key:16s} [{s.role:10s}] {s.instrument}")
        try:
            raw = _fetch(session, s.url)
            s.rows, s.coverage_start, s.coverage_end = _coverage(raw)
            s.sha256 = hashlib.sha256(raw).hexdigest()
            path = os.path.join(OUT_DIR, s.key + ".csv")
            with open(path, "wb") as f:
                f.write(raw)
            s.ok = s.rows > 0
            if not s.ok:
                s.reason = "no numeric rows parsed (possible anti-bot HTML or "\
                           "discontinued series)"
            print(f"       {s.rows:>6} rows  {s.coverage_start} -> "
                  f"{s.coverage_end}  {'OK' if s.ok else 'EMPTY: '+s.reason}")
        except Exception as e:  # noqa: BLE001 -- audit tool, record and continue
            s.ok = False
            s.reason = repr(e)
            print(f"       FAILED: {s.reason}")

    manifest = {
        "generated": COED,
        "governance": "STAGE_G3_0_GOVERNANCE.md §10 (G3-A) / §68.9",
        "status": "AUDIT STAGING ONLY — nothing here is ADOPTED into any "
                  "forecast. Adoption is gated behind delegate review at the "
                  "spec stage (Order of work step 4).",
        "development_window": "2020-01-01 .. 2024-12-31 (sequential-in-time)",
        "sealed_window": "2025-01 .. 2026-07 (SEALED; enforced at model read, "
                         "not by truncating these raw files)",
        "holdout": "untouched",
        "series": [asdict(s) for s in SERIES],
    }
    mpath = os.path.join(OUT_DIR, "MANIFEST.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=2)
    ok = sum(1 for s in SERIES if s.ok)
    print(f"\n  wrote {mpath}")
    print(f"  {ok}/{len(SERIES)} series staged with data; "
          f"{len(SERIES)-ok} empty/failed (see manifest reasons).")


if __name__ == "__main__":
    main()

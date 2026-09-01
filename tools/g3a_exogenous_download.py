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
from datetime import datetime, timezone

import requests

OUT_DIR = "data/exogenous"

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}&coed={end}"
CBOE_VIX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

# Full available history is staged. The governance DEVELOPMENT window is
# 2020-01-01 -> 2024-12-31 and 2025-01 -> 2026-07 is SEALED; that seal is
# enforced at model-read time (PIT views), NOT by truncating the raw archive.
# The manifest records the boundaries so no fit ever crosses them.
COSD = "1990-01-01"
# timezone-aware (NOTES 68.11.2); never date.today()
COED = datetime.now(timezone.utc).date().isoformat()


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
    licence_class: str = "redistribution_restricted"  # conservative default
    # filled after fetch:
    retrieved_at_utc: str = ""
    retrieval_time_quality: str = ""
    http_last_modified: str | None = None
    http_etag: str | None = None
    http_status: int | None = None
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
        first_public="H.15 released each business day, at the manifest's "
                     "release_local_time in release_timezone, for the PRIOR "
                     "business day.",
        pit_usable="loader-governed (tools/g3_exogenous_loader.py); use the "
                   "most recent release with source_available_time <= t.",
        notes="Constant-maturity yield, percent. NaN on non-trading days.",
        licence_class="public_domain",
    ),
    Series(
        key="fred_DGS10", instrument="US 10Y", source="FRED (Federal Reserve H.15)",
        url=_fred("DGS10"), role="primary", granularity="daily (business days)",
        cost="free, public domain",
        first_public="Same H.15 release rule as fred_DGS2.",
        pit_usable="loader-governed; same rule as fred_DGS2.",
        notes="Constant-maturity yield, percent.",
        licence_class="public_domain",
    ),
    Series(
        key="fred_DTWEXBGS", instrument="DXY (SUBSTITUTE)",
        source="FRED (Federal Reserve H.10 broad dollar)",
        url=_fred("DTWEXBGS"), role="substitute", granularity="daily (business days)",
        cost="free, public domain",
        first_public="H.10 daily broad index, released next business day.",
        pit_usable="loader-governed; next-business-day availability.",
        licence_class="public_domain",
        notes="NOT ICE U.S. Dollar Index (DXY). ICE DXY is proprietary/licensed. "
              "This is the Fed nominal broad trade-weighted USD index — a "
              "documented free substitute, STAGED NOT ADOPTED. Basket and "
              "weights differ from DXY (adds CNY, MXN, etc.).",
    ),
    Series(
        key="cboe_VIX", instrument="VIX", source="CBOE (official index history)",
        url=CBOE_VIX, role="primary", granularity="daily OHLC",
        cost="free (CBOE public CSV)",
        first_public="Official daily close published by CBOE after the US cash "
                     "session close, same day.",
        pit_usable="loader-governed; same-day availability at the manifest's "
                   "release_local_time in release_timezone.",
        notes="Authoritative source, history to 1990. OHLC.",
    ),
    Series(
        key="fred_VIXCLS", instrument="VIX (cross-check)",
        source="FRED (CBOE via FRED)", url=_fred("VIXCLS"), role="substitute",
        granularity="daily close", cost="free, public domain",
        first_public="Underlying public same day (CBOE); this FRED mirror serves "
                     "it the next business day.",
        pit_usable="loader-governed; source availability set conservatively "
                   "to end of the next business day (NOTES 68.11.1).",
        notes="Close-only cross-check against cboe_VIX. Prefer CBOE for OHLC.",
    ),
    Series(
        key="fred_SP500", instrument="ES (SUBSTITUTE: S&P 500 cash index)",
        source="FRED (S&P Dow Jones Indices)", url=_fred("SP500"),
        role="substitute", granularity="daily close",
        cost="free, but ROLLING ~10-year window only (licensing)",
        first_public="Underlying close public at the NYSE cash close; this FRED "
                     "source serves it the next business day.",
        pit_usable="loader-governed; source_available_time (FRED) governs "
                   "access. Cash index has NO overnight/Globex session "
                   "(Panel-B caveat, NOTES 68.11.2).",
        notes="NOT the ES future. CME ES futures (which trade ~23h and are the "
              "state variable actually named in §68.9) are proprietary. Cash "
              "index is a free substitute, STAGED NOT ADOPTED. Rolling 10y "
              "window means pre-~2016 history is unavailable from this feed.",
    ),
    Series(
        key="fred_NASDAQ100", instrument="NQ (SUBSTITUTE: Nasdaq-100 cash index)",
        source="FRED (Nasdaq)", url=_fred("NASDAQ100"), role="substitute",
        granularity="daily close", cost="free",
        first_public="Underlying close public at the NYSE cash close; this FRED "
                     "source serves it the next business day.",
        pit_usable="loader-governed; same rule and caveats as fred_SP500.",
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


def _fetch(session: requests.Session, url: str, retries: int = 3):
    """Returns (content, headers, status) — provenance capture, §68.11.2."""
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            return r.content, r.headers, r.status_code
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
            raw, headers, status = _fetch(session, s.url)
            s.retrieved_at_utc = datetime.now(timezone.utc).isoformat()
            s.retrieval_time_quality = "observed"   # 68.12.2: true value
            s.http_last_modified = headers.get("Last-Modified")
            s.http_etag = headers.get("ETag")
            s.http_status = status
            s.rows, s.coverage_start, s.coverage_end = _coverage(raw)
            s.sha256 = hashlib.sha256(raw).hexdigest()
            # §68.11.3 raw-data policy: restricted raw files are NEVER
            # committed — they live in the gitignored raw/ subdirectory.
            sub = "" if s.licence_class == "public_domain" else "raw"
            os.makedirs(os.path.join(OUT_DIR, sub) or OUT_DIR, exist_ok=True)
            path = os.path.join(OUT_DIR, sub, s.key + ".csv")
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

    # §68.11: the manifest's release semantics and policy fields are
    # authoritative and hand-audited — this tool MERGES retrieval provenance
    # into the existing manifest and never regenerates it wholesale.
    mpath = os.path.join(OUT_DIR, "MANIFEST.json")
    with open(mpath, encoding="utf-8") as f:
        manifest = json.load(f)
    by_key = {e["key"]: e for e in manifest["series"]}
    retrieval_fields = ("retrieved_at_utc", "retrieval_time_quality",
                        "http_last_modified", "http_etag",
                        "http_status", "rows", "coverage_start",
                        "coverage_end", "sha256", "ok", "reason")
    for s in SERIES:
        entry = by_key.get(s.key)
        d = asdict(s)
        if entry is None:
            manifest["series"].append(d)
            continue
        for k in retrieval_fields:
            entry[k] = d[k]
        # 68.12.2: observed values supersede the upper bound for the copy
        # actually on disk; nothing historical is back-filled.
        entry.pop("retrieved_at_provenance", None)
        entry.pop("retrieved_at_upper_bound_utc", None)
    manifest["manifest_updated_utc"] = datetime.now(timezone.utc).isoformat()
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    ok = sum(1 for s in SERIES if s.ok)
    print(f"\n  wrote {mpath}")
    print(f"  {ok}/{len(SERIES)} series staged with data; "
          f"{len(SERIES)-ok} empty/failed (see manifest reasons).")


if __name__ == "__main__":
    main()

"""
Binance USDS-M futures historical ingest, from the public dumps.

WHY THE DUMPS AND NOT THE REST API
----------------------------------
fapi REST is rate-limited and paginated; backfilling ~7 years x N symbols
through it takes hours and trips weight limits. The public dumps are static
zips: one HTTP GET per symbol-month, no auth, no rate limit.

SURVIVORSHIP
------------
`list_all_symbols()` enumerates the S3 bucket rather than calling
exchangeInfo. exchangeInfo returns only symbols listed *today*, so building a
universe from it silently drops every delisted contract and biases the
backtest toward survivors. The bucket retains directories for delisted
symbols, which is exactly what point-in-time membership needs.

DATA START
----------
Binance USDS-M futures launched 2019-09. Funding history begins then, and
funding is a mandatory cost input, so DEFAULT_START is set there. Spot data
reaches back to 2017 but trades a different instrument with different costs.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta

import requests

BASE = "https://data.binance.vision"
BUCKET_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FAPI = "https://fapi.binance.com"

DEFAULT_START = date(2019, 9, 1)

# Binance kline CSV column order (futures monthly dumps).
# Newer files carry a header row; older ones do not. sniff_header handles both.
_KLINE_COLS = 12


@dataclass
class FetchResult:
    symbol: str
    period: str
    rows: int
    ok: bool
    reason: str = ""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "xsmom-research/1.0"})
    return s


def list_all_symbols(session: requests.Session | None = None) -> list[str]:
    """
    Every USDS-M symbol that has ever had a monthly kline dump, including
    delisted ones. This is the survivorship-safe symbol list.
    """
    session = session or _session()
    prefix = "data/futures/um/monthly/klines/"
    symbols: list[str] = []
    token = None
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"

    while True:
        params = {"delimiter": "/", "prefix": prefix, "list-type": "2"}
        if token:
            params["continuation-token"] = token
        r = session.get(BUCKET_LIST, params=params, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.content)

        for cp in root.findall(f"{ns}CommonPrefixes"):
            p = cp.find(f"{ns}Prefix")
            if p is not None and p.text:
                symbols.append(p.text[len(prefix):].strip("/"))

        truncated = root.find(f"{ns}IsTruncated")
        if truncated is not None and truncated.text == "true":
            nxt = root.find(f"{ns}NextContinuationToken")
            token = nxt.text if nxt is not None else None
            if not token:
                break
        else:
            break

    return sorted(s for s in symbols if s)


def month_range(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _sniff_and_read(raw: bytes, expected_cols: int) -> list[list[str]]:
    """Parse dump CSV, skipping a header row if present."""
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if len(r) >= expected_cols]
    if not rows:
        return []
    try:
        float(rows[0][0])
    except ValueError:
        rows = rows[1:]  # header
    return rows


def _download_zip(
    session: requests.Session, url: str, retries: int = 3
) -> bytes | None:
    """Return inner file bytes, or None on 404 (symbol not listed that month)."""
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                return z.read(z.namelist()[0])
        except (requests.RequestException, zipfile.BadZipFile) as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return None


def fetch_klines_month(
    session: requests.Session, symbol: str, interval: str, period: str
) -> list[tuple]:
    """Returns rows shaped for PointInTimeStore.insert_klines."""
    url = f"{BASE}/data/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{period}.zip"
    raw = _download_zip(session, url)
    if raw is None:
        return []
    out = []
    for r in _sniff_and_read(raw, _KLINE_COLS):
        # open_time, o, h, l, c, volume, close_time, quote_volume, count, ...
        out.append(
            (
                int(r[0]),   # open_time
                int(r[6]),   # close_time  <- the knowable-at stamp
                float(r[1]),
                float(r[2]),
                float(r[3]),
                float(r[4]),
                float(r[5]),
                float(r[7]),
                int(float(r[8])),
            )
        )
    return out


def fetch_funding_month(
    session: requests.Session, symbol: str, period: str
) -> list[tuple]:
    """Returns (funding_time_ms, rate) rows."""
    url = f"{BASE}/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{period}.zip"
    raw = _download_zip(session, url)
    if raw is None:
        return []
    out = []
    for r in _sniff_and_read(raw, 3):
        # calc_time, funding_interval_hours, last_funding_rate
        out.append((int(r[0]), float(r[2])))
    return out


def fetch_exchange_filters(session: requests.Session | None = None) -> list[dict]:
    """
    Current per-symbol filters from exchangeInfo.

    MIN_NOTIONAL is per-symbol, not a uniform $5 floor. This is the check that
    decides whether a symbol is tradeable at small capital, and it must be read
    live because Binance revises these without notice.
    """
    session = session or _session()
    r = session.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=30)
    r.raise_for_status()
    out = []
    for s in r.json().get("symbols", []):
        rec = {
            "symbol": s["symbol"],
            "status": s.get("status", "UNKNOWN"),
            "min_notional": None,
            "step_size": None,
            "tick_size": None,
        }
        for f in s.get("filters", []):
            ft = f.get("filterType")
            if ft == "MIN_NOTIONAL":
                rec["min_notional"] = float(f["notional"])
            elif ft == "LOT_SIZE":
                rec["step_size"] = float(f["stepSize"])
            elif ft == "PRICE_FILTER":
                rec["tick_size"] = float(f["tickSize"])
        out.append(rec)
    return out


def backfill(
    store,
    symbols: list[str],
    start: date = DEFAULT_START,
    end: date | None = None,
    interval: str = "1d",
    include_funding: bool = True,
    verbose: bool = True,
) -> list[FetchResult]:
    """
    Idempotent backfill. Re-running skips symbol-months already in ingest_log,
    so an interrupted run resumes rather than restarting.
    """
    session = _session()
    end = end or (date.today() - timedelta(days=1))
    periods = month_range(start, end)
    results: list[FetchResult] = []

    done = {
        (r[0], r[1], r[2])
        for r in store._conn.execute(
            "SELECT source, symbol, period FROM ingest_log"
        ).fetchall()
    }

    for i, sym in enumerate(symbols, 1):
        if verbose:
            print(f"[{i}/{len(symbols)}] {sym}", flush=True)
        for period in periods:
            if ("klines", sym, period) not in done:
                try:
                    rows = fetch_klines_month(session, sym, interval, period)
                    if rows:
                        n = store.insert_klines(sym, interval, rows)
                        store._conn.execute(
                            "INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?)",
                            ("klines", sym, period, n, int(time.time() * 1000)),
                        )
                        store._conn.commit()
                        results.append(FetchResult(sym, period, n, True))
                except Exception as e:
                    results.append(FetchResult(sym, period, 0, False, str(e)))

            if include_funding and ("funding", sym, period) not in done:
                try:
                    rows = fetch_funding_month(session, sym, period)
                    if rows:
                        n = store.insert_funding(sym, rows)
                        store._conn.execute(
                            "INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?)",
                            ("funding", sym, period, n, int(time.time() * 1000)),
                        )
                        store._conn.commit()
                except Exception as e:
                    results.append(FetchResult(sym, period, 0, False, str(e)))

    return results

"""
Stage G3-B cross-asset structure measurement (NOTES 69.1) — DESCRIPTIVE ONLY.

Everything statistical here was frozen in NOTES 69.1 BEFORE any series was
read. The map is quarantined from feature selection (69.1.1): M1 uses, for
each adopted exogenous series, the single most recent PIT-available
observation at the decision time — one lag, no lag search — so no result in
this module can select a lag, window, or transform. No forecast is fitted, no
model compared, no trial consumed, nothing is adopted, and no skill is
claimed; Q1-Q4 remain the only skill criteria.

Data discipline: crypto legs come from the PIT store's 1d UTC bars via a
seal-checked, hard-capped, read-only load (the 63.2.1 pattern); exogenous
legs go through the release rules of tools/g3_exogenous_loader under the
69.0 invariants, clamped to the development window 2020-01-01 .. 2024-12-31.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rcm.seal import assert_range_allowed                     # noqa: E402
from tools.g3_exogenous_loader import RULES, load_series      # noqa: E402

DEV_START = date(2020, 1, 1)
DEV_END = date(2024, 12, 31)
DAY_MS = 86_400_000
DEV_START_MS = 1_577_836_800_000          # 2020-01-01T00:00:00Z bar open
DEV_END_MS = 1_735_603_200_000            # 2024-12-31T00:00:00Z bar open
WINDOW = 90                               # 63.6 precedent, the only window
KS = tuple(range(-5, 6))                  # 69.1.3, calendar days
PCTS = (5, 25, 50, 75, 95)                # 63.2.4.2 — the only percentiles
TARGET = "BTCUSDT"
COMPARATOR = "ETHUSDT"                    # crypto-internal comparator (69.1.2)
EXOG_KEYS = ("fred_DGS2", "fred_DGS10", "fred_DTWEXBGS", "cboe_VIX",
             "fred_VIXCLS", "fred_SP500", "fred_NASDAQ100")
DIFF_KEYS = frozenset({"fred_DGS2", "fred_DGS10"})   # yield levels: diff, not log
DB_PATH = Path(__file__).resolve().parents[1] / "xsmom.db"
OUT_DIR = Path(__file__).resolve().parent / "g3b" / "out"
SENSITIVITY_LABEL = "SENSITIVITY — NOT THE PIT-VALID SERIES"

DATES = [DEV_START + timedelta(days=i)
         for i in range((DEV_END - DEV_START).days + 1)]
N_DATES = len(DATES)


class RangeRefused(RuntimeError):
    """A G3-B request past 2024-12-31. The development measurement never
    touches a later date (69.1.2); the seal begins one instant after."""


def _refuse_if_late(t: date) -> None:
    if t > DEV_END:
        raise RangeRefused(
            f"G3-B request for {t.isoformat()} > 2024-12-31 — the frozen "
            f"development window (NOTES 69.1.2); refused before any read")


def tau_ms(t: date) -> int:
    """Snapshot instant for UTC date t: 00:00:00Z of the following day."""
    nxt = t + timedelta(days=1)
    return int(datetime(nxt.year, nxt.month, nxt.day,
                        tzinfo=timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------ crypto legs

def load_crypto_log_returns(db_path: Path = DB_PATH) -> dict[str, np.ndarray]:
    """r_sym over the DATES axis (NaN where undefined). Seal-checked and
    hard-capped BEFORE any row is read; read-only connection."""
    if DEV_END_MS >= 1_735_689_600_000:
        raise RangeRefused("hard cap breached before read")   # unreachable
    assert_range_allowed(DEV_START_MS, DEV_END_MS)
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT symbol, open_time, close FROM klines WHERE interval='1d' "
            "AND symbol IN (?, ?) AND open_time >= ? AND open_time <= ?",
            (TARGET, COMPARATOR, DEV_START_MS, DEV_END_MS)).fetchall()
    finally:
        con.close()
    closes: dict[str, dict[int, float]] = {}
    for sym, ot, c in rows:
        closes.setdefault(sym, {})[int(ot)] = float(c)
    out = {}
    for sym in (TARGET, COMPARATOR):
        r = np.full(N_DATES, np.nan)
        cs = closes.get(sym, {})
        for i, d in enumerate(DATES):
            ot = DEV_START_MS + i * DAY_MS
            if ot in cs and (ot - DAY_MS) in cs and cs[ot - DAY_MS] > 0:
                r[i] = np.log(cs[ot] / cs[ot - DAY_MS])
        out[sym] = r
    return out


# --------------------------------------------------------- exogenous legs

def exogenous_returns(key: str, timing: str
                      ) -> tuple[np.ndarray, np.ndarray]:
    """(r_X, stale) over the DATES axis under the 69.1.3 stale-carry.

    timing: "source" (the PIT-valid availability rule) or "underlying"
    (publisher timing — the 69.1.4 sensitivity, never the valid series).
    """
    rule = RULES[key][timing]
    obs = [(d, v) for d, v in load_series(key) if DEV_START <= d <= DEV_END]
    avail = sorted(
        (int(rule.availability_utc(d).timestamp() * 1000), d, v)
        for d, v in obs)
    level = np.full(N_DATES, np.nan)
    stale = np.full(N_DATES, np.nan)
    j = 0
    last = np.nan
    for i, t in enumerate(DATES):
        cut = tau_ms(t)
        fresh = 0
        while j < len(avail) and avail[j][0] <= cut:
            last = avail[j][2]
            fresh = 1
            j += 1
        if not np.isnan(last):
            level[i] = last
            stale[i] = 0 if fresh else 1
    r = np.full(N_DATES, np.nan)
    ok = ~np.isnan(level)
    for i in range(1, N_DATES):
        if ok[i] and ok[i - 1]:
            r[i] = (level[i] - level[i - 1] if key in DIFF_KEYS
                    else np.log(level[i] / level[i - 1]))
    return r, stale


# ------------------------------------------------- windowed statistics

def _windows(a: np.ndarray) -> np.ndarray:
    """(N_DATES-WINDOW+1, WINDOW) sliding windows; row w ends at date
    index w+WINDOW-1."""
    return np.lib.stride_tricks.sliding_window_view(a, WINDOW)


def avg_ranks_rows(m: np.ndarray) -> np.ndarray:
    """Average (tie-mean) ranks per row — Spearman's rank transform,
    implemented directly (69.1.3), no external statistics dependency."""
    out = np.empty_like(m, dtype=float)
    for i in range(m.shape[0]):
        a = m[i]
        order = np.argsort(a, kind="mergesort")
        sa = a[order]
        ranks = np.empty(len(a))
        j = 0
        while j < len(a):
            k = j
            while k + 1 < len(a) and sa[k + 1] == sa[j]:
                k += 1
            ranks[order[j:k + 1]] = (j + k) / 2.0 + 1.0
            j = k + 1
        out[i] = ranks
    return out


def rowwise_corr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pearson per row pair; NaN rows or zero-variance rows -> NaN."""
    bad = np.isnan(a).any(axis=1) | np.isnan(b).any(axis=1)
    a0 = np.nan_to_num(a)
    b0 = np.nan_to_num(b)
    da = a0 - a0.mean(axis=1, keepdims=True)
    db = b0 - b0.mean(axis=1, keepdims=True)
    sa = np.sqrt((da * da).sum(axis=1))
    sb = np.sqrt((db * db).sum(axis=1))
    with np.errstate(invalid="ignore", divide="ignore"):
        c = (da * db).sum(axis=1) / (sa * sb)
    c[bad | (sa == 0) | (sb == 0)] = np.nan
    return c


def measure(r_btc: np.ndarray, r_x: np.ndarray, stale: np.ndarray) -> dict:
    """All 69.1.3 statistics for one X. Returns dict of aligned arrays
    indexed by window-end date index t (t = row + WINDOW - 1)."""
    wx = _windows(r_x)
    wb = _windows(r_btc)
    rx_ranks = avg_ranks_rows(np.nan_to_num(wx, nan=np.inf))
    rb_ranks = avg_ranks_rows(np.nan_to_num(wb, nan=np.inf))
    n_rows = wx.shape[0]
    rho_p, rho_s = {}, {}
    for k in KS:
        # X window ends at t (row w = t-89); BTC window ends at t+k
        # (row w+k). Valid rows: both in range.
        lo, hi = max(0, -k), min(n_rows, n_rows - k)
        p = np.full(n_rows, np.nan)
        s = np.full(n_rows, np.nan)
        if hi > lo:
            p[lo:hi] = rowwise_corr(wx[lo:hi], wb[lo + k:hi + k])
            valid = (~np.isnan(wx[lo:hi]).any(axis=1)
                     & ~np.isnan(wb[lo + k:hi + k]).any(axis=1))
            sr = np.full(hi - lo, np.nan)
            sr[valid] = rowwise_corr(rx_ranks[lo:hi][valid],
                                     rb_ranks[lo + k:hi + k][valid])
            s[lo:hi] = sr
        rho_p[k], rho_s[k] = p, s
    # k=0 OLS: slope of r_BTC on r_X, intercept included, classical SE
    bad = np.isnan(wx).any(axis=1) | np.isnan(wb).any(axis=1)
    x0 = np.nan_to_num(wx)
    b0 = np.nan_to_num(wb)
    dx = x0 - x0.mean(axis=1, keepdims=True)
    db = b0 - b0.mean(axis=1, keepdims=True)
    sxx = (dx * dx).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        beta = (dx * db).sum(axis=1) / sxx
        rss = (db * db).sum(axis=1) - beta * (dx * db).sum(axis=1)
        rss = np.maximum(rss, 0.0)
        se = np.sqrt(rss / (WINDOW - 2) / sxx)
    beta[bad | (sxx == 0)] = np.nan
    se[bad | (sxx == 0)] = np.nan
    stale_w = _windows(np.nan_to_num(stale, nan=1.0)).sum(axis=1)
    stale_w[np.isnan(_windows(stale)).any(axis=1)
            | np.isnan(wx).any(axis=1)] = -1   # marker: window not formed
    return {"rho_p": rho_p, "rho_s": rho_s, "beta": beta, "se": se,
            "stale_w": stale_w, "stale": stale}


# --------------------------------------------------------------- output

def _f(v) -> float | None:
    return None if v is None or (isinstance(v, float) and np.isnan(v)) \
        else round(float(v), 8)


def header(sensitivity: bool) -> dict:
    h = {
        "header": True,
        "protocol": "NOTES 69.1 (frozen before any read, commit 789e987)",
        "descriptive_only": True,
        "quarantined_from_feature_selection": True,
        "adoption": "adopted:false stands; measuring with a series adopts "
                    "nothing",
        "trial_consumed": False,
        "skill_claim": "none - Q1-Q4 remain the only skill criteria",
        "as_of_rule": "rho[k] at window-end t is knowable at t+max(k,0); "
                      "tau_t = 00:00:00Z of t+1; k>0 shifts the BTC leg "
                      "later in time",
        "window": WINDOW, "ks": list(KS), "target": TARGET,
        "dev_window": [DEV_START.isoformat(), DEV_END.isoformat()],
    }
    if sensitivity:
        h["label"] = SENSITIVITY_LABEL
        h["timing"] = "underlying_public_time (publisher release)"
    else:
        h["timing"] = "source_available_time (the PIT-valid rule)"
    return h


def run(timing: str, out_path: Path) -> dict:
    """One full map under the given availability timing. Returns the
    distributional summaries (also written as summary records)."""
    sensitivity = timing == "underlying"
    crypto = load_crypto_log_returns()
    r_btc = crypto[TARGET]
    xs: dict[str, tuple[np.ndarray, np.ndarray]] = {
        COMPARATOR: (crypto[COMPARATOR], np.zeros(N_DATES))}
    for key in EXOG_KEYS:
        xs[key] = exogenous_returns(key, timing)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, dict] = {}
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(header(sensitivity)) + "\n")
        for key, (r_x, stale) in xs.items():
            m = measure(r_btc, r_x, stale)
            for w in range(N_DATES - WINDOW + 1):
                t = w + WINDOW - 1
                rp = {str(k): _f(m["rho_p"][k][w]) for k in KS
                      if not np.isnan(m["rho_p"][k][w])}
                rs = {str(k): _f(m["rho_s"][k][w]) for k in KS
                      if not np.isnan(m["rho_s"][k][w])}
                if not rp and np.isnan(m["beta"][w]):
                    continue
                rec = {"x": key, "t": DATES[t].isoformat(),
                       "rho_pearson": rp, "rho_spearman": rs,
                       "beta": _f(m["beta"][w]), "se_beta": _f(m["se"][w]),
                       "n": WINDOW,
                       "stale": int(m["stale"][t])
                       if not np.isnan(m["stale"][t]) else None,
                       "stale_days_in_window": int(m["stale_w"][w])
                       if m["stale_w"][w] >= 0 else None}
                if sensitivity:
                    rec["label"] = SENSITIVITY_LABEL
                fh.write(json.dumps(rec) + "\n")
            sm: dict = {}
            for k in KS:
                for nm, arr in (("pearson", m["rho_p"][k]),
                                ("spearman", m["rho_s"][k])):
                    v = arr[~np.isnan(arr)]
                    if len(v):
                        sm[f"rho_{nm}_k{k:+d}"] = {
                            f"p{q}": _f(np.percentile(v, q)) for q in PCTS}
            for nm, arr in (("beta", m["beta"]), ("se_beta", m["se"])):
                v = arr[~np.isnan(arr)]
                if len(v):
                    sm[nm] = {f"p{q}": _f(np.percentile(v, q)) for q in PCTS}
            sw = m["stale_w"][m["stale_w"] >= 0]
            if len(sw):
                sm["stale_days_in_window"] = {
                    f"p{q}": _f(np.percentile(sw, q)) for q in PCTS}
            sm["n_windows_k0"] = int((~np.isnan(m["rho_p"][0])).sum())
            summaries[key] = sm
            srec = {"summary": True, "x": key, "pcts": list(PCTS), **sm}
            if sensitivity:
                srec["label"] = SENSITIVITY_LABEL
            fh.write(json.dumps(srec) + "\n")
    return summaries


def exercise_refusals() -> list[str]:
    """69.1.6 step 2 — both refusals, BEFORE the first measurement read."""
    msgs = []
    try:
        assert_range_allowed(DEV_START_MS, DEV_END_MS + DAY_MS)
        raise AssertionError("seal FAILED to refuse a 2025 read")
    except Exception as e:
        if type(e).__name__ != "SealViolation":
            raise
        msgs.append(f"SealViolation: {e}")
    try:
        _refuse_if_late(date(2025, 1, 1))
        raise AssertionError("G3-B cap FAILED to refuse 2025-01-01")
    except RangeRefused as e:
        msgs.append(f"RangeRefused: {e}")
    return msgs


def main() -> None:
    print("G3-B structure measurement (NOTES 69.1) — descriptive only, "
          "nothing adopted, no trial\n")
    for m in exercise_refusals():
        print("REFUSAL EXERCISED:", m)
    print()
    diag = OUT_DIR / "diagnostics.jsonl"
    sens = OUT_DIR / "sensitivity.jsonl"
    s1 = run("source", diag)
    s2 = run("underlying", sens)
    for path in (diag, sens):
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"sha256 {path.name}: {h}")
    print()
    for label, ss in (("PIT-VALID", s1),
                      (SENSITIVITY_LABEL, s2)):
        print(f"== {label} ==")
        for key, sm in ss.items():
            print(f"-- {key}  (k=0 windows: {sm.get('n_windows_k0')})")
            for stat in sorted(sm):
                if stat in ("n_windows_k0",):
                    continue
                q = sm[stat]
                if isinstance(q, dict):
                    line = " ".join(f"p{p}={q[f'p{p}']}" for p in PCTS)
                    print(f"   {stat:22s} {line}")
        print()


if __name__ == "__main__":
    main()

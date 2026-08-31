"""
Stage 21 Part D (NOTES §63.2): development-era residual-correlation
STRUCTURE measurement, 2020-01-01 → 2024-12-31.

THE PROTOCOL IS FROZEN IN §63.2, COMMITTED BEFORE THIS FILE EXISTED.
Nothing here decides anything: the universe rules, the window, the
statistic list, the tail definition, and the output location all come from
the ledger. This module computes NO performance quantity — no portfolio,
no PnL, no Sharpe, no formation, no comparison between specifications.

Quarantine (§63.2.1, AST-tested): imports are limited to `rcm.factors`
(the frozen §60.1 estimators, reused verbatim), `rcm.seal`,
`backtest.universe_filter`, numpy and the standard library. The optimizer,
gates, state machine, attribution, momentum, and every backtest/live
execution module are BANNED here.

D.4 (§63.2.6): the measured structure may not be fed into any strategy
component before the delegates freeze the robustness fixture and criteria
in a §63 append. D.6: after the measurement, STOP.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from backtest.universe_filter import classify
from rcm.factors import estimate_betas, orthogonalize_eth, residual_series
from rcm.seal import assert_range_allowed

DAY_MS = 86_400_000
# §63.2.1: the development window, hard-capped. The end constant is the
# module's own refusal line — the seal (2025-01 → 2026-07) is checked as
# well, but this cap refuses BEFORE the seal is even consulted.
START_DAY_MS = 1_577_836_800_000          # 2020-01-01 UTC
END_DAY_MS = 1_735_603_200_000            # 2024-12-31 UTC
MIN_HISTORY_DAYS = 180                    # §59.3.2, frozen
WINDOW = 90                               # §60.1, the single system window
FACTOR_SYMBOLS = ("BTCUSDT", "ETHUSDT")   # excluded by math (§63.2.2.4)
PCTS = (5, 25, 50, 75, 95)                # §63.2.4.2 — the only percentiles
EIG_KS = (1, 2, 3, 5)
OUT_PATH = Path(__file__).resolve().parent / "residcorr_out" / "diagnostics.jsonl"
DB_PATH = Path(__file__).resolve().parents[1] / "xsmom.db"


class RangeRefused(RuntimeError):
    """A requested read past 2024-12-31 (or into the seal). D.5."""


def load_daily_closes(db_path: Path = DB_PATH,
                      start_ms: int = START_DAY_MS - (MIN_HISTORY_DAYS + 2) * DAY_MS,
                      end_ms: int = END_DAY_MS) -> dict[str, dict[int, float]]:
    """All 1d closes in [start_ms, end_ms], keyed symbol -> {open_time: close}.

    Read-only connection; the full range is seal-checked and hard-capped
    BEFORE any row is read (§63.2.1). The lead-in before 2020-01-01 exists
    only so the 180-day history rule is evaluable on the first dates.
    """
    if end_ms > END_DAY_MS:
        raise RangeRefused(
            f"read range ends {end_ms} > 2024-12-31 — §63.2.1 hard cap; "
            f"the development measurement never touches a later bar")
    assert_range_allowed(start_ms, end_ms)
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT symbol, open_time, close FROM klines "
            "WHERE interval='1d' AND open_time >= ? AND open_time <= ?",
            (start_ms, end_ms)).fetchall()
    finally:
        con.close()
    out: dict[str, dict[int, float]] = {}
    for sym, ot, close in rows:
        out.setdefault(sym, {})[int(ot)] = float(close)
    return out


# Pure mechanics (no protocol content): per-closes index of sorted times /
# time-sets / classification verdicts, so the 1,827-date sweep is not
# O(symbols x rows) per date. Keyed by id(closes); one store per process.
_PREP: dict[int, tuple] = {}


def _prep(closes: dict[str, dict[int, float]]):
    key = id(closes)
    if key not in _PREP:
        class_ok = sorted(s for s in closes
                          if s not in FACTOR_SYMBOLS and classify(s).eligible)
        times = {s: np.array(sorted(closes[s]), dtype=np.int64)
                 for s in class_ok}
        sets = {s: set(closes[s]) for s in class_ok}
        _PREP[key] = (class_ok, times, sets)
    return _PREP[key]


def eligible_names(closes: dict[str, dict[int, float]], t_ms: int
                   ) -> tuple[list[str], dict[str, int]]:
    """§63.2.2 rules 1–3, with the per-date counts. Returns the names whose
    91-close window t−91 … t−1 is complete, plus the count dict."""
    class_ok, times, sets = _prep(closes)
    window_days = [t_ms - k * DAY_MS for k in range(91, 0, -1)]
    n_hist = n_dropped_incomplete = 0
    names = []
    for sym in class_ok:
        if int(np.searchsorted(times[sym], t_ms)) < MIN_HISTORY_DAYS:
            continue
        n_hist += 1
        if all(d in sets[sym] for d in window_days):
            names.append(sym)
        else:
            n_dropped_incomplete += 1
    return names, {"n_class_ok": len(class_ok), "n_hist_ok": n_hist,
                   "n_dropped_incomplete": n_dropped_incomplete}


def _returns(series: dict[int, float], t_ms: int) -> np.ndarray:
    px = np.array([series[t_ms - k * DAY_MS] for k in range(91, 0, -1)])
    return px[1:] / px[:-1] - 1.0


def measure_date(closes: dict[str, dict[int, float]], t_ms: int) -> dict:
    """One §63.2.4 row. Dates with N_t < 2 emit counts + nulls, never skip."""
    if t_ms > END_DAY_MS:
        raise RangeRefused(f"date {t_ms} > 2024-12-31 — §63.2.1 hard cap")
    row: dict = {"t_ms": t_ms}
    names, counts = eligible_names(closes, t_ms)
    row.update(counts)

    null_row = dict(row, n_dropped_zero_var=0, N_t=0, diag_expectation=None,
                    frobenius_dist=None,
                    **{f"offdiag_p{p}": None for p in PCTS},
                    **{f"eig{k}_share": None for k in EIG_KS},
                    **{f"eig{k}_share_ex1": None for k in (2, 3, 5)})
    window_days = [t_ms - k * DAY_MS for k in range(91, 0, -1)]
    if not all(d in closes.get(s, {}) for s in FACTOR_SYMBOLS
               for d in window_days):
        # the store's daily bars begin 2020-01-01, so dates before
        # 2020-04-01 have no complete 91-close factor window; the §63.2.4
        # rule applies — a row with counts and nulls, never a skip. (The
        # §59.3.2 180-day rule already makes N_t = 0 on all such dates.)
        return null_row

    f_btc = _returns(closes["BTCUSDT"], t_ms)
    f_eth_perp = orthogonalize_eth(f_btc, _returns(closes["ETHUSDT"], t_ms))

    resids, kept, n_zero_var = [], [], 0
    for sym in names:
        r = _returns(closes[sym], t_ms)
        eps = residual_series(r, f_btc, f_eth_perp,
                              estimate_betas(r, f_btc, f_eth_perp))
        if float(np.var(eps)) == 0.0:            # §63.2.2.5
            n_zero_var += 1
            continue
        resids.append(eps)
        kept.append(sym)
    row["n_dropped_zero_var"] = n_zero_var
    n = len(kept)
    row["N_t"] = n

    null_stats = {f"offdiag_p{p}": None for p in PCTS}
    null_stats.update({f"eig{k}_share": None for k in EIG_KS})
    null_stats.update({f"eig{k}_share_ex1": None for k in (2, 3, 5)})
    null_stats.update({"diag_expectation": None, "frobenius_dist": None})
    if n < 2:
        row.update(null_stats)
        return row

    c = np.corrcoef(np.vstack(resids))
    iu = np.triu_indices(n, k=1)
    off = c[iu]
    for p in PCTS:
        row[f"offdiag_p{p}"] = float(np.percentile(off, p))
    lam = np.sort(np.linalg.eigvalsh(c))[::-1]
    total = float(lam.sum())
    row["diag_expectation"] = 1.0 / n
    for k in EIG_KS:
        row[f"eig{k}_share"] = float(lam[k - 1]) / total if k <= n else None
    tail = float(lam[1:].sum())
    for k in (2, 3, 5):
        row[f"eig{k}_share_ex1"] = (float(lam[k - 1]) / tail
                                    if (k <= n and tail > 0) else None)
    row["frobenius_dist"] = float(np.linalg.norm(c - np.eye(n)))
    return row


def run(out_path: Path = OUT_PATH) -> dict:
    """The full 2020–2024 sweep. Writes one JSON line per date; returns the
    §63.2.4 aggregates (percentiles over defined dates, per statistic)."""
    closes = load_daily_closes()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    keys = ([f"offdiag_p{p}" for p in PCTS]
            + [f"eig{k}_share" for k in EIG_KS]
            + [f"eig{k}_share_ex1" for k in (2, 3, 5)]
            + ["frobenius_dist", "N_t"])
    series: dict[str, list] = {k: [] for k in keys}
    n_dates = 0
    with out_path.open("w", encoding="utf-8") as fh:
        t = START_DAY_MS
        while t <= END_DAY_MS:
            row = measure_date(closes, t)
            fh.write(json.dumps(row) + "\n")
            n_dates += 1
            for k in keys:
                if row.get(k) is not None:
                    series[k].append(row[k])
            t += DAY_MS
    agg = {"n_dates": n_dates}
    for k in keys:
        v = np.asarray(series[k], float)
        agg[k] = ({"n_defined": int(len(v))}
                  | {f"p{p}": float(np.percentile(v, p)) for p in PCTS}
                  if len(v) else {"n_defined": 0})
    return agg


if __name__ == "__main__":
    aggregates = run()
    print(json.dumps(aggregates, indent=2))

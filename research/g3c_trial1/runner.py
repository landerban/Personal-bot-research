"""
Gen-3 Trial 1 runner (STAGE_G3_C_RUN / NOTES 71) — assembles data for
the LOCKED specification and executes it exactly. Imports only locked
modules for every frozen quantity; this file adds data plumbing, no
statistical decisions. Development window only; seal-checked; the
holdout is never touched.

Implementation alignments (mechanical, recorded in the run record):
- Rows are indexed by TARGET day T; the decision instant is 22:00 UTC
  on D = T-1 (70.6.3), so crypto features use complete days <= D-1 and
  every locked array transform is evaluated at calendar index D.
- The daily exogenous series x[d] used both as the day-d feature and as
  the beta-regression leg is the change/level served by the locked
  reader at the day-d cutoff; beta pairs (r_i[d], x[d]) over the locked
  trailing-90 window ending D-1 - both legs knowable at the cutoff.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.universe_filter import classify, load_snapshot   # noqa: E402
from g3 import calibration as cal                              # noqa: E402
from g3 import eval as ev                                      # noqa: E402
from g3 import features as feat                                # noqa: E402
from g3 import models as mod                                   # noqa: E402
from g3 import sequential as seq                               # noqa: E402
from g3 import timing as tim                                   # noqa: E402
from rcm.eval_ic import spearman_ic                            # noqa: E402
from rcm.seal import assert_range_allowed                      # noqa: E402
from tools.g3_exogenous_loader import pit_view_usable          # noqa: E402

DEV_START = date(2020, 1, 1)
DEV_END = date(2024, 12, 31)
DAY_MS = 86_400_000
DEV_START_MS = 1_577_836_800_000
DEV_END_MS = 1_735_603_200_000
DATES = [DEV_START + timedelta(days=i)
         for i in range((DEV_END - DEV_START).days + 1)]
N = len(DATES)
IDX = {d: i for i, d in enumerate(DATES)}
DB = ROOT / "xsmom.db"
OUT = Path(__file__).resolve().parent / "out"

# locked exogenous conventions (70.6.4): (manifest key, kind)
EXOG = {
    "nasdaq100_ret_1d": ("fred_NASDAQ100", "logret"),
    "vix_level": ("cboe_VIX", "level"),
    "vix_chg_1d": ("cboe_VIX", "diff"),
    "us2y_level": ("fred_DGS2", "level"),
    "us2y_chg_1d": ("fred_DGS2", "diff"),
    "us10y_level": ("fred_DGS10", "level"),
    "us10y_chg_1d": ("fred_DGS10", "diff"),
    "usd_ret_1d": ("fred_DTWEXBGS", "logret"),
}
INT_TO_MOVE = {                     # 70.6.5: beta x market move pairs
    "name_int_ndx": ("fred_NASDAQ100", "nasdaq100_ret_1d"),
    "name_int_vix": ("cboe_VIX", "vix_chg_1d"),
    "name_int_2y": ("fred_DGS2", "us2y_chg_1d"),
    "name_int_10y": ("fred_DGS10", "us10y_chg_1d"),
    "name_int_usd": ("fred_DTWEXBGS", "usd_ret_1d"),
}


# ------------------------------------------------------------- loading

def load_crypto():
    """Seal-checked read of 1d closes and funding for the dev window."""
    assert_range_allowed(DEV_START_MS, DEV_END_MS)
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        krows = con.execute(
            "SELECT symbol, open_time, close FROM klines WHERE "
            "interval='1d' AND open_time >= ? AND open_time <= ?",
            (DEV_START_MS, DEV_END_MS)).fetchall()
        frows = con.execute(
            "SELECT symbol, funding_time, funding_rate FROM funding "
            "WHERE funding_time >= ? AND funding_time <= ?",
            (DEV_START_MS, DEV_END_MS)).fetchall()
    finally:
        con.close()
    closes: dict[str, dict[int, float]] = {}
    for s, ot, c in krows:
        closes.setdefault(s, {})[int(ot)] = float(c)
    funding: dict[str, list[tuple[int, float]]] = {}
    for s, ft, fr in frows:
        funding.setdefault(s, []).append((int(ft), float(fr)))
    for s in funding:
        funding[s].sort()
    return closes, funding


def log_returns(closes: dict[int, float]) -> np.ndarray:
    r = np.full(N, np.nan)
    for i in range(1, N):
        a = DEV_START_MS + (i - 1) * DAY_MS
        b = DEV_START_MS + i * DAY_MS
        if a in closes and b in closes and closes[a] > 0:
            r[i] = np.log(closes[b] / closes[a])
    return r


def funding_at_cutoffs(rows: list[tuple[int, float]]) -> np.ndarray:
    """Most recent settled 8h rate at each day's 22:00Z cutoff."""
    out = np.full(N, np.nan)
    j = 0
    last = np.nan
    for i, d in enumerate(DATES):
        cut = int(tim.decision_time(d).timestamp() * 1000)
        while j < len(rows) and rows[j][0] <= cut:
            last = rows[j][1]
            j += 1
        out[i] = last
    return out


CACHE = OUT / "cache"


def exog_series() -> dict[str, np.ndarray]:
    """Each locked exogenous feature at each day's cutoff, through the
    locked provenance-dispatching reader. Cached (pure memoization of
    deterministic locked-function outputs) so the assembly phases fit
    the process budget; the cache is built by prep_exog.py BEFORE the
    trial is logged started."""
    cpath = CACHE / "exog.json"
    if cpath.exists():
        raw = json.loads(cpath.read_text())
        return {k: np.array(v, dtype=float) for k, v in raw.items()}
    out = {name: np.full(N, np.nan) for name in EXOG}
    keys = sorted({k for k, _ in EXOG.values()})
    for key in keys:
        for i, d in enumerate(DATES):
            view = pit_view_usable(key, tim.decision_time(d))
            if not view:
                continue
            last = view[-1][1]
            prev = view[-2][1] if len(view) >= 2 else np.nan
            for name, (k2, kind) in EXOG.items():
                if k2 != key:
                    continue
                if kind == "level":
                    out[name][i] = last
                elif kind == "diff" and not np.isnan(prev):
                    out[name][i] = last - prev
                elif kind == "logret" and not np.isnan(prev) and prev > 0:
                    out[name][i] = np.log(last / prev)
        print(f"    exog {key} done", flush=True)
    out["slope_2s10s"] = out["us10y_level"] - out["us2y_level"]
    CACHE.mkdir(parents=True, exist_ok=True)
    cpath.write_text(json.dumps(
        {k: [None if not np.isfinite(x) else float(x) for x in v]
         for k, v in out.items()}))
    return out


# ------------------------------------------------------- feature panel

def build_panel():
    closes, funding = load_crypto()
    snap = load_snapshot()
    eligible = sorted(s for s in closes
                      if classify(s, snap).eligible)
    print(f"  eligible symbols: {len(eligible)}", flush=True)
    r = {s: log_returns(closes[s]) for s in eligible}
    f = {s: funding_at_cutoffs(funding.get(s, [])) for s in eligible}
    r_btc, f_btc = r["BTCUSDT"], f["BTCUSDT"]

    # market-level family 3/4 aggregates at index D (prior-day returns)
    fund_mean = np.full(N, np.nan)
    disp = np.full(N, np.nan)
    breadth = np.full(N, np.nan)
    universe_sets: list[list[str]] = [[] for _ in range(N)]
    for i in range(1, N):
        prev = [s for s in eligible if np.isfinite(r[s][i - 1])]
        universe_sets[i] = prev
        if len(prev) >= 2:
            vals = np.array([r[s][i - 1] for s in prev])
            disp[i] = vals.std(ddof=1)
            breadth[i] = float((vals > 0).mean())
        frates = [f[s][i] for s in prev if np.isfinite(f[s][i])]
        if frates:
            fund_mean[i] = float(np.mean(frates))

    exog = exog_series()
    print("  exogenous panel complete", flush=True)

    # direction feature dict on the calendar axis (evaluated at D)
    tr = feat.trailing_returns(r_btc)
    dirf = {
        "trend_1d": tr[:, 0], "trend_5d": tr[:, 1], "trend_21d": tr[:, 2],
        "vol_21d": feat.realised_vol(r_btc),
        "volofvol_21d": feat.vol_of_vol(r_btc),
        "funding_level": f_btc, "funding_mean": fund_mean,
        "xsec_dispersion": disp, "breadth_positive": breadth,
        **{k: exog[k] for k in feat.CROSS_ASSET},
    }
    X_dir = {m: feat.direction_matrix(dirf, names)
             for m, names in (("M0", feat.DIRECTION_M0),
                              ("M1", feat.DIRECTION_M1))}

    # per-name features + betas (betas cached by prep_betas.py)
    bpath = CACHE / "betas.npz"
    bcache = dict(np.load(bpath)) if bpath.exists() else {}
    per_name: dict[str, dict[str, np.ndarray]] = {}
    for s in eligible:
        trs = feat.trailing_returns(r[s])
        pn = {"name_trend_1d": trs[:, 0], "name_trend_5d": trs[:, 1],
              "name_trend_21d": trs[:, 2],
              "name_vol_21d": feat.realised_vol(r[s]),
              "name_funding_level": f[s]}
        for iname, (key, move) in INT_TO_MOVE.items():
            ck = f"{s}|{iname}"
            if ck in bcache:
                beta = bcache[ck]
                se = bcache[ck + "|se"]
            else:
                beta, se, _ = feat.beta_exposure(r[s], exog[move])
            pn[iname] = beta * exog[move]
            pn[iname + "_se"] = se
        per_name[s] = pn
    print(f"  per-name features + exposures complete", flush=True)

    common = {"xsec_dispersion": disp, "breadth_positive": breadth}
    return dict(eligible=eligible, r=r, X_dir=X_dir, per_name=per_name,
                common=common, r_btc=r_btc, universe_sets=universe_sets)


# ------------------------------------------------------------ assembly

def direction_rows(panel, model):
    """(targets T, X, y): row for target day T uses features at D=T-1;
    defined rows only (no partial windows)."""
    X = panel["X_dir"][model]
    ts, feats, ys = [], [], []
    for T in range(1, N):
        row = X[T - 1]
        if np.isnan(row).any() or np.isnan(panel["r_btc"][T]):
            continue
        ts.append(T)
        feats.append(row)
        ys.append(1.0 if panel["r_btc"][T] > 0 else 0.0)
    return ts, np.array(feats), np.array(ys)


def xsec_rows(panel, names_order):
    """Pooled (T, name) rows in chronological then name order."""
    rows = []
    for T in range(1, N):
        D = T - 1
        for s in panel["universe_sets"][D] or []:
            pn = panel["per_name"][s]
            vec = []
            ok = True
            for k in names_order:
                v = pn[k][D] if k.startswith("name_") else \
                    panel["common"][k][D]
                if not np.isfinite(v):
                    ok = False
                    break
                vec.append(v)
            tgt = panel["r"][s][T]
            if ok and np.isfinite(tgt):
                rows.append((T, s, np.array(vec), float(tgt)))
    return rows


# ------------------------------------------------------------ sequence

def run_direction(panel, model):
    ts, X, y = direction_rows(panel, model)
    dates_all = [DATES[t] for t in ts]
    out = {}
    for seg_no, (fs, fe, tsd, ted) in enumerate(seq.SEGMENTS, 1):
        fit_i = [i for i, d in enumerate(dates_all) if fs <= d <= fe]
        tgt_i = [i for i, d in enumerate(dates_all) if tsd <= d <= ted]
        assert max(dates_all[i] for i in fit_i) < \
            min(dates_all[i] for i in tgt_i)
        Xf, yf = X[fit_i], y[fit_i]
        folds = seq.inner_folds([dates_all[i] for i in fit_i])
        C, losses = mod.select_penalty(Xf, yf, "logistic", folds)
        ab = cal.fit_calibrator_oof(Xf, yf, C, folds)
        mu, sd = mod.standardize_fit(Xf)
        w = mod.logistic_fit(mod.standardize_apply(Xf, mu, sd), yf, C)
        raw = mod.logistic_predict(
            w, mod.standardize_apply(X[tgt_i], mu, sd))
        p = cal.platt_apply(ab, raw)
        clim = float(yf.mean())
        for j, i in enumerate(tgt_i):
            out[ts[i]] = (float(p[j]), clim, float(y[i]))
        print(f"    dir {model} segment {seg_no}: C={C} "
              f"fit_n={len(fit_i)} oos_n={len(tgt_i)} clim={clim:.4f}",
              flush=True)
    return out           # T -> (p_cal, p_clim, y)


def run_xsec(panel, model):
    names_order = feat.XSEC_M0 if model == "M0" else feat.XSEC_M1
    rows = xsec_rows(panel, names_order)
    dates_all = [DATES[t] for t, _, _, _ in rows]
    X = np.array([v for _, _, v, _ in rows])
    y = np.array([t for _, _, _, t in rows])
    preds = {}
    for seg_no, (fs, fe, tsd, ted) in enumerate(seq.SEGMENTS, 1):
        fit_i = [i for i, d in enumerate(dates_all) if fs <= d <= fe]
        tgt_i = [i for i, d in enumerate(dates_all) if tsd <= d <= ted]
        assert max(dates_all[i] for i in fit_i) < \
            min(dates_all[i] for i in tgt_i)
        folds = seq.inner_folds([dates_all[i] for i in fit_i])
        alpha, _ = mod.select_penalty(X[fit_i], y[fit_i], "ridge", folds)
        mu, sd = mod.standardize_fit(X[fit_i])
        w = mod.ridge_fit(mod.standardize_apply(X[fit_i], mu, sd),
                          y[fit_i], alpha)
        pr = mod.ridge_predict(w, mod.standardize_apply(X[tgt_i], mu, sd))
        for j, i in enumerate(tgt_i):
            T, s, _, tgt = rows[i]
            preds.setdefault(T, {})[s] = (float(pr[j]), tgt)
        print(f"    xsec {model} segment {seg_no}: alpha={alpha} "
              f"fit_rows={len(fit_i)} oos_rows={len(tgt_i)}", flush=True)
    return preds         # T -> {name: (pred, realized)}

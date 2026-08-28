#!/usr/bin/env python3
"""
Stage 8: validate B at 10% vol / $800 capital on 2024. ONE trial.

  python tools/stage8_validate.py

The first clean out-of-sample look at the honestly-derived deployment config.
Every prior validate measured something else: §38 measured a different vol
(20% @ $400) and §41 measured a floor-broken book (14% @ $400, 51.8% skips,
126% drift).

The config is PRE-CHOSEN AND LOCKED in NOTES §44, committed before this ran.
2024 is a pass/fail check, NOT a vol comparison -- 12% was declined on train,
on the record (NOTES §44.1), and no 10-vs-12 comparison is computed here.

Gates (NOTES §44.3, unchanged from §37/§40):
    G1  2024 price PnL >= 0
    G2  max drawdown <= 30% (USDT)
    G3  Sharpe >= 0.30 at USDC fees

Success band (NOTES §44.4): 2024 Sharpe 0.40-0.65. LOWER than B@20%'s band by
construction -- 10% vol earns less. JUDGE SHARPE, NOT RETURN.

Mechanism checks (NOTES §44.5) against the 10%/$800 TRAIN references: drift
22%, skip 21.55%, realised vol 9.28%.

Window is 2024 ONLY. The holdout (2025-01 -> 2026-07) is not touched by any
code path here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import metrics, runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from backtest.weights import BTC  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

USDT_TAKER, USDC_TAKER = 0.0005, 0.00036
SWITCH, SHARPE_FLOOR = 0.30, 0.30
BAND_LO, BAND_HI = 0.40, 0.65          # NOTES 44.4
DRIFT_CAVEAT = 0.40                    # NOTES 44.5 / 44.6
DRIFT_CLEAN = 0.30
# NOTES 43.6, the 10% @ $800 train row -- references, not new thresholds
TRAIN = {"sharpe": 0.651, "drift": 0.22, "skip": 0.2155, "vol": 0.0928,
         "max_dd": 0.1703}


def d(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def window(res):
    if not res.rebalances:
        return list(res.timestamps), np.array(res.equity)
    i = res.timestamps.index(res.rebalances[0].ts_fill)
    lo = max(i - 1, 0)
    return res.timestamps[lo:], np.array(res.equity[lo:])


def recost(res, new_rate):
    ts = res.timestamps
    fees = {t: f for t, f, _ in res.fees_by_day}
    turn = {t: v for t, _, v in res.fees_by_day}
    cum_old = np.cumsum([fees[t] for t in ts])
    cum_new = np.cumsum([turn[t] * new_rate for t in ts])
    ge, eq = np.array(res.gross_equity), np.array(res.equity)
    return ge - cum_new + (eq - ge + cum_old), float(cum_new[-1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--demeaned-db", default=str(ROOT / "xsmom_demeaned.db"))
    ap.add_argument("--vol-target", type=float, default=0.10,
                    help="NOTES 44.1 deployment vol, locked before the run")
    ap.add_argument("--capital", type=float, default=800.0,
                    help="NOTES 42/43 deployment capital")
    ap.add_argument("--purpose", default="stage8-validate-B-10pct-800-2024")
    ap.add_argument("--no-log-trial", action="store_true",
                    help="re-run for diagnostics without appending a second "
                         "trial row (the trial is already spent)")
    a = ap.parse_args()

    cfg = Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                 max_liquidity_rank=15, vol_target=a.vol_target,
                 initial_capital=a.capital)
    start, end = runner.split_view_range("validate")
    assert d(end) == "2024-12-31", f"validate window is wrong: {d(end)}"
    print(f"=== VALIDATE B @ {cfg.vol_target:.0%} vol @ ${cfg.initial_capital:.0f} "
          f"on {d(start)} -> {d(end)} | ONE TRIAL ===")
    print("    (holdout 2025-01 -> 2026-07 is not touched)")
    print("    (NOT a 10-vs-12 comparison: 12% was declined on train, NOTES 44.1)\n")

    store = PointInTimeStore(a.db, read_only=True)
    runner.ensure_data_covers(store, "validate")
    res = run_backtest(store, cfg, start, end)
    v_end = store.view_as_of(end)
    bclose = {b.close_time: b.close for b in v_end.klines(BTC, "1d", limit=2000)}
    store.close()

    ts, eq_usdt = window(res)
    eq_usdc_full, fees_usdc = recost(res, USDC_TAKER)
    i = res.timestamps.index(res.rebalances[0].ts_fill) if res.rebalances else 1
    eq_usdc = eq_usdc_full[max(i - 1, 0):]

    def stats(eq, fees):
        r = metrics.daily_returns(eq)
        peaks = np.maximum.accumulate(eq)
        dd = (peaks - eq) / peaks
        j = int(np.argmax(dd))
        return {"sharpe": metrics.sharpe(r), "ci": metrics.sharpe_bootstrap_ci(r),
                "ann_return": metrics.ann_return(eq), "vol": metrics.ann_vol(r),
                "max_dd": float(dd.max()), "dd_date": d(ts[j]),
                "net": float(eq[-1] - cfg.initial_capital), "fees": fees,
                "active": int((r != 0).sum()), "days": len(r)}

    su, sc = stats(eq_usdt, res.total_fees), stats(eq_usdc, fees_usdc)
    price = res.gross_pnl
    fund_u = su["net"] - price + su["fees"]
    fund_c = sc["net"] - price + sc["fees"]

    print(f"{'':22} {'USDT fees':>14} {'USDC fees':>14}")
    for k, lab, f in (("sharpe", "Sharpe", "{:+.3f}"),
                      ("ann_return", "ann return", "{:+.2%}"),
                      ("vol", "ann vol", "{:.2%}"),
                      ("max_dd", "max drawdown", "{:.2%}"),
                      ("net", "net PnL $", "{:+.2f}"),
                      ("fees", "fees $", "{:.2f}")):
        print(f"  {lab:<20} {f.format(su[k]):>14} {f.format(sc[k]):>14}")
    print(f"  {'Sharpe 90% CI':<20} {str(tuple(round(x, 2) for x in su['ci'])):>14} "
          f"{str(tuple(round(x, 2) for x in sc['ci'])):>14}")
    print(f"  {'price PnL $':<20} {price:>+14.2f} {price:>+14.2f}")
    print(f"  {'funding PnL $':<20} {fund_u:>+14.2f} {fund_c:>+14.2f}")
    print(f"  {'max DD date':<20} {su['dd_date']:>14} {sc['dd_date']:>14}")
    print(f"  {'active days':<20} {su['active']:>7}/{su['days']:<6} "
          f"{sc['active']:>7}/{sc['days']:<6}")

    # ---------------- TIER 1 GATES (NOTES 44.3) ----------------
    print("\n=== TIER 1 GATES (pre-registered NOTES 44.3, before the run) ===")
    g1 = bool(price >= 0)
    g2 = bool(su["max_dd"] <= SWITCH)
    g3 = bool(sc["sharpe"] >= SHARPE_FLOOR)
    print(f"  G1 price PnL >= 0        : {price:+.2f}          "
          f"{'PASS' if g1 else 'FAIL -> REFUTED'}")
    print(f"  G2 maxDD <= 30% (USDT)   : {su['max_dd']:.2%}           "
          f"{'PASS' if g2 else 'FAIL -> REFUTED'}   "
          f"(train 10%/$800: {TRAIN['max_dd']:.2%})")
    print(f"  G3 Sharpe >= 0.30 (USDC) : {sc['sharpe']:+.3f}          "
          f"{'PASS' if g3 else 'FAIL -> too weak for the holdout'}")
    all_pass = g1 and g2 and g3

    # ---------------- structural invariants ----------------
    print("\n=== STRUCTURAL INVARIANTS (NOTES 44.5) ===")
    r = metrics.daily_returns(eq_usdt)
    bt = np.array([bclose.get(t, np.nan) for t in ts], float)
    br = np.diff(bt) / bt[:-1]
    n = min(len(r), len(br))
    ok = np.isfinite(r[:n]) & np.isfinite(br[:n])
    beta = float(np.cov(r[:n][ok], br[:n][ok])[0, 1] / np.var(br[:n][ok]))
    lev = np.array([rb.realised_gross_leverage for rb in res.rebalances])
    tilt = max(abs(sum(rb.final_weights.values())
                   - rb.vol_scale * (1 - rb.beta_scale))
               for rb in res.rebalances)
    act = su["active"] / su["days"]
    beta_ok = bool(abs(beta) <= 0.15)
    tilt_ok = bool(tilt <= 1e-9)
    act_ok = bool(act >= 0.80)
    print(f"  realised beta to BTC     : {beta:+.3f}   (band +/-0.15)  "
          f"{'ok' if beta_ok else 'BREACH'}")
    print(f"  dollar-tilt identity     : {tilt:.2e}   (<= 1e-9)  "
          f"{'ok' if tilt_ok else 'BREACH'}")
    print(f"  active-days fraction     : {act:.1%}   (floor 80%)  "
          f"{'ok' if act_ok else 'BREACH'}")
    print(f"  realised gross leverage  : median {np.median(lev):.2f} "
          f"p95 {np.percentile(lev, 95):.2f}")

    # ---------------- FLOOR / MECHANISM (NOTES 44.5) ----------------
    print("\n=== FLOOR MECHANISM vs the 10%/$800 TRAIN row (NOTES 43.6) ===")
    skip_rate = len(res.skips) / max(res.n_scheduled, 1)
    notionals = np.array([abs(w) * rb.equity_at_decision
                          for rb in res.rebalances
                          for w in rb.final_weights.values()])
    print(f"  skip rate                : {skip_rate:.2%} of {res.n_scheduled} "
          f"scheduled   (train {TRAIN['skip']:.2%})")
    for reason, k in sorted(res.skip_counts().items(), key=lambda kv: -kv[1]):
        print(f"      {reason:<26} {k:>5}")
    print(f"  rebalances / skips       : {len(res.rebalances)} / {len(res.skips)}")
    print(f"  realised vol vs target   : {su['vol']:.2%} vs {cfg.vol_target:.0%}  "
          f"({cfg.vol_target - su['vol']:+.2%})   (train {TRAIN['vol']:.2%})")
    if len(notionals):
        print(f"  position notional        : median ${np.median(notionals):.2f} | "
              f"p05 ${np.percentile(notionals, 5):.2f} | "
              f"min ${notionals.min():.2f} | "
              f"under $5: {int((notionals < 5.0).sum()):,} of {len(notionals):,}")
    print(f"  turnover                 : "
          f"{metrics.turnover_annualised(res.total_turnover, eq_usdt):.1f}x")
    print(f"  price / funding split    : "
          f"{price / su['net'] if su['net'] else float('nan'):.0%} price / "
          f"{fund_u / su['net'] if su['net'] else float('nan'):.0%} funding")
    print(f"  long/short price PnL     : long {res.gross_pnl_long:+.2f} | "
          f"short {res.gross_pnl_short:+.2f}")

    # ---------------- 2024 drift ----------------
    print("\n=== 2024 DRIFT DECOMPOSITION (DIAGNOSTIC, not a trial) ===")
    st2 = PointInTimeStore(a.demeaned_db, read_only=True)
    resd = run_backtest(st2, cfg, start, end)
    st2.close()
    _, eqd = window(resd)
    sr_real = metrics.sharpe(metrics.daily_returns(eq_usdt))
    sr_dm = metrics.sharpe(metrics.daily_returns(eqd))
    dfrac = (sr_real - sr_dm) / sr_real if sr_real else float("nan")
    print(f"  Sharpe real {sr_real:+.3f} | demeaned {sr_dm:+.3f} | "
          f"drift {sr_real - sr_dm:+.3f} ({dfrac:.0%} of total)")
    print(f"  train 10%/$800 was {TRAIN['drift']:.0%}; caveat fires above "
          f"{DRIFT_CAVEAT:.0%}; 41 (the 14%/$400 failure) was 126%")

    drift_clean = bool(np.isfinite(dfrac) and dfrac <= DRIFT_CLEAN)
    drift_caveat = bool(np.isfinite(dfrac) and dfrac > DRIFT_CAVEAT)
    skips_worse = bool(skip_rate > TRAIN["skip"] + 0.10)

    # ---------------- VERDICT (NOTES 44.6) ----------------
    print("\n=== VERDICT (NOTES 44.6, fixed before the run) ===")
    if not g1:
        v = ("G1 FAILED -- 2024 price PnL is negative. Momentum did not survive "
             "at this size. REFUTED. Holdout stays sealed.")
    elif not g2:
        v = (f"G2 FAILED -- max drawdown {su['max_dd']:.2%} > 30%, against a "
             f"{TRAIN['max_dd']:.2%} train drawdown at the same vol and capital. "
             f"That contradicts the train sizing: INVESTIGATE FOR A BUG before "
             f"accepting it as a risk finding.")
    elif not g3:
        v = (f"G3 FAILED -- USDC Sharpe {sc['sharpe']:+.3f} < 0.30. Not refuted, "
             f"but too weak to spend the holdout on.")
    elif drift_caveat or skips_worse:
        parts = []
        if drift_caveat:
            parts.append(f"drift {dfrac:.0%} > {DRIFT_CAVEAT:.0%}")
        if skips_worse:
            parts.append(f"skips {skip_rate:.2%} vs train {TRAIN['skip']:.2%}")
        v = ("ALL TIER 1 PASS, BUT FLOOR-CONTAMINATED OUT OF SAMPLE -- "
             + "; ".join(parts)
             + ". The same trap as 41. A serious caveat, NOT holdout-ready.")
    else:
        band = ("inside the 0.40-0.65 drift-adjusted success band"
                if BAND_LO <= sc["sharpe"] <= BAND_HI
                else "ABOVE the 0.40-0.65 drift-adjusted band"
                if sc["sharpe"] > BAND_HI
                else "above the 0.30 floor but BELOW the 0.40-0.65 band")
        v = (f"ALL TIER 1 PASS and the mechanism is clean. USDC Sharpe "
             f"{sc['sharpe']:+.3f} is {band}; drift {dfrac:.0%} "
             f"(train {TRAIN['drift']:.0%}); skips {skip_rate:.2%} "
             f"(train {TRAIN['skip']:.2%}). NOT REFUTED -- the best obtainable "
             f"outcome, and NOT proof.")
    print(f"  {v}")
    print("\n  Judge Sharpe, not return: 10% vol earns less by design "
          "(NOTES 44.4). The absolute return is smaller than every prior "
          "config's and that is arithmetic, not weakness.")
    print("  Holdout decision is DEFERRED TO THE USER (NOTES 37.5 / 44.7). "
          "Not taken in this session.")

    if a.no_log_trial:
        print("  --no-log-trial: trial row NOT appended (already logged)")
    else:
        runner.log_trial(cfg, "validate", a.purpose, runner.summarise(res))
        print(f"  trial result appended to trials.jsonl (purpose={a.purpose})")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "stage8_validate_B",
            "vol_target": cfg.vol_target, "capital": cfg.initial_capital,
            "purpose": a.purpose,
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "validate", "usdt": su, "usdc": sc,
            "price_pnl": price, "funding_usdt": fund_u, "funding_usdc": fund_c,
            "gates": {"G1_price_pnl": g1, "G2_drawdown": g2, "G3_sharpe": g3,
                      "all_pass": all_pass},
            "band": [BAND_LO, BAND_HI],
            "invariants": {"beta": beta, "beta_ok": beta_ok, "tilt": tilt,
                           "tilt_ok": tilt_ok, "active_frac": act,
                           "active_ok": act_ok,
                           "lev_median": float(np.median(lev))},
            "floor": {
                "skip_rate": skip_rate,
                "skips_by_reason": res.skip_counts(),
                "n_rebalances": len(res.rebalances),
                "n_scheduled": res.n_scheduled,
                "realised_vol": su["vol"],
                "notional_median": float(np.median(notionals)) if len(notionals) else None,
                "notional_p05": float(np.percentile(notionals, 5)) if len(notionals) else None,
                "notional_min": float(notionals.min()) if len(notionals) else None,
                "n_under_floor": int((notionals < 5.0).sum()) if len(notionals) else None,
                "n_positions": int(len(notionals)),
            },
            "drift_2024": {"real": sr_real, "demeaned": sr_dm, "fraction": dfrac,
                           "clean": drift_clean, "caveat": drift_caveat},
            "train_reference": TRAIN,
            "verdict": v,
            "note": "first OOS look at the honestly-derived deployment config; "
                    "holdout untouched; drift run is DIAGNOSTIC ONLY; NOT a "
                    "10-vs-12 vol comparison",
        }) + "\n")
    print("  diagnostics appended (kind=stage8_validate_B)")


if __name__ == "__main__":
    main()

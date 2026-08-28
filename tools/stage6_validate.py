#!/usr/bin/env python3
"""
Stage 6: validate B on 2024. THE FIRST OUT-OF-SAMPLE LOOK. One trial.

  python tools/stage6_validate.py

Grades config B against the three Tier 1 gates pre-registered in NOTES 37
(committed at 8ac7ec4 before this ran), reports the Tier 2 mechanism checks,
and runs the 2024 drift decomposition as a diagnostic.

Window is 2024 ONLY. The holdout (2025-01 -> 2026-07) is not touched by any
code path here.

Remember the drift adjustment (NOTES 37.1): 41% of B's train Sharpe was
drift, so ~0.66 is the success expectation and 0.5-0.7 is CONSISTENT WITH
SUCCESS, not failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
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


def d(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


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
    ap.add_argument("--vol-target", type=float, default=0.20,
                    help="vol target; 0.14 is the Stage 6a deployment vol")
    ap.add_argument("--purpose", default="stage6-validate-B-2024")
    ap.add_argument("--no-log-trial", action="store_true",
                    help="re-run for diagnostics without appending a "
                         "second trial row (the trial is already spent)")
    a = ap.parse_args()

    cfg = Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                 max_liquidity_rank=15, vol_target=a.vol_target)
    start, end = runner.split_view_range("validate")
    assert d(end) == "2024-12-31", f"validate window is wrong: {d(end)}"
    print(f"=== Stage 6: VALIDATE B on {d(start)} -> {d(end)} | ONE TRIAL ===")
    print("    (holdout 2025-01 -> 2026-07 is not touched)\n")

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
    for k, lab, f in (("sharpe", "Sharpe", "{:+.3f}"), ("ann_return", "ann return", "{:+.2%}"),
                      ("vol", "ann vol", "{:.2%}"), ("max_dd", "max drawdown", "{:.2%}"),
                      ("net", "net PnL $", "{:+.2f}"), ("fees", "fees $", "{:.2f}")):
        print(f"  {lab:<20} {f.format(su[k]):>14} {f.format(sc[k]):>14}")
    print(f"  {'Sharpe 90% CI':<20} {str(tuple(round(x,2) for x in su['ci'])):>14} "
          f"{str(tuple(round(x,2) for x in sc['ci'])):>14}")
    print(f"  {'price PnL $':<20} {price:>+14.2f} {price:>+14.2f}")
    print(f"  {'funding PnL $':<20} {fund_u:>+14.2f} {fund_c:>+14.2f}")
    print(f"  {'max DD date':<20} {su['dd_date']:>14} {sc['dd_date']:>14}")
    print(f"  {'active days':<20} {su['active']:>7}/{su['days']:<6} "
          f"{sc['active']:>7}/{sc['days']:<6}")

    # ---------------- TIER 1 GATES ----------------
    print(f"\n=== TIER 1 GATES (NOTES 37.2, fixed before the run) ===")
    # cast: numpy bools are not JSON serializable and this record is logged
    g1 = bool(price >= 0)
    g2 = bool(su["max_dd"] <= SWITCH)
    g3 = bool(sc["sharpe"] >= SHARPE_FLOOR)
    print(f"  G1 price PnL >= 0        : {price:+.2f}          "
          f"{'PASS' if g1 else 'FAIL -> REFUTED'}")
    print(f"  G2 maxDD <= 30% (USDT)   : {su['max_dd']:.2%}           "
          f"{'PASS' if g2 else 'FAIL -> REFUTED'}")
    print(f"  G3 Sharpe >= 0.30 (USDC) : {sc['sharpe']:+.3f}          "
          f"{'PASS' if g3 else 'FAIL -> too weak for the holdout'}")
    all_pass = g1 and g2 and g3

    # ---------------- TIER 2 ----------------
    print(f"\n=== TIER 2 mechanism checks (report, do not auto-refute) ===")
    r = metrics.daily_returns(eq_usdt)
    bt = np.array([bclose.get(t, np.nan) for t in ts], float)
    br = np.diff(bt) / bt[:-1]
    n = min(len(r), len(br))
    ok = np.isfinite(r[:n]) & np.isfinite(br[:n])
    beta = float(np.cov(r[:n][ok], br[:n][ok])[0, 1] / np.var(br[:n][ok]))
    lev = np.array([rb.realised_gross_leverage for rb in res.rebalances])
    tilt = max(abs(sum(rb.final_weights.values()) - rb.vol_scale * (1 - rb.beta_scale))
               for rb in res.rebalances)
    act = su["active"] / su["days"]
    print(f"  realised beta to BTC     : {beta:+.3f}   (band +/-0.15)  "
          f"{'ok' if abs(beta) <= 0.15 else 'BREACH'}")
    print(f"  realised gross leverage  : median {np.median(lev):.2f} p95 "
          f"{np.percentile(lev,95):.2f}   (train 0.52 / 0.97)")
    print(f"  dollar-tilt identity     : {tilt:.2e}   (<= 1e-9)  "
          f"{'ok' if tilt <= 1e-9 else 'BREACH'}")
    print(f"  active-days fraction     : {act:.1%}   (floor 80%)  "
          f"{'ok' if act >= 0.80 else 'BREACH'}")
    split_u = price / su["net"] if su["net"] else float("nan")
    print(f"  price / funding split    : {split_u:.0%} price / "
          f"{fund_u/su['net'] if su['net'] else float('nan'):.0%} funding"
          f"   (train B 77/23)")
    print(f"  long/short price PnL     : long {res.gross_pnl_long:+.2f} | "
          f"short {res.gross_pnl_short:+.2f}")
    print(f"  turnover                 : "
          f"{metrics.turnover_annualised(res.total_turnover, eq_usdt):.1f}x  "
          f"| rebalances {len(res.rebalances)} | skips {len(res.skips)}")

    # ---------------- 2024 drift ----------------
    print(f"\n=== 2024 drift decomposition (DIAGNOSTIC, not a trial) ===")
    st2 = PointInTimeStore(a.demeaned_db, read_only=True)
    resd = run_backtest(st2, cfg, start, end)
    st2.close()
    _, eqd = window(resd)
    sr_real, sr_dm = metrics.sharpe(metrics.daily_returns(eq_usdt)), \
        metrics.sharpe(metrics.daily_returns(eqd))
    dfrac = (sr_real - sr_dm) / sr_real if sr_real else float("nan")
    print(f"  Sharpe real {sr_real:+.3f} | demeaned {sr_dm:+.3f} | "
          f"drift {sr_real-sr_dm:+.3f} ({dfrac:.0%} of total)")
    print(f"  train B was 41%; synthetic zero-drift floor ~18%")

    print(f"\n=== VERDICT ===")
    if not g1:
        v = "G1 FAILED -- momentum did not survive. B REFUTED. Do not proceed to holdout."
    elif not g2:
        v = ("G2 FAILED -- B at 20% vol is unrunnable. The parked vol question is "
             "ANSWERED: B needs lower vol, a NEW config and a NEW validate. Holdout stays sealed.")
    elif not g3:
        v = "G3 FAILED -- not refuted, but too weak to spend the holdout on."
    else:
        band = ("0.5-0.7, the drift-adjusted success band" if 0.5 <= sc["sharpe"] <= 0.7
                else "above the drift-adjusted expectation" if sc["sharpe"] > 0.7
                else "above the floor but below the drift-adjusted band")
        v = (f"ALL TIER 1 PASS. Sharpe {sc['sharpe']:+.3f} at USDC fees is {band}. "
             f"NOT REFUTED -- which is the best obtainable outcome, and is NOT proof.")
    print(f"  {v}")
    print(f"\n  Holdout decision is DEFERRED TO THE USER (NOTES 37.5). "
          f"Not taken in this session.")

    if a.no_log_trial:
        print("  --no-log-trial: trial row NOT appended (already logged)")
    else:
        runner.log_trial(cfg, "validate", a.purpose, runner.summarise(res))
        print("  trial 10 result appended to trials.jsonl (budget 10 of 25)")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "stage6_validate_B",
            "vol_target": cfg.vol_target, "purpose": a.purpose,
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "validate", "usdt": su, "usdc": sc,
            "price_pnl": price, "funding_usdt": fund_u, "funding_usdc": fund_c,
            "gates": {"G1_price_pnl": g1, "G2_drawdown": g2, "G3_sharpe": g3,
                      "all_pass": all_pass},
            "tier2": {"beta": beta, "lev_median": float(np.median(lev)),
                      "tilt": tilt, "active_frac": act,
                      "price_share": split_u,
                      "long": res.gross_pnl_long, "short": res.gross_pnl_short},
            "drift_2024": {"real": sr_real, "demeaned": sr_dm, "fraction": dfrac},
            "verdict": v,
            "note": "first out-of-sample look; holdout untouched; drift run is "
                    "DIAGNOSTIC ONLY",
        }) + "\n")
    print(f"  diagnostics appended (kind=stage6_validate_B)")


if __name__ == "__main__":
    main()

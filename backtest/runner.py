"""
CLI runner: split boundaries, trial logging, the holdout guard, reporting.

TRIAL DISCIPLINE
----------------
Every backtest execution through this runner — exploratory, abandoned, or
final — appends one record to trials.jsonl with the git commit it ran under.
The budget is 20 total; the pre-registered grid is 6. Unit tests run the
engine on synthetic stores and do not pass through here: they validate the
harness, not the strategy, and consume no budget.

HOLDOUT
-------
One look, ever. The runner refuses without the explicit flag, records the
look in holdout_log.json BEFORE running (a crashed look is still a look),
and refuses permanently once the log shows a run. There is no override.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backtest import metrics
from backtest.engine import BacktestResult, Config, run_backtest
from pitdata.store import PointInTimeStore

DAY_MS = 86_400_000
ROOT = Path(__file__).resolve().parents[1]
TRIALS_PATH = ROOT / "trials.jsonl"
DIAGNOSTICS_PATH = ROOT / "diagnostics.jsonl"   # attribution, never trials
HOLDOUT_LOG = ROOT / "holdout_log.json"
DIAGNOSTIC_NOTE = ("DIAGNOSTIC ONLY -- uses full-sample means, "
                   "not runnable live.")

# Pre-registered splits. Do not cross these. The holdout ends where the data
# ends (2026-07-31, Stage 2b A5) -- not 2026-08-31 -- so an empty month is
# never silently included; ensure_data_covers() enforces it at run time.
SPLITS = {
    "train": ("2019-09-01", "2023-12-31"),
    "validate": ("2024-01-01", "2024-12-31"),
    "holdout": ("2025-01-01", "2026-07-31"),
}


def split_years(split: str) -> float:
    d0, d1 = SPLITS[split]
    return (_date_ms(d1) - _date_ms(d0) + DAY_MS) / DAY_MS / metrics.ANN


def data_end_ms(store: PointInTimeStore) -> int | None:
    """close_time of the last BTCUSDT daily bar in the store (BTC has the
    longest history, so it bounds the dataset)."""
    view = store.view_as_of(int(time.time() * 1000))
    bars = view.klines("BTCUSDT", limit=1)
    store.reset_clock()  # a deliberate probe, not a backtest step
    return bars[-1].close_time if bars else None


def ensure_data_covers(store: PointInTimeStore, split: str) -> None:
    """Refuse a split the data does not reach. Runs BEFORE the holdout look
    is recorded, so a coverage mistake cannot spend the look."""
    end = data_end_ms(store)
    _, split_end = split_view_range(split)
    if end is None or end < split_end - DAY_MS:
        sys.exit(
            f"data ends {_ms_date(end)} but split '{split}' ends "
            f"{SPLITS[split][1]}; refusing to run over an empty tail. "
            f"Backfill further or amend SPLITS deliberately."
        )


def _ms_date(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

# The CLI default follows Config so the two cannot drift apart.
DEFAULT_CAPITAL = Config.__dataclass_fields__["initial_capital"].default

GRID_LOOKBACKS = (7, 14, 28)
GRID_SKIPS = (0, 2)


def _date_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def split_view_range(split: str) -> tuple[int, int]:
    """First/last end-of-day view stamps for a split (bar-close aligned)."""
    d0, d1 = SPLITS[split]
    return _date_ms(d0) + DAY_MS - 1, _date_ms(d1) + DAY_MS - 1


def config_hash(cfg: Config) -> str:
    blob = json.dumps(asdict(cfg), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def git_state() -> tuple[str | None, bool]:
    """(commit, dirty). Without the commit a result is not reproducible."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=ROOT,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        )
        return commit, dirty
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None, True


def leverage_stats(result: BacktestResult) -> dict:
    """Realised gross leverage distribution over filled rebalances."""
    L = np.array([rb.realised_gross_leverage for rb in result.rebalances])
    if len(L) == 0:
        return {}
    return {
        "min": float(L.min()),
        "p05": float(np.percentile(L, 5)),
        "median": float(np.median(L)),
        "p95": float(np.percentile(L, 95)),
        "max": float(L.max()),
        "frac_below_1": float((L < 1.0).mean()),
    }


def summarise(result: BacktestResult) -> dict:
    ts, eq = metrics.strategy_window(result)
    rets = metrics.daily_returns(eq)
    lev = leverage_stats(result)
    min_pos = min(
        (rb.min_position_notional for rb in result.rebalances), default=None
    )
    binding = max(
        (rb.binding_min_notional for rb in result.rebalances
         if rb.binding_min_notional is not None), default=None,
    )
    return {
        "sharpe": _r(metrics.sharpe(rets)),
        "ann_return": _r(metrics.ann_return(eq)),
        "ann_vol": _r(metrics.ann_vol(rets)),
        "max_dd": _r(metrics.max_drawdown(eq)),
        "turnover": _r(metrics.turnover_annualised(result.total_turnover, eq)),
        "fee_drag": _r(metrics.fee_drag(result.total_fees, result.gross_pnl)),
        "n_rebalances": len(result.rebalances),
        "n_scheduled": result.n_scheduled,
        "n_skips": len(result.skips),
        "skips_by_reason": result.skip_counts(),
        "n_days": len(eq),
        "leverage_median": _r(lev.get("median", float("nan"))),
        "leverage_frac_below_1": _r(lev.get("frac_below_1", float("nan"))),
        "min_position_notional": _r(min_pos) if min_pos is not None else None,
        "binding_min_notional": binding,
        "gross_pnl": _r(result.gross_pnl),
        "gross_pnl_long": _r(result.gross_pnl_long),
        "gross_pnl_short": _r(result.gross_pnl_short),
        "total_fees": _r(result.total_fees),
        "total_funding": _r(result.total_funding),
        "forced_liquidations": len(result.forced_liquidations),
        "n_delistings": len(result.delistings),
        "n_delist_estimated": sum(1 for *_, e in result.delistings if e),
        "n_data_gap_exits": len(result.data_gap_exits),
        "sharpe_ci90": [_r(x) for x in metrics.sharpe_bootstrap_ci(
            metrics.daily_returns(metrics.strategy_window(result)[1]))],
        "min_intraday_equity": _r(min(
            (e for _, e in result.daily_worst_equity), default=float("nan"))),
        "n_rescales": len(result.rescales),
        "rescale_fees": _r(sum(r.fees for r in result.rescales)),
        "n_rescale_drops": len(result.rescale_drops),
        "slippage_bps": result.config.slippage_bps_per_side,
        "execution_delay_minutes": result.config.execution_delay_minutes,
        "minute_fill_fallbacks": result.minute_fill_fallbacks,
        "total_slippage": _r(result.total_slippage),
        "min_gross_leverage": result.config.min_gross_leverage,
        "beta_se_median": _r(float(np.median(
            [rb.beta_se_median for rb in result.rebalances]))
        ) if result.rebalances else None,
        "beta_se_p95": _r(float(np.percentile(
            [rb.beta_se_median for rb in result.rebalances], 95))
        ) if result.rebalances else None,
        "beta_shrink_median": _r(float(np.median(
            [rb.beta_shrink_median for rb in result.rebalances]))
        ) if result.rebalances else None,
        "missing_funding_settlements": result.missing_funding_settlements,
        "missing_funding_exposure_ratio": _r(
            result.funding_notional_missing / result.funding_notional_expected
            if result.funding_notional_expected > 0 else float("nan")),
    }


def _r(x: float, nd: int = 6) -> float | None:
    return None if (x is None or math.isnan(x)) else round(x, nd)


def log_trial(
    cfg: Config, split: str, purpose: str, summary: dict,
    error: str | None = None,
) -> None:
    commit, dirty = git_state()
    rec: dict = {
        "ts": int(time.time()),
        "git_commit": commit,
        "config_hash": config_hash(cfg),
        "config": asdict(cfg),
        "split": split,
        "purpose": purpose,
        **summary,
    }
    if dirty:
        rec["dirty"] = True
    if error is not None:
        rec["error"] = error
    with open(TRIALS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def load_trials() -> list[dict]:
    if not TRIALS_PATH.exists():
        return []
    out = []
    with open(TRIALS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def trial_srs_for_deflation(split: str) -> list[float]:
    """
    Daily-frequency Sharpe per DISTINCT config already tried on this split
    (latest record per config; re-running the same config is not a new
    independent trial). Used as the DSR's trial set.
    """
    latest: dict[str, float] = {}
    for rec in load_trials():
        if rec.get("split") != split or rec.get("error") or rec.get("void"):
            continue
        sr = rec.get("sharpe")
        if sr is None:
            continue
        latest[strategy_key(rec["config"])] = sr / math.sqrt(metrics.ANN)
    return list(latest.values())


def strategy_key(config: dict) -> str:
    """
    Trial identity for the DSR: the config MINUS the slippage assumption.
    Stage 2c 4 counts the 6-point grid at two slippage settings as 6
    trials, not 12, because slippage is a cost assumption reported as a
    pair rather than a strategy parameter selected between. The
    conservative count (every distinct config hash) is printed alongside
    so the stricter reading is always visible.
    """
    # Both slippage and execution delay are COST ASSUMPTIONS run as
    # sensitivity pairs and never selected between (Stage 2e 2, Stage 3c
    # 5), so neither makes a run a distinct trial. Dropping delay merges
    # no existing rows: every logged row has delay=1.
    drop = ("slippage_bps_per_side", "execution_delay_minutes")
    c = {k: v for k, v in config.items() if k not in drop}
    return json.dumps(c, sort_keys=True)


def n_trials_conservative(split: str) -> int:
    """Distinct non-void config hashes on this split (slippage counted)."""
    return len({
        rec["config_hash"] for rec in load_trials()
        if rec.get("split") == split and not rec.get("void")
        and not rec.get("error")
    })


def report(result: BacktestResult, split: str) -> None:
    ts, eq = metrics.strategy_window(result)
    rets = metrics.daily_returns(eq)
    sr = metrics.sharpe(rets)
    vol = metrics.ann_vol(rets)
    aret = metrics.ann_return(eq)
    mdd = metrics.max_drawdown(eq)
    aw, al = metrics.avg_win_loss(rets)
    skew, kurt = metrics.moments(rets)
    trial_srs = trial_srs_for_deflation(split)
    dsr = metrics.deflated_sharpe(
        sr / math.sqrt(metrics.ANN) if not math.isnan(sr) else float("nan"),
        len(rets), trial_srs, skew, kurt,
    )

    def fmt(x, pct=False, nd=2):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return "n/a"
        return f"{x * 100:.{nd}f}%" if pct else f"{x:.{nd}f}"

    print(f"\n=== {split} | {len(eq)} days | "
          f"{len(result.rebalances)} rebalances | capital ${result.config.initial_capital:.0f} ===")
    print(f"ann return        {fmt(aret, pct=True)}")
    print(f"ann vol           {fmt(vol, pct=True)}")
    lo, hi = metrics.sharpe_bootstrap_ci(rets)
    print(f"sharpe            {fmt(sr)}"
          + ("" if math.isnan(lo) else
             f"   90% CI [{lo:+.2f}, {hi:+.2f}] (stationary bootstrap)"))
    print(f"max drawdown      {fmt(mdd, pct=True)}   "
          f"(expected ~ sigma/2S = {fmt(metrics.expected_max_dd(vol, sr), pct=True)})")
    print(f"turnover (ann, x capital)  "
          f"{fmt(metrics.turnover_annualised(result.total_turnover, eq))}")
    print(f"FEE DRAG (% of gross PnL)  "
          f"{fmt(metrics.fee_drag(result.total_fees, result.gross_pnl), pct=True)}")
    print(f"  gross PnL {result.gross_pnl:+.2f} | fees {result.total_fees:.2f}"
          f" | funding {result.total_funding:+.2f}")
    n_active = metrics.active_days(rets)
    print(f"hit rate          {fmt(metrics.hit_rate(rets), pct=True)}   "
          f"avg win {fmt(aw, pct=True, nd=3)} vs avg loss {fmt(al, pct=True, nd=3)}"
          f"   (over {n_active} active of {len(rets)} window days)")
    if result.rebalances:
        bse = np.array([rb.beta_se_median for rb in result.rebalances])
        bsh = np.array([rb.beta_shrink_median for rb in result.rebalances])
        print(f"beta SE (median per book): median {np.median(bse):.3f} | "
              f"p95 {np.percentile(bse, 95):.3f} | max {bse.max():.3f}   "
              f"shrink |post-pre|: median {np.median(bsh):.3f}")
    print(f"long-leg PnL {result.gross_pnl_long:+.2f} | "
          f"short-leg PnL {result.gross_pnl_short:+.2f}")

    lev = leverage_stats(result)
    if lev:
        print(f"realised gross leverage  min {lev['min']:.2f} | p05 {lev['p05']:.2f}"
              f" | median {lev['median']:.2f} | p95 {lev['p95']:.2f}"
              f" | max {lev['max']:.2f} | below 1.0x: {fmt(lev['frac_below_1'], pct=True)}")
    skips = result.skip_counts()
    n_sched = max(result.n_scheduled, 1)
    print(f"skipped rebalances: {len(result.skips)} of {result.n_scheduled} "
          f"scheduled ({fmt(len(result.skips) / n_sched, pct=True)})")
    for reason, n in sorted(skips.items(), key=lambda kv: -kv[1]):
        flag = "  <-- sizing floor" if reason == "below_min_notional" else ""
        print(f"    {reason:<26} {n:>5}  ({fmt(n / n_sched, pct=True)}){flag}")
    if "below_min_notional" not in skips and result.rebalances:
        print("    below_min_notional             0  (0.00%)")
    min_pos = min(
        (rb.min_position_notional for rb in result.rebalances), default=None
    )
    binding = max(
        (rb.binding_min_notional for rb in result.rebalances
         if rb.binding_min_notional is not None), default=None,
    )
    if min_pos is not None:
        print(f"min position notional taken  ${min_pos:.2f}  vs binding "
              f"MIN_NOTIONAL "
              + (f"${binding:.2f}" if binding is not None else "n/a (no filter)"))
    rs_fees = sum(r.fees for r in result.rescales)
    rs_turn = sum(r.turnover_notional for r in result.rescales)
    print(f"rescale-on-skip events: {len(result.rescales)} | turnover "
          f"${rs_turn:.2f} | fees ${rs_fees:.2f} | positions dropped under "
          f"floor: {len(result.rescale_drops)}")
    print(f"execution: fills at the +{result.config.execution_delay_minutes}min "
          f"open"
          + (f" ({result.minute_fill_fallbacks} fell forward to a later minute)"
             if result.minute_fill_fallbacks else "")
          + ("   <-- 0 = the operationally impossible 00:00 fill"
             if result.config.execution_delay_minutes == 0 else ""))
    print(f"slippage assumed {result.config.slippage_bps_per_side:.1f} bps/side "
          f"-> cost ${result.total_slippage:.2f}"
          + ("   (5bps = plausible magnitude from n=1 synthetic testnet "
             "fill, NOT a measurement)"
             if result.config.slippage_bps_per_side else ""))
    n_est = sum(1 for *_, est in result.delistings if est)
    if result.daily_worst_equity:
        wmin_ts, wmin = min(result.daily_worst_equity, key=lambda x: x[1])
        frac = wmin / result.config.initial_capital
        print(f"intraday stress (H/L): min implied equity ${wmin:.2f} "
              f"({frac:.0%} of start) on {_ms_date(wmin_ts)}"
              + ("   <-- BELOW 25%: close-to-close results may describe a "
                 "path that never happened" if frac < 0.25 else ""))
    print(f"delistings: {len(result.delistings)}"
          + (f" ({n_est} settled at last mark, price estimated)" if n_est else "")
          + f" | data-gap forced exits: {len(result.data_gap_exits)}"
          + f" (tolerance {result.config.max_data_gap_days}d)")
    print(f"forced liquidations: {len(result.forced_liquidations)} | "
          f"missing funding settlements: {result.missing_funding_settlements}"
          + (f" (exposure-weighted "
             f"{result.funding_notional_missing / result.funding_notional_expected:.2%})"
             if result.funding_notional_expected > 0 else "")
          + ("  (rates absent from data; NOT zero-cost in reality)"
             if result.missing_funding_settlements else ""))
    if result.bankrupt:
        print("!! BANKRUPT: equity reached zero; run truncated")
    print(f"deflated Sharpe   {fmt(dsr)}  "
          f"(over {len(trial_srs)} distinct configs on this split"
          + ("; needs >=2 trials)" if len(trial_srs) < 2 else ")"))
    print("Who is paying, and why would they keep paying? — answer required "
          "before trusting this line (see NOTES.md).")
    if split == "holdout":
        yrs = split_years(split)
        resolvable = 2.0 / math.sqrt(yrs)  # 2 SE of an annualised Sharpe
        print(f"HOLDOUT CAVEAT: {yrs:.2f} years resolves only a true Sharpe "
              f"above ~{resolvable:.1f} (2 SE). The realistic 0.7-1.0 range is "
              f"BELOW what this holdout can confirm. A holdout Sharpe of 0.8 "
              f"means 'consistent with working, not confirmed' -- and that is "
              f"the best available outcome. Do not over-read this number.")
    if not math.isnan(sr) and sr > 1.5:
        print("!! Sharpe > 1.5: treat as a bug until a counterparty is "
              "identified. High Sharpe with no payer is the signature of "
              "lookahead, survivorship, or unrealistic fills.")


def check_holdout_guard(args: argparse.Namespace, cfg: Config) -> None:
    """Refuse the holdout unless this is genuinely the one look."""
    if HOLDOUT_LOG.exists():
        log = json.loads(HOLDOUT_LOG.read_text(encoding="utf-8"))
        if log.get("runs"):
            print("HOLDOUT ALREADY USED. The one look has been spent. "
                  "Prior result:")
            print(json.dumps(log["runs"], indent=2))
            sys.exit(1)
    if not args.i_understand_this_is_the_only_look:
        print("Refusing to run the holdout without "
              "--i-understand-this-is-the-only-look.\n"
              "This split can be looked at ONCE, ever. Be sure the config "
              "was chosen on train+validate alone.")
        sys.exit(1)
    # Record the look BEFORE running: a crashed look is still a look.
    HOLDOUT_LOG.write_text(
        json.dumps(
            {"runs": [{
                "ts": int(time.time()),
                "config": asdict(cfg),
                "config_hash": config_hash(cfg),
                "git_commit": git_state()[0],
                "status": "started",
            }]},
            indent=2,
        ),
        encoding="utf-8",
    )


def record_holdout_result(summary: dict) -> None:
    log = json.loads(HOLDOUT_LOG.read_text(encoding="utf-8"))
    log["runs"][-1].update(status="completed", result=summary)
    HOLDOUT_LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")


def assert_reportable_fee_mode(cfg: Config, purpose: str) -> None:
    """
    Stage 2e 4: no maker-mode research result may be reported until a
    fill-probability model exists.

    The backtester treats a maker fee as a guaranteed fill. The live harness
    correctly knows a post-only order may not fill at all -- and maker fills
    are not a random subset of intended orders: you fill when the market
    comes to you, which for a momentum entry means filling on the entries
    about to work least well. That adverse selection has no representation
    here, so a maker-mode number would be optimistic by an unknown amount.
    """
    if cfg.fee_mode == "maker" and not purpose.startswith("exploratory-nonreportable"):
        raise ValueError(
            "maker mode is not reportable (Stage 2e 4): the backtester assumes "
            "a guaranteed fill and has no fill-probability model. Re-run with "
            "--fee-mode taker, or pass --purpose exploratory-nonreportable-<why> "
            "to log an explicitly non-reportable exploratory run."
        )


def execute(store: PointInTimeStore, cfg: Config, split: str,
            purpose: str) -> BacktestResult:
    assert_reportable_fee_mode(cfg, purpose)
    start, end = split_view_range(split)
    try:
        result = run_backtest(store, cfg, start, end)
    except Exception as e:
        # An aborted execution still spends a trial.
        log_trial(cfg, split, purpose, {}, error=f"{type(e).__name__}: {e}")
        raise
    summary = summarise(result)
    log_trial(cfg, split, purpose, summary)
    if split == "holdout":
        record_holdout_result(summary)
    report(result, split)
    return result


def drift_decomposition(
    cfg: Config, real_db: Path, demeaned_db: Path, split: str = "train"
) -> dict:
    """
    Stage 2a §2.1: run the SAME config on the real store and on the
    per-symbol drift-demeaned copy. Sharpe_real − Sharpe_demeaned estimates
    the part of the edge that is drift-harvesting rather than
    trend-continuation. Attribution of an existing result, not a new
    configuration: nothing here touches trials.jsonl.
    """
    start, end = split_view_range(split)
    out: dict = {}
    for label, db in (("real", real_db), ("demeaned", demeaned_db)):
        store = PointInTimeStore(db, read_only=True)
        try:
            res = run_backtest(store, cfg, start, end)
        finally:
            store.close()
        out[label] = summarise(res)
        print(f"\n--- {label} ({db.name}) ---")
        report(res, split)

    sr_real = out["real"]["sharpe"]
    sr_dm = out["demeaned"]["sharpe"]
    drift = (sr_real - sr_dm) if (sr_real is not None and sr_dm is not None) else None
    frac = (drift / sr_real) if (drift is not None and sr_real) else None

    commit, dirty = git_state()
    rec = {
        "ts": int(time.time()),
        "kind": "drift_decomposition",
        "git_commit": commit,
        "dirty": dirty,
        "config_hash": config_hash(cfg),
        "config": asdict(cfg),
        "split": split,
        "sharpe_real": sr_real,
        "sharpe_demeaned": sr_dm,
        "drift_component": _r(drift) if drift is not None else None,
        "drift_fraction": _r(frac) if frac is not None else None,
        "real": out["real"],
        "demeaned": out["demeaned"],
        "note": DIAGNOSTIC_NOTE,
    }
    with open(DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")

    def f2(x):
        return "n/a" if x is None else f"{x:.2f}"

    print("\n=== drift / trend decomposition ===")
    print(f"Sharpe (real)      : {f2(sr_real)}")
    print(f"Sharpe (demeaned)  : {f2(sr_dm)}")
    print(f"Drift component    : {f2(drift)}  "
          + (f"({frac * 100:.0f}% of total)" if frac is not None else "(n/a)"))
    print(f"NOTE: {DIAGNOSTIC_NOTE}")
    if frac is not None and frac > 0.5:
        print("!! Majority of the Sharpe is drift-harvesting: closer to "
              "disguised beta than to momentum. State this plainly in results.")
    return rec


def print_grid_table(rows: list[tuple[Config, dict]], split: str) -> None:
    """Compact cross-config comparison printed after the grid. Every number
    is also in trials.jsonl; this is for reading, not for deciding."""
    def f(x, nd=2, pct=False):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return "n/a"
        return f"{x * 100:.{nd}f}%" if pct else f"{x:.{nd}f}"

    print(f"\n=== grid summary | {split} | {len(rows)} configs ===")
    print(f"{'lb':>3} {'skip':>4} {'sharpe':>7} {'ann_ret':>8} {'ann_vol':>8} "
          f"{'max_dd':>7} {'turn':>6} {'fee_drag':>9} {'lev_med':>7} {'<1.0x':>7} "
          f"{'bmn_skip':>9} {'n_rb':>5}")
    for cfg, s in rows:
        n_sched = max(s.get("n_scheduled", 0), 1)
        bmn = s.get("skips_by_reason", {}).get("below_min_notional", 0) / n_sched
        print(f"{cfg.lookback:>3} {cfg.skip:>4} {f(s['sharpe']):>7} "
              f"{f(s['ann_return'], pct=True):>8} {f(s['ann_vol'], pct=True):>8} "
              f"{f(s['max_dd'], pct=True):>7} {f(s['turnover'], 1):>6} "
              f"{f(s['fee_drag'], pct=True):>9} {f(s['leverage_median']):>7} "
              f"{f(s['leverage_frac_below_1'], 0, pct=True):>7} "
              f"{f(bmn, 1, pct=True):>9} {s['n_rebalances']:>5}")
    print("bmn_skip = below_min_notional skips as a share of scheduled rebalances; "
          "<1.0x = share of filled rebalances with realised gross leverage under 1.0")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="backtest.runner")
    p.add_argument("--db", default=str(ROOT / "xsmom.db"))
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="single backtest")
    # choices pin the CLI to the pre-registered grid; anything outside it is
    # a deliberate code change, not a flag away.
    r.add_argument("--split", choices=list(SPLITS), required=True)
    r.add_argument("--lookback", type=int, choices=GRID_LOOKBACKS,
                   required=True)
    r.add_argument("--skip", type=int, choices=GRID_SKIPS, required=True)
    r.add_argument("--fee-mode", choices=("taker", "maker"), default="taker")
    r.add_argument("--capital", type=float, default=DEFAULT_CAPITAL,
                   help=f"initial capital in USDT (default {DEFAULT_CAPITAL:.0f})")
    r.add_argument("--slippage-bps", type=float, default=0.0,
                   help="adverse slippage per side in bps (Stage 2c 4: run 0 and 5 as a pair)")
    r.add_argument("--execution-delay-minutes", type=int, default=None,
                   help="minutes after the daily open at which orders fill "
                        "(Stage 2e 2; default from Config, run 0 and 1 as a pair)")
    r.add_argument("--max-liquidity-rank", type=int, default=None,
                   help="exclude names below this point-in-time liquidity rank "
                        "from candidacy (Stage 3c Part B; default uncapped)")
    r.add_argument("--purpose", default="manual")
    r.add_argument("--i-understand-this-is-the-only-look",
                   action="store_true")

    g = sub.add_parser("grid", help="the pre-registered 6-point grid")
    g.add_argument("--split", choices=("train",), default="train",
                   help="the grid is a train-only exercise")
    g.add_argument("--fee-mode", choices=("taker", "maker"), default="taker")
    g.add_argument("--capital", type=float, default=DEFAULT_CAPITAL,
                   help=f"initial capital in USDT (default {DEFAULT_CAPITAL:.0f})")
    g.add_argument("--slippage-bps", type=float, default=0.0,
                   help="adverse slippage per side in bps (Stage 2c 4: run 0 and 5 as a pair)")

    d = sub.add_parser(
        "diagnose",
        help="drift/trend decomposition of one config on train (diagnostics.jsonl)",
    )
    d.add_argument("--lookback", type=int, choices=GRID_LOOKBACKS, required=True)
    d.add_argument("--skip", type=int, choices=GRID_SKIPS, required=True)
    d.add_argument("--fee-mode", choices=("taker", "maker"), default="taker")
    d.add_argument("--capital", type=float, default=DEFAULT_CAPITAL,
                   help=f"initial capital in USDT (default {DEFAULT_CAPITAL:.0f})")
    d.add_argument("--slippage-bps", type=float, default=0.0,
                   help="adverse slippage per side in bps (Stage 2c 4: run 0 and 5 as a pair)")
    d.add_argument("--demeaned-db", default=str(ROOT / "xsmom_demeaned.db"))

    args = p.parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        sys.exit(f"database not found: {db} — build it with build.py first")

    if args.cmd == "diagnose":
        dm = Path(args.demeaned_db)
        if not dm.exists():
            sys.exit(f"demeaned database not found: {dm} — build it with "
                     f"tools/build_demeaned_db.py first")
        cfg = Config(lookback=args.lookback, skip=args.skip,
                     fee_mode=args.fee_mode,
                         initial_capital=args.capital,
                         slippage_bps_per_side=args.slippage_bps)
        drift_decomposition(cfg, db, dm, split="train")
        return

    store = PointInTimeStore(db, read_only=True)
    try:
        if args.cmd == "run":
            extra = {}
            if args.execution_delay_minutes is not None:
                extra["execution_delay_minutes"] = args.execution_delay_minutes
            if args.max_liquidity_rank is not None:
                extra["max_liquidity_rank"] = args.max_liquidity_rank
            cfg = Config(lookback=args.lookback, skip=args.skip,
                         fee_mode=args.fee_mode,
                         initial_capital=args.capital,
                         slippage_bps_per_side=args.slippage_bps,
                         **extra)
            ensure_data_covers(store, args.split)  # before the look is spent
            if args.split == "holdout":
                check_holdout_guard(args, cfg)
            execute(store, cfg, args.split, args.purpose)
        elif args.cmd == "grid":
            ensure_data_covers(store, args.split)
            rows: list[tuple[Config, dict]] = []
            for lb in GRID_LOOKBACKS:
                for sk in GRID_SKIPS:
                    cfg = Config(lookback=lb, skip=sk,
                                 fee_mode=args.fee_mode,
                         initial_capital=args.capital,
                         slippage_bps_per_side=args.slippage_bps)
                    print(f"\n##### grid: lookback={lb} skip={sk} "
                          f"fee={args.fee_mode} #####")
                    res = execute(store, cfg, args.split, "grid")
                    rows.append((cfg, summarise(res)))
            print_grid_table(rows, args.split)
    finally:
        store.close()


if __name__ == "__main__":
    main()

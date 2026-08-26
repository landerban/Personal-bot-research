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
HOLDOUT_LOG = ROOT / "holdout_log.json"

# Pre-registered splits. Do not cross these.
SPLITS = {
    "train": ("2019-09-01", "2023-12-31"),
    "validate": ("2024-01-01", "2024-12-31"),
    "holdout": ("2025-01-01", "2026-08-31"),
}

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
        "missing_funding_settlements": result.missing_funding_settlements,
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
        if rec.get("split") != split or rec.get("error"):
            continue
        sr = rec.get("sharpe")
        if sr is None:
            continue
        latest[rec["config_hash"]] = sr / math.sqrt(metrics.ANN)
    return list(latest.values())


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
          f"{len(result.rebalances)} rebalances ===")
    print(f"ann return        {fmt(aret, pct=True)}")
    print(f"ann vol           {fmt(vol, pct=True)}")
    print(f"sharpe            {fmt(sr)}")
    print(f"max drawdown      {fmt(mdd, pct=True)}   "
          f"(expected ~ sigma/2S = {fmt(metrics.expected_max_dd(vol, sr), pct=True)})")
    print(f"turnover (ann, x capital)  "
          f"{fmt(metrics.turnover_annualised(result.total_turnover, eq))}")
    print(f"FEE DRAG (% of gross PnL)  "
          f"{fmt(metrics.fee_drag(result.total_fees, result.gross_pnl), pct=True)}")
    print(f"  gross PnL {result.gross_pnl:+.2f} | fees {result.total_fees:.2f}"
          f" | funding {result.total_funding:+.2f}")
    print(f"hit rate          {fmt(metrics.hit_rate(rets), pct=True)}   "
          f"avg win {fmt(aw, pct=True, nd=3)} vs avg loss {fmt(al, pct=True, nd=3)}")
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
    print(f"forced liquidations: {len(result.forced_liquidations)} | "
          f"missing funding settlements: {result.missing_funding_settlements}"
          + ("  (rates absent from data; NOT zero-cost in reality)"
             if result.missing_funding_settlements else ""))
    if result.bankrupt:
        print("!! BANKRUPT: equity reached zero; run truncated")
    print(f"deflated Sharpe   {fmt(dsr)}  "
          f"(over {len(trial_srs)} distinct configs on this split"
          + ("; needs >=2 trials)" if len(trial_srs) < 2 else ")"))
    print("Who is paying, and why would they keep paying? — answer required "
          "before trusting this line (see NOTES.md).")
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


def execute(store: PointInTimeStore, cfg: Config, split: str,
            purpose: str) -> BacktestResult:
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
    r.add_argument("--purpose", default="manual")
    r.add_argument("--i-understand-this-is-the-only-look",
                   action="store_true")

    g = sub.add_parser("grid", help="the pre-registered 6-point grid")
    g.add_argument("--split", choices=("train",), default="train",
                   help="the grid is a train-only exercise")
    g.add_argument("--fee-mode", choices=("taker", "maker"), default="taker")

    args = p.parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        sys.exit(f"database not found: {db} — build it with build.py first")
    store = PointInTimeStore(db, read_only=True)

    try:
        if args.cmd == "run":
            cfg = Config(lookback=args.lookback, skip=args.skip,
                         fee_mode=args.fee_mode)
            if args.split == "holdout":
                check_holdout_guard(args, cfg)
            execute(store, cfg, args.split, args.purpose)
        elif args.cmd == "grid":
            for lb in GRID_LOOKBACKS:
                for sk in GRID_SKIPS:
                    cfg = Config(lookback=lb, skip=sk,
                                 fee_mode=args.fee_mode)
                    print(f"\n##### grid: lookback={lb} skip={sk} "
                          f"fee={args.fee_mode} #####")
                    execute(store, cfg, args.split, "grid")
    finally:
        store.close()


if __name__ == "__main__":
    main()

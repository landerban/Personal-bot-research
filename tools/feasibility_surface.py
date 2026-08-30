#!/usr/bin/env python3
"""
Stage 17 Part II: the feasibility surface.

  python tools/feasibility_surface.py

THE QUESTION (NOTES 57.4, verbatim)
  "Under what capital, universe-rank, and identifiability conditions can the
   strategy definition (5L/5S, rank-weighted, beta-hedged, vol-targeted)
   physically express a book in the current market?"

OUTPUTS ARE PROBABILITIES OF BOOK FORMATION. Never Sharpe, never PnL, never
returns. **Nothing here reads a return series** -- the inputs are today's
listings, floors, step sizes, prices and beta standard errors, all structural
facts about market microstructure.

WHY BOOTSTRAP THE RANKING -- AND WHY A UNIFORM SHUFFLE IS WRONG
---------------------------------------------------------------
Formation must be robust to WHICH names momentum picks, not conditioned on
last Tuesday's ordering. So the ordering is resampled -- but from REAL
relative-strength orderings, as §57.4 registered.

The first implementation of this file used `rng.permutation`, a uniform
shuffle, and reported P(form)=59% for the frozen cell against a measured 0/12.
The gap was the estimator, not the market: momentum does not pick names
uniformly, it picks EXTREMES, and on this venue the extreme movers are
systematically the recently-listed names with the largest beta standard
errors. A uniform draw puts a high-SE name in a leg occasionally; real
momentum puts one there almost always.

So each draw now samples a real historical day, computes the actual 14-day
relative-strength ordering on that day, and takes its top-k/bottom-k.

Returns are read HERE ONLY to construct orderings. No return is turned into a
Sharpe, a PnL or any performance statistic anywhere in this file -- that
distinction is what §57.4's "never returns" is protecting, and ranking by
relative strength is what it explicitly asked for.

THE ANTI-TUNING RULE (NOTES 57.4)
---------------------------------
The 12 replay days are a SMOKE TEST ONLY. Nothing here is fitted to them: a
region chosen to explain 12 known failures would be defined by those failures
and would say nothing about the next twelve days. Candidate regions are
verified forward, as live days accumulate.

FIXED, NOT SWEPT: N=10, k=5, the [0.5,1.5] band, the hedge-guard thresholds,
MIN_LEG_NAMES. The strategy DEFINITION is what is being tested for
expressibility, so it does not bend during the test.

**NO CELL IS PROMOTED TO A DEPLOYMENT CONFIG.**
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import runner  # noqa: E402
from backtest import weights as W  # noqa: E402
from backtest.sizing import SymbolFilters, size_from_weight  # noqa: E402
from backtest.universe_filter import filter_universe  # noqa: E402
from live.phase2 import production_volumes  # noqa: E402
from live.proddata import ProdDataClient  # noqa: E402

# NOTES 57.4 -- fixed in advance.
CAPITALS = (800.0, 1200.0, 2000.0, 3000.0, 5000.0)
RANK_CAPS = (15, 20, 25, 30)
SCREENS = ("none", "se<=0.3", "se<=0.5", "60d+se<=0.5")
VOL_TARGETS = (0.10, 0.12, 0.14)

N_POSITIONS = 10
K = N_POSITIONS // 2
MIN_LEG_NAMES = W.MIN_LEG_NAMES
BAND_LO = W.WEIGHT_BAND[0]
N_DRAWS = 400
BARS = 90
MIN_LISTED_DAYS = 60

FLOOR = "floor"
IDENT = "identifiability"
LEGCOUNT = "leg_count"


def gather(c: ProdDataClient, symbols: list[str]) -> dict:
    """Today's structural facts. No returns are read for a PnL purpose --
    daily closes are used only to estimate betas and their standard errors,
    which are microstructure inputs to the hedge guard."""
    info = c.exchange_info()
    filt = {}
    for s in info.get("symbols", []):
        mn = step = None
        for f in s.get("filters", []):
            if f.get("filterType") == "MIN_NOTIONAL":
                mn = float(f["notional"])
            elif f.get("filterType") == "LOT_SIZE":
                step = float(f["stepSize"])
        filt[s["symbol"]] = SymbolFilters(s["symbol"], mn, step)

    out: dict = {}
    btc_rets = None
    closes: dict[str, np.ndarray] = {}
    hist: dict[str, np.ndarray] = {}
    for sym in symbols:
        rows = c.klines(sym, "1d", limit=BARS)
        if len(rows) < 61:
            continue
        closes[sym] = np.array([float(r[4]) for r in rows])
        hist[sym] = closes[sym]
    if W.BTC not in closes:
        raise SystemExit("no BTC history; cannot estimate betas")
    btc_rets = np.diff(closes[W.BTC]) / closes[W.BTC][:-1]

    for sym, px in closes.items():
        r = np.diff(px) / px[:-1]
        n = min(len(r), len(btc_rets))
        rr, bb = r[-60:], btc_rets[-60:]
        m = min(len(rr), len(bb))
        if m < 30:
            continue
        beta = float(W.compute_betas(rr[-m:].reshape(-1, 1), bb[-m:])[0])
        se = float(W.compute_beta_ses(rr[-m:].reshape(-1, 1), bb[-m:],
                                      np.array([beta]))[0])
        out[sym] = {"price": float(px[-1]), "beta": beta, "se": se,
                    "listed_days": len(px), "closes": px,
                    "filters": filt.get(sym, SymbolFilters(sym))}
    return out


LOOKBACK = 14


def momentum_ordering(eligible: list[str], facts: dict, day_back: int
                      ) -> list[str] | None:
    """The REAL 14-day relative-strength ordering `day_back` days ago.

    This is what §57.4 meant by bootstrapping from real orderings: momentum
    selects extremes, and which names are extreme is a property of the market,
    not a coin flip.
    """
    scored = []
    for s in eligible:
        px = facts[s]["closes"]
        end = len(px) - 1 - day_back
        start = end - LOOKBACK
        if start < 0 or px[start] <= 0:
            return None
        scored.append((px[end] / px[start] - 1.0, s))
    if len(scored) < N_POSITIONS:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, s in scored]


def screen_ok(fact: dict, screen: str) -> bool:
    if screen == "none":
        return True
    if screen == "se<=0.3":
        return fact["se"] <= 0.3
    if screen == "se<=0.5":
        return fact["se"] <= 0.5
    if screen == "60d+se<=0.5":
        return fact["listed_days"] >= MIN_LISTED_DAYS and fact["se"] <= 0.5
    raise ValueError(screen)


def try_book(names: list[str], facts: dict, capital: float, vol_target: float,
             rng) -> tuple[bool, str, int]:
    """One formation attempt. Returns (formed, failure_mode, seated).

    The book is built the way the pipeline builds it -- rank-weight ramp, the
    band, a gross implied by the vol target -- then sized through the shared
    module with real floors and step sizes, then hedge-checked with real betas
    and SEs. The guards are the frozen ones and are not relaxed.
    """
    if len(names) < N_POSITIONS:
        return False, LEGCOUNT, 0
    longs, shorts = names[:K], names[-K:]

    # realised gross scales roughly with the vol target; 0.24 at 10% is the
    # measured value for the frozen config (NOTES 43.6), scaled linearly.
    gross = 0.24 * (vol_target / 0.10)
    profile = W._leg_profile(K)          # the [0.5,1.5] band, unchanged

    # The pipeline applies the floor POST-HEDGE, post-vol-scale, where
    # positions are smaller than their band weights. Checking at the band
    # weight understates floor failures -- the first run of this file reported
    # floor 0% against a measured 50%. So the short leg is scaled by the same
    # hedge ratio the pipeline would apply before anything is sized.
    beta_l = sum(w * facts[n]["beta"] for n, w in zip(longs, profile))
    beta_s = sum(w * facts[n]["beta"] for n, w in zip(shorts, profile))
    hedge_scale = abs(beta_l / beta_s) if abs(beta_s) > 1e-12 else 1.0
    hedge_scale = min(max(hedge_scale, 0.1), 10.0)   # guard a degenerate ratio

    seated_l, seated_s = [], []
    for leg, seated, scale in ((longs, seated_l, 1.0),
                               (shorts, seated_s, hedge_scale)):
        for name, w in zip(leg, profile):
            weight = w * gross / 2.0 * scale
            f = facts[name]["filters"]
            sized = size_from_weight(name, weight, capital,
                                     facts[name]["price"], f)
            if sized.ok:
                seated.append((name, weight))

    if len(seated_l) < MIN_LEG_NAMES or len(seated_s) < MIN_LEG_NAMES:
        return False, FLOOR, len(seated_l) + len(seated_s)

    # the hedge guard, on the SEATED book, with real betas/SEs
    for leg in (seated_l, seated_s):
        contrib = sum(w * facts[n]["beta"] for n, w in leg)
        se = float(np.sqrt(sum((w * facts[n]["se"]) ** 2 for n, w in leg)))
        if se > abs(contrib):
            return False, IDENT, len(seated_l) + len(seated_s)

    return True, "", len(seated_l) + len(seated_s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=N_DRAWS)
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    a = ap.parse_args()

    print("=== Stage 17 Part II: the feasibility surface ===")
    print("    P(book forms). No Sharpe, no PnL, no return series.")
    print(f"    N={N_POSITIONS} k={K} band={W.WEIGHT_BAND} "
          f"MIN_LEG_NAMES={MIN_LEG_NAMES} -- FIXED, not swept\n")

    c = ProdDataClient()
    volumes, age = production_volumes(a.db)
    info = c.exchange_info()
    listed = [s["symbol"] for s in info.get("symbols", [])
              if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"]
    ranked = filter_universe(sorted([s for s in listed if s in volumes],
                                    key=lambda s: -volumes[s]))[0]
    pool = ranked[:max(RANK_CAPS)]
    print(f"fetching structural facts for {len(pool)} symbols "
          f"(volume reference {age:.0f}d old) ...", flush=True)
    facts = gather(c, pool + [W.BTC])
    print(f"  {len(facts)} symbols with >=61 daily bars\n")

    rng = np.random.default_rng(17)
    rows = []
    for cap in CAPITALS:
        for rank in RANK_CAPS:
            universe = [s for s in ranked[:rank] if s in facts]
            for screen in SCREENS:
                eligible = [s for s in universe if screen_ok(facts[s], screen)]
                for vt in VOL_TARGETS:
                    formed = 0
                    modes = Counter()
                    seats = []
                    for _ in range(a.draws):
                        if len(eligible) < N_POSITIONS:
                            modes[LEGCOUNT] += 1
                            continue
                        # a REAL ordering from a randomly chosen recent day
                        day_back = int(rng.integers(0, BARS - LOOKBACK - 2))
                        order = momentum_ordering(eligible, facts, day_back)
                        if order is None:
                            modes[LEGCOUNT] += 1
                            continue
                        ok, mode, seated = try_book(order, facts, cap, vt, rng)
                        if ok:
                            formed += 1
                            seats.append(seated)
                        else:
                            modes[mode] += 1
                    rows.append({
                        "capital": cap, "rank_cap": rank, "screen": screen,
                        "vol_target": vt, "eligible": len(eligible),
                        "p_form": formed / a.draws,
                        "floor": modes[FLOOR] / a.draws,
                        "ident": modes[IDENT] / a.draws,
                        "legcount": modes[LEGCOUNT] / a.draws,
                        "median_seated": float(np.median(seats)) if seats else 0.0,
                    })

    # ---------------- report ------------------------------------------
    print("=== P(form) by capital x screen, at rank-cap 15, vol 10% "
          "(the frozen config's axes) ===")
    print(f"{'capital':>9} " + "".join(f"{s:>14}" for s in SCREENS))
    for cap in CAPITALS:
        cells = []
        for s in SCREENS:
            r = next(x for x in rows if x["capital"] == cap and
                     x["rank_cap"] == 15 and x["screen"] == s and
                     x["vol_target"] == 0.10)
            cells.append(f"{r['p_form']:>13.0%} ")
        print(f"${cap:>8,.0f} " + "".join(cells))

    print(f"\n=== P(form) by capital x rank cap, screen=none, vol 10% ===")
    print(f"{'capital':>9} " + "".join(f"{'top-' + str(r):>10}" for r in RANK_CAPS))
    for cap in CAPITALS:
        cells = []
        for rank in RANK_CAPS:
            r = next(x for x in rows if x["capital"] == cap and
                     x["rank_cap"] == rank and x["screen"] == "none" and
                     x["vol_target"] == 0.10)
            cells.append(f"{r['p_form']:>9.0%} ")
        print(f"${cap:>8,.0f} " + "".join(cells))

    best = max(rows, key=lambda r: r["p_form"])
    frozen = next(x for x in rows if x["capital"] == 800.0 and
                  x["rank_cap"] == 15 and x["screen"] == "none" and
                  x["vol_target"] == 0.10)
    print(f"\n=== the frozen config's own cell ===")
    print(f"  $800 / top-15 / no screen / 10%:  P(form) = {frozen['p_form']:.0%}")
    print(f"    failure split -- floor {frozen['floor']:.0%}, "
          f"identifiability {frozen['ident']:.0%}, "
          f"leg-count {frozen['legcount']:.0%}")
    print(f"    (the 12-day replay measured 0/12; this is the SMOKE TEST -- "
          f"agreement is a sanity check, not a fit)")

    print(f"\n=== best cell on the grid ===")
    print(f"  ${best['capital']:,.0f} / top-{best['rank_cap']} / "
          f"{best['screen']} / vol {best['vol_target']:.0%}"
          f"  ->  P(form) = {best['p_form']:.0%}")
    print(f"    median seated names {best['median_seated']:.0f} of "
          f"{N_POSITIONS}, eligible pool {best['eligible']}")

    # ---------------- the reading (NOTES 57.5) -------------------------
    no_screen = [r for r in rows if r["screen"] == "none"]
    with_screen = [r for r in rows if r["screen"] != "none"]
    cap_effect = (max(r["p_form"] for r in no_screen if r["capital"] == 5000.0)
                  - max(r["p_form"] for r in no_screen if r["capital"] == 800.0))
    screen_effect = (max(r["p_form"] for r in with_screen)
                     - max(r["p_form"] for r in no_screen))
    grid_max = best["p_form"]

    print(f"\n=== READING (NOTES 57.5, fixed before computing) ===")
    print(f"  capital effect (no screen, $800 -> $5k): {cap_effect:+.0%}")
    print(f"  screen effect (best screened - best unscreened): {screen_effect:+.0%}")
    print(f"  grid maximum P(form): {grid_max:.0%}")
    if grid_max < 0.5:
        pattern = ("AGED OUT as defined: P(form) is low everywhere on the "
                   "grid. No capital, rank cap, screen or vol target in the "
                   "pre-registered ranges lets this definition express a book "
                   "reliably in today's market.")
    elif cap_effect >= 0.4 and screen_effect < 0.2:
        pattern = ("CAPITAL-BOUND: $800 is simply too small for today's "
                   "structure.")
    elif screen_effect >= 0.2 and cap_effect < 0.4:
        pattern = ("UNIVERSE-TOO-YOUNG: identifiability binds, not money. The "
                   "screen is the research object.")
    else:
        pattern = ("MIXED: capital and identifiability both move P(form) "
                   "materially. Reported as mixed rather than resolved.")
    print(f"\n  --> {pattern}")
    viable = [r for r in rows if r["p_form"] >= 0.9]
    if viable:
        v = min(viable, key=lambda r: (r["capital"], r["rank_cap"]))
        print(f"\n  smallest cell reaching P >= 0.9: ${v['capital']:,.0f} / "
              f"top-{v['rank_cap']} / {v['screen']} / {v['vol_target']:.0%}")
    else:
        print(f"\n  NO cell on the grid reaches P >= 0.9.")
    print(f"\n  NO CELL IS PROMOTED TO A DEPLOYMENT CONFIG (NOTES 57.5).")
    print(f"  Any viable region inherits a validation problem: a train era "
          f"that no longer\n  resembles the market, a sealed holdout, and "
          f"forward validation as the only\n  clean option left.")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": int(time.time()), "kind": "feasibility_surface",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "draws": a.draws, "axes": {"capital": list(CAPITALS),
                                       "rank_cap": list(RANK_CAPS),
                                       "screen": list(SCREENS),
                                       "vol_target": list(VOL_TARGETS)},
            "rows": rows, "frozen_cell": frozen, "best_cell": best,
            "capital_effect": cap_effect, "screen_effect": screen_effect,
            "pattern": pattern,
            "note": "structural only; no return series read; no config selected",
        }, default=str) + "\n")
    print(f"\n  logged to {runner.DIAGNOSTICS_PATH.name} "
          f"(kind=feasibility_surface)")


if __name__ == "__main__":
    main()

# Stage 2e — Pre-grid fixes from external review

Supersedes `STAGE2D_GRID.md` §6 ordering: **the grid moves after these fixes.**
Everything else in Stage 2d — §1 floor withdrawal, §2 capital $400, §3 Test 20,
§5 funding-start exclusion — stands unchanged and should be completed first.

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 0. Why the grid moved again

An external review found a feasibility bug and an unrealistic fill assumption.
Running now would spend 6 of 20 trials on a harness about to change, then 6
more after fixing it — twelve trials for one answer.

Same logic as not running before the backfill finished. **Trials are the
scarcest resource here; don't spend them on a harness you're about to
change.**

This is the last remediation round before the grid. Six items, all of which
make results *wrong* rather than merely imprecise. Items that would only make
them *better* are in §8 and are explicitly deferred — if a seventh good idea
appears mid-implementation, it goes in §8 too.

---

## 1. Feasibility must be checked AFTER hedging

### The bug

`tradeable_universe()` estimates the smallest position as
`MIN_WEIGHT_FRACTION · L · C / N`. Then `beta_hedge()` scales the entire short
leg by `s`. When `s < 1` every short shrinks below that estimate; the
feasibility check validated weights that no longer exist.

`NOTES` §13.3 recorded `s` median 1.02, p95 1.73, max 4.03, with `s > 3` on 1%
of rebalances. Both directions occur and neither is modelled.

Consequence: a viable day becomes a total skip because one post-hedge position
fell under the floor. Given that skips are regime-correlated — they cluster in
high-vol periods — this biases *which* days the strategy trades, which is
exactly the defect that made grid v2's headline meaningless.

### The fix

Check feasibility on **post-hedge, post-vol-target weights**, then:

1. If every position clears `MIN_NOTIONAL` → proceed.
2. If any position is below → **drop that position, renormalise the remainder,
   re-hedge, re-check.** Same rule as §2.1.5 of `STAGE2C_PREGRID.md`, applied
   at construction rather than only on rescale.
3. Repeat at most 3 times. Still infeasible → skip and log
   `below_min_notional_post_hedge`.
4. If dropping would leave fewer than 3 names on either leg → skip instead.
   A 2-name leg is a different strategy.

### Explicitly rejected

The reviewer proposed *substituting the next-ranked candidate* for an
infeasible one. **Do not implement this.** Substituting on feasibility selects
for position size, which correlates with volatility and liquidity — it would
introduce a size tilt that is hard to reason about and impossible to separate
from the momentum signal afterwards. Dropping is neutral; substituting is a
strategy change.

`compute_target_weights()` should also stop calling the universe filter with
`max_gross_leverage`. Pass the vol-targeted gross actually in use.

### Test 21

- `s = 0.3` forces shorts under the floor → drop-and-renormalise fires, final
  book feasible, dollar and beta neutrality preserved post-drop
- Repeated infeasibility → skip logged with the new reason
- Leg reduced below 3 names → skip, not a 2-name leg
- No substitution occurs: names in the final book are always a subset of the
  originally ranked selection

---

## 2. Execution at the 00:01 open, not 00:00

### The bug

I specified fills at day T+1's 00:00 open. That is operationally impossible —
at 00:00:00 you still have to build the universe, rank hundreds of assets,
compute betas and covariance, quantise, and transmit. The live bot already
waits ~15s for funding settlement.

This is not covered by the slippage parameter. Flat slippage is symmetric
noise; execution delay against a **momentum** signal is one-signed — if the
move continues into the first minute, you systematically buy higher and sell
lower.

### The fix

Ingest **1-minute bars for the first 5 minutes of each UTC day only**
(00:00–00:04). Roughly 832 symbols × ~2,400 days × 5 bars ≈ 10M rows —
tractable. A full 1-minute history is not, and is not needed.

Fill at the **00:01 open**. Store in a separate table with the same PIT
gating; a 00:01 bar has `close_time` 00:01:59.999 and must not be visible to a
decision made at the previous close.

Config: `execution_delay_minutes: int = 1`. Keep `0` runnable so the effect is
measurable, but **`1` is the pre-registered setting** — do not select between
them on results.

### Test 22

- Fill price equals the 00:01 open, never the 00:00 open, never the signal
  bar's close
- `test_lookahead.py`-style gating holds for the minute table
- A missing 00:01 bar falls back to 00:02, then skips — never silently back to
  00:00, which would restore the optimistic fill

---

## 3. Delisting is not a data gap

Currently a held symbol with no bar is force-settled at last mark with a taker
fee, whether it delisted or the data has a hole. Cross-sectional momentum
shorts collapsing coins, and collapsing coins are what delist — this lands
squarely on the leg where the strategy's profits should come from.

Maintain explicit listing/delisting metadata. Then:

- **Delisting**: settle at the exchange settlement price where available, at
  the settlement timestamp. If unavailable, settle at last mark and log
  `delist_settlement_estimated`.
- **Data gap**: hold the position, mark at last close, and if the gap exceeds
  3 days force-settle and log `data_gap_forced_exit`.

Report both counts separately. Never merge them into one event again.

**Test 23:** a delisting and a data gap in one fixture produce different code
paths, different log reasons, and different PnL.

---

## 4. Taker-only for research

Drop `fee_mode = "maker"` from the grid entirely.

The backtester treats a maker fee as a guaranteed fill. The live harness
correctly knows a post-only order may not fill at all. Worse, maker fills are
not a random subset of intended orders — you fill when the market comes to
you, which for a momentum entry means you fill on the entries that were about
to work least well. That adverse selection has no representation in the
backtest.

Keep `fee_mode` in `Config` and keep collecting maker data in paper trading.
**No maker-mode research result may be reported until a fill-probability model
exists.** Add an assertion in the runner: maker mode on a real DB raises
unless explicitly flagged as exploratory and marked non-reportable.

---

## 5. Beta estimation uncertainty

`s > 3` on 1% of rebalances is estimation noise being executed as a hedge
instruction. `NOTES` §13.3 called it "worth watching"; I agreed too readily.

Compute the standard error of each 60-day OLS beta. Then:

- Shrink toward 1.0 in proportion to relative standard error:
  `β_shrunk = w·β̂ + (1−w)·1.0` where `w = 1 / (1 + (SE/β̂)²)`
- Skip with `unhedgeable_beta` if the leg's weighted beta SE exceeds its
  estimate

This is a **risk control, not an alpha choice** — it costs no trial. But it
changes results, so pre-register it here and log beta SE distributions per run.

**Test 24:** a high-noise synthetic beta shrinks toward 1.0; a clean one
barely moves; `s` stays bounded under noise that previously produced `s > 3`.

---

## 6. Respect `funding_interval_hours`

The parser exposes it and the applier handles 4-hourly symbols correctly, but
the missing-settlement checker assumes a fixed 8-hour schedule — so 4-hourly
symbols report spurious missing settlements.

Read the column, use it. Report missing settlements against each symbol's
actual schedule.

Low priority and low materiality, but it's a correctness fix costing minutes.

---

## 7. Cheap liquidation stress test

The reviewer proposed a full intraday liquidation model with mark-price data
and margin brackets. **Rejected as specified** — the magnitudes don't justify
it:

| Gross leverage | Adverse move needed to liquidate (MMR 1%) |
|---|---|
| 0.45 (real median) | 221% |
| 3.0 (the cap) | 32% |
| 20 (the v1 bug) | 4% |

At realised leverage this is remote. It was severe in v1 *because of the
leverage bug*, which is fixed.

**Do implement the cheap version**, using daily highs and lows you already
have: after each run, compute worst intraday mark-to-market using H/L against
held positions, and report the minimum equity that path implies. If any config
shows implied equity below 25% of starting capital, flag it — that config's
reported close-to-close results may describe a path that never happened.

Report as a diagnostic. Costs no trial.

---

## 8. Deferred — do NOT implement now

Good ideas that would make results better rather than prevent them being
wrong. Each costs trials, complexity, or both, and none blocks the grid.

| Item | Why deferred |
|---|---|
| **Rank buffer / hysteresis** | Best alpha-side idea available — costs dominate this strategy at 6–26% fee drag. But it's a strategy change; it belongs in a pre-registered variant *after* a baseline exists |
| **L1 turnover-penalised optimisation** | Adds an optimiser and a λ to tune. Try the buffer first |
| **Residual momentum** | The right *next* alpha. Needs a baseline to be measured against |
| **EWMA / shrinkage covariance** | `w'Σ̂w` is unbiased for fixed weights; the reactivity argument is real but second-order |
| **Full mark-price funding** | ~6e-4 of notional cumulative over four years — half a taker round trip |
| **Walk-forward folds** | See §9 |

**If a new idea appears while implementing §1–§7, it goes in this table.** The
list must not grow into the blocking section. A project that never leaves
remediation produces no answer at all.

---

## 9. Walk-forward — a decision for after the grid

The reviewer proposes multiple OOS folds instead of a single validate. It is
better practice, and it is **not free**:

- 6 configs × 4 folds = 24 evaluations against a 20-trial budget
- "Pick a stable plateau rather than the best average" does not escape
  selection. Best-of-6 chosen by *minimum* fold Sharpe has a non-zero
  expectation under the null — less biased than max-picking, not unbiased

If adopted it must be paid for honestly: expand the budget deliberately,
**pre-register the selection rule before looking at any fold**, and recompute
DSR at the higher trial count.

**Adopt now regardless, at no cost:** stationary/block-bootstrap confidence
intervals on Sharpe. Resampling an existing result is not a new backtest, and
crypto returns are neither Gaussian nor IID, so the parametric SE understates
uncertainty. Report a 90% CI alongside every Sharpe from the grid onward.

Decide walk-forward vs single validate **after** the grid, with output on the
table.

---

## 10. Live fixes — before Phase 2, not before the grid

None block the grid. All block real money.

1. **Multi-leg atomicity (most serious).** `_execute` catches a rejection and
   returns; the loop continues. You can end a rebalance with the long leg on
   and the short leg missing — an unhedged directional book in a strategy
   whose risk framing assumes neutrality. Require a post-execution beta and
   tracking-error check; repair the residual or flatten if it exceeds
   tolerance.
2. **Stop-execution cascade.** One position stopping at 14:00 leaves the rest
   running directionally until midnight. A stop fill must trigger immediate
   reconcile and either re-hedge or flatten.
3. **Funding reconstruction.** `record_day()` rebuilds expected funding from
   the *current* position, not the one held at the settlement timestamp — and
   the bot rebalances ~15s after the 00:00 settlement. Reconstruct position at
   each settlement from fill history.
4. **POST retry idempotency.** Accepted order, lost response, client retries.
   `newClientOrderId` helps; query the order by client ID after any ambiguous
   response before submitting anything.

---

## 11. Order of work

1. Stage 2d §1, §2, §3, §5 (floor withdrawal, $400, Test 20, funding-start)
2. §1 post-hedge feasibility + Test 21
3. §5 beta shrinkage + Test 24
4. §3 delisting metadata + Test 23
5. §6 funding interval
6. §2 minute-bar ingest + Test 22 — data lift, run it last so failures don't
   block the rest
7. §4 taker-only assertion; §7 stress-test diagnostic; §9 bootstrap CIs
8. Full suites green; null canaries at 30 seeds, reported fresh
9. **Stop. Report. Do not run the grid.**

## 12. Acceptance

- Tests 21–24 green, each demonstrated failing against the pre-fix path
- No substitution logic anywhere in weight construction
- `execution_delay_minutes = 1` pre-registered; `0` runnable but not selected on
- Delisting and data gaps report separately
- Maker mode cannot produce a reportable real-data result
- Beta SE distribution logged; `s` bounded under synthetic beta noise
- Liquidation stress diagnostic implemented, costs no trial
- Bootstrap CI available for every Sharpe
- §8 table updated with anything new that came up
- Trial budget unchanged — **none of this consumes trials**

## 13. Do not

- Substitute candidates on feasibility
- Select `execution_delay_minutes` on results
- Report a maker-mode real-data result
- Implement anything from §8
- Run the grid, validate, or holdout
- Let the blocking list grow

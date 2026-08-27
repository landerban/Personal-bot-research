# Stage 2c — Pre-grid remediation

Follows review of `TEST_NOTES.md` (commit `fa99ffa`) and the first real-data
train grid. Amends `STAGE2_PROMPT.md`, `STAGE2A_REMEDIATION.md`, and
`STAGE2B_CORRECTIONS_AND_PAPER.md`. **Where this conflicts with them, this
wins.**

§0 of `STAGE2_PROMPT.md` remains in force.

**Nothing in this document may be decided by running it on real data.** Every
choice below is settled by arithmetic and written down *before* the re-run.
Choosing between two options by comparing their real-data results is a
data-dependent selection and would contaminate the grid.

---

## 1. The invariant hole (root cause)

The first train grid drove leverage past 20× and bankrupted four configs,
while 24 tests stayed green. The reason:

```python
for rb in res.rebalances:            # filled days only
    assert L <= CFG.max_gross_leverage
```

Skipped days are in `res.skips`, not `res.rebalances`. **The cap was asserted
only on days you traded, and the failure occurred on days you didn't.**

This is my specification error, not an implementation error. The same shape
explains why `below_min_notional` has never fired in testing: both are
invariants checked at the wrong moment.

### 1.1 Required: Test 16 — leverage cap holds every day

Assert on the **daily equity/exposure trace**, not on the rebalance list:

```
for every day d in the run (filled, skipped, or held):
    gross_notional(d) / equity(d) <= max_gross_leverage + 1e-9
```

Add `daily_leverage: list[tuple[int, float]]` to the result object if the
trace isn't already exposed.

**Verification requirement:** before applying the §2 fix, run Test 16 against
the pre-fix code path and confirm it **fails**. A regression test that cannot
reproduce the bug it guards proves nothing. Record the observed peak leverage
in `NOTES.md`. Then apply the fix and confirm it passes.

### 1.2 Required: audit every other invariant for the same hole

Go through each existing test and ask: *is this asserted at a moment, or over
the whole run?* Anything asserted only at fill time needs the same treatment.
Known candidates:

- `below_min_notional` — never fires; the $50-floor fixture logs
  `universe_too_small` instead, so the sizing-below-floor path is untested
- `dollar_neutrality` and `dollar_tilt_is_the_hedge` — checked at rebalance;
  do they hold while the book is held across a skip?
- `beta_neutrality` — realised beta is measured over the run, so probably
  fine, but confirm skips don't leave an unhedged book drifting

Report findings in `NOTES.md` §14. Add tests where a hole exists.

### 1.3 Required: a fixture where the floor actually binds

`below_min_notional` must fire. Build a synthetic store with capital,
`N`, and `MIN_NOTIONAL` set so the universe is viable but position sizing
falls under the floor. Assert the skip is logged with that exact reason.

---

## 2. RULING: rescale on skip, not flatten

`STAGE2B` shipped flatten-on-skip. **Change it to rescale-on-skip.**

### Reasoning (this is the pre-registration — do not re-litigate on data)

The bug was never "holding." It was **holding without rescaling**.
Vol-targeting sets gross from equity at decision time; skip the decision and
the ratio drifts as equity moves. Both remedies stop the drift. They differ
in cost:

- **Flatten** exits the entire book and pays a full round trip. At the
  observed ~12% skip rate that is a round trip on 1 in 8 rebalances. Your own
  random-signal canary showed costs moving gross +0.14 to net −1.27 — cost
  terms dominate this strategy, and flatten-on-skip could plausibly cost more
  Sharpe than the bug it fixes.
- **Rescale** multiplies every existing position by a single scalar to
  restore the vol target. It trades only the delta, so cost scales with how
  far leverage drifted rather than with book size.

Rescaling is available regardless of *why* the skip fired: shrinking existing
positions proportionally needs no new symbols and no viable universe. It works
even under `universe_too_small`, which is the most common skip reason.

It is also closer to the strategy's intent. A skip means "I cannot re-rank
today," not "I want to be flat."

### 2.1 Specification

On any skip:

1. Compute current gross leverage from the held book and current equity.
2. Recompute the vol target from data available at `as_of` (same estimator as
   a normal rebalance).
3. Apply a single scalar to every position to restore target gross, subject
   to `max_gross_leverage` and the §3 floor.
4. Execute the resulting deltas at the next bar's open, with normal fees.
5. If any resulting position falls below `MIN_NOTIONAL`, close **that
   position only** (reduce-only orders are floor-exempt on Binance) and
   rescale the remainder. Log as `rescale_dropped_position`.
6. Log the skip reason **and** the rescale: pre-gross, post-gross, turnover,
   fee.

**Do not re-rank on a skip.** Weights keep their relative proportions; only
the scalar changes. Re-ranking would make a skip into a rebalance and defeat
the reason it was skipped.

### 2.2 Deadband

Rescaling every skip on a tiny drift wastes fees. Apply a deadband: rescale
only when `|current_gross / target_gross − 1| > 0.10`.

10% is chosen as roughly half the drift needed to breach the §3 floor from
target — tight enough to prevent accumulation, loose enough to ignore noise.
**Not a tunable parameter. Do not grid it.**

### 2.3 Test 17

- Skip with drifted leverage → rescale fires, post-gross ≈ target to 1e-9
- Skip within the deadband → no trade, no fee
- Relative weights unchanged by rescale (ratios preserved to 1e-12)
- Consecutive skips → leverage stays bounded; run Test 16 over a
  skip-heavy fixture
- A position pushed under `MIN_NOTIONAL` by rescale is closed, remainder
  rescaled, event logged

---

## 3. PRE-REGISTRATION: gross leverage floor at 1.05×

Outstanding since `STAGE2A_REMEDIATION.md` §4. **Decide it now.**

Arithmetic: the smallest position is `MIN_WEIGHT_FRACTION · L · C / N`. At
`C=$100`, `N=10`, floor `$5`, that requires **`L ≥ 1.0` exactly**. Synthetic
runs put realised `L` at median 0.94–0.98 with 58–80% of rebalances below
1.0×, and real crypto idiosyncratic vol is higher than the synthetic 3%/day,
which pushes `L` lower still.

**Ruling: floor gross leverage at 1.05×.**

Cost: vol runs ~21% instead of 20%. Expected max drawdown moves from
`σ/(2S)` = 14.3% to 15.0% — still far below the 30% kill threshold, and
inside Test 6's ±30% relative band.

Rejected alternatives, for the record:

| Option | Smallest position | Why not |
|---|---|---|
| N=10 → 8 | $5.94 | Breadth is the only lever on achievable Sharpe; `IR ≈ IC·√breadth` costs ~11% |
| Band → [0.7, 1.3] | $6.65 | Distorts rank weighting, which is a specified part of the strategy |
| Accept skips | — | Unbounded and unknown |

Two derived constraints conflict — the `MIN_NOTIONAL` floor and the 20% vol
target. The floor is set by Binance and is not negotiable; the vol target was
chosen to make the drawdown limit informative, and 21% still does that. Relax
the softer one.

Add `min_gross_leverage: float = 1.05` to `Config`. **Test 18:** realised
gross never below the floor on any filled or rescaled day.

**Re-measure after the backfill.** A wider universe diversifies better and
pushes realised leverage up; if median `L` lands comfortably above 1.05 the
floor stops binding on its own. Report the distribution either way. Changing
the floor after seeing grid *results* is forbidden; changing it after seeing
the post-backfill *leverage distribution* — before any grid run — is
acceptable, and must be logged with a timestamp and a reason.

---

## 4. Slippage as a config parameter

`grep -i slippage backtest/` currently returns nothing. The paper log
measured **0.050% per side**, which contradicts the zero assumed everywhere.

Add to `Config`:

```python
slippage_bps_per_side: float = 0.0
```

Applied to every fill, adverse to the trade direction, on top of fees.

**Run the grid twice: at 0.0 and at 5.0 bps.** Both go in `trials.jsonl`.
This is a sensitivity analysis, not a search — the pair is reported together
and neither is selected over the other.

**Trial accounting:** the 6-point grid at two slippage settings is **12
runs**, but slippage is a cost assumption rather than a strategy parameter,
so count it as **6 trials** for the Deflated Sharpe. Document this reasoning
in `NOTES.md`; if in doubt, count 12 — over-counting is conservative and
under-counting is not.

Caveat to carry: 5bps came from n=1 testnet fill, where fills are synthetic.
It is a plausible magnitude, not a measurement. Label it as such wherever it
appears.

### 4.1 Per-symbol slippage — build the hook, don't use it yet

Slippage is not uniform: BTCUSDT spreads run under a basis point, mid-cap
alts 5–20bps. Post-backfill you'll hold far more mid-caps, so a flat constant
understates cost where it matters.

Structure `costs.slippage_bps(symbol, view)` so a per-symbol model can drop
in later, but **return the flat config value for now**. A per-symbol model
fitted before the baseline exists is an extra trial with no baseline to
measure against. `live/costlog.py` should bucket the weekly report by symbol
liquidity tier so real data accumulates for that model.

---

## 5. Trial accounting for the void grid

The first train grid ran against a harness violating a pre-registered
constraint (`max_gross_leverage = 3.0`, breached ~7×). It measured the bug,
not the strategy.

**Ruling:**

1. Those runs are **void observations** and do **not** consume trial budget.
2. They stay in `trials.jsonl`, marked `"void": true` with a reason. Deleting
   them would falsify the record.
3. The Deflated Sharpe trial count excludes void runs.
4. Re-running the same six configs post-fix is **the same six trials**, not
   six more.
5. Add `NOTES.md` §15 recording that a first look at real train data occurred
   and produced only wipeouts. Near-zero information about edge, but not
   literally zero, and it should be on the record.

The bug fix itself is not a trial: restoring conformance to a pre-registered
constraint is not a new choice.

**Any number carried forward from that grid is void.** If a Sharpe figure
from it has been quoted anywhere, strike it explicitly in `NOTES.md` rather
than letting it drift into memory as a prior.

---

## 6. Order of work

1. Test 16 against the **pre-fix** path — confirm it FAILS, record peak
   leverage
2. §1.2 invariant audit; §1.3 binding-floor fixture
3. §2 rescale-on-skip, replacing flatten; Test 17
4. §3 leverage floor; Test 18
5. §4 slippage parameter and hook
6. §5 trial-log housekeeping
7. Full suite: Stage 1 13/13, Stage 2 all green, live 19/19
8. Re-run null canaries at 30 seeds — **report the new figures**; they will
   move again because the skip path changed, and that is expected
9. **Stop. Report before running the grid.**

Do not run the grid in the same session as the fixes. The reason is
procedural: a green suite plus a fresh result in one message makes it easy to
skim the suite and read the result, which is the moment mistakes survive.

## 7. Acceptance

- Test 16 demonstrated failing pre-fix, passing post-fix, peak leverage recorded
- `below_min_notional` fires in at least one fixture
- Rescale-on-skip live, with deadband; Tests 17 and 18 green
- `min_gross_leverage = 1.05` and `slippage_bps_per_side` in `Config`
- Void runs marked, §14/§15 written
- All three suites green; null canary figures reported fresh
- Nothing in §2 or §3 decided by comparing real-data outcomes

## 8. Do not

- Choose between flatten and rescale by testing both on real data
- Tune the deadband or the leverage floor
- Fit a per-symbol slippage model before the baseline exists
- Delete or rewrite void trial records
- Run the grid before reporting
- Treat any figure from the void grid as a prior

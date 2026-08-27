# Stage 3 — Post-grid analysis (zero trials)

Follows the first valid train grid (`c46c295`). Incorporates an external
review, with two of its proposals modified and one rejected — reasoning in
§6 and §7.

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 0. The constraint that shapes this entire document

**Two trials remain of twenty. Nothing in §1–§4 spends one.**

Every item here is either a data-correctness fix, attribution of a backtest
that has already run, or cost sensitivity on an unchanged strategy. None of
them selects a configuration, so none consumes budget.

The external review proposed two new strategy experiments. That is the whole
remaining budget with nothing left for validate. **Do §1–§4 first.** They may
answer the question without a trial being spent, and they will certainly
change how the remaining two should be used.

If a step here tempts you into a new configuration, it goes in §5 and waits.

---

## 1. Funding data — the blocking fix

### Why this is first

17.6% of funding settlements on held positions are missing (~7,300 of
~41,430). Funding generates 61% of net PnL on the frozen config. **The number
this project rests on has an 18% hole in its largest input.**

If the missing settlements resemble the observed ones, true funding is ~1.21×
reported, net PnL rises ~13%, and Sharpe moves 0.81 → ~0.92. But they are
concentrated in long-tail names, which is exactly where funding rates are most
extreme — so the error could be substantially larger, in either direction.

### 1.1 Exposure-weighted missing funding

A raw count cannot distinguish 7,300 negligible exposures from 200 large ones
on big shorts. Report:

```
missing_exposure_ratio =
    Σ |position notional| over missing settlements
  / Σ |position notional| over all settlements
```

Break out by: leg (long/short), by year, and by symbol (top 20 contributors).

**If this ratio is under ~2%, the gap is immaterial and §1.2 becomes
optional.** If it is above 10%, the grid result is provisional until fixed.
Report the number before deciding how much of §1.2 to do.

### 1.2 Recover what is recoverable

For each missing settlement, in order:

1. Re-fetch the symbol-month dump — some gaps are ingest failures, not
   absences.
2. Query the REST funding endpoint for that symbol and window.
3. If genuinely absent, leave it missing. **Never interpolate or zero-fill.**
   An imputed funding rate on a long-tail alt is a fabricated number in the
   line item that drives the result.

Report recovery: settlements recovered, still missing, and the
exposure-weighted ratio before and after.

### 1.3 Mark-price accuracy — conditional

The review wants full mark-price funding accounting. **Do this only if §1.1
shows the exposure-weighted ratio above 5%**, and only for settlements on
positions in the top decile of notional.

Earlier estimate put the daily-open approximation at ~6e-4 of notional
cumulatively — about half a taker round trip. That was computed when funding
was a minor cost. It is still probably second-order next to *missing* data,
which is why §1.2 outranks it. Confirm with the §1.1 numbers rather than
assuming either way.

### 1.4 Re-run the frozen config after fixing

`lb14/skip0` only, at the pre-registered 5bps and +1min. **This is a re-run of
an existing trial on corrected data, not a new trial.** Log it as such,
replacing the prior row with a pointer to it, and state the Sharpe before and
after.

---

## 2. Per-year attribution — the highest-value free action

**This costs nothing and may be decisive.**

The grid has already run. Slicing its output by calendar year is attribution
of a completed backtest, not new selection. No trial.

For the frozen `lb14/skip0` at 5bps, report per calendar year 2020–2023:

| Metric | Why |
|---|---|
| Price PnL | Is the momentum component alive? |
| **Funding PnL** | **The one that matters most** |
| Long-leg PnL | Review found longs make price PnL |
| Short-leg PnL | Review found shorts lose on price, live on funding |
| Fees, turnover | Cost stability |
| Sharpe, max DD | Regime consistency |
| Realised beta, gross leverage | Risk stability |

### 2.1 What you are looking for

Documented crypto carry ran Sharpe 6.45 over 2020–2025, fell to 4.06 from
2024, and turned **negative in 2025** — inside your holdout window.

So the decisive question: **does funding PnL hold across all four years, or
does it collapse after 2021?**

- **Holds roughly flat** → the mechanism is durable, the aggregate 0.81 is
  not a 2020 artifact, and validate is worth a trial.
- **Concentrated in 2020–21, decaying after** → the aggregate is historical,
  the holdout sits in the decayed regime, and you may have your answer without
  spending anything.

An aggregate Sharpe of 0.81 that decomposes as `2020: +2.1, 2021: +1.4,
2022: +0.1, 2023: −0.2` is a completely different object from
`+0.7, +0.9, +0.8, +0.8`, and they are indistinguishable at the aggregate.

**Do not pick a config based on which looks best per year.** The config is
frozen. This is diagnosis, not selection.

### 2.2 Also slice the drift decomposition

The 44% drift component was measured over the full window. Report it per year
too, against the ~18% synthetic floor. Drift-harvesting should decay with
sample length if it is finite-sample noise; if it is stable across years, it
is something else.

---

## 3. Cost curve

Same strategy, cost sensitivity, no trial. Run `lb14/skip0` at:

```
0, 2.5, 5, 7.5, 10, 15, 20 bps per side
```

Report the headline statistic:

```
c* = maximum per-side execution cost at which annualised net return > 0
```

Also report Sharpe at each point and the bps at which Sharpe drops below 0.3
(the pre-registered stop threshold).

**Interpretation, agreed in advance so it is not chosen after seeing it:**

- `c* > 15 bps` — robust to execution quality; live cost uncertainty is not a
  threat
- `c* ∈ [7, 15]` — viable but execution quality matters; paper-trading cost
  data becomes critical before live
- `c* < 7 bps` — the 5bps assumption rests on a single synthetic testnet fill,
  and the strategy may be inside the noise of its own cost estimate

**The 0-bps figure is no longer a headline anywhere.** 5bps (or the eventual
measured live cost) is the baseline. Update `NOTES` §18.1 to present it that
way.

---

## 4. Freeze the configuration

`skip=2` is dominated at every lookback at 5bps: 0.60/0.12, 0.81/0.17,
0.72/0.61. It also produces larger drawdowns and fee drag up to 424%.

**Frozen baseline: `lookback=14, skip=0`.**

Do not explore `skip=1`, `skip=3`, `skip=4`, or lookbacks between the grid
points. That is how a six-trial project becomes a parameter mine. Record the
freeze in `NOTES` with the date.

Note explicitly: choosing `lb14/skip0` from six configs is a selection made on
train. It is already reflected in the Deflated Sharpe count of 6 and must stay
reflected there.

---

## 5. The two remaining trials — do NOT spend yet

Decide after §1–§4 report. Options, with my assessment:

### Option A — pure carry benchmark (1 trial)

Same engine, universe, N, hedge, vol target, execution, costs. **Change only
the ranking signal** to trailing point-in-time funding: short the highest
positive trailing funding, long the lowest.

Answers: *did momentum select useful assets, or did we reinvent a funding
carry trade?*

Three outcomes, all informative — carry ≫ current (pivot), current ≫ carry
(momentum is doing real selection), similar (you have a momentum-selected
carry trade).

**This is the better of the review's two experiments and the one I'd endorse.**

### Option B — long momentum / short carry (1 trial) — I recommend against

The review's preferred architecture selects shorts by funding attractiveness.
That builds the carry trade explicitly on the short leg — the mechanism
documented as going negative in 2025, which is your holdout window.

There is also an economic point worth stating plainly: **funding receipts are
not free money, they are compensation for a risk.** You are paid to short
crowded longs because sometimes the crowded long keeps ripping and squeezes
you. Your short leg's negative price PnL *is* that risk materialising. It is
not an anomaly to exploit; it is the carry trade functioning as designed.

Selecting shorts by funding means deliberately loading that risk immediately
before testing on the period when it stopped paying. If it improves train
Sharpe, you will have optimised into a decayed mechanism and made the holdout
*more* likely to fail.

If §2 shows funding PnL stable across all four years, this objection weakens
considerably. **Revisit only then.**

### Option C — reserve one trial for validate

Validate is a run and costs a trial. Spending both on experiments leaves none.

### On expanding the budget

Twenty was a discipline device you chose, not a law of nature. It can be
expanded — **deliberately, logged with a date and reason, and with the
Deflated Sharpe recomputed at the higher count.** That is the honest path if
the carry benchmark is worth the dilution.

What is not acceptable is spending trial 21 without recording that the budget
moved. State the new number in `NOTES` before running, not after.

---

## 6. Rejected from the review

**Walk-forward as a parameter search.** Six configs across four folds is 24
evaluations against two remaining trials. "Pick a stable plateau rather than
the best average" does not escape selection — best-of-6 chosen by *minimum*
fold Sharpe still has non-zero expectation under the null.

§2's per-year attribution captures the review's actual diagnostic intent —
*does the mechanism appear in different regimes* — at zero cost, because the
config is frozen and already run.

**Covariance/EWMA work.** The review deprioritised it and I agree. Realised
vol 19.7–25.6% against a 20% target is not the dominant problem.

**Residual momentum.** Good idea and the 44% drift result motivates it, but it
is a new alpha costing a trial. It belongs behind Option A. §2.2 may inform
whether it is worth it — if drift is stable rather than decaying, residual
momentum becomes more interesting.

---

## 7. Holdout

**Untouched.** Not a candidate for anything in this document.

At 1.58 years it confirms nothing below a true Sharpe of ~1.6, and your
baseline sits at 0.81 — squarely in the range it cannot resolve. That is a
known limitation, not a reason to look early.

Sequence remains: freeze → validate → predefined pass/fail → holdout, one
look, no tweaking after.

---

## 8. Order of work

1. §1.1 exposure-weighted missing funding — **report before doing anything else**
2. §1.2 recovery, scaled to what §1.1 shows
3. §1.3 only if §1.1 > 5%
4. §1.4 re-run frozen config on corrected data
5. §2 per-year attribution, including §2.2
6. §3 cost curve
7. §4 record the freeze
8. **Stop. Report. Spend no trials.**

## 9. Acceptance

- Exposure-weighted missing funding reported by leg, year, and top-20 symbols
- Recovery counts stated; nothing interpolated or zero-filled
- Frozen config re-run on corrected data; Sharpe before and after
- Per-year attribution table complete for 2020–2023
- Drift decomposition sliced by year against the 18% floor
- `c*` reported with Sharpe at each cost point
- Freeze recorded with date; `skip=2` retired
- Trial budget still **6 of 20**
- `NOTES` §18.1 updated so 5bps is the headline, not 0bps

## 10. Do not

- Spend a trial on anything in §1–§4
- Explore skip or lookback values outside the frozen config
- Interpolate or zero-fill a funding rate
- Select a config using per-year results
- Implement Option B before §2 reports
- Touch validate or holdout
- Quote a 0-bps figure as a headline

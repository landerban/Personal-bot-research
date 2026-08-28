# Stage 3b — Rank-bucket attribution (zero trials)

Follows Stage 3a. §22.5 concluded "the train window is exhausted as an
information source" on the decay-vs-regime question. **That is true on the
dispersion axis and not true on the composition axis.** This document tests
the composition axis, for free.

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 0. Constraint

**Spends no trial.** This is attribution of a backtest that has already run —
no configuration is changed, no signal altered, no new strategy evaluated.

Config stays frozen: `lookback=14, skip=0`, $400, 5bps, +1min. Budget stays
**6 of 20**. Validate and holdout untouched.

---

## 1. The hypothesis

§22.1 showed dispersion does not explain the price-PnL decline: 2020
(sd 15.17%) and 2023 (sd 13.13%) have near-identical dispersion and opposite
outcomes. You flagged universe growth as a confound. It is a better fit than
dispersion:

```
universe  :   29  ->  104  ->  129  ->  166      (monotonic)
price$    : +163  ->  +110  ->  +30  ->  -37     (monotonic)
dispersion: 15.2  ->  30.3  ->  13.0 -> 13.1     (not monotonic)
```

**Mechanism.** Published research finds momentum concentrated in roughly the
top 2% of coins by market capitalisation, with the remaining 98% showing
*negative* average momentum payoffs. In 2020 only large caps had perpetual
listings, so a 29-name universe was effectively majors-only. By 2023 the
strategy ranked 166 names dominated by mid and small caps — the segment where
the published payoff is negative.

If true, the alpha did not decay. **It was progressively diluted by the
universe it was permitted to trade.**

This is testable from the completed run.

---

## 2. Measurement

For every position held in the frozen-config train run, record:

| Field | Notes |
|---|---|
| date, symbol, leg | long or short |
| **liquidity rank** | rank within that day's point-in-time universe, by the same median-quote-volume measure `universe()` uses; 1 = most liquid |
| universe size that day | needed to interpret rank |
| price PnL contribution | from the existing per-symbol trace |
| funding PnL contribution | same |
| position-days | exposure weighting |

**Rank must be point-in-time.** Compute through `PITView` at each rebalance
date. A rank derived from present-day liquidity would be lookahead, and the
habit matters more than this one statistic.

The per-symbol daily PnL trace already exists and reconciles to `gross_pnl`
exactly (`pnl_trace_reconciles`). Use it as the input rather than re-running
the engine.

### 2.1 Buckets

`1–30`, `31–100`, `101+`.

**Note the structural limit and do not work around it:** the 2020 universe
peaked at 29 names, so the two lower buckets are empty or near-empty that
year. Within-year bucket comparison is impossible for 2020. The test is
therefore **across years within a bucket**, not across buckets within a year.

Report bucket availability per year explicitly so an empty cell is never read
as a zero result.

### 2.2 Report

Per bucket per year:

- price PnL, funding PnL
- position-days, and **share of total position-days** — this is the dilution
  mechanism itself, and it should show the `101+` share rising
- price PnL per position-day, so buckets of different sizes are comparable
- long-leg and short-leg split

Plus one summary: **the top-30 bucket's price PnL per position-day, by year,
2020–2023.** That single series is the test.

---

## 3. The reading — write this into NOTES before computing anything

Per Stage 3a precedent, fix the interpretation first.

| If... | Then |
|---|---|
| Top-30 price PnL per position-day stays **positive across all four years**, while `101+` is negative and its share of position-days rises | **Dilution.** The alpha survives where it always lived; the universe grew into the segment with negative payoffs |
| Top-30 price PnL per position-day **declines across years like the aggregate** | **Decay.** The alpha weakened in the segment where it was strongest; universe growth is coincidental |
| **No consistent rank pattern** | Neither. Something else drives the decline and this axis is exhausted too |

Do not adjust the reading after seeing the numbers. Report which branch fired
and state plainly if the evidence behind it is weaker than the label — as
§22.1 correctly did.

---

## 4. Correction required to NOTES §22.3

§22.3 states the holdout window "is a period in which BTC made new highs" and
that the `>50%` drawdown bucket "would largely not exist there."

**That is factually wrong and must be corrected.** Bitcoin peaked at $126,296
on 6 October 2025, then fell ~46.7% to about $67,550 by mid-February 2026 and
roughly 49% below the peak by June 2026, with total crypto market cap down
~48%.

So the holdout (2025-01 → 2026-07) contains **both regimes**: new highs
through October 2025, then a ~50% drawdown across the remainder. The deep-
drawdown bucket that produced 72% of train funding **does exist there**.

Rewrite the §22.3 conclusion accordingly. Note also the structural echo: your
worst drawdown ran 2021-11-25 → 2022-04-22, beginning two weeks after the
November 2021 peak and troughing *before* both LUNA and FTX — so the
vulnerability is the bull-to-bear transition, not crash depth. The October
2025 peak sits in the same position relative to the holdout that November 2021
sits relative to train.

This is a factual correction from external data, not a result. It costs no
trial and changes no configuration.

---

## 5. Do not implement

If §3 fires "dilution," the implied fix is restricting the universe — a
liquidity-rank cap or a market-cap tilt. **Do not implement it.**

That is a configuration change and costs a trial. It also cannot be
parameterised from these results: choosing "top 30" because top-30 looked best
here is fitting the cap to the attribution that motivated it. If it is worth a
trial, the cap must be justified from the published 2%-of-market-cap finding
or from a liquidity threshold derived independently — and pre-registered
before any backtest of it runs.

Add to the §22.6 candidates table with that constraint stated.

---

## 6. Order of work

1. Write the §3 reading into `NOTES` §23, dated, before computing
2. Rank extraction through `PITView`; verify ranks are point-in-time by
   asserting no rank uses a bar with `close_time > as_of`
3. Bucket attribution; reconcile bucket price PnL sums to the run's total
   `price$` per year to within floating-point tolerance — **if it does not
   reconcile, stop and report**, because the trace is the input to everything
   here
4. Report the §2.2 table and the summary series
5. §4 correction to §22.3
6. State which branch fired and how strong the evidence actually is
7. **Stop. Report. Spend no trials.**

## 7. Acceptance

- §3 reading recorded before any number was computed
- Ranks point-in-time, asserted
- Bucket price PnL reconciles to per-year totals
- Bucket availability per year stated; empty cells never reported as zeros
- Top-30 price PnL per position-day series, 2020–2023
- `101+` share of position-days by year
- §22.3 holdout premise corrected
- §22.6 updated; nothing implemented
- **Budget still 6 of 20**; validate and holdout untouched

## 8. Do not

- Re-run the engine — use the existing per-symbol trace
- Use present-day liquidity for ranking
- Change bucket boundaries after seeing results
- Implement a universe restriction
- Adjust the §3 reading post hoc
- Touch validate or holdout

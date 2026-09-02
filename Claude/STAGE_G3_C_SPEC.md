# Stage G3-C-SPEC (v2) — The specification, then the lock. No run.

Closes eleven items found by the first delegate — including one **algebraic
degeneracy** that would have wasted the trial — and pins the three things the
corrections make newly important. **No forecast fitted on real data. No
return-based result. No trial consumed. Gen-3 0 of 20. Holdout sealed.**

Appended as `NOTES` §70. §0, §68 as amended, §69 govern.

---

# PART A — Owner decisions (§70.1)

## A.1 Source timing — the bot's possession time, not the publisher's

**USER DECISION:** the deployed system reads each value from its publisher's
own public page on a **scheduled evening fetch**, no live feed, no vendor.

**Corrected rule (v1 was wrong here):** publisher time is *not* the time the
bot knows the value. If the Fed publishes at 21:15 and the job runs at 22:00,
the bot possesses it at 22:00. Freeze:

```
t_usable = max( t_publisher , t_scheduled_retrieval )
```

**Train at the earliest timestamp the deployed acquisition system would
actually possess the observation.** Training at `t_publisher` would hand the
model up to 45 minutes it will not have.

Justification remains **architectural** — the intended production source and
schedule — never the §69 sensitivity map, which stays quarantined (§69.1.1).

Invariants unchanged: `source_available_time ≥ underlying_public_time`; the
reader returns nothing when `source_available_time > decision_time`. A missed
fetch yields a **stale** value with its true knowable-at stamp, never a
forward-filled value posing as fresh.

## A.2 Adoption — full Panel A, gold excluded

Adopted: BTC, ETH (PIT store, with funding), VIX, US 2Y, US 10Y, the USD
measure (**exact series pinned in the manifest at lock**), S&P 500,
Nasdaq-100. **Gold NOT adopted** (`UNVERIFIED` stands). Rates and USD are kept
despite looking weak in §69's unconditional map — dropping them after seeing
it would be selection on development data.

## A.3 Panel B — DEFERRED, not conditional

**Corrected (v1's gate was circular):** a daily test cannot falsify an
intraday transmission hypothesis — an effect lasting twenty minutes would fail
Q2 and still be real. Therefore:

> Panel B remains **deferred** and may later be proposed as an **independent,
> separately pre-registered mechanism** if justified by architecture and
> resources. **A daily G3-C PASS is not a prerequisite.**

# PART B — Timing, frozen exactly (§70.2)

## B.1 The four timestamps of the daily arm

```
22:00 UTC on day D   decision_time = feature_cutoff_time
                     (features: only observations with t_usable ≤ 22:00 D)
        │
        │            NO target exposure in this gap
        ▼
00:00 UTC D+1        target_start_time  (hypothetical execution)
        │
        │            the next COMPLETE UTC day
        ▼
00:00 UTC D+2        target_end_time
```

**Stated explicitly so it cannot be misread later:** *the strategy does not
notionally enter at 22:00. The 22:00 observation is a decision snapshot;
execution is defined at the next 00:00 UTC boundary, and the target is the
next complete UTC-day return.* The two-hour gap is deliberately forgone —
losing two hours of possible edge is preferred to ambiguous training/live
alignment. No information arriving between 22:00 and 00:00 may enter that
forecast. Panel B may recover the gap later.

A test asserts every feature's `t_usable ≤ 22:00 D` and every target window is
`[00:00 D+1, 00:00 D+2)`.

# PART C — Model specification (§70.3), two models with distinct semantics

**v1 conflated them.** The BTC-direction and cross-sectional questions need
different feature semantics, and pretending one table serves both is what
produced the Q3/Q4 defects.

## C.1 BTC-direction models — market-level features

```
M0-dir  (4 families)
  1 btc_trend : BTC log-return over {1, 5, 21} complete UTC days
  2 btc_vol   : realised vol = sample std (ddof=1) of daily BTC log-returns over 21 days,
                NOT annualised; vol_of_vol = sample std of that 21d series over 21 days
  3 carry     : BTC funding rate, most recent settlement with t_usable ≤ cutoff;
                cross-sectional mean funding over the eligible universe, same rule
  4 dispersion: cross-sectional sample std (ddof=1) of 1-day log-returns over the
                eligible universe; breadth = fraction of eligible universe with
                positive 1-day log-return

M1-dir  = M0-dir + (4 families)
  5 equities  : SP500 1-day log-return; NASDAQ100 1-day log-return
  6 volatility: VIX level; VIX 1-day change (levels, not log)
  7 rates     : 2Y level; 2Y 1-day change; 10Y level; 10Y 1-day change; 2s10s = 10Y − 2Y
  8 usd       : USD measure 1-day log-return
```

## C.2 Cross-sectional models — asset-specific, and the Q4 fix

**The degeneracy, recorded:** if the exogenous term is common to every asset,
`r̂ᴹ¹_i = r̂ᴹ⁰_i + γᵀX_t`, then every pairwise difference is unchanged and the
cross-sectional ranking is **identical by construction** — verified: Spearman
exactly 1.0. Q4 as written in v1 could not return anything but zero. It was
not a weak test; it was not a test.

```
M0-xs   per-asset predictors, market state as conditioning only
  1 asset_trend : log-return over {1, 5, 21} complete UTC days, per asset i
  2 asset_vol   : sample std (ddof=1) of daily log-returns over 21 days, per asset i
  3 asset_carry : asset i's funding rate, most recent settlement with t_usable ≤ cutoff
  4 market_state: the C.1 family-4 dispersion and breadth (shared; conditioning only)

M1-xs   = M0-xs + asset-specific EXPOSURE × exogenous state
  5 equity_beta_int : β^SPX_{i,t}·r_SPX,t ;  β^NDX_{i,t}·r_NDX,t
  6 vol_beta_int    : β^VIX_{i,t}·ΔVIX_t
  7 rates_beta_int  : β^2Y_{i,t}·Δ2Y_t ;  β^10Y_{i,t}·Δ10Y_t
  8 usd_beta_int    : β^USD_{i,t}·r_USD,t
```

Now the same market move affects a high-exposure and a low-exposure coin
differently, and Q4 asks a real question: *does cross-market state, combined
with each coin's exposure to it, improve which coins outperform?*

## C.3 The exposure estimator — frozen, or it becomes the next tuning surface

```
β^X_{i,t} = Cov( r_i , r_X ) / Var( r_X )        over the trailing 90 complete UTC days
                                                  ending at the feature cutoff
  intercept        : yes (OLS with intercept; β is the slope)
  min observations : 60 valid paired days; otherwise the asset's interaction
                     features are MISSING for that date
  missing data     : the date/asset is excluded from that day's cross-section,
                     counted and reported; never zero-filled
  standard error   : computed and recorded (not used as a feature in Trial 1)
  shrinkage        : NONE in Trial 1
  estimated        : separately per X ∈ {SPX, NDX, VIX, 2Y, 10Y, USD}
  universe         : mature names only — assets meeting the min-observation rule
```

**No hierarchical shrinkage in Trial 1**, deliberately: sliding the juvenile
solution into Q4 would let it earn credit before the juvenile hypothesis has
its own pre-registered trial (§68.12.6, gated on Q3 PASS).

## C.4 Forecast form and regularisation — fully frozen

**Form:** L2-regularised logistic regression (direction); L2-regularised
linear regression (cross-section). Simplest forms admitting the stated
features; the neural network remains a later rung (§68.6 rule 5).

**Penalty:** L2 only. **Grid:** `C ∈ {0.01, 0.03, 0.1, 0.3, 1, 3, 10}`
(logistic) / `α ∈ {0.001, 0.01, 0.1, 1, 10}` (ridge) — fixed here, not
searched adaptively. **Inner selection:** expanding-window CV *within the
training years only*, folds split at calendar-year boundaries (for the
single-year first fit, at quarter boundaries). **Selection metric:** mean
inner-fold log-loss (direction) / mean inner-fold MSE (cross-section).
**Tie-break:** the **strongest** regularisation among ties (smallest `C`,
largest `α`). Standardisation uses training-window statistics only.

## C.5 Sequential protocol

Fit 2020 → forecast 2021; refit 2020–21 → 2022; refit 2020–22 → 2023; refit
2020–23 → 2024. A test asserts every forecast's fit window ends strictly
before its target date.

## C.6 Calibration — chosen, and fitted out-of-fold

**Method: Platt scaling** (logistic on the raw score), chosen for stability at
these sample sizes.

**Procedure, frozen** — the calibrator is never fitted to in-sample fitted
probabilities:

```
training years → time-respecting out-of-fold predictions (same inner folds as C.4)
              → fit Platt calibrator on those OOF predictions
              → fit the final model on the full training window
              → forecast the target year
              → apply the already-fitted calibrator
```

Calibration quality is **reported** (reliability curve, Brier decomposition),
never a criterion.

# PART D — Criteria (§70.4)

## D.1 The four questions

| Q | Test | Criterion |
|---|---|---|
| Q1 | `M0-dir` skill vs climatology | BSS, stationary bootstrap, **`CI_lower(BSS) > 0`** |
| Q2 | `M1-dir` incremental | **`CI_lower(BSS_M1) > 0` AND `CI_lower(BSS_M1 − BSS_M0) > 0`** |
| Q3 | `M0-xs` cross-sectional skill | §60.12 daily-IC evaluator, **`CI_lower(IC) > 0`** |
| Q4 | `M1-xs` incremental | **`CI_lower(IC_M1) > 0` AND `CI_lower(IC_M1 − IC_M0) > 0`** |

**Bootstrap inherited unchanged:** stationary bootstrap on the daily series,
2,000 replicates, block length `n^{1/3}` recomputed per series, the Gen-1
interval construction **located by hash** (§60.12), two-sided 90%, seed
derived from this commit. Undefined dates excluded, counted, reported —
never zero-filled.

## D.2 MDE disclosure

No numerical MDE fabricated. The realized CI half-width is reported afterward
as observed resolving precision; **not a second criterion** (§60.12.3).

## D.3 Descriptive reporting — no economic gate in G3-C

**v1 asked a classifier for `E[r]` it does not produce.** A calibrated model
gives `P(r > 0 | X)`, not `E[r | X]`; deriving one would require an invented
magnitude mapping. Removed. Report instead, **descriptively, with no pass/fail
effect**: Brier/BSS; reliability curve; probability-bin counts; and **realized
subsequent returns conditional on probability bin** (mean and median), e.g.

```
calibrated P(up) 0.70–0.80 : n = 142, actual up-rate 0.74, mean +0.41%, median +0.22%
```

**No trade count, no cost gate, no profitability claim.** The expected-return
machinery belongs to a later stage whose model produces a return distribution.

## D.4 Consequences — the macro rung survives

**v1 contradicted §68.12.7.** Corrected:

| Outcome | Consequence |
|---|---|
| Q2 or Q4 PASS | **Cross-asset** exogenous hypothesis supported at that level; next rung per the §68.12.7 ladder |
| Q2 and Q4 fail/INDETERMINATE, Q1 or Q3 PASS | **Cross-asset** exogenous hypothesis unsupported; the §68.1 construction may proceed on **crypto-native** forecasts, relabelled as such |
| All four fail | **The crypto-native + cross-asset predictive branch fails.** The **scheduled-macro cheap rung remains eligible** on its own pre-registration. This is *not* the death of Gen-3 |
| Q3 PASS | The hierarchical cold-start rung becomes testable; Q4 decides only whether exogenous features are inherited into it |
| Apparent failure with a **reproduced** defect | attempt VOID (`void: true`, retained, budget unconsumed); corrected run is the next attempt |

Wording fixed throughout: **"cross-asset exogenous hypothesis"**, never
"exogenous hypothesis" — scheduled macro, filings, and news are different
mechanisms.

**No post-result discretion.**

# PART E — The lock (§70.5)

Pins: feature families and every transform formula; the C.3 exposure
estimator; forecast forms, penalty, grids, inner folds, metric, tie-break;
Platt calibration and its OOF procedure; the B.1 timing; **and the evaluator
code and hashes** (Brier/BSS, the §60.12 IC evaluator, the bootstrap). Trial 1
logged `pre-registered`, `attempt_id = 1`, `valid_trial_count = 0`. A test
asserts no frozen quantity can change between pre-registration and execution.

**Deterministic evaluator tests before the lock:** planted positive skill ⇒
`CI_lower > 0`; planted zero ⇒ CI straddles zero; planted negative ⇒ FAIL; the
conjunctive criterion rejects `BSS_M0 = −0.20, BSS_M1 = −0.10`; **and a
degeneracy test — a shared additive term applied to every asset leaves the
cross-sectional IC exactly unchanged** (the v1 failure, pinned so it cannot
recur).

## Order of work

1. §70.1–§70.5 appended, dated; owner decisions labelled
2. Publisher-page fetch implemented at the scheduled evening time; `t_usable`
   rule and B.1 timing tests green
3. Model, exposure estimator, calibration, evaluators written and unit-tested
   on **synthetic** data only
4. Commit; record lock hash; log trial 1 `pre-registered`
5. **STOP.** Both delegates review §70.

## Acceptance

- `t_usable = max(publisher, scheduled retrieval)`, tested
- B.1's four timestamps frozen with the no-entry-at-22:00 statement verbatim
- Separate direction and cross-sectional semantics; M1-xs uses exposure
  interactions; degeneracy test present and green
- Exposure estimator fully frozen incl. min-observations and missing-data
  behaviour; no shrinkage in Trial 1
- Penalty, grid, folds, metric, tie-break, calibration method and OOF
  procedure all explicit
- Criteria conjunctive; bootstrap by hash; MDE disclosure only; **no economic
  gate**; consequences preserve the macro rung; wording is "cross-asset"
- Lock pins spec **and** evaluators; trial `pre-registered`
- **No return read; Gen-3 0 of 20; holdout sealed**

## Do not

- Train at publisher time when the scheduled fetch is later
- Let any 22:00–00:00 information enter the forecast
- Use shared exogenous features in the cross-sectional model
- Apply hierarchical shrinkage to Q4's exposures
- Cite the §69 sensitivity map as a reason for anything
- Search regularisation outside the frozen grid, or select any parameter
  against out-of-sample-in-time results
- Compute an expected-return or trade-count gate
- Read "all four fail" as ending Gen-3
- Run G3-C, build any book, or touch the holdout

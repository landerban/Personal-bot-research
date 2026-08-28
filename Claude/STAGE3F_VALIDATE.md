# Stage 3f — Amend the validate rule, calibrate, then validate

Follows Stage 3e. **One trial remains.** Parts A and B spend none of it.

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 0. Why §29 is being amended, and why that is legitimate

§28.4 computed the validate MDE at **1.65** two-sided against a train Sharpe
of 0.796. Extending that: validate can barely *refute* either. A validate
Sharpe of **−0.50** — a visibly bad year — sits only 1.29 SE below train,
one-sided p ≈ 0.098.

| Validate Sharpe | SE below train | one-sided p |
|---|---|---|
| −0.50 | 1.29 | 0.098 |
| 0.00 | 0.80 | 0.213 |
| +0.30 | 0.50 | 0.310 |

**So a rule resting mainly on a minimum Sharpe rests on the least powerful
statistic available**, and would produce "inconclusive" for almost any
outcome.

**Why amending is not a violation.** §29 was pre-registered before any
validate result exists, and **no validate run has occurred**. The amendment
is motivated by a power analysis of the *sample* — §28.4 — not by an
outcome. There is no result to fit to.

Conditions, which are not optional:

1. The original §29 stays in the record **unmodified**. The amendment is
   §30, not an edit.
2. §30 is dated and committed **before** any validate run, in its own commit.
3. §30 states this rationale so a reader can check the ordering.
4. If any validate result exists when this is read, **stop** — the amendment
   is void and §29 stands as written.

---

## 1. Three tiers of test, ordered by power

The insight §28.4 implies but does not state: **conditioning away common
market noise is what made the Stage 3d paired bootstrap tighter than either
standalone CI (0.56 vs 1.62 width). The same trick applies out of sample.**

**Tier 1 — structural invariants.** Properties fixed by construction, not by
market outcome. Realised beta should sit near zero because the book is
hedged; realised leverage should track the vol target given realised market
vol; dollar tilt should equal `g(1−s)`. These have expected values from
*design*, so a breach means something is broken regardless of PnL. Highest
power, lowest ambiguity.

**Tier 2 — per-unit-exposure figures against train's bootstrapped
intervals.** You already have price PnL per position-day by rank bucket with
90% CIs (§24.1). Validate's equivalents can be compared directly to those
intervals. This is far tighter than a Sharpe comparison because it is a
per-observation statistic with a known train distribution, not a
whole-window aggregate.

**Tier 3 — headline Sharpe.** Report it, but per §28.4 it is nearly
uninformative in either direction. It must not be the deciding criterion.

## 2. Calibrate on train first — free, and required

**A tracking test without a null distribution is unreadable.** You cannot
tell whether validate's tracking error is anomalous without knowing what
normal looks like.

Before running validate, on **train only**, using existing runs:

1. Compute each Tier 1 invariant per calendar year and report its range
   across 2020–2023. That range is the tolerance band.
2. Restate the Tier 2 per-position-day figures and 90% CIs from §24.1 in one
   table, per bucket, pooled and by year, as the reference validate will be
   compared against.
3. For each Tier 1 and Tier 2 quantity, state **what value would constitute
   a breach**, in numbers, before validate runs.

No new backtest. No trial. Everything here comes from runs already logged.

## 3. §30 — the amended criteria

Write all of the following, each as a number or yes/no.

### 3.1 Tier 1 — automatic stop if breached

| Invariant | Train reference | Breach if |
|---|---|---|
| Realised beta to BTC | ±0.05 observed | state a bound |
| Realised gross leverage, median | ~0.44 | state a band |
| Dollar tilt identity `g(1−s)` | exact to 1e-9 | any deviation |
| Max drawdown | 27.87%, kill switch 30% | state |
| Active-days fraction | ~100% | state a floor |
| Skip rate by reason | from §19 | state a ceiling |

A Tier 1 breach means the harness or the strategy is behaving differently
out of sample, which is a stop regardless of PnL.

### 3.2 Tier 2 — the substantive test

State, in numbers:

- How far validate's **pooled top-30 price PnL per position-day** may fall
  outside train's 90% CI [+0.0084, +0.0793] before the mechanism counts as
  broken
- The same for **pooled `101+`**, train CI [−0.2437, −0.0603] — note this is
  the finding that *did* survive Stage 3d, so it is the strongest single
  claim available to test
- Tolerance on the **price/funding composition**, train ~61% funding
  uncapped
- One-sided or two-sided, per §28.3

### 3.3 Tier 3 — reported, not deciding

State the minimum Sharpe if you want one, **and state explicitly that per
§28.4 it cannot decide the outcome alone.** If Tier 1 and Tier 2 pass and
Sharpe is weak, say now what happens.

### 3.4 Unchanged from §29

Carry forward items 6 (what failure means) and 7 (sidedness) as written. If
§29 already specifies a Sharpe-based stop, §30 supersedes it and must say so
explicitly rather than silently.

---

## 4. Part C — validate (trial 8)

**Only on explicit user go-ahead.** Do not run it as a continuation of Parts
A and B.

Config: the **uncapped frozen config** of §19.5. `lookback=14`, `skip=0`,
$400, N=10, no cap, 20% vol target, 3× cap, taker-only. Slippage {0,5} ×
delay {0,1} as cost sensitivity, reported together — **7 → 8 trials.**

Log before running; an errored run still spends the trial.

Report, in this order — Tier 1 first, Sharpe last:

1. Every Tier 1 invariant against its §3.1 band, pass or fail
2. Every Tier 2 figure against its train CI, with validate's own bootstrap CI
3. Per-year table in the §19.3 format
4. Headline Sharpe with bootstrap CI, labelled as Tier 3
5. Each §30 criterion evaluated explicitly
6. **Who is paying, and why would they keep paying?** — corrected twice
   already (§18.2 → §22.3 → §25.3); correct it again if composition moved

**After this, zero trials remain.** The holdout is one look, ever.

---

## 5. Order of work

1. Verify no validate run exists. If one does, **stop** — §0 voids this
2. §2 calibration from existing runs; report the reference table
3. Write §30 per §3, dated, **its own commit**
4. **Stop. Report. Await explicit go-ahead.**
5. On go-ahead only: log trial 8, run validate, report per §4

## 6. Acceptance

- §29 unmodified in the record; §30 is a separate section and commit
- §30 committed before any validate run, ordering verifiable from git
- Train reference table complete: Tier 1 ranges, Tier 2 CIs, breach values
- Every threshold stated as a number before validate runs
- Sidedness specified
- Budget **7 of 20** at the end of Parts A and B
- Holdout untouched

## 7. Do not

- Edit §29
- Write §30 and run validate in the same session or commit
- Run validate without explicit user go-ahead
- Let the headline Sharpe decide the outcome alone
- Adjust any §30 threshold after seeing validate
- Touch the holdout

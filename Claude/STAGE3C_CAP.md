# Stage 3c — Bootstrap first, then the universe cap

Follows Stage 3b. The dilution result is promising enough to spend a trial
on — **but not before it survives error bars**, and the trial must be
pre-registered before it runs.

§0 of `STAGE2_PROMPT.md` remains in force.

Two parts, in strict order:

- **Part A (§1–§3): zero trials.** Bootstrap the bucket splits. If they don't
  survive, Part B does not happen.
- **Part B (§4–§6): one trial.** Universe cap at rank 100. Only if Part A
  passes, and only with §5 written down first.

---

# PART A — Bootstrap the buckets (zero trials)

## 1. Why this gates everything

Stage 3b reports twelve cells (3 buckets × 4 years) with **no confidence
intervals**. Some will look extreme by chance. The headline conclusion also
depends on a bug found mid-analysis (§23.3) whose fix flipped 2022 from
−0.0049 to +0.0101 — the exact difference between branch one failing and
firing.

Rough sizing suggests 2023's ±$200 splits are large relative to noise while
2021's top-30 (+0.0028/day) is indistinguishable from zero. That is a guess.
**You built block-bootstrap machinery in Stage 2e §9 and have not used it
here.** Use it.

## 2. What to bootstrap

Stationary or block bootstrap over **daily** bucket PnL series — not
position-days. Positions are correlated within a day, so resampling
position-days would treat correlated observations as independent and produce
intervals that are too tight. Block length should reflect autocorrelation in
daily strategy returns; state the choice and how it was made.

Report 90% CIs for:

1. **Price PnL per position-day, each of the 12 cells.** Empty cells stay
   empty — 2020 `101+` is `n/a`, never zero.
2. **Top-30 minus `101+` spread, per year.** This is the dilution claim
   stated as a single testable quantity. It is the number that matters most.
3. **Top-30 price PnL per position-day, pooled across all four years** —
   the claim that the liquid segment's alpha is alive.
4. **`101+` price PnL per position-day, pooled across the three years it
   exists** — the claim that the tail loses money.

## 3. The reading — write into NOTES §24 before computing

| If... | Then |
|---|---|
| Pooled `101+` CI is **entirely below zero** AND the top-30-minus-`101+` spread CI excludes zero in **at least 2 of 3** years where both exist | Dilution survives. **Proceed to Part B.** |
| Pooled `101+` CI **straddles zero**, or the spread excludes zero in ≤1 year | Dilution is not established. **Stop. Do not run Part B.** Report and reassess |
| Pooled `101+` below zero but the spread is significant in only 2023 | Weak — the finding rests on one year. **Stop and report**; the decision moves to the user |

Also report, without it gating anything: whether the 2021 top-30 cell
(+0.0028) is distinguishable from zero, and whether `31-100` beating `1-30`
in 2020–2022 survives its own CI. §23.2 leads with that caveat and it should
be resolved rather than carried.

**Do not adjust these thresholds after seeing intervals.**

---

# PART B — Universe cap (ONE TRIAL)

**Only if Part A returns branch one. Otherwise stop at §3.**

## 4. Pre-registration — write into NOTES §25 before any run

### 4.1 The cap, and why 100

**Cap the tradeable universe at liquidity rank 100.** Names ranked 101+ are
excluded from candidacy; everything else is unchanged.

Justification, which is the whole point: **the boundary was fixed in advance.**
Stage 3b's buckets (`1–30`, `31–100`, `101+`) were pre-registered before any
number existed, per §23. Using one of them as a cap is therefore not fitted to
the result.

Rejected alternatives, recorded so the choice can be audited:

| Cap | Why not |
|---|---|
| Rank 30 | Fitted to 2023, the only year `1-30` beat `31-100` |
| Top 2% by market cap (from the literature) | 2% of 166 names is three. Not a portfolio |
| A new liquidity threshold | Any dollar figure chosen now is chosen knowing the answer |

### 4.2 The competing hypothesis, stated before the run

§23.2 flags 2021 as awkward: top-30 earned ~nothing (+0.0028/day) in the
highest-dispersion year while `31-100` carried it, then the ordering reversed
in 2023. That reads like **the profitable segment migrates with regime** —
alt-season favouring mid-caps, selective markets favouring majors.

If so, a fixed cap freezes a boundary that moves. What is stable across all
four years is only the negative bottom bucket — which is a floor, not a cap.
The rank-100 cap is therefore a test of "exclude the tail," **not** of "trade
only the majors." Do not report it as the latter.

### 4.3 What counts as success — fixed now

| Outcome | Reading |
|---|---|
| Sharpe improves **and** 2023 price PnL turns positive | Dilution confirmed and remediable |
| Sharpe improves but 2023 price PnL stays negative | Something else drives 2023; cap helps for another reason |
| Sharpe roughly unchanged | The tail was noise, not drag. Attribution was misleading |
| Sharpe worsens | The tail contributed diversification the attribution missed |

**No re-run at a different cap under any outcome.** One trial, one cap. If it
fails, that is the result.

## 5. Configuration

Identical to frozen except the cap: `lookback=14`, `skip=0`, capital $400,
5bps, +1min, N=10, 20% vol target, 3× cap, taker-only.

Run at both slippage settings {0, 5} and both delay settings {0, 1} as cost
sensitivity — reported together, **6 → 7 trials, not 7 → 10**.

Log the trial before running, per Stage 2c §5 precedent. If it errors, the
trial is still spent.

## 6. Report

Everything from `STAGE2_PROMPT.md` §6, plus:

- Per-year price PnL, funding PnL, long/short split — **directly comparable to
  §19.3's table**
- Universe size per year after the cap
- Position-days by bucket (should be zero in `101+`)
- Skip counts by reason — a smaller universe means more `universe_too_small`
- Realised leverage distribution — fewer names may change vol targeting
- Turnover and fee drag versus the uncapped run
- Deflated Sharpe **at 7 trials**
- Block-bootstrap 90% CI on the headline Sharpe

And answer in writing: **who is paying, and why would they keep paying?**
The §18.2 answer was already corrected once by §22.3 — funding is long-leg
and tail-driven, not short-leg carry. Update it again if the cap changes the
composition.

---

## 7. Order of work

1. §3 reading into `NOTES` §24, dated, **before** computing
2. Part A bootstrap; report all four quantities
3. State which branch fired
4. **If branch two or three: STOP. Report. Do not run Part B.**
5. If branch one: §4 pre-registration into `NOTES` §25, dated
6. Log trial 7; run the cap; report per §6
7. **Stop.** Do not run validate or holdout

## 8. Acceptance

- §3 reading recorded before any interval computed
- Bootstrap over daily series, not position-days; block length justified
- CIs for all 12 cells, both pooled figures, and the per-year spread
- 2021 top-30 and the `31-100` vs `1-30` caveat resolved against CIs
- Empty cells reported as `n/a`
- Part B only if branch one; §4 written before running
- If run: trial logged before execution, budget stated 6 → 7
- Per-year table comparable to §19.3
- One trial remains after this

## 9. Do not

- Run Part B if Part A does not return branch one
- Adjust §3 thresholds or §4.3 readings after seeing results
- Try a second cap value under any outcome
- Bootstrap position-days as independent observations
- Report the cap as "trade only the majors"
- Touch validate or holdout

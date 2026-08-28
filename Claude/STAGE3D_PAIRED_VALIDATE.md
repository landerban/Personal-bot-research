# Stage 3d — Paired test, then pre-register validate

Follows Stage 3c (trial 7). **One trial remains of twenty.** Two things must
happen before it is spent, and both are free.

§0 of `STAGE2_PROMPT.md` remains in force.

- **Part A (§1–§2): zero trials.** Paired bootstrap on the cap's effect.
  Gates everything after it.
- **Part B (§3–§5): zero trials.** Pre-register the validate pass/fail rule.
  Written before the run, never after.
- **Part C (§6): the last trial.** Only if Part A passes and Part B is
  recorded.

---

# PART A — Is the cap's improvement real? (zero trials)

## 1. The test that has not been run

§25.1 reports capped Sharpe 1.059 with a 90% CI of [+0.23, +1.85]. The
uncapped figure is 0.796.

**0.796 sits comfortably inside that interval.** The capped run alone
therefore cannot reject "the cap did nothing."

But that is the wrong test. Capped and uncapped ran on **the same days**, so
the market noise they share cancels in the difference. A paired bootstrap on
the *difference* series is far tighter than either standalone CI, because it
removes common variance. Both daily series already exist. No trial.

### 1.1 Method

1. Align capped and uncapped **daily** PnL series over the identical train
   window. Assert the dates match exactly — if they don't, stop and report.
2. Form `d_t = pnl_capped(t) − pnl_uncapped(t)`.
3. Stationary bootstrap `d_t` with the same block-length rule as §24.1
   (state the length; the lag-1 autocorrelation of the *difference* series
   may differ from the level series, so recompute it rather than reusing
   −0.0136).
4. Report 90% CIs on: mean daily difference; annualised return difference;
   **Sharpe difference**.

Resample the difference series directly. Do **not** bootstrap the two runs
independently and subtract point estimates — that reintroduces the common
noise this test exists to remove.

### 1.2 Also report

- Paired difference restricted to **2021 and 2022 only** (excluding 2023).
  §25.1's improvement is **85% from 2023 alone**, and 2020 is inert because
  the cap never binds. If the effect exists only in 2023, it rests on one
  year, which is the third time this project has produced a headline
  concentrated in a single window.
- Paired difference in **2023 alone**, for comparison.
- Fraction of days where `d_t ≠ 0` — the cap binds on 63/80/100% of days by
  year, so a large share of days contribute nothing and the effective sample
  is smaller than the day count suggests.

## 2. The reading — write into NOTES §26 before computing

| If... | Then |
|---|---|
| Sharpe-difference CI **entirely above zero** | The cap's effect is established. Proceed to Part B |
| CI **straddles zero** | Not established. **Stop.** Do not spend the last trial validating the cap. Report and reassess |
| CI above zero **only when 2023 is included**, and the 2021–22 subset straddles zero | Weak — the effect rests on one year. **Stop and report**; the decision moves to the user |

Do not adjust these after seeing intervals.

**Confound to state alongside the result, not to test around:** the cap binds
100% of days in 2023 *and* 2023 has the largest `101+` share (22.1%).
Removing the tail necessarily helps most where the tail is largest. So 2023's
dominance is the mechanism restated, not independent evidence for it.

---

# PART B — Pre-register the validate rule (zero trials)

## 3. Why this must be written first

The project's own sequence is freeze → validate → **predefined rules** →
holdout. Those rules have never been written.

One year of validate at Sharpe ~1.0 gives a t-statistic of about 1.0.
**Almost any outcome will be "consistent with" something.** That is precisely
the condition under which a criterion gets invented to fit whatever came back.

Write §27 before the run. Once written, do not modify it.

## 4. What the rule must specify

State each as a number or a yes/no, decided now:

1. **Minimum validate Sharpe** to justify spending the holdout. Note the
   pre-registered stop threshold elsewhere in this project is 0.3; state
   whether that applies here and why.
2. **Sign requirement on price PnL.** §25.3's central claim is that the cap
   shifted the return source from carry toward momentum. If validate price
   PnL is negative, that claim fails regardless of headline Sharpe.
3. **Composition requirement.** Capped train ran 73% price / 27% funding.
   State the tolerance — how far can validate drift before the mechanism is
   considered different?
4. **Drawdown limit.** Capped train maxDD was 25.64% against a 30% kill
   switch. State whether a validate breach of 30% is an automatic stop.
5. **Active-days floor.** Grid v2's 1.65 came from 72 active days of 1,342.
   State the minimum active-day fraction below which the validate Sharpe is
   not interpretable at all.
6. **What failure means.** If validate fails, does the project stop, or does
   it return to research with the holdout still unspent? Decide now.

## 5. Expectations to record before the run

Not pass/fail criteria — context, so the result is read honestly:

- **2022 got slightly worse under the cap** (0.05 → −0.04). The cap is not
  uniformly beneficial. A weak validate year is not automatically a
  refutation.
- 2024 is a single year: `t ≈ SR × √1`. Even a true Sharpe of 1.0 produces a
  t-stat near 1.0. **Validate cannot confirm; it can only refute.** Say so in
  §27 so the result is not over-read in either direction.
- Documented carry decayed from 2024 onward. Since capped train is 27%
  funding, expect the funding component to be weaker in validate than in
  train, and do not treat that alone as failure.

---

# PART C — The last trial

## 6. Conditions

Run validate **only if**:

- Part A returned branch one, **and**
- §27 is written, dated, and committed before the run

Configuration: the capped frozen config, unchanged. `lookback=14`, `skip=0`,
$400, N=10, rank-100 cap, 20% vol target, 3× cap, taker-only, 5bps, +1min.
Slippage {0,5} × delay {0,1} as cost sensitivity, reported together —
**7 → 8 trials, not 7 → 11.**

Log the trial before running. If it errors, the trial is still spent.

Report everything from `STAGE2_PROMPT.md` §6 plus the per-year table in the
§19.3/§25.1 format, paired-bootstrap CI on the headline Sharpe, and each §4
criterion evaluated explicitly as pass or fail.

Then answer again, in writing: **who is paying, and why would they keep
paying?** The answer has been corrected twice already (§18.2 → §22.3 →
§25.3). If validate changes the composition, correct it a third time.

**After this, zero trials remain.** The holdout look is the only thing left,
and it is one look, ever, with no tuning after.

---

## 7. Order of work

1. §2 reading into `NOTES` §26, dated, **before** computing
2. Part A paired bootstrap; report all quantities in §1.1 and §1.2
3. State which branch fired
4. **If branch two or three: STOP. Report. Spend nothing.**
5. If branch one: write §27 per §4 and §5, dated and committed
6. **Stop and report before running validate.** Do not write §27 and run
   validate in the same session — the whole value of a pre-registered rule is
   that it exists before the result, and same-session means nobody can verify
   the order
7. On user go-ahead: log trial 8, run validate, report per §6

## 8. Acceptance

- §26 reading recorded before any interval computed
- Paired bootstrap on the **difference** series, not two independent runs
- Block length recomputed for the difference series, stated
- CIs for full window, 2021–22 subset, and 2023 alone
- Fraction of non-zero difference days reported
- Branch stated plainly, including if the evidence is weaker than the label
- §27 written, dated, committed **in a separate commit from any validate run**
- Trial budget still **7 of 20** at the end of this document

## 9. Do not

- Bootstrap capped and uncapped independently and subtract
- Adjust §26 or §27 after seeing any result
- Run validate in the same session §27 is written
- Try a second cap value, or repair the ranking-basis discrepancy in §25.2
- Touch the holdout

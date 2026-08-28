# Stage 8 — Validate B at 10% / $800 on 2024 (ONE trial)

The first clean out-of-sample look at the honestly-derived deployment config.
Config is **pre-chosen and locked**: B, top-15 majors, N=10, k=5, **10% vol**,
**$800**. 2024 is a pass/fail check, not a vol comparison.

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 0. The rule override — record this in NOTES §44 before the run

§43's rule selected **12%** as "highest qualifying vol." The deployment config
is **10%**, one step below. This is a deliberate override of a pre-registered
rule, and it is legitimate **only** for reasons that must be recorded before
the validate, or it looks like fitting:

1. **The override goes toward LESS return, not more.** 10% earns less than 12%
   (train Sharpe 0.651 vs 0.882). Overriding a rule to reduce profit for
   safety is the opposite direction from fitting, which always reaches for more.
2. **The ground is measurement instability the sweep itself found.** §43
   qualification 1: drift is non-monotonic (22/31/26 across 10/11/12%),
   ±~5 points of floor noise. 12%'s qualifying margins — drawdown 0.53pt, drift
   4pt to the cap — are both *inside* that noise. 12% could fail its own
   criteria on a different day-set. 10% clears both caps by 3–4 points, outside
   the noise.
3. **It is recorded now, before 2024 is seen.** Not chosen after a result.
4. **It avoids repeating a known failure.** §41: a boundary-hugging config
   (14% at $400) passed on paper and failed OOS. 12% hugs two boundaries. The
   entire vol investigation existed to escape boundary-hugging; deploying
   another cap-hugger would repeat the error with open eyes.

Record all four. State explicitly that 12% is the aggressive alternative and
was **not** chosen, so the decision is auditable.

---

## 1. Config and run

B at **10% vol target, $800 capital**: top-15 PIT majors, `lookback=14`,
`skip=0`, N=10, k=5, 3× cap, beta-neutral, +1min fill, 5bps slippage. Both fee
schedules (USDT / USDC), reported together.

Window: **2024 only.** Holdout (2025-01 → 2026-07) stays sealed.

Cost: **ONE trial.** Budget 11 → 12 of 25. Log before running; an errored run
still spends it.

---

## 2. The rule — write into NOTES §44 before running

Same three hard gates as §37/§40, applied to the 10%/$800 config.

### 2.1 Tier 1 — hard gates, any one failing = refuted

| # | Test | Refuted if |
|---|---|---|
| G1 | Price PnL sign | 2024 price PnL **< 0** |
| G2 | Drawdown | max DD **> 30%** (USDT run). Train at 10%/$800 was 17.03%, so a large breach signals a bug |
| G3 | Sharpe floor | Sharpe **< 0.30** at USDC fees |

### 2.2 The success band — lower than B@20%, and here is why

The demeaned (drift-stripped) train Sharpe at 10% is the real-edge estimate.
Train Sharpe 0.651 with 22% drift → real edge ≈ 0.651 × 0.78 ≈ **0.51**.

**Success band: 2024 Sharpe ~0.4–0.65.** This is *lower* than B@20%'s 0.5–0.7
band because 10% vol sizes smaller and earns less in absolute and risk-adjusted
terms once drift is removed. **Judge Sharpe, not return** — the absolute return
will be visibly smaller than any prior config and that is by design, not
weakness. A 2024 Sharpe of 0.4–0.65 is a PASS consistent with the edge
surviving.

### 2.3 The mechanism checks — this config's specific risks

§41 and §42 showed the floor can pass hard gates while corrupting the
mechanism. Both checks that caught it must run here:

- **Drift fraction on 2024.** Train at 10%/$800 was 22%. If 2024 drift is far
  above that (say > 40%), the OOS edge is mostly artifact — a caveat on any
  pass, as it was the disqualifier in §41.
- **Skip rate on 2024** vs the train 21.55%. A large jump means the floor bit
  harder OOS.
- **Realised vol vs 10% target**, **active-days fraction ≥ 80%**, structural
  invariants (beta within ±0.15, dollar-tilt to 1e-9).

## 3. The reading

| Outcome | Meaning |
|---|---|
| Tier 1 pass, Sharpe 0.4–0.65, drift ≤ ~30%, floor clean | **The deployment config survives OOS cleanly.** The holdout becomes worth spending on a config that is survivable, mechanically clean, and honestly derived |
| Tier 1 pass but drift > 40% or skips ≫ train | Passes gates, floor-contaminated OOS — same trap as §41. Record as a serious caveat; not holdout-ready |
| G1 fails (price PnL < 0) | Momentum did not survive at this size. Refuted |
| G2 fails (DD > 30%) | Contradicts train sizing — investigate for a bug |
| G3 fails (Sharpe < 0.30) | Too weak to carry to holdout |

Nothing adjusted after seeing 2024.

## 4. After — stop, holdout deferred

Whatever 2024 shows, **stop at the report.** The holdout is one look, ever, and
gets its own decision with a clear head (§37.5). A clean 2024 here means the
holdout would finally test a config that is validated at its own vol and
capital — the cleanest holdout setup the project has been able to offer. But
that decision is the user's, made separately.

State: gates passed/failed, whether the drift-adjusted read is positive, floor
behaviour vs train, and that the holdout decision is deferred.

## 5. Order of work

1. §0 override rationale and §2 rule into `NOTES` §44, dated, committed before
   the run
2. Log trial 12
3. Run B at 10%/$800 on 2024, both fees
4. Grade gates; report §2.3 mechanism checks and the per-2024 breakdown
5. **Stop.** Holdout sealed, decision deferred

## 6. Acceptance

- §44 records the four-point override rationale before the run
- §44 rule committed before the run
- Trial logged; budget 11 → 12 of 25
- Three gates graded, both fee schedules
- Success band 0.4–0.65 applied; Sharpe judged not return
- 2024 drift fraction and skip rate reported vs train
- Holdout untouched; decision deferred to the user

## 7. Do not

- Frame this as 10% vs 12% on 2024 — 12% was declined on train, on the record
- Demand a Sharpe near B@20%'s band — 10% earns less by design
- Penalise the lower absolute return
- Adjust any §44 threshold after seeing the result
- Chain into the holdout this session
- Touch the holdout

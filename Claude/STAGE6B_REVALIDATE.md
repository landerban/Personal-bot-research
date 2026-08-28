# Stage 6b — Re-validate B at 14% on 2024 (ONE trial)

Confirms the **pre-chosen** 14% deployment config survives out of sample. The
vol is already locked by the Stage 6a train rule — 2024 is a pass/fail check on
that config, **not** a comparison against 20%.

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 0. The one thing that keeps this honest

2024 was already used once (Stage 6: B at 20%, Sharpe 0.675, passed). This is a
**second look at the same year**. The discipline that makes it legitimate:

- The deployment vol is **already 14%**, fixed by the §39 train drawdown rule
  before this run. It is not being chosen here.
- 2024 answers exactly one question: **does the 14% config clear the same
  pre-registered gates the 20% config cleared?**
- The 20% result is **reference only**. This is not "14% vs 20%, keep the
  winner." Selecting the vol by 2024 performance would turn the validation set
  into a selection set — the precise error avoided all project long.
- 14% must pass **on its own**. "Beats 20% on 2024" is not a criterion and must
  not be reported as one.

If at any point the framing drifts toward comparing the two vols to pick one,
stop — the vol is chosen; only its OOS survival is in question.

---

## 1. Config and run

B at **14% vol target**, top-15 PIT majors, `lookback=14`, `skip=0`, N=10, k=5,
3× cap, beta-neutral, +1min fill, 5bps slippage, $400. Both fee schedules
(USDT / USDC), reported together.

Window: **2024 only.** Holdout (2025-01 → 2026-07) stays sealed.

Cost: **ONE trial.** Budget 10 → 11 of 25. Log before running; an errored run
still spends it.

---

## 2. The rule — write into NOTES §40 before running

Same three gates as Stage 6 §37, same drift adjustment, applied to the 14%
config.

### 2.1 Tier 1 — hard gates, any one failing = refuted

| # | Test | Refuted if |
|---|---|---|
| G1 | Price PnL sign | 2024 price PnL **< 0** |
| G2 | Drawdown | max DD **> 30%** (USDT run) — should pass easily; train DD at 14% was 14.78% |
| G3 | Sharpe floor | Sharpe **< 0.30** at USDC fees |

### 2.2 The drift-adjusted band — unchanged from Stage 6

41% of B's train Sharpe is drift and does not repeat. Train Sharpe at 14% is
not the reference (the §39.7 invariance failure means the sweep's Sharpe column
is not clean) — use the **20% train Sharpe of 1.114** as the edge estimate,
since edge is the size-independent quantity:

```
1.114 × (1 − 0.41) ≈ 0.66     drift-adjusted success expectation
```

A 2024 Sharpe of **0.5–0.7 is consistent with success**, exactly as in Stage 6.
Do not demand more. The 14% run's realised vol (~13%) means its *absolute*
return will be lower than the 20% run's — that is expected and not a mark
against it. **Judge Sharpe, not return.**

### 2.3 The floor check — specific to this config

§39.7 showed vol targeting is not a clean rescale at $400 — the floor flips
whole rebalances. So 2024 at 14% must confirm the book actually functions:

- skip rate vs the 20% 2024 run
- realised vol vs the 14% target (train fell ~1.1 points short)
- names dropped under `MIN_NOTIONAL`
- active-days fraction ≥ 80%

If the 14% config skips materially more of 2024 than the 20% config did, the
floor is distorting it OOS and that is a **caveat on any pass**, recorded
explicitly.

## 3. The reading

| Outcome | Meaning |
|---|---|
| All Tier 1 pass, Sharpe 0.5–0.7, price-driven, floor clean | 14% deployment config survives OOS. Holdout may test the config actually intended for live use |
| All Tier 1 pass but floor skips ≫ 20% run | Passes, but the floor contaminates it OOS — deployment carries that limitation; record it |
| G1 fails (price PnL < 0) | The 14% config does not survive. Since 20% passed and 14% did not, the difference is floor-driven day-selection, not edge — decisive against deploying 14% |
| G2 fails (DD > 30%) | Would contradict the train sizing entirely — investigate for a bug before accepting |
| G3 fails (Sharpe < 0.3) | Too weak to carry to holdout at this vol |

Nothing adjusted after seeing 2024.

## 4. After — stop, holdout still deferred

Whatever the result, **stop at the report.** The holdout is a separate decision
with a clear head, per Stage 6 §4. A 14% pass does not auto-license the holdout;
it removes the §39.9 tension (deployment config now has OOS evidence) and makes
the holdout decision cleaner, nothing more.

State: gates passed/failed, whether the drift-adjusted read is positive, floor
behaviour vs the 20% run, and that the holdout decision is deferred.

## 5. Order of work

1. §2 rule into `NOTES` §40, dated, committed before the run
2. Log trial 11
3. Run B at 14% on 2024, both fees
4. Grade gates; report §2.3 floor diagnostics and the per-2024 breakdown
5. **Stop.** Holdout sealed, decision deferred

## 6. Acceptance

- §40 committed before the run
- Trial logged; budget 10 → 11 of 25
- Three gates graded, both fee schedules
- Drift-adjusted band applied (0.5–0.7 success), Sharpe judged not return
- Floor diagnostics vs the 20% 2024 run reported
- 20% result used as reference only — no "14% beats 20%" framing anywhere
- Holdout untouched; decision deferred to the user

## 7. Do not

- Frame this as choosing between 14% and 20% on 2024
- Demand a Sharpe near 1.1, or penalise the lower absolute return
- Adjust any §40 threshold after seeing the result
- Chain into the holdout this session
- Touch the holdout

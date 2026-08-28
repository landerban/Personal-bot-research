# Stage 7 — Does the 14% config heal at $800? (train, zero trials)

The gate on the whole $800 plan. Re-run B's 14% config at $800 capital on
**train** and check whether the floor contamination that broke it at $400
disappears. Free — capital is a config input, so this is a re-sizing on data
already used, not a new strategy or a new trial.

§0 of `STAGE2_PROMPT.md` remains in force. Budget stays **11 of 25**; validate
(2024) not touched; holdout sealed.

---

## 0. Why this is free and why it gates everything

Capital is a `Config` value. Changing $400 → $800 rescales position sizes; it
does not change the signal, universe, or which names rank. So this consumes no
trial — it is the same risk-sizing diagnostic family as the vol sweep.

It gates everything because **the 14% config has never had a clean run at any
capital.** Every prior look was at $400, where §41.2 showed it skipped 51.8% of
2024 rebalances and §41.3 showed its return was 126% drift. At $800 it is a
different book — one that may trade every day. Whether it heals is the
precondition for spending the holdout or any real money. If it does not heal,
$800 is not the answer and that must be known before funding.

---

## 1. The run

B config at **$800 capital**: top-15 PIT majors, `lookback=14`, `skip=0`, N=10,
k=5, **14% vol target**, 3× cap, beta-neutral, +1min fill, 5bps slippage, USDT
fees (and USDC for reference). Window: **train 2020–2023.**

For comparison in the same table, cite (do not re-run):
- B @ 14% @ $400 on train (§39.6) — the broken reference
- B @ 20% @ $400 on train (§34) — the clean-mechanism reference

## 2. The reading — write into NOTES §42 before running

The question is **mechanism repair**, not performance. Sharpe is not the test
(and per §39.7 is not even clean across sizes). The tests, each pre-registered:

| Check | Broken ($400/14%) | Healthy target | Pass if |
|---|---|---|---|
| **Skip rate** | 51.8% (2024) / high on train | 20%-config ~0.3–5% | train skip rate falls near the 20% config's, not the $400/14% run's |
| **Drift fraction** | 126% (2024) | ~18–24% | demeaned run stays profitable; drift is a *minority* of Sharpe, not >100% |
| **Realised vol vs target** | ~1.1pt short, floor-capped | hits 14% | realised vol reaches ~14%, i.e. the floor no longer caps size |
| **Smallest position** | ~$7, under-floor days | comfortably > $5 | median and p05 position notional clear the floor with margin |
| **Rebalance count** | non-monotonic, floor-driven | full ~1380 | traded on nearly all scheduled days |

### 2.1 The decisive one

**Drift fraction is the disqualifier.** If the $800/14% book still shows drift
as a majority of Sharpe, it is still mostly holding rather than trading, and
$800 has not fixed it — the floor bites harder than the capital doubling
relieves. Conversely, if drift returns to the ~18–24% range the clean 20%
config showed, the mechanism has healed and the config is genuinely deployable
at $800.

Write the pass/fail for drift as: **demeaned Sharpe > 0 AND drift fraction
< 50%.** Below 30% is clean; 30–50% is healed-but-watch; ≥ 50% is unhealed.

## 3. The reading table

| Outcome | Meaning |
|---|---|
| Skip rate low, drift < 30%, realised vol ~14% | **$800 heals it.** The deployment config is confirmed on train. Holdout becomes worth spending on this config |
| Drift 30–50%, skips moderate | Partially healed. $800 is marginal; consider whether $1,000–1,200 is the real line before committing |
| Drift ≥ 50% or skips still high | **$800 does not heal it.** N=10 at 14% needs more than $800. Report the capital at which the smallest position clears the floor with margin, as the revised target |

## 4. If it heals — what comes next (do not do now)

State the path, do not execute it:

1. **One clean validate** of the $800/14% config on 2024 — a trial, because the
   $400 validate (§41) measured a different, broken book. This is the config's
   first honest out-of-sample look.
2. **Then** the holdout decision, with a config that is finally both survivable
   (14% vol, ~8% DD) and mechanically clean (drift in range).
3. Paper-trade at $800 for real fill rates before live, especially for USDC
   maker.

Budget note: that validate would be trial 12 of 25. The holdout remains the one
irreplaceable look and is not spent by this stage.

## 5. If it does not heal

State the revised capital target from §3, and stop. The honest finding becomes
"the validated strategy needs ~$X to run at its survivable vol," where $X is
computed from the floor, not guessed. No config is shrunk to force $800 to
work — that would be re-entering the compression cycle §41.5 named.

## 6. Order of work

1. §2 reading and §2.1 drift pass/fail into `NOTES` §42, dated, before running
2. Run B @ $800 @ 14% on train; cite the two $400 references
3. Report the §2 table and the §3 outcome
4. State plainly: healed / marginal / unhealed, and the next step or revised
   capital target
5. **Stop.** Spend no trial. Holdout sealed.

## 7. Acceptance

- §42 reading and drift pass/fail recorded before the run
- $800/14% train run reported against both $400 references in one table
- All five §2 checks reported; drift fraction is explicit
- Verdict stated: healed / marginal / unhealed
- If unhealed, revised capital target computed from the floor
- Budget **11 of 25**; 2024 not re-run; holdout untouched

## 8. Do not

- Log the re-sizing as a trial (capital is a risk-sizing config value)
- Judge the run by Sharpe instead of the mechanism checks
- Shrink N or vol to force $800 to work — report the true capital line instead
- Run the $800 validate on 2024 in this stage (it is the next step, a trial)
- Touch the holdout

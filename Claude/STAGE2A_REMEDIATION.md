# Stage 2a — Remediation

Amendment to `STAGE2_PROMPT.md`, following review of `NOTES.md`. Read both
before acting. Where this document and `STAGE2_PROMPT.md` conflict, **this
document wins**, and the conflict is itself noted below.

Your `NOTES.md` was correct on the substance. Two of the items below are
corrections to *my* spec, not to your implementation.

---

## 0. Scope discipline

This is a targeted remediation, not a refactor. The §0 rules from
`STAGE2_PROMPT.md` remain in force: constraints are derived, disagreements go
in `NOTES.md` rather than into code, and the holdout stays untouched.

**Explicitly do NOT change the following.** Each was reviewed and confirmed
correct; changing it would be a regression:

| Thing | Why it stays |
|---|---|
| Leg-scaling beta hedge | Verified correct — see §1 |
| Sample covariance, no shrinkage | Weights aren't derived from Σ̂, so `w'Σ̂w` is unbiased for fixed `w`. Shrinkage adds a parameter and buys nothing |
| Funding sign and settlement conventions | Correct; the mark-price approximation is ~2e-6 per settlement, immaterial |
| 365-day annualisation | Correct for perps |
| Holdout logged before the run | Better than specified; keep |
| Candidate filter stricter than universe filter | Correct — 60 returns need 61 closes |
| `gross_leverage = max_gross_leverage` in the universe filter | **Measure before changing** — see §4 |
| Anything in `pitdata/` | Stage 1 is frozen |

If you believe any row above is wrong, write it in `NOTES.md`. Do not act.

---

## 1. RULING on NOTES §1 — the spec was wrong, your code was right

You identified a genuine contradiction. I verified it independently: across
2000 random draws, **zero** cases satisfy both exact dollar-neutrality and
exact beta-neutrality under a single leg scale, with the net dollar tilt
averaging 19% of leg gross. Two constraints, one free parameter. Scaling both
legs doesn't help either, since dollar-neutrality forces the scales equal and
net beta then vanishes only if the leg betas match exactly.

I also tested the alternative I would have proposed — projecting the weight
vector onto the null space of `[1, β]`. It satisfies both constraints exactly
and **violates the `[0.5×, 1.5×]` weight band in 100% of cases**, with the
smallest weight collapsing to zero. That is strictly worse than a tilt.

Your reading is also the economically correct one, which the spec should have
said: dollar-neutrality was only ever a cheap proxy for market-neutrality.
Once beta-neutrality is genuine, the proxy is redundant, and enforcing it
anyway is an extra constraint with no risk justification.

**Action: keep the implementation. Change only documentation.**

1. Amend the §2.3.5 / §2.4 discussion in `NOTES.md` from "DISAGREEMENT" to
   "RESOLVED — spec amended", recording that the tilt `g·(1−s)` is the hedge
   by design and not a defect.
2. Add **Test 12** (§5 below) pinning this behaviour so a future change cannot
   silently restore exact dollar neutrality and break the hedge.

---

## 2. NOTES §2 — correct diagnosis, missing consequence

The shuffled-returns analysis is right: a time-shuffle preserves each series'
sample mean exactly, each symbol keeps its full-sample drift as a permanent
property, and trailing returns are a noisy estimator of that drift. The test
was wrong; the harness was right.

**But the consequence extends to the real data, and this is the important
part.** If cross-sectional momentum harvests persistent drift differences in
shuffled data, it is doing the same thing in real data. Some unknown fraction
of whatever Sharpe the grid produces is drift-estimation rather than
trend-continuation. These have different payers and — critically — different
decay profiles. Trend-continuation has a behavioural story with identifiable
counterparties. Drift-harvesting in a mostly-rising sample is closer to
disguised beta and will not survive a drift reversal.

Every payer listed in `NOTES.md` §12 is a trend-continuation story. If the
edge is substantially drift, none of them explain it.

### 2.1 Required: drift/trend decomposition

Run the best grid config twice on **train only**:

- **(a)** real data → `Sharpe_real`
- **(b)** real data with each symbol's returns demeaned by its full-sample
  mean → `Sharpe_demeaned`

`Sharpe_real − Sharpe_demeaned` estimates the drift component.

**Implementation constraint — read carefully.** Build a *separate demeaned
database* and run the unmodified engine against it. Do **not** add a
transform hook to the engine. Hooks that alter returns get accidentally
enabled, and this one would be invisible in the output.

Write `tools/build_demeaned_db.py` producing `xsmom_demeaned.db`. Adjust
`close` prices so daily log returns are demeaned per symbol; leave volumes,
funding, and timestamps untouched so universe construction is unchanged.

**This diagnostic uses full-sample means and therefore could not be run
live.** Label it as such everywhere it appears. It is attribution of an
existing result, not a new configuration, so it does **not** consume trial
budget — but log it to a separate `diagnostics.jsonl`, never `trials.jsonl`.

### 2.2 Required: update NOTES §12

Add drift-harvesting as a candidate explanation with **no identifiable
payer**, and state the decision rule: if the decomposition attributes a
majority of the Sharpe to drift, the strategy is closer to disguised beta than
to momentum, and that must be stated plainly in the results rather than
buried.

---

## 3. Null test power

Your seed-variance correction is right and my `|SR| < 1` assertion was naive.
But 5 seeds gives a 2 SE bound of ≈0.81, which is weak enough to shrug at a
real leak.

**Action:** raise both null tests (random signal, demeaned shuffle) to **30
seeds**. Synthetic runs are free. The bound tightens to ≈0.33.

Keep the loose per-seed guard (`|SR| < 3`) — it catches gross mechanical leaks
without waiting for the full sweep.

Report for each null test: mean Sharpe across seeds, SE of the mean, t-stat,
and the per-seed min/max.

---

## 4. NOTES §7 — measure, then decide

The universe filter uses `gross_leverage = max_gross_leverage = 3.0`, which is
a fair literal reading of §2.1. It creates a failure mode worth quantifying
before changing anything.

The filter admits symbols assuming 3x gross. Vol-targeting then sets *actual*
gross leverage, and a beta-neutral crypto book hitting 20% vol will often land
near or below 1.0x. With the `[0.5×, 1.5×]` band, the smallest position is
`0.5 × L × C / N` — so at $100 and N=10, **any realised `L` below 1.0 puts
positions under the $5 floor** and skips the whole rebalance.

**Do not change the filter yet.** Instrument it:

1. Record realised gross leverage at every rebalance; report min, median, p05,
   p95, and the fraction of rebalances below 1.0x.
2. Report skip counts **by reason**, with `below_min_notional` called out.
3. Record the minimum position notional actually taken across the run, and
   compare it to the binding `MIN_NOTIONAL`.

If `below_min_notional` skips are rare, leave it. If material, we change the
filter to use expected rather than maximum leverage — but that decision needs
the numbers.

### 4.1 Correction to my README

`C ≥ 20N/L` is wrong. It applied a 0.5× rank factor *and* a 0.5× vol factor,
but vol scaling is already inside `L`. The correct form is:

```
C >= 10N / L
```

It gave the same $100 answer at N=10, L=2 by coincidence, which is how the
error survived. Fix the README, and note that the relevant `L` is **realised**
leverage, not the configured cap.

---

## 5. New tests

Add to `tests/test_backtest.py`, keeping all existing tests:

**Test 12 — the dollar tilt is the hedge, not a bug.**
At every rebalance assert `sum(final_weights) ≈ g·(1 − s)` to 1e-9, where `g`
is post-vol-target gross and `s` the short-leg scale. Fails if anyone
reintroduces exact dollar-neutrality on the final book. Test 7 continues to
assert on `raw_weights`.

**Test 13 — demeaned database is faithful.**
Given a synthetic store, assert `build_demeaned_db` produces per-symbol mean
log return ≈ 0 (< 1e-10) while leaving volume, funding, timestamps, and the
resulting universe membership byte-identical to the source. If the universe
changes, the decomposition compares two different strategies.

**Test 14 — realised leverage is recorded.**
Assert every non-skipped rebalance emits a finite realised gross leverage and
that it never exceeds `max_gross_leverage`.

---

## 6. Reporting additions

Append to the per-run report from `STAGE2_PROMPT.md` §6:

- Realised gross leverage: min / p05 / median / p95 / max, and fraction below 1.0x
- Skip counts by reason, as counts and as a fraction of scheduled rebalances
- Minimum position notional taken, versus binding `MIN_NOTIONAL`
- `forced_liquidations` and `missing_funding_settlements` counts
- Long-leg vs short-leg PnL split (needed to argue §12 at all)

And for the best config only, the decomposition from §2.1:

```
Sharpe (real)      : X.XX
Sharpe (demeaned)  : X.XX
Drift component    : X.XX  (YY% of total)
NOTE: diagnostic only -- uses full-sample means, not runnable live.
```

---

## 7. Order of work

1. Re-run `tests/test_lookahead.py` (must be 13/13) and the existing Stage 2
   suite. Record the baseline before touching anything.
2. Documentation fixes: `NOTES.md` §1 reclassification, README `C ≥ 10N/L`.
   No code.
3. Tests 12 and 14, plus the leverage/skip instrumentation. Re-run everything.
4. Null tests to 30 seeds. Re-run. **If either null test now fails at 30 seeds
   when it passed at 5, stop and report** — that is the tighter bound doing
   its job, and nothing downstream is trustworthy until it's understood.
5. `tools/build_demeaned_db.py` and Test 13.
6. Re-run the full grid on **train** with the new reporting.
7. Run the §2.1 decomposition on the best config.
8. Report. Do not touch validate or holdout.

## 8. Acceptance

- `tests/test_lookahead.py` 13/13
- Stage 2 suite green including Tests 12, 13, 14
- Both null tests: mean Sharpe within 2 SE of zero across 30 seeds
- Grid re-run with full reporting; every run in `trials.jsonl` with commit hash
- Decomposition in `diagnostics.jsonl`, not `trials.jsonl`
- `NOTES.md` updated: §1 resolved, §12 extended, plus anything new you disagree with

If any null test fails, stop at that point and report. A harness that
manufactures edge makes every downstream number meaningless, and the grid can
wait.

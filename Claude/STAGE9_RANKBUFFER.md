# Stage 9 — Rank buffer (turnover reduction) on the deployment config

The last open **strategy** question before the holdout: does a rank buffer cut
turnover enough to lift net returns, or does lower turnover revert the book to
drift-harvesting? Train comparison against the frozen 10%/$800 config,
pre-registered, with the same mechanism checks that caught §41.

§0 of `STAGE2_PROMPT.md` remains in force. Holdout stays sealed throughout.

---

## 0. Why this, why now

Fee drag is **27% (USDC) to 37% (USDT) of price PnL** on the deployment config
— the dominant friction since the project began. §32.4 showed **68% of
turnover is boundary-crossing** (names entering/leaving at the rank-5 edge),
which is exactly what a buffer softens. The rank buffer has been deferred since
Stage 3 as "the best turnover idea." This is the moment to test it: after the
config is otherwise frozen, before the holdout is spent, so that if it improves
the config, *it* becomes the deploy candidate and the holdout tests the better
version.

If it does not help, the last strategy question is closed and the current
config stands unchanged.

---

## 1. The mechanism

Current rule: a name is held if it is in the top-k (long) or bottom-k (short)
by momentum rank, exited the moment it leaves. A small rank wobble at the k
boundary triggers a full round trip.

Buffer rule (hysteresis): **enter** at rank ≤ k, **exit** only at rank > k + b,
where `b` is the buffer width. A name already held is retained until it falls
meaningfully past the boundary. Same on both legs.

This is a construction change to the weight/selection step only. Beta hedge,
vol target, floor handling, everything else unchanged.

## 2. Pre-register the buffer widths — from arithmetic, before running

`b` must be chosen from the universe geometry, **not** from which value
performs best. The B universe is top-15 majors, k=5 (long top 5, short bottom
5). Candidate buffers:

- **b = 1** — exit at rank > 6. Minimal hysteresis.
- **b = 2** — exit at rank > 7. Moderate.
- **b = 3** — exit at rank > 8. The §3 example from the original deferral.

Beyond b=3, with only 15 names and k=5, the long-hold and short-hold zones
begin to overlap in the middle of the book (rank 8 from the top is rank 8 from
the bottom in a 15-name universe) — so **b ≤ 3 is the feasible ceiling** and
must be stated as a geometric constraint, not a tuning choice.

Write into `NOTES` §45 before running: the three buffers, the geometric ceiling,
and the selection rule in §4.

## 3. Trial accounting

Three buffers = **3 trials** (b=1,2,3), each at both fee schedules reported
together. The frozen b=0 config already exists (§44) and is the baseline — not
re-run.

Budget **12 → 15 of 25.** Log each before running. This stays inside the
expanded budget; no further expansion.

## 4. The selection rule — fixed before running

A buffer **wins** and becomes the new deploy candidate only if, on train:

1. **Net Sharpe improves** over b=0's train Sharpe (0.651) by a paired-bootstrap
   margin whose 90% CI **excludes zero** — same paired method as §5 (buffers and
   baseline run on the same days, so common noise cancels). A point-estimate
   improvement is not enough; the last three "improvements" that could not clear
   a paired CI were correctly not adopted.
2. **Turnover actually falls** — the mechanism must do what it claims. Report
   the boundary-crossing vs adjustment split; the boundary-crossing share should
   drop.
3. **Mechanism stays clean** — drift fraction < 30% and demeaned Sharpe > 0. A
   buffer holds names longer, and §41 showed that *holding* is how a book
   reverts to drift-harvesting. **This is the specific failure mode to watch:** a
   buffer could lift Sharpe by holding drifting names, which is not tradeable
   edge. If drift rises toward the §41 pattern as `b` increases, that is
   disqualifying regardless of Sharpe.

If multiple buffers pass all three, take the **smallest** `b` that does —
minimal intervention, least overfit surface. Not the highest Sharpe.

If none passes, **b=0 stands** and the deployment config is unchanged.

## 5. What to report per buffer

Against the b=0 baseline, on train:

- Net Sharpe, price PnL, funding PnL, each with paired-bootstrap 90% CI vs b=0
- Turnover multiple and boundary/adjustment split (vs b=0's 68% boundary)
- Fee drag % of price PnL, both fee schedules
- **Drift fraction and demeaned Sharpe** — the §41 disqualifier
- Skip rate and realised vol (a buffer changes which names are held, which can
  change floor interaction — confirm it does not re-break)
- Max drawdown (holding longer can deepen drawdowns — check against the 20% cap
  the vol was chosen under)

## 6. The reading — fixed before running

| Outcome | Meaning |
|---|---|
| A buffer improves net Sharpe (paired CI excludes 0), turnover falls, drift stays < 30% | The buffer is a real improvement. Smallest passing `b` becomes the deploy candidate; it must then be validated on 2024 before the holdout — a new config needs its own OOS look |
| Buffer lifts Sharpe but drift rises toward §41 | The gain is drift-harvesting from holding, not turnover saving. **Reject** — this is the trap §41 established |
| Turnover falls but net Sharpe CI includes zero | The fee saving does not survive the noise. b=0 stands; the buffer is not worth the added parameter |
| No buffer passes | b=0 is the deployment config. The last strategy question is closed |

Nothing adjusted after seeing results.

## 7. If a buffer wins — the consequence

A winning buffer is a **new config** and cannot inherit b=0's 2024 validation.
It would need its own single validate on 2024 (a further trial, 15 → 16) before
being holdout-eligible. State this; do not run that validate in this stage. The
holdout stays sealed regardless of outcome.

## 8. Order of work

1. §2 buffers, geometric ceiling, and §4 rule into `NOTES` §45, dated, before
   running
2. Log trials 13, 14, 15; run b=1,2,3 on train, both fees
3. Paired bootstraps vs b=0; the §5 table per buffer
4. Apply §4: smallest `b` passing all three, or b=0 stands
5. State the outcome and, if a buffer won, that it needs its own 2024 validate
   next
6. **Stop.** Holdout sealed.

## 9. Acceptance

- §45 records buffers, geometric ceiling, and selection rule before running
- Three buffers run on train, paired-bootstrapped against b=0 on the same days
- Drift fraction and demeaned Sharpe reported per buffer — the §41 check
- Turnover split reported; the mechanism claim (lower boundary-crossing) verified
- Winner is the smallest `b` passing all three, or b=0 if none
- If a buffer wins, its need for a separate 2024 validate is stated, not executed
- Budget **15 of 25**; holdout untouched

## 10. Do not

- Choose `b` by which performs best instead of smallest-that-passes
- Adopt a buffer on a point-estimate improvement without the paired CI
- Accept a Sharpe gain that comes with rising drift (the §41 trap)
- Exceed b=3 (geometric overlap in a 15-name universe)
- Validate a winning buffer on 2024 in this stage
- Touch the holdout

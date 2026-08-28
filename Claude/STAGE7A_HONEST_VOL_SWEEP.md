# Stage 7a — The honest vol sweep at $800 (train, zero trials)

Re-derives B's deployment vol on **uncontaminated** drawdown numbers, and
tests whether a single vol at $800 can satisfy the drawdown cap, the floor, and
the drift band **at once**. Free — risk-sizing on train, same class as §39 and
§42.

§0 of `STAGE2_PROMPT.md` remains in force. Budget stays **11 of 25**; validate
(2024) not touched; holdout sealed.

---

## 0. Why this must run before any validate

§42 healed the mechanism at $800 but surfaced a **circularity**: the §39 rule
picked 14% as "highest vol with drawdown ≤ 20%," but that drawdown (14.78%) was
a $400 floor artifact — the book was skipping the very days it would have lost
on. At $800 the same vol produces **24.79%**, which would never have passed the
cap. So 14% was chosen from a number its own contamination produced.

Validating 14% now would spend a trial on a config whose defining parameter is
a known artifact. This sweep removes the circularity for free, on train, before
the trial is spent.

---

## 1. The coupling this sweep exists to expose

Capital and vol are **coupled through the floor**. Lowering vol shrinks
positions; at some point they fall back under $5 and the floor re-breaks the
book — re-introducing skips and drift, exactly what $800 just healed at 14%.

The honest ratio at $800 is **~1.92** (24.79% DD / 12.88% realised vol), not
the 1.49 the contaminated data implied. Under that ratio the 20% cap points to
~10% vol — but **~10% vol at $800 may re-break the floor.** So the sweep cannot
just re-derive vol from the ratio; it must **re-measure skip and drift at each
vol**, because a vol that satisfies the drawdown cap on paper is worthless if
the floor mangles it.

**The feasible config is a joint point where three conditions hold at once:**
1. measured max drawdown ≤ 20%
2. drift fraction < 30% (clean band), demeaned Sharpe > 0
3. skip rate near the clean reference, realised vol reaching target

## 2. The sweep

B at **$800 capital**, top-15 PIT majors, `lookback=14`, `skip=0`, N=10, k=5,
3× cap, beta-neutral, +1min fill, 5bps slippage, USDT fees. Train 2020–2023.

Vol targets: **8%, 10%, 11%, 12%, 14%.** (14% is the §42 healed reference and
must reproduce §42's numbers exactly — if it does not, stop, something is
non-deterministic.)

Cite, do not re-run: §42's $800/14% row and §34's $400/20% row.

## 3. Report per vol — all three axes, not just drawdown

| Column | Why |
|---|---|
| measured max drawdown + date | the cap input, now uncontaminated |
| **skip rate by reason** | does the floor re-break at this vol? |
| **drift fraction + demeaned Sharpe** | does it stay trend, or revert to drift-harvesting? |
| realised vol vs target | is the floor capping size again? |
| p05 / median position notional | how close to the $5 floor |
| rebalance count | trading most days, or holding? |
| Sharpe | reported, NOT selected on (vol-invariance is broken by the floor) |

## 4. The selection rule — write into NOTES §43 before running

**Deploy the highest vol that satisfies ALL THREE simultaneously:**
- measured max drawdown ≤ 20%
- drift fraction < 30% and demeaned Sharpe > 0
- skip rate ≤ the §42 $800/14% rate (17.26%) + 5 points, and realised vol
  within 2 points of target

The drawdown cap stays 20% — unchanged from §39, not re-tuned. The drift and
skip conditions are what §39 lacked and what let the circularity through.

If no vol satisfies all three, that is the key finding (see §5). Do not relax a
condition to manufacture a winner.

## 5. The three possible outcomes — fix the reading before running

| Outcome | Meaning | Next |
|---|---|---|
| A clean vol exists (all three hold) | That is the deployment config, derived honestly | Validate **that** vol on 2024 — one trial, spent right |
| Floor re-breaks below 14% (drawdown cap wants ~10%, but ~10% skips/drifts) | $800 heals 14% but cannot support the vol the cap requires. **$800 is not enough for a cap-satisfying book** | Report the capital at which the cap-satisfying vol also clears the floor — the true joint target |
| 14% is the lowest floor-clean vol, and it breaches the cap (24.79%) | Genuine tension: at $800 you must choose drawdown headroom OR floor-clean mechanism, not both | A knowing risk decision, deferred to the user with both numbers on the table |

## 6. What this does not do

- Does not change N or k — those stay 10 and 5. Only vol is swept.
- Does not touch 2024 or the holdout.
- Does not select on Sharpe — the §39.7 invariance failure means Sharpe is not
  comparable across vols, and the floor makes it worse at low vol.
- Does not "fix" the persistent ~1.1-point vol shortfall (§42 finding 3) —
  report it per vol, note if it worsens as vol drops (which would confirm the
  weight-band-vs-scale interaction), but do not chase it here.

## 7. Order of work

1. §4 selection rule and §5 readings into `NOTES` §43, dated, before running
2. Sweep 8/10/11/12/14% at $800 on train; 14% must reproduce §42
3. Report the §3 table — all three axes per vol
4. Apply §4: highest vol satisfying all three, or declare which §5 outcome
   fired
5. State the deployment config, or the revised capital target, or the risk
   tension — whichever the data gives
6. **Stop.** Spend no trial. Holdout sealed.

## 8. Acceptance

- §43 rule and readings recorded before the run
- 14% row reproduces §42 exactly (determinism check)
- All three axes (drawdown, drift, skip/vol) reported per vol — not drawdown
  alone
- Selection applied on all three simultaneously, not on Sharpe
- Verdict: clean deployment vol, or revised capital target, or stated risk
  tension
- Budget **11 of 25**; 2024 not re-run; holdout untouched

## 9. Do not

- Log the sweep as a trial
- Select on Sharpe, or on drawdown alone
- Relax a condition to force a winner
- Change N or k
- Validate any vol on 2024 in this stage — that is the next step, one trial
- Touch the holdout

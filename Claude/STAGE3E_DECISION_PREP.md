# Stage 3e — Housekeeping, power, and the validate rule

Follows Stage 3d (branch two). **One trial remains and is unallocated. This
document spends none of it.**

§0 of `STAGE2_PROMPT.md` remains in force.

Purpose: record what Stage 3d established, run the free checks that inform
the last decision, and pre-register the validate rule *while it is still
undecided whether validate will be run* — which is the cleanest possible
moment to write it.

---

## 1. Record: why the cap's effect was always going to be small

§26.1 argues the tail losing money and the cap not helping are compatible
because the tail is a small sliver. Correct, but there is a sharper
structural reason worth recording as §28.

Pooled `101+` loss is **−0.1516/position-day × 1,828 position-days ≈ −$277**.
Capping improved price PnL by only **+$217**, and that was not significant.

**Because `N=10` is fixed.** The cap does not *delete* those positions — it
*replaces* them with the next-best eligible candidate. You swap one noisy
outcome for another. The expected gain is the margin between the 5th and 6th
ranked name, not the full loss of the 5th.

So the effect size was structurally small before any data was seen. That is a
design lesson, not bad luck: **an experiment that removes candidates from a
fixed-N selection tests a marginal substitution, not a deletion.** Any future
universe-restriction test has the same ceiling.

## 2. Record: underpowered, not refuted

§26.1 is right that a near-miss is not evidence. Record the precise
characterisation alongside it, because "not established" and "no effect" are
different claims and the difference matters for what gets tried next:

- implied SE ≈ **0.169**, t ≈ **1.56**
- two-sided p ≈ **0.12**, one-sided p ≈ **0.06**
- effective sample: the cap is inert on 34.3% of days, so **908 of 1,381 days**
  carry information

A true effect of +0.26 Sharpe is entirely consistent with what was observed.
The finding is that this sample cannot establish it.

### 2.1 A methodological lesson — NOT grounds to reopen

A 90% interval is two-sided by convention. A **one-sided** test at the same
level would have passed, and one-sided is arguably natural here since only
"the cap helps" is of interest.

**The branch-two decision stands.** Reversing it now would be exactly the
fudge §26.1 refused, and the rule said thresholds are not adjusted after
seeing intervals.

The lesson is forward-looking: **every future pre-registration must specify
one-sided or two-sided explicitly.** It is decisive and invisible until it
matters. Add this to the pre-registration checklist wherever that lives.

---

## 3. Free check: what can this project actually detect?

Never computed, and it should govern what the last trial is spent on.

Using the paired-difference SE from §26.1 and the standalone Sharpe SEs from
the train and validate windows, report the **minimum detectable effect** at
90%, one- and two-sided, for:

1. A paired comparison of two configs on train (the Stage 3c/3d shape)
2. A standalone Sharpe on the 2024 validate window (1 year)
3. A standalone Sharpe on the 2025-01 → 2026-07 holdout (1.58 years)
4. A paired comparison on validate, if two configs were run there

State plainly, for each: **what is the smallest effect that could be
established, and is any candidate in §22.6 plausibly that large?**

If the answer is that no remaining candidate is detectable with the available
sample, that is itself the most important conclusion available and should be
written as such.

## 4. Free check: USDC-margined universe

Raised earlier and never run. `exchangeInfo` query, no backtest, no trial.

Report:

- Count of USDC-margined perpetual symbols
- Overlap with the current USDT universe (832 symbols)
- Median daily quote volume of the USDC set versus the USDT set
- Their `MIN_NOTIONAL` values

Context: USDC-margined futures are **0.0000% maker / 0.0400% taker** at
Regular User (0.0360% with the BNB discount) versus USDT's 0.0200% / 0.0500%.
USDC at Regular User equals USDT at VIP 2, with no holding requirement.

**Do not switch, model, or backtest anything.** This is a feasibility fact
for the record. If the USDC set is a few dozen liquid names, note the
observation that its boundary would be set by Binance rather than chosen —
which sidesteps the parameterisation problem in STAGE3C §4.1 — and leave it
at that.

## 5. Free: BNB fee discount

Hold enough BNB in the futures wallet to cover fees. 10% off futures fees
immediately, no strategy change, no VIP threshold involved.

Keep the balance to roughly what fees require. It is an unhedged directional
position inside a market-neutral strategy, so it should not be stockpiled.

Record the corrected VIP criteria in `NOTES` for the record: VIP 1 is **5 BNB
+ ($1M spot / $5M futures / $100k wallet / $100k net borrowing)**, and VIP 1
does **not** improve the futures taker rate at all (0.0500% at both Regular
and VIP 1). Earlier figures in this project of $15M and 25 BNB were wrong.

---

## 6. Pre-register the validate rule — write it now, before deciding

STAGE3D §27 was gated on branch one and therefore never written. **Write it
now anyway**, for the **uncapped frozen config** of §19.5.

Writing it while it is still undecided whether validate will run is the
cleanest possible moment: there is no result to fit it to, and no decision
riding on it yet.

Specify each as a number or yes/no:

1. **Minimum validate Sharpe** to justify spending the holdout. State whether
   the project's 0.3 stop threshold applies and why.
2. **Sign requirement on price PnL.** Train price PnL was negative by 2023 in
   the uncapped config. State what validate price PnL must do.
3. **Composition tolerance.** Uncapped train ran ~61% funding. How far can
   validate drift before the mechanism counts as different?
4. **Drawdown limit.** Uncapped train maxDD was 27.87% against a 30% kill
   switch — 2.13 points of headroom. Is a validate breach an automatic stop?
5. **Active-days floor** below which validate Sharpe is not interpretable.
6. **What failure means.** Does the project stop, or return to research with
   the holdout unspent? Decide now.
7. **One-sided or two-sided**, per §2.1.

### 6.1 Expectations to record — context, not criteria

- Validate is one year. `t ≈ SR × √1`. **Validate cannot confirm; it can only
  refute.** Say so, so the result is not over-read either way.
- Documented carry decayed from 2024 onward, and the uncapped config is ~61%
  funding. Expect the funding component to be weaker in validate than train.
  That alone is not failure.
- Momentum price PnL was already negative in 2023. Expect it to be weak.
- Both engines enter 2024 degraded. A poor validate is the *expected*
  outcome, not a surprise, and the rule should be written knowing that.

---

## 7. The decision — present, do not make

After §1–§6, present the three options for the last trial with the §3 power
figures attached to each. Do not recommend one; the user decides.

| Option | What it buys |
|---|---|
| **Validate the uncapped frozen config** | The pre-registered sequence. Can refute before the holdout, preserving the one look for a future strategy |
| **Pure carry benchmark** (STAGE3_POSTGRID §5 Option A) | Tells you what was built rather than improving it. Less decision-relevant now that §25.3's composition shift is not established |
| **Expand the budget deliberately** | 20 → N, logged with date and reason, DSR recomputed. More honest than spending the last trial because it is the last one |

Add to the table, from §3: whether each is detectable at all with the
available sample.

---

## 8. Order of work

1. §1 and §2 into `NOTES` §28, including the §2.1 lesson
2. §3 power analysis
3. §4 USDC query; §5 BNB note and VIP correction
4. §6 validate rule into `NOTES` §29, dated and committed **in its own commit**
5. §7 decision table
6. **Stop. Report. Spend no trial.**

## 9. Acceptance

- §28 written: replacement mechanism, underpowered-not-refuted, sidedness lesson
- Minimum detectable effect stated for all four comparisons in §3
- USDC symbol count, overlap, and liquidity reported; nothing switched
- VIP criteria corrected in `NOTES`; earlier $15M/25 BNB figures struck
- §29 validate rule written with all seven items, in a standalone commit
- Decision table presented with power figures; **no recommendation made**
- Budget still **7 of 20**; validate and holdout untouched

## 10. Do not

- Reopen the branch-two decision on sidedness
- Run validate, the carry benchmark, or any backtest
- Switch margin asset, or model USDC in the backtest
- Write §29 and run anything in the same commit
- Expand the trial budget without an explicit user instruction
- Touch the holdout

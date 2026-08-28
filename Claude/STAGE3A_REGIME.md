# Stage 3a — Regime diagnostics (zero trials)

Follows Stage 3 §1–§4. Answers one question raised by the per-year attribution
and left open: **is the price-PnL decline alpha decay or regime dependence?**

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 0. Scope and constraint

**Nothing here spends a trial.** Every item is either a statistic computed
from data already ingested, or attribution of a backtest that has already run.
No configuration is selected, no signal is changed, no new strategy is
evaluated.

**Two trials remain of twenty. This document spends none of them.**

The config stays frozen at `lookback=14, skip=0`, capital $400, 5bps, +1min.
Do not vary it. If a result here suggests a change, it goes in §6 and waits.

---

## 1. The question

Per-year price PnL: **+163 → +110 → +30 → −37**. Monotonic decline to
negative. Two explanations, very different implications:

| Hypothesis | Mechanism | Implication |
|---|---|---|
| **Regime** | Momentum needs cross-sectional dispersion. Correlated markets have nothing to rank. | Momentum returns when dispersion does |
| **Decay** | Crowding, competition, or the effect was always weak | It does not come back |

External data is unhelpfully mixed. Published rolling correlation shows ~0.8
through 2022 spiking to ~0.9 at the crash trough, then a mid-2023 dip to ~0.5
— but a yearly average for the same pair reports 2023 *higher* than 2022. It
is one pair, not your universe.

**You can compute the exact figure from data you already have.** That is the
point of this document. Do not reason from third-party correlation blogs.

---

## 2. Cross-sectional dispersion, per year

The core measurement. For each rebalance date in the train window, over the
point-in-time universe as of that date:

1. Compute each symbol's 14-day trailing return (the frozen lookback).
2. Compute the **cross-sectional standard deviation** of those returns across
   the universe on that date.
3. Also compute the **top-decile minus bottom-decile spread** — closer to what
   the strategy actually harvests, since it trades the extremes rather than
   the whole distribution.

Aggregate both to annual mean and median. Report 2020–2023.

**Use `PITView` for this.** It is a diagnostic, but computing dispersion from
ungated data would use future information, and the habit matters more than
this one number.

### 2.1 Also report

- **Annual mean pairwise correlation** to BTCUSDT across the universe. This is
  the direct test of the "nothing to rank" hypothesis.
- **Universe size per year** — dispersion is not comparable across a universe
  that grew from 40 symbols to 300.
- **Dispersion in the crash months specifically** (2022-05 LUNA, 2022-11 FTX)
  versus the 2022 annual mean.

### 2.2 The reading, fixed before you look

- **Dispersion collapses in 2022 AND stays low in 2023** → regime explains
  both years. Momentum is dormant, not dead.
- **Dispersion collapses in 2022 but recovers in 2023** → regime explains 2022
  only. 2023's negative price PnL needs another explanation, and decay becomes
  the leading candidate.
- **Dispersion roughly flat across all four years** → regime explains nothing.
  Decay is the explanation for the whole decline.

Write the reading down before running it. Do not adjust the interpretation
after seeing the numbers.

---

## 3. Drawdown timing

Max drawdown on the frozen config is 27.7%, against a 30% kill switch. **An
annual Sharpe says nothing about the intra-year path.**

Report:

- Max drawdown within each calendar year, with peak and trough dates
- The date of the global 27.7% trough
- Equity curve monthly, so the shape is visible
- **Whether the 30% kill switch would ever have fired** on the daily path

That last one matters most. If the drawdown breached 30% intra-year and only
the annual close looks acceptable, then under your own pre-registered rules
you would have shut the strategy down mid-run — and the reported four-year
Sharpe describes a path you would not have completed.

Check whether the trough sits inside 2022. If it does, "2022 was flat" is true
annually and misleading operationally.

---

## 4. Funding by regime

2022 funding was **−$6** — you paid. That is the crash mechanism that actually
threatens this strategy: in a crash, shorts become the crowded side, so shorts
pay longs. Your largest income line inverts exactly when the price leg is
starved.

Report:

- Monthly funding PnL across the train window
- Funding PnL split by leg (long vs short)
- Funding PnL bucketed by trailing BTC drawdown depth: 0–10%, 10–30%, 30–50%,
  >50%
- The fraction of days on which the short leg *paid* rather than received

This quantifies how much of your dominant return source is regime-conditional,
which bears directly on a holdout sitting in a period when carry is documented
to have gone negative.

---

## 5. Sanity check on the crash claim

The premise is that 2022 hurt the strategy. Its own numbers say it was flat:
long −$173, short +$186, net +$12, Sharpe 0.05.

Verify explicitly and report:

- Realised gross leverage distribution in 2022 vs other years
- Realised beta to BTC per year — did the hedge hold under a correlation spike?
- Skip counts by reason in 2022 — did the harness stay functional?
- Closest approach to liquidation: minimum of
  `equity / (maintenance_margin_requirement)` across the run, using the
  existing intraday H/L stress diagnostic

**If beta-neutrality held and leverage stayed low through the worst crash on
record, that is a positive result and should be stated as one.** It is
evidence the risk design works, which is separate from whether the alpha
works.

---

## 6. Do not implement — candidates arising

Anything suggested by these diagnostics goes here and waits.

| Candidate | Status |
|---|---|
| **Dispersion/correlation filter** — halt or reduce when dispersion is below a threshold | Real mechanism, genuinely motivated. **Costs a trial.** And the threshold cannot be chosen by testing which value performs best in 2022 — that is fitting the filter to the crash |
| Funding-sign filter — avoid shorts when funding is negative | Same objection, costs a trial |
| Per-name stop loss | Risk control, but the drawdown evidence does not yet show it is needed |
| Correlation-regime vol target | Overlaps the dispersion filter; pick one, later |

The dispersion filter is the most interesting idea currently on the table. It
is still not worth a trial until §2 says which hypothesis is true, because if
dispersion recovered in 2023 while PnL stayed negative, the filter would not
have helped and would be fitted noise.

---

## 7. Order of work

1. §2 dispersion and correlation, with §2.2 reading written down first
2. §3 drawdown timing, including the kill-switch check
3. §4 funding by regime
4. §5 crash-resilience verification
5. Write §19 in `NOTES`: which hypothesis the evidence supports, and what it
   would take to change that conclusion
6. **Stop. Report. Spend no trials.**

## 8. Acceptance

- Annual dispersion and decile spread, 2020–2023, computed through `PITView`
- Annual mean correlation to BTC; universe size per year stated alongside
- §2.2 reading recorded *before* the numbers, and the conclusion follows it
- Max drawdown per year with dates; explicit yes/no on whether the 30% kill
  switch would have fired
- Monthly funding PnL; funding bucketed by BTC drawdown depth; short-leg
  paid-vs-received day counts
- 2022 leverage, beta, and skip counts reported
- §6 table updated with anything new
- **Trial budget still 6 of 20**; validate and holdout untouched

## 9. Do not

- Vary the frozen config for any diagnostic
- Reason from third-party correlation figures when your own data answers it
- Choose a dispersion threshold, or any threshold, from these results
- Implement anything in §6
- Adjust the §2.2 reading after seeing the numbers
- Touch validate or holdout

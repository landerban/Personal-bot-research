# Stage 2d — Floor withdrawal, and the first valid grid

Follows Stage 2c acceptance (`549fce2`, `8cf8956`). Amends
`STAGE2C_PREGRID.md` §3, which was **wrong**. Where this conflicts with any
earlier document, this wins.

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 1. WITHDRAWN: the 1.05× gross leverage floor

`STAGE2C_PREGRID.md` §3 pre-registered `min_gross_leverage = 1.05`. **Remove
it.** This is a specification error, not an implementation error — you built
what the document said.

### Why

I calibrated the floor from the synthetic fixture's unit-book volatility. The
real figure, from the v1 replay in `NOTES` §13.1, is ~4× higher:

| | unit-book vol | gross | floor multiplier | realised vol | expected maxDD |
|---|---|---|---|---|---|
| Synthetic fixture | 21% | 1.90 | 1.00× | 20.0% | 14.3% |
| **Real, median** | **89%** | **0.45** | **2.34×** | **46.7%** | **33.4%** |
| Real, p95 | 164% | 0.24 | 4.30× | 86.1% | 61.5% |

The floor does not solve the `MIN_NOTIONAL` problem. It breaks the risk budget
instead — pushing the book to ~47% realised volatility and an expected maximum
drawdown of 33% against the pre-registered 30% kill switch. The synthetic
fixture cannot show this because its unit-book vol is 4× too low, so the floor
never binds there.

Your own Stage 2c evidence already showed the mechanism: the pre-fix peak went
from 35.71× to 156.44× once the floor existed, because "the floor starts the
book larger, so the un-shrunk notional is larger." A 4.4× amplification of the
worst case. The floor makes every exposure failure bigger.

Compounding it: crypto momentum's tail variance is undefined under power-law
tests, so volatility management improves Sharpe but does not bound the tail.
Running at 47% vol with an unbounded tail is not a risk position this project
can take.

### Action

```python
min_gross_leverage: float = 0.0    # withdrawn, Stage 2d §1
```

Keep the config field so the withdrawal is visible in every logged trial row
rather than disappearing from the record.

---

## 2. RESTORED: `initial_capital = 400`

The floor and the capital ruling are **opposite** solutions, not
interchangeable ones:

- **Floor** raises exposure until positions clear $5 → sacrifices the vol target
- **Capital** lowers required leverage until real exposure clears $5 → preserves it

Only the second keeps the pre-registered 20% target intact.

`C ≥ 10N/L` at N=10 → **$400 needs L ≥ 0.25**, against a real distribution of
min 0.21 / p05 0.27 / median 0.46. Clears above the 5th percentile. Residual
`below_min_notional` skips of roughly 1–5% are expected and acceptable; they
now rescale rather than flatten.

Your procedural objection was correct from where you sat — $400 wasn't in a
document, the floor was. But §0 places constraints with the user, and capital
is an input constraint the user set, not a strategy parameter being tuned.

```python
initial_capital: float = 400.0
```

### 2.1 Record the calibration honestly

In `NOTES` §16, with today's date:

- The floor was withdrawn on volatility-calibration grounds **before** any
  grid run under the current harness
- The calibration input (unit-book vol ~89% median) came from **void-run
  replay data**
- This is a risk calibration, not a performance selection — but the use of
  real data is visible either way

---

## 3. Test 20 — the floor path at realistic volatility

Test 18 binds on 499 rebalances at synthetic vol, so it isn't vacuous, but it
has never run at a volatility regime that exists.

Build a fixture whose hedged unit book has **~89% annualised volatility**
(match the v1 replay median). On it, assert:

1. With `min_gross_leverage = 0.0`: realised vol stays within Test 6's band of
   the 20% target; gross sits near 0.45; no unbounded growth.
2. With `min_gross_leverage = 1.05` (the withdrawn setting): realised vol
   **exceeds 40%** — demonstrating the floor's failure mode is reproducible
   rather than asserted.

Case 2 is the important one. A withdrawal justified only by arithmetic in a
markdown file is weaker than one pinned by a failing test.

Any fixture used to justify a risk parameter must match the real volatility
regime. That's the general lesson from this error — add it to `NOTES` §16.

---

## 4. Pre-flight checklist

Before the grid, confirm and report each:

| Check | Expected |
|---|---|
| Backfill complete | 832 symbols, ~612k bars, 2020-01-01 → 2026-07-31 |
| Funding-start exclusion | Implemented (see §5) |
| `initial_capital` | 400.0 |
| `min_gross_leverage` | 0.0 |
| `slippage_bps_per_side` | runs at 0.0 and 5.0 |
| Suites | Stage 1 13/13, Stage 2 all green incl. Test 20, live 19/19 |
| Trial budget | count before, count after |
| `holdout_log.json` | absent or empty |

If any fails, stop and report. Do not run a partial grid.

---

## 5. Funding-start exclusion (carried from the last review, unimplemented)

`NOTES` §13.4: funding history begins **477 days after klines for ICPUSDT, 592
for TLMUSDT, 305 for BNXUSDT**. Those symbols are currently tradeable for a
year or more with funding silently absent — logged as missing, never
zero-filled, but the position still runs cost-free on that leg.

This systematically understates costs on exactly the long-tail names momentum
favours. **Exclude a symbol from candidacy until its funding history begins.**

One line in the §5 candidate filter. Conservative, data-policy rather than
strategy, **costs no trial**. Report how many symbol-days it removes.

Do this **before** the grid — otherwise the grid measures inflated returns and
the trials are wasted.

---

## 6. The grid

Six configs — lookback {7, 14, 28} × skip {0, 2} — on **train only**, at
`slippage_bps_per_side` ∈ {0.0, 5.0}.

**12 runs, 6 trials.** Slippage is a cost assumption, not a strategy
parameter; the pair is reported together and neither is selected over the
other. If the DSR count is ever borderline, use the higher number.

Run once. Do not re-run to "check" a surprising result — re-running the same
config is not a new trial, but re-running because you dislike the answer is
how selection enters through the back door. If a result looks wrong,
diagnose by replay (`tools/postmortem.py`, no budget), not by re-running.

### 6.1 Report per config

Everything in `STAGE2_PROMPT.md` §6 and `STAGE2A_REMEDIATION.md` §6, plus:

- Realised gross leverage: min / p05 / median / p95 / max
- **Active days vs window days** — the single most important line. A Sharpe
  computed over a sliver of active days is not comparable to one computed over
  a full window, and grid v2's headline was 72 active days of 1,342.
- Skip counts by reason; rescale events, their turnover and fees
- Fee drag as % of gross PnL, at both slippage settings
- Long-leg vs short-leg PnL split
- Deflated Sharpe with the trial count stated explicitly

### 6.2 Then the decomposition

Drift/trend on the best config only, per `STAGE2A_REMEDIATION.md` §2.1.
`diagnostics.jsonl`, no budget. Interpret against the ~18% synthetic floor,
not against zero, and only if active days are sufficient to mean anything.

### 6.3 The gate

For any config reporting Sharpe > 1.5, answer in writing before the number is
reported as a result:

> **Who is paying, and why would they keep paying?**

If it can't be answered, treat it as a bug and investigate. Check first:
active-day count, whether returns concentrate in one short window, and
realised leverage distribution. Those three explained the entire grid v2
headline.

---

## 7. Stop after the grid

Report. **Do not run validate.** Do not touch holdout.

Two trials remain after this and they are the scarcest thing in the project.
Whether to spend one on validate is a decision that needs the grid output on
the table first.

---

## 8. Do not

- Re-introduce any gross leverage floor without a fixture at realistic vol
- Justify a risk parameter from a synthetic fixture whose vol regime doesn't
  match real data — this is the specific error that produced §1
- Re-run a config because the result is disappointing
- Run validate or holdout
- Treat any grid v1/v2 figure as a prior; they are struck in `NOTES` §15.2
- Skip §5 to get to the grid faster

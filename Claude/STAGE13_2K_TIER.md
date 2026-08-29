# Stage 13 — The $2k question: measure it on train, then exercise it on paper

Two parts, strict order. **Part A** (free, train) answers *what the strategy
becomes* at higher capital — BTC/ETH seating, skip collapse, and the truer
drawdown. **Part B** (paper, second testnet account) runs the higher-capital
book alongside the $800 clock to exercise the execution path.

§0 of `STAGE2_PROMPT.md` remains in force. **No trials** (capital re-sizing is
the Stage 7 diagnostic class). The $800 28-day clock continues untouched.
Holdout sealed.

---

# PART A — Train diagnostic at $2,000 and $2,500 (zero trials)

## A.1 Why measured, not assumed

At $800/10% the train maxDD of 17.03% was measured **with 21.55% of days
skipped** — and skips are accidentally protective (Stage 7: healing skips moved
DD 14.78% → 24.79%). Higher capital heals skips and reveals the truer DD. The
honest-ratio estimate at 10% vol is ~19.2% against the 20% cap — inside by
0.8 points, which is inside this project's measurement noise. So the question
"does the 10% vol choice carry to $2k?" is open until measured.

## A.2 The runs

Frozen config except capital: top-15 crypto majors (crypto filter active),
lb14/skip0, N=10, k=5, **10% vol**, b=0, USDT fees, train 2020–2023, at:

- **$2,000** (the asked-for point; BTC marginal — avg $48 vs $50 floor)
- **$2,500** (BTC seats unconditionally; the clean point)

Cite $800 (§43.6 sweep row / §42) as the reference row; do not re-run it.

## A.3 Report per capital — the Stage 7 mechanism table plus seating

| Column | Watching for |
|---|---|
| skip rate by reason | should collapse from 21.55% toward ~0 |
| **measured maxDD + date** | **the decision number — vs the 20% cap** |
| drift fraction + demeaned Sharpe | must stay clean (<30%, >0) as skips heal |
| realised vol vs 10% target | shortfall should shrink |
| **seating table**: per-symbol count of days each of BTC/ETH/top-5-by-MN was selected-and-seated vs selected-and-dropped | does the book actually become "majors incl. BTC"? |
| p05/median position notional | distance from per-symbol floors |
| Sharpe (reported, not judged) | floor discreteness makes it non-comparable |

## A.4 The reading — write into NOTES §50 before running

| Outcome | Meaning |
|---|---|
| DD ≤ 20% at $2k/$2.5k, drift clean, skips collapse | The 10% vol choice carries. The higher-capital book is the same strategy, finally including BTC/ETH. Record as the config-of-record **for that capital tier** |
| DD > 20% | The coupling bites again: at healed skips, 10% vol breaches the cap. The vol for the $2k+ tier must be re-derived by the §43 three-condition rule — a future free sweep, **not** done ad hoc in this stage |
| Drift or skips degrade | Something new — stop and report |

Either way: **the $800/10% deployment config is unchanged** — it was validated
as-is, skips and all. Part A characterises a different capital tier; it
re-freezes nothing.

# PART B — The exercise book (paper, second account)

## B.1 Structure — recorded as a user decision

A second paper book at the Part-A-informed capital (**$2,500** if DD ≤ 20%
there; otherwise hold Part B until the user decides) runs **in parallel** with
the $800 clock:

- **Separate testnet account, separate keys** (user registers a second
  testnet email and supplies `BINANCE_TESTNET_KEY_2`/`_SECRET_2`). One account
  cannot host two books — reconciliation would see the union and both
  harnesses would flag phantom mismatches.
- Separate `status.json`, log directory, costlog (`venue=testnet`,
  `book=exercise`), and dashboard section or port.
- **Labeled `exercise`, everywhere.** It is machine-exercise, not the
  deployment rehearsal: its PnL counts toward nothing, its behaviour tunes
  nothing, and it is not the config the holdout would test.
- The **$800 book keeps the official 28-day clock** unchanged. Day-1's skip is
  within its expected 21.55% cadence — the clock is not stalled and is not
  being rescued.

## B.2 What the exercise book is for

Order flow. At $2.5k the book seats BTC/ETH and should trade most days, which
makes the four induced demonstrations (§49.9) runnable on schedule:
run demos 1, 2 and 4 on the **exercise book** (they act on orders, which it
has), and demo 3 (process kill/recover) on **each book once**. Evidence
requirements unchanged from §49.9. Demos on the exercise book satisfy §46
criterion 4 for the *machinery* — record that interpretation in NOTES §50 so
the criterion's meaning is fixed before it is graded.

Shadow reconciliation runs on both books independently.

## B.3 Do-nots for Part B

- No strategy parameter changes on either book, ever, in response to paper
  behaviour
- The exercise book never becomes the rehearsal book by drift — if the $800
  book's clock fails its criteria, that is reported, not papered over with the
  exercise book's cleaner days
- No mainnet, no holdout, no 2025+ historical data

## Order of work

1. §A.4 + §B.2 readings into NOTES §50, dated, before running
2. Part A at $2,000 and $2,500; report §A.3 tables
3. State which §A.4 branch fired
4. If DD ≤ 20%: request second-account keys from the user, then wire Part B
5. Exercise book live; demo schedule re-dated against it
6. Report. Both clocks visible on the dashboard. Holdout sealed.

## Acceptance

- §50 recorded before any run
- Both capitals measured with the full §A.3 table; seating table per symbol
- Branch stated; the $800 deployment config explicitly unchanged
- Part B only after Part A passes and keys are supplied; books fully isolated
  (accounts, keys, logs, status, costlog tag)
- Demo schedule reassigned with the criterion-4 interpretation recorded
- Budget **15 of 25**; $800 clock uninterrupted; holdout sealed

## Do not

- Treat Part A results as re-validating anything out of sample
- Re-derive the vol target in this stage if DD > 20% — report and stop
- Run two books on one account
- Let exercise-book results touch any criterion except the demo evidence
- Touch mainnet, the holdout, or 2025+ return data

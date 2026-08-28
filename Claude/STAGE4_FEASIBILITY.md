# Stage 4 — Feasibility checks for the majors / USDC / rank-weighted hypothesis

**Zero trials.** Every item is a data query or an attribution of existing
runs. No backtest, no configuration change, no strategy switch.

§0 of `STAGE2_PROMPT.md` remains in force. Budget stays **7 of 20**; validate
untouched; holdout sealed.

---

## 0. The hypothesis being checked

Four pieces, each of which makes the next possible:

1. **Majors only** (~15–30 names) — pre-registered by the user in the very
   first interrogation, before any data existed. Supported by the significant
   pooled top-30 rate (+0.0424/pos-day, CI [+0.0084, +0.0793]) and by
   published research placing momentum in roughly the top 2% by market cap.
2. **Rank-weighted allocation across the whole universe** rather than top-5 /
   bottom-5 — feasible only in a small universe, since 166 names would put
   most positions under `MIN_NOTIONAL`.
3. **Smooth weight changes** rather than discrete entries and exits — attacks
   fee drag, which runs 27–48% of gross PnL and is the dominant friction.
4. **USDC-margined perps at 0% maker** — viable only because (3) tolerates an
   unfilled post-only order (you end up slightly mis-weighted, not missing a
   position).

The earlier objection that USDC has only 20 liquid names against 100+ on USDT
**dissolves under (1)**: if the universe is already majors, 20 names is not a
downgrade, it is the same universe.

**This document does not test the hypothesis. It checks whether it is
buildable, and looks for the one thing most likely to kill it.**

---

## 1. THE DECISIVE CHECK — funding on USDC vs USDT

Do this first. If it fails, the rest is moot.

**Why it decides everything.** Funding is ~60% of net PnL, and §22.3 found it
is 81% long-leg and tail-driven — 27% of settlements with violently negative
rates, i.e. crowded leveraged longs paying shorts. That crowding lives where
the open interest and retail leverage are, which is USDT. USDC perps have far
lower open interest and a different participant mix.

The plausible failure mode: **fees near zero, funding income largely gone.**

### 1.1 Measure

For every base asset with both a USDT and a USDC perp (all 38 USDC pairs
qualify per §28.5), over the longest common history:

- mean funding rate, USDT vs USDC
- **the negative tail**: 5th percentile, 1st percentile, and the fraction of
  settlements below −0.01% — this is the part that carries the PnL, not the
  mean
- correlation of the two funding series per asset
- open interest, if available from the dumps
- funding history start date per USDC symbol (USDC perps are newer; state how
  much common history actually exists)

### 1.2 The reading — write into NOTES §31 before measuring

| If... | Then |
|---|---|
| USDC negative tail is comparable to USDT (within ~25% on the sub-−0.01% fraction) | Funding survives the switch. The hypothesis stays alive |
| USDC negative tail is materially thinner | **The switch trades 60% of PnL for a fee saving.** The hypothesis is likely dead in this form; record and stop |
| Common history is under ~2 years | Not answerable on available data. Say so; do not extrapolate from USDT |

Do not adjust after seeing the numbers.

## 2. Spread and depth

A 0% maker fee is worthless if USDC spreads are wider than the fee saved.

From order book snapshots or the dumps, for the same base assets:

- median bid-ask spread in bps, USDT vs USDC
- depth within 10bps of mid
- how the difference compares to the 4bps taker saving and the 5bps you
  currently assume for slippage

**If USDC spreads are wider by more than ~4bps**, the taker route saves
nothing and the whole case rests on maker fills actually happening.

## 3. Universe confirmation

- Confirm BTC, ETH, SOL, XRP and the other intended majors are among the
  USDC pairs clearing $5M median volume
- List the 20 liquid USDC names with median volume and `MIN_NOTIONAL`
- Overlap with the top-30 liquidity bucket from §23 — how many of the top-30
  names have a USDC pair?

If the liquid USDC set does not substantially overlap the segment that
tested significant, the fee advantage applies to the wrong names.

## 4. Rank weighting — feasibility only, no backtest

Using existing train data and `PITView`, for a hypothetical 15–30 name
universe at $400 with realised gross ~0.45:

- Under **pure linear rank weighting**, how many positions per day fall below
  `MIN_NOTIONAL`? (Expect the middle to collapse toward zero — ranks 6–10 of
  15 fell under $5 in the hand calculation.)
- Under a **step scheme** (e.g. 30% top-3 / 40% middle / 30% bottom-3), how
  many? (Hand calculation suggested all clear $5.)
- What is the **minimum capital** at which pure linear weighting is feasible
  for 15, 20 and 30 names?

### 4.1 Specification problem to resolve in writing

"The rest gets 40% equal" is ambiguous and must be resolved before any test.

If the middle names are all long, dollar-neutrality is broken and you have a
directional bet. They must be **split by rank** — the weight function has to
be **monotonic decreasing in momentum rank and sum to zero**, with a floor so
no non-zero position falls under `MIN_NOTIONAL`.

State it as a formula, not three buckets. The beta hedge, the neutrality
tests, and the tilt identity all need a well-defined weight vector.

## 5. Turnover estimate — attribution, not simulation

From the existing train run, without re-running anything:

- How often does a name cross the rank-5 boundary (triggering a full round
  trip today)?
- What fraction of current turnover is boundary-crossing versus weight
  adjustment within the held set?

That fraction is the upper bound on what rank weighting could save. If most
turnover is already adjustment rather than crossing, the turnover argument is
weaker than it looks.

## 6. What this does NOT establish

Record explicitly, so the chain is not oversold:

- Better weighting of a **decaying signal still decays**. Train price PnL ran
  +163 → +110 → +30 → −37. None of this addresses that.
- Truncating to extremes is optimal if only the extremes carry signal.
  Spreading weight across all names assumes the signal is roughly **linear in
  rank**. You have no data on middle-ranked names because you have never held
  them. If momentum lives only in the tails, diluting into the middle costs.
- **No maker-mode result is reportable until a fill-probability model
  exists** (Stage 2e §4, unchanged). Rank weighting makes maker *plausible*,
  not proven. Post-only fill rates must be measured in paper trading first.
- Testing this properly needs train, validate and holdout — three looks
  against one remaining trial. It requires a **deliberate budget expansion**,
  logged with date and reason, DSR recomputed at the higher count.

## 7. Order of work

1. §1.2 reading into `NOTES` §31, dated, **before** measuring
2. §1 funding comparison — **if branch two fires, stop and report**
3. §2 spread and depth
4. §3 universe confirmation
5. §4 rank-weighting feasibility; §4.1 written as a formula
6. §5 turnover attribution
7. §6 recorded
8. **Stop. Report. Spend no trial. Ingest no USDC price history**

Ingesting USDC *funding* is required for §1. Ingesting USDC *klines* is not —
that is backfill for a strategy not yet approved.

## 8. Acceptance

- §31 reading recorded before measurement
- Funding comparison covering mean, 5th/1st percentile, sub-−0.01% fraction,
  and common-history length per symbol
- Branch stated plainly, including if evidence is weaker than the label
- Spread comparison in bps against the 4bps saving
- USDC liquid list with overlap against the §23 top-30 bucket
- `MIN_NOTIONAL` feasibility counts for linear and step weighting; minimum
  capital for each universe size
- Weight function written as a formula, monotonic in rank, summing to zero
- Turnover decomposed into boundary-crossing vs adjustment
- §6 caveats recorded
- Budget **7 of 20**; validate and holdout untouched

## 9. Do not

- Backtest anything
- Switch margin asset or modify the frozen config
- Ingest USDC klines
- Report a maker-mode performance figure
- Expand the trial budget without explicit user instruction
- Adjust the §31 reading after seeing results
- Touch validate or holdout

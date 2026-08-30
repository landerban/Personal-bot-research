# Stage 16 — Real market, imaginary money

Four parts in strict order: **(A)** prove the order machinery actually places,
fills and reconciles real orders (on testnet, the only venue where orders are
allowed); **(B)** the read-only production data client with hard safety rails;
**(C)** the fill simulator that turns real data into paper fills; **(D)** cut
the daily rehearsal over to production data, verify the book finally forms,
restart the clock.

§0 of `STAGE2_PROMPT.md` remains in force. **No trials. No strategy parameter
changes. ZERO orders on mainnet — the production client cannot sign.** Holdout
sealed. Budget stays **15 of 25**.

---

## Pre-register first — NOTES §56, dated, before code

1. **The venue amendment**, §49.3-style: production Binance **market data**
   (public, unauthenticated, read-only GET) is permitted for the paper feed;
   production **trading** remains forbidden and holdout-gated exactly as
   §49.3 left it. Grounds: §55.9 — testnet's synthetic history defeats the
   60-day eligibility screen; the screen works as designed on real data; a
   read-only feed risks no capital.
2. **The fill model**: paper fills execute at the **next 1-minute bar's open
   after decision, plus 5 bps adverse slippage** — the backtest's own
   assumption, now applied to live real bars. Recorded so nobody later tunes
   fills to flatter results.
3. **Criterion interpretations** (fixing them before grading, per §50.4
   precedent): criterion 1/3/5/6 are satisfied on the real-data rehearsal;
   criterion 4's demos 1, 2, 4 run on the testnet demo fixture; criterion 2 =
   ledger-half already demonstrated (§52.3) + daily accrual check against real
   published funding rates.
4. **The counter restarts at the first real-data cycle** (currently 0 anyway;
   §55.10's restart never began counting).

# PART A — Prove the order machinery (testnet, real orders, tiny sizes)

The user's requirement, and the right one: before retiring testnet to
demo-fixture duty, demonstrate end-to-end that the code can genuinely trade.

## A.1 The roundtrip test

On the demo account, with `demo=True` tagging on every record so nothing
contaminates strategy data, run a scripted sequence on **liquid testnet
majors** — BTCUSDT ($60 notional, above its $50 floor), ETHUSDT ($25), one
liquid alt (SOL or XRP, $15). Not junk symbols: the point is proving the
pipeline, and unidentifiable junk adds noise, not evidence.

Per symbol, demonstrate and log with timestamps and exchange responses:

1. **Market order** → ack → fill → fill price and fee recorded in costlog
2. **Limit order** placed passively → **cancel** → confirmed gone
3. **Reduce-only stop** placed → visible on the exchange → cancelled
4. **Position visible to reconcile** — the reconciler reports the live
   position matching the fill
5. **Close** → flat → reconcile confirms flat
6. One **deliberately undersized order** → rejection captured and classified
   correctly (the error path, not just the happy path)

## A.2 Pass condition

Every step produces the expected exchange response and the expected local
record; costlog rows appear with correct fees and `venue=testnet, demo=True`;
reconcile agrees with the exchange before, during, and after. Any divergence:
stop and report. This doubles as the dress rehearsal for the §49.9 demos,
which stay scheduled on this fixture.

# PART B — The production data client

## B.1 Hard rails, enforced in code and tests

- **Separate module** (`live/proddata.py`), GET-only, allow-listed endpoints:
  klines, premiumIndex/fundingRate, exchangeInfo, 24hr ticker, bookTicker.
- **No signing capability**: the module has no access to any secret, takes no
  credential argument, and a test asserts importing it with credentials in the
  environment still cannot produce a signed request (no HMAC path exists).
- **Cannot POST**: the HTTP layer it uses refuses non-GET methods; tested.
- Base host is a constant; the URL guard extends: `paper_feed=production` may
  only combine with `execution=simulated`. `execution=live` with any
  production host remains a startup-refusal.

## B.2 Real-time verification — the user's "is it really connected" check

A `feedcheck` command that reports, against wall clock:

- last **closed** 1m and 1d bar timestamps for BTC/ETH/SOL and their lag
  (expected: 1m bar closes within ~seconds of the minute)
- current funding rate and next funding time for three majors
- bookTicker bid/ask freshness (successive calls move)
- 24h quote volumes vs the store's production medians (same order of
  magnitude — the §55 fix's ranking source and the live feed agree)
- exchangeInfo hash vs the committed snapshot (drift = composition guard
  input, §51.3)

Run it, include its output in the report. This is the evidence the feed is
live, complete, and the same market the research measured.

# PART C — The fill simulator

## C.1 Mechanics

At execution time the simulator fetches the real 1m bar following the
decision, fills at its open ± 5 bps (adverse), computes taker fee at the USDC
schedule for reporting alongside USDT, and writes costlog rows tagged
`venue=prod_data_sim`. Partial-fill and rejection semantics mirror the floor
rules (an order under a symbol's MIN_NOTIONAL is refused by the simulator
exactly as the exchange would).

## C.2 What real-time data adds beyond the backtest

At both decision and execution moments, capture **bookTicker bid/ask** for
every traded symbol into the costlog. Two uses, both measurement-only:

- **spread context**: the realised half-spread vs the 5 bps assumption,
  building the first real-data evidence about whether 5 bps is generous or
  tight (evidence for the *future* real-order stage; adopts nothing)
- **shadow-maker upgrade**: Stage 15 D.1's counterfactual now keys off real
  quotes and the real tape — would the post-only price have been touched —
  which turns the maker dataset from synthetic to genuine

## C.3 Tests

Simulator unit tests: fill price arithmetic, slippage sign, fee schedules,
MIN_NOTIONAL refusal, partial handling. One full dry-run cycle end-to-end in
CI with canned real bars: decision → simulated fills → costlog → status.json
→ shadow reconciliation MATCH.

# PART D — Cutover and verification

1. Daily rehearsal switches: decisions and risk on the production feed,
   execution through the simulator. Testnet keys remain only for the Part A/
   demo fixture path.
2. **Replay the last 12 days on real data** through the full path (the §55.8
   comparison, third and final time). Expected: book forms most days,
   betas identified, skips near the backtest's ~2–5% live expectation. Report
   the same table format as §55.8. If formation is again ~0%, **stop** — that
   would falsify the §55.9 diagnosis and needs eyes, not another layer.
3. First live cycle on real data completes → **day 1 of 28**, per §56.
4. Dashboard: venue line shows `prod_data / sim_fills`; alerting (§55.4)
   unchanged; regime context (D.3) now on real data.

## Order of work

1. NOTES §56 pre-registration (amendment, fill model, criterion map, counter)
2. Part A roundtrip — stop on any divergence
3. Part B client + rails + `feedcheck` evidence
4. Part C simulator + tests
5. Part D replay verification — stop if formation ~0% — then cutover
6. Report: roundtrip evidence, feedcheck output, replay table, day-1 status.
   Holdout sealed.

## Acceptance

- §56 recorded before implementation
- A.1 roundtrip complete on all steps incl. the rejection path, evidence
  logged, `demo=True` isolation verified
- Production client provably unable to sign or POST; URL guard extended;
  feedcheck output in the report
- Simulator tests green; spread capture and shadow-maker running on real
  quotes
- 12-day real-data replay shows the book forming; cutover done; counter at
  day 1
- Zero orders on mainnet anywhere; budget **15 of 25**; holdout sealed

## Do not

- Give the production client any secret, signing path, or non-GET method
- Place any order outside the testnet demo fixture
- Tune the fill model beyond the pre-registered backtest assumption
- Proceed past a failed roundtrip or a ~0% replay
- Touch the holdout

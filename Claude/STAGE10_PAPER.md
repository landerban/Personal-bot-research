# Stage 10 — Paper trading on testnet (Phase 2 begins)

Runs the **frozen config** against the Binance futures testnet with the user's
new keys. This phase validates the **machine**, not the strategy: auth, data,
decisions, orders, reconciliation, funding accounting, crash recovery. PnL on
testnet is noise and is not a success criterion.

§0 of `STAGE2_PROMPT.md` remains in force. **No trials are consumed** — paper
is not a backtest. Holdout stays sealed. The frozen config is not modified.

---

## 0. What paper can and cannot establish

**CAN:** that the live code path faithfully implements the strategy; that
orders place, fill, and reconcile; that funding is recorded correctly; that the
watchdog, kill switch, and recovery work; that the cost-measurement pipeline
produces data.

**CANNOT:** fill quality or slippage (testnet books are thin and fake), real
fee rates, strategy performance. Four weeks of PnL has t ≈ 0.2 — a paper phase
"making money" means nothing and "losing money" means nothing. **Success is
operational, defined in §8, and pre-registered now.**

---

## 1. Configuration

The frozen deployment config, unmodified:

```
universe   = top-15 PIT majors by median quote volume
lookback   = 14, skip = 0, N = 10, k = 5, rank_buffer = 0
vol_target = 0.10, max_gross = 3.0, beta-neutral
capital    = $800 PAPER (sized to $800 even though testnet grants more)
fees       = taker-only; venue USDT-margined testnet
kill switch = 30% drawdown, ARMED
```

Testnet has no USDC-margined markets worth trusting; the USDC execution
question stays deferred to small real orders later. Paper runs USDT-margined.

## 2. Testnet wiring

1. Keys via environment variables only (`BINANCE_TESTNET_KEY` /
   `BINANCE_TESTNET_SECRET`). Assert at startup they are present and **never**
   log them. Grep the repo to confirm no key material is committed.
2. Base URL is a config switch: `testnet.binancefuture.com`. Assert at startup
   that when `testnet=True` the client refuses a mainnet URL and vice versa —
   one assertion, prevents the worst category of accident later.
3. **Symbol availability check, day one:** query testnet `exchangeInfo` and
   report which of the intended top-15 universe exists there. Testnet lists
   fewer symbols than production. If names are missing, paper trades the
   reduced set and the gap is **recorded as a known limitation** — this phase
   tests plumbing, so a reduced universe is acceptable and must be stated, not
   silently absorbed.
4. Live data path: decisions use klines fetched from the testnet REST API at
   decision time, gated by the same close-time rule as `PITView`. Log the
   fetch timestamp and the latest bar close used for every decision.

## 3. The shadow reconciliation — the most important check in this stage

Every day, after the live decision executes, run the **backtester** on the same
inputs (same universe snapshot, same bars the live path fetched) and compare:

- selected names, long and short
- target weights per name
- computed betas, vol estimate, gross leverage

**They must match to tolerance (weights to 1e-6).** A mismatch means the live
path and the research path implement different strategies — every backtest
conclusion would then be about a strategy that is not the one running. Any
mismatch is a same-day stop-and-diagnose, logged with both decision vectors.

This is the paper phase's real product: proof that the thing you validated and
the thing that trades are the same thing.

## 4. The four Phase-2 fixes — now due (STAGE2E §10, deferred to "before live")

Implement and test on testnet during this phase. None are optional before real
money:

1. **Multi-leg atomicity.** After each rebalance, compute the residual beta and
   tracking error of the actually-filled book vs target. If beyond tolerance
   (|beta| > 0.15 or tracking error > 20% of gross), repair immediately or
   flatten. Test by deliberately rejecting one leg (undersized order) and
   verifying the repair fires.
2. **Stop-execution cascade.** A stop fill triggers immediate reconcile and
   re-hedge/flatten, not a log line. Test by placing a tight stop that fires.
3. **Funding reconstruction.** `record_day()` reconstructs the position held at
   each settlement from fill history, not from the current book. Verify against
   a day containing a rebalance ~15s after settlement.
4. **POST retry idempotency.** On timeout/5xx after an order POST, query by
   `newClientOrderId` before any resubmit. Test by simulating an ambiguous
   response (drop the response in a wrapper, verify the query-first path runs).

Each gets a test run on testnet with the induced condition, logged with
evidence.

## 5. Testnet quirks the harness must survive

- **Balance resets.** Testnet wipes balances periodically. Detect: if equity
  jumps in a way inconsistent with positions and recorded PnL (e.g. resets to
  a round number with no fills), log `testnet_reset`, re-baseline the paper
  equity series, and **do not fire the kill switch.** A reset must not
  masquerade as a 100% drawdown or a windfall. The kill-switch logic keys off
  the re-baselined series.
- **Thin books.** Wide testnet spreads may reject or badly fill orders. Record
  it all in the costlog; do not tune the strategy around testnet
  microstructure.
- **Symbol gaps** per §2.3.

## 6. The cost pipeline — building the dataset that replaces the n=1 figure

For every fill: decision price (the price the sizing assumed), submitted price,
fill price, fee paid, timestamp deltas (decision → submit → ack → fill). Store
in `costlog` with a `venue=testnet` tag so testnet rows can never contaminate a
future real-cost estimate.

The numbers themselves are not trustworthy for markets — the **pipeline** being
exercised is the deliverable. When small real orders eventually run, the same
pipeline produces the measured slippage that replaces the 5 bps assumption.

## 7. Daily report

One compact block per day, appended to a running log:

```
date | equity | daily PnL | positions (n) | gross lev | beta | skips+reasons |
shadow reconciliation: MATCH/MISMATCH | funding recorded vs expected |
watchdog heartbeats OK | anomalies
```

Weekly: cumulative equity curve, funding accounting reconciliation
(sum of recorded settlements vs exchange income history), any drift between
paper equity and exchange-reported balance beyond fees.

## 8. Pre-registered success criteria — write into NOTES §46 before starting

The paper phase **passes** after a minimum of **28 consecutive calendar days**
in which:

1. **Shadow reconciliation matched every trading day** (zero unexplained
   decision mismatches; explained ones fixed and re-verified)
2. **Funding accounting reconciles** to the exchange's income history within
   $0.01 cumulative
3. **No unrecovered crash**: every failure (including at least one deliberately
   induced kill of the process mid-cycle) recovered to a correct book via
   reconcile without manual repair
4. **All four §4 fixes demonstrated** with induced-condition tests
5. **Kill switch and watchdog verified armed** (heartbeat gap test fires the
   alert; drawdown computed on re-baselined series)
6. **Zero silent errors**: every exception surfaced in the daily report

**PnL is explicitly not a criterion.** If all six hold for 28 days, the machine
is validated. The clock restarts only on criterion-1 or criterion-3 failures;
lesser issues are fixed and noted without restarting.

## 9. What passing paper does and does not license

- **Does:** the machine is trustworthy; the project is ready for the holdout
  decision and, after it, small real orders to measure true costs.
- **Does not:** validate strategy performance (testnet PnL is noise), justify
  skipping the holdout, or replace the slippage measurement (that needs real
  fills). The 5 bps figure remains an assumption until real orders exist.

## 10. Order of work

1. §8 criteria into `NOTES` §46, dated, before the first paper day
2. §2 wiring + symbol availability report
3. §3 shadow reconciliation live from day one
4. §4 fixes implemented and induced-tested during the first two weeks
5. Daily reports; weekly reconciliation
6. At 28 clean days: report against all six criteria, and stop

## 11. Acceptance

- §46 criteria recorded before day one
- No key material in the repo; URL/flag assertion present
- Testnet symbol coverage of the top-15 reported
- Shadow reconciliation running daily with logged decision vectors
- All four §4 fixes with induced-condition evidence
- Reset handling demonstrated (or explicitly untriggered after 28 days)
- Costlog rows tagged `venue=testnet`
- 28-day verdict against the six criteria, PnL excluded
- Budget unchanged (15 of 25); holdout sealed

## 12. Do not

- Treat testnet PnL, fills, or slippage as evidence about the strategy
- Tune any strategy parameter based on paper behaviour
- Let a testnet balance reset fire the kill switch
- Log testnet costs without the venue tag
- Touch the holdout

# Stage 2b — Corrections, and Stage 3 — Paper trading

Two independent tracks. **Part A blocks the grid. Part B runs in parallel**
with the backfill, which takes hours and otherwise leaves you idle.

Supersedes conflicting text in `STAGE2_PROMPT.md` and `STAGE2A_REMEDIATION.md`.

---

# PART A — Corrections (blocking)

Findings from `tools/diagnose.py` against the real database:

```
KLINES   119,493 bars / 154 symbols   2020-01-01 -> 2026-07-31 (6.58y)
FUNDING  484,113 settlements / 154 symbols
FILTERS  877 symbols; BTCUSDT MIN_NOTIONAL = $50.00
liquid universe (>=$5M median daily): 34
  gross 1.0x  tradeable   0   <-- BELOW N=10
  gross 2.0x  tradeable  33
```

## A1. `min_weight_fraction` is wrong — 0.25 should be 0.5

**This authorises a one-line change to `pitdata/store.py`, which was
otherwise frozen. Nothing else in `pitdata/` may be touched.**

The "0 tradeable at 1.0x" result is mostly artifactual. Two constants held the
same quantity and disagreed:

- `diagnose.py` printed `0.5 × L × C / N` → $5.00
- `store.tradeable_universe` used `min_weight_fraction = 0.25` → $2.50

The 0.25 double-counted: it applied a 0.5× rank factor *and* a 0.5× vol
factor, but vol scaling is already inside `L`. Since `STAGE2_PROMPT.md` §2.3.4
sets the weight band at `[0.5×, 1.5×]` of leg average, the correct value is
**0.5**.

```python
min_weight_fraction: float = 0.5,   # was 0.25 -- double-counted vol scaling
```

Update the docstring to state that this must equal the lower bound of the
§2.3.4 weight band, and that changing one without the other silently breaks
`MIN_NOTIONAL` enforcement.

After the change, `tests/test_lookahead.py` must still be **13/13**. Test
`tradeable_universe_respects_min_notional` may need its fixture arithmetic
updated — verify the intent still holds (a $100-floor symbol excluded, a
$5-floor symbol included) rather than tuning numbers until it passes.

## A2. Eliminate the duplicated constant

The bug existed because one quantity lived in two places. Fix the structure,
not just the value.

Export from `pitdata/store.py`:

```python
MIN_WEIGHT_FRACTION = 0.5   # single source of truth
```

`tradeable_universe` defaults to it; `tools/diagnose.py` imports it rather
than hardcoding 0.5. Add **Test 15**: assert the value used by
`tradeable_universe` equals `MIN_WEIGHT_FRACTION` and equals the §2.3.4 lower
band constant in the weights module. Three places, one number, asserted equal.

## A3. The `L >= 1.0` floor is real — instrument it

With the correction, the requirement is `0.5 × L × 100/10 >= 5`, i.e.
**`L >= 1.0` exactly**. Not comfortably above — precisely at. Any rebalance
where vol-targeting pulls realised gross below 1.0x fails
`below_min_notional` and skips.

This makes `STAGE2A_REMEDIATION.md` §4 the highest-priority item in that
document. Do not change the filter yet; report the numbers as specified there.

## A4. Complete the backfill — this is the long pole

154 symbols out of 877 in exchangeInfo. Run the unlimited backfill.

Why it matters more than it looks: breadth is your **only** lever on
statistical power. `IR ≈ IC × √breadth`, so more simultaneous bets raises
achievable Sharpe for the same skill. You cannot extend the calendar, and
higher rebalance frequency does not help (the `√T` and per-period Sharpe
terms cancel exactly). Breadth is it.

Concretely: 5 long and 5 short from 34 candidates means trading the top and
bottom 15%. From ~150 it is ~3%, which is a far stronger signal. And the
planned N=20 expansion at $200 equity does not work at 34 candidates.

Budget several hours. Ingest is idempotent — interrupting and resuming is
safe. Re-run `tools/diagnose.py` afterwards; expect ~150 liquid symbols and
the 1.0x tradeable row to roughly match the liquid count.

## A5. Holdout boundary

Data ends **2026-07-31**, not 2026-08-31. Holdout is 2025-01-01 → 2026-07-31,
**1.58 years**. Update the runner so it does not silently include an empty
month, and update the reported figure.

Restating the consequence, because it governs how results should be read: at
1.58 years the holdout only confirms a true Sharpe above roughly 1.6. The
realistic 0.7–1.0 range is **below what your holdout can resolve**. A holdout
Sharpe of 0.8 means "consistent with working, not confirmed," and that is the
best available outcome. Print this caveat alongside the holdout result so it
cannot be over-read later.

## A6. BTCUSDT at $50 — no action

Correctly handled. BTC is the beta *reference*; beta uses its returns, not a
position. Only 10 of 877 symbols exceed $5, so the constraint binds far less
than feared. Add a one-line comment in the beta code noting BTCUSDT is
intentionally a reference and may be untradeable at small capital.

## A7. Order

1. A1 + A2, re-run all tests (13/13 plus Stage 2 suite green)
2. A5
3. A4 — start the backfill, then move to Part B while it runs
4. After backfill: re-run `diagnose.py`, then `STAGE2A_REMEDIATION.md`, then the grid

Running the grid before A4 burns trials on a universe about to be replaced.

---

# PART B — Paper trading harness

## B0. What this is for

**Read this before writing code, because the wrong mental model produces the
wrong system.**

Paper trading **cannot** tell you whether the strategy makes money. Testnet
fills are synthetic, its book is thin and unrealistic, and its funding rates
are not real. Even on a live account, distinguishing Sharpe 0.7 from zero
takes 4+ years. **Any PnL figure this produces is not evidence about edge.**
If the harness reports a headline PnL, someone will eventually read it as one.

It exists for two things nothing else can provide:

**1. Execution-stack validation.** Signing, reconnects, partial fills,
rejections, restarts, rate limits, quantity quantisation, funding
settlements. Every one of these will break. Each is cheap to find now and
expensive to find with real money.

**2. Cost-model ground truth — the higher-value one.** Real fee charges, real
funding debits, real fill prices versus the mid you decided on. This directly
attacks the largest open question in the project: taker costs demand ~82%
more gross edge than maker, and that gap is worth more than any signal we
will find. Right now those numbers are assumptions.

## B1. Hard rules

- **Testnet only.** No production keys anywhere in this codebase. No mainnet
  endpoint, not even commented out.
- **The exchange is the source of truth for positions.** On startup and after
  any reconnect, reconcile from the exchange API. Never trust a local state
  file — it goes stale and lies precisely when you need it.
- **Shared weight computation.** The live bot imports the *same* module the
  backtester uses. Two implementations will diverge, and the divergence will
  be silent. This is also what makes live-vs-backtest tracking error
  measurable later.
- **Fail closed.** Any unhandled exception flattens and halts. It does not
  retry blindly, and it does not continue with unknown state.
- Keys in environment variables. Never in the repo, never in logs.

## B2. Phase 1 strategy: deliberately trivial

Do **not** run the momentum strategy. There is no validated config yet, and
running an arbitrary one would invite reading its PnL as a result.

Phase 1: hold one long and one short in fixed, liquid symbols, rebalance
daily at 00:00 UTC, sized like the real strategy would size them. That is
enough to exercise every code path while making the PnL obviously
uninterpretable.

Phase 2, only after the grid and holdout are done: the real config.

## B3. Components

```
live/client.py       testnet REST/WS wrapper; auth, retries, rate limits
live/reconcile.py    position/order state from the exchange
live/trader.py       main loop; imports backtest.weights
live/watchdog.py     separate process, separate lifecycle
live/killswitch.py   one script, flattens everything, nothing else
live/costlog.py      records expected vs actual for every fill
```

### Watchdog layering

Order matters — a watchdog on the same machine dies with that machine.

1. **Exchange-side reduce-only stops**, placed at entry. Survives process
   crash, power loss, ISP failure. The only protection that works while you
   are asleep and offline.
2. **Watchdog process.** Trader writes a heartbeat every 30s; watchdog
   flattens if it goes stale past 120s. Must share **no** code path with the
   trader — no shared client object, no shared config loader. The failure
   being defended against is a trader that is alive but wedged, and shared
   code means shared wedge.
3. **Manual kill switch** runnable from your phone.

## B4. Cost logging — the deliverable that matters

For every fill record: timestamp, symbol, side, intended price (mid at
decision), actual fill price, quantity, fee charged, fee currency, whether it
was maker or taker, and the order type submitted.

For every funding settlement: timestamp, symbol, position notional, rate
applied, amount charged.

Then a weekly comparison against the backtest's assumptions:

```
assumed taker fee     0.050%   actual  X.XXX%
assumed maker fee     0.020%   actual  X.XXX%
assumed slippage      0.000%   actual  X.XXX%   <- the interesting one
funding: assumed vs actual, per settlement
```

Slippage was assumed zero on the argument that ~$100 notional is a rounding
error on a liquid book. **That assumption has never been tested.** If it is
wrong, every backtest number moves.

Caveat honestly: testnet fills are synthetic, so the slippage figure is
indicative, not authoritative. Fees and funding mechanics are more
trustworthy than fill quality.

## B5. Failure injection — acceptance criteria

The harness is not accepted until each is performed and survived:

| Injection | Required behaviour |
|---|---|
| `kill -9` trader with open position | Watchdog flattens within 120s |
| Disconnect network entirely | Exchange-side stop is what saves you |
| Restart trader | Reconciles from exchange, does not double-position |
| Submit unquantised quantity | Rejection caught, logged, no crash |
| Submit below `MIN_NOTIONAL` | Rejected, logged, rebalance skipped cleanly |
| Trip the rate limit | Exponential backoff, no hammering |
| Hold across a funding settlement | Charge appears; sign matches `costs.py` |
| Kill the WebSocket mid-session | Reconnect and resync without gaps |
| Set system clock forward 5 min | Timestamp rejection handled explicitly |

Test 1 and 2 are the ones people skip and later regret. Do them physically —
actually pull the cable.

`NOTES.md` §7 flagged that `step_size` quantisation and order-level
`MIN_NOTIONAL` (with reduce-only exemption) are unmodelled. Rows 3 and 4
above are where those surface. Record what actually happens; it feeds back
into the backtest cost model.

## B6. Reporting

Daily, appended to `paper_log.jsonl`: rebalances attempted, skips by reason,
fills, total fees, total funding, reconnects, errors, watchdog triggers.

Weekly, the §B4 cost comparison.

**Never a headline PnL.** If someone asks how it is performing, the answer is
the cost-model comparison and the error log. That is what this measures.

## B7. Do not

- Use mainnet keys or endpoints
- Run the momentum strategy before the grid and holdout are complete
- Treat any paper PnL as evidence about edge
- Let live and backtest weight computation diverge
- Trust local state over the exchange
- Skip the failure injections because the happy path works

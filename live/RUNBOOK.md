# Paper-trading runbook (Binance USDS-M futures TESTNET)

Read `Claude/STAGE2B_CORRECTIONS_AND_PAPER.md` Part B first. This harness
measures the **execution stack** and the **cost model**. It does not — and
cannot — measure edge. It prints no PnL. If anyone asks how it is
performing, the answer is the cost comparison and the error log.

## 0. Layering (what saves you when)

| Layer | Survives | Where |
|---|---|---|
| 1. Exchange-side reduce-only `STOP_MARKET` (`closePosition`) per position, replaced daily | process crash, power loss, ISP failure, you being asleep | placed by `trader.py` after every fill |
| 2. Watchdog process: flattens if the heartbeat is stale > 120 s | trader alive-but-wedged | `live/watchdog.py` (shares **no** code with the trader) |
| 3. Kill switch from your phone | everything else | `live/killswitch.py` (stdlib only) |

## 1. Setup

1. Create testnet keys at the Binance futures testnet UI (GitHub login).
   They only work against `testnet.binancefuture.com`. There are no
   production keys anywhere in this repo and nothing here can talk to
   mainnet (`tests/test_live.py::test_no_mainnet_anywhere_in_live`).
2. Put the keys in the environment of every shell that runs a component:
   ```
   $env:BINANCE_TESTNET_API_KEY    = "..."
   $env:BINANCE_TESTNET_API_SECRET = "..."
   ```
   Never in a file in the repo. `.env` is git-ignored as a courtesy, not an
   invitation.
3. Account must be in **one-way** position mode (the trader refuses hedge
   mode) and have testnet USDT (the faucet gives ~10k; the trader sizes at
   a `$100` equity cap regardless, so the real `MIN_NOTIONAL` floor is
   exercised).
4. Verify the WebSocket host in `live/client.py` (`WS_BASE`) against the
   current testnet docs; it has been renamed before. REST is the source of
   truth, so a wrong WS host only costs telemetry — reconnects are logged
   and every reconnect triggers a REST reconcile.
5. Offline checks first: `python tests/test_live.py` (fake exchange, no
   network).

## 2. Running

Three terminals, in this order:

```
python live/watchdog.py                 # 1. separate process, separate lifecycle
python live/trader.py --once            # 2. one rebalance now, exit: proves the path end to end
python live/trader.py                   # 3. daily loop at 00:00 UTC (+15 s settle grace)
python live/trader.py --fee-mode maker  #    post-only LIMIT at the touch, 30 s then cancel
```

What Phase 1 does: long `ETHUSDT`, short `BNBUSDT`, sized by the backtester's
own `rank_weights → beta_hedge → vol_target_scale` at a $100 equity cap,
rebalanced daily, stops replaced after each rebalance. That is the whole
strategy. Its PnL means nothing; that is deliberate.

Phase 2 (the momentum config) is refused by the code until the grid and
holdout exist.

Outputs:

| File | Contents |
|---|---|
| `paper_log.jsonl` | every reconcile, plan, fill, rejection, skip, stop, halt; one `daily` record per day (counts and exchange-reported fee/funding totals — **no PnL field exists**); `watchdog_trigger` records |
| `paper_costs.jsonl` | one record per fill (intended vs actual price, fee, maker/taker, slippage bps) and per funding settlement (expected vs actual, sign check) |
| `live/state/heartbeat` | a timestamp, nothing else; the watchdog reads it |

Weekly:

```
python -c "from live.costlog import CostLog, weekly_report; import time; weekly_report(CostLog(), int((time.time()-7*86400)*1000))"
```

## 3. Kill switch from a phone

Any SSH client (Termux, Blink, …) to the machine:

```
cd C:\Stock; python live/killswitch.py
```

Exit code 0 = flat. Non-zero = something remains; the output says what.
It needs only the two env vars and Python; it imports nothing from the
trader or client.

## 4. Failure injections — acceptance (B5)

Not accepted until each row is **performed and survived**. Record the
observation in the last column and keep this file updated. Rows 1–2 are
physical: actually kill the process, actually pull the cable.

| # | Injection | How | Required behaviour | Evidence | Observed (date, notes) |
|---|---|---|---|---|---|
| 1 | `kill -9` trader with open position | `Stop-Process -Id <pid> -Force` while positions are open | watchdog flattens within 120 s (+ poll interval) | `watchdog_trigger` in `paper_log.jsonl`; `positionRisk` all zero | |
| 2 | Disconnect network entirely | pull the cable / disable Wi-Fi with positions open, wait > 120 s, reconnect | the **exchange-side stop** is the protection here (watchdog cannot reach the exchange either); on reconnect trader reconciles, watchdog may fire late — both logged | `stop_placed` records exist before the cut; `reconcile why=ws-reconnect/startup` after | |
| 3 | Restart trader | Ctrl-C, `python live/trader.py --once` with positions open | reconciles from exchange, plan `deltas` empty or tiny, no double position | `plan` record: `current` == exchange, `deltas` ≈ {} | |
| 4 | Unquantised quantity | `--once --inject unquantised` | `-1013` caught, logged, no crash, no position | `order_rejected code=-1013` ×2, exit 0 | |
| 5 | Below `MIN_NOTIONAL` | `--once --inject below-min-notional` | `-4164` caught, logged, rebalance skipped cleanly | `order_rejected code=-4164` ×2, positions unchanged | |
| 6 | Rate limit | hammer a public endpoint from a second script until 429 | exponential backoff honouring `Retry-After`, no hammering; 418 = you overdid it, wait it out | `backoff ...s` warnings; `daily.backoffs` count | |
| 7 | Hold across a funding settlement | leave positions open through 00:00/08:00/16:00 UTC | charge appears in `income FUNDING_FEE`; sign matches `backtest.costs` (long pays positive rate) | `funding` record with `sign_ok: true` in `paper_costs.jsonl` | |
| 8 | Kill the WebSocket mid-session | `--inject ws-kill` (or drop the socket at the firewall) | reconnect with backoff; a REST reconcile runs on every reconnect so nothing is missed | `reconcile why=ws-reconnect`; `daily.reconnects` ≥ 1 | |
| 9 | System clock forward 5 min | `--once --inject clock-skew` (or actually set the clock) | `-1021` handled explicitly: one server-time resync, then success; a second rejection halts | `daily.timestamp_resyncs == 1`, exit 0 | |
| 10 | Unknown position on the account | open a manual SOLUSDT position in the testnet UI, start the trader | halts (exit 2) and flattens; `--flatten-unknown` to adopt-and-flatten instead | `halt reason=unknown positions` | |
| 11 | Exception mid-rebalance | `--once --inject raise` | flatten and halt, exit 2, never continue blind | `halt`, `halted remaining={}` | |

Rows 3–5 are where `NOTES.md` §7's unmodelled order-level `MIN_NOTIONAL`
(reduce-only exemption) and `step_size` quantisation surface. Whatever the
exchange actually does feeds back into the backtest cost model.

## 5. Caveats — say these out loud when reporting

- Testnet fills are synthetic and its book is thin: the **slippage** figure
  is indicative, not authoritative. Fee and funding *mechanics* are more
  trustworthy than fill quality.
- No number this produces is evidence about edge. Distinguishing Sharpe 0.7
  from zero would take 4+ years on a real account.
- `websocket-client` (already installed) is used only for the optional
  user-data stream; `--no-stream` runs REST-only with no loss of state
  correctness.

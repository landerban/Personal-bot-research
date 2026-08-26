# xsmom — point-in-time dataset builder

Stage 1 of the cross-sectional momentum project. Builds a Binance USDS-M
futures dataset where lookahead bias is structurally impossible, not merely
discouraged.

## Run order

```bash
python3 build.py filters      # DO THIS FIRST, AND DAILY FROM NOW ON
python3 build.py symbols      # enumerate every symbol ever listed
python3 build.py backfill --limit 20   # test on 20 symbols before the full run
python3 build.py audit
python3 build.py tradeable --capital 100 --leverage 2 --n 10
```

`filters` is first for a reason. Binance publishes only *current* exchange
filters — historical MIN_NOTIONAL is unrecoverable. Every day you don't
snapshot is a day whose true constraint is lost forever. Put it in cron today.

## The guarantee

All reads go through `PITView`, frozen at an `as_of` timestamp:

```python
store = PointInTimeStore("xsmom.db")
for view in store.iter_views(start_ms, end_ms, step_ms=86_400_000):
    universe = view.tradeable_universe(capital=100, gross_leverage=2, n_positions=10,
                                       min_quote_volume=5_000_000)
    ranked = sorted(universe, key=lambda s: view.trailing_return(s, lookback=28, skip=2) or -9e9)
    # ... nothing here can see past view.as_of
```

Enforced by:

1. **`close_time`, never `open_time`.** A bar stamped `open_time=2024-01-01`
   isn't knowable until 2024-01-02. Filtering on `open_time` leaks a full bar
   of future — for a daily strategy, exactly the horizon being predicted.
2. **No escape hatch.** `PITView` is `__slots__`-locked and exposes no
   connection, cursor, or raw-SQL method. Tested.
3. **Monotonic clock.** Time cannot move backwards within a run.
4. **A sweep test** asserting no public reader returns data past `as_of`, which
   fails if a future method is added without a gate.

13 adversarial tests: `python3 tests/test_lookahead.py`

## Survivorship

The symbol list comes from enumerating the S3 bucket, **not** `exchangeInfo`.
`exchangeInfo` returns only symbols listed *today*, so a universe built from it
silently drops every delisted contract and biases results toward survivors. The
bucket retains delisted directories.

Universe membership is derived per-date from bars that existed at `as_of`, so a
symbol delisted in 2022 correctly appears in 2021 backtests.

## Known limitations

- **Historical filters are unknown.** Pre-first-snapshot dates apply today's
  MIN_NOTIONAL to past dates. `audit_filter_coverage()` reports the size of
  this gap rather than hiding it.
- **History-length check uses elapsed time**, from a cached first-bar time.
  For a symbol with gaps in *distant* history this differs slightly from a bar
  count. Gaps in the recent window are still caught.
- **Liquidity uses median, not mean**, so a single listing-day volume spike
  can't promote an illiquid symbol. Tested.
- **Timestamps normalised** — some 2025+ dumps switched ms → µs.

## Not yet verified — check before trusting the sizing

`MIN_NOTIONAL` is **per-symbol**, not a uniform $5 floor. I could not reach
Binance from the build environment to confirm actual values. Run
`python3 build.py filters` and read the output. If BTCUSDT's is $100, it is
untradeable at $100 capital and the sizing derivation needs redoing.

Sizing rule this encodes: `C >= 20N / L`, from requiring the smallest position
(≈0.25× average after rank-weighting and vol-scaling) to clear the floor.
At L=2, N=10 → C ≥ $100.

## Next

Stage 2: the event-driven backtester on top of this — rank-weighted,
BTC-beta-neutral, 20% vol target, `{7,14,28} × {skip 0,2}` grid.

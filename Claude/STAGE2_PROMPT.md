# Stage 2 — Event-driven backtester

You are implementing the backtester for a cross-sectional momentum research
project on Binance USDS-M perpetual futures. Stage 1 (the point-in-time data
store) is complete, tested, and **must not be modified**.

Read this whole document before writing code.

---

## 0. The single most important thing

Every numeric constraint below was **derived from arithmetic**, not chosen by
preference. They are not defaults to improve on. If you change one because it
"performs better," you have invalidated the experiment rather than improved it,
because the result will have been selected on the test data.

You will feel pulled to:

- add a signal or feature that seems obviously useful
- tune a parameter because the Sharpe looks low
- retry the holdout after a disappointing result
- bypass `PITView` for a "quick" direct query
- refactor Stage 1 while you're in there

**Do none of these.** If you believe a constraint is wrong, stop and write your
reasoning in `NOTES.md`. Do not act on it. The user decides.

---

## 1. What exists

```
pitdata/store.py      PointInTimeStore, PITView  -- DO NOT MODIFY
pitdata/download.py   Binance public-dump ingest -- DO NOT MODIFY
build.py              CLI for dataset construction
tests/test_lookahead.py   13 adversarial tests   -- MUST KEEP PASSING
```

`PITView` is frozen at an `as_of` timestamp and gated on `close_time`. It is
`__slots__`-locked and exposes no connection, cursor, or raw-SQL method.

**Hard rule: backtest code touches the database only through `PITView`.**
No `sqlite3.connect`. No `store._conn`. No pandas `read_sql`. If you find
yourself wanting raw access for performance, the answer is to make `PITView`
faster via a new gated method with a test — not to go around it.

Available on `PITView`:

```python
view.as_of                                     -> int (ms)
view.klines(symbol, interval="1d", limit=100)  -> list[Bar]
view.latest_close(symbol, interval="1d")       -> float | None
view.trailing_return(symbol, lookback, skip=0) -> float | None
view.realised_vol(symbol, window=30)           -> float | None   # NOT annualised
view.funding(symbol, since=None)               -> list[(ts, rate)]
view.universe(min_quote_volume, lookback_days=30, min_history_days=60)
view.min_notional(symbol)                      -> float | None
view.tradeable_universe(capital, gross_leverage, n_positions, min_quote_volume)
```

`store.iter_views(start, end, step_ms)` walks the clock forward. The clock
cannot move backwards within a run.

---

## 2. Strategy specification

Cross-sectional momentum, dollar-neutral, BTC-beta-neutral, volatility-targeted,
daily rebalance.

### 2.1 Universe (per rebalance)

```python
symbols = view.tradeable_universe(
    capital=<current equity>,
    gross_leverage=<config>,
    n_positions=<config>,
    min_quote_volume=5_000_000,
)
```

Use **current equity**, not initial capital — as the account compounds or
draws down, the set of symbols clearing `MIN_NOTIONAL` changes. Using initial
capital is a subtle lookahead when equity has fallen.

If `len(symbols) < n_positions`, **skip the rebalance and log it**. Do not
silently run a smaller book; that is a different strategy and would contaminate
the results.

### 2.2 Signal

`view.trailing_return(symbol, lookback=L, skip=S)` for each symbol.
Drop symbols returning `None`. Never impute.

### 2.3 Ranking and weights

1. Rank surviving symbols by signal, descending.
2. Take top `n_positions/2` long, bottom `n_positions/2` short.
3. **Rank-weight**: linearly interpolate weights so the extreme ranks get the
   largest magnitude. Normalise so each leg sums to 1.0 gross.
4. Clip any single weight to within [0.5×, 1.5×] of the leg average. This
   bound is what makes the `C >= 20N/L` sizing derivation hold — without it,
   the smallest position can fall below `MIN_NOTIONAL`.
5. Long leg positive, short leg negative, **each summing to the same gross** →
   dollar-neutral.

### 2.4 Beta neutralisation

Estimate each symbol's beta to BTCUSDT over a **60-day rolling window** using
returns available at `as_of`. Then scale the short leg so portfolio beta ≈ 0.

Order matters: rank-weight first, then beta-neutralise, then vol-target. Doing
beta-neutralisation last would break the weight clipping from 2.3.4.

If BTCUSDT is not in the universe on some date, still compute beta against it
(it is the market proxy, not necessarily a position). If BTCUSDT has
insufficient history, skip the rebalance and log.

### 2.5 Volatility targeting

Target **20% annualised** portfolio volatility. Estimate portfolio vol from the
weighted covariance of constituent returns over a 60-day window, or from
realised portfolio returns over a 60-day window — **implement one, document
which, do not test both** (that would be an extra trial).

Scale gross exposure to hit the target, then apply a **hard cap of 3× gross
leverage**. The cap exists because a beta-neutral book can run well under 20%
vol, and uncapped targeting would lever without bound into a correlation
regime change.

### 2.6 Execution timing — READ CAREFULLY

This is the most common source of silent lookahead in daily backtests.

- Signal is computed at `as_of` = **close of day T**.
- Orders execute at the **open of day T+1**.
- Fill price is day T+1's open, which is **not visible** at `as_of = close of T`.

So the loop must be: compute target weights at close of T, advance the clock to
T+1, then fill at T+1's open. Do **not** fill at day T's close — that assumes
you both saw the close and traded at it, which is worth a large spurious edge.

### 2.7 Costs — all mandatory

| Item | Value |
|---|---|
| Maker fee | 0.02% |
| Taker fee | 0.05% |
| Funding | 8-hourly, from `view.funding()`, actual historical rates |
| Slippage | 0 (position size ~$100 notional is a rounding error on a liquid book) |

Make maker/taker a **config flag**, defaulting to taker. Both must be
runnable — the maker-vs-taker comparison is the single largest lever in the
project and the user needs the number.

Fees apply to **turnover**, not gross. If a position is unchanged between
rebalances, it costs nothing to hold except funding.

Funding: apply every settlement (00:00/08:00/16:00 UTC) to positions held
across it. Sign convention: **positive funding means longs pay shorts.** Get
this backwards and the results invert. Write a test asserting the sign.

---

## 3. Configuration

```python
@dataclass(frozen=True)
class Config:
    lookback: int              # {7, 14, 28}
    skip: int                  # {0, 2}
    n_positions: int = 10
    vol_target: float = 0.20
    max_gross_leverage: float = 3.0
    beta_window: int = 60
    vol_window: int = 60
    min_quote_volume: float = 5_000_000
    initial_capital: float = 100.0
    fee_mode: str = "taker"    # "taker" | "maker"
    rebalance_ms: int = 86_400_000
```

`frozen=True` is required: config must be hashable so each run is logged under
a stable identity.

### Data splits — do not cross these

| Split | Range | Rule |
|---|---|---|
| Train | 2019-09-01 → 2023-12-31 | free use |
| Validate | 2024-01-01 → 2024-12-31 | free use |
| **Holdout** | 2025-01-01 → 2026-08-31 | **one look, ever** |

The holdout runner must **refuse to execute** unless invoked with an explicit
`--i-understand-this-is-the-only-look` flag, and must record its use in
`holdout_log.json`. If that file already shows a run, **abort** and print the
prior result. Do not add an override.

### Trial budget

**20 total.** The grid is 3 lookbacks × 2 skips = **6**. That leaves 14 for
unforeseen needs.

Every backtest execution — including exploratory and abandoned ones — appends
to `trials.jsonl`:

```json
{"ts": 1234567890, "git_commit": "abc123", "config_hash": "...",
 "config": {...}, "split": "train", "sharpe": 0.42, "max_dd": 0.18,
 "turnover": 12.4, "fee_drag": 0.31, "n_rebalances": 1200, "purpose": "grid"}
```

The git commit is not optional. Without it a six-month-old result is not
reproducible. If the working tree is dirty, record `"dirty": true`.

---

## 4. Deliverables

```
backtest/engine.py      event loop, portfolio accounting
backtest/weights.py     ranking, rank-weighting, beta-neutral, vol-target
backtest/costs.py       fees, funding accrual
backtest/metrics.py     Sharpe, DD, turnover, fee drag, deflated Sharpe
backtest/runner.py      CLI; trial logging; holdout guard
tests/test_backtest.py  the tests in §5
NOTES.md                decisions, surprises, anything you disagreed with
```

---

## 5. Required tests — the null tests matter most

These are not about proving the strategy works. They are about proving the
**harness** doesn't manufacture edge.

1. **Random signal → no edge.** Replace the momentum signal with a seeded RNG.
   Sharpe must be ≈0 before costs and clearly **negative** after. If a random
   signal is profitable, the harness has a bug — stop and report.

2. **Shuffled returns → no edge.** Shuffle each symbol's return series in time,
   keep the signal logic. Edge must vanish. This catches lookahead the sweep
   test misses.

3. **Constant-price → zero PnL, negative equity.** With flat prices, gross PnL
   must be exactly 0 and equity must decline by exactly the fees and funding.
   Validates the cost accounting in isolation.

4. **Analytic fee reconciliation.** Total fees must equal
   `Σ(turnover_notional × rate)` to within floating-point tolerance.

5. **Beta neutrality.** Realised portfolio beta to BTC over the run must be
   within ±0.15 of zero.

6. **Vol targeting.** Realised annualised vol within ±30% relative of the 20%
   target (loose — targeting is noisy — but it catches an inverted scale factor).

7. **Dollar neutrality.** At every rebalance, `abs(sum(weights)) < 1e-9`.

8. **Weight bounds.** No weight outside [0.5×, 1.5×] of leg average; no
   position below its symbol's `MIN_NOTIONAL`.

9. **Funding sign.** Construct a position held across a known positive funding
   settlement; assert a long **pays** and a short **receives**.

10. **Execution timing.** Assert fill price equals the next bar's open, never
    the signal bar's close. Test explicitly.

11. **Stage 1 regression.** `tests/test_lookahead.py` must still be 13/13.

Tests 1, 2 and 3 are the canaries. Run them before trusting any result.

---

## 6. Reporting

For each run print:

- Annualised return, annualised vol, Sharpe
- Max drawdown, and expected max DD per `σ/(2S)` for comparison
- Turnover (annualised, as multiple of capital)
- **Fee drag as a % of gross PnL** — the headline number
- Hit rate; average win vs average loss
- Number of skipped rebalances and why
- **Deflated Sharpe Ratio** given the trial count from `trials.jsonl`

And one line answering: **"Who is paying, and why would they keep paying?"**
If a run shows Sharpe > 1.5 and this can't be answered, treat it as a bug and
investigate before reporting it as a result. High Sharpe with no identifiable
counterparty is the signature of lookahead, survivorship, or unrealistic fills.

Reference point for calibration: crypto carry ran Sharpe 6.45 over 2020–2025,
fell to 4.06 from 2024, and turned negative in 2025 — a *real* edge with an
identifiable payer (crowded leveraged longs), which decayed as capital arrived.
That is what a genuine edge looks like. Sharpe 3 with no payer is a bug.

---

## 7. Style

- Python 3.12, stdlib + pandas/numpy. No new deps without asking.
- Type hints on public functions.
- Comments explain **why**, not what. Especially for anything non-obvious about
  time ordering.
- Small, focused commits. Do not bundle refactors with features.
- Fail loudly. No silent `except: pass`, no imputing missing data, no
  defaulting around a missing value. A skipped rebalance is logged, not hidden.

---

## 8. Order of work

1. Read `pitdata/store.py` fully. Run `tests/test_lookahead.py`.
2. Write `costs.py` + tests 3, 4, 9 first. Cost accounting is where results
   are most often quietly wrong, and it's testable without any strategy.
3. Write `weights.py` + tests 7, 8, 5, 6.
4. Write `engine.py` + tests 10, 1, 2.
5. Only then run the 6-point grid on **train**. Log every run.
6. Report. Do not touch validate until the user has seen train results.
7. Do not touch holdout at all. The user runs it.

If a null test fails, stop and report. Do not proceed to the grid with a
harness that manufactures edge.

# TEST NOTES — full run, 2026-08-27

Fresh, unfiltered run of both suites requested after Stage 2a remediation.
Complete logs were captured (not just PASS/FAIL lines); everything below is
taken from them.

## Run metadata

| | |
|---|---|
| Date | 2026-08-27 01:08:43 → 01:11:25 UTC+9 (local) |
| Code under test | identical to commit `328d7cb` (commit was made during the run; `git status` clean after, no file differed) |
| Python / numpy | 3.14.3 / 2.4.3 |
| `tests/test_lookahead.py` (Stage 1) | **13/13**, 1 s |
| `tests/test_backtest.py` (Stage 2 + 2a) | **20/20**, 162 s |
| Data | synthetic stores only — the real database was still being backfilled (no real-data trial was run or logged) |

Reproduce:

```
python tests/test_lookahead.py
python tests/test_backtest.py
```

---

## Stage 1 — 13/13 (unchanged, frozen)

`bar_invisible_until_closed`, `partial_bar_never_visible_midday`,
`latest_close_is_gated`, `trailing_return_skip_excludes_recent_bars`,
`insufficient_history_returns_none`, `funding_gated_at_settlement`,
`universe_is_point_in_time`, `liquidity_threshold_uses_median_not_mean`,
`clock_cannot_move_backwards`, `no_raw_sql_escape_hatch`,
`every_public_reader_is_time_gated`, `microsecond_timestamps_normalised`,
`tradeable_universe_respects_min_notional` — all PASS. Stage 2 test 11
re-runs this file in a subprocess and asserts on the `13/13 passed` line.

---

## Stage 2 — 20/20, what each one proves

Spec numbers refer to `STAGE2_PROMPT.md` §5; 12–14 to `STAGE2A_REMEDIATION.md` §5.

### The canaries (harness must not manufacture edge)

| Test | Spec | Evidence from this run |
|---|---|---|
| `random_signal_no_edge` | 1 | 30 seeds. **Gross** Sharpe: mean **+0.140**, SE 0.173, t **+0.81**, per-seed min −2.20 / max +2.09. **Net**: mean **−1.271**, SE 0.172, t −7.39, min −3.64 / max +0.51. Acceptance: \|mean gross\| ≤ 2 SE (0.35) ✔; net < gross ✔; net < −0.5 ✔ |
| `shuffled_returns_no_edge` | 2 | 30 seeds, per-symbol **demeaned** before shuffling, momentum signal kept. Gross Sharpe: mean **−0.060**, SE 0.141, t **−0.43**, min −2.17 / max +1.43. Acceptance \|mean\| ≤ 2 SE (0.28) ✔ |
| `constant_price_zero_pnl_costs_exact` | 3 | Flat alts, wiggling BTC excluded from universe by volume. `gross_pnl == 0.0` **exactly**; fees == turnover × 0.05 % (rel 1e-12); funding == independent day-by-day replay encoding the 00:00-old-book / 08:00+16:00-new-book convention (abs 1e-9); final equity == initial − fees + funding (rel 1e-12); equity strictly below initial |

Reading the null statistics: a single ~440-day run estimates annualised
Sharpe with SE ≈ 0.9, which is why the per-seed range is ±2 while the 30-seed
means are within a third of a Sharpe point of zero. The bound is 2 SE of the
*observed* cross-seed spread, so a real leak of +0.35 or more would fail;
seeds are fixed, so the outcome is deterministic run to run.

### Cost accounting

| Test | Spec | Evidence |
|---|---|---|
| `fee_reconciliation` | 4 | Σ per-rebalance turnover == engine total; total fees == turnover × rate; Σ per-rebalance fees == total (all rel 1e-12); no forced liquidations in that run so the paths are comparable |
| `funding_sign_unit` | 9 | `funding_cashflow(+2, 100, 1e-4) == −0.02` (long pays), `(−2, …) == +0.02` (short receives), both flip with negative rate; taker/maker fee arithmetic; unknown fee mode raises; settlement calendar in (T, T+1d] is exactly {00:00, 08:00, 16:00} |
| `funding_sign_engine` | 9 | Flat market, +rate on the (deterministic) long leg and −rate on the short leg → **both legs pay**, total funding < 0 and == replay; rates inverted → total funding > 0 |

### Weight construction

| Test | Spec | Evidence |
|---|---|---|
| `rank_weight_profile` | 2.3 | 5-name leg == [0.3, 4/15, 0.2, 2/15, 0.1] (ramp clipped at both ends, sums to 1 exactly); for k ∈ {1,2,3,5,8,25}: sum 1, all in [0.5×, 1.5×] avg, descending; two-leg output sums to 0 |
| `beta_hedge_math` | 2.4 | s = 1.1/0.9 reproduced; hedged Σ wβ = 0 to 1e-12; long leg untouched, short leg scaled uniformly; negative short-leg beta → `Skip`, never a fabricated scale |
| `dollar_neutrality` | 7 | \|Σ raw_weights\| < 1e-9 at all 439 rebalances of the shared factor run |
| `dollar_tilt_is_the_hedge` | 12 | Σ final == k(1−s) == gross·(1−s)/(1+s) to 1e-9 at every rebalance; tilt is real (max \|net\| **0.383**), so exact dollar-neutrality cannot be reintroduced silently |
| `weight_bounds_and_min_notional` | 8 | Band holds on raw *and* final weights every rebalance; at **$100 capital with a $4 floor** every executed position ≥ $4.00; with a **$50 floor** → 0 rebalances, every skip logged `universe_too_small` (never a smaller book) |

### Risk targeting (shared 500-day factor run, 439 rebalances)

| Test | Spec | Evidence |
|---|---|---|
| `beta_neutrality` | 5 | Realised beta of daily strategy returns to BTC: **+0.033** (band ±0.15) |
| `vol_targeting` | 6 | Realised annualised vol **0.192** vs 0.20 target (band 0.14–0.26) |
| `realised_leverage_recorded` | 14 | Every filled rebalance: finite realised gross leverage measured from executed fills, ≤ 3.0, equal to the decided gross to 1e-9; `n_scheduled == rebalances + skips` |

### Timing

| Test | Spec | Evidence |
|---|---|---|
| `execution_timing` | 10 | 2 % overnight gaps injected; every fill price == next bar's open **exactly** and ≠ the signal bar's close; `ts_fill == ts_decision + 1 day` |

### Tooling and guards

| Test | Spec | Evidence |
|---|---|---|
| `demeaned_db_is_faithful` | 13 | Per-symbol mean log return < 1e-10 after demeaning; source untouched (drift still present); open_time/close_time/volume/quote_volume/trades identical bar-for-bar; close/open ratio preserved (rel 1e-12); funding and MIN_NOTIONAL identical; `tradeable_universe` identical at 4 dates; refuses to overwrite an existing output |
| `runner_diagnose_never_logs_a_trial` | 2a §2.1 | Decomposition writes `diagnostics.jsonl`; `trials.jsonl` is never created; both runs produce rebalances |
| `runner_trial_log_and_holdout_guard` | §3 | Trial record carries commit, config hash, split, purpose; holdout refused without flag; with flag, look recorded as `started` **before** any result; a second look refused even with the flag; report/summarise run on a real result without error |
| `deflated_sharpe_sanity` | §6 | NaN with < 2 trials; DSR ∈ [0,1] and increases with observed Sharpe |
| `stage1_regression` | 11 | `13/13 passed` from a subprocess |

---

## Observations from the complete log (not visible in PASS-only output)

1. **Hit-rate artifact — fixed after this run.** The synthetic diagnose run
   printed `hit rate 7.00%`: the metric counted every flat day (no position
   held) as a non-win, and that run had positions on only ~200 of 1144 window
   days. On real data the same distortion would appear during universe gaps.
   `metrics.hit_rate` now excludes exactly-zero return days, and the report
   prints the number of active days so any dilution is visible. Sharpe is
   deliberately left computed over the full window — flat days *understate*
   it, which is the conservative direction. No test asserts on hit rate, so
   the 20/20 result is unaffected; the change is committed separately.

2. **Drift-decomposition noise floor.** On synthetic data with **zero true
   drift** (200 days), the decomposition still attributed 0.11 of a 0.62
   Sharpe — **18 %** — to "drift", because the finite-sample mean of each
   symbol is itself a persistent relic the signal can estimate. The real
   train decomposition therefore has a nonzero floor even if the strategy were
   pure trend-continuation. Interpret the real drift fraction relative to a
   floor of this order (it shrinks with sample length; train is ~8× longer),
   not relative to zero. One seed, one run — indicative, not a calibration.

3. **Forced liquidations behave as designed.** The synthetic diagnose run
   shows `forced liquidations: 10` — the synthetic data ends after 200 days
   while the train window continues, so every open position was settled at
   its last mark and logged. On real data this path fires on delistings.

4. **Preview of the Stage 2a §4 question (universe filter at 3×).** In the
   synthetic runs realised gross leverage sat at median **0.94–0.98** with
   **58–80 %** of rebalances below 1.0×. At $100 capital and N = 10 the
   smallest position is 0.5·L·C/N ≈ **$4.75** at L = 0.95 — under the live
   $5 floor. Real crypto idiosyncratic vol is higher than the synthetic 3 %/day,
   which pushes realised leverage lower still. Expect `below_min_notional`
   skips to be **material** on the real $100 grid. The instrumentation will
   give the number; per the ruling nothing changes until it does.

5. **Runtime.** 162 s for Stage 2, of which the two 30-seed null tests are
   ~60 engine runs. Acceptable; they are the canaries.

---

## What these tests do not cover

- No real-data result exists yet; nothing here says the strategy works.
- MIN_NOTIONAL is enforced on positions, not on order deltas (Binance rejects
  sub-floor non-reduce-only *orders*); `step_size` quantisation not modeled.
- Funding for 08:00/16:00 is marked at the day's open (≈2e-6 per settlement).
- Slippage is 0 by specification; maker mode assumes passive fills at the open.
- The 2 SE null bound has a ~5 % false-alarm rate per test in the abstract,
  but seeds are fixed so the current outcome is stable, not a coin flip.

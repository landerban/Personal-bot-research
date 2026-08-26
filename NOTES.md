# NOTES — Stage 2 decisions, surprises, disagreements

Everything here is a decision the spec left open, a surprise found while
building, or a place I believe the spec conflicts with itself. Per §0 I have
not acted on any disagreement beyond what was needed to make the required
tests self-consistent; the user decides.

---

## 1. RESOLVED — spec amended: the net-dollar tilt is the hedge, by design

Original finding (Stage 2): spec §2.3.5 makes the book dollar-neutral (legs
sum to +1/−1); §2.4 then scales the short leg by s = β̄_long/β̄_short so
portfolio beta ≈ 0. After that, sum(weights) ≠ 0 whenever the legs' betas
differ. Test 7 (`|Σw| < 1e-9`) and test 5 (realised beta within ±0.15) cannot
both hold on the final weights with a single leg scale.

**Ruling (Stage 2a, `STAGE2A_REMEDIATION.md` §1):** the spec was wrong, the
implementation is right. Independently verified: across 2000 random draws,
zero cases satisfy both constraints under one leg scale; the null-space
projection that would satisfy both violates the `[0.5×, 1.5×]` band in 100%
of cases. Dollar-neutrality was only ever a proxy for market-neutrality; once
beta-neutrality is genuine the proxy is redundant.

What stays, now pinned by tests:

- Test 7 asserts on the §2.3 construction output (`raw_weights`), which is
  dollar-neutral to 1e-15 by construction.
- The final book carries the deliberate net-dollar tilt. **Notation**: with
  `k` the vol-target scale (= long-leg gross after targeting) and `s` the
  short-leg beta scale, `Σ final = k·(1 − s)`, equivalently
  `gross·(1 − s)/(1 + s)` where `gross = k·(1 + s)` is total gross. The
  earlier note wrote this as `g·(1 − s)` with `g` meaning the long-leg gross,
  not total gross. Test 12 asserts the exact identity at every rebalance and
  that the tilt is actually nonzero, so exact dollar-neutrality cannot be
  silently reintroduced on the final book.
- Realised beta on synthetic factor data: +0.03.

## 2. SURPRISE: the naive shuffled-returns null test fails on a correct harness

Null test 2 (shuffle each symbol's returns in time → edge must vanish)
initially FAILED with gross Sharpe +2.24. Investigation over 30 seeds showed
a systematic +0.43 mean gross Sharpe (t = 2.7) — but it was **not lookahead**:

- A time-shuffle preserves each series' sample mean *exactly*. Every symbol
  keeps its full-sample drift, which in the shuffled series becomes a
  genuine, permanent, per-symbol property.
- Cross-sectional momentum legitimately harvests persistent cross-sectional
  drift differences. Trailing 14d returns correlate with per-symbol drift.
- Demeaning each series before shuffling removes exactly this channel:
  mean gross Sharpe over 30 seeds drops to −0.06 (t = −0.4).

The implemented test demeans before shuffling. **Caution for later**: if
anyone ever runs a shuffle test against the *real* dataset, demean per symbol
first, or momentum's drift-picking will look like lookahead.

Related: single-run Sharpe bounds are statistically naive — one ~440-day run
estimates annualised Sharpe with SE ≈ 0.9, so a "|SR| < 1" single-seed
assertion false-alarms ~27% of the time (and did, on the random-signal test
too: +1.49 on one seed, cross-seed mean +0.19, t = 0.7). Both null tests now
average 5 seeds and bound the mean at ~2 SE, with a loose per-seed guard
(|SR| < 3) that still catches gross mechanical leaks instantly.

## 3. Vol estimator: weighted covariance (the alternative was NOT tried)

§2.5 offers two estimators and says implement one, document which, don't test
both. Implemented: **ex-ante portfolio vol from the weighted sample
covariance of constituent daily returns** over `vol_window` (60d):
`sqrt(w'Σw) · sqrt(365)`. Plain sample covariance, no shrinkage. The
realised-portfolio-returns variant was not implemented or run — running both
and picking would be an extra trial.

Realised vol on synthetic factor data: 0.192 vs 0.20 target.

## 4. Funding conventions

- **Sign**: positive rate → longs pay shorts. Lives in exactly one place
  (`costs.funding_cashflow`); tested at unit and engine level.
- **Which book pays the 00:00 settlement**: the fill happens at the open
  (00:00), the same instant as the settlement. Convention: the *old* book
  (held across midnight) pays 00:00; the *new* (post-fill) book pays 08:00
  and 16:00. Encoded independently in the test replay so it can't drift.
- **Mark price**: all three settlements of day D are marked at day D's open —
  exact for 00:00 (the open *is* the 00:00 print), a no-lookahead
  approximation for 08:00/16:00 (daily bars have no intraday prices; the
  day's close is not knowable at 16:00).
- **Missing settlements**: a settlement absent from the funding table is
  counted and reported (`missing_funding_settlements`), never silently
  treated as zero — but the run continues. Early-history gaps are common.

## 5. Candidate filter is slightly stricter than the universe filter

`universe()` requires 60 days of history; a 60-return beta/vol window needs
61 closes. Symbols with exactly 60 bars (and symbols whose last-61-bar dates
don't align with BTCUSDT's — gaps) are dropped from candidacy *before*
ranking. Dropping before selection keeps the book size honest; dropping after
would quietly change the strategy. No imputation anywhere.

## 6. Skip and degenerate-data semantics

- Any skip (`universe_too_small`, `insufficient_candidates`,
  `btc_insufficient_history`, `btc_zero_variance`, `unhedgeable_beta`,
  `below_min_notional`, `missing_fill_bar`) → **hold the existing book**, log
  reason + timestamp. Never a smaller or partial book.
- `unhedgeable_beta`: if the short leg's weighted beta is ≤ 0 no positive
  scale can neutralise — skip. If both leg betas are ~0 the book is already
  neutral → s = 1, proceed.
- A held symbol with no bar today (delisting or data hole) is force-settled
  at its last mark, with a taker fee, and logged (`forced_liquidations`). A
  *pending target* symbol with no bar aborts that whole rebalance
  (`missing_fill_bar`) rather than partially filling a neutral construction.
- Equity ≤ 0 → bankrupt: run stops and says so.

## 7. Sizing and MIN_NOTIONAL

- Position sizes are fixed in **dollars at decision time** (weight × equity
  at close of T) and converted to units at the T+1 open. No T+1 information
  affects sizing.
- MIN_NOTIONAL is enforced on **positions** (checkable at decision time since
  fill notional == target dollars). Binance actually applies it to *orders*,
  and exempts reduce-only orders — small rebalance deltas below the floor
  would be rejected live. Not modeled; at $100 scale this deserves a note in
  any live-trading writeup. `step_size` quantisation also not modeled.
- `tradeable_universe` is called with `gross_leverage = max_gross_leverage`
  (3.0) — the only leverage in Config, per §2.1 "gross_leverage=<config>".
  Note the README's C ≥ 20N/L derivation used L = 2; at L = 3 the implied
  floor is C ≥ 67, so $100 still clears it, but the universe filter is
  slightly more permissive than an L = 2 filter would be. Flagging, not
  acting.

## 8. Annualisation = 365

Perps trade every calendar day; the equity curve has one point per calendar
day. 252 would overstate Sharpe by ~1.20×.

## 9. Trial accounting

- Unit/null tests run the engine on **synthetic** stores and do not touch
  real returns → they consume no trial budget and are not logged.
- Every execution through `backtest.runner` (real DB) appends to
  `trials.jsonl` — including errored runs (logged with `"error"`).
- Deflated Sharpe uses the number of **distinct config hashes** on the same
  split (latest record per config) as the trial count; re-running an
  identical config is not a new independent trial. Needs ≥ 2 trials, so the
  very first run reports DSR = n/a.
- The runner CLI restricts `--lookback` to {7,14,28} and `--skip` to {0,2} —
  anything outside the pre-registered grid requires a deliberate code change.

## 10. Holdout

The look is recorded in `holdout_log.json` **before** the run starts — a
crashed holdout run still spends the look. No override exists.

## 11. Environment

- Python 3.14.3 on this machine (spec said 3.12; nothing 3.12-specific used).
- numpy installed (sanctioned); pandas allowed but not needed, so not used.
- Stage 1 arrived as `files.zip` and was laid out unmodified into
  `pitdata/`, `tests/`, `build.py`; `tests/test_lookahead.py` = 13/13 before
  and after Stage 2 (test 11 re-runs it inside the Stage 2 suite).

## 12. Who is paying? (to be answered per-run before results are trusted)

For cross-sectional momentum the candidate payers are: late trend-chasers
paying for entries after moves are established, and holders liquidated into
weakness on the short side's continued declines. Funding asymmetry (crowded
longs paying shorts in alt rallies) can be either a cost or a tailwind here.
This must be argued against the *actual* run: if the grid shows Sharpe > 1.5
net of costs, that answer is not adequate until fee drag, turnover and the
long/short PnL split make the mechanism visible. High Sharpe with no
identifiable counterparty = bug until proven otherwise.

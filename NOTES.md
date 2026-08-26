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
average **30 seeds** (Stage 2a; was 5) and require the mean within 2 SE of
zero (bound ≈ 0.33), reporting mean / SE / t / min / max, with a loose
per-seed guard (|SR| < 3) that still catches gross mechanical leaks instantly.

### 2a. Consequence for the REAL data (Stage 2a §2) — drift vs trend

If momentum harvests persistent drift differences in shuffled data, it does
the same in real data. Some unknown fraction of any grid Sharpe is
drift-estimation (each symbol's full-sample mean, a proxy for disguised beta
in a mostly-rising sample) rather than trend-continuation (a behavioural
story with identifiable counterparties). They have different payers and
different decay profiles.

Required decomposition, run on **train only** for the best grid config:

- (a) real store → `Sharpe_real`
- (b) `xsmom_demeaned.db` (built by `tools/build_demeaned_db.py`, the
  unmodified engine run against it) → `Sharpe_demeaned`
- `Sharpe_real − Sharpe_demeaned` ≈ drift component.

It uses full-sample means and **could not be run live**; it is labelled so
everywhere it appears, and logged to `diagnostics.jsonl`, never
`trials.jsonl` (attribution of an existing result, not a new configuration —
no trial budget consumed). Decision rule: see §12.

**Detail where the tool deviates from the amendment's wording, and why**:
§2.1 says "adjust `close` prices". The tool scales **every price column of
the bar** (open/high/low/close) by the same `exp(−μ_s·t)`. The engine fills
at the *open*; an unscaled open against a scaled close would inject a
synthetic overnight jump of `+μ_s·t` every day, growing without bound, and
put the drift straight back in through the fills. Scaling the whole bar
leaves every intrabar ratio intact and demeans both the close-to-close and
overnight returns consistently. Volumes, timestamps, funding and filters are
untouched, so universe membership is byte-identical (Test 13).

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
  **Stage 2a ruling: measure before changing.** The filter admits symbols
  assuming 3× gross, but vol-targeting sets *realised* gross, and a
  beta-neutral crypto book at 20% vol will often land near or below 1.0×.
  With the `[0.5×, 1.5×]` band the smallest position is `0.5·L·C/N`, so at
  $100 and N=10 any realised L < 1.0 puts positions under the $5 floor and
  skips the whole rebalance. Instrumented (every filled rebalance records
  realised gross leverage, min position notional, binding MIN_NOTIONAL; the
  report shows the leverage distribution, `below_min_notional` skips as a
  fraction of scheduled rebalances, and min notional vs floor). Decision
  rule: if `below_min_notional` skips are rare, leave it; if material, change
  the filter to expected rather than maximum leverage — **with the numbers,
  not before**. Grid numbers go in §13.
- README sizing rule corrected (Stage 2a §4.1): `C ≥ 10N/L`, not `20N/L` —
  the old form counted the vol factor twice (once as 0.5×, again inside L)
  and gave the same $100 at N=10, L=2 by coincidence. `L` is realised
  leverage, not the configured cap.

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

Candidate explanations, with their payers:

1. **Trend-continuation** (behavioural): late trend-chasers paying for
   entries after moves are established; holders liquidated into weakness on
   the short side's continued declines. Funding asymmetry (crowded longs
   paying shorts in alt rallies) can be a cost or a tailwind. Identifiable
   payers; decays as capital arrives.
2. **Drift-harvesting** (Stage 2a §2): the signal is a noisy estimator of
   each symbol's *persistent* drift, so the book is long the symbols that
   went up over the whole sample and short the ones that went down. In a
   mostly-rising sample this is disguised beta with a cross-sectional face.
   **No identifiable payer** — nobody is systematically on the other side of
   "this coin drifted up" — and it will not survive a drift reversal.

Every payer in (1) is a trend-continuation story; if the edge is
substantially (2), none of them explain it.

**Decision rule**: the §2a decomposition attributes the best config's train
Sharpe between the two. If a **majority** is drift, the strategy is closer
to disguised beta than to momentum, and the results must say so plainly —
in the headline, not buried. Independently of that: if any run shows
Sharpe > 1.5 net of costs, the answer is not adequate until fee drag,
turnover and the long/short PnL split make the mechanism visible. High
Sharpe with no identifiable counterparty = bug until proven otherwise.

## 13. Stage 2a — grid instrumentation results (to fill after the re-run)

Pending the train grid re-run and the §2a decomposition.

## 14. Stage 2b — corrections (Part A)

- **A1/A2 — `MIN_WEIGHT_FRACTION` 0.25 → 0.5.** The one authorised change to
  `pitdata/store.py`. The 0.25 double-counted the vol factor (once as 0.5×,
  again inside `L`). Now a single exported constant; `weights.WEIGHT_BAND`
  uses it as the band's lower bound; `tools/diagnose.py` imports it; Test 15
  asserts all three agree. Effect on the partially-built store (220
  symbols): tradeable at 1.0× went from **0** (reported by the reviewer's
  diagnose on their copy) to **44** of 46 liquid — the "0 tradeable" was an
  artifact of the double count. Stage 1 still 13/13; the fixture's intent
  (a $100-floor symbol excluded, a $5-floor one included) holds unchanged.
- **A3 — the `L ≥ 1.0` floor is exact**, not comfortable: `0.5·L·100/10 ≥ 5`.
  The Stage 2a §4 instrumentation (leverage distribution, `below_min_notional`
  share) is therefore the first thing to read in the grid output. Synthetic
  runs put realised leverage at median ≈0.95, i.e. *just under* — expect this
  to bind on real data (see `TEST_NOTES.md` obs. 4). Nothing changes until
  the numbers say so.
- **A5 — holdout ends 2026-07-31** (1.58 y), where the data ends. The Binance
  monthly futures dumps also start at **2020-01-01**, not 2019-09-01, so the
  train split's effective first bar is 2020-01-01 and its first tradeable day
  ~2020-03 after warm-up; the nominal 2019-09-01 start is harmless (empty
  views skip and are logged). `ensure_data_covers()` refuses any split whose
  end the data does not reach, *before* the holdout look is recorded. The
  report prints the resolution caveat: 1.58 y resolves only a true Sharpe
  above ~1.6 (2 SE = 2/√1.58); 0.7–1.0 is below what the holdout can confirm.
- **A6 — BTCUSDT $50**: reference only, documented in `weights.py`.

## 15. Stage 2b — paper-trading harness (Part B)

Built offline while the backfill ran; 19 tests in `tests/test_live.py`
against an in-memory fake of the USDS-M REST surface with Binance's real
rejection codes. Design choices and their reasons:

- **REST is the only source of truth.** Positions, orders, fills, fees and
  funding are always read from the exchange; there is no local position
  file. The user-data WebSocket is telemetry: any reconnect triggers a REST
  reconcile, so a stream gap cannot become a state gap. `--no-stream` runs
  REST-only with no loss of correctness.
- **Dependency note.** `websocket-client` (already installed on this
  machine, not added by me) is imported lazily and only by the optional
  stream class. Say the word and it goes.
- **Watchdog and kill switch are stdlib-only and import nothing from the
  trader or client** — deliberately duplicated signing code. The failure
  they exist for is a wedged trader; shared code is a shared wedge. A test
  greps for this.
- **Phase 1 sizes through `backtest.weights` itself** (`rank_weights` →
  `beta_hedge` → `vol_target_scale`) at a **$100 equity cap** even though
  the testnet account holds ~10k, so `MIN_NOTIONAL` and `step_size` are
  exercised at the real scale. Phase 2 is refused in code.
- **No PnL anywhere.** Tests assert the daily record, the cost report and
  the rebalance result contain no `pnl` key.
- **Exchange-side stops** (`STOP_MARKET`, `closePosition`, mark-price
  trigger) are placed after every fill at ±20% — an operational safety
  distance, not a strategy parameter — and replaced daily.
- **Injections available in-process** (`--inject`): `below-min-notional`,
  `unquantised`, `clock-skew` (+5 min on the request timestamp → −1021 →
  one resync), `raise` (fail closed), `ws-kill`. All covered by tests.
  **Not doable by me**: the physical ones (kill −9 with the watchdog live,
  pull the cable), the rate-limit trip, the funding-settlement hold and any
  run against the real testnet — those need keys and a person. The runbook
  has the acceptance table with an "observed" column to fill in.
- **Unmodelled-in-backtest items surface here**, on purpose: order-level
  `MIN_NOTIONAL` with the reduce-only exemption, `step_size` floor-rounding
  (never up), and post-only (`GTX`) rejection when the touch moves. Whatever
  the exchange does with them feeds back into `costs.py`.
- **Not verified**: the testnet WebSocket host name (`WS_BASE`) — flagged in
  the runbook; a wrong host only costs telemetry.

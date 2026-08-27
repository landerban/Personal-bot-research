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
  `below_min_notional`, `missing_fill_bar`) → log reason + timestamp and
  **flatten to cash at the next open** (fees on the closing turnover,
  recorded in `flattens`). Never a smaller or partial book. *Originally
  "hold the existing book"; changed by user ruling 2026-08-27 after §13.1
  showed a held book drifting past 20× leverage on real data.* Repeated
  skips while already flat cost nothing.
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

## 13. Train grid — results, and why they are NOT results yet (2026-08-27)

Data: 832 symbols, 612,421 daily bars, 2020-01-01 → 2026-07-31; 179 liquid
/ 173 tradeable at 1.0× on the last day. Six trials logged (`trials.jsonl`,
commit `36f191d`, taker fees). Summary:

```
 lb skip  sharpe  ann_ret  ann_vol  max_dd   turn  fee_drag lev_med   <1.0x  bmn_skip  n_rb
  7    0    1.12   56.52%   50.46%  34.79%  120.9    22.43%    0.46     96%     32.4%   826
  7    2   -2.05      n/a  216.25% 102.31%   52.6       n/a    1.10     14%     45.5%    44   BANKRUPT
 14    0   -1.58      n/a  295.21% 104.94%   37.5       n/a    1.08     31%     45.0%    49   BANKRUPT
 14    2   -1.47      n/a  463.81% 106.75%   45.1       n/a    1.05     40%     42.4%    63   BANKRUPT
 28    0    0.83   42.94%   72.84%  51.81%   62.5    15.69%    0.46     93%     40.2%   705
 28    2   -0.51      n/a  819.24% 118.14%   27.9       n/a    1.05     34%     43.4%    61   BANKRUPT
```

Four wipeouts and two "winners" at 2.5–3.6× the vol target. **None of
these numbers describe the strategy.** Postmortem replays of the logged
trials (`tools/postmortem.py`, `diagnostics.jsonl`, no budget spent) show
one harness behaviour behind all six:

### 13.1 A skipped rebalance holds the stale book — with unbounded leverage

`lb7/skip2`: the book decided 2020-07-21 at $118 equity (short IOST, TRX,
ADA, BNB, EOS; gross 1.01, s = 0.90, max weight 14%) was still on the books
on **2021-02-10 with $5.63 of equity** — ADA alone lost $21.7 that day,
386% of equity. Of 528 scheduled rebalances, 484 were skipped
(`universe_too_small` 244, `below_min_notional` 240) and the engine holds
the previous book through a skip. As equity fell, the held notional did
not, so effective leverage went past 20× — through the §2.5 hard cap of 3×,
which is only enforced at decision time. The hedge scale was NOT the
problem: s stayed in [0.90, 1.23] for that run.

`lb7/skip0` is the same artifact with the sign flipped. Ex-ante vol of the
hedged unit book on real data is ~89% (median; p95 164%), so the vol
target sets k ≈ 0.23 and gross ≈ 0.46; the smallest position is then
0.5 × 0.46 × $100 / 10 ≈ **$2.3 < $5**, and 512 of 1582 rebalances skip on
`below_min_notional`. The strategy spent most of 2021 holding whatever
book last cleared the floor, long-leg PnL +$521 vs short −$143, plus
+$120 of funding from being short through a bull market. That is a
buy-and-hold of stale books, not cross-sectional momentum, and its Sharpe
1.12 says nothing about the signal.

Why the null tests did not catch it: synthetic universes never skip, so a
held book never diverged from a decided one.

**Decision required (§0: not mine to make).** Two consistent readings of
§2.1 "skip the rebalance and log it":

- (a) **Flatten on skip**: no valid book can be formed → go to cash; the
  §2.5 cap is honoured at all times; at $100 the strategy will then sit in
  cash on most days (see 13.2) and pay round-trip fees to re-enter.
- (b) **Hold on skip, but enforce the cap**: keep the book unless its
  realised leverage exceeds 3× (or some fraction), then flatten. Fewer
  fees, but a stale book is a different strategy from a daily-rebalanced
  one, and "different strategy" is exactly what §2.1 forbids running
  silently.

My recommendation is (a): it is the only reading under which every held
position was decided by the strategy on the day it is held, and it makes
13.2 visible instead of hiding it. Either way the six trials above must be
re-run (6 of the remaining 14).

### 13.2 The $5 floor binds at $100 — this is the §4 number

Realised gross leverage, filled rebalances only: `lb7/skip0` min 0.21 /
p05 0.27 / median 0.46 / p95 0.90; 96% below 1.0×. `below_min_notional`
skips: 32–45% of scheduled rebalances across the grid. Sizing rule with
realised L: `C ≥ 10N/L` → at L = 0.46, **C ≥ $217**; at L = p05 0.27,
**C ≥ $370**. At $100 the book is untradeable on most days by
construction. This is not the universe filter's leverage assumption (§4 of
the amendment); changing `gross_leverage` in the filter changes *which
symbols* are admitted, not whether the vol-targeted position clears the
floor. The lever is capital (or N, or the vol target — all constraints,
not mine to move).

### 13.3 What is NOT a bug (checked before claiming it)

- **Return-matrix alignment.** 52 of 265 train symbols have internal
  missing days (a cluster 2022-02-25 → 03-01 across ~40 symbols; 26–29-day
  holes for TLM/BNX/ICP). `compute_target_weights` already drops any symbol
  whose last-61-bar `open_time`s differ from BTCUSDT's (§5), so gapped
  windows never enter betas or the covariance. Effect: those symbols leave
  candidacy for 61 days after each hole — coverage, not corruption.
- **Beta-hedge scale.** My first hypothesis for the wipeouts (a small
  β̄_short blowing up s) is refuted by the replays: s ∈ [0.90, 1.23] on the
  fastest wipeout, median 1.02 / p95 1.73 / max 4.03 on `lb7/skip0`, with
  s > 3 on 1% of rebalances. Worth watching, not the cause.
- **Prices.** Largest overnight gap in train is ×1.05; no non-positive
  prices; fills at the open are on continuous prints.

### 13.4 Funding coverage (data, Stage 1 scope)

`settlements_between` applies every funding row in the window, so symbols
Binance moved to 4-hourly funding (the 14,400,000 ms residue cluster, ~0.5M
rows) settle six times a day correctly, and +1–3 ms stamps are harmless.
"Missing" means a held symbol had a bar but no funding row: 1,480 of
181,617 train symbol-days (0.8%), but concentrated — funding data starts
**477 days after klines for ICPUSDT, 592 for TLMUSDT, 305 for BNXUSDT**.
A stale book shorting a collapsed coin like ICP for months (13.1) explains
the 7,599 count. Missing settlements are logged, never zero-filled, but the
run continues; whether those symbols should be excluded while funding is
unknown is a data-policy call (Stage 1 territory), flagged here, not acted on.

### 13.5 Ruling and fix

User ruling 2026-08-27: **flatten on skip**. Implemented in the engine (a
skip of any reason, including an unfillable decision, closes the book at
the next open with fees on the turnover; `BacktestResult.flattens`),
pinned by `test_flatten_on_skip`, §6 updated. Suite 24/24, Stage 1 13/13.

### 13.6 Grid v2 — flatten on skip (commit `fa99ffa`); 12 of 20 trials used

```
 lb skip  sharpe  ann_ret  ann_vol  max_dd   turn  fee_drag lev_med   <1.0x  bmn_skip  n_rb  active_days
  7    0   -0.11   -0.49%    3.76%   5.43%   11.3       n/a    1.21      6%     82.6%    34    44/1332
  7    2    0.50    1.82%    3.72%   3.44%   11.3    25.83%    1.19      8%     82.4%    36    46/1330
 14    0    0.72    3.22%    4.58%   4.23%    8.7    13.65%    1.17     15%     82.6%    34    43/1342
 14    2    1.01    5.40%    5.37%   4.58%   11.0    10.76%    1.05     39%     80.8%    61    70/1342
 28    0    0.96    5.58%    5.81%   7.68%    9.7     9.36%    1.05     34%     81.1%    59    72/1342
 28    2    1.65    8.36%    4.94%   1.86%    8.5     5.87%    1.05     34%     81.0%    61    72/1342
```

The artifact is gone: no bankruptcies, realised vol 3.7–5.8%, max DD ≤
7.7%, 9–13 flatten events per run costing $0.5–0.7 in fees. What is left
is the finding of 13.2 in its final form:

**At $100 / N = 10 / 20% vol target / $5 floor, the strategy is infeasible.**
81–83% of scheduled rebalances fail `below_min_notional`; 15% have no
universe (2020 warm-up); each config is invested on **43–72 of ~1,340
days**. The full-window Sharpes are statistics of a regime-selected
sliver: the book clears the floor only when k ≥ ~0.43, i.e. when the
60-day ex-ante vol of the hedged unit book is ≤ ~46% — calm regimes only.
Postmortem of `lb28/skip2` (`diagnostics.jsonl`): 61 rebalances, s ∈
[0.84, 1.28], k median 0.49 *on the days it traded* (selection-biased
upward; on all decision days the v1 replay put the unit-book vol median at
89% → k ≈ 0.23); all six worst days fall in Apr–Jun 2020; equity 100 → ~125
in those two months, then cash for most of 3.5 years. The runner's own
"> 1.5" flag fired, correctly: no counterparty can be argued from 72
days, and the deflated Sharpe (0.99) is overstated because it uses the
1,343-day window as the sample size.

Decomposition (2a §2.1, required, run on `lb28/skip2`, `diagnostics.jsonl`,
no budget): Sharpe real 1.65, demeaned 1.61, drift 0.04 (3%).
**DIAGNOSTIC ONLY — full-sample means, not runnable live.** Recorded, not
interpreted: 72 active days, and the synthetic zero-drift floor was ~18%.

What v2 does establish about the harness: the null canaries pass on the
same code, flatten-on-skip holds the cap, fee drag 6–26% of gross at taker,
funding is small (+$0.5–1.0 over four years — the v1 "+$120" was the
stale-book artifact).

### 13.7 Decision required — constraints, not mine to move (§0)

The 2a §4 question ("change the universe filter's leverage?") is moot:
`below_min_notional` skips are caused by vol-targeting, not by which
symbols the filter admits. The lever is one of:

1. **Capital.** `C ≥ 10N/L` with realised L: **$217** at L = 0.46 (median
   on decision days), **$370** at L = 0.27 (p05). ~$400 trades on most days.
2. **N.** N = 4 at $100 clears the floor at the same L (a different
   strategy: 2 long / 2 short, far less breadth — against A4's argument).
3. **Vol target.** 20% → higher raises k proportionally (a constraint).

Whichever is chosen, a grid re-run costs 6 of the remaining 8 trials, and
must be run once — the sizing arithmetic above is not a trial and needs no
further backtest to check. Nothing runs until the user chooses. Validate
and holdout untouched.


## 18. Stage 2b — corrections (Part A)

*(Renumbered from §14 → §16 → §18: later prompts reserve §14/§15 for the
invariant audit and void-trial record, and §16 for the floor withdrawal.)*

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

## 19. Stage 2b — paper-trading harness (Part B)

*(Renumbered from §15 → §17 → §19.)*

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

### 13.8 Ruling — capital $400 (user, 2026-08-27) — **WITHDRAWN, see §15.2**

> Superseded by the pre-registered 1.05x leverage floor (2c §3), which
> makes C=$100 viable by construction. `initial_capital` stays at the
> specified $100; the `--capital` flag remains but is not used for the
> grid. The reasoning below rests on leverage measured without a floor.

Constraint change chosen by the user: `initial_capital` 100 → **400** for
the re-run; N, vol target, cap and windows unchanged. Rationale: `C ≥ 10N/L`
at the p05 realised leverage (0.27) gives $370. Exposed as `--capital` on
the runner (the default stays 100; every trial row carries the full
config, so the change is visible per row). Grid v3 = trials 13–18 of 20.

## 14. Stage 2c — the invariant hole, Test 16, and the audit

**Root cause restated (2c §1):** the 3× cap was asserted on `res.rebalances`
— filled days only — and the breach happened on held days. Test 16 now
asserts `gross_notional / equity ≤ cap` on the **daily** trace
(`BacktestResult.daily_leverage`, every day, filled or not).

**Pre-fix verification (required before applying any fix):** the engine at
`7f2ea6d` (hold-on-skip) was loaded unmodified except for the additive
daily-leverage trace, and run on `breach_market()` — deterministic ranking,
then skips while the held longs fall 10%/day and the shorts rise 4%/day for
30 days. Reproducible with `tools/verify_test16_prefix.py`.

| Measured | Pre-fix peak leverage | Days over cap | Outcome | Test 16 |
|---|---|---|---|---|
| before the 2c 3 floor existed | **35.71×** | 8 | bankrupt, −$146 | fails, day 315 at 3.03× |
| under the committed config (floor 1.05×) | **156.44×** | 8 | bankrupt, −$367 | fails, day 313 at 3.18× |

Both are the same bug; the second is larger because the 1.05× floor starts
the book bigger before the crash, so the held notional it fails to shrink is
bigger too. The second row is what the committed tool reproduces today —
quoted in preference, with the first kept because it is the number the fix
was originally verified against. On the **current** engine the same fixture
peaks at 3.00× (the cap binding through the rescale path, not a breach) and
the plain factor run at 1.29×. The test reproduces the bug it guards.

**Audit of the other invariants (2c §1.2):**

| Invariant | Asserted where | Hole? | Action |
|---|---|---|---|
| 3× cap | rebalances only | **yes** (the bug) | Test 16 on the daily trace |
| `below_min_notional` | never fired | **yes** — the $50 fixture was excluded by the universe filter first | `test_below_min_notional_fires`: $6 floor, viable universe, 439 skips with that exact reason, 0 fills |
| dollar neutrality (test 7) | `raw_weights` at rebalance | no — a construction property of the ranking stage; it has no meaning on a held day | none |
| tilt identity (test 12) | final weights at rebalance | across a skip the relative weights are preserved only under a single-scalar rescale; flatten trivially, hold not at all | Test 17 asserts ratios preserved to 1e-12 through a rescale |
| beta neutrality (test 5) | realised beta over the whole run | no — held days are in the sample | Test 17 runs the skip-heavy fixture through it as well |
| vol target (test 6) | realised over the run | no; but the ex-ante target is restored on skips only by rescaling | Test 17 |
| MIN_NOTIONAL (test 8) | at fill | partial — a held position can drift under the floor | rescale's drop rule + Test 17 |

## 15. Stage 2c — void trial records, and the figures struck from the record

### 15.1 A first look at real train data happened, and produced only wipeouts

All **13** logged rows in `trials.jsonl` are now marked `"void": true` with a
reason. They stay in the file — deleting them would falsify the record — but
they do **not** consume trial budget and are excluded from the Deflated
Sharpe trial set (`runner.trial_srs_for_deflation` skips `void`).

| Commit | Runs | Why void |
|---|---|---|
| `36f191d` | 6 | Pre-fix harness held the stale book through the pre-registered 3× cap (35.71× reproduced on the fixture). Measured the bug, not the strategy. |
| `fa99ffa` | 6 | Flatten-on-skip, superseded by the pre-registered rescale-on-skip (2c §2). Not comparable to a rescale run. |
| `e4c69ca` | 1 | Aborted mid-grid; capital $400 is superseded by the 1.05× floor at C=$100, and the harness lacked rescale, floor and slippage. |

**Budget: 0 of 20 consumed.** Re-running the six configs post-fix is *the same
six trials*, not six more (2c §5.4). The bug fix is not a trial: restoring
conformance to a pre-registered constraint is not a new choice.

Information content of that first look: near zero, but not literally zero,
and it is on the record. What it established was about the *harness* — the
invariant hole of §14 — not about edge.

### 15.2 STRICKEN — do not carry these forward as priors

Every number below came from a void harness. They are struck explicitly so
they cannot drift into memory as a prior:

- ~~Sharpe 1.12 (lb7/skip0), 0.83 (lb28/skip0)~~ — stale-book artifact
- ~~four bankruptcies, "vol 216–819%"~~ — the same artifact, inverted
- ~~Sharpe 1.65 (lb28/skip2) at $100 with flatten-on-skip~~, and its
  ~~drift decomposition 1.65 / 1.61 / 3%~~ — a flatten harness, 72 active
  days; the decomposition must be re-run against the rescale harness on the
  config the *new* grid selects
- ~~"C ≥ $217–370, capital is the lever"~~ — superseded: the pre-registered
  1.05× floor (2c §3) makes C=$100 viable by construction, and the earlier
  arithmetic used leverage from a harness that did not enforce a floor
- ~~median realised leverage 0.46, 81% below-floor skips~~ — measured
  without the floor

The **$400 capital ruling of §13.8 is withdrawn**: it was a response to the
floor problem that 2c §3 solves at the pre-registered level, and it was not
pre-registered. `Config.initial_capital` stays at its specified **$100**;
`--capital` remains available but is not used for the grid.

### 15.3 The 2c rulings as implemented

| Ruling | Where | Test |
|---|---|---|
| Rescale on skip, not flatten (§2) | `weights.plan_rescale`, engine step 4 `elif pending_rescale` | 17 |
| Deadband 0.10, not tunable (§2.2) | `weights.RESCALE_DEADBAND` | 17 (in-band → no trade) |
| No re-ranking on a skip (§2.1) | one scalar `alpha`; ratios preserved to 1e-12 | 17 |
| Drop a position under MIN_NOTIONAL, rescale the rest (§2.1.5) | `plan_rescale` loop; `rescale_drops` | 17 |
| Gross leverage floor 1.05× (§3) | `Config.min_gross_leverage`, `vol_target_scale(min_gross=)` | 18 |
| Slippage per side, 0 and 5 bps as a pair (§4) | `Config.slippage_bps_per_side`, `costs.slip_price` | `slippage_is_adverse_and_priced` |
| Per-symbol slippage hook, flat for now (§4.1) | `costs.slippage_bps(symbol, view, cfg)` | same |
| Void runs excluded from DSR (§5) | `runner.trial_srs_for_deflation`, `strategy_key` | — |

**Trial accounting for the slippage pair (§4):** the DSR trial set keys on
the config *minus* `slippage_bps_per_side`, so the 6-point grid at two
slippage settings counts as **6 trials**. `runner.n_trials_conservative()`
reports the stricter count (12 distinct hashes) alongside it, since 2c §4
says over-counting is the safe direction.

**Caveat carried:** 5 bps came from **n = 1 synthetic testnet fill**. It is a
plausible magnitude, not a measurement, and the report prints that wherever
the number appears.

### 15.4 Two things I flag rather than act on (§0)

1. **The floor and the cap can now both bind on the same day.** With
   `min_gross_leverage = 1.05`, a book whose ex-ante vol implies less than
   1.05× is levered *up* to the floor — so realised vol exceeds 20% by
   construction on those days, which is the intended trade (2c §3 accepts
   ~21%). But on the skip-heavy fixture the rescale path now pins leverage at
   exactly 3.00× on the worst days (Test 16 peak), i.e. the cap binds while
   the crash runs. That is the cap doing its job, and it is also the reason
   the fixture no longer bankrupts. Recording it so the 3.00× in the test
   output is not later mistaken for a breach.
2. **`missing_fill_bar` no longer forces the book flat.** Under 2c §2 the
   book is held and step 7 decides again at that close (rescaling if it has
   drifted). This follows from "rescale, not flatten", but it is a change in
   behaviour for that specific skip reason relative to Stage 2b, so it is
   written down rather than left implicit.

## 16. Stage 2d — the floor withdrawn, capital restored (2026-08-27)

### 16.1 The 1.05x floor is withdrawn, and why the record matters

`STAGE2C_PREGRID.md` §3 pre-registered `min_gross_leverage = 1.05`. Stage 2d
§1 withdraws it as a **specification error**. The config field is kept at
`0.0` rather than deleted, so the withdrawal is visible in every logged trial
row instead of vanishing from the record.

The floor was calibrated from the synthetic fixture's unit-book volatility
(~21%). The real figure is ~4x higher:

| | unit-book vol | gross | floor multiplier | realised vol | expected maxDD |
|---|---|---|---|---|---|
| Synthetic fixture | 21% | 1.90 | 1.00x | 20.0% | 14.3% |
| **Real, median** | **89%** | **0.45** | **2.34x** | **46.7%** | **33.4%** |
| Real, p95 | 164% | 0.24 | 4.30x | 86.1% | 61.5% |

At the real regime the floor binds on every rebalance and pushes the book to
~47% realised vol against a 20% target, with expected max drawdown past the
pre-registered 30% kill switch. It does not solve `MIN_NOTIONAL`; it breaks
the risk budget instead. Crypto momentum's tail variance is undefined under
power-law tests, so vol management improves Sharpe without bounding the tail
— 47% vol with an unbounded tail is not a position this project can take.

Consistent with the Stage 2c evidence: the pre-fix peak leverage went
**35.71x → 156.44x** once the floor existed, because a floor starts the book
larger, so every un-shrunk notional is larger. The floor amplifies every
exposure failure by ~4.4x.

**Test 20 pins this** (`test_leverage_floor_fails_at_realistic_vol`) on a
fixture calibrated to the real regime (unit-book vol **0.91**):

| Setting | realised vol | gross median | max DD |
|---|---|---|---|
| floor 0.0 (withdrawn) | **0.200** | 0.42 | 24.8% |
| floor 1.05 (the withdrawn value) | **0.495** | 1.05 (pinned) | **50.8%** |

The withdrawal is now a reproducible failing configuration, not an argument
in a markdown file.

### 16.2 Calibration honesty (Stage 2d §2.1)

Stated plainly, because it is the kind of thing that is easy to lose:

- The floor was withdrawn on **volatility-calibration grounds, before any
  grid run under the current harness**. No performance number influenced it.
- The calibration input — unit-book vol ~89% median, ~164% p95 — came from
  **void-run replay data** (`NOTES` §13.1, the v1 postmortem). Those runs are
  void for *performance* purposes and every Sharpe from them is struck in
  §15.2. Their *volatility* measurements are what informed this decision.
- That is a risk calibration, not a performance selection. But real data was
  used, and the use is visible here either way rather than buried.

### 16.3 `initial_capital` restored to 400

The floor and the capital ruling are **opposite** solutions, not
interchangeable:

- **Floor**: raise exposure until positions clear $5 → sacrifices the vol target.
- **Capital**: lower the leverage needed until real exposure clears $5 → preserves it.

Only the second keeps the pre-registered 20% target intact. `C >= 10N/L` at
N=10 → **$400 needs L >= 0.25**, against a real distribution of min 0.21 /
p05 0.27 / median 0.46 — clearing above the 5th percentile. Residual
`below_min_notional` skips of ~1-5% are expected and acceptable, and they now
rescale rather than flatten.

My §13.8 procedural objection (withdrawing $400 because it was not in a
document, while the floor was) was right from where I sat but wrong in
substance: §0 places constraints with the user, and capital is an **input
constraint the user set**, not a strategy parameter being tuned. §13.8's
withdrawal is itself now withdrawn; `Config.initial_capital = 400.0`, and the
runner's `--capital` default follows the dataclass field so the two cannot
drift apart.

### 16.4 Funding-start exclusion (Stage 2d §5) — implemented, measured

A symbol is no longer a candidate until its funding history has begun.
Before this, symbols were tradeable for a year or more with funding silently
absent: logged as missing, never zero-filled, but the position still ran
cost-free on that leg — understating costs on exactly the long-tail names
momentum favours.

Implementation: one condition in the §5 candidate filter, checking a
**trailing 3-day window** (`FUNDING_PRESENCE_WINDOW_MS`) rather than "any
funding ever". That is cheap (no full-history scan per symbol per rebalance)
and strictly *stricter*: it also excludes a symbol during a mid-history
funding gap, which has the identical defect. Data policy, not strategy; costs
no trial. The skip detail now reports how many symbols were dropped for this
reason.

**Measured over the train window: 1,477 of 181,617 symbol-days (0.81%),
across 7 symbols.**

| Symbol | days removed |
|---|---|
| TLMUSDT | 593 |
| ICPUSDT | 478 |
| BNXUSDT | 301 |
| BTCSTUSDT | 65 |
| LENDUSDT | 29 |
| QTUMUSDT | 10 |
| USDCUSDT | 1 |

The top three match `NOTES` §13.4's figures (+592, +477, +305 days) exactly,
which is the confirmation that the filter targets what it was meant to.

### 16.5 The general lesson (Stage 2d §3, added to the record)

**A fixture used to justify a risk parameter must match the volatility regime
that parameter will meet.** The 1.05x floor passed a non-vacuous test (Test
18 bound on 499 rebalances) and was still wrong, because the fixture's
unit-book vol was 4x below reality and the floor's failure mode simply cannot
appear there. Non-vacuous is not the same as representative.

Applied going forward: `realistic_vol_market()` exists in the test suite for
this purpose, and Test 20 asserts the fixture's own unit-book vol is in
[0.75, 1.05] before asserting anything about the floor — so if someone
changes the fixture, the test says the regime no longer matches rather than
silently proving nothing.

## 17. Stage 2e — pre-grid fixes from external review (2026-08-27)

The grid moved again. The 2d grid was **stopped after 7 of 12 runs** when
this review landed; those rows are marked void (§15 accounting, reason
recorded in `trials.jsonl`) and **their Sharpes were deliberately not read**.
Budget back to **0 of 20**. Spending 6 trials on a harness about to change,
then 6 more after fixing it, is twelve trials for one answer.

### 17.1 Feasibility is now checked AFTER hedging (2e §1)

The universe filter estimated the smallest position as
`MIN_WEIGHT_FRACTION · L · C / N`, then `beta_hedge` scaled the whole short
leg by `s` — so every short shrank when `s < 1` and the filter had validated
weights that no longer existed. Since skips cluster in high-vol regimes,
this biased *which days the strategy traded*, the same defect that made grid
v2's headline meaningless.

Now: the check runs on post-hedge, post-vol-target weights; an infeasible
position is **dropped**, the remainder renormalised and re-hedged, up to
`MAX_FEASIBILITY_PASSES = 3`; below `MIN_LEG_NAMES = 3` on either leg the
rebalance skips as `below_min_notional_post_hedge`.

**No substitution**, per §1's explicit rejection: substituting the
next-ranked candidate would select on position size, which correlates with
volatility and liquidity, and that tilt could never be separated from the
momentum signal afterwards. Test 21 asserts the final book is always a
subset of the originally ranked names.

The universe filter no longer passes `max_gross_leverage`. It passes the
**last realised gross** — known at the decision close, the same class of
path dependence as `capital=equity`. A two-pass "measure then re-filter"
scheme was rejected: re-ranking on a feasibility-filtered pool *is*
substitution. At C=$400 the old call admitted anything under
`0.5 × 3.0 × 400/10 = $60`, i.e. every symbol, while the book actually runs
near 0.45x where the threshold is $9.

Demonstrated failing pre-fix: same fixture, the old path skipped the whole
rebalance (`below_min_notional`, ALT13 at $7.12 < $8.00).

### 17.2 Fills at the 00:01 open (2e §2)

Filling at 00:00:00 is operationally impossible, and against a momentum
signal the delay is **one-signed** — not covered by symmetric slippage.

`tools/backfill_minutes.py` ingests the first five 1-minute bars of each UTC
day. **No change to `pitdata/` was needed**: the `klines` table already
carries an `interval` column, so the minute bars live under `interval='1m'`
and inherit exactly the `close_time <= as_of` gating that
`test_lookahead.py` already verifies. A 00:01 bar has `close_time`
00:01:59.999 and cannot be seen by the decision at the previous close —
Test 22 asserts this directly.

`execution_delay_minutes = 1` is pre-registered; `0` stays runnable and
reproduces the old convention exactly, and the two are never selected
between on results. A missing 00:01 falls **forward** to 00:02/00:03; if
none exists the rebalance skips. It never falls back to 00:00.

**A vacuity trap this exposed, worth recording.** Switching the default to
minute fills made every synthetic fixture unfillable — and the suite went on
passing: 439 rebalance records with **zero fills and zero fees**, because the
assertions iterated over fills that no longer existed. This is the Stage 2c
§1 lesson again (an invariant asserted at the wrong moment). Two fixes: a
missing execution bar now aborts the rebalance loudly as `missing_fill_bar`
rather than quietly not trading, and every fixture builder writes its minute
bars. Tests now assert `total_fees > 0` before drawing conclusions.

### 17.3 Delisting is not a data gap (2e §3)

Cross-sectional momentum shorts collapsing coins, and collapsing coins
delist — so merging the two events landed squarely on the leg the profits
should come from. Now separate:

- **Delisting** (metadata timestamp passed): settled at the exchange
  settlement price; without one, at last mark, flagged
  `delist_settlement_estimated`. A delisted symbol is never tradeable again.
- **Data gap**: the position is *held* and marked at its last close (no PnL,
  no fee, no funding on those days). Only past `max_data_gap_days = 3` is it
  force-settled as `data_gap_exits`.

Counted and reported separately, never merged. Test 23 puts both in one
fixture and asserts different paths, reasons and PnL (−50.74 vs −23.08).

**Honest limitation**: the dataset has no historical listing/delisting
metadata — `symbol_filters` holds a single snapshot dated 2026-08-26, and
using a future-dated status inside a backtest would be lookahead. The
delisting path is therefore driven by an explicit `delistings` argument, and
on the real store it is empty: every disappearance is handled by the
point-in-time-safe data-gap rule. That is the conservative direction and it
is reported as such rather than papered over.

### 17.4 Taker-only for research (2e §4)

`assert_reportable_fee_mode` raises on maker mode unless the purpose starts
with `exploratory-nonreportable`. The backtester treats a maker fee as a
guaranteed fill; the live harness correctly knows a post-only order may not
fill at all, and maker fills are not a random subset — you fill when the
market comes to you, which for a momentum entry means filling on the entries
about to work least well. That adverse selection has no representation here.

### 17.5 Beta shrinkage (2e §5)

`s > 3` on 1% of rebalances was estimation noise executed as a hedge
instruction. Now each 60-day OLS beta carries its standard error;
`beta_shrunk = w·β̂ + (1−w)·1.0` with `w = 1/(1 + (SE/β̂)²)`, and a leg whose
weighted beta SE exceeds its estimate skips as `unhedgeable_beta`. Beta SE
median/p95 and median shrink are logged per run.

Effect (Test 24): a clean beta 1.499 → 1.499; a noisy one −0.449 → 0.850;
and on the fixture that previously produced **s = 17.7**, shrinkage gives
**s = 1.20**. A risk control, not an alpha choice — costs no trial, but it
changes results, so it is pre-registered here.

### 17.6 Funding cadence (2e §6)

The missing-settlement checker assumed a fixed 8h schedule, so 4-hourly
symbols reported spurious gaps. **The dataset has no
`funding_interval_hours` column** (the parser only names it in a comment),
so the cadence is *inferred* from each symbol's own settlement timestamps up
to `as_of` — point-in-time safe, and more robust than a current-snapshot
field, since it reflects the cadence actually in force in the window being
measured. Diagnostic only; no PnL effect.

### 17.7 Liquidation stress and bootstrap CIs (2e §7, §9)

The full intraday liquidation model was rejected as specified; the cheap
version is in. Every day records the equity implied by marking each position
at the adverse side of its own bar (low for a long, high for a short) — the
worst path the daily data can support. Reported as min implied equity, with
a flag below 25% of starting capital.

Stationary-bootstrap (Politis–Romano) 90% CIs now accompany every Sharpe.
Resampling an existing result is not a new backtest and costs no trial;
crypto returns are neither Gaussian nor IID, so the parametric SE
understates uncertainty.

### 17.8 §8 deferred table — nothing added

No new idea was promoted to blocking during this round. The deferred list
stands as written (rank buffer, L1 turnover penalty, residual momentum,
EWMA/shrinkage covariance, full mark-price funding, walk-forward). Two
things that came up and were deliberately **not** acted on:

| Came up | Why not now |
|---|---|
| The `s`-shrinkage formula sends a genuinely zero-beta asset to 1.0 | It is the specified formula and the conservative direction (assume market exposure when the estimate says nothing). Changing it is an alpha-side judgement, not a fix |
| Minute bars exist for five minutes a day, so a smarter execution model (VWAP over 00:01–00:04) is now cheap | A strategy change. Belongs in §8, after a baseline exists |

### 17.9 One test had to be rewritten, not just re-run

`test_below_min_notional_fires` (the Stage 2c 1.3 fixture) failed after 2e
1 and the failure was correct. Its premise -- "the filter assumes 3x, so
hopeless symbols reach the sizing check" -- is precisely what 2e 1 removed.
With the filter using the gross actually in use, that fixture's symbols are
now rejected up front (493 `universe_too_small`) instead of being admitted
and caught later, so the old assertion was asserting obsolete behaviour.

Rewritten to assert what still matters and is still true: the post-hedge
floor path is reachable (it fires), the pre-filter rejects at the realised
gross rather than at 3x, and -- the invariant that actually protects the
results -- every executed position clears its floor (36 of 36 at $6).

### 17.10 Minute ingest complete, and what the data says

`tools/backfill_minutes.py`: **20,550/20,550 symbol-months, 3,057,900 minute
bars, 0 failures, 34.4 min**, minutes 0-4 only, 2020-01-01 -> 2026-07-31,
831 symbols (GRVTUSDT has daily bars but no 1m dumps; a 2024+ listing,
irrelevant to train).

Verified before any grid run:

- **Execution-bar coverage on train: 181,352 / 181,617 daily bars (99.85%)
  have a 00:01 bar.** The 265 that do not also lack a 00:02, so the
  fall-forward cannot rescue them and those symbol-days will skip. 0.15% is
  immaterial and is reported rather than assumed away.
- **PIT gating holds on real data**: at the close of 2023-06-14 the newest
  visible minute bar is 2023-06-14 00:04, and the 2023-06-15 00:01 bar is
  invisible. Same `close_time <= as_of` gate as every other bar.

**The size of the thing 2e 2 was worried about.** Unconditional 00:00 ->
00:01 open move across the train window, 181,605 symbol-days:

```
mean +2.73 bps | median +0.00 | mean |move| 19.12 bps | sd 39.03
p05 -37.62     | p95 +45.85
```

Mean absolute move is **3.8x the entire 5 bps slippage assumption**. That
is the unconditional distribution -- it does not say what the *momentum
book* pays, because the relevant quantity is the move conditional on being
long the winners and short the losers, which is one-signed if the move
continues. That number falls out of the grid itself, from the
`execution_delay_minutes` 0-vs-1 pair, and is deliberately not estimated
here: pre-empting it with a hand calculation would be exactly the kind of
result-shaped guess 0 warns about. What the distribution does establish is
that the delay is not second-order next to slippage.

### 17.11 Status

Stage 2e §1–§7 and §9 implemented; §10 (live fixes) is explicitly
*before Phase 2, not before the grid* and is not done. Minute ingest of
20,550 symbol-months runs at ~7/s. **The grid has not been run.** Budget 0
of 20. Validate and holdout untouched.

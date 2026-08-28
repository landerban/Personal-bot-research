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


## 20. Stage 2b — corrections (Part A)

*(Renumbered §14 → §16 → §18 → §20 as later prompts reserved those numbers:
§14/§15 the invariant audit and void trials, §16 the floor withdrawal,
§17 the 2e fixes, §18 the grid, §19 Stage 3.)*

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

## 21. Stage 2b — paper-trading harness (Part B)

*(Renumbered §15 → §17 → §19 → §21.)*

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

## 18. THE FIRST VALID TRAIN GRID (2026-08-27, commit `c46c295`)

Pre-flight 12/12. 832 symbols, 612,421 daily and 3,057,900 minute bars,
2020-01-01 -> 2026-07-31. Capital $400, leverage floor withdrawn, fills at
the +1min open, taker, N=10, 20% vol target. **12 runs = 6 trials**
(slippage is a cost assumption reported as a pair, never selected between;
the conservative count is 12 and is available from
`runner.n_trials_conservative`). **Budget now 6 of 20.**

### 18.1 Results

**The 5bps column is the headline. The 0bps column is a sensitivity bound
and is not a headline anywhere** (Stage 3 3).

```
                 slippage 5.0 bps  (BASELINE)      slippage 0.0 bps (bound)
 lb skip  sharpe  ann_ret  maxDD  feedrag | sharpe  ann_ret  maxDD  feedrag
  7    0    0.60   12.85%  36.6%   75.4%  |   0.96   23.99%  35.3%   41.5%
  7    2    0.12    0.28%  48.0%  288.4%  |   0.48    8.11%  39.6%   84.6%
 14    0    0.81   18.35%  27.7%   48.3%  |   0.91   20.92%  25.0%   41.1%   <- FROZEN
 14    2    0.17    1.33%  46.4%  423.8%  |   0.42    6.77%  38.6%   99.5%
 28    0    0.72   14.15%  35.9%   61.3%  |   0.97   20.57%  30.4%   37.2%
 28    2    0.61   10.67%  36.3%   70.7%  |   0.90   17.08%  31.5%   40.4%
```

**Active days 1380-1381 of 1381 in every config.** That is the line that
makes these numbers mean anything at all, and the difference from the void
grid v2, whose headline came from 72 active days of 1,342. The $400 capital
did what it was supposed to: `below_min_notional` skips are **0.00%** of
scheduled rebalances (the post-hedge check fires on 1.1-2.4%, and total
skips are ~20%, almost all `universe_too_small` during the 2020 warm-up).

Realised gross leverage median 0.44, p05 0.21, 96-97% below 1.0x --
confirming the Stage 2d ruling: at this leverage a $100 book could not have
cleared the floor, and raising leverage to fix that would have broken the
vol target (Test 20).

Other per-config figures: realised vol 19.7-25.6% against the 20% target;
beta SE median 0.24-0.26 with median shrink 0.011-0.013; rescale-on-skip
20-38 events costing $0.30-0.56 in fees; 0 delistings (no metadata -- see
17.3), 2-4 data-gap forced exits; intraday H/L stress bottoms at 76-92% of
starting capital, so no config trips the 25% flag; deflated Sharpe
0.26-0.79 over 6 distinct configs.

### 18.2 WHO IS PAYING -- and the answer changes what this strategy is

No config reaches Sharpe 1.5, so 6.3's bug-gate does not fire. But the
question is worth answering anyway, and the answer is uncomfortable.

**Funding is 49-100% of net PnL in every single config**:

```
 lb skip  bps   gross     fees   funding      net   funding as % of net
  7   0     0  +435.16   180.62  +247.36  +501.90         49%
 14   0     0  +318.15   130.83  +233.52  +420.84         55%
 28   0     0  +283.17   105.33  +233.99  +411.83         57%
 14   0     5  +266.67   128.69  +218.68  +356.66         61%
 28   0     5  +155.95    95.55  +199.63  +260.03         77%
  7   2     5   +44.74   129.04   +88.49    +4.20       2108%
```

For the `skip=2` configs at 5bps, price PnL after fees is *negative* and
funding alone keeps them above water. Even for the best config, 61% of net
profit at the realistic slippage setting is funding, not price movement.

The mechanism: **CORRECTED IN 22.3 -- read that instead.** This paragraph
originally attributed the carry to being short crowded-long alts, with
leveraged retail longs as the payer. Stage 3a measured it directly: 81% of
funding income accrues on the **LONG** leg (long coins with negative
funding, i.e. coins whose *shorts* are paying), and it is a tail rather
than a yield -- the long leg's median rate is positive, so on a typical day
it pays. The identifiable-payer conclusion survives; the direction did not.

So: **a substantial majority of this strategy's profit is carry, not
cross-sectional momentum.** That has direct consequences:

- The decay profile is carry's, not momentum's. The reference says carry
  went negative in 2025 -- inside the holdout window.
- The long leg makes the price PnL (+412 to +840) while the short leg loses
  it (-208 to -579). The shorts pay for themselves through funding receipts,
  not through price decline. A momentum story would have the shorts
  contributing on price.
- Comparing this against a pure carry benchmark is now the obvious question,
  and it is a NEW strategy, so it costs trials. Not done, not decided.

### 18.3 Drift decomposition (6.2)

Run on `lb14/skip0` at 5bps -- chosen as the best *worst-case* across the
pre-registered slippage pair (0.81 at 5bps is the highest any config
reaches there, 0.91 at 0bps is within 0.06 of the maximum) and the only
config whose max drawdown stays under the 30% kill threshold at both
settings. That choice is a selection made on train and is recorded as such.

```
Sharpe (real)      : 0.81
Sharpe (demeaned)  : 0.46
Drift component    : 0.36  (44% of total)
NOTE: DIAGNOSTIC ONLY -- full-sample means, not runnable live.
```

Against the ~18% synthetic zero-drift floor (`TEST_NOTES` obs. 2), 44% is
well clear of noise. It does not cross 12's majority rule, but combined with
18.2 the picture is that trend-continuation is the *smallest* of the three
contributions: carry > drift-harvesting > trend.

**A trap caught before it produced a wrong number.** `xsmom_demeaned.db` had
been built before the minute bars existed, so it had zero 1m rows. Running
the decomposition against it would have aborted every fill and reported the
demeaned Sharpe as ~0, inflating the "drift" component to ~100%. The tool
now demeans the 1m execution bars with the same per-day factor as the daily
bars -- scaling the daily series alone would leave fills on an undemeaned
price series and put the drift back through every fill while the daily data
looked correct. Same class of failure as 17.2; found by checking the input
rather than the output.

### 18.4 Two honest limitations in these numbers

1. **31 traded symbols have no `symbol_filters` row**, so `min_notional` is
   None and they are exempt from the floor check. Measured impact: **3 of
   12,781 executed positions (0.02%)** fell under $5, all in delisted names
   (HNT, MATIC, SRM). Immaterial here, but it is a real hole and it would
   not be immaterial at smaller capital.
2. **~7,300 missing funding settlements per run.** With funding now known to
   drive most of the PnL, this is no longer a minor diagnostic. They are
   counted, never zero-filled, and the funding-start filter (17.6, 0.81% of
   symbol-days) removes the worst offenders -- but the residual is
   concentrated in exactly the long-tail names the strategy trades.

### 18.5 Status

Grid: **run once, on train, logged, 6 of 20 trials used.** Not re-run.
Validate and holdout untouched -- per 2d 7 the walk-forward-vs-single-validate
decision needs this output on the table first, and that decision is the
user's. The two remaining scarce trials are unspent.

## 19. Stage 3 — post-grid analysis (2026-08-27, zero trials)

Every item below is a data-correctness fix or attribution of a backtest that
had already run. **No configuration was selected and no trial was spent.**
Budget unchanged at **6 of 20**.

### 19.1 The "18% hole in the largest input" was my bug, not missing data

Stage 3 1 opened on the premise that 17.6% of funding settlements on held
positions were missing while funding generated 61% of net PnL. The
exposure-weighted audit (1.1, `tools/funding_audit.py`) put the ratio at
**17.34%** -- above the 10% band, i.e. "the grid result is provisional until
fixed". It was also suspiciously diffuse: 49.7/50.3 across legs, present in
all four years, top symbol only 3% of the total.

Diffuse is the signature of a systematic error, not of absent data. It was.
**Binance stamps a settlement a few milliseconds PAST its boundary** --
45.7% of all funding rows are off-boundary by 1-6ms, and BTCUSDT's 00:00
settlement on 2020-03-26 is at `00:00:00.006`. `apply_funding` bucketed rows
by comparing the raw stamp to the boundary, so every such settlement:

1. failed the `== day_open_time` test and was **counted as missing**, and
2. passed the `> day_open_time` test, so it was applied to the **post-fill**
   book instead of the book held across midnight -- violating the convention
   in section 4 of these notes.

Fix: snap each settlement to the boundary it belongs to (`(ts // iv) * iv`)
before bucketing. Result:

| | before | after |
|---|---|---|
| missing settlements | 7,352 | **11** |
| missing notional-days | $204,812 | **$209** |
| **exposure-weighted ratio** | **17.34%** | **0.02%** |

The 11 survivors are LENDUSDT (9) and ANCUSDT (2), both genuinely delisted.
That is the "under 2% -> immaterial" band, so **1.2 recovery is optional and
1.3 mark-price work is not required**. Nothing was interpolated or
zero-filled at any point. Pinned by
`test_funding_settlements_tolerate_millisecond_offsets`.

### 19.2 (1.4) Frozen config re-run on corrected data

`lb14/skip0` at 5bps and +1min, logged as a **re-run of an existing trial,
not a new one**; the prior row now carries `superseded_by` with a pointer.
Distinct-config count is unchanged, so the Deflated Sharpe trial count stays
at 6.

| | before | after |
|---|---|---|
| Sharpe | 0.815 | **0.796** |
| funding PnL | +218.68 | +205.30 |
| gross PnL | +266.67 | +266.70 |

The correction moved settlements between books rather than adding or
removing them, so the headline barely moves. Worth stating plainly: **the
bug inflated a diagnostic, not the PnL.** The 17.34% figure would have sent
us re-fetching data that was never absent.

### 19.3 (2) Per-year attribution -- the decisive table

Frozen config, 5bps. Diagnosis only; nothing here selects anything.

```
 year  days  sharpe      net$    price$  funding$    fees$     long$    short$    vol  maxDD   lev
 2020   366    1.83   +164.16   +163.12    +24.28    23.23   +360.84   -191.21  19.8%  12.1%  0.62
 2021   365    0.91   +104.54   +110.46    +17.29    23.21   +359.20   -240.65  21.1%  13.7%  0.28
 2022   365    0.05    -14.69    +30.17     -6.03    38.84   -173.38   +185.80  28.1%  18.9%  0.48
 2023   365    0.65    +90.12    -37.05   +169.73    42.56   +162.17   -184.58  24.4%  14.2%  0.47
```

**The two components move in opposite directions, and the aggregate hides it:**

```
price   PnL:  +163  ->  +110  ->   +30  ->   -37      monotonically decaying to negative
funding PnL:   +24  ->   +17  ->    -6  ->  +170      concentrated in the LAST year
```

Funding is **20% / 80%** between 2020-21 and 2022-23 -- the opposite of the
decay pattern 2.1 was watching for. Under 2.1's pre-registered reading,
funding is not "concentrated in 2020-21 and decaying", so the aggregate is
not a 2020 artifact on that axis.

But the honest reading is more uncomfortable than either branch 2.1
anticipated:

- **The momentum component is dead by 2023.** Price PnL decays +163 → +110
  → +30 → **-37**. Whatever cross-sectional trend-continuation existed in
  2020-21 is gone by the end of train.
- **What replaced it is carry**, and 2023's entire +90 net is +170 funding
  against -37 price.
- 2022 is the tell: Sharpe 0.05, and the legs invert (long -173, short
  +186) -- in the bear market the shorts finally earn on price, and the
  strategy still makes nothing.
- So the strategy is not "momentum with a carry tailwind". It is a strategy
  whose momentum engine faded across train and whose remaining return is
  carry, measured in the years immediately before the period where
  documented carry went negative (2024, then 2025 -- the holdout window).

This does not decide the validate question; it reframes it. Section 5's
Option A (pure carry benchmark) is now clearly the highest-information use
of a trial, because the per-year table implies the momentum signal may be
contributing nothing by the end of train.

### 19.4 (2.2) Drift decomposition by year

```
 year  SR real  SR demean   drift  % of total   vs ~18% synthetic floor
 2020     1.83       1.37   +0.46        25%   above
 2021     0.91       0.65   +0.26        28%   above
 2022     0.05      -0.63   +0.69      1263%   above (ratio meaningless at SR~0)
 2023     0.65       0.69   -0.04        -6%   below
```

Drift-harvesting is above the synthetic floor in 2020-2022 and **absent in
2023** (slightly negative). It does not decay smoothly with sample length,
which is what finite-sample noise would do -- it tracks the price-momentum
component, which is consistent with drift and trend being the same fading
thing rather than two independent effects. The 2022 percentage is an
artifact of dividing by a near-zero Sharpe and should not be quoted as
"1263% drift"; the level (+0.69) is the meaningful figure.

### 19.5 (3) Cost curve -- c* = 16.4 bps/side

Frozen config, train, same strategy at seven execution-cost assumptions.
Cost sensitivity on an unchanged configuration: no trial consumed.

```
 bps/side  sharpe   ann_ret      net$    fees$    slip$   feedrag   maxDD
      0.0    0.89    20.41%   +407.69   130.03     0.00    40.95%   25.1%   (bound, not a headline)
      2.5    0.81    18.05%   +349.35   124.59    62.28    46.80%   26.1%
      5.0    0.80    17.83%   +344.13   127.87   127.84    47.95%   27.9%   <- BASELINE
      7.5    0.71    15.42%   +288.17   124.13   186.14    57.29%   28.7%
     10.0    0.52    10.13%   +176.22   113.83   227.60    94.20%   31.3%
     15.0    0.21     2.28%    +35.69    99.54   298.54       n/a   40.1%
     20.0   -0.16    -5.74%    -80.15    81.05   324.09       n/a   48.2%
```

- **c* = 16.4 bps/side** (annualised net return crosses zero)
- **Sharpe falls below the 0.3 stop threshold beyond 13.6 bps/side**

Against the interpretation fixed in advance (3): `c* > 15 bps` →
**robust to execution quality; live cost uncertainty is not a threat.** The
5bps assumption -- which rests on a single synthetic testnet fill -- has
roughly 3x of headroom before the strategy stops making money, so the
weakness of that assumption is not what decides this project.

Two caveats worth keeping attached to that verdict:

1. The measured 00:00→00:01 move (17.10) has mean |move| 19.12 bps, which
   is *above* c*. That is the unconditional distribution, not the cost the
   book actually pays, and the +1min fill is already in every number above
   -- but it means real execution cost is not obviously small relative to
   c*, and the paper harness's cost data is still the thing that settles it.
2. Drawdown degrades faster than return: 25% → 48% across the curve, so the
   30% kill threshold is breached beyond ~10bps even while net return is
   still positive. **The binding constraint at high cost is drawdown, not
   c*.**

### 19.6 (4) CONFIGURATION FROZEN, 2026-08-27

**`lookback=14, skip=0`, capital $400, N=10, 20% vol target, 3x cap, taker,
fills at the +1min open, 5bps baseline slippage.**

`skip=2` is retired: dominated at every lookback at 5bps (0.60/0.12,
0.81/0.17, 0.72/0.61), with larger drawdowns and fee drag up to 424%.

No exploration of `skip=1/3/4` or lookbacks between grid points. That is how
a six-trial project becomes a parameter mine.

**Stated explicitly: choosing `lb14/skip0` out of six configs is a selection
made on train.** It is already reflected in the Deflated Sharpe trial count
of 6 and must stay reflected there.

## 22. Stage 3a — regime diagnostics: PRE-REGISTERED READING

*(`STAGE3A_REGIME.md` says "write §19 in NOTES"; §19 is already Stage 3, so
this lands at §22. Same content, different number.)*

**Recorded 2026-08-27 BEFORE any dispersion number was computed, per
STAGE3A 2.2 and 9 ("do not adjust the reading after seeing the numbers").**

The question: per-year price PnL fell +163 → +110 → +30 → −37. Is that
**regime** (momentum needs cross-sectional dispersion; correlated markets
have nothing to rank) or **decay** (crowding/competition, or it was always
weak)? Regime implies momentum returns when dispersion does; decay implies
it does not come back.

The reading, fixed in advance:

| If dispersion... | Then |
|---|---|
| collapses in 2022 **and stays low in 2023** | regime explains both years; momentum is dormant, not dead |
| collapses in 2022 **but recovers in 2023** | regime explains 2022 only; 2023's negative price PnL needs another explanation and **decay becomes the leading candidate** |
| is **roughly flat across all four years** | regime explains nothing; **decay** is the explanation for the whole decline |

Measurement, also fixed in advance: per rebalance date, over the
point-in-time universe, each symbol's 14-day trailing return (the frozen
lookback); cross-sectional standard deviation; and top-decile minus
bottom-decile spread (closer to what the strategy harvests, since it trades
the extremes). Aggregated to annual mean and median, reported alongside
universe size per year because dispersion is not comparable across a
universe that grew. Computed through `PITView`, not raw SQL — it is a
diagnostic, but ungated data would use future information and the habit
matters more than this one number.

No threshold will be chosen from these results (STAGE3A 9). Nothing in
STAGE3A 6 will be implemented. The config stays frozen; budget stays 6 of 20.

### 22.1 (2) Dispersion — the pre-registered branch fires "regime", with a caveat that matters

Computed through `PITView`, 14d trailing returns over the point-in-time
universe, every rebalance date in train.

```
 year  days  universe |  sd mean   sd med |  decile mean  decile med | corr to BTC
 2020   288        29 |   15.17%   13.20% |       52.87%      46.98% |       0.639
 2021   365       104 |   30.29%   27.78% |       96.12%      88.42% |       0.555
 2022   365       129 |   13.03%   11.43% |       44.22%      40.73% |       0.711
 2023   365       166 |   13.13%   12.20% |       44.13%      41.05% |       0.605
```

Dispersion collapses in 2022 (30.29% → 13.03%) **and stays low in 2023**
(13.13%). Under the reading fixed in §22 that is branch one: **regime
explains both years; momentum is dormant, not dead.** I am not adjusting the
rule — it selected this branch and that stands.

**But the branch label is stronger than the evidence behind it, and the
honest report has to say so.** The reading was written assuming 2020–21 were
"normal" and 2022 "collapsed". What the data actually shows is that **2021
was the outlier high** and the other three years are similar:

```
sd     : 15.17%  ->  30.29%  ->  13.03%  ->  13.13%
price$ :   +163  ->    +110  ->     +30  ->     -37
```

2020 and 2023 have almost the same dispersion (15.2% vs 13.1%) and opposite
price PnL (+163 vs −37). Dispersion therefore cannot be the whole story: if
"momentum needs dispersion" were sufficient, 2020 should look like 2023, and
2021 (dispersion doubled) should have been the best year rather than the
second best. So the correct statement is: *dispersion did collapse after
2021 and stayed collapsed, which is consistent with regime — but the same
dispersion level supported a strongly positive 2020 and a negative 2023, so
something else changed as well.* Neither hypothesis is cleanly established;
regime is supported, decay is not excluded.

Two further caveats, both flagged in advance by STAGE3A 2.1:

- **Universe size grew 29 → 104 → 129 → 166.** A decile spread over 29 names
  is not the same statistic as over 166, and the growth is monotonic in the
  same direction as the PnL decline. This is a real confound and it is not
  possible to remove it from these numbers.
- **Correlation to BTC does not carry the story either**: 0.639 → 0.555 →
  0.711 → 0.605. 2022 is the correlation spike, as expected, but 2023 (0.605)
  is back near 2020 (0.639) — the year with the best price PnL.

Crash months against the 2022 annual mean: 2022-05 (LUNA) sd 12.66% vs
13.03%, decile 43.60% vs 44.22% — indistinguishable; 2022-11 (FTX) sd 18.47%,
decile 56.41% — *above* the annual mean. The crashes did not suppress
dispersion; if anything FTX raised it.

### 22.2 (3) Drawdown timing — the kill switch never fired, but the trough is in 2022

```
 year   maxDD         peak       trough   eq peak  eq trough
 2020  12.11%   2020-07-08   2020-11-12    520.49     457.46
 2021  13.71%   2021-05-21   2021-06-28    661.33     570.66
 2022  18.94%   2022-01-06   2022-04-22    673.63     546.05
 2023  14.18%   2023-05-25   2023-08-10    684.58     587.48
```

**Global max drawdown 27.87%: peak 2021-11-25 ($757.01) → trough 2022-04-22
($546.05).** Explicit answer to the question STAGE3A 3 says matters most:
**the 30% kill switch would NEVER have fired** on the daily path — worst
observed 27.87%, which is 2.1 points of headroom and no more.

The trough does sit inside 2022, and the drawdown *spans the year boundary*
(Nov 2021 → Apr 2022), so no single calendar year shows it: the worst
per-year figure is 18.94%. "2022 was flat" (Sharpe 0.05) is true annually
and **misleading operationally** — the strategy was in its deepest drawdown
of the whole sample during that year, and per-year Sharpe conceals it.

### 22.3 (4) Funding by regime — and a correction to §18.2

**§18.2 said the carry comes from being short crowded-long alts, with
leveraged retail longs as the payer. That was wrong about the direction.**

```
funding by leg: long +166.02 | short +39.28 | total +205.30
```

**81% of funding income accrues on the LONG leg**, not the short. A long
position receives when the rate is negative, i.e. when *shorts* are the
crowded side paying to stay short. So the strategy is predominantly long
coins that shorts are paying to hold, not short coins that longs are paying
to hold.

Sharper still — it is a tail, not a steady stream:

| | settlements | mean rate | median rate | % negative | funding |
|---|---|---|---|---|---|
| long positions | 20,701 | **−0.000144** | **+0.000100** | 27.1% | **+166.02** |
| short positions | 20,725 | +0.000082 | +0.000100 | 18.4% | +39.28 |

The long leg's *median* rate is positive: on a typical settlement the long
leg **pays**. The +166 comes entirely from the 27% of settlements with
negative rates, some violently so — squeezes in names the momentum signal
had already selected as winners.

Concentration, three ways, all pointing the same direction:

- **By BTC drawdown depth**: `>50%` bucket contributes **+148.39 of +205.30
  (72%)**, against +16.92 in the `0–10%` bucket over a comparable number of
  settlements. Funding income is overwhelmingly a deep-bear-market
  phenomenon.
- **By month**: 2023-08 (+42.9) and 2023-09 (+52.7) alone are **+95.6, or
  47% of the entire four-year funding total.**
- **By sign**: the short leg paid on 388 days and received on 992 (28.1% of
  days paying).

This is the sharpest risk finding in the project so far. The dominant return
source is not a steady carry yield; it is a **tail payoff concentrated in
deep drawdown regimes and, within those, in two months.** The holdout window
(2025-01 → 2026-07) is a period in which BTC made new highs — the `>50%`
drawdown bucket that produced 72% of train funding would largely not exist
there. That is independent of, and additional to, the documented carry decay.

### 22.4 (5) Crash resilience — a genuinely positive result

```
 year  lev med  lev p95  realised beta  rebal  skips
 2020     0.62     1.27         +0.007    250    115
 2021     0.28     0.47         -0.002    335     30
 2022     0.48     0.75         -0.023    332     33
 2023     0.47     0.77         +0.045    365      0
```

STAGE3A 5 asked for this to be stated as a positive if it held, and it held:

- **Realised beta to BTC never exceeded ±0.05 in any year** — including 2022,
  when cross-sectional correlation to BTC spiked to 0.711. The hedge did not
  break under the correlation spike.
- **Leverage stayed low throughout**: median 0.28–0.62, p95 never above 1.27,
  against a 3.0 cap.
- **The harness stayed functional through the crash**: 33 skips in all of
  2022 (23 `universe_too_small`, 6 `below_min_notional_post_hedge`, 3
  `missing_fill_bar`, 1 `insufficient_candidates`).
- **Closest approach to liquidation**: worst-case intraday equity / close
  equity = 0.843 on 2022-06-07; minimum implied equity $366.62, i.e. 92% of
  starting capital. Nowhere near a margin event.

**The risk design works.** That is a separate question from whether the alpha
works, and it should not be used to argue the latter.

### 22.5 Which hypothesis the evidence supports, and what would change it

**Verdict: regime is supported but not established; decay is not excluded.**
The pre-registered rule selects regime, and I am reporting that as it stands.
The qualification is that 2020 and 2023 share a dispersion level and have
opposite price PnL, so dispersion alone does not explain the decline, and the
monotonic growth of the universe (29 → 166) is confounded with it.

What would change this conclusion:

- **Toward decay**: a validate run (2024) showing price PnL still negative
  while dispersion recovered. Costs a trial.
- **Toward regime**: dispersion recovering in 2024–25 *and* price PnL
  recovering with it. Also costs a trial, and the holdout is the wrong
  instrument for it (1.58 years, resolves nothing below Sharpe ~1.6).
- **Neither, cheaply**: nothing further. The train window is exhausted as an
  information source on this question — every remaining discriminator needs
  out-of-sample data, which means a trial.

The practical consequence for the two remaining trials: the §18.2 correction
above **strengthens** the case for Option A (pure carry benchmark), because
the funding mechanism is now known to be (a) the dominant return source, (b)
long-leg dominant rather than short-leg, and (c) a tail concentrated in deep
drawdowns. A carry benchmark tests whether the momentum ranking selects those
tail episodes better than a direct funding signal would — which is precisely
the open question.

### 22.6 (6) Candidates arising — none implemented

| Candidate | Status after 3a |
|---|---|
| Dispersion/correlation filter | **Weakened.** 2020 and 2023 share a dispersion level with opposite outcomes, so a threshold fitted to "low dispersion" would not have separated them. Still costs a trial; now with less motivation than before 3a |
| Funding-sign filter | **Changed shape, not implemented.** The income is long-leg and tail-driven, so a sign filter is really a "be long the squeezed names" filter. Costs a trial |
| Per-name stop loss | Still unmotivated: kill switch never fired, closest liquidation approach 0.843 |
| Correlation-regime vol target | Overlaps the dispersion filter; both weakened by 22.1 |
| **NEW: drawdown-regime exposure scaling** | Arises from 22.3 (72% of funding in `>50%` BTC drawdown). Explicitly NOT implemented: it is a strategy change, costs a trial, and choosing the depth threshold from these numbers is exactly the fitting STAGE3A 9 forbids |

Nothing above was implemented. The config remains frozen at `lookback=14,
skip=0`; budget remains **6 of 20**; validate and holdout untouched.

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
deep drawdown regimes and, within those, in two months.**

**CORRECTION (Stage 3b §4, 2026-08-28).** The sentence that stood here
claimed the holdout window "is a period in which BTC made new highs" and
that the `>50%` drawdown bucket "would largely not exist there". **That was
factually wrong.** Bitcoin peaked at $126,296 on 2025-10-06, then fell
~46.7% to about $67,550 by mid-February 2026 and was roughly 49% below the
peak by June 2026, with total crypto market cap down ~48%. The holdout
(2025-01 → 2026-07) therefore contains **both** regimes: new highs through
October 2025, then a ~50% drawdown across the remainder. **The deep-drawdown
bucket that produced 72% of train funding does exist in the holdout.**

I asserted a fact about the holdout period from memory instead of checking
it, and it happened to point the conclusion in the pessimistic direction.
The corrected reading is not "the funding regime is absent from the
holdout"; it is that the holdout contains a bull-to-bear transition of the
same shape as the one in train.

Which sharpens a structural echo worth stating: **our worst drawdown ran
2021-11-25 → 2022-04-22** — beginning two weeks after the November 2021
peak and troughing *before* both LUNA (2022-05) and FTX (2022-11). So the
vulnerability this strategy has shown is **the bull-to-bear transition, not
crash depth**. The October 2025 peak sits in the same position relative to
the holdout that November 2021 sits relative to train. That is a reason to
expect the holdout to be informative about the risk, not a prediction of the
result.

The documented carry decay (negative in 2025) remains a separate concern and
is unaffected by this correction.

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
| **NEW (3b): liquidity-rank cap / market-cap tilt** | The strongest-motivated candidate on the table after §23.1: `101+` loses money every year it exists and grew to 22.1% of position-days. **NOT implemented.** It is a configuration change costing a trial, and — the binding constraint — **the cap cannot be parameterised from this attribution**: choosing "top 30" because top-30 looked best here is fitting the cap to the result that motivated it. If it is worth a trial, the boundary must come from the published ~2%-of-market-cap finding or an independently derived liquidity threshold, **pre-registered before any backtest of it runs** |

Nothing above was implemented. The config remains frozen at `lookback=14,
skip=0`; budget remains **6 of 20**; validate and holdout untouched.

## 23. Stage 3b — rank-bucket attribution: PRE-REGISTERED READING

**Recorded 2026-08-28 BEFORE any rank or bucket number was computed**, per
STAGE3B 3/6.1 and the Stage 3a precedent.

The hypothesis: §22.1 showed dispersion does not explain the price-PnL
decline, and I flagged universe growth as a confound. Universe growth fits
better than dispersion because it is monotonic in the same direction:

```
universe  :   29  ->  104  ->  129  ->  166      (monotonic)
price$    : +163  ->  +110  ->  +30  ->   -37    (monotonic)
dispersion: 15.2  ->  30.3  ->  13.0 ->  13.1    (NOT monotonic)
```

Mechanism proposed: published research puts momentum in roughly the top 2%
of coins by market cap, with the other 98% showing *negative* average
momentum payoffs. In 2020 only large caps had perpetual listings, so a
29-name universe was effectively majors-only; by 2023 the strategy ranked
166 names dominated by mid/small caps. If true, **the alpha did not decay —
it was progressively diluted by the universe it was permitted to trade.**

The reading, fixed in advance:

| If... | Then |
|---|---|
| top-30 price PnL per position-day stays **positive across all four years**, while `101+` is negative and its share of position-days rises | **DILUTION.** The alpha survives where it always lived; the universe grew into the negative-payoff segment |
| top-30 price PnL per position-day **declines across years like the aggregate** | **DECAY.** The alpha weakened where it was strongest; universe growth is coincidental |
| **no consistent rank pattern** | **NEITHER.** Something else drives the decline and this axis is exhausted too |

Method, also fixed in advance: liquidity rank computed **point-in-time
through `PITView`** at each rebalance date, using the same measure
`universe()` uses (median daily quote volume over the trailing 30 bars),
1 = most liquid. Buckets `1-30`, `31-100`, `101+`, **boundaries fixed now
and not to be changed after seeing results**. Input is the existing
per-symbol daily PnL trace, which reconciles to `gross_pnl` exactly; bucket
sums must reconcile to per-year price PnL or the run stops and reports.

Structural limit, acknowledged in advance: the 2020 universe peaked at 29
names, so the lower buckets are empty or near-empty that year. Within-year
bucket comparison is impossible for 2020, so **the test is across years
within a bucket**, and bucket availability is reported per year so an empty
cell is never read as a zero result.

Nothing will be implemented from this (STAGE3B 5): if "dilution" fires, the
implied universe restriction is a configuration change costing a trial, and
its cap cannot be parameterised from this attribution without fitting it to
the result that motivated it. Config stays frozen; budget stays 6 of 20.

### 23.1 Result — DILUTION fires, and cleanly

Ranks computed at 1,402 dates; **4,614,240 bars asserted `close_time <=
as_of`** (the point-in-time requirement is enforced, not assumed). Bucket
price PnL reconciles to per-year totals to ~1e-13 with **zero unranked PnL**
in every year.

```
 year   bucket  avail    price$  funding$  pos-days   share  price/day     long$    short$
 2020     1-30    yes    +99.92    +24.16      3026   89.8%    +0.0330   +302.51   -202.59
 2020   31-100    yes    +63.20     +0.11       345   10.2%    +0.1832    +58.33     +4.87
 2020     101+    n/a         -         -         0       -          -         -         -
 2021     1-30    yes     +5.05    +14.69      1780   38.6%    +0.0028    +81.21    -76.16
 2021   31-100    yes   +164.89     +2.12      2637   57.2%    +0.0625   +313.40   -148.52
 2021     101+    yes    -59.48     +0.48       197    4.3%    -0.3019    -35.41    -24.07
 2022     1-30    yes    +13.81     -6.82      1367   29.7%    +0.0101    -61.27    +75.08
 2022   31-100    yes    +32.71     -0.98      2661   57.8%    +0.0123   -101.35   +134.06
 2022     101+    yes    -16.35     +1.80       575   12.5%    -0.0284    -10.76     -5.59
 2023     1-30    yes   +204.07    +74.19      1446   30.3%    +0.1411   +162.67    +41.40
 2023   31-100    yes    -39.84    +50.68      2266   47.5%    -0.0176   +123.30   -163.14
 2023     101+    yes   -201.27    +44.86      1056   22.1%    -0.1906   -123.80    -77.47
```

`101+` is unavailable in 2020 — the universe never reached rank 101 that
year. That cell is empty, **not zero**.

**The test — top-30 price PnL per position-day:**

```
  2020 +0.0330 -> 2021 +0.0028 -> 2022 +0.0101 -> 2023 +0.1411
```

**Positive in all four years, and highest in the final year.**

**The dilution mechanism — `101+` share of position-days:**

```
  2020  0.0% (n/a)  ->  2021  4.3%  ->  2022 12.5%  ->  2023 22.1%
```

Rising monotonically, and `101+` price PnL per position-day is **negative in
every year it exists** (−0.3019, −0.0284, −0.1906).

All three branch-one conditions hold exactly as written in §23. **DILUTION
fires.** The alpha did not decay; it was progressively diluted by the
universe the strategy was permitted to trade.

2023 makes the point vividly: **top-30 earned +$204.07 of price PnL while
`101+` lost −$201.27.** They nearly cancel, and the reported year is −$37.
The most liquid segment had its *best* year of the entire sample in the same
year the aggregate went negative.

### 23.2 Where the evidence is weaker than the label

Per §23's instruction to say so plainly:

- **The clean finding is the bottom bucket, not a liquidity gradient.**
  `101+` is consistently negative with a rising share — that is solid. But
  `31-100` **outperformed** `1-30` in three of four years (+0.1832 vs
  +0.0330 in 2020, +0.0625 vs +0.0028 in 2021, +0.0123 vs +0.0101 in 2022),
  reversing only in 2023. So "more liquid is better" is *not* an established
  monotonic gradient; "the illiquid tail loses money" is.
- **2021 is awkward for the story.** Top-30 was nearly flat (+0.0028) while
  the mid bucket carried the year. If the alpha lived in the majors, 2021
  should not look like that.
- **2020's `101+` cell is structurally empty**, so the mechanism can only be
  observed over three years, one of which (2022) has small magnitudes
  throughout.
- **Bucket boundaries were fixed in advance** (§23) and not revisited. Had
  they been chosen after the fact, 2023's near-perfect cancellation would
  have made almost any boundary look decisive.
- **Funding does not follow the price pattern.** In 2023 the `101+` bucket
  earned +$44.86 of funding against −$201.27 of price. So the illiquid tail
  is a large *net* loser (−$156) even after carry, but the carry mechanism
  itself is broad-based across liquidity, not concentrated where the price
  alpha is.

### 23.3 A method error found and fixed before reporting

The first run stranded **$30.82 of 2022 price PnL as "unranked" — more than
2022's entire price PnL of $30.17.** It arithmetically reconciled, so a
weaker check would have passed it.

Cause: I keyed each symbol's rank to the *current* book's decision date. A
symbol dropped at a rebalance still books PnL on its exit day (it is marked
before the fill), and the new decision does not contain it. LUNAUSDT's
+$24.69 exit on 2022-02-28 fell through that hole — verified directly:
LUNAUSDT was in neither `universe()` nor the book on that date.

Fixed by keying each symbol to the decision under which it was actually
**held**, carrying that key forward when the symbol leaves the book. After
the fix: zero unranked in all four years, and 2022's top-30 figure moved
from −0.0049 to **+0.0101** — which is the difference between branch one
failing and firing. The first numbers were not reported as a result.

### 23.4 What this changes, and what it does not

It **reopens** the question §22.5 closed. Stage 3a concluded "the train
window is exhausted as an information source"; that was true on the
dispersion axis and wrong as a general claim — the composition axis was
still available and has now been read.

It does **not** establish that a universe restriction would have worked.
That is a different claim requiring a backtest, and per §23/STAGE3B 5 it
costs a trial and cannot be parameterised from this attribution.

Interaction with §22.3, worth stating: the price alpha now looks alive in the
liquid segment (2023 was its best year), while the funding income is a tail
concentrated in deep-drawdown regimes. Those are two different return sources
with two different regime dependencies, and the frozen config holds both.

## 24. Stage 3c Part A — bootstrap the buckets: PRE-REGISTERED READING

**Recorded 2026-08-28 BEFORE any confidence interval was computed**, per
STAGE3C 3/7.1 and the 3a/3b precedent.

Why this gates Part B: §23 reports twelve cells with **no error bars**, and
its headline depends on a bug found mid-analysis (§23.3) whose fix moved
2022 from −0.0049 to +0.0101 — exactly the difference between branch one
failing and firing. A conclusion that fragile must survive resampling before
it justifies a trial.

**Method, fixed in advance.** Stationary (Politis–Romano geometric-block)
bootstrap over the **daily** bucket PnL series — never over position-days,
which are correlated within a day and would give intervals that are too
tight. "Price PnL per position-day" is a *ratio*, so each resampled day
carries both its PnL and its position-day count and the statistic is the
ratio of the resampled sums. The per-year spread resamples both buckets on
the *same* days so they stay aligned. Block length is reported with the
measured autocorrelation that justifies it. 90% CIs, 2000 resamples.

The reading:

| If... | Then |
|---|---|
| pooled `101+` CI **entirely below zero** AND the top-30-minus-`101+` spread CI excludes zero in **at least 2 of the 3** years where both exist | dilution survives → **proceed to Part B** |
| pooled `101+` CI **straddles zero**, or the spread excludes zero in **≤1** year | dilution not established → **STOP, do not run Part B** |
| pooled `101+` below zero but the spread is significant **only in 2023** | weak, rests on one year → **STOP and report**; the decision moves to the user |

Reported but **not** gating: whether 2021's top-30 cell (+0.0028/day) is
distinguishable from zero, and whether `31-100` beating `1-30` in 2020–2022
survives its own CI — §23.2 led with that caveat and it should be resolved
rather than carried forward.

Thresholds will not be adjusted after seeing the intervals. Part A spends no
trial; budget stays 6 of 20 unless and until Part B is authorised by branch
one.

### 24.1 Part A result — BRANCH ONE, dilution survives

Stationary bootstrap, 2,000 resamples, 90% CIs, over **daily** series with
ratio-of-sums statistics. Lag-1 autocorrelation of daily strategy PnL is
**−0.0136** (essentially none), so the block length is set by the
`n^(1/3)` rule at **11.7 days** rather than by measured dependence; both
figures are reported so the choice is auditable.

```
 year   bucket      point                90% CI  excl 0  pos-days
 2020     1-30    +0.0330   [ -0.0122, +0.0814]      no      3026
 2020   31-100    +0.1832   [ +0.0419, +0.2924]     yes       345
 2020     101+        n/a                   n/a       -         0
 2021     1-30    +0.0028   [ -0.0675, +0.0776]      no      1780
 2021   31-100    +0.0625   [ -0.0081, +0.1242]      no      2637
 2021     101+    -0.3019   [ -0.5496, -0.0739]     yes       197
 2022     1-30    +0.0101   [ -0.0736, +0.0986]      no      1367
 2022   31-100    +0.0123   [ -0.0675, +0.1238]      no      2661
 2022     101+    -0.0284   [ -0.1771, +0.1390]      no       575
 2023     1-30    +0.1411   [ +0.0512, +0.2299]     yes      1446
 2023   31-100    -0.0176   [ -0.0860, +0.0595]      no      2266
 2023     101+    -0.1906   [ -0.3048, -0.0674]     yes      1056
```

**The gating quantities:**

| | point | 90% CI | |
|---|---|---|---|
| pooled `101+` (3 years) | −0.1516 | [−0.2437, −0.0603] | **entirely below zero** |
| pooled top-30 (4 years) | +0.0424 | [+0.0084, +0.0793] | excludes zero |
| spread 2021 | +0.3048 | [+0.0313, +0.5391] | **significant** |
| spread 2022 | +0.0385 | [−0.1511, +0.2179] | not significant |
| spread 2023 | +0.3317 | [+0.1810, +0.4803] | **significant** |

Pooled `101+` below zero **and** the spread significant in **2 of 3** years
→ **branch one**, exactly as written in §24. Part B is authorised.

Only 3 of the 12 individual cells are individually significant, which is the
expected shape: single cells are thin, and pooling is what the reading was
designed around.

**The §23.2 caveats, now resolved against intervals:**

- **2021's top-30 (+0.0028) is NOT distinguishable from zero** (CI
  [−0.0675, +0.0776]). §23.2 was right to flag it; it is noise, not a weak
  positive.
- **`31-100` beating `1-30` survives only in 2020** (+0.1502, [+0.0134,
  +0.2591]); 2021 and 2022 are **not** significant. And 2023 reverses
  significantly the other way (−0.1587, [−0.2815, −0.0264]). So the picture
  is not "mid beats top": it is 2020 mid, 2023 top, and noise in between —
  which is direct support for the competing hypothesis STAGE3C §4.2 raises,
  that **the profitable segment migrates with regime**. What is stable is
  only the negative bottom bucket.

## 25. Stage 3c Part B — universe cap: PRE-REGISTRATION (trial 7)

**Recorded 2026-08-28 BEFORE the capped configuration was run**, per
STAGE3C 4/7.5.

**The cap: exclude names of liquidity rank 101+ from candidacy.** Everything
else identical to frozen: `lookback=14`, `skip=0`, capital $400, N=10, 20%
vol target, 3× cap, taker-only, 5bps, +1min.

**Why 100, and why that is not fitting.** The boundaries `1-30 / 31-100 /
101+` were pre-registered in §23 *before any bucket number existed*. Using
one of them as a cap therefore inherits that pre-registration rather than
being chosen from the result. Rejected alternatives, recorded so the choice
can be audited:

| Cap | Why not |
|---|---|
| rank 30 | fitted to 2023, the only year `1-30` beat `31-100` — and §24.1 shows that ordering reverses significantly between 2020 and 2023 |
| top 2% by market cap (literature) | 2% of 166 names is three. Not a portfolio |
| a new dollar liquidity threshold | any figure chosen now is chosen knowing the answer |

**The competing hypothesis, stated before the run.** §24.1 shows the
profitable segment *migrates*: mid-caps significantly better in 2020,
majors significantly better in 2023, noise between. If the segment moves,
a fixed cap freezes a boundary that does not hold still. What is stable
across all years is only the negative bottom bucket — a **floor**, not a
cap. So this trial tests **"exclude the tail"**, NOT "trade only the
majors", and must not be reported as the latter.

**What counts as success — fixed now:**

| Outcome | Reading |
|---|---|
| Sharpe improves **and** 2023 price PnL turns positive | dilution confirmed and remediable |
| Sharpe improves but 2023 price PnL stays negative | something else drives 2023; the cap helps for another reason |
| Sharpe roughly unchanged | the tail was noise, not drag — the attribution was misleading |
| Sharpe worsens | the tail carried diversification the attribution missed |

**No re-run at a different cap under any outcome. One trial, one cap.**

**Trial accounting: 6 → 7.** Four runs are executed — slippage {0, 5} ×
execution delay {0, 1} — as cost sensitivity, reported together and never
selected between. Both are cost *assumptions*, not strategy parameters, so
`strategy_key` excludes both from the Deflated Sharpe trial count (it
already excluded slippage; delay is added on the same reasoning and the
change merges no existing rows, since every logged row has delay=1). The
trial is logged **before** execution; if it errors, it is still spent.

**One trial remains after this.**

### 25.1 Trial 7 result — Sharpe improves AND 2023 price PnL turns positive

Trial logged **before** execution (`status: started`, commit `611d13b`), per
STAGE3C 5. Four runs, slippage {0,5} × delay {0,1}, reported together as
cost sensitivity and never selected between. **Budget 6 → 7. One remains.**

```
 slippage  delay   sharpe   90% CI (bootstrap)  ann_ret   maxDD   feedrag
    5 bps  1 min    1.059   [+0.23, +1.85]      24.92%   25.64%    27.30%   <- BASELINE
    5 bps  0 min    0.941   [+0.11, +1.73]      21.40%   28.28%    32.82%
    0 bps  1 min    1.178   [+0.36, +1.95]      28.23%   23.55%    23.78%
    0 bps  0 min    1.033   [+0.22, +1.81]      23.96%   24.17%    28.66%
```

At the pre-registered baseline (5bps, +1min), against the uncapped frozen
config: **Sharpe 0.796 → 1.059**, annualised return 17.83% → 24.92%, max
drawdown 27.87% → 25.64%, fee drag 47.95% → 27.30%, turnover essentially
unchanged (109.3 → 107.7). Deflated Sharpe **0.878 at 7 trials**.

**Per-year, directly comparable to §19.3** (uncapped in brackets):

```
 year  sharpe        price$      funding$        long$        short$
 2020  1.83 [1.83]  +163 [+163]   +24 [+24]   +361 [+361]   -191 [-191]
 2021  1.15 [0.91]  +148 [+110]   +17 [+17]   +391 [+359]   -235 [-241]
 2022 -0.04 [0.05]   +25  [+30]   -16  [-6]   -230 [-173]   +236 [+186]
 2023  1.50 [0.65]  +147  [-37]  +152 [+170]  +340 [+162]   -178 [-185]
```

```
price PnL by year, uncapped: +163 -> +110 -> +30 -> -37   (monotonic decline)
price PnL by year, capped  : +163 -> +148 -> +25 -> +147  (decline gone)
```

**§4.3 outcome: "Sharpe improves AND 2023 price PnL turns positive" →
dilution confirmed and remediable.** 2020 is identical to four decimal
places because the universe never reached rank 101 that year, so the cap
binds nothing — an unplanned but clean control. 2022 remains flat
(−0.04), so **the cap does not explain 2022**; that year stays what §22.2
said it was, the bull-to-bear transition containing the sample's worst
drawdown.

**Bucket verification** (position-days by rank, capped vs uncapped share):

```
 year   101+ share capped   101+ share uncapped
 2020        n/a                  n/a
 2021        2.2%                 4.3%
 2022        2.8%                12.5%
 2023        2.6%                22.1%
```

### 25.2 The cap is weaker than its name — and that matters for reading it

The residual `101+` exposure is not zero, and the reason is worth stating
because it changes what was actually tested.

The cap ranks within the **eligible** universe — post-`MIN_NOTIONAL`, and
since Stage 2e §1 that filter uses the *realised* gross leverage
(~0.44), which removes high-floor names. Stage 3b's attribution ranked
within the **full liquid** universe. The two bases differ by a handful of
names, so a name at attribution-rank 101 can sit inside the engine's top
100. Verified directly: on 2021-05-22 the eligible universe is **96**
names at `gross_hint=0.44` versus 102 unfiltered, so the rank-100 cap
binds *nothing* that day and UNFIUSDT (attribution-rank 101) is traded.

How often the cap actually binds:

```
 year  median eligible universe   max   % of decision days where the cap binds
 2020            24                57                    0.0%
 2021           107               125                   63.0%
 2022           131               140                   79.9%
 2023           163               204                  100.0%
```

So this trial did not test "exclude everything past rank 100 of the liquid
universe". It tested **"exclude everything past rank 100 of the eligible
universe, on the days there are more than 100 eligible names"** — inert in
2020, partial in 2021–22, fully binding in 2023. That the largest effect
(2023: −37 → +147) lands in the year the cap binds every day is consistent
with the dilution mechanism rather than coincidental, but the cap is a
weaker instrument than "rank 100" suggests.

**No re-run to align the two bases.** STAGE3C 4.3/9 forbid a second cap
value under any outcome, and re-running on a different ranking basis is a
different cap. The discrepancy is recorded; it is not repaired by spending
the last trial.

### 25.3 Who is paying, and why would they keep paying?

Composition at the capped baseline: gross price PnL **+482.81**, funding
**+177.16**, fees 131.82 → net +528. So the cap **reverses the balance
§18.2/§22.3 described**: price PnL is now 73% of gross profit and funding
27%, where uncapped it was price +266.70 against funding +205.30 (roughly
half each).

- **The momentum payer** is the one the spec's §6 names: late trend-chasers
  entering after moves are established, and holders liquidated into
  weakness. The capped book earns this in the liquid segment, where the
  published literature puts the effect and where there is enough depth for
  a crowd to be late in.
- **The funding payer** is unchanged from §22.3 and still the sharper risk:
  81% long-leg, tail-driven, 72% of it arriving while BTC is >50% below its
  peak. The cap reduces funding from +205 to +177 while roughly doubling
  price PnL, so the strategy becomes *less* carry-dependent, which is the
  direction that reduces exposure to the 2024–25 carry decay.

Why they would keep paying: the momentum leg's counterparty story is
behavioural and does not obviously exhaust, but it is also the leg the
literature says decays with capital. The honest position is that this trial
moved the return source from one with a documented decay (carry, negative
in 2025) toward one with a behavioural story — **on train only**, and one
train result at Sharpe 1.06 with a 90% CI of [+0.23, +1.85] does not
establish an edge.

### 25.4 Status

Trial 7 spent. **One trial remains of twenty.** Validate and holdout
untouched. Nothing re-run, no second cap tried, the §25 pre-registration
unmodified since `2aa3424`.

## 26. Stage 3d Part A — paired bootstrap: PRE-REGISTERED READING

**Recorded 2026-08-28 BEFORE any paired interval was computed**, per
STAGE3D 2/7.1.

Why this test and not the one already done: §25.1 reports capped Sharpe
1.059 with a 90% CI of [+0.23, +1.85], and the uncapped 0.796 **sits
comfortably inside it**. The standalone interval therefore cannot reject
"the cap did nothing". But capped and uncapped ran on the *same days*, so
the market noise they share cancels in the difference — a paired bootstrap
on the difference series is far tighter because it removes common variance.

**Method, fixed in advance.** Align the two daily series over the identical
train window and **assert the dates match exactly**; a mismatch stops the
run. Form `d_t = capped(t) − uncapped(t)` and stationary-bootstrap **the
difference series directly** — never two independent bootstraps subtracted,
which would put back the common noise this test exists to remove. Block
length is recomputed from the *difference* series' own lag-1
autocorrelation, not reused from §24.1's −0.0136. Report 90% CIs on mean
daily difference, annualised return difference, and Sharpe difference.

The reading:

| If... | Then |
|---|---|
| Sharpe-difference CI **entirely above zero** | the cap's effect is established → **proceed to Part B** |
| CI **straddles zero** | not established → **STOP**, do not spend the last trial validating the cap |
| CI above zero **only with 2023 included**, and the 2021–22 subset straddles zero | weak, rests on one year → **STOP and report**; decision moves to the user |

Also reported: the 2021–22 subset alone, 2023 alone, and the fraction of
days on which `d_t ≠ 0` — the cap binds on 0/63/80/100% of days by year, so
a large share of days contribute nothing and the effective sample is smaller
than the day count suggests.

**Confound to state alongside the result, not to test around:** the cap
binds on 100% of 2023 days *and* 2023 has the largest `101+` share (22.1%).
Removing the tail necessarily helps most where the tail is largest, so
2023's dominance is the mechanism restated, not independent evidence for
it.

Thresholds will not be adjusted after seeing the intervals. Part A spends no
trial; budget stays 7 of 20.

### 26.1 Part A result — BRANCH TWO. The cap's improvement is NOT established.

Aligned on **1,381 identical daily observations** (2020-03-21 → 2023-12-31);
dates matched exactly. The difference series has lag-1 autocorrelation
**+0.0822** against the level series' −0.0076 — recomputing it mattered, as
§26 required, though `n^(1/3)` still sets the 11.1-day block. The cap is
**inert on 34.3% of days** (908 of 1,381 have a non-zero difference), so the
effective sample is smaller than the day count.

```
 window                 capped   uncapped   Sharpe diff        90% CI          verdict
 FULL 2020-2023         +1.059     +0.796      +0.2624   [-0.0095, +0.5455]   straddles 0
 2021-22 (excl. 2023)   +0.485     +0.418      +0.0672   [-0.1799, +0.3071]   straddles 0
 2023 alone             +1.503     +0.651      +0.8523   [-0.0710, +1.7249]   straddles 0
```

**None of the three intervals excludes zero**, so the pre-registered branch
is two: *not established — stop, do not spend the last trial validating the
cap.* Part B (§27, the validate rule) is gated on branch one and is
therefore **not written**. No trial spent; budget stays **7 of 20**.

**The honest characterisation, including how close it came.** The
full-window lower bound is **−0.0095** — a hair below zero. It would be easy
to write "essentially significant" and proceed. The rule was fixed in
advance precisely to make that unavailable, and §26 says thresholds are not
adjusted after seeing intervals. A 90% interval that includes zero does not
establish an effect, and the margin being small is not evidence; it is the
same evidence, described more favourably.

Note also that **2023 alone does not clear the bar either** (CI
[−0.0710, +1.7249]). The single year carrying ~89% of the improvement is
itself not statistically distinguishable from noise on a paired basis. So
this is not the familiar "rests on one year" pattern — it is weaker than
that: no window supports the effect individually.

**What this does and does not overturn:**

- **Stands:** the `101+` bucket loses money. §24.1's pooled CI
  [−0.2437, −0.0603] is unaffected — that is an attribution of where PnL
  came from, and it remains significant.
- **Does not stand:** that *excluding* the tail produces a measurably better
  strategy. §25.1's 0.796 → 1.059 is a real point estimate on train, but the
  paired test says it is within what shared noise can produce.
- These are compatible. The tail is a losing sliver of position-days (2.2 to
  22.1% by year, and the cap only binds on 66% of days), and removing a
  losing sliver need not move the aggregate detectably. The attribution was
  right about the sign and could not, on its own, establish the magnitude.

**Why the paired test was the right one.** The standalone capped interval
[+0.23, +1.85] contains 0.796, so it never could have rejected the null; the
paired test removes shared market noise and is much tighter (width 0.56 vs
1.62) — and it *still* includes zero. The tighter, better-powered test is
the one that says no.

### 26.2 Where this leaves the project

**One trial of twenty remains, and it is now unallocated.** The candidates
in §22.6 are unchanged and none has been implemented. What Stage 3d
establishes is narrower than it looks: the case for spending the last trial
on *validating the cap specifically* has failed its own pre-registered gate.

Recorded for whoever decides next, without recommending a spend:

- The frozen uncapped config (§19.5) remains the baseline; the cap is not
  part of it and was never merged into the freeze.
- STAGE3_POSTGRID §5 Option A (pure carry benchmark) has not been run and
  is untouched by this result.
- The budget can be expanded deliberately, logged with a date and reason and
  the Deflated Sharpe recomputed at the higher count (STAGE3_POSTGRID §5) —
  that is the honest path if more than one question remains worth asking.
- Validate and holdout remain untouched, in that order, with the holdout
  still one look, ever.

## 28. Stage 3e — why the cap's effect was small, and what this project can detect

Zero trials. Budget stays **7 of 20**.

### 28.1 The cap tested a substitution, not a deletion

§26.1 said the tail losing money and the cap not helping are compatible
because the tail is a small sliver. True, but there is a sharper structural
reason, and it was knowable before any data was seen.

Pooled `101+` loss is **−0.1516/position-day × 1,828 position-days ≈ −$277**.
Capping improved price PnL by only **+$217**, and not significantly.

**Because `N=10` is fixed.** The cap does not *delete* those positions — it
*replaces* them with the next-best eligible candidate. The expected gain is
the margin between the 5th and 6th ranked name, not the full loss of the
5th. One noisy outcome is swapped for another.

The design lesson, which generalises past this experiment: **removing
candidates from a fixed-N selection tests a marginal substitution, not a
deletion, and the effect size is bounded by the rank-adjacent margin.** Any
future universe-restriction test carries the same ceiling, and that ceiling
should be estimated *before* the test is thought worth running.

### 28.2 Underpowered, not refuted

"Not established" and "no effect" are different claims, and the difference
governs what is worth trying next:

| | |
|---|---|
| paired point estimate | **+0.2624** Sharpe |
| implied SE | **0.169** |
| t | **1.56** |
| two-sided p | **0.120** |
| one-sided p | **0.060** |
| effective sample | cap inert on 34.3% of days → **908 of 1,381** days carry information |

A true effect of +0.26 Sharpe is entirely consistent with what was observed.
The finding is that **this sample cannot establish it** — not that it is
absent.

### 28.3 A methodological lesson, NOT grounds to reopen

A 90% interval is two-sided by convention. A **one-sided** test at the same
level would have passed (p = 0.060 < 0.10), and one-sided is arguably the
natural framing here, since only "the cap helps" was ever of interest.

**The branch-two decision stands.** Reversing it now is exactly the fudge
§26.1 refused, and §26 fixed the thresholds before the numbers.

The lesson is forward-looking and belongs on the pre-registration checklist:
**every future pre-registration must state one-sided or two-sided
explicitly.** It is decisive, and invisible until the moment it decides
something. §29 below states it.

### 28.4 What this project can actually detect (minimum detectable effect, 90%)

Never computed before, and it governs what the last trial is worth spending
on. MDE = the smallest true effect whose 90% interval would exclude zero.

```
 comparison                                          SE   MDE 2-sided   MDE 1-sided
 1. paired, two configs on train (the 3c/3d shape) 0.169        0.28          0.22
 2. standalone Sharpe, validate 2024 (1.00y)       1.001        1.65          1.28
 3. standalone Sharpe, holdout (1.58y)             0.796        1.31          1.02
 4. paired, two configs on validate (1.00y)        0.328        0.54          0.42
```

**This is the most important number in the document.** The frozen uncapped
config's train Sharpe is **0.796**. The validate MDE is **1.65** two-sided
(1.28 one-sided), and the holdout MDE is **1.31** (1.02 one-sided).

So: **no standalone out-of-sample test available to this project can
establish this strategy.** Validate and holdout can *refute* — a sufficiently
bad result is informative — but neither can confirm an edge of the size this
strategy plausibly has. That is a property of 1.0 and 1.58 years of daily
data, not of the strategy, and no amount of care in the harness changes it.

Against the §22.6 candidates: a paired train comparison detects ~0.28
Sharpe, and the one candidate actually measured (the rank cap) came in at
+0.26 — i.e. **the best-motivated idea on the list sits just under the
detection threshold of the best-powered test available.** Nothing else on
that list has an obvious reason to be larger. Stated plainly: on the current
sample, the remaining candidates are mostly not distinguishable from noise
even if they work.

### 28.5 USDC-margined universe — feasibility fact, nothing switched

`exchangeInfo` query only; no backtest, no trial, nothing modelled.

```
TRADING perpetuals   567   |  USDT-quoted 524  |  USDC-quoted 38
USDC base assets     38, and ALL 38 also exist as USDT pairs (zero unique names)
MIN_NOTIONAL         USDC: $5 x36, $20 x1 (ETH), $50 x1 (BTC)  -- same structure as USDT
24h quote volume     USDC median $6.0M, 20 of 38 clear $5M
                     USDT median $2.5M, 173 of 524 clear $5M
already ingested     0 of 38
```

Fee context: USDC-margined futures are **0.0000% maker / 0.0400% taker** at
Regular User (0.0360% with the BNB discount) versus USDT's 0.0200% /
0.0500%. USDC at Regular User equals USDT at VIP 2, with no holding
requirement. Taker cost is ~20% lower, which is material against a fee drag
that runs 27–48% of gross PnL.

The observation worth recording, per STAGE3E 4: **a USDC universe's boundary
would be set by Binance's listing decisions rather than chosen by us** —
which sidesteps the parameterisation problem that made the rank cap
unfalsifiable-by-construction in STAGE3C §4.1. That is a genuine structural
advantage over any threshold we could pick.

Against it, unprompted but it belongs next to the observation: **20 liquid
names means N=10 trades the top and bottom 25% of the universe**, against
~3% today. `IR ≈ IC × √breadth` — the fee saving would be bought with a
large breadth loss, and A4 of Stage 2b argued breadth is the only lever on
achievable Sharpe. Nothing here says the trade is favourable; it says the
boundary problem is different, not that the result would be better.

**Nothing switched, nothing modelled, no data ingested.**

### 28.6 BNB fee discount and the corrected VIP criteria

Hold enough BNB in the futures wallet to cover fees: **10% off futures fees
immediately**, no strategy change, no VIP threshold involved. Keep the
balance to roughly what fees require — it is an unhedged directional
position inside a market-neutral strategy and should not be stockpiled.

**VIP criteria, corrected for the record.** VIP 1 is **5 BNB + ($1M spot /
$5M futures / $100k wallet / $100k net borrowing)**, and **VIP 1 does not
improve the futures taker rate at all** (0.0500% at both Regular and VIP 1).
Any earlier figures in this project of **$15M volume or 25 BNB were wrong**
and are struck. (They do not appear in these notes — they were stated in
conversation only — but they are recorded as struck here so the corrected
numbers are the ones on file.)

## 29. THE VALIDATE RULE — pre-registered 2026-08-28, before any decision to run

Written for the **uncapped frozen config** of §19.5: `lookback=14`,
`skip=0`, capital $400, N=10, 20% vol target, 3x cap, taker-only, 5bps,
+1min, no liquidity cap.

Written now **while it is still undecided whether validate will be run at
all** — there is no result to fit it to and no decision riding on it. Once
written it is not modified. If validate runs, it is scored against exactly
this text.

### 29.1 The seven criteria

**1. Minimum validate Sharpe to justify spending the holdout: ≥ 0.30.**

The project's pre-registered stop threshold of 0.3 (§ cost curve) applies
here, and deliberately *not* something higher. §28.4 is the reason: the
validate MDE is 1.65 two-sided, so a threshold above ~0.3 would be
demanding a result the sample cannot produce even if the strategy works. A
higher bar would not be more rigorous; it would be a coin flip dressed as
rigour. 0.30 is therefore a **refutation** threshold — below it the
strategy is behaving worse than the weakest version of itself worth
trading — not a confirmation threshold.

**2. Sign requirement on price PnL: validate price PnL must be > 0.**

Uncapped train price PnL was already negative by 2023 (−$37). If it is
negative again in 2024 that is two consecutive years of the momentum engine
failing, which is the core claim of the strategy. Negative validate price
PnL is a **fail on this criterion regardless of headline Sharpe**, because a
positive Sharpe carried entirely by funding is the carry trade of §22.3 with
a momentum label on it.

**3. Composition tolerance: funding share of net PnL must be < 85%.**

Uncapped train ran ~61% funding / 39% price. Validate may drift — §29.2
expects funding to weaken, not strengthen. The tolerance is set on the
side that matters: if funding exceeds 85% of net, the strategy is
functionally pure carry and the momentum component is not contributing,
which is a different mechanism from the one under test. A *low* funding
share is not a failure.

**4. Drawdown limit: a validate max drawdown > 30% is an automatic stop.**

Train maxDD was 27.87% with 2.13 points of headroom (§22.2). The 30% kill
switch is pre-registered elsewhere in this project and applies unchanged. A
breach stops the project regardless of Sharpe — a strategy that would have
been switched off mid-run has not produced a completable path.

**5. Active-days floor: ≥ 80% of window days must be active.**

Grid v2's discredited 1.65 came from 72 active days of 1,342 (5.4%). The
uncapped frozen config runs 1,381 of 1,381 (100%) on train. Below 80%, the
validate Sharpe describes a sliver and **is not interpretable at all** —
neither pass nor fail, and the run must be reported as uninformative rather
than scored.

**6. What failure means: the project returns to research with the holdout
UNSPENT.**

Failure does not end the project and does not trigger the holdout look. The
holdout is one look, ever; spending it to confirm a failure is the worst
available use of it. On failure the frozen config is retired, the holdout
stays sealed, and any future strategy inherits an unspent look. On pass, the
holdout becomes available but is not thereby obligated.

**7. Sidedness: ONE-SIDED at 90%, stated explicitly per §28.3.**

Only "the strategy works" is of interest; a significantly *negative* result
and a merely-not-positive one lead to the same action. All criteria above
are evaluated one-sided at the 90% level, and confidence intervals are
reported two-sided alongside so both readings are visible. This is stated
before the run precisely because §28.3 showed sidedness silently decided the
Stage 3d outcome.

**All seven are evaluated. Criteria 2, 4 and 5 are hard gates — any one of
them failing is a fail regardless of the others.** Criteria 1 and 3 are
scored. Criterion 6 defines the consequence; criterion 7 the method.

### 29.2 Expectations recorded before the run — context, not criteria

These are written so the result is not over-read in either direction. They
are **not** pass/fail conditions and must not be used to excuse a failure
after the fact.

- **Validate cannot confirm; it can only refute.** One year gives
  `t ~ SR x sqrt(1)`, and §28.4 puts the MDE at 1.65 two-sided / 1.28
  one-sided against a strategy whose train Sharpe is 0.796. A "pass" here
  means "not refuted", nothing stronger, and must be reported in those
  words.
- **Expect the funding component to be weaker than train.** Documented carry
  decayed from 2024 onward and the uncapped config is ~61% funding. A
  weaker funding contribution is the *expected* outcome and is not on its
  own a failure — criterion 3 is deliberately one-sided about this.
- **Expect price PnL to be weak.** It was already negative in 2023. Note the
  tension with criterion 2, which is intentional: the criterion asks the
  strategy to do the one thing it had stopped doing, because if it cannot,
  the momentum claim is finished irrespective of how the carry behaves.
- **Both engines enter 2024 degraded.** A poor validate is the *expected*
  outcome, not a surprise. This rule was written knowing that, which is why
  criterion 1 sits at the floor rather than at anything resembling the
  train figure.
- 2022 got slightly worse under the cap (0.05 → −0.04) and the cap is not
  part of this config anyway; no criterion here concerns the cap.

### 29.3 Status

Rule recorded. **Nothing has been run against it.** Validate remains
untouched, the holdout remains sealed, and the trial budget stands at
**7 of 20** with one unallocated trial.

## 30. THE VALIDATE RULE, AMENDED — pre-registered 2026-08-28, nothing run

**§29 is not edited and stands in the record as written.** This section
supersedes it where the two conflict, and says so explicitly at each point.

### 30.0 Why amending is legitimate here

§28.4 put the validate MDE at 1.65 two-sided against a train Sharpe of
0.796. Extending that arithmetic: validate can barely *refute* either. A
validate Sharpe of **−0.50** — a visibly bad year — sits only 1.29 SE below
train (one-sided p ≈ 0.098); 0.00 sits 0.80 SE below (p ≈ 0.213); +0.30 sits
0.50 SE below (p ≈ 0.310). **A rule resting mainly on a minimum Sharpe rests
on the least powerful statistic available** and would return "inconclusive"
for almost any outcome.

The amendment is motivated by a power analysis of the **sample** (§28.4),
not by any outcome. Verified before writing: `trials.jsonl` contains **0
validate rows**, `diagnostics.jsonl` **0 validate records**, no
`holdout_log.json`. **No validate result exists**, so there is nothing to
fit to, and the ordering is checkable in git: this section is committed
before any validate run.

The insight §28.4 implies: conditioning away common market noise is what
made the Stage 3d paired bootstrap tighter than either standalone CI (width
0.56 vs 1.62). The same applies out of sample — so the rule is built on
per-observation and structural quantities, not on a whole-window aggregate.

### 30.1 Train reference, measured (uncapped frozen config, 5bps, +1min)

```
 year     beta  lev med  lev p95   maxDD  active  skip rate  rebal
 2020   +0.007     0.62     1.27  12.11%  100.0%      31.5%    250
 2021   -0.002     0.28     0.47  13.71%  100.0%       8.2%    335
 2022   -0.023     0.48     0.75  18.94%  100.0%       9.0%    332
 2023   +0.045     0.47     0.77  14.18%  100.0%       0.0%    365
```

Whole window: max drawdown **27.87%**, active days **1,381 of 1,381**,
dollar-tilt identity worst deviation **1.39e-16**, funding **59.7% of net**
(price +266.70, fees 127.87, funding +205.30, net +344.13).

**2020's 31.5% skip rate is excluded from the band below** and the ceiling is
set from the mature years (2021–23: 8.2%, 9.0%, 0.0%). 2020 is the warm-up
year in which the universe grew from 29 names, so `universe_too_small`
dominates; 2024 has a mature universe and is not comparable to it. Stating
the exclusion rather than quietly widening the band to 31.5%.

### 30.2 TIER 1 — structural invariants. Any breach is an automatic stop.

These have expected values from **design**, not from market outcome, so a
breach means something is broken regardless of PnL.

| # | Invariant | Train | **Breach if** |
|---|---|---|---|
| T1.1 | realised beta to BTC | [−0.023, +0.045] | **\|beta\| > 0.15** — the project's own pre-registered hedge tolerance (Test 5). Train ran ~3x tighter; the looser design bound is used deliberately so a regime-driven wobble is not scored as a break |
| T1.2 | realised gross leverage, median | [0.28, 0.62] | **outside [0.15, 1.00]** — vol targeting sets this against realised market vol, so it moves with regime; above 1.00 would mean the targeting is behaving differently, not merely meeting a different market |
| T1.3 | dollar-tilt identity `\|sum(w) − k(1−s)\|` | 1.39e-16 | **> 1e-9** — exact by construction (Test 12); any deviation is a code fault |
| T1.4 | max drawdown (whole window) | 27.87% | **> 30%** — the pre-registered kill switch, unchanged |
| T1.5 | active-days fraction | 100% | **< 80%** — carried unchanged from §29 item 5; below it the Sharpe describes a sliver and is **uninterpretable**, reported as such rather than scored |
| T1.6 | skip rate | 8.2/9.0/0.0% (mature) | **> 25%** — well above every mature train year, so only a real malfunction trips it |

### 30.3 TIER 2 — the substantive test. One-sided at 90%.

| # | Quantity | Train 90% CI | **Breach if** |
|---|---|---|---|
| T2.1 | pooled top-30 price PnL per position-day | +0.0424 [+0.0084, +0.0793] | **< 0.0000**. Falling into [0, +0.0084] is *weaker than train but not a breach* — the claim is that the liquid segment still earns, not that it earns as much |
| T2.2 | pooled `101+` price PnL per position-day | −0.1516 [−0.2437, −0.0603] | **> 0.0000**. This is the one finding that survived Stage 3d, so it is the strongest single claim available to test; if the tail stops losing money the dilution attribution does not replicate |
| T2.3 | aggregate price PnL | +266.70 over train | **≤ 0** — carried from §29 item 2, unchanged and still a hard gate. A positive Sharpe carried entirely by funding is the §22.3 carry trade with a momentum label |
| T2.4 | funding share of net PnL | 59.7% | **> 85%** — carried from §29 item 3. One-sided on purpose: a *lower* funding share is not a failure, it is the §25.3 direction |

Validate's own bootstrap CIs are reported for T2.1/T2.2 alongside the point
estimates, and whether each sits inside train's interval — but **the breach
tests above are on the point estimates**, because with one year the CIs will
be wide enough to overlap almost anything.

### 30.4 TIER 3 — headline Sharpe. Reported, never deciding.

Minimum: **≥ 0.30**, the project's existing stop threshold. **Per §28.4 this
cannot decide the outcome alone** — the validate MDE is 1.65 two-sided, so
this figure is a floor for refutation, not evidence of an edge.

**§30 supersedes §29 item 1**, which made Sharpe ≥ 0.30 the gate on spending
the holdout. Sharpe ≥ 0.30 is now **necessary but not sufficient**.

Decided now, per STAGE3F 3.3 — **if Tier 1 and Tier 2 all pass but Sharpe is
below 0.30**: the outcome is recorded as *"not refuted, mechanism intact,
headline weak"*, the **holdout is NOT spent**, and the project returns to
research. A mechanism that survives with a headline below the project's own
stop threshold does not justify spending the single remaining look; keeping
the look is worth more than confirming an ambiguity.

### 30.5 Carried unchanged from §29

- **Item 6 — what failure means:** the project returns to research with the
  **holdout UNSPENT**. Failure does not trigger the look; spending it to
  confirm a failure is the worst available use of it.
- **Item 7 — sidedness: ONE-SIDED at 90%**, per §28.3, with two-sided CIs
  reported alongside so both readings are visible.

Everything in §29 not explicitly superseded above (items 2, 3, 4, 5, 6, 7)
carries forward. Only item 1 is superseded, and §30.4 says so.

### 30.6 Expectations — context, not criteria

Unchanged from §29.2 and repeated because they govern how the result is
read, not whether it passes: validate **cannot confirm, only refute**;
funding is *expected* to be weaker than train (carry decayed from 2024);
price PnL is *expected* to be weak (already negative in 2023, which is
exactly the tension T2.3 is meant to resolve); a poor validate is the
expected outcome, not a surprise.

### 30.7 Status

Rule recorded. **Nothing has been run against it.** Trial budget **7 of
20**; validate untouched; holdout sealed. Running validate requires explicit
user go-ahead and will be trial 8, in a separate session and commit from
this one.

## 31. Stage 4 — USDC funding feasibility: PRE-REGISTERED READING

**Recorded 2026-08-28 BEFORE any USDC funding data was fetched or measured**,
per STAGE4 1.2/7.1.

The hypothesis under check (not under test): majors-only universe → rank
weighting across it → smooth weight changes → USDC-margined perps at 0%
maker. Each piece makes the next possible, and §28.5's objection that USDC
has only 20 liquid names dissolves if the universe is already majors.

**Why funding decides it.** Funding is **59.7% of net PnL** (§30.1), and
§22.3 found it is **81% long-leg and tail-driven** — the +166 on the long
leg comes from the 27% of settlements with negative rates, i.e. crowded
leveraged *shorts* paying. That crowding lives where open interest and
retail leverage are, which is USDT. USDC perps have far lower open interest
and a different participant mix. The plausible failure mode is **fees near
zero, funding income largely gone** — trading 60% of PnL for a fee saving.

Measured per base asset with both a USDT and a USDC perp, over the longest
common history: mean funding rate; **the negative tail** (5th percentile,
1st percentile, and the fraction of settlements below −0.01%, which is the
part that carries the PnL rather than the mean); per-asset correlation of
the two funding series; and the funding-history start date per USDC symbol.

The reading:

| If... | Then |
|---|---|
| USDC negative tail is comparable to USDT — **within ~25% on the sub-−0.01% fraction** | funding survives the switch; the hypothesis stays alive |
| USDC negative tail is **materially thinner** | the switch trades ~60% of PnL for a fee saving → **hypothesis likely dead in this form; record and stop** |
| common history is **under ~2 years** | **not answerable on available data**; say so and do not extrapolate from USDT |

Thresholds will not be adjusted after seeing the numbers. Comparison is on
matched base assets over their common window only — never USDC's window
against USDT's full history, which would confound the switch with the
regime.

**Scope limits fixed now:** no backtest, no configuration change, no margin
switch, **no USDC kline ingestion** (that is backfill for a strategy not
approved). USDC *funding* is fetched because §1 requires it, and into a
separate file so the frozen `xsmom.db` is untouched. Budget stays **7 of
20**; validate untouched; holdout sealed.

### 31.1 THE DECISIVE CHECK — BRANCH ONE. USDC funding survives the switch.

38 USDC perpetuals, all 38 with a USDT counterpart. 111,928 USDC funding
rows fetched into a **separate** `usdc_funding.db`; `xsmom.db` untouched and
**no USDC klines ingested**. Compared on matched base assets over their
**common window only**.

```
pooled over 38 matched base assets
  fraction of settlements below -0.01% (the tail that carries the PnL):
    USDT mean 13.22%  |  USDC mean 11.31%   ->  USDC/USDT = 0.86
  assets where the USDC tail is thinner: 26 of 38
  median common history: 836 days (2.29 years)
```

Ratio **0.86** is inside the pre-registered ±25% band, and 2.29 years clears
the ~2-year bar, so **branch one**: funding survives the switch and the
hypothesis stays alive. Per-asset correlations run 0.21–0.99 (median ~0.80),
i.e. the two funding series are largely the same signal.

**Three things that weaken this more than the headline ratio suggests, and
they belong next to it:**

1. **26 of 38 assets have a thinner USDC tail.** The pooled 0.86 is a mean
   over assets where the *direction* is consistently against USDC — it is
   small, not absent. On the majors specifically: XRP 5.3% → 2.3%, ADA
   11.5% → 3.2%, NEAR 8.5% → 3.6%, AVAX 14.7% → 9.3%. Several of the names
   most likely to be in a majors universe show the largest thinning.
2. **The comparison window is 2024-2026 and does not overlap train
   (2020-2023).** USDC perps did not exist then. The test is internally
   valid — matched pairs, identical window — and it answers "is USDC funding
   like USDT funding?", which is the right question. It cannot answer what
   USDC funding would have been during the period the strategy was fitted on,
   and no extrapolation is offered.
3. BTC and ETH — the two largest intended positions — sit at 0.4%/1.1%
   sub-threshold fractions on both quotes. The funding tail lives in the
   alts, so a majors-only universe already has less of it than the train
   book did, independently of the margin asset.

## 32. Stage 4 — the rest of the feasibility chain

### 32.1 (§2) Spread and depth — the fee saving is not eaten

Live order-book snapshot across the 16 largest USDC pairs. **A snapshot, not
a history**, and labelled as such.

```
median USDT spread 0.90 bps  |  median USDC spread 1.91 bps
median USDC-minus-USDT spread: +0.00 bps
USDC wider on 8 of 16 | wider by MORE than 4 bps on 1 (UNI, +4.38)
median depth within 10bps: USDT $288,695 | USDC $61,957  (ratio 0.21)
```

The §2 bar was "if USDC spreads are wider by more than ~4bps, the taker
route saves nothing". The median difference is **0.00 bps** — on the majors
the two books quote the same spread — so the 4bps taker saving survives.
Depth is ~5x thinner on USDC, which is **irrelevant at $400** (positions are
$5–25 against $62k of depth within 10bps) and would become the binding
constraint long before capital reached six figures.

### 32.2 (§3) Universe — the intended majors are all there

20 of 38 USDC pairs clear $5M/24h. BTC, ETH, SOL, XRP all present and
liquid. `MIN_NOTIONAL` is $5 for 18 of the 20, $20 for ETH, $50 for BTC —
the same structure as USDT, so **BTC is untradeable at $400 on either
quote** and that constraint does not change.

Overlap with the §23 top-30 bucket: **138 distinct symbols held liquidity
rank ≤30 at some point in train; 25 of them (18%) have a USDC perp today.**

That 18% is the honest number but it is largely answering the wrong
question, and the distinction matters:

- The union over four years counts churn in the 2020-23 alt universe —
  names like AGIX, ALICE, ANKR that were briefly top-30 and are now
  irrelevant. It is not the overlap on any given day.
- More to the point, §23's bucket is a **rank** bucket, not a name list. A
  USDC universe of 20 liquid names *is* a top-liquidity universe by
  construction. The fee advantage therefore applies to the right *segment*
  even though it applies to few of the specific historical names.

What the 18% does establish is that a USDC universe is **not** a superset of
the segment that tested significant — it is a much narrower, more
concentrated slice of it.

### 32.3 (§4, §4.1) Rank weighting — the floor turns it into truncation

**The weight function, stated as a formula** (§4.1 required this; "30% top-3
/ 40% middle / 30% bottom-3" is ambiguous and would break dollar-neutrality
if the middle names were not split by rank):

```
  z_i = (N+1)/2 - i                      centred momentum rank, i = 1..N
  K   = {1..k} u {N-k+1..N}              symmetric kept set
  w_i = g * z_i / sum_{j in K} |z_j|     for i in K,  else 0
```

`z` sums to zero over any symmetric `K`, so **the book is dollar-neutral by
construction**; `w` is monotonic non-increasing in rank; and `k` is the
single free integer, chosen as the largest `k <= floor(N/2)` for which every
kept position clears `MIN_NOTIONAL`. It degenerates to the current strategy
when the floor drives `k` to 5.

**Pure linear across the whole universe is infeasible at $400** (g = 0.444
median realised gross):

```
   N   min |w|   min notional @$400   # under $5   min capital for $5
  15   0.01786          $3.17              2            $630
  20   0.00500          $0.89              6          $2,250
  30   0.00222          $0.40             12          $5,063
```

But with symmetric truncation it is feasible, and holds **more** names than
the current book:

```
   N   feasible k   names held   smallest $   largest $   ratio
  15        6           12          6.58        23.02      3.5x
  20        7           14          6.83        18.54      2.7x
  30        9           18          6.11        13.63      2.2x
  38       11           22          5.08        11.06      2.2x
```

**The structural finding: `MIN_NOTIONAL` converts rank weighting into
truncation.** At $400 you cannot spread weight across a whole universe —
the floor forces you back to trading the extremes, and `k` is the only
question. The hypothesis's piece (2), "rank-weighted allocation across the
whole universe", is **not available at this capital**; what is available is
truncation at 12–22 names instead of the current 10. That is more breadth,
which is the one lever §A4 identified, but it is not the stated idea.

### 32.4 (§5) Turnover — most of it is boundary-crossing

From the existing train run, no simulation:

```
  boundary-crossing (entry/exit) : $172,709   67.9%
  adjustment within the held set : $ 81,767   32.1%
```

**68% is the upper bound on what smoother weighting could save.** The §5
worry — that most turnover is already adjustment, making the argument
weaker than it looks — does **not** materialise. This is the most favourable
result in Stage 4 for the hypothesis: fee drag runs 27–48% of gross PnL, and
two-thirds of the turnover generating it comes from names crossing the rank-5
boundary, which smooth weighting is precisely what would soften.

### 32.5 (§6) What none of this establishes

Recorded so the chain is not oversold:

- **Better weighting of a decaying signal still decays.** Train price PnL ran
  +163 → +110 → +30 → −37. Nothing in Stage 4 addresses that, and it is the
  central problem.
- **Truncation is optimal if only the extremes carry signal.** Spreading
  weight across ranks assumes the payoff is roughly linear in rank. There is
  **no data on middle-ranked names because they have never been held** — and
  §24.1 found the one middle-ish bucket that was measured (`31-100`) beat the
  top bucket in 2020 and lost in 2023, i.e. no stable linearity. If momentum
  lives only in the tails, diluting into the middle costs.
- **No maker-mode result is reportable until a fill-probability model
  exists** (Stage 2e §4, unchanged). Rank weighting makes maker *plausible*
  — an unfilled post-only order leaves you slightly mis-weighted rather than
  missing a position — but plausible is not proven, and post-only fill rates
  must be measured in paper trading first.
- **Testing this needs train, validate and holdout against one remaining
  trial.** It requires a deliberate budget expansion, logged with date and
  reason and the Deflated Sharpe recomputed at the higher count. No such
  expansion is made here.

### 32.6 Status

All six checks done. Branch one on the decisive one. Nothing backtested,
no configuration changed, no margin asset switched, **no USDC klines
ingested**. USDC funding lives in `usdc_funding.db`, separate from the frozen
store. Trial budget **7 of 20**; validate untouched; holdout sealed.

## 33. Stage 5 — majors reconstruction: PRE-REGISTERED READING and BUDGET EXPANSION

**Recorded 2026-08-28 BEFORE any Stage 5 run**, per STAGE5 5.1/8.1.

### 33.1 TRIAL BUDGET EXPANDED: 20 -> 25

**Date:** 2026-08-28. **Instruction:** `STAGE5_MAJORS.md` §6, supplied by the
user. **Reason:** the majors/venue question is a distinct strategy-family
question the original 20-trial budget did not anticipate; it needs B1 and B2
(2 trials) and the remaining 1 would leave nothing for validate.

**New denominator stated before running: 25.** After this stage, **9 of 25**
spent. This is a recorded decision, not silent drift.

**Deflated Sharpe recomputed for every prior reported result at the new
count.** The cross-trial variance is held at the value measured from the 7
trials actually run (0.000301) — it is *not* padded with synthetic trials,
because padding with the mean would shrink the variance and make the DSR
rise, which is backwards. Only the expected-maximum term moves, from a
daily 0.02407 at n=7 to 0.03466 at n=25:

```
 config                       sharpe   DSR@7   DSR@25
 lb14/skip0 cap100 (trial 7)   1.178   0.919    0.842
 lb28/skip0 uncapped           0.971   0.840    0.726
 lb 7/skip0 uncapped           0.965   0.837    0.722
 lb14/skip0 uncapped (FROZEN)  0.908   0.808    0.684
 lb28/skip2 uncapped           0.897   0.802    0.676
 lb 7/skip2 uncapped           0.483   0.518    0.364
 lb14/skip2 uncapped           0.422   0.470    0.320
```

Every figure falls, as widening a search space must. **The frozen config's
DSR at the honest new denominator is 0.684** (at 5bps it is lower still;
these are the 0bps rows, which §18.1 already marks as a bound rather than a
headline).

### 33.2 The design, and the survivorship trap it must avoid

Three configs on train 2020–2023: **A** = frozen (full USDT universe, USDT
fees), **B1** = top-15 majors PIT, USDT fees, **B2** = the identical B1
position series re-costed at USDC fees. A→B1 isolates the universe; B1→B2
isolates the venue.

**The trap (STAGE5 §1):** the USDC name list is defined by what has a USDC
perp *today*. Dropping that list into 2020 would exclude every coin that
pumped and died before 2024 — pure hindsight, and exactly what the
point-in-time store exists to prevent.

**How it is prevented here:** B's universe is `max_liquidity_rank = 15`,
which selects the top 15 by **median quote volume over the trailing 30 bars,
computed through `PITView` at each rebalance date** from the full ingested
symbol list. No name list appears anywhere in the selection path. Delisted
symbols are therefore selected on the dates they traded. "Has a USDC pair
today" is used **only** in §32.2 to confirm the thing is buildable live, and
touches no historical date. A test asserts a symbol that delisted mid-train
is still selected on its live dates, which a today's-names filter could not
do.

### 33.3 The reading, fixed in advance

| B1 − A (the universe effect, paired 90% CI) | Reading |
|---|---|
| CI **entirely above zero** | majors-only helps out of the noise → strong basis to validate B |
| CI **straddles zero**, point estimate positive | consistent with helping, **not established** — the honest most-likely outcome given §28.4's MDE of 0.28 on this very comparison |
| CI **entirely below zero** | majors-only *hurts* — concentration removed more signal than tail noise → do not validate B |

Separately, and not gated on the CI: **does B's per-year price PnL avoid A's
+163 → +110 → +30 → −37 collapse?** If B's price PnL stays positive through
2023 where A's went negative, that is the mechanism working even if the
paired interval is wide.

Sidedness: intervals are **two-sided at 90%**, and the one-sided reading is
reported alongside, per §28.3.

Thresholds will not be adjusted after seeing intervals.

### 33.4 Fixed choices, recorded so they cannot drift

- **k = 5 (N = 10), unchanged.** §24.1 showed ranks 6–11 flip sign by year,
  so widening k adds a middle-rank bet deliberately not being made. If a
  top-15 universe cannot support k=5 at the `MIN_NOTIONAL` floor, that is
  **reported and the stage stops** — k is not silently shrunk.
- **B1 and B2 are one position series costed twice**, not two backtests.
  B2 holds the unit positions of B1 fixed and re-costs the fee line, so no
  ordering or path difference can contaminate the fee comparison. The
  simplification this makes — in reality different fees would change equity
  and hence subsequent sizing — is stated wherever B2 appears.
- **Fees:** B1 at USDT taker 0.0500%; B2 at USDC taker 0.0360% (Regular User
  0.0400% with the 10% BNB discount — the discount is an assumption and is
  labelled as one). **Maker is not used**: Stage 2e §4 stands, no maker
  figure is reportable without a fill-probability model.
- **Funding is a USDT proxy.** USDC funding did not exist in 2020–2023, so B
  uses USDT funding for those names. §31.1 measured the USDC tail at 0.86x
  USDT, so the proxy is if anything **generous** to funding. Labelled a proxy
  everywhere; never presented as USDC funding.
- **The reconstruction is generous on two axes** and a marginal B result is
  therefore an **upper bound**: USDC-list membership still correlates with
  having survived to 2024 even though selection is PIT, and the funding proxy
  is slightly rich.

Validate and holdout remain untouched.

## 34. Stage 5 result — BRANCH TWO on the universe, but the composition moved

Trials 8 and 9 logged **before** execution. **Budget 9 of 25.** Validate and
holdout untouched.

### 34.1 The three configs on train

```
                                       sharpe   90% CI          ann_ret   vol    maxDD   fee drag
 A   frozen, full USDT universe         0.796  [-0.019, +1.572]  17.83%  24.21%  27.87%   47.95%
 B1  top-15 PIT majors, USDT fees       1.114  [+0.358, +1.897]  20.88%  18.57%  29.73%   27.71%
 B2  same positions, USDC fees          1.195  [+0.437, +1.976]  22.22%  18.17%  27.47%   19.95%

                    price      fees    funding      net   funding as % of net
 A                +266.70    127.87   +205.30   +344.13        59.7%
 B1               +449.26    124.49    +94.86   +419.63        22.6%
 B2               +449.26     89.63    +94.86   +454.49        20.9%
```

Deflated Sharpe at the expanded denominator (variance held at the 7 real
trials): A 0.603, B1 0.810, B2 0.850 at n=25 (0.715 / 0.882 / 0.910 at n=9).

### 34.2 The pre-registered branch: TWO

```
 B1 - A   +0.3175   90% CI [-0.5964, +1.2403]   straddles zero
 B2 - B1  +0.0808   90% CI [+0.0657, +0.0955]   ABOVE zero
 B2 - A   +0.3984   90% CI [-0.4807, +1.3190]   straddles zero
```

**B1 − A straddles zero with a positive point estimate → branch two:
consistent with helping, NOT established.** Exactly the outcome §33.3 called
the honest most-likely one, and consistent with §28.4: the paired MDE on
train is ~0.28 two-sided and this effect is +0.32 with a much wider interval
than the Stage 3d comparison had, because A and B hold *different books*
rather than differing on a sliver of days.

**B2 − B1 is above zero, but that is arithmetic, not evidence.** The two
series differ by a deterministic fee scaling on identical positions, so
there is no independent noise for the bootstrap to find; the tight interval
[+0.0657, +0.0955] measures the fee saving's size, not its significance.
§5 predicted "small and positive by construction" and that is what it is:
**+0.08 Sharpe for 1.4bps of taker fee**, worth $34.86 on $249k of turnover.

### 34.3 The non-gated question — the collapse IS avoided, but 2022 breaks

```
 price PnL by year   2020    2021    2022    2023
 A  (full universe)  +163    +110     +30     -37
 B1 (top-15 majors)  +178    +175     -40    +137
```

**B1's 2023 price PnL is +137 against A's −37.** The monotonic collapse that
motivated all of Stage 3b–3d does not happen in the majors universe — this
is the clearest evidence yet for the dilution hypothesis, and it arrives
from a completely different direction than the rank cap did.

**But B1's 2022 is much worse: −40 price PnL and Sharpe −0.86, against A's
+30 and +0.05.** The majors book has no tail to hide in during the
bull-to-bear transition. §22.2 identified that transition as this strategy's
structural vulnerability, and concentrating the universe *sharpens* it.

So majors-only is not uniformly better: it is better in three years out of
four and materially worse in the one that already worried us.

### 34.4 Two things that need flagging, not burying

1. **B1's max drawdown is 29.73%, against a 30% kill switch.** That is 0.27
   points of headroom — under §30's Tier 1 it would very nearly be an
   automatic stop, and it is worse than A's 27.87%. B2's 27.47% is lower only
   because lower fees lift the equity path, not because the risk is
   different. **A validate or holdout run of B would be one bad week from
   being switched off**, and that is a stronger objection to B than anything
   in the Sharpe comparison.
2. **The composition moved exactly as §25.3 hoped and the cap failed to
   deliver:** funding falls from **59.7% to 22.6%** of net while price PnL
   rises from +267 to +449. B is a momentum strategy in a way A is not. That
   matters because the documented carry decay (negative in 2025) threatens A
   far more than B — but it also means B forfeits the carry cushion that
   carried A through 2023.

### 34.5 A reporting bug found and fixed before these numbers were used

The first run of `tools/stage5_majors.py` computed Sharpe over the **full**
window including ~200 pre-fill zero days, reporting A at **0.744** where
every prior figure in this project uses the strategy window (first fill
onward) and gives **0.796**. The paired differences were unaffected — both
legs shared the basis — but the absolute levels were not comparable to
anything previously reported.

Fixed so `ann_return`, `max_drawdown` and Sharpe all use the same
strategy-window slice, and the numbers above are the corrected ones. The
first set was never reported as a result.

### 34.6 What this does and does not establish

- It does **not** establish that majors-only helps: the pre-registered
  interval straddles zero and §33.3 fixed that reading in advance.
- It **does** show the price-PnL collapse is universe-dependent rather than
  a property of the signal, which is the single most useful thing learned
  since Stage 3b.
- The reconstruction is **generous on two axes** (§33.4): USDC-list
  membership still correlates with surviving to 2024 even though selection
  is point-in-time, and the funding proxy is USDT's, measured at 1.16x the
  USDC tail. A marginal B result should be read as an **upper bound**.
- **Funding for B is a USDT proxy throughout.** USDC funding did not exist
  in 2020–23. Nothing above is USDC funding.
- **No maker figure is reported.** Stage 2e §4 stands: USDC's 0% maker is a
  live-execution question answered by measuring post-only fill rates in
  paper trading, not by this backtest.
- One year of validate still cannot **confirm** whichever config wins
  (MDE ~1.65). It can refute. Unchanged.

### 34.7 Status

Budget **9 of 25**. Validate untouched, holdout sealed. The frozen config of
§19.5 is unchanged — B is a candidate, not a new freeze, and nothing in
§30's validate rule has been rewritten to accommodate it.

## 35. Stage 5a — un-truncated drawdown path: PRE-REGISTERED READING

**Recorded 2026-08-28 BEFORE looking at B1's drawdown path**, per STAGE5A
3/5.1. Train-only diagnostic. **No trial; budget stays 9 of 25.** Validate
and holdout untouched.

### 35.1 No flag was needed — the path was never truncated

STAGE5A anticipated a kill-switch-disabled flag and set a hard rule around
it (never in `Config` defaults, never in a logged trial, never on a
validate/holdout path). **That flag was not necessary and does not exist.**

The 30% kill switch has never been implemented in the engine. It is a
pre-registered *operational* criterion — §30 Tier 1 scores a validate run
against it, and §22.2 asked whether it *would* have fired — but the backtest
never truncated on it. The engine's only early exit is bankruptcy
(`equity <= 0`), which B1 never approaches. B1's recorded equity path is
therefore **already the un-truncated path**, and this diagnostic is pure
analysis of a series that already exists.

That is the strongest available compliance with STAGE5A §0: the flag cannot
leak into validate or holdout because it was never created.

### 35.2 The reading, fixed before looking

The question: B1's max drawdown is 29.73% against a 30% switch (§34.4). If
the strategy had not been stopped there, would it have recovered or kept
falling?

| If, after the −30% point... | Then |
|---|---|
| equity recovers within **~1–3 months** | the 30% switch is **too tight** — it would stop at the bottom and forfeit the rebound. Argues for a **lower vol target** (staying under the switch), not a looser switch |
| equity keeps falling toward **−40% or worse** | the switch is **correctly protective**. B needs the vol reduction to survive, not switch removal |
| recovers but **slowly (6+ months underwater)** | ambiguous — survivable but painful. A judgement call for the user, not an automatic reading |

Not adjusted after looking.

### 35.3 What this cannot tell anyone

- It is **in-sample on train**. The worst real drawdown is out-of-sample and
  unseen; a train path that recovers is not a promise the holdout path will.
- It says **nothing about whether B's edge is real** — that is the validate
  question, untouched.
- **Removing the kill switch is not a strategy improvement** and must not be
  read as one. The takeaway concerns the **vol target and the switch level**,
  not running without a switch. The switch remains in the strategy
  definition for every future run.

### 35.4 Result — the switch never fired, and the drawdown never healed

```
1,382 days 2020-03-20 -> 2023-12-31   equity $400.00 -> $819.63   max DD 29.73%

deepest drawdown 29.73%
  peak     2022-03-25   $828.10
  trough   2023-06-25   $581.90     457 days from peak
  recovery DID NOT RECOVER within train -- still 1.02% below the peak at
           2023-12-31 ($819.63 vs $828.10)
  time underwater 646 days (21.2 months), still counting at train end

excursions past -25%: TWELVE, from 2022-11-14 to 2023-09-11
the 30% switch: NEVER REACHED -- worst 29.73%, 0.27 points of headroom
```

**The pre-registered table does not cleanly apply, and I am not going to
force it to.** All three branches in §35.2 are conditioned on "after the
−30% point". There is no −30% point: the switch never fired on train, so
there is no post-switch path to inspect. The reading assumed a fact that
turned out to be false, which is a limitation of the reading, not a result.

**Closest branch is three (ambiguous — survivable but painful), and it
understates it.** The drawdown did *not* deepen toward −40%, so branch two
is wrong. It did *not* recover in 1–3 months, so branch one is wrong. It ran
**21.2 months underwater and had still not recovered when train ended**,
finishing 1.02% short of the 2022-03 peak — worse than branch three's "6+
months" contemplates.

For reference rather than as a branch reading, from the worst point
(2023-06-25): +30d **+5.76%**, +60d **+2.00%**, +90d **+15.00%**. The
recovery had begun but was slow and non-monotonic — the +60d figure is
barely above the trough.

### 35.5 The observation that matters more than the branch

**Whether the kill switch fires depends on the fee schedule.** Same
positions, same days:

```
 B1 (USDT taker 0.0500%)   max DD 29.73%   -- 0.27 points from the switch
 B2 (USDC taker 0.0360%)   max DD 27.47%   -- 2.53 points from the switch
```

A **1.4 bps** difference in taker fee moves max drawdown by **2.26 points**
and decides whether the strategy would have been shut down. That is not a
comfortable place to sit: the operational survival of the config depends on
a cost assumption that §28.5 flagged as resting on a 10% BNB discount and,
before that, on a single synthetic testnet fill.

Combined with **twelve separate excursions past −25%** between 2022-11 and
2023-09, the honest characterisation of B1 is: *a strategy that spent the
last third of train continuously within a few points of its own stop, and
that a modestly worse cost assumption would have stopped.*

### 35.6 What this changes

- **Not** an argument for removing or loosening the switch (§35.3 stands, and
  STAGE5A §4 forbids reading it that way).
- It **is** an argument that B1's risk sits too close to its own limit at a
  20% vol target. The lever §35.2 identified for branch one — *lower the vol
  target rather than loosen the switch* — applies here for a different
  reason: not to capture a forfeited rebound, but to buy headroom against a
  stop that a fee assumption can trip.
- Any such change is a **new configuration and costs a trial**. Nothing is
  changed here. The frozen config remains §19.5; B remains a candidate.
- This is **in-sample on train**. The worst real drawdown is out-of-sample
  and unseen.

Budget **9 of 25**, unchanged. Validate untouched, holdout sealed.

## 36. Stage 5b — free attribution on B: PRE-REGISTERED READINGS

**Recorded 2026-08-28 BEFORE computing any of it**, per STAGE5B 7.1. Zero
trials: every item is attribution or resampling of the **existing** B1 run.
Budget stays 9 of 25; validate and holdout untouched.

### 36.1 (§1) Per-year intervals — the reading

§34.3 gave B's per-year price PnL as point estimates (+178, +175, −40, +137)
with no intervals. Adding block-bootstrap 90% CIs per year, same method as
§24.1 with the block length recomputed for B's own series.

**The question is not whether 2022 is negative — it is.** It is whether the
three *positive* years are individually distinguishable from zero, or
whether B's whole edge rests on one or two years the way A's rested on 2023
funding. If only one year clears its interval, B is the same
single-window story A was, wearing a different universe.

### 36.2 (§2) Drift decomposition on B — the one that can overturn Stage 5

A's decomposition (§19.4 / §22) attributed **~44%** of Sharpe to persistent
cross-sectional drift against an **~18%** synthetic zero-drift floor
(`TEST_NOTES` obs. 2). **B has never had this run.** If B's apparent
momentum is substantially the same finite-sample drift artifact, then B is
not the cleaner momentum strategy §34 made it look like, and the validate
decision changes.

Method: `xsmom_demeaned.db` (per-symbol full-sample mean log return removed
from every price column including the 1m execution bars) with the B1 config.
Volumes are untouched in that build, so the point-in-time liquidity ranking
— and therefore B's top-15 universe — is identical between the two stores
(Test 13 asserts universe identity). The comparison isolates drift, not
universe.

| If B's drift fraction... | Then |
|---|---|
| comparable to A's **~44%** | B's "cleaner momentum" is partly the same drift artifact → mechanism story weakens; validate only with that caveat explicit |
| materially **below** A's | B genuinely harvests more trend and less drift — the majors concentration removed drift-prone names → strengthens B |
| near or below the **~18% floor** | B's edge is essentially all trend-continuation → strongest possible read for B |

**DIAGNOSTIC ONLY — uses full-sample means, not runnable live.** Logged to
`diagnostics.jsonl`, never `trials.jsonl`.

Readings not adjusted after the numbers. No config is chosen in this stage —
§5 assembles an evidence table, it does not decide.

### 36.3 (§1) Per-year intervals — B does not rest on one year, but 2023 is not significant

```
 year    price$          90% CI      excl0 | funding$       90% CI      excl0 | sharpe      90% CI
 2020   +177.95  [ +28.50, +321.60]   yes  |  +19.05  [+11.13, +27.69]   yes  | +2.27  [+0.49, +4.08]
 2021   +174.53  [ +27.35, +334.67]   yes  |   +7.60  [ +1.18, +13.94]   yes  | +1.40  [+0.09, +2.66]
 2022    -40.35  [-215.89, +138.39]   no   |  -21.57  [-47.34,  -2.83]   yes  | -0.83  [-2.38, +0.62]
 2023   +137.12  [ -49.07, +316.72]   no   |  +89.78  [+45.26, +144.48]  yes  | +1.53  [-0.20, +3.15]
```

**Two of the three positive years (2020, 2021) have price-PnL intervals
excluding zero.** So B's edge does *not* rest on a single window the way A's
rested on 2023 funding — that part of §36.1's worry is answered favourably.

**But 2023's price PnL is NOT individually significant** ([−49.07,
+316.72]). 2023 is the year that produced §34.3's headline — +137 against
A's −37, "the collapse is avoided" — and on its own it cannot be
distinguished from zero. The collapse-avoidance finding is real as a point
estimate and weak as a single-year claim; what carries B statistically is
2020–2021, the two years furthest from the present.

2022's price PnL is also not significant (its CI spans zero), so B's bad
year is no more established than its best one.

### 36.4 (§2) Drift on B — the decisive check, and it went against B

```
  Sharpe (real)     : +1.114
  Sharpe (demeaned) : +0.662
  drift component   : +0.452   =  41% of total
  A: 44%          synthetic zero-drift floor: ~18%
```

**41% against A's 44% is "comparable" — branch one of §36.2 fires: B's
"cleaner momentum" is partly the same drift artifact, the mechanism story
weakens, and any validate of B must carry this caveat explicitly.**

This is the result that could have overturned Stage 5, and it substantially
does qualify it. §34 read B as the more genuinely momentum-driven config
because funding fell from 60% to 23% of net. That is still true of the
*funding* split — but the price leg B relies on is drift-contaminated to
essentially the same degree as A's. Concentrating into majors changed
**which** return source dominates without changing **how much of the price
leg is trend rather than persistent drift**.

Per year:

```
 year  SR real  SR demean   drift   % of total
 2020    +2.27      +1.21   +1.06        47%
 2021    +1.40      +1.49   -0.09        -7%
 2022    -0.83      -1.06   +0.24       -29%   (ratio meaningless at SR<0)
 2023    +1.53      +0.96   +0.57        37%
```

Worth noting against §36.3: **2021 — one of the two statistically solid
years — has essentially no drift (−7%)**, while 2020 and 2023 carry 47% and
37%. So the drift contamination and the statistical significance sit in
partly different years, which is the most favourable reading available, and
it is not enough to move the branch.

### 36.5 (§3) Turnover — the fee-drag advantage is a denominator effect

```
      annualised   boundary-crossing   adjustment   fee drag
 A        109.3x              67.9%        32.1%     47.95%
 B        104.0x              56.8%        43.2%     27.71%
```

The §3 hypothesis — that 15 names should cross the rank boundary less often
than 800 — is **directionally right but small**: the crossing share falls
67.9% → 56.8%, yet **total turnover barely moves (109.3x → 104.0x)**.

So B's much better fee drag (47.95% → 27.71%) is **not** because it trades
less. It is because it earns more: gross price PnL +449 against A's +267,
against near-identical turnover. **That is a denominator effect, and it
should not be entered in the ledger as a trading-cost advantage** — if B's
edge does not survive out of sample, its fee drag reverts to A's.

### 36.6 (§4) Who is paying, for B — re-derived, not inherited

B's composition (price +449.26, fees 124.49, funding +94.86, net +419.63 —
price is 107% of net because fees exceed funding) is a different animal from
A's and needs its own answer.

- **Price leg (dominant).** Long +604.10 against short −154.84: the profit
  is made being long the strongest majors, and the short leg loses money on
  price while hedging beta. The payer story is the conventional momentum one
  — late entrants buying established moves in the most liquid, most
  crowded-with-attention names, and leveraged holders liquidated into
  weakness on the short side. In majors this story is *more* credible than
  in the alt tail (there is a genuine crowd, with depth to be late into) and
  *more* exposed to the literature's finding that momentum crowds out as
  capital arrives. **This is the leg that decays with capital.**
- **Funding leg (23%).** Long +46.63, short +48.23 — **balanced across
  legs**, where A's was 81% long-leg and tail-driven (§22.3). 14.9% of B's
  settlements sit below −0.01% against A's 13.2%, so the tail is not thinner
  — it is simply not concentrated on one side. B collects ordinary two-sided
  carry rather than A's crowded-short squeeze premium. That is a *steadier*
  mechanism but a smaller one, and it is the piece the documented 2024–25
  carry decay threatens.
- **Decay exposure.** A's dominant source (long-leg funding tail) and B's
  dominant source (majors momentum) both decay, for different reasons — A's
  with the carry regime, B's with capital. Neither is a mechanism one would
  expect to persist indefinitely, and B's is the one the published
  literature is most explicit about.

### 36.7 (§5) THE B-vs-A LEDGER — evidence, not a decision

| Dimension | A (frozen §19.5) | B (top-15 majors) |
|---|---|---|
| Train Sharpe (90% CI) | 0.796 [−0.02, +1.57] | 1.114 [+0.36, +1.90] |
| B−A paired | — | **+0.32 [−0.60, +1.24] straddles zero** |
| Price / funding split | 60 / 40 | 77 / 23 |
| 2023 price PnL | −37 | **+137, CI [−49, +317] — not significant** |
| 2022 price PnL | +30 | −40, CI [−216, +138] — not significant |
| Years with significant price PnL | — | **2 of 3 positive (2020, 2021)** |
| Max drawdown | 27.87% | 29.73% (USDT) / 27.47% (USDC) |
| Time underwater (worst) | — | **21.2 months, unrecovered at train end** |
| **Drift fraction** | ~44% | **41% — comparable, not better** |
| **Turnover** | 109.3x, 67.9% crossing | **104.0x, 56.8% crossing** |
| Fee drag | 47.95% | 27.71% — but a *denominator* effect |
| Carry-decay exposure | high | lower |
| Capital-crowding exposure | lower | **higher (momentum in majors)** |
| Kill-switch headroom | 2.13 pts | **0.27 pts (USDT) — parked** |

**No config is chosen here.** Two cells that were expected to favour B did
not: drift is comparable rather than better, and the turnover advantage is
marginal with the fee-drag gap explained by higher gross profit instead.

### 36.8 What none of this establishes

All of it is in-sample on train. None of it confirms B's edge is real — only
validate can refute that, and only weakly (MDE ~1.65, §28.4). Drift,
turnover and payer analysis characterise **what B is**, not **whether it
works out of sample**. The parked vol/kill-switch question (§35.6) is not
answered here and still gates live deployment.

Budget **9 of 25**. Validate untouched, holdout sealed, frozen config
unchanged.

## 37. THE VALIDATE RULE FOR B — pre-registered 2026-08-28, before the run

**Config under test:** B — top-15 point-in-time majors, `lookback=14`,
`skip=0`, N=10, k=5, 20% vol target, 3x cap, beta-neutral, +1min fill, 5bps
slippage, $400. Both fee schedules (USDT 0.0500%, USDC 0.0360%), reported
together. **Window: 2024 only.** Cost: **one trial, budget 9 -> 10 of 25.**

**Relationship to §30.** §30 is the validate rule written for config **A**
(the uncapped frozen config) and it stands unmodified in the record. This
stage validates **B** instead, so §37 governs this run. §30 is not deleted,
not edited, and would still apply if A were ever validated. Saying so
explicitly rather than silently swapping rules.

### 37.1 The drift adjustment — the number that must not be forgotten

§36.4 measured **41% of B's train Sharpe as drift**, which does not repeat
out of sample. So even if B's real momentum holds *perfectly* into 2024:

```
  expected validate Sharpe  =  1.114 x (1 - 0.41)  ~  0.66     before any decay
```

**A validate Sharpe of 0.5-0.7 is therefore CONSISTENT WITH SUCCESS, not
failure.** A threshold demanding ~1.0 would reject a working strategy. This
is baked into the gates below and is not to be quietly dropped when the
number arrives.

### 37.2 Tier 1 — hard gates. Any one failing = refuted.

| # | Test | REFUTED if | Why |
|---|---|---|---|
| **G1** | price PnL sign | 2024 price PnL **< 0** | B is 77% price. Negative price PnL means the momentum leg is gone, drift or no drift. The single most important gate |
| **G2** | drawdown | max DD **> 30%** on the **USDT-fee** run | Breaches the pre-registered kill switch. **Not a failed test — the parked vol question answering itself:** B at 20% vol is too fragile to run. Decisive, not noise |
| **G3** | Sharpe floor | Sharpe **< 0.30** at **USDC** fees | The project's standing stop threshold. Below it, even "not refuted" does not justify the holdout |

### 37.3 Tier 2 — mechanism checks. Reported; they inform, they do not auto-refute.

- **Structural invariants** against §30.1's train ranges: realised beta
  within **±0.15**; realised gross leverage in a sane band; dollar-tilt
  identity to **1e-9**; active-days fraction **>= 80%**. A breach means the
  harness behaved differently out of sample — investigate before trusting
  anything else.
- **Price/funding split.** Train B was 77/23. **If 2024 flips to
  funding-carried, B has become A** — noted explicitly if so.
- **Drift check on 2024.** Demeaned decomposition on the 2024 result. If the
  2024 drift fraction is far above train's 41%, the "momentum" that survived
  is mostly artifact — a caveat on any positive read. Diagnostic, not a trial.

### 37.4 The reading

| Outcome | Meaning |
|---|---|
| all Tier 1 pass, Sharpe **0.5-0.7**, price-driven | **best realistic outcome** — consistent with momentum surviving; justifies the holdout, subject to §37.5 |
| all Tier 1 pass, Sharpe **> 0.7** | stronger than the drift-adjusted expectation; genuinely encouraging, still **not confirmation** |
| **G1 fails** (price PnL < 0) | momentum did not survive. **B refuted.** Do not proceed to holdout |
| **G2 fails** (DD > 30%) | B at 20% vol is unrunnable. The vol question is answered: B needs lower vol, which is a **new config and a new validate** — the holdout stays sealed |
| **G3 fails** (Sharpe < 0.3) | not refuted, but too weak to spend the holdout on |

### 37.5 This stage ends at the report

Whatever 2024 shows, **the holdout decision is deferred to the user** and is
not taken in this session. Two reasons, recorded now so they are not
rationalised away later:

1. Even the best outcome here is **"not refuted"** (MDE ~1.65 on one year,
   §28.4), and the parked vol/kill-switch question (§35.6) still gates live
   deployment. A clean 2024 does not clear it.
2. Chaining validate -> holdout in one session is exactly how a last look
   gets spent in the momentum of a good number. **The holdout is one look,
   ever.** It gets its own decision with a clear head.

No threshold above is adjusted after seeing 2024.

## 38. VALIDATE RESULT — B on 2024. All three gates pass. NOT REFUTED.

**The first out-of-sample look.** Trial 10 logged `status: started` at commit
`8ac7ec4` — the same commit that fixed §37's rule — before execution.
**Budget 10 of 25.** Holdout untouched.

### 38.1 The result

```
                            USDT fees      USDC fees
  Sharpe                       +0.603         +0.675
  Sharpe 90% CI          (-1.02, 2.19)  (-0.95, 2.26)
  ann return                   +9.54%        +10.87%
  ann vol                      17.70%         17.58%
  max drawdown                 18.52%         17.91%    (2024-08-09)
  net PnL $                    +38.16         +43.49
  fees $                        19.04          13.71
  price PnL $                  +48.47         +48.47
  funding PnL $                 +8.73          +8.73
  active days                 365/365        365/365
```

### 38.2 Tier 1 gates — all pass

| Gate | Threshold | Result | |
|---|---|---|---|
| **G1** price PnL ≥ 0 | refuted if < 0 | **+48.47** | **PASS** |
| **G2** max DD ≤ 30% (USDT) | refuted if > 30% | **18.52%** | **PASS** |
| **G3** Sharpe ≥ 0.30 (USDC) | too weak if < 0.30 | **+0.675** | **PASS** |

**Sharpe +0.675 sits inside the 0.5–0.7 drift-adjusted success band** that
§37.1 fixed before the run (1.114 × 0.59 ≈ 0.66). This is the outcome §37.4
called "best realistic": **consistent with momentum surviving out of
sample.**

**It is NOT proof.** The 90% CI is (−0.95, +2.26) — one year cannot confirm
anything (MDE ~1.65, §28.4). "Not refuted" is the strongest claim available
and the only one being made.

**G2 is the more interesting pass.** §35 left the vol/kill-switch question
parked because B's train max drawdown was 29.73%, 0.27 points from the stop,
and §37.2 pre-registered a breach as *decisive* — the parked question
answering itself. It did not breach: **18.52%, with 11.5 points of
headroom**, and realised vol came in at 17.70% against the 20% target. On
2024 evidence the fragility that worried §35.6 did not materialise. One year
is not a guarantee, but the pre-registered decisive failure did not happen.

### 38.3 Tier 2 mechanism checks — the harness behaved

```
  realised beta to BTC     +0.022    (band +/-0.15)     ok
  realised gross leverage  median 0.49, p95 0.76        (train 0.52 / 0.97)
  dollar-tilt identity     1.39e-16  (<= 1e-9)          ok
  active-days fraction     100.0%    (floor 80%)        ok
  turnover                 96.6x | rebalances 364 | skips 1
```

Every structural invariant holds out of sample, and leverage tracks train
closely. Nothing suggests the harness behaved differently on unseen data.

**Price/funding split: 127% price / 23% funding.** Price PnL (+48.47)
exceeds net (+38.16) because fees (19.04) outweigh funding (+8.73). Against
train B's 77/23 this is *more* price-driven, not less — **B did not become
A.** §37.3's explicit check on that is answered: the momentum leg carried
2024, and funding contributed a fifth of what it did in train proportionally.

Long/short price split: **long +69.44, short −20.96** — the same shape as
train B (long +604, short −155). The profit comes from being long the
strongest majors; the short leg costs money on price while doing its hedging
job.

### 38.4 The 2024 drift check — better than train

```
  Sharpe real +0.603 | demeaned +0.461 | drift +0.142  =  24% of total
  train B: 41%      synthetic zero-drift floor: ~18%
```

§37.3 flagged the risk that 2024's drift fraction would come in *far above*
train's 41%, which would mean the surviving "momentum" was mostly artifact.
**The opposite happened: 24%, well below train's 41% and much closer to the
~18% synthetic floor.**

So the out-of-sample year is *less* drift-contaminated than the in-sample
years. That is the most encouraging single number in this stage — it means
the part of B that survived into 2024 is disproportionately the
trend-continuation part, not the persistent-drift part. Worth stating
plainly: this is one year, and the drift estimate itself uses full-sample
means and is **DIAGNOSTIC ONLY, not runnable live**.

### 38.5 Who is paying, for 2024

The §36.6 momentum-payer story **held**, and the composition moved further
in its direction rather than reverting.

- **Price leg (dominant, +48.47 against +38.16 net).** Long +69.44 / short
  −20.96. Same mechanism argued in §36.6: late entrants buying established
  moves in the most liquid majors, and leveraged holders liquidated into
  weakness on the short side. In 2024 — a year with a documented retail bid
  in majors — this is the story one would expect to work, and it did.
- **Funding leg (+8.73, a fifth of train's proportional contribution).**
  Documented carry decayed from 2024 onward, and §37.4/§29.2 said to *expect*
  a weaker funding component and not to read it as failure. That is exactly
  what appeared. The strategy did not need it.
- **The decay exposure is unchanged and is B's real risk.** The leg that
  carried 2024 is majors momentum, which is the leg the literature says
  crowds out as capital arrives. A good 2024 does not answer that; it is the
  mechanism most likely to erode precisely because it worked.

### 38.6 What this does and does not license

- **Does not confirm an edge.** One year, MDE ~1.65, CI (−0.95, +2.26).
  "Not refuted" is the ceiling of what 2024 could have delivered.
- **Does clear every pre-registered refutation condition**, including the one
  (G2) that §35 parked as potentially decisive against B.
- **Does not clear live deployment.** §35.6's vol question is *softened* by
  18.52% out of sample but the paper-trading fill-rate work (Stage 2b B5,
  Stage 2e §4) is untouched, and no maker figure is reportable.
- **The holdout decision is DEFERRED TO THE USER** per §37.5, fixed before
  the run precisely so a good number could not carry it. The holdout is one
  look, ever, and it gets its own decision.

### 38.7 Status

Budget **10 of 25**. Validate **spent**. Holdout **sealed and untouched** —
`holdout_log.json` absent, zero holdout rows in `trials.jsonl`. Frozen
config §19.5 unchanged; B remains a candidate that has now survived one
out-of-sample year without being refuted.

## 39. Stage 6a — vol sweep for B: PRE-REGISTERED SELECTION RULE

**Recorded 2026-08-28 BEFORE running any sweep**, per STAGE6A 5.1. **Zero
trials** — vol targeting rescales identical positions without changing the
signal, the universe, or which names are selected, and Sharpe is
theoretically vol-invariant, so there is no edge being searched for. Budget
stays **10 of 25**; 2024 is not re-run; holdout sealed.

### 39.1 The selection rule

**Deploy the highest vol target whose MEASURED max drawdown ≤ 20%.**

- The cap is **20% drawdown**, for headroom. B's train drawdown at 20% vol
  was 29.73% — 0.27 points from the kill switch (§35). A 20% cap roughly
  halves the distance-to-death and leaves ~10 points of buffer.
- **Measured, not estimated.** The arithmetic (29.73% × v/20 ≤ 20% → v ≈
  13.4%) predicts ~13% wins, but the `MIN_NOTIONAL` floor may distort the
  drawdown/vol relationship at low vol. The rule uses the drawdown each vol
  *actually produces*.
- **Highest qualifying vol, not the best Sharpe.** Sharpe is vol-invariant;
  selecting on it selects on noise. The tie-break is return — higher vol
  gives higher return at the same Sharpe — which is why "highest qualifying"
  is the rule.
- If **no vol** produces measured drawdown ≤ 20%, report that and **stop**:
  the answer becomes "B needs vol below 12%", which reopens the floor
  question rather than resolving it, and is the user's decision.

The 20% cap is not adjusted after seeing the drawdowns.

### 39.2 The floor is the real output, not Sharpe

Every swept target is below 20% vol, so positions shrink and more of them
risk falling under the $5 `MIN_NOTIONAL`. Reported at each vol: skip rate by
reason; names dropped under the floor per rebalance; **realised** vol against
target; median and p95 position notional; measured max drawdown with date;
and Sharpe (reported, never selected on — if Sharpe moves a lot with vol,
something non-linear, i.e. the floor, is interfering).

### 39.3 The disqualifier, fixed in advance

A vol target is **disqualified regardless of drawdown** if either:

1. its **skip rate exceeds the 20%-vol run's by more than ~10 points**, or
2. its **realised vol falls more than ~2 points short of target**.

Either means the floor is *distorting* the strategy rather than merely
resizing it. A vol that only "works" by skipping half its rebalances is not
runnable at any Sharpe.

### 39.4 Why choose the vol now rather than after the holdout

Stage 6 validated B at 20% vol and it passed with an 18.52% drawdown, so
**2024 did not need the lower vol**. But the holdout window contains the deep
2025 drawdown (§22.3's correction: BTC peaked 2025-10-06 then fell ~47%),
where the headroom matters far more than it did in a mild year.

Choosing the deployment vol **now, on train**, means any eventual holdout
look tests the config actually intended for live use rather than the 20%
version that happened to survive one benign year. Recording that reasoning
here so the vol choice cannot later be mistaken for post-hoc fitting to a
holdout result that does not yet exist.

### 39.5 What this settles and what it does not

- **Settles:** B's deployment vol, and whether §35's fragility is fixable by
  sizing.
- **Does not settle:** whether B's edge is real. That was Stage 6 and is not
  reopened; this sweep *assumes* the edge and sizes risk around it.
- **Does not re-touch 2024.** Whether to re-validate B at the new vol, or
  proceed to holdout at it, is the next decision and is deferred to the user.

### 39.6 Result — the rule selects 14%

```
  vol  sharpe  realised  shortfall   ann ret   max DD      DD date  skip rate   lev
  12%  +1.363    11.76%     +0.24%   +16.57%   12.27%   2022-09-13     33.44%  0.32
  13%  +0.627    11.88%     +1.12%    +6.98%   17.85%   2023-06-28     42.35%  0.34
  14%  +0.985    12.91%     +1.09%   +12.61%   14.78%   2023-06-28     27.81%  0.37
  15%  +0.814    14.11%     +0.89%   +11.06%   21.34%   2023-06-28     25.47%  0.39
  20%  +1.114    18.57%     +1.43%   +20.88%   29.73%   2023-06-25     20.67%  0.52   (§34 reference)
```

Applying §39.1/§39.3 mechanically:

```
   12%  DD 12.27%  skip 33.44%   NO -- skip 33.44% > ref 20.67% + 10%
   13%  DD 17.85%  skip 42.35%   NO -- skip 42.35% > ref 20.67% + 10%
   14%  DD 14.78%  skip 27.81%   QUALIFIES
   15%  DD 21.34%  skip 25.47%   NO -- DD 21.34% > 20% cap
```

**DEPLOYMENT VOL = 14%.** Highest qualifying, selected on measured drawdown
and the floor disqualifier alone. Sharpe is reported and was **not** used to
select — 14% is not the best Sharpe in the table, and that is by design.

It delivers what §39.1 wanted: **max drawdown 14.78% against 29.73% at 20%
vol** — roughly halved, and now ~15 points clear of the 30% kill switch
instead of 0.27.

### 39.7 The vol-invariance check FAILED, and that is the important finding

§39.2 fixed this diagnostic in advance: *"if Sharpe moves a lot with vol,
something non-linear (the floor) is interfering."*

**Sharpe ranges +0.627 to +1.363 across the sweep — a spread of 0.736.**
Sharpe is theoretically vol-invariant. A spread that large means the vol
targets are **not** cleanly rescaling the same strategy.

Two further symptoms, both non-physical under clean rescaling:

- **Skip rate is not monotonic in vol.** 13% skips *more* (42.35%) than 12%
  (33.44%), and 14% skips *less* (27.81%) than both. Lower vol should mean
  smaller positions and monotonically more floor-driven skipping.
- **Rebalance counts are not monotonic either**: 1053 / 912 / 1142 / 1179 /
  1255 for 12/13/14/15/20%.

The mechanism is the floor's discreteness: a small change in target size
flips *whole rebalances* between feasible and skipped, and each flip changes
which days the book trades at all. So the runs differ in **which strategy
was executed**, not merely in position size.

**What this means for the choice.** The rule is mechanical and 14% is its
honest output, and the drawdown reduction it buys is real. But the Sharpe
column across this table is **not comparable between rows**, and 14% must
not be presented as performing better or worse than 20% — only as the vol
that meets the pre-registered risk cap without tripping the floor
disqualifier. Anyone reading 14%'s +0.985 against 12%'s +1.363 and inferring
something about edge would be reading noise generated by skip-set changes.

### 39.8 Floor diagnostics — sub-floor exposure is immaterial

```
  vol  positions  under $5   share   in NO-FILTER names  distinct names
  12%    10,432        19    0.18%             19               5
  13%     8,955        40    0.45%             40              10
  14%    11,329        20    0.18%             20               5
  15%    11,708        13    0.11%             13               5
  20%    12,491         0    0.00%              0               0
```

At the chosen 14%, **20 of 11,329 positions (0.18%) sit below $5, and all 20
are in symbols with no `symbol_filters` row** — EOS, SXP, MATIC, TOMO, BZRX,
all delisted or renamed. This is §18.4's known data hole resurfacing, not a
new problem, and at 0.18% it does not affect the sizing decision. It would,
however, be a **live order rejection** in each of those 20 cases, so it is
recorded rather than waved off.

Position notionals at 14%: median $18.26, p95 $42.82, min $3.00. Realised
vol 12.91% against a 12.91→14% target, i.e. **1.09 points short** — inside
the 2-point tolerance, but the shortfall is present at every target
(0.24–1.43 points) and is itself the floor capping size.

### 39.9 What is settled and what is not

- **Settled:** B's deployment vol is **14%**, and §35's fragility *is*
  fixable by sizing — 15 points of kill-switch headroom against 0.27.
- **Newly surfaced:** the vol knob is not a clean rescaling at this capital.
  The floor makes it partly a *selection* knob, which is a limitation of
  running $400 against a $5 floor and not of the vol choice.
- **Not settled, and not reopened:** whether B's edge is real. Stage 6 said
  "not refuted" on 2024 and this sweep assumes that; it sizes risk around
  the edge rather than testing it.
- **2024 was not re-run and the holdout is untouched.** Whether to
  re-validate B at 14% on 2024, or proceed to holdout at 14%, is the next
  decision and is **deferred to the user**. Note the tension either way: §39.4
  chose the vol on train precisely so a holdout look would test the
  deployment config — but the 2024 validate that passed was run at 20%, so a
  holdout at 14% would be testing a config whose only out-of-sample evidence
  came at a different size.

Budget **10 of 25**, unchanged — no sweep run was logged as a trial.

## 40. RE-VALIDATE RULE — B at 14% on 2024. Pre-registered 2026-08-28, before the run.

**Config:** B at **14% vol target** — the vol already locked by the §39 train
drawdown rule. Top-15 PIT majors, `lookback=14`, `skip=0`, N=10, k=5, 3x cap,
beta-neutral, +1min fill, 5bps slippage, $400, both fee schedules. **Window:
2024 only.** Cost: **one trial, budget 10 -> 11 of 25.** Holdout sealed.

### 40.1 The discipline that makes a second look at 2024 legitimate

2024 has already been used once (Stage 6: B at 20%, Sharpe 0.675, passed).
This is a **second look at the same year**, so the framing has to be exact:

- **The vol is already chosen.** 14% was fixed by the §39 train rule on
  measured drawdown, before this run and without reference to 2024.
- **2024 answers exactly one question:** does the 14% config clear the same
  pre-registered gates the 20% config cleared?
- **The 20% result is reference only.** This is *not* "14% vs 20%, keep the
  winner". Selecting the vol by 2024 performance would convert the validation
  set into a selection set — the precise error avoided all project long.
- **14% must pass on its own.** "Beats 20% on 2024" is not a criterion, will
  not be computed as one, and will not be reported as one. The only place the
  20% run appears is §40.4's floor comparison, which is a *mechanism* check
  on whether the book still functions, not a performance ranking.

If the framing drifts toward picking a vol from 2024, that is the error to
stop on.

### 40.2 Tier 1 — hard gates, any one failing = refuted

| # | Test | REFUTED if |
|---|---|---|
| **G1** | price PnL sign | 2024 price PnL **< 0** |
| **G2** | drawdown | max DD **> 30%** (USDT run). Should pass easily — train DD at 14% was 14.78% |
| **G3** | Sharpe floor | Sharpe **< 0.30** at USDC fees |

### 40.3 The drift-adjusted band — unchanged from §37.1

41% of B's train Sharpe is drift and does not repeat out of sample. The
reference is the **20% train Sharpe of 1.114**, not the 14% sweep figure —
§39.7's invariance failure means the sweep's Sharpe column is not clean, and
edge is the size-independent quantity:

```
  1.114 x (1 - 0.41)  ~  0.66     drift-adjusted success expectation
```

**A 2024 Sharpe of 0.5-0.7 is consistent with success.** More is not
demanded. The 14% run's realised vol (~13%) means its *absolute return* will
be lower than the 20% run's — that is arithmetic, expected, and **not a mark
against it. Judge Sharpe, not return.**

### 40.4 The floor check — specific to this config

§39.7 established that vol targeting is not a clean rescale at $400: the
`MIN_NOTIONAL` floor flips whole rebalances in and out of feasibility. So
2024 at 14% must confirm the book actually *functions* out of sample:

- skip rate against the 20% 2024 run (mechanism check, not performance)
- realised vol against the 14% target (train fell ~1.1 points short)
- names dropped under `MIN_NOTIONAL`
- active-days fraction **>= 80%**

**If the 14% config skips materially more of 2024 than the 20% config did,
the floor is distorting it out of sample, and that is a caveat on any pass**
— recorded explicitly, not absorbed into a green verdict.

### 40.5 The reading

| Outcome | Meaning |
|---|---|
| all Tier 1 pass, Sharpe 0.5-0.7, price-driven, floor clean | the 14% deployment config survives OOS; a holdout would test the config actually intended for live use |
| all Tier 1 pass but floor skips **>>** the 20% run | passes, but the floor contaminates it OOS — deployment carries that limitation, recorded |
| **G1 fails** | the 14% config does not survive. Since 20% passed and 14% did not, the difference is floor-driven day-selection rather than edge — **decisive against deploying 14%** |
| **G2 fails** | would contradict the train sizing entirely — **investigate for a bug** before accepting |
| **G3 fails** | too weak to carry to holdout at this vol |

Nothing adjusted after seeing 2024.

### 40.6 After the result

**Stop at the report.** The holdout is a separate decision with a clear head
(§37.5). A 14% pass does **not** auto-license it — it removes the §39.9
tension (the deployment config would then have its own OOS evidence) and
makes the holdout decision cleaner. Nothing more.

## 41. RE-VALIDATE RESULT — B at 14% on 2024. Gates pass; both mechanism checks fail.

Trial 11 logged `status: started` at commit `814e0d6` before execution.
**Budget 11 of 25.** Holdout untouched.

### 41.1 The result

```
                            USDT fees      USDC fees
  Sharpe                       +1.721         +1.765
  Sharpe 90% CI           (0.27, 3.20)   (0.32, 3.25)
  ann return                  +22.17%        +22.76%
  ann vol                      12.06%         12.03%
  max drawdown                  7.95%          7.92%    (2024-03-13)
  net PnL $                    +88.69         +91.03
  price PnL $                  +91.72         +91.72
  funding PnL $                 +5.34          +5.34
```

**Tier 1 gates — all pass on their own terms:**

| Gate | Threshold | Result | |
|---|---|---|---|
| G1 price PnL ≥ 0 | refuted if < 0 | **+91.72** | PASS |
| G2 max DD ≤ 30% | refuted if > 30% | **7.95%** | PASS |
| G3 Sharpe ≥ 0.30 (USDC) | too weak if < 0.30 | **+1.765** | PASS |

**Neither the headline Sharpe nor the gate results should be believed as
evidence of edge.** Both mechanism checks §40.4 required fired, and they
fired hard.

### 41.2 The floor caveat fired overwhelmingly

§40.4: *"If the 14% config skips materially more of 2024 than the 20% config
did, the floor is distorting it OOS and that is a caveat on any pass."*

```
                 rebalances   skips   skip rate
  B @ 20% (§38)         364       1       0.3%
  B @ 14% (this)        176     189      51.8%
```

**The 14% config skipped 189 of 365 scheduled rebalances — 51.8%, against
0.3% at 20%.** It traded on fewer than half the days the validated config
traded on. That is not "materially more"; it is a different strategy. Held
positions were carried across skips by rescale-on-skip, so turnover collapsed
from 96.6x to **38.8x** and realised leverage fell to a 0.36 median (train
0.52).

§39.7 predicted exactly this mechanism on train — the floor flipping whole
rebalances in and out of feasibility rather than cleanly rescaling — and 2024
confirms it out of sample and worse.

### 41.3 The drift check is the disqualifying one

§40.4 / §37.3: *"If the 2024 drift fraction is far above train's 41%, the
'momentum' that survived is mostly artifact."*

```
  Sharpe real  +1.721 | demeaned  -0.441 | drift +2.162  =  126% of total
  train B: 41%   |   the 20% config on 2024: 24%   |   synthetic floor: ~18%
```

**Remove each symbol's full-sample drift and the 14% config LOSES MONEY in
2024** (demeaned Sharpe −0.441). The drift component is 126% of the total —
more than all of it. Against train's 41% and the 20% config's 24% on this
same year, this is not a marginal deterioration; it is a categorical one.

The two failures are the same failure. A book that skips 52% of its
rebalances is a book that mostly *holds*, and holding is precisely how a
strategy harvests persistent per-symbol drift rather than trend
continuation. This is the same shape as the stale-book artifact that voided
grid v1 (§13.1) — different mechanism (rescale-on-skip, not
hold-without-rescaling) but the same economics.

### 41.4 The reading, per §40.5

The pre-registered branch is *"all Tier 1 pass but floor skips ≫ the 20% run
→ passes, but the floor contaminates it OOS — deployment carries that
limitation; record it."* That branch fires, and the drift result makes the
limitation severe rather than cosmetic.

**Stated plainly: the 14% config is not deployable on this evidence.** It
clears the three hard gates, and I am not moving those gates after the fact —
but the gates were designed to catch a dead signal, not a floor-mangled one,
and both checks written specifically to catch the latter came back positive.

**What must NOT be concluded.** §40.1 forbade a 14%-vs-20% comparison on
2024, and that prohibition protects against the *opposite* error to the one
that is tempting here. The 14% run's Sharpe of 1.765 against the 20% run's
0.675 is **not** evidence that 14% is better — the two ran on different day
sets (176 vs 364 rebalances). No such comparison is made or implied.

### 41.5 What this leaves

- **The 20% config remains the only version of B with clean out-of-sample
  evidence**: §38's 24% drift fraction (better than train), 1 skip, 364
  rebalances, all invariants intact. Its problem is §35's 0.27 points of
  kill-switch headroom on train.
- **The 14% config has the risk headroom and not the mechanism**: 7.95%
  drawdown, but half its rebalances skipped and its return essentially all
  drift.
- **The $400 capital is the binding constraint underneath both.** §32.3
  already established that `MIN_NOTIONAL` converts rank weighting into
  truncation at this size; §39.7 and now §41.2 show it also converts the vol
  knob into a day-selection knob. The dilemma is not vol — it is that $400
  against a $5 floor cannot express a 14%-vol book on 15 majors.
- **Not attempted here:** re-sizing capital, changing N, or any third vol.
  Each is a new configuration and a new trial, and §40.1's discipline means
  the choice cannot be made by trying them on 2024.

### 41.6 Status

Budget **11 of 25**. Holdout **sealed and untouched** — `holdout_log.json`
absent, zero holdout rows. The holdout decision remains **deferred to the
user** (§37.5/§40.6), and this result makes it harder rather than easier: the
deployment vol chosen on train does not survive its own mechanism checks out
of sample, so a holdout look at 14% would be testing a contaminated config,
while a holdout at 20% would be testing one already judged too fragile to
deploy.

## 42. Stage 7 — does the 14% config heal at $800? PRE-REGISTERED READING

**Recorded 2026-08-28 BEFORE running.** Capital is a `Config` value:
doubling it rescales position sizes without changing the signal, the
universe, or which names rank, so this is the same risk-sizing diagnostic
family as the vol sweep and consumes **no trial**. Budget stays **11 of 25**;
2024 not touched; holdout sealed.

**Why it gates everything.** The 14% config has never had a clean run at any
capital. Every prior look was at $400, where §41.2 showed it skipped 51.8%
of 2024's rebalances and §41.3 showed its return was 126% drift with a
*negative* demeaned Sharpe. At $800 it is a different book — possibly one
that trades every day. Whether it heals is the precondition for spending the
holdout or any real money.

### 42.1 The checks — mechanism repair, not performance

Sharpe is **not** the test, and per §39.7 is not even comparable across
sizes (the floor changes which days trade).

| Check | Broken ($400/14%) | Pass if |
|---|---|---|
| skip rate | 27.81% train / 51.8% on 2024 | falls near the 20% config's ~20.7% train, not the $400/14% run's |
| **drift fraction** | 126% on 2024, demeaned Sharpe **negative** | see §42.2 — the disqualifier |
| realised vol vs target | ~1.1 pt short, floor-capped | reaches ~14%, i.e. the floor no longer caps size |
| smallest position | min $3.00, sub-floor days | median and p05 clear $5 with margin |
| rebalance count | 1142 train, non-monotonic in vol | traded on nearly all ~1380 scheduled days |

To keep the comparison honest I will also compute the **$400/14% train drift
fraction**, which has never been measured — comparing $800/14% against the
$400/**20%** figure of 41% would confound capital with vol.

### 42.2 The decisive check, stated as pass/fail

**Drift fraction is the disqualifier.** A book that still shows drift as a
majority of Sharpe is still mostly *holding* rather than trading, and $800
has not fixed it.

```
  PASS  =  demeaned Sharpe > 0  AND  drift fraction < 50%
          < 30%      clean
          30 - 50%   healed-but-watch
          >= 50%     UNHEALED
```

### 42.3 The outcome table

| Outcome | Meaning |
|---|---|
| skip rate low, drift < 30%, realised vol ~14% | **$800 heals it** — deployment config confirmed on train; the holdout becomes worth spending on it |
| drift 30–50%, skips moderate | **partially healed** — $800 is marginal; consider whether $1,000–1,200 is the real line before committing |
| drift ≥ 50% or skips still high | **$800 does not heal it.** Report the capital at which the smallest position clears the floor with margin, as the revised target |

### 42.4 What happens next in each case — stated, not executed

**If it heals:** (1) one clean validate of the $800/14% config on 2024 — that
is a **trial**, number 12 of 25, because the $400 validate measured a
different, broken book and this would be the config's first honest
out-of-sample look; (2) *then* the holdout decision, with a config finally
both survivable and mechanically clean; (3) paper-trade at $800 for real fill
rates before live, especially for USDC maker. **None of that is done in this
stage.**

**If it does not heal:** state the revised capital target computed from the
floor, and stop. **No config is shrunk to force $800 to work** — that would
re-enter the compression cycle §41.5 named. The honest finding becomes "the
validated strategy needs ~$X to run at its survivable vol", where $X is
computed, not guessed.

The holdout remains the one irreplaceable look and is not spent by this
stage.

### 42.5 Result — HEALED at $800, on every mechanism check

```
config                          skip  rebal  realised  short   maxDD  sharpe   lev
B @ 14% @ $800  <- THE TEST   17.26%   1309    12.88%  +1.12%  24.79%  +1.017  0.36
B @ 14% @ $400  (broken ref)  27.81%   1142    12.91%  +1.09%  14.78%  +0.985  0.37
B @ 20% @ $400  (clean ref)   20.67%   1255    18.57%  +1.43%  29.73%  +1.114  0.52

config                        notional med     p05     min   under $5      of
B @ 14% @ $800  <- THE TEST        $36.95  $13.07   $5.00          0  13,026
B @ 14% @ $400  (broken ref)       $18.26   $7.11   $3.00         20  11,329
B @ 20% @ $400  (clean ref)        $29.80  $10.91   $5.00          0  12,491
```

**Drift — the disqualifier (§42.2):**

```
config                        SR real  SR demeaned   drift  fraction
B @ 14% @ $800  <- THE TEST    +1.017       +0.867  +0.150      15%
B @ 14% @ $400  (broken ref)   +0.985       +0.954  +0.031       3%
B @ 20% @ $400  (clean ref)    +1.114       +0.662  +0.452      41%
```

| Check | Result | |
|---|---|---|
| demeaned Sharpe > 0 | **+0.867** | PASS |
| drift fraction < 50% | **15%** | PASS — **clean (<30%)** |
| skip rate near the 20% ref | 17.26% vs 20.67% | **better than the reference** |
| realised vol vs target | 12.88% vs 14% (−1.12) | still short — see §42.6 |
| p05 position notional | **$13.07** (floor $5) | clear with margin |
| positions under the floor | **0 of 13,026** | clean |

**Verdict: HEALED at $800.** Rebalances rise 1142 → 1309 of ~1380 scheduled,
skips fall *below* the 20% reference's, no position touches the floor, and
drift lands at 15% — cleaner than the 20% config's 41% and below the ~18%
synthetic floor.

### 42.6 Three findings that qualify the headline, none of which I will bury

**1. The drawdown got WORSE at $800, and that undermines the rule that chose
14%.** Same vol target, double the capital: max drawdown **14.78% → 24.79%**.

That is not noise. At $400 the floor was skipping rebalances — 27.81% of
them — and those skips were *accidentally protective*: the book sat out days
it could not size. At $800 it trades them and takes the losses. **The
$400/14% config's flattering 14.78% drawdown was itself a floor artifact.**

The consequence is direct: §39.1 selected 14% as "the highest vol whose
**measured** max drawdown ≤ 20%", and that measurement was made at $400 on a
floor-contaminated run. **At $800 the same vol produces 24.79%, which would
not have passed that cap.** The healed config does not satisfy the rule that
picked its vol. Headroom to the 30% kill switch is back down to 5.2 points.

**2. The "broken reference" was broken on 2024, not on train.** $400/14% shows
only **3%** drift on train against **126%** on 2024, and 27.81% skips against
51.8%. So §41's damage was concentrated in the validate year, where the floor
bit far harder, rather than being a property of the config everywhere. This
does not rescue the $400/14% config — 2024 is the out-of-sample year and that
is where it failed — but the diagnosis is more specific than "$400/14% is
broken."

**3. Realised vol still falls 1.12 points short of target.** The shortfall is
essentially unchanged from $400 (1.09) and is present at every configuration
tested. The floor is no longer *skipping* rebalances, but something is still
capping size — most likely the `[0.5x, 1.5x]` weight band and the 3x gross
cap interacting with the vol scale, not `MIN_NOTIONAL`. Not investigated
here; recorded.

### 42.7 What this licenses, and the tension in it

Per §42.4, healing licenses **one clean validate of the $800 config on 2024
as trial 12** — the $400 validate measured a different, broken book.

**But finding 1 makes the vol choice questionable.** Two coherent paths, and
they are the user's to choose between:

- **Validate $800 @ 14% as planned.** The mechanism is clean and the
  drawdown (24.79%) is still inside the 30% switch. Accepts that 14% was
  selected on a contaminated measurement.
- **Re-derive the vol at $800 first.** A vol sweep at $800 is free — capital
  and vol are both risk-sizing inputs on train, the same class as §39 and
  this stage — and would apply §39.1's 20%-drawdown cap to *uncontaminated*
  drawdowns. It costs no trial and would likely select a vol below 14%.

I have **not** run the second, because §6's order of work ends this stage at
the verdict, and because choosing between the two is a decision about what
the deployment config *is*, which belongs to the user. Recording the tension
rather than resolving it unilaterally.

**Not done, per §8:** no config was shrunk to force $800 to work; N stays 10,
k stays 5, the vol target was not adjusted after seeing these numbers.

### 42.8 Status

Budget **11 of 25** — no trial spent, capital being a risk-sizing input.
2024 not re-run. Holdout **sealed and untouched**. The next step is a
decision, not a run: validate $800 @ 14%, or re-derive the vol at $800 first.

## 43. Stage 7a — the honest vol sweep at $800: PRE-REGISTERED RULE

**Recorded 2026-08-28 BEFORE running.** Risk sizing on train, same class as
§39 and §42: **no trial.** Budget stays **11 of 25**; 2024 untouched;
holdout sealed.

### 43.1 The circularity this removes

§39 picked 14% as "the highest vol with measured drawdown ≤ 20%" — but that
14.78% was a **$400 floor artifact**: the book was skipping 27.81% of
rebalances, i.e. sitting out the very days it would have lost on. At $800,
with the floor no longer skipping, the same vol produces **24.79%** (§42.6).
**14% was chosen from a number its own contamination produced.** Validating
it would spend a trial on a config whose defining parameter is a known
artifact. This sweep removes that for free, before the trial.

### 43.2 The coupling the sweep must expose

Capital and vol are **coupled through the floor**. Lowering vol shrinks
positions; far enough down they fall back under $5 and the floor re-breaks
the book — re-introducing the skips and drift $800 just healed.

The honest drawdown/vol ratio at $800 is **24.79 / 12.88 ≈ 1.92**, not the
1.49 the contaminated data implied. Under 1.92 the 20% cap points to roughly
**10% vol** — but ~10% vol at $800 may itself re-break the floor. So the vol
cannot be re-derived from the ratio alone; **skip and drift must be
re-measured at every vol**, because a vol that satisfies the cap on paper is
worthless if the floor mangles it.

### 43.3 The selection rule — all three at once

**Deploy the highest vol satisfying ALL THREE simultaneously:**

1. **measured max drawdown ≤ 20%** — the cap is unchanged from §39, not
   re-tuned;
2. **drift fraction < 30% AND demeaned Sharpe > 0** — the clean band from
   §42.2;
3. **skip rate ≤ 17.26% + 5 points = 22.26%** (the §42 $800/14% rate) **AND
   realised vol within 2 points of target.**

Conditions 2 and 3 are exactly what §39 lacked and what let the circularity
through. Sharpe is reported and **never selected on** — §39.7's invariance
failure means it is not comparable across vols, and the floor makes that
worse at low vol.

**If no vol satisfies all three, that is the finding.** No condition will be
relaxed to manufacture a winner.

### 43.4 The three outcomes, fixed before the numbers

| Outcome | Meaning | Next |
|---|---|---|
| **A clean vol exists** (all three hold) | that is the deployment config, derived honestly | validate **that** vol on 2024 — one trial, spent right |
| **The floor re-breaks below 14%** (the cap wants ~10%, but ~10% skips or drifts) | $800 heals 14% but cannot support the vol the cap requires — **$800 is not enough for a cap-satisfying book** | report the capital at which the cap-satisfying vol *also* clears the floor: the true joint target |
| **14% is the lowest floor-clean vol and it breaches the cap** (24.79%) | genuine tension — at $800 you may have drawdown headroom **or** a floor-clean mechanism, not both | a knowing risk decision, **deferred to the user** with both numbers on the table |

### 43.5 Scope limits

- **N stays 10, k stays 5.** Only vol is swept.
- 14% must **reproduce §42's numbers exactly** — a determinism check; if it
  does not, stop.
- The persistent ~1.1-point vol shortfall (§42.6 finding 3) is **reported per
  vol**, and whether it worsens as vol drops is noted (that would confirm the
  weight-band-vs-scale interaction), but it is **not chased here**.
- No vol is validated on 2024 in this stage; that is the next step and a
  trial. Holdout untouched.

### 43.6 Result — OUTCOME A. Deployment vol = 12% @ $800.

**Determinism check passed first:** the 14% row reproduces §42 exactly
(skip 17.26%, 1309 rebalances, maxDD 24.79%, realised vol 12.88%, drift
14.71% against §42's rounded 15%).

```
  vol   maxDD      DD date    skip  rebal  realised   short   drift  demean SR  sharpe
   8%   8.25%   2022-09-13  27.94%   1140     7.38%  +0.62%      1%     +0.967  +0.977
  10%  17.03%   2023-06-28  21.55%   1241     9.28%  +0.72%     22%     +0.651  +0.835
  11%  14.44%   2023-06-28  21.11%   1248    10.15%  +0.85%     31%     +0.684  +0.991
  12%  19.47%   2023-06-25  19.85%   1268    11.07%  +0.93%     26%     +0.882  +1.191
  14%  24.79%   2023-08-28  17.26%   1309    12.88%  +1.12%     15%     +0.867  +1.017

  vol  notional med      p05      min   under $5       of
   8%       $18.72    $7.26    $3.08         15   11,317
  10%       $22.49    $8.43    $3.10          9   12,360
  11%       $25.41    $9.33    $3.40          6   12,432
  12%       $31.06   $11.21    $4.45          1   12,622
  14%       $36.95   $13.07    $5.00          0   13,026
```

Applying §43.3's three conditions simultaneously:

```
    8%  DD ok   drift ok   skip NO (27.94% > 22.26%)
   10%  DD ok   drift ok   skip ok        -> ALL THREE
   11%  DD ok   drift NO (31%)  skip ok
   12%  DD ok   drift ok   skip ok        -> ALL THREE
   14%  DD NO (24.79%)  drift ok  skip ok
```

**Highest vol satisfying all three: 12%.** maxDD 19.47%, drift 26%, demeaned
Sharpe +0.882, skip 19.85%, realised vol 11.07%, 1 of 12,622 positions under
the floor.

### 43.7 Three qualifications on that verdict

**1. The qualifying set is non-monotonic, and that is a warning about
precision.** 10% and 12% both pass; **11% between them fails on drift (31%
against a 30% cap)**. Adjacent vols should not straddle a criterion like
that. It is the same floor discreteness §39.7 identified — small size changes
flip whole rebalances, and the drift measure moves ±5 points between adjacent
vols as a result.

So **12%'s "26% drift" carries an error bar comparable to its distance from
the 30% cap.** The rule selected it correctly on the numbers, but those
numbers are not smooth in vol, and a slightly different grid could have
returned a different answer.

**2. 12% sits 0.53 points under the drawdown cap** (19.47% vs 20%). That is
the same boundary-hugging that made the original 14% choice fragile — §39
picked 14% at 14.78% against a 20% cap and it turned out to be an artifact.
This time the drawdown is uncontaminated, so the number is trustworthy in a
way the earlier one was not, but the margin is thin.

**10% is the qualifying alternative with real headroom**: maxDD 17.03%
(3 points of margin), drift 22%, skip 21.55%. The pre-registered rule takes
the *highest* qualifying vol, so 12% is the answer, and I am not re-opening
the rule to prefer 10%. Recording that 10% exists and is materially safer is
part of reporting honestly, not a re-selection.

**3. §42.6's finding-3 suspicion is contradicted, informatively.** The vol
shortfall does **not** worsen as vol drops — it shrinks monotonically:
1.12 → 0.93 → 0.85 → 0.72 → 0.62 points at 14/12/11/10/8%. §7A.6 asked
whether it would worsen (which would confirm a weight-band-vs-scale
interaction). It does the opposite, so that hypothesis is wrong. The residual
shortfall is more likely the ex-ante vol estimate running slightly high
against realised, which is ordinary estimation error, not a structural cap.

### 43.8 What the sweep settles

- **The §39 circularity is gone.** 14%'s selection rested on a contaminated
  14.78% drawdown; the honest number at $800 is 24.79% and 14% **fails** the
  unchanged 20% cap. The rule that produced 14% has now been applied to clean
  data and produces **12%**.
- **$800 does support a cap-satisfying book** — Outcome B (insufficient
  capital) and Outcome C (headroom-vs-mechanism tension) both did not fire.
  Two vols satisfy all three conditions at once.
- **No condition was relaxed and nothing was shrunk.** N stays 10, k stays 5,
  the 20% drawdown cap is the same one §39 used, and the drift and skip
  conditions were fixed before the numbers.

### 43.9 Next step — a decision, not a run

Per §43.4 Outcome A, the next step is **one validate of the $800/12% config
on 2024 — trial 12 of 25.** That is the config's first honest out-of-sample
look: every prior validate measured either a different vol (§38, 20% @ $400)
or a floor-broken book (§41, 14% @ $400).

**Not run here.** §7A.7 ends this stage at the verdict, and §9 forbids
validating any vol on 2024 in this stage. Budget stays **11 of 25**; 2024 not
re-run; holdout **sealed and untouched**.

One thing worth putting in front of the user before that trial is spent:
given §43.7's non-monotonicity, 12% and 10% are close enough on the evidence
that which one gets validated is a judgement about risk appetite — 12% for
return, 10% for 3 points of drawdown margin — rather than something the data
resolves. The rule says 12%; the choice of whether to spend the trial on the
rule's answer or on the safer neighbour is the user's.

## 44. Stage 8 — VALIDATE B at 10% / $800 on 2024: PRE-REGISTERED RULE + RULE OVERRIDE

**Recorded 2026-08-29, BEFORE the run and before 2024 is seen at this
config.** Committed in its own commit ahead of any execution. Cost: **one
trial, budget 11 → 12 of 25.** Holdout (2025-01 → 2026-07) stays sealed.

### 44.1 The override: deploying 10%, not the 12% the §43 rule selected

§43.3's pre-registered rule took *the highest vol satisfying all three
conditions*, and §43.6 applied it correctly: **12%**. The config being
validated here is **10%**, one step below. That is a deliberate override of a
pre-registered rule, and an override is exactly the move that lets fitting in
through the back door. It is legitimate here only for reasons recorded
**before** the result, so it can be audited later. Four, all of which hold
now:

**1. The override goes toward LESS return, not more.** Train Sharpe at 10% is
0.651 against 12%'s 0.882; realised vol 9.28% against 11.07%; annualised
return correspondingly smaller. Fitting always reaches for *more* — a rule
broken in the direction of lower profit for more safety margin is the
opposite failure mode from the one pre-registration exists to prevent.

**2. The ground is measurement instability the sweep itself surfaced, not a
result.** §43.7 qualification 1: drift is **non-monotonic in vol** — 22% at
10%, **31% at 11%**, 26% at 12%, against a 30% cap. Adjacent vols straddle
the criterion, so the drift measure carries roughly ±5 points of floor
discreteness noise. 12%'s two qualifying margins are **inside that noise**:
drawdown 19.47% against a 20% cap (0.53 points) and drift 26% against 30%
(4 points). On a different day-set 12% could fail its own criteria. 10%
clears both caps by 3–4 points — outside the noise.

**3. It is recorded now, before 2024 at this config is seen.** Not selected
after a result. If 2024 later favours 12%, that is not retrievable: the
choice is spent here, on the record, in advance.

**4. It avoids repeating a failure this project has already made once.** §39
picked 14% at $400 as the highest vol under a 20% drawdown cap, measured
14.78%, hugged the boundary — and §41 showed it passed the gates on paper
and failed both mechanism checks out of sample, its "drawdown headroom"
being a floor artifact. 12% hugs two boundaries. The entire vol
investigation (§42, §43) existed to escape boundary-hugging; deploying
another cap-hugger would repeat the same error with open eyes.

**Explicitly on the record: 12% is the aggressive alternative and it was NOT
chosen.** It is the rule's answer, it has the higher train Sharpe, and it was
declined on train — before 2024 — for the four reasons above. No part of this
decision is licensed to be revisited by looking at 2024.

### 44.2 The config and the cost

B at **10% vol target, $800 capital**: top-15 PIT majors, `lookback=14`,
`skip=0`, N=10, k=5, 3× gross cap, beta-neutral, +1min fill, 5 bps/side
slippage. Both fee schedules (USDT taker 5.0 bps, USDC taker 3.6 bps),
reported together. **Window: 2024 only.**

The trial row is logged with `status: started` at a clean commit **before**
execution: an errored run still spends the trial. Budget **11 → 12 of 25**.

### 44.3 Tier 1 — hard gates. Any one failing = refuted.

Identical thresholds to §37 and §40. Nothing re-tuned for this config.

| # | Test | Refuted if |
|---|---|---|
| **G1** | price PnL sign | 2024 price PnL **< 0** |
| **G2** | drawdown | max DD **> 30%** (USDT run). Train at 10%/$800 was 17.03%, so a large breach signals a bug, not a risk finding |
| **G3** | Sharpe floor | Sharpe **< 0.30** at USDC fees |

### 44.4 The success band: **0.4 – 0.65**, and why it is lower

The drift-stripped train number is the real-edge estimate. At 10%/$800 train
Sharpe is 0.651 with a 22% drift fraction:

```
  0.651 x (1 - 0.22)  ~  0.51     drift-adjusted expectation
  success band: 2024 Sharpe ~0.40 - 0.65
```

This is **lower** than B@20%'s 0.5–0.7 band (§37.1) and that is by
construction: 10% vol sizes smaller and earns less both absolutely and, once
drift is removed, in risk-adjusted terms.

**Judge Sharpe, not return.** The absolute 2024 return will be visibly
smaller than any prior config's — arithmetic, not weakness. A 2024 Sharpe in
0.40–0.65 is a **PASS consistent with the edge surviving**. More is not
demanded, and a lower absolute return is not a mark against it.

### 44.5 The mechanism checks — this config's specific risks

§41 and §42 established that the floor can pass all three hard gates while
corrupting the mechanism underneath. Both checks that caught it run here.

| Check | Train reference at 10%/$800 | Reading |
|---|---|---|
| **drift fraction on 2024** | **22%** | far above train (say **> 40%**) means the OOS edge is mostly artifact — this was the §41 disqualifier |
| **skip rate on 2024** | **21.55%** | a large jump means the floor bit harder out of sample |
| realised vol vs 10% target | 9.28% (−0.72) | is the floor capping size again |
| active-days fraction | floor **≥ 80%** | |
| realised beta to BTC | band **±0.15** | structural invariant |
| dollar-tilt identity | **≤ 1e-9** | structural invariant |

### 44.6 The reading — fixed before the run

| Outcome | Meaning |
|---|---|
| Tier 1 pass, Sharpe 0.40–0.65, drift ≤ ~30%, floor clean | **the deployment config survives OOS cleanly.** The holdout becomes worth spending on a config that is survivable, mechanically clean, and honestly derived |
| Tier 1 pass but drift > 40% or skips ≫ train | passes the gates, floor-contaminated OOS — the same trap as §41. A serious caveat; **not holdout-ready** |
| **G1 fails** (price PnL < 0) | momentum did not survive at this size. **Refuted** |
| **G2 fails** (DD > 30%) | contradicts the train sizing — **investigate for a bug** before accepting |
| **G3 fails** (Sharpe < 0.30) | too weak to carry to the holdout |

**Nothing in §44.3–§44.6 is adjusted after seeing 2024.** No threshold, no
band, no branch.

### 44.7 After the result — stop

Whatever 2024 shows, this stage **stops at the report**. The holdout is one
look, ever, and gets its own decision with a clear head (§37.5). A clean 2024
here would mean the holdout finally tests a config validated *at its own vol
and its own capital* — the cleanest holdout setup the project has been able
to offer — but that decision is the user's, made separately. It is not
chained into this session.

Prohibited in this stage: framing the result as 10% vs 12% on 2024 (12% was
declined on train, on the record); demanding a Sharpe near B@20%'s band;
penalising the lower absolute return; touching the holdout.

### 44.8 RESULT — all three gates pass, mechanism clean. NOT REFUTED.

Trial 12 logged `status: started` at commit `bfb2cd1` on a clean tree
**before** execution. **Budget 12 of 25.** Holdout untouched.

```
                            USDT fees      USDC fees
  Sharpe                       +0.619         +0.693
  Sharpe 90% CI          (-1.00, 2.20)  (-0.93, 2.28)
  ann return                   +5.21%         +5.89%
  ann vol                       8.84%          8.81%
  max drawdown                  9.57%          9.26%    (2024-08-09)
  net PnL $                    +41.70         +47.11
  fees $                        19.33          13.92
  price PnL $                  +52.33         +52.33
  funding PnL $                 +8.70          +8.70
  active days                 365/365        365/365
```

**Tier 1 gates (§44.3), all three pass:**

| Gate | Threshold | Result | |
|---|---|---|---|
| G1 price PnL ≥ 0 | refuted if < 0 | **+52.33** | PASS |
| G2 max DD ≤ 30% (USDT) | refuted if > 30% | **9.57%** (train 17.03%) | PASS |
| G3 Sharpe ≥ 0.30 (USDC) | too weak if < 0.30 | **+0.693** | PASS |

**Structural invariants (§44.5), all intact:** realised beta to BTC **+0.011**
(band ±0.15), dollar-tilt **6.9e-17** (≤ 1e-9), active-days **100.0%** (floor
80%), realised gross leverage median 0.24 / p95 0.38.

### 44.9 The mechanism checks — both clean, and the skip result is the finding

```
                          skip rate   rebalances   drift   demeaned SR
  10% / $800  2024 (this)     0.27%      364 / 1     25%       +0.464
  10% / $800  train          21.55%     1241         22%       +0.651
  14% / $400  2024 (41)      51.80%      176        126%       -0.441
  20% / $400  2024 (38)       0.30%      364 / 1     24%          n/a
```

**Drift: 25% of the 2024 Sharpe.** Train at this config was 22%; the §44.5
caveat fires above 40%; §41 disqualified the previous candidate at 126% with a
*negative* demeaned Sharpe. **Here the demeaned Sharpe is +0.464** — strip
every symbol's full-sample drift and the book still makes money out of sample.
25% is inside the <30% clean band and 3 points from its own train value. This
is the check that killed the previous deployment candidate, and it passes.

**Skips: 1 of 365.** The floor did not bite in 2024. The comparison that makes
this informative is §41: **the same year, at 14% / $400, skipped 189 of 365.**
2024 is not a year that is easy on the floor — it destroyed the previous
config. The difference is position size, and it is a cliff rather than a
gradient (§39.7): train median notional $18.26 at 14%/$400 against $22.49 at
10%/$800, a 23% size difference separating a 51.8% skip rate from a 0.27% one.

**One number moved the wrong way, and it is small: the vol shortfall widened.**
Realised 8.84% against the 10% target is **−1.16 points**, against train
−0.72 at this config. Consistent with §43.7 finding 3 (the residual shortfall
is the ex-ante vol estimate running high, not a structural cap), but it did not
shrink out of sample.

**Position sizing, and the operational headroom it implies:** median $18.52,
p05 $7.86, min $5.04, **0 of 3,620 positions under the $5 floor.** Positions
scale 1:1 with capital, so the p05 position reaches the floor at
`5 / 7.86 = 0.636` of current capital — **the account can lose ~36% before the
floor starts biting the bottom of the book again.** The 30% kill switch sits
just inside that, which is the right order but not by much: a drawdown that
trips the switch leaves roughly 6 points of margin before the mechanism that
broke §41 re-engages.

### 44.10 The Sharpe landed slightly ABOVE the pre-registered band. Do not over-read it.

§44.4 set the success band at **0.40–0.65**. USDT Sharpe **0.619 is inside
it**; USDC **0.693 is 0.043 above the top**.

Above-band is not one of §44.6 failure branches, and it is not extra evidence
of strength. Two reasons to hold it flat:

1. **The band is narrower than the noise.** The band spans 0.25 points. The
   90% bootstrap CI on the 2024 Sharpe spans **about 3.2 points** (−0.93 to
   2.28). One year of daily returns cannot distinguish 0.51 from 0.69, which is
   exactly why §44.4 called 0.40–0.65 *consistent with the edge surviving*
   rather than a point prediction.
2. **The drift-adjusted expectation was 0.51 and the drift-adjusted realisation
   is 0.46** (the demeaned Sharpe). Measured on the quantity the band was
   *derived* from, the result is marginally **below** expectation, not above.
   The headline exceeds the band because 2024 drift came in slightly richer
   (25%) than the 22% assumed — not because the edge was stronger.

Recorded so that no later stage can cite "beat the band" as evidence.

### 44.11 The branch that fired, and what it is not

§44.6 row 1: **Tier 1 pass, drift ≤ ~30%, floor clean → the deployment config
survives OOS cleanly.** That is the branch, on the terms fixed before the run.

What it establishes: the honestly-derived config — vol re-selected on
uncontaminated drawdowns (§43), capital chosen to clear the floor (§42),
override to 10% recorded in advance (§44.1) — clears the same three gates the
20% config cleared, with the mechanism intact where the 14% config mechanism
was not. It is the first version of B whose out-of-sample evidence and whose
deployment parameters are the *same configuration*.

What it is not:

- **Not proof.** "Not refuted" remains the best obtainable outcome. The CI
  includes zero and a good deal below it.
- **Not a vol comparison.** 12% was declined on train, on the record (§44.1).
  Nothing here re-opens that, and no 10-vs-12 number was computed on 2024. Had
  this result been weak, 12% would not have become available either.
- **Not a fresh year.** 2024 has now been looked at three times (§38, §41, and
  this). Each look was a pre-registered pass/fail on a config fixed beforehand,
  and none selected a parameter from 2024 — but the power of the year to
  surprise is spent, and that is the strongest argument for the holdout being
  the next and last measurement rather than another 2024 variant.
- **Not a licence to deploy.** Testnet-only paper evidence, an unmeasured
  slippage assumption (5 bps from n=1), and a fee drag of 37% of price PnL at
  USDT fees (27% at USDC) all still stand between this and live capital.

**Composition note:** long leg **+71.46**, short leg **−19.13**. In a strongly
up year for the majors, a dollar-neutral book losing money on the short leg is
expected and is not a defect; the beta measurement (+0.011) confirms neutrality
held while it happened. Funding contributed **+$8.70**, 21% of net. Turnover
48.6× against §38 96.6× — the arithmetic of the lower leverage.

### 44.12 Status — STOP. Holdout decision deferred to the user.

Budget **12 of 25**. Holdout **sealed and untouched**: `holdout_log.json` does
not exist, `trials.jsonl` contains **zero** holdout rows.

Per §44.7 this stage ends at the report. The holdout is one look, ever, and
gets its own decision. What has changed is only that the decision is now
cleaner than it has ever been: a holdout look would test a config that is
survivable (9.57% max DD against a 30% switch), mechanically clean (0.27%
skips, 25% drift, positive demeaned Sharpe), and honestly derived (no parameter
of it chosen on 2024 or on a contaminated measurement). Every previous point in
this project at which the holdout was contemplated had at least one of those
three missing.

**That decision is the user's, and is not taken here.**

## 45. Stage 9 — the rank buffer: PRE-REGISTERED WIDTHS, RULE AND READING

**Recorded 2026-08-29, BEFORE any buffer is implemented or run.** Committed in
its own commit ahead of the code and of every number. Cost: **three trials,
budget 12 → 15 of 25.** Holdout sealed throughout.

The last open *strategy* question before the holdout. Fee drag on the frozen
deployment config is **27% (USDC) to 37% (USDT) of price PnL** (§44.8) — the
dominant friction since the project began — and §32.4 measured **67.9% of
turnover as boundary-crossing**, names round-tripping at the rank-k edge. That
is exactly what hysteresis softens. Tested now: after the config is otherwise
frozen, before the holdout is spent, so that if the buffer improves the config
then *it* becomes the deploy candidate.

### 45.0 CORRECTION to §44.4 and §44.10 — recorded before this stage runs

Setting up §45.5's baseline row surfaced an arithmetic error in Stage 8 that
must be on the record before any Stage 9 number exists.

**The 10%/$800 train row (§43.6) is Sharpe 0.835 real, 0.651 demeaned, drift
22%.** `0.651` is the **drift-stripped** column, not the headline. §44.4 took
0.651 as "train Sharpe" and applied the 22% drift haircut to it a second time
(`0.651 × 0.78 ≈ 0.51`) to derive the 0.40–0.65 success band. The haircut was
already in the number. Correctly derived, the drift-adjusted expectation for
the 2024 headline Sharpe is **0.835 × 0.78 = 0.651**, and the band by the same
±20% construction would have been roughly **0.55–0.85**.

**§44's verdict does not change.** The three Tier 1 gates are unaffected — none
of them references the band. 2024's Sharpe (0.619 USDT / 0.693 USDC) falls
inside the band as recorded *and* inside the corrected one. The recorded band
stays as recorded: it was pre-registered, the result was graded against it, and
moving a threshold after seeing the result is the exact thing this project
does not do.

**What does change is §44.10's reading, and it becomes less flattering.**
§44.10 compared 2024's demeaned Sharpe (0.464) against an "expectation" of
0.51 and called it "marginally below". The correct like-for-like comparison is
2024's demeaned **0.464** against train's demeaned **0.651** — the
drift-stripped edge came in **29% below train out of sample**, not marginally
below. Also void: §44.10's claim that the headline "exceeds the band because
2024 drift came in richer". Against the corrected centre of 0.651, the headline
did not exceed anything — USDT 0.619 is slightly *under* it and USDC 0.693
slightly over.

Two knock-on labels, corrected here: §44.1 called 0.651 and 0.882 the "train
Sharpe" of 10% and 12%. Those are the **demeaned** Sharpes; the headline pair
is 0.835 and 1.191. The override argument in §44.1 is unaffected — it ranks the
two vols the same way either way, and it never rested on the level.

Deterioration of roughly this size out of sample is ordinary and was expected
in kind if not in amount; it is recorded plainly rather than smoothed. It does
not re-open the §44.6 branch, which was decided on gates, drift fraction and
floor behaviour — none of which this touches.

### 45.1 The mechanism, and the construction details fixed in advance

Current rule (b=0): a name is held while it is in the top-k (long) or bottom-k
(short) by momentum rank, and exits the moment it leaves. A one-rank wobble at
the boundary costs a full round trip.

Buffer rule (hysteresis, width `b`): **enter** at rank ≤ k; **exit** only at
rank > k + b. Symmetric on the short leg — enter in the bottom k, exit only
once outside the bottom k + b. Selection/weighting step only: beta hedge, vol
target, floor handling, the feasibility drop-loop and rescale-on-skip are all
unchanged.

Hysteresis needs the *held* book as an input, which the b=0 selection never
used, so three construction details are degrees of freedom and are fixed here
rather than after seeing results:

1. **Retentions are resolved first, on both legs, then vacancies are filled.**
   A held long with rank ≤ k+b is retained; a held short still inside the
   bottom k+b is retained; remaining long slots are then filled from the
   best-ranked unused names and remaining short slots from the worst-ranked
   unused names. Resolving both legs' retentions before either fills keeps the
   rule leg-symmetric — filling longs first would silently give the long leg
   priority over a contested name.
2. **No name may be used twice**, and a name is only ever retained on the side
   it is actually held. This is what keeps b=3 well defined where the zones
   touch (§45.2).
3. **Leg order follows momentum rank, not hold status.** The long leg is
   ordered best-first and the short leg worst-first before `rank_weights`
   applies the [0.5x, 1.5x] ramp, so a retained name now at rank 7 takes the
   smallest long weight rather than inheriting the weight it had. **The buffer
   changes which names are held, never the weight profile.**

A held name that has left the candidate set entirely — dropped by the
liquidity cap, the funding-presence filter, a missing or misaligned bar, a
delisting — is **not** retained at any `b`. Retention requires a current rank,
and there is no imputing one.

### 45.2 The three buffers, and the geometric ceiling

`b` is chosen from universe geometry, **not** from which value performs best.
B trades the top-15 PIT majors with N=10, so k=5.

- **b = 1** — exit at rank > 6. Minimal hysteresis.
- **b = 2** — exit at rank > 7. Moderate.
- **b = 3** — exit at rank > 8. The §23 example from the original deferral.

**The ceiling is arithmetic.** With M candidates the long hold-zone is ranks
`1 … k+b` and the short hold-zone is ranks `M−k−b+1 … M`. They meet when
`2(k+b) ≥ M+1`. At k=5, M=15 that is `b ≥ 3`: at **b=3 the two zones touch at
exactly rank 8** — rank 8 from the top is rank 8 from the bottom in a 15-name
universe — and at b=4 they overlap by three ranks, where a name could be
"still a long" and "still a short" at once. **b ≤ 3 is a geometric constraint,
not a tuning choice**, and b=3 already sits on its boundary. M falls below 15
on days when names lack funding history or an aligned window, and there b=3
overlaps rather than touches; §45.1's rules 1–2 resolve that deterministically,
with no tie-break on performance.

### 45.3 Trial accounting

Three buffers = **three trials** (b=1, 2, 3), each reported at both fee
schedules. Each is logged with `status: started` at a clean commit before it
runs; an errored run still spends its trial.

**The b=0 baseline is not a new trial** — it is the frozen §44 config, already
logged, same config hash. It is nonetheless **re-run** here because the paired
bootstrap needs its daily series on identical dates, and that re-run **must
reproduce §43.6 exactly** (Sharpe 0.835, 1241 rebalances, skip 21.55%, maxDD
17.03%). If it does not, **stop**: the buffer code has moved the b=0 path and
every comparison would be against a shifted baseline.

Budget **12 → 15 of 25.** Inside the expanded budget; no further expansion.

### 45.4 The selection rule — all three conditions, fixed before running

A buffer **wins** and becomes the new deploy candidate only if, on train:

1. **Net Sharpe improves over b=0 by a paired-bootstrap margin whose 90% CI
   excludes zero.** Buffer and baseline run on the same days, so the difference
   series cancels common market noise; the two are never bootstrapped
   independently and subtracted (§26). **A point-estimate improvement is not
   enough.** Prior candidate improvements that could not clear a paired CI were
   correctly not adopted. The comparison is on the **run's net Sharpe** —
   b=0's is **0.835** (§45.0; the 0.651 quoted in the stage document is the
   demeaned column).
2. **Turnover actually falls, and the boundary-crossing share falls with it.**
   The mechanism must do the thing it claims. A Sharpe gain with flat or rising
   boundary-crossing turnover is not the buffer working.
3. **The mechanism stays clean: drift fraction < 30% and demeaned Sharpe > 0.**
   **This is the specific failure mode to watch.** A buffer holds names longer,
   and §41 established that *holding* is how this book reverts to
   drift-harvesting: the 14%/$400 config skipped 51.8% of its rebalances and
   its Sharpe was 126% drift with a **negative** demeaned Sharpe. A buffer
   could lift Sharpe the same way — by sitting on drifting names — and that is
   not tradeable edge. **If drift rises toward the §41 pattern as `b`
   increases, that is disqualifying regardless of Sharpe.**

**If several buffers pass, take the SMALLEST `b` that does** — minimal
intervention, least overfit surface. **Not the highest Sharpe.**

**If none passes, b=0 stands** and the deployment config is unchanged.

### 45.5 What is reported per buffer, against b=0 on train

The b=0 reference is §43.6's 10% @ $800 train row: **Sharpe 0.835**, demeaned
**0.651**, drift **22%**, max drawdown **17.03%**, skip rate **21.55%**,
realised vol **9.28%**, **1241** rebalances, ann return **7.60%**.

Per buffer:

- Net Sharpe, price PnL, funding PnL, each with a **paired-bootstrap 90% CI
  against b=0** on identically-dated days
- Turnover multiple, and the **boundary-crossing / adjustment split**, against
  b=0's own split measured on the same run (§32.4 measured 67.9% on the earlier
  config; the b=0 figure for *this* config is computed here, not assumed)
- Fee drag as a share of price PnL, both fee schedules
- **Drift fraction and demeaned Sharpe** — the §41 disqualifier
- Skip rate and realised vol — a buffer changes which names are held, which can
  change the floor interaction; confirm it does not re-break
- Max drawdown, against the **20% cap the vol was selected under** (§43.3).
  Holding longer can deepen drawdowns.

### 45.6 The reading — fixed before running

| Outcome | Meaning |
|---|---|
| A buffer improves net Sharpe (paired CI excludes 0), turnover falls, drift < 30% | **A real improvement.** The smallest passing `b` becomes the deploy candidate — and then needs its own 2024 validate before the holdout, because a new config cannot inherit b=0's |
| Buffer lifts Sharpe but drift rises toward the §41 pattern | The gain is drift-harvesting from holding, not a turnover saving. **Reject** — this is the trap §41 established |
| Turnover falls but the net-Sharpe CI includes zero | The fee saving does not survive the noise. **b=0 stands**; the buffer is not worth the added parameter |
| No buffer passes | **b=0 is the deployment config. The last strategy question is closed** |

**Nothing in §45.2–§45.6 is adjusted after seeing results.**

### 45.7 If a buffer wins — the consequence, stated now

A winning buffer is a **new configuration** and **cannot inherit §44's 2024
validation.** It would need its own single validate on 2024 (trial 15 → 16)
before being holdout-eligible. **That validate is not run in this stage.**

### 45.8 What this stage does not do

- Does not choose `b` by which performs best — smallest that passes, or none
- Does not adopt a buffer on a point estimate without the paired CI
- Does not accept a Sharpe gain that arrives with rising drift
- Does not exceed b=3 (§45.2 geometry)
- Does not validate a winning buffer on 2024
- Does not touch the holdout

### 45.9 RESULT — no buffer passes. b=0 stands. The last strategy question is closed.

Trials 13, 14, 15 logged `status: started` at commit `1851b5e` on a clean tree
before execution. **Budget 15 of 25.** Holdout untouched, 2024 not run.

**Baseline check passed first:** the b=0 re-run reproduces §43.6 exactly on all
six pinned figures (Sharpe 0.8351, 1241 rebalances, skip 21.55%, maxDD 17.03%,
realised vol 9.28%, demeaned 0.6513). The buffer code did not move the frozen
path, so every comparison below is against the real baseline.

```
 b  sharpe  demean  drift  annret   maxDD    vol    skip  rebal   turn  cross%  drag U  drag C
 0  +0.835  +0.651    22%   7.60%  17.03%  9.28%  21.55%   1241   50.6   56.0%   32.9%   23.7%
 1  +1.031  +0.845    18%   9.64%  12.62%  9.34%  21.30%   1245   45.2   50.0%   24.5%   17.6%
 2  +0.944  +0.991    -5%   8.81%  14.18%  9.41%  21.30%   1245   40.8   45.0%   24.3%   17.5%
 3  +1.079  +0.985     9%  10.29%  10.08%  9.49%  21.05%   1249   38.5   42.7%   20.3%   14.6%

 b   price PnL  funding PnL  fees USDT  fees USDC
 0     +275.37       +70.63      90.70      65.30
 1     +346.47       +71.40      84.74      61.01
 2     +306.98       +68.81      74.70      53.78
 3     +363.78       +68.88      73.79      53.13
```

**Paired bootstrap vs b=0**, 1,381 identically-dated days, 2,000 resamples,
90% CI, block length 11.1 days from the difference series' own autocorrelation:

```
  b=1   Sharpe difference +0.1963   90% CI [-0.0310, +0.4431]   straddles zero
  b=2   Sharpe difference +0.1093   90% CI [-0.1548, +0.3697]   straddles zero
  b=3   Sharpe difference +0.2442   90% CI [-0.1201, +0.6166]   straddles zero
```

**§45.4 applied:**

```
  b=1   1 NO   2 ok   3 ok
  b=2   1 NO   2 ok   3 ok
  b=3   1 NO   2 ok   3 ok
```

**No buffer satisfies all three. b=0 is the deployment config.** The §45.6 row
that fires is *"turnover falls but the net-Sharpe CI includes zero — the fee
saving does not survive the noise; b=0 stands, the buffer is not worth the
added parameter."*

### 45.10 The mechanism claim is CONFIRMED — and that is not the same as the buffer working

Condition 2 passed at every width, decisively and monotonically:

```
                    b=0     b=1     b=2     b=3
  turnover (ann)   50.6x   45.2x   40.8x   38.5x     -24% by b=3
  boundary share   56.0%   50.0%   45.0%   42.7%     -13.3 points
  fees USDT        90.70   84.74   74.70   73.79     -$16.91
  fee drag USDT    32.9%   24.5%   24.3%   20.3%     -12.6 points
  fee drag USDC    23.7%   17.6%   17.5%   14.6%     -9.1 points
```

Hysteresis does exactly what §0 of the stage document claimed it would: it
stops names round-tripping at the rank-k edge, and the fee drag that has been
the dominant friction since the project began falls by more than a third. That
question is now answered affirmatively and does not need asking again.

**It is still not enough**, because the rule was written on net Sharpe and the
net-Sharpe difference cannot be distinguished from zero. That is the intended
behaviour of the rule, not a malfunction of it.

Note also that b=0's own boundary-crossing share is **56.0%**, not §32.4's
67.9%. That 67.9% was measured on config A (uncapped universe, $400); §45.5
required computing this config's own split rather than inheriting the older
number, and the difference is why.

### 45.11 What makes this rejection uncomfortable, stated rather than smoothed

Every buffer improved almost every reported number, several of them a lot:

- **Sharpe** up at all three widths (+0.20, +0.11, +0.24)
- **Max drawdown** down at all three: 17.03% → 12.62% / 14.18% / **10.08%**,
  against the 20% cap the vol was selected under
- **Annual return** up: 7.60% → 9.64% / 8.81% / **10.29%**
- **Fee drag** down by a third
- **Skip rate and realised vol essentially unchanged** — the floor did not
  re-break, which §45.5 required confirming and which was a live risk

And the rule rejects all of them. That is the rule working as designed: three
prior candidate improvements in this project failed a paired CI and were
correctly not adopted, and adopting this one on a point estimate after
pre-registering the CI test would make the pre-registration decorative.

**Two things about the test design that belong on the record, neither of which
changes the verdict:**

1. **The paired test mixes a stable channel with a noisy one.** The buffer's
   benefit arrives through *costs* (turnover, fees), which move monotonically
   in `b` and by large margins. Its effect on *price PnL* (+275 → +346 → +307 →
   +364) is not monotone and is the noisy part. The Sharpe difference sums the
   two, and the noise dominates the CI. A test that isolated the cost channel
   would be a different test with a different pre-registration; **it is not run
   here, and inventing one after the rule has spoken would be exactly the
   goalpost-move this project does not make.**
2. **"Smallest b" does not mean "small effect".** The buffer changes the book
   on **97.2% of days at every width**. Once one retention decision differs the
   books diverge and stay diverged — this is a path-dependent portfolio, not a
   thin overlay. The pairing still removes shared market noise, which is why
   the CIs are far tighter than independent bootstraps would give, but b=1 is
   not a small perturbation of b=0.

### 45.12 Two numbers that should not be over-read

**b=2's drift fraction is −5%** — its demeaned Sharpe (0.991) *exceeds* its
real Sharpe (0.944), meaning stripping per-symbol drift would have *helped*.
Taken at face value that is a book earning more than all of its return from
trend. It should not be taken at face value: the drift fractions across widths
run **22%, 18%, −5%, 9%**, which is not monotone and not smooth. This is the
same measurement instability §43.7 recorded across vols — small changes in
which names are held flip whole rebalances and move the drift estimate several
points. **The honest reading is that all four sit in a broad clean band, and
that the ordering within it is not resolvable**, not that b=2 is drift-free.

**The §41 trap did not fire.** The specific worry in §45.4 condition 3 was that
a buffer would lift Sharpe by holding drifting names. The opposite happened:
drift fell at every width (22% → 18% / −5% / 9%) while Sharpe rose. Whatever
the buffers are doing, they are not harvesting drift, and the demeaned Sharpe
rose at every width too (0.651 → 0.845 / 0.991 / 0.985). Recorded because it
was the pre-registered failure mode and it is worth knowing it was absent.

### 45.13 Status — the strategy is frozen

**The deployment config is unchanged and now final on train+validate
evidence:** B, top-15 PIT majors, `lookback=14`, `skip=0`, N=10, k=5, **10%
vol**, **$800**, 3x gross cap, beta-neutral, +1min fill, 5 bps/side,
**rank_buffer = 0**.

Budget **15 of 25**. Holdout **sealed and untouched**: `holdout_log.json` does
not exist, `trials.jsonl` contains zero holdout rows. 2024 not re-run in this
stage; no buffer earned the validate §45.7 would have required.

Per §45.6 row 4, **the last open strategy question is closed.** What remains
before live capital is not strategy work: measured slippage (the 5 bps is an
n=1 synthetic figure, not a measurement), the testnet paper harness, and the
holdout — which remains one look, ever, and remains the user's decision to
spend or not.

## 46. Stage 10 — the paper phase: PRE-REGISTERED SUCCESS CRITERIA

**Recorded 2026-08-29, BEFORE day one of paper trading.** Committed in its own
commit ahead of the first paper day and ahead of any operational result.

**No trials are consumed** — paper is not a backtest. Budget stays **15 of
25**. Holdout **sealed**. The frozen config is not modified by anything in this
phase.

### 46.1 What this phase can and cannot establish

**CAN:** that the live code path faithfully implements the strategy that was
validated; that orders place, fill and reconcile; that funding is recorded
correctly; that the watchdog, kill switch and crash recovery work; that the
cost-measurement pipeline produces data.

**CANNOT:** fill quality or slippage (testnet books are thin and synthetic),
real fee rates, or anything about strategy performance. Four weeks of PnL has
t ≈ 0.2. **A paper phase "making money" means nothing and "losing money" means
nothing.** Success here is operational and is defined in §46.2 before it
starts, so that no operational result can be reinterpreted after the fact.

### 46.2 The six criteria — the phase PASSES after ≥ 28 consecutive calendar days in which

1. **Shadow reconciliation matched every trading day.** Zero unexplained
   decision mismatches between the live path and the backtester run on the same
   inputs; any explained mismatch fixed and re-verified.
2. **Funding accounting reconciles** to the exchange's income history within
   **$0.01 cumulative**.
3. **No unrecovered crash.** Every failure — including at least one
   *deliberately induced* kill of the process mid-cycle — recovered to a
   correct book through `reconcile` with no manual repair.
4. **All four §46.4 fixes demonstrated** with induced-condition tests, each
   logged with evidence.
5. **Kill switch and watchdog verified armed.** A heartbeat-gap test fires the
   alert, and drawdown is computed on the **re-baselined** equity series
   (§46.5).
6. **Zero silent errors.** Every exception surfaced in the daily report.

**PnL is explicitly NOT a criterion** and will not be reported as a headline
(§21 standing rule). If all six hold for 28 days, the machine is validated —
the strategy is not, and never can be, by this phase.

**The clock restarts only on a criterion-1 or criterion-3 failure.** Lesser
issues are fixed and noted without restarting. Fixed now so that a bad day
cannot later be argued into or out of a restart.

### 46.3 The shadow reconciliation — the real product of this phase

Every day, after the live decision executes, the **backtester** runs on the
same inputs (the same universe snapshot and the same bars the live path
fetched) and the two decisions are compared on:

- selected names, long and short
- target weight per name, to **1e-6**
- computed betas, ex-ante vol estimate, gross leverage

**A mismatch is a same-day stop-and-diagnose**, logged with both decision
vectors. The reason it outranks everything else here: if the live path and the
research path implement different strategies, then every backtest conclusion in
this document is about a strategy that is not the one trading. This phase's
deliverable is the proof that the thing validated and the thing running are the
same thing.

### 46.4 The four Phase-2 fixes — due now (deferred at §2e 10 to "before live")

None is optional before real money, and each needs an induced-condition test on
testnet, not just an implementation:

1. **Multi-leg atomicity.** After each rebalance, compute residual beta and
   tracking error of the *actually filled* book against target. Beyond
   tolerance (|beta| > 0.15, or tracking error > 20% of gross) repair
   immediately or flatten. Induced test: deliberately reject/undersize one leg
   and verify the repair fires.
2. **Stop-execution cascade.** A stop fill triggers an immediate reconcile and
   re-hedge/flatten — not a log line. Induced test: a tight stop that fires.
3. **Funding reconstruction.** `record_day()` reconstructs the position held at
   each settlement from fill history, never from the current book. Verify on a
   day containing a rebalance shortly after a settlement.
4. **POST retry idempotency.** On timeout or 5xx after an order POST, query by
   `newClientOrderId` before any resubmit. Induced test: drop the response in a
   wrapper and verify the query-first path runs.

### 46.5 Testnet quirks the harness must survive, and the reset rule

- **Balance resets.** Testnet wipes balances periodically. If equity jumps in a
  way inconsistent with positions and recorded PnL, log `testnet_reset`,
  **re-baseline the paper equity series, and do NOT fire the kill switch.** A
  reset must never masquerade as a 100% drawdown or a windfall; the kill switch
  keys off the re-baselined series. Recorded before the phase so a reset cannot
  later be confused with a real drawdown.
- **Thin books.** Wide testnet spreads may reject or badly fill orders. All of
  it is recorded in the costlog and **none of it is tuned around** (§46.7).
- **Symbol gaps.** Testnet lists fewer symbols than production. Paper trades
  the reduced set and the gap is recorded as a **known limitation**, stated and
  not silently absorbed.

### 46.6 The cost pipeline — building the dataset that replaces the n=1 figure

Every fill records: decision price (the price the sizing assumed), submitted
price, fill price, fee paid, and the timestamp deltas decision → submit → ack →
fill. Stored with a **`venue=testnet` tag** so testnet rows can never
contaminate a future real-cost estimate.

**The numbers are not trustworthy as market measurements — the pipeline being
exercised is the deliverable.** When small real orders eventually run, the same
pipeline produces the measured slippage that replaces the 5 bps assumption
(which remains an n=1 synthetic figure until then).

### 46.7 What passing paper does and does not license

- **Does:** the machine is trustworthy; the project is ready for the holdout
  decision, and after it, small real orders to measure true costs.
- **Does NOT:** validate strategy performance (testnet PnL is noise), justify
  skipping or shortcutting the holdout, or replace the slippage measurement.

**Prohibited for the duration:** treating testnet PnL, fills or slippage as
evidence about the strategy; tuning any strategy parameter on paper behaviour;
letting a testnet balance reset fire the kill switch; logging testnet costs
without the venue tag; touching the holdout.

### 46.8 Credential handling — recorded because it is a standing hazard

Keys are **futures-testnet only**, verified by a signed call to the testnet
account endpoint (which a mainnet key cannot satisfy). They live in
environment variables sourced from a file **outside the repository**
(`~/.binance_testnet.env`), never in the repo, never in a log line, never in a
committed file. `tools/scan_secrets.py` enforces the repo half of that on
every run and in the test suite.

The keys were transmitted in plain text through a chat transcript that is
stored on disk, so they must be treated as **already disclosed** and rotated
when the paper phase ends — or sooner. They grant access to a testnet account
holding no real value, which is why this is a hygiene item and not an incident.

## 47. THE UNIVERSE HAS DRIFTED OUT OF CRYPTO — found 2026-08-29, before the holdout

Surfaced by the Stage 10 §2.3 testnet coverage check, which is a plumbing
task. It is not a plumbing finding. **It bears directly on the holdout
decision and on what "deploy the frozen config" would mean today**, so it is
recorded here in full rather than as a footnote to the paper phase.

### 47.1 What the check found

The top-15 PIT majors **as of 2026-07-31** — the universe the frozen config
would trade if started today — are:

```
rank  symbol         median quote vol (30d)   first bar    on testnet
   1  BTCUSDT              9,019,746,862     2020-01-01      YES
   2  ETHUSDT              7,116,172,903     2020-01-01      YES
   3  SNDKUSDT             2,452,567,209     2026-04-07      MISSING
   4  SKHYNIXUSDT          1,742,499,203     2026-06-02      MISSING
   5  SOXLUSDT             1,675,930,829     2026-05-15      MISSING
   6  SOLUSDT              1,249,992,982                     YES
   7  MUUSDT               1,217,500,347     2026-04-07      MISSING
   8  XAUUSDT              1,186,286,558     2025-12-11      MISSING
   9  SPCXUSDT               811,032,874     2026-05-21      MISSING
  10  XAGUSDT                731,563,138     2026-01-07      MISSING
  11  CLUSDT                 552,747,965     2026-04-01      MISSING
  12  ZECUSDT                492,370,869     2020-02-05      YES
  13  XRPUSDT                456,238,486                     YES
  14  HYPEUSDT               445,559,880     2025-05-30      YES
  15  BANKUSDT               289,827,132     2025-04-18      YES
```

**Eight of the fifteen are not crypto assets.** SNDK (SanDisk), SKHYNIX,
MU (Micron) and SOXL (a leveraged semiconductor ETF) are equities; SPCX is a
private-company proxy; XAU, XAG and CL are gold, silver and crude oil. They
are tokenised equity and commodity perpetuals, and they now occupy **53% of
the deployment universe by rank and roughly 45% of its quote volume.**

Every one of them first listed between **2025-12-11 and 2026-06-02**. None
existed during train (2019-09 → 2023-12). None existed during validate (2024).
**All of them appeared inside the holdout window (2025-01 → 2026-07).**

### 47.2 Why this is not a small thing

The universe rule — "top 15 by point-in-time median quote volume" — was
pre-registered in §33 and has never been changed, and it is behaving exactly
as written. The rule did not drift. **The market underneath it did.** The rule
selects the most-traded instruments on Binance USDS-M futures, and in 2026
those are increasingly not cryptocurrencies.

That breaks three things the validated result rested on:

1. **The strategy is no longer the one that was validated.** Cross-sectional
   momentum across {BTC, ETH, SOL, XRP, ZEC, HYPE, BANK} is one strategy.
   Cross-sectional momentum across {BTC, ETH, gold, silver, crude, Micron,
   SanDisk, SpaceX} is a different one — different factor structure, different
   correlation matrix, different reasons a cross-sectional spread would pay.
   Every number in §34 through §46 was measured on the first.
2. **The beta hedge loses its meaning.** BTC is the market proxy (§weights,
   `BTC = "BTCUSDT"`), and the book is hedged to zero BTC beta. Hedging a gold
   or crude-oil position against BTC does not neutralise its market risk; it
   adds an unrelated leg. A "beta-neutral" book that is half commodities is
   beta-neutral to the wrong market.
3. **The calendar assumption is now questionable.** `ANNUALISATION = 365` is
   justified by "perps trade every calendar day". The tokenised instruments
   trade continuously but their *underlyings* do not — equities and futures
   have weekends, holidays and settlement halts. Whether that produces gap
   behaviour the backtester models correctly is **not established**, and this
   project has never tested it.

### 47.3 What it means for the holdout — the decision this most affects

The holdout is 2025-01 → 2026-07. **These instruments listed inside it.** So a
holdout run would measure a strategy whose universe composition transforms
partway through the window: crypto-only at the start, roughly half non-crypto
by the end.

That does not make the holdout worthless, but it does change what a holdout
number would *mean*, and it changes it in a way that was not known when the
holdout rules (§29, §30, §37.5) were written:

- A **pass** would be a pass on a blend of the validated strategy and a
  different one, with no way to attribute which part produced it — and the one
  look, once spent, cannot be re-run to separate them.
- A **fail** would be uninterpretable in the same way: the edge decaying, or
  the universe changing under it, are not distinguishable from one number.

**Nothing about the holdout has been run, looked at, or touched.**
`holdout_log.json` still does not exist and `trials.jsonl` still contains zero
holdout rows. This is stated from listing dates and from the 2026-07-31
universe snapshot, both of which are outside the holdout's return data and
neither of which required running anything on it.

### 47.4 The options, none of which is taken here

Recorded so the choice is visible, and left to the user:

1. **Add an asset-class filter to the universe rule** (crypto-only), making
   the deployed strategy the validated one. It is a change to a
   pre-registered rule and would need its own pre-registration and, arguably,
   its own validate — but it is the change that makes deployment mean what the
   research says it means.
2. **Accept the drifted universe** and treat the validated evidence as
   applying to a strategy that no longer exists. Not defensible on the record
   as it stands.
3. **Split the holdout** — measure crypto-only and full-universe variants. The
   holdout is **one look, ever**; two variants is two looks, and §29's rule has
   no provision for it. Would need an explicit, deliberate amendment, and the
   temptation to report whichever looks better is exactly what the one-look
   rule exists to prevent.
4. **Re-derive the universe rule from asset class rather than volume rank.**
   The largest piece of work, and the only one that addresses the cause rather
   than the symptom.

**No option is selected and no rule is changed here.** Selecting one is a
research decision, not a paper-phase decision, and it belongs to the user.

### 47.5 Effect on the paper phase (Stage 10)

Testnet lists **7 of the 15** intended names (47% coverage), and the eight it
lacks are precisely the eight non-crypto ones — testnet carries crypto perps.
So the paper book is **crypto-only by accident**, which resembles the
validated universe more closely than production does today.

Two consequences, both recorded as known limitations under §46.5:

- **7 available names cannot support N=10** (5 long + 5 short needs 10
  candidates). The paper phase runs a reduced N with `MIN_LEG_NAMES = 3`
  respected. This is a venue constraint, not a tuning decision, and no paper
  observation may be used to justify a strategy parameter (§46.7).
- **Paper-phase book composition is not the composition of either the
  validated strategy or the current production universe.** It tests the
  machine, which is all §46.1 ever claimed for it.

## 48. Stage 11 — THE CRYPTO-ONLY UNIVERSE AMENDMENT: pre-registered 2026-08-29

**Recorded BEFORE any filter code exists.** No trials. Holdout **sealed**:
nothing in this stage reads, runs, or touches 2025+ return data. Listing dates
and exchangeInfo metadata are not return data. Budget stays **15 of 25**.

### 48.0 First, a correction to §47.5 — my coverage number was wrong

§47.5 reported **7 of 15** intended names present on testnet and said the eight
absent ones were "precisely the eight non-crypto ones". Both halves are wrong,
and the cause was a bug in `tools/testnet_symbols.py`, not in the data.

The tool filtered `contractType == "PERPETUAL"`. Binance gives tokenised
equity and commodity perps `contractType = "TRADIFI_PERPETUAL"`, so the tool
counted them as **absent** when they are listed and trading. Actual presence:

```
  present on testnet (10):  BTCUSDT ETHUSDT SOLUSDT ZECUSDT XRPUSDT HYPEUSDT
                            BANKUSDT  + XAUUSDT SPCXUSDT XAGUSDT  (TradFi)
  genuinely absent    (5):  SNDKUSDT SKHYNIXUSDT SOXLUSDT MUUSDT CLUSDT
```

So testnet **does** list some tokenised commodity and pre-market perps. The
corrected figure is **10 of 15**, and the reduced-N limitation in §47.5 rests
on a count that must be recomputed (§48.7).

**§47's finding itself is unaffected.** The drift into non-crypto was measured
from the research store's 2026-07-31 universe, which never involved testnet.
The listing dates, the eight non-crypto names, and every consequence in
§47.2–§47.4 stand exactly as written. What was wrong was one downstream
coverage count.

### 48.1 The amendment

The universe rule becomes:

> **top 15 by point-in-time median quote volume, among crypto-asset
> perpetuals.**

Everything else is unchanged. `lookback=14`, `skip=0`, N=10, k=5,
`vol_target=0.10`, `$800`, `rank_buffer=0`, 3x gross cap, beta-neutral, +1min
fill, 5 bps — **no strategy parameter is touched.** Only universe *eligibility*
is clarified.

### 48.2 The grounds

§47 found that the volume-rank operationalisation of "major cryptocurrencies"
had drifted into tokenised equities, ETFs, commodities and pre-market
instruments — 8 of the current top 15. The rule held formally while the market
moved underneath it.

Three properties make this amendment legitimate rather than a fit:

1. **It rests on listing dates and instrument metadata**, both external to any
   return series. Nothing about performance entered the decision.
2. **It was discovered by a plumbing check** (Stage 10 §2.3 testnet coverage),
   not by looking for a better universe.
3. **It is registered before the holdout**, and §48.5 proves it inert on every
   day where evidence already exists.

### 48.3 The definition — and what the metadata actually contains

§9 of the stage document forbids relying on the underlying-type metadata
without first inspecting and documenting it. Inspected 2026-08-29 against
testnet `exchangeInfo` (733 symbols), snapshotted to
`data/underlying_classes.json` and committed so the classifier and its test are
hermetic and do not depend on a network call:

```
  underlyingType     COIN 675 | PREMARKET 22 | EQUITY 21 | HK_EQUITY 6
                     COMMODITY 5 | INDEX 4
  contractType       PERPETUAL 677 | TRADIFI_PERPETUAL 40 | quarterly/weekly 16
  underlyingSubType  [] 631 | ['TradFi'] 34 | ['DEFI'] 26 | ['Pre-Market'] 16
                     | ['Pre-IPO','TradFi'] 5 | ...
```

Worked examples, exactly as returned:

```
  BTCUSDT    underlyingType COIN       subType []           contractType PERPETUAL
  ETHUSDT    underlyingType COIN       subType []           contractType PERPETUAL
  HYPEUSDT   underlyingType COIN       subType []           contractType PERPETUAL
  XAUUSDT    underlyingType COMMODITY  subType ['TradFi']   contractType TRADIFI_PERPETUAL
  XAGUSDT    underlyingType COMMODITY  subType ['TradFi']   contractType TRADIFI_PERPETUAL
  SPCXUSDT   underlyingType PREMARKET  subType ['TradFi']   contractType TRADIFI_PERPETUAL
```

**The distinction is clean and machine-readable** — better than §2 of the stage
document anticipated. A symbol is **eligible** iff all of:

1. USDS-M perpetual, `status = TRADING`, `quoteAsset = USDT` (existing rules)
2. `underlyingType == "COIN"`
3. `contractType == "PERPETUAL"` (never `TRADIFI_PERPETUAL`)
4. `underlyingSubType` contains none of `TradFi`, `Pre-IPO`, `Pre-Market`
5. not on the seeded `EXCLUDED_SYMBOLS` list (from §47.1: SNDK, SKHYNIX, MU,
   SPCX, SOXL, XAU, XAG, CL) — belt and braces, so the filter still bites if
   Binance ever relabels

**Ambiguity excludes.** A symbol present in the metadata whose
`underlyingType` is a value not in the known set resolves to
`underlying_ambiguous`, is excluded, and is logged. The default is
conservative because *the next weird listing will not be as obvious as
tokenised gold.*

**Recency is not the test.** HYPE and BANK listed in 2025 and are `COIN`:
they are **in**. Asset class was the problem, never novelty.

### 48.4 One case the definition must handle, stated before it bites

A symbol that **delisted before the metadata snapshot** does not appear in
`exchangeInfo` at all. This is a different situation from ambiguity and is
resolved differently, on purpose:

- **Live universe building:** cannot arise. The live universe is built *from*
  exchangeInfo, so an absent symbol is not tradeable and never a candidate.
- **Historical replay (the §48.5 proof, and any future backtest):** an absent
  symbol means "delisted before 2026-08-29". The seeded list and pattern
  classes still apply; if neither fires, the symbol is treated as crypto and
  the fallback is **counted and reported**, never silent.

Treating absent-from-snapshot as ambiguous-and-excluded would retroactively
delete legitimately-traded crypto symbols (LUNA among them) from the
historical universe and would break the very equivalence §48.5 exists to
prove. The count of such fallbacks is part of the proof output.

### 48.5 The no-op proof — the load-bearing step, and the stop condition

Point-in-time universe selection is re-run **with the filter** across
**2020-01-01 → 2024-12-31**, and compared day by day against the unfiltered
selection every logged run used — the full eligible ranking, not just the top
slice.

- **Zero diffs → the amendment is proven inert on train and validate.** The
  filtered strategy and the tested strategy are the same strategy everywhere
  evidence exists, so nothing needs revalidating.
- **Any diff → STOP.** Report the day and the symbols. It would mean a
  non-crypto instrument was in the historical universe after all, the premise
  is false, and the amendment is no longer free. The decision returns to the
  user before anything else happens.

The expected reason it holds: the earliest non-crypto listing in §47.1 is
**XAUUSDT on 2025-12-11**, and train ends 2023-12 with validate ending
2024-12. The proof does not assume that — it checks it.

Recorded as **Test 26**, permanent, so no future refactor can silently break
the equivalence.

### 48.6 The standing composition guard

§47's deeper lesson is that **a pre-registered rule can be broken by the world
while continuing to hold formally.** So the harness gains a guard that watches
for it happening again:

- every universe build logs each excluded symbol with its reason and its
  unfiltered volume rank
- an **alert** fires (daily report line, dashboard AMBER) when either an
  `underlying_ambiguous` exclusion occurs, or excluded instruments would have
  occupied **≥ 3 of the unfiltered top-15**
- **the guard observes and alerts; it never auto-amends anything.** A rule that
  edits itself in response to the market is the failure mode this project has
  spent eleven stages avoiding.

### 48.7 Scope, and what this amendment is NOT

**Binds from 2025-12-11**, the first excluded instrument's listing date. Inert
before that, proven not asserted (§48.5).

- **Not a response to return data.** No 2025+ series was read, run, or touched
  in this stage; the holdout is exactly as sealed after it as before.
- **Not a performance choice.** No filtered-vs-unfiltered performance
  comparison was computed, on any split, and none may be cited later. The
  amendment would stand identically if it lowered returns.
- **Not a holdout adjustment.** §29, §30 and §37.5 are unchanged. The holdout
  will simply test the strategy those rules always intended.
- **Not a revalidation trigger.** §48.5 proves nothing needs it.
- **Not a strategy change.** Parameters untouched.

The holdout decision remains **the user's**, unchanged and undecided.

### 48.8 The no-op proof FAILED on the first definition — 1,508 of 1,827 days

The definition registered in §48.3 excluded three symbols that were in the
historical universe:

```
  BTCDOMUSDT   underlyingType=INDEX
  DEFIUSDT     underlyingType=INDEX
  OMGUSDT      underlyingSubType=['Pre-IPO','TradFi']
```

Per §48.5 that is the STOP condition, and it stopped. The diagnosis matters
more than the count:

- **BTCDOM and DEFI are indices on CRYPTO assets** (BTC dominance; a DeFi
  composite), both `contractType=PERPETUAL`. STAGE11 §2 excludes an *"index on
  non-crypto assets"* — neither is one. §48.3 rule 3 excluded `INDEX`
  wholesale, which is broader than the specification it was implementing.
- **OMGUSDT is `underlyingType=COIN`, `contractType=PERPETUAL`** — OMG
  Network, a genuine crypto token — that merely carries a `['Pre-IPO',
  'TradFi']` subtype tag in Binance's metadata. SOMIUSDT (Somnia) is tagged
  the same way. §48.3 rule 4 treated the subtype as a standalone signal; it
  is not one.

So the failure was **not** evidence that the §48.2 premise is false. It was
evidence that my operationalisation was broader than the written definition.

### 48.9 The correction, and the circularity it had to avoid

The hazard here is precise and worth naming: **if the definition is edited
repeatedly until the proof passes, the definition is being selected BY the
historical universe, and the "no-op proof" proves nothing** — it becomes a
tautology that says "crypto means whatever was in the universe during train".

The correction is therefore justified by text that **predates the proof**, not
by the diff the proof produced. STAGE11 §2, written before any of this,
excludes "an equity, ETF, fund, commodity, index on **non-crypto** assets, or
fiat-referenced instrument". Under that text BTCDOM, DEFI and OMG are all
eligible, and §48.3's rules 3 and 4 were over-broad against it.

**The corrected discriminator is a single field:**

```
  NON_CRYPTO  iff  contractType == "TRADIFI_PERPETUAL"   (or on the seeded list)
```

Binance's own label for a tokenised traditional-finance perpetual. Within the
snapshot it captures the TradFi block exactly — EQUITY (21), HK_EQUITY (6),
COMMODITY (5), and the pre-IPO subset of PREMARKET (6) — and nothing else.
`underlyingType` and `underlyingSubType` are retained for **logging**, never
as exclusion triggers.

**Re-run: ZERO DIFFS across 1,827 days.**

### 48.10 A claim I made and had to withdraw

The first version of `universe_filter.py` described the seeded exclusion list
as "belt and braces" behind the metadata test. **That was false and I checked
it rather than leaving it standing.** Of §47.1's eight instruments, the
metadata catches only **three** (XAU, XAG, SPCX); the other five (SNDK,
SKHYNIX, MU, SOXL, CL) are absent from the snapshot entirely and are caught
**only** by the seeded list.

The reason is structural: **the metadata snapshot is taken from TESTNET**,
which lists a subset of production instruments. The seeded list is
load-bearing, not decorative.

### 48.11 That structural gap is a real hole, and it was live

If the snapshot cannot see an instrument, the §48.4 fallback admits it as
crypto — raising **neither an exclusion nor an ambiguity**, so the §48.6
composition guard would not fire on it either. A silent hole.

`suspicious_absences()` was written for exactly that case: a symbol **still
trading** but absent from the snapshot cannot be "delisted before the
snapshot". Run against the current universe it immediately found **seven**:

```
  symbol         unfiltered rank   listed        what it is
  BZUSDT              17          2026-04-01    Brent crude
  DRAMUSDT            18          2026-05-18    DRAM / memory
  EWYUSDT             20          2026-03-16    iShares MSCI South Korea ETF
  SAMSUNGUSDT         27          2026-06-02    Samsung
  MRVLUSDT            29          2026-05-15    Marvell Technology
  AMDUSDT             48          2026-05-06    AMD
  NBISUSDT            52          2026-05-26    Nebius
```

All seven were being classified **crypto**. Three of them (BZ, DRAM, EWY)
entered the *crypto-only* top-15 once the seeded eight were removed — so the
first "crypto-only" universe this stage produced had Brent crude at rank 9,
DRAM at 10 and a South Korea ETF at 12.

**The TradFi wave is therefore at least 15 instruments, not §47's eight.**
§47.1 enumerated what was in the top-15 on one date; it was never the full
population, and it should not be read as such.

The fix applies STAGE11 §2.3's own conservative default — *ambiguity excludes
and logs* — to this case: a symbol trading contemporaneously with the snapshot
but missing from it is `underlying_ambiguous`, excluded, logged. The §48.4
fallback now applies only where the question is unanswerable.

### 48.12 The guard's own regression, and why the proof caught it

Wiring the recency test into the historical proof **broke it again**: 1,556
days, the first exclusion being **LENDUSDT** — Aave's predecessor LEND, an
unambiguously crypto token that traded in 2020 and delisted years ago.

Asked at a 2020 reference, "is this trading now but missing from a 2026
snapshot?" flags **every symbol that was alive then and has since delisted**.
A forward-looking guard had become a retroactive deletion of the past.

The test is only *answerable* when the data and the metadata are
contemporaneous, so it now goes inert when the reference predates the
snapshot by more than `CONTEMPORANEOUS_MS` (90 days). **Re-run: zero diffs
across 1,827 days**, with the recency test active throughout.

**Three definitions were tried, and two were wrong.** Recorded in full,
because the sequence is the evidence that the proof is load-bearing rather
than decorative: each wrong definition was caught by the proof and by nothing
else, and neither was corrected by reference to the diff.

### 48.13 RESULT — the amendment is proven inert. Coverage is 100%.

```
  no-op proof   1,827 days, 2020-01-01 -> 2024-12-31   ZERO DIFFS
  excluded in that window                              0 symbols
  ambiguous in that window                             0 symbols
  historical-fallback admissions                       26 symbols, all delisted
  distinct symbols across all train/validate universes 346
```

**The filtered universe is bit-identical to the unfiltered one on every day of
train and validate.** The filtered strategy IS the tested strategy; nothing
needs revalidating. Recorded as **Test 26** (hermetic, runs off the committed
346-symbol artifact) so no refactor can silently break it.

**Refreshed testnet coverage (§5), crypto-only top-15 as of 2026-07-31:**

```
   1 BTCUSDT   2 ETHUSDT   3 SOLUSDT   4 ZECUSDT       5 XRPUSDT
   6 HYPEUSDT  7 BANKUSDT  8 DOGEUSDT  9 BNBUSDT      10 LABUSDT
  11 AKEUSDT  12 ADAUSDT  13 1000PEPEUSDT  14 WLDUSDT 15 NEARUSDT

  coverage 15 of 15 (100%)   tradeable on testnet: 15
```

**The full frozen config runs on paper: N=10, k=5. The §47.5 reduced-N
limitation is RETIRED**, and §46.5 is updated accordingly. Note the reason it
retired is not that testnet gained symbols — it is that removing 8 TradFi
names and 3 unclassifiable ones promoted 11 crypto names that testnet does
list.

**Budget 15 of 25. Holdout sealed and untouched** — `holdout_log.json` absent,
zero holdout rows, and no 2025+ return series was read, run, or touched at any
point in this stage. Suites 76/76.

### 48.14 What remains open, and belongs to the user

1. **The filter cannot be fully verified without production metadata.** The
   snapshot comes from testnet by hard rule (no mainnet host may appear in
   this codebase). Production-only instruments are caught by the recency guard
   only once they reach a ranked universe. A production `exchangeInfo`
   snapshot supplied out-of-band would close this properly, and would not
   require the codebase to make a mainnet call.
2. **§47.3 is unchanged by this stage.** The holdout window still contains the
   TradFi transition. The amendment means a holdout run would now test the
   crypto-only strategy — which is the strategy the research measured — but
   the composition of the *unfiltered* market still shifted underneath the
   window, and the seven newly-found instruments make that shift larger than
   §47 described. Whether that changes the holdout decision is the user's call.
3. **The exclusion list will need maintenance.** Seven instruments were found
   by a guard written the same day. The guard alerts; it never auto-amends
   (§48.6). Someone has to look.

## 49. Stage 12 — the Phase-2 gate amendment, and the paper-run preconditions

**Recorded 2026-08-29, BEFORE the Phase-2 code is written or run.** No trials.
Budget stays **15 of 25**. Holdout sealed.

### 49.1 A standing coded rule blocks this stage, and it is not being deleted

`live/trader.py` refuses to construct a Phase-2 trader at all:

```python
if cfg.phase != 1:
    raise NotImplementedError(
        "Phase 2 (the momentum config) is refused until the grid and "
        "holdout are complete (STAGE2B B2). Phase 1 only.")
```

STAGE12 starts Phase 2 with the holdout **still sealed**, so the two are in
direct conflict. Under the project's amendment convention the later document
wins, but a *safety* gate tied to the holdout is exactly the kind of rule that
should not be quietly edited away, so the amendment is recorded here with its
reasoning before any code moves.

### 49.2 What B2 was actually protecting against, and why testnet is outside it

B2's intent was: **do not put the real strategy in front of real money before
the research is finished.** Everything about the rule points at capital risk —
it sits beside "testnet only", "no production keys", "never a headline PnL".

The paper phase risks **no capital**: testnet, play money, an account holding
5,000 fake USDT. It also cannot consume the holdout — STAGE10 §9 and NOTES
§46.7 both state explicitly that passing paper does **not** license skipping
or shortcutting the holdout, and §46.1 states paper cannot say anything about
strategy performance at all.

So the gate conflated two different things: *research complete* and *safe to
exercise the machine*. Only the first is genuinely coupled to the holdout.

### 49.3 The amendment

Phase 2 is permitted **on testnet only**. The gate is narrowed, not removed:

- `phase=2` is allowed when the client is a testnet client (which
  `assert_testnet_url` already guarantees can be nothing else — the codebase
  has no mainnet host and cannot construct one).
- **Real-money trading remains gated on the holdout decision**, which is
  unchanged and still the user's. There is no code path to it, and this
  amendment creates none.
- The refusal message and its B2 citation stay in the code, rewritten to say
  what is now refused rather than being deleted, so the history is visible in
  the file and not only in this document.

### 49.4 What the paper universe will be, and three honest limitations

The live universe is built from **testnet's own data** through the same
`compute_target_weights` path research uses (via `live/pitfeed.py`), with the
§48 crypto filter applied. Three consequences, recorded now rather than
discovered later:

1. **Testnet quote volumes are synthetic.** The liquidity ranking that selects
   the top-15 is therefore not the production ranking. Paper-phase book
   composition is not the composition of the validated strategy nor of the
   current production universe. This tests the machine, which is all §46.1
   claims for it.
2. **A candidate shortlist is applied before ranking.** Fetching daily klines
   for all ~700 listed symbols every cycle is not a reasonable REST budget, so
   the cycle pre-narrows to the top-40 by 24h quote volume and then applies
   the real top-15 rule inside that. A name outside the 24h top-40 that would
   have ranked top-15 on 30-day median volume would be missed. The margin is
   40 against 15; the deviation is recorded, not assumed harmless.
3. **The metadata gap of §48.11 still applies.** The classifier can only see
   what testnet lists. On testnet that is self-consistent — the universe and
   the metadata come from the same venue — which is a strictly better position
   than production, where they do not.

### 49.5 The day counter

Per STAGE12 B.2 the counter starts at the **first cycle that completes with
all §46 instrumentation live** — shadow reconciliation, costlog `venue=testnet`
tagging, watchdog heartbeat, `status.json`, dashboard. A cycle run before any
of those existed does not count toward the 28, and none has been counted.

Induced-failure demonstrations (§46.4) do **not** reset the counter; only an
*unexplained* shadow mismatch or an *unrecovered* crash does (§46.2).

### 49.6 Part A verdict — the dashboard was ABSENT, and is now built

Checked before building, per STAGE12 A.1: no `dashboard/` directory, no
`status.json` writer anywhere in the tree, no dashboard tests, `import
dashboard` raised `ModuleNotFoundError`. **Absent, not partial.**

Built to the STAGE10A spec: `live/status.py` (atomic snapshot) and
`dashboard/` (read-only stdlib server, 127.0.0.1 only, GET only), 13 tests
covering healthy / missing / stale / MISMATCH / testnet-reset renders, the
composition-guard AMBER line, loopback refusal, live serving, and a
concurrent-reader atomicity test.

**One deviation, stated not hidden:** STAGE10A §3 says "FastAPI (or Flask)".
Neither is installed, and adding a web framework to a project whose runtime
dependencies are numpy and requests — in order to serve one read-only local
page — works against that same section's binding constraint ("no build step,
no framework, no database — it reads files"). `http.server` meets every
non-negotiable and installs nothing.

**The atomicity test found a real bug.** On Windows `os.replace` raises
PermissionError when the destination is open, because Python's `open()` does
not request FILE_SHARE_DELETE. A dashboard reading `status.json` at the moment
the harness wrote it would have made **the harness's write fail** —
nondeterministically, and in the harness rather than in the UI. Both sides now
retry over a short window; a genuine absence still returns immediately so the
day-one page stays instant. Found by the test, not in production.

### 49.7 Part B.1 preconditions — asserted, not assumed

```
  check                          result
  46 criteria recorded           NOTES 46, dated 2026-08-29, before day one   PASS
  keys                           env vars present; scan_secrets clean over
                                 88 tracked files                             PASS
  URL guard                      mainnet hosts and testnet=False both
                                 refused; allow-list, no mainnet string       PASS
  universe filter                crypto-only active; Test 26 green;
                                 composition guard wired and reporting        PASS
  production exchangeInfo        NOT supplied -- standing limitation
                                 (48.14.1), proceeding on testnet metadata
                                 + seeded list                                GAP
  testnet coverage               558 USDT TRADING symbols, 533 crypto-
                                 eligible, 25 excluded, 0 ambiguous           PASS
  config                         frozen: lb14/skip0, N=10, k=5, vol 10%,
                                 $800, b=0, top-15 crypto, kill switch 30%    PASS
  dashboard                      serving; RED on missing/stale verified       PASS
  suites                         99/99 (76 backtest + 13 dashboard +
                                 10 phase2) + 19 live                         PASS
```

The one **GAP** is §48.14.1 and it is recorded rather than worked around: on
testnet the universe and the metadata come from the same venue, so the §48.11
hole cannot open here — anything testnet lists, testnet's exchangeInfo
describes. It remains open for production.

### 49.8 Day 1 — the cycle completed, and it SKIPPED. What that does and does not establish.

```
  universe shortlist    40 crypto names (from 533 eligible)
  composition guard     clear -- 25 excluded, 0 ambiguous, 0 in the top-15
  exchange equity       5,000 (testnet grant); paper capital 800
  decision              SKIP: below_min_notional_post_hedge
                        leg reduced to 2L/4S by BTCUSDT, DOGEUSDT, ETHUSDT, FXSUSDT
  orders placed         0
  positions             flat
  shadow                SKIP (not MATCH)
  errors                none
  status.json           written; dashboard renders AMBER
```

**The skip is the frozen config behaving exactly as measured, not a fault.**
BTCUSDT carries `MIN_NOTIONAL = 50` and ETHUSDT `20`, against an average
position of roughly `0.24 x 800 / 10 ≈ $19` at this vol. Those names cannot be
seated, the feasibility drop-loop removes them, and the long leg falls under
`MIN_LEG_NAMES = 3`.

**Checked, because it would have been a serious finding otherwise:** the
research store carries the *same* values — BTCUSDT 50.0, ETHUSDT 20.0 — so
this is **not** a live-versus-backtest discrepancy. The backtest skips 21.55%
of train days at this config (§43.6) for exactly this reason. The live path
reproduced a known behaviour of the frozen config on live data, which is
itself weak evidence the machine matches.

**What day 1 did NOT establish, stated plainly:**

- **The shadow comparison was not exercised.** It correctly returned `SKIP`
  rather than `MATCH` — a vacuous pass here would have been the Stage 2e trap
  in a new costume — but that means criterion 1 has one day of *nothing to
  compare*, not one day of agreement.
- **The execution path was not exercised.** No orders, no fills, so no
  costlog rows, no atomicity repair, no slippage capture.
- The dashboard shows **AMBER, not GREEN**, precisely because the shadow did
  not return MATCH. The light is telling the truth.

**So the checks were proven by test instead of by the venue** (`tests/
test_phase2.py`, 10 tests): the shadow detects a weight drift above 1e-6, a
different name set, a re-fetch that skips where the live path traded, reports
SKIP without ever claiming MATCH, and measures fill divergence; the costlog
tags every row `venue=testnet`; the client order id is stable within a second
so an ambiguous POST is resolvable. Shipping an unexercised check would have
been worse than shipping none.

**Day counter: 1 of 28**, per STAGE12 B.2 — the cycle completed with all §46
instrumentation live. A reader who thinks a skipped day should not count has a
fair argument; the count is recorded with exactly what it did and did not
exercise so the judgement stays available.

### 49.9 The induced-failure schedule (STAGE12 B.3) — planned, not winged

One at a time, each on a day whose previous daily report was clean. **None
resets the 28-day clock** (§46.2): they satisfy criterion 4.

```
  target date   demonstration                              evidence to capture
  2026-09-01    1. undersized/rejected leg -> atomicity     residual beta and
                   repair fires                             tracking error before
                                                            and after the repair
  2026-09-04    2. tight stop that fills -> cascade         reconcile log, book
                   reconcile re-hedges                      before/after, re-hedge
  2026-09-08    3. process killed mid-cycle -> restart      exchange book vs
                   reconciles, no manual repair             recovered book, diff
  2026-09-11    4. ambiguous POST response -> query by      the query-first call
                   newClientOrderId before any resubmit     ordering, no duplicate
```

Demonstrations 1, 2 and 4 need a **non-skipping** day to be meaningful, since
all three act on an order that exists. If the config keeps skipping, they wait
— they are not made possible by widening the book, which §46.7 and STAGE12 B.5
forbid.

## 50. Stage 13 — the $2k capital tier: PRE-REGISTERED READINGS

**Recorded 2026-08-29, BEFORE any run.** Capital re-sizing is the Stage 7
diagnostic class, so **no trials**: budget stays **15 of 25**. The $800 28-day
paper clock continues untouched. Holdout sealed; no 2025+ return data is read.

### 50.0 A gap this stage forces me to close first

STAGE13 §A.2 requires the runs to have the **crypto filter active**. It is
not: §48's filter lives in `backtest/universe_filter.py` and is used by the
live path, the no-op proof, the coverage tool and Test 26 — but
`backtest.weights.compute_target_weights` never calls it. **A backtest today
does not apply the amendment.**

That is a real gap, not a technicality: §48.1 says the universe rule *becomes*
"top 15 by point-in-time median quote volume, among crypto-asset perpetuals",
and an amendment that is not in the code is not an amendment. So the filter is
wired into the weight path before Part A runs, applied as an **eligibility
rule before the liquidity ranking** — the same position as the funding-presence
filter — so the top-15 is the top-15 *among crypto*.

**This cannot move any historical number**, and that is proven rather than
asserted: §48.5 checked 1,827 days of train and validate and found the filtered
and unfiltered rankings bit-identical, and Test 26 pins it permanently. If the
suites move, the premise is wrong and this stage stops.

Wired unconditionally rather than behind a `Config` flag: the amendment
replaced the rule, it did not add an option, and a flag would let a future run
silently opt out of it.

### 50.1 Why $2k is measured rather than assumed

At $800/10% the train max drawdown of **17.03%** was measured with **21.55% of
days skipped**, and §42.6 established that skips are *accidentally protective*
— the book sits out days it cannot size. Healing them moved the $400/14%
drawdown from 14.78% to **24.79%**. Higher capital heals skips and therefore
reveals a truer, larger drawdown.

The honest-ratio estimate at 10% vol is roughly **19.2% against the 20% cap** —
inside by 0.8 points, which is well inside this project's measurement noise
(§43.7 recorded ±5 points of floor discreteness on adjacent vols). **So
"does the 10% vol choice carry to $2k?" is genuinely open.**

### 50.2 The runs

Frozen config except capital: top-15 crypto majors (filter active per §50.0),
`lookback=14`, `skip=0`, N=10, k=5, **10% vol**, `rank_buffer=0`, 3x gross cap,
beta-neutral, +1min fill, 5 bps/side, USDT fees, **train 2020–2023**, at:

- **$2,000** — the asked-for point. BTC is marginal here: average position
  ≈ 0.24 × 2000 / 10 ≈ $48 against BTCUSDT's $50 MIN_NOTIONAL.
- **$2,500** — BTC seats unconditionally; the clean point.

$800 is **cited** from §43.6, not re-run.

### 50.3 The reading — fixed before the numbers

| Outcome | Meaning |
|---|---|
| maxDD ≤ 20% at both, drift < 30% and demeaned Sharpe > 0, skips collapse | **The 10% vol choice carries.** The higher-capital book is the same strategy, finally including BTC/ETH. Recorded as the config-of-record **for that capital tier** |
| maxDD > 20% | **The coupling bites again**: with skips healed, 10% vol breaches the cap. The vol for the $2k+ tier must be re-derived by the §43 three-condition rule — **a future free sweep, not done ad hoc here** |
| drift rises past 30%, demeaned Sharpe ≤ 0, or skips fail to collapse | Something new. **Stop and report** |

**Whatever fires, the $800/10% deployment config is UNCHANGED.** It was
validated as-is, skips and all (§44). Part A characterises a *different capital
tier*; it re-freezes nothing and re-validates nothing. Part A is a train
diagnostic and carries no out-of-sample weight of any kind.

### 50.4 Part B, and the criterion-4 interpretation fixed in advance

A second paper book at the Part-A-informed capital would run **in parallel**
with the $800 clock, on a **separate testnet account with separate keys**
(one account cannot host two books — reconciliation would see the union and
both harnesses would flag phantom mismatches), with separate `status.json`,
logs and costlog tagged `venue=testnet, book=exercise`.

**Labelled `exercise` everywhere.** Its PnL counts toward nothing, its
behaviour tunes nothing, and it is **not** the config a holdout would test.

**The criterion-4 interpretation, recorded now so it is fixed before it is
graded:** the §46.2 criterion-4 demonstrations (the four Phase-2 fixes) test
**machinery**, not a config. Demos 1, 2 and 4 act on orders, which only a
non-skipping book reliably has, so running them on the exercise book satisfies
criterion 4 **for the machinery** — the same code paths serve both books.
Demo 3 (process kill and recover) runs on **each book once**, because recovery
is per-process state.

**The $800 book keeps the official 28-day clock, unchanged.** Day 1's skip sits
inside its expected 21.55% cadence: the clock is not stalled and is not being
rescued. If the $800 book fails its criteria, that is reported — never papered
over with the exercise book's cleaner days (STAGE13 B.3).

### 50.5 What this stage does not do

- Does not treat Part A as re-validating anything — it is train, and free
- Does not re-derive the vol target if the drawdown breaches; it reports and
  stops
- Does not run two books on one account
- Does not let exercise-book results touch any criterion except demo evidence
- Does not touch mainnet, the holdout, or 2025+ return data

### 50.6 PART A RESULT — BRANCH THREE. Something new, and it is not a degradation.

Determinism check passed first: the $800 row reproduces §43.6 on all six
pinned figures (Sharpe 0.8351, 1241 rebalances, skip 21.55%, maxDD 17.03%,
realised vol 9.28%, demeaned 0.6513). The §50.0 filter wiring is therefore
inert on the real store as well as on the synthetic fixtures.

```
  capital   maxDD      DD date    skip  rebal  realised  short  drift  demean SR  sharpe
  $   800  17.03%   2023-06-28  21.55%   1241    9.28%  +0.72%    22%    +0.651  +0.835
  $ 2,000  18.43%   2023-09-02  15.36%   1339    9.20%  +0.80%    -7%    +0.769  +0.719
  $ 2,500  19.81%   2023-08-25  14.85%   1347    9.23%  +0.77%    18%    +0.442  +0.539

  capital  notional med       p05       min
  $   800 $      22.49 $    8.43 $    3.10
  $ 2,000 $      55.15 $   18.71 $    5.46
  $ 2,500 $      67.45 $   22.82 $    6.03
```

**Branch THREE fired** on the pre-registered skip condition: skips did not
collapse. But the reason is a decomposition finding rather than a failure, and
it corrects a premise the stage document was built on.

### 50.7 The skip premise was wrong: skips were never mostly floor-driven

STAGE13 §A.3 expected the skip rate to "collapse from 21.55% toward ~0",
because §42/§43 discussed skip rate as a measure of floor health. The reason
breakdown says otherwise:

```
  capital   universe_too_small  insufficient_candidates  below_min_notional  missing_fill_bar
  $   800                 278                       36                  26                 1
  $ 2,000                 207                       35                   0                 1
  $ 2,500                 200                       34                   0                 1
```

**The floor skips healed COMPLETELY: 26 -> 0 -> 0.** That is the thing capital
was supposed to fix, and it fixed it entirely.

But at $800 those 26 days were only **1.6% of the 21.55% skip rate.** The
dominant cause at every capital is `universe_too_small` — fewer than N=10
symbols clearing 60 days of history and the $5M median-volume floor, which is
an early-sample **data availability** constraint, not a capital one. It falls
from 278 to 200 (capital does admit more symbols through the MIN_NOTIONAL leg
of `tradeable_universe`) and then plateaus, because on those remaining days
the symbols simply did not exist yet.

**So the 21.55% skip rate at $800 was never evidence of a floor-crippled
book** in the way §41-§43 framed skip rate, and no amount of capital can drive
it to zero on this train window. Recorded because the same misreading could
otherwise be carried into a future sweep.

### 50.8 What actually got worse: the drawdown hugs the cap, and BOTH Sharpes fall

**Drawdown rises monotonically with capital — 17.03% -> 18.43% -> 19.81%** —
exactly as §42.6 predicted once the protective skips heal. All three are
inside the 20% cap, so the §50.3 drawdown test technically passes.

**It should not be read as a pass.** At $2,500 the measured drawdown is
**0.19 points** under the cap. §44.1 point 4 declined 12% vol at $800 for
sitting 0.53 points under this same cap, on the grounds that "the entire vol
investigation existed to escape boundary-hugging; deploying another cap-hugger
would repeat the error with open eyes." 0.19 points is a tighter hug than the
one already refused, and §43.7 measured this project's floor discreteness at
±5 points. **The margin is a small fraction of the noise.**

**Sharpe falls monotonically as capital rises: 0.835 -> 0.719 -> 0.539.** The
drift-stripped number, which is the real-edge estimate, falls too:
**0.651 -> 0.769 -> 0.442.** More capital, more names seated, *worse*
risk-adjusted result — the opposite of the intuition that the $800 book is
crippled by its floor and would improve if freed from it.

Two honest qualifications on that, both of which cut against over-reading it:

- **Sharpe is not comparable across capital here.** §39.7 established that the
  floor breaks vol-invariance, and the same discreteness breaks size-
  invariance: different capitals trade different day-sets and different books.
  Reported, never selected on (§7A.3).
- **The drift measure is wildly unstable across this range.** It runs
  **22% -> -7% -> 18%**, and at $2,000 the demeaned Sharpe (0.769) *exceeds*
  the real one, meaning stripping per-symbol drift would have helped. Between
  two capitals only $500 apart the demeaned Sharpe moves **0.33**. That is not
  a measurement precise enough to choose a tier on, and it is the same
  instability §43.7 and §45.12 recorded.

### 50.9 Seating: the book does become "majors including BTC"

```
  capital  symbol   seated days  dropped(traded)  dropped(skipped)
  $   800  BTCUSDT            1                0                 0
  $   800  ETHUSDT           61                9                10
  $ 2,000  BTCUSDT           78                4                 0
  $ 2,000  ETHUSDT          612               13                 0
  $ 2,500  BTCUSDT          232               17                 0
  $ 2,500  ETHUSDT          676                8                 0
```

**At $800 BTCUSDT is seated on exactly ONE day out of 1,241 rebalances.** The
$800 book is, in practice, a book that cannot hold Bitcoin — its $50
MIN_NOTIONAL against a ~$19 average position. ETH seats on 61 days (5%).

At $2,500 BTC seats on 232 days (17% of rebalances) and ETH on 676 (50%). So
the answer to §A.3's question is **yes**: the higher-capital book genuinely
becomes majors-including-BTC.

**And that is the same fact as §50.8's falling Sharpe.** The book that seats
BTC and ETH is the book with the lower risk-adjusted return. Whether that is
BTC/ETH carrying less cross-sectional momentum than the alt tail, or the beta
hedge behaving differently once the hedge reference is itself a position, is
**not established here** and would need its own attribution.

### 50.10 The reading, and what is NOT being done

**Branch THREE: stop and report.** Per §50.3 that is where this stage ends.

- **The $800/10% deployment config is UNCHANGED.** It was validated as-is,
  skips and all (§44). Part A is a train diagnostic, carries no out-of-sample
  weight, re-freezes nothing and re-validates nothing.
- **The vol target is NOT re-derived here.** §50.5 and STAGE13 §"Do not"
  forbid it, and the drawdown did not technically breach in any case. If a
  $2k+ tier is ever wanted, its vol must come from the §43 three-condition
  rule as a separate free sweep — and §50.8's instability says that sweep
  should expect to find the same ±5-point noise it found before.
- **Part B is NOT wired.** STAGE13's order of work gates it on "if DD <= 20%",
  which is literally satisfied, but §50.3 branch three says stop and report,
  and the $2,500 drawdown hugs the cap tighter than a configuration this
  project has already declined for hugging it. Standing up a second paper book
  at a capital whose own diagnostic returned "something new" would be
  proceeding past a stop condition.
- **No second-account keys are requested**, since Part B is not being wired.

**Budget 15 of 25** — no trial spent, capital being a risk-sizing input. 2024
not re-run. Holdout **sealed and untouched**. The $800 paper clock is
uninterrupted and unaffected.

The decision this hands back: whether a higher-capital tier is worth a
dedicated vol sweep at all, given that its most-seated version measures a
*lower* drift-stripped edge than the $800 book that is already validated.

## 51. Stage 14 — production metadata, and the standalone runner: PRE-REGISTERED

**Recorded 2026-08-29, BEFORE any Stage 14 code.** No trials; budget stays
**15 of 25**. No strategy parameter changes. The $800 paper clock continues.
Holdout sealed.

### 51.1 Provenance of the production snapshot

The user supplied a production `fapi/v1/exchangeInfo` dump **out of band** and
committed it to the repo at `exchangeInfo.json`.

```
  serverTime      2026-08-28 09:36:55 UTC
  symbols         882          (testnet snapshot: 733)
  underlyingType  COIN 699 | EQUITY 148 | HK_EQUITY 12 | COMMODITY 8
                  | KR_EQUITY 8 | INDEX 3 | PREMARKET 2 | CN_EQUITY 2
  contractType    PERPETUAL 698 | TRADIFI_PERPETUAL 180
                  | CURRENT_QUARTER 2 | NEXT_QUARTER 2
  status          TRADING 751 | SETTLING 130 | PENDING_TRADING 1
```

**The no-mainnet-host rule is intact and is not being relaxed.** The file
arrived as a file. No code fetches it, no production hostname enters the
codebase, and `test_no_mainnet_anywhere_in_live` continues to enforce that.
Refreshing this snapshot is always a user-supplied file, never an in-code
fetch.

**Two metadata sources, with fixed precedence:**

- **Production** is authoritative for *classification* — it sees every listed
  instrument, which is precisely the §48.11 blindness being closed. 180 TradFi
  perpetuals against testnet's 40.
- **Testnet** remains authoritative for *tradeability* — what the paper book
  can actually hold.

### 51.2 A classifier gap the production file exposes, fixed before it is used

Production carries two `underlyingType` values the testnet snapshot never
had: **`KR_EQUITY` (8)** and **`CN_EQUITY` (2)**. Under §48.3 as written both
are outside `KNOWN_UNDERLYING_TYPES`, so SAMSUNGUSDT and SKHYNIXUSDT would
classify **`underlying_ambiguous`** rather than `non_crypto`.

Both outcomes exclude, so no book changes — but the *reason* would be wrong,
and STAGE14 §A.2.1 requires these to classify **by metadata**, not by an
ambiguity fallback. An ambiguity would also fire the §48.6 composition guard
spuriously, every single day, which is how a guard gets ignored.

Two corrections, both recorded before the run:

1. `KR_EQUITY` and `CN_EQUITY` join `KNOWN_UNDERLYING_TYPES`. They are
   regional equity labels of the same kind as `HK_EQUITY`, already present.
2. **The `TRADIFI_PERPETUAL` test moves ahead of the unknown-type test.**
   `contractType` is the discriminator (§48.9); checking it first means a
   future `XX_EQUITY` label is classified correctly on its contract type
   instead of falling into ambiguity, while a genuinely unknown type on a
   *`PERPETUAL`* contract still goes ambiguous. Strictly better coverage with
   no loss of conservatism.

**The no-op proof must still return zero diffs after both changes.** If it
does not, the change was not the safe reordering it appears to be, and this
stage stops (§48.5 remains the stop condition).

### 51.3 Snapshot staleness — the rule, fixed now

A metadata snapshot ages, and a stale one silently loses the ability to see
new listings — the §47 failure mode, one level up.

**When the production snapshot is older than 30 days, the dashboard
composition line goes AMBER with "metadata snapshot stale — refresh
advised."** It is a prompt, never an automatic action: the guard observes and
alerts and never auto-amends (§48.6), and refreshing is a user-supplied file.

### 51.4 The missed-cycle policy — fixed BEFORE the runner is built

A local machine will sometimes be off or asleep at 00:00 UTC (09:00 KST).
Deciding this in the moment would be deciding it after seeing which answer
flatters the clock, so it is fixed here:

| Situation | Action | 28-day clock |
|---|---|---|
| Started within a **2-hour grace window** of the scheduled cycle | run a **`late_cycle`**, marked as such in the daily report. Decisions use the same close-gated inputs — unchanged; fills happen later and the fill-divergence measurement simply records that | **counts normally** |
| Beyond the grace window | log **`missed_cycle`**, hold the book, next regular cycle proceeds normally | **PAUSES** — the day does not count, and the count does not reset |
| Unrecovered crash, or unexplained shadow mismatch | the existing §46.2 rules | **RESETS** |

The distinction that matters: **a host that was switched off is not a
failure of the machine under test.** It produces no evidence either way, so it
neither credits nor destroys the count. A crash or a mismatch *is* evidence
about the machine, and those still reset. Recorded so the eventual 28-day
verdict cannot be argued retroactively in either direction.

### 51.5 What the runner is, and what it is not

**Is:** a venv, a launcher, and a Windows Scheduled Task that starts a
supervisor at logon and at boot. The supervisor runs the dashboard
continuously and the cycle once daily, writes a heartbeat, restarts a crashed
child with exponential backoff to ~5 minutes, holds a **single-instance lock**
so a second launch refuses loudly rather than creating a twin trader, and
shuts down cleanly on console close.

**Is not:** a packaged single-file executable. Per STAGE14 §B.1 that is
declined by default — it invites antivirus false positives and build
fragility for no functional gain — and is available only if the user asks.

**Keys stay outside the repo tree** (`~/.binance_testnet.env`, already the
arrangement), the installer never writes them anywhere, and `scan_secrets`
must stay clean.

**Still testnet-only.** The supervisor runs the same `TestnetClient` that
cannot be pointed at a production venue. Nothing in this stage creates a path
to real money; that remains gated on the holdout decision (§49.3).

### 51.6 PART A RESULT — §48.14.1 is CLOSED

```
  classification of all 882 production instruments
    crypto        702      underlyingType=COIN 699, INDEX 3
    non_crypto    180      TRADIFI_PERPETUAL 172, seeded list 8
    ambiguous       0
```

- **All seven §48.11 symbols now classify BY METADATA**, not by the recency
  guard's ambiguity fallback: BZ (COMMODITY), DRAM/EWY/MRVL/AMD/NBIS (EQUITY),
  SAMSUNG (KR_EQUITY) — every one `contractType=TRADIFI_PERPETUAL`.
- **The seeded five are now redundant.** With the seeded list bypassed, SNDK,
  SKHYNIX, MU, SOXL and CL are all still caught on contract type. §48.10
  recorded the seeded list as "load-bearing, not belt-and-braces"; with
  production metadata it is belt-and-braces at last. It stays in place anyway,
  so the filter keeps biting if Binance ever relabels.
- **Zero testnet-vs-production disagreements** across the 700 symbols both
  snapshots carry. The §48.10 hazard is enumerated and it is empty.
- **Zero still-invisible symbols.** Of the 182 instruments production carries
  that testnet does not, **177** were invisible to the testnet-only classifier
  and fell through as "crypto" — and **142 of those are positively TradFi**:
  AAPL, ADBE, AMAT, ASML, AVGO, BABA, ARM, ANTHROPIC and 134 more.

**So the TradFi wave is 180 instruments — 20% of the exchange — not §47's
eight nor §48.11's fifteen.** §47.1 enumerated one day's top-15 and should
never have been read as the population; §48.11 doubled the count and was still
an order of magnitude short. Each successive look has found more, which is the
argument for the standing guard rather than for a fixed list.

**The no-op proof was re-run and still returns ZERO DIFFS over 1,827 days**;
the historical-fallback count fell 26 -> 19 as production metadata covered
seven more delisted symbols. Test 26 green. **History did not move.**

### 51.7 PART B RESULT — the runner is built, installed and running

Installed at `C:\Users\ASUS TUF\Desktop\App`:

```
  install.bat     one-time: venv, deps, credential check, clock check,
                  power-settings advice, auto-start registration, and a
                  verification tick. Idempotent -- run twice, one entry.
  start_bot.bat   double-click to run. Loads keys from OUTSIDE the repo.
  stop_bot.bat    clean shutdown via the lock file's pid.
  RUNBOOK.md      one page: start, know-it-is-alive, stop, update, logs,
                  the three lights, and the two rules of thumb.
  logs/           rotating, 5 MB x 7.
  .venv/          numpy + requests only.
```

**Auto-start fell back, and the fallback is the honest one.** `schtasks
/create` returns *Access is denied* on this machine without elevation, so the
installer now tries the Scheduled Task first and drops to a **Startup-folder
shortcut** (no admin required) when that fails, reporting which mechanism is
active. Start-at-boot and restart-on-failure still need an elevated shell and
are documented in the runbook rather than silently skipped. A missing
auto-start is a warning, never a failed install.

**Verified live, not merely tested:**

```
  preflight ok: venue https://testnet.binancefuture.com, credentials present
  dashboard serving on http://127.0.0.1:8787          (health: 200)
  supervisor up (pid 50464). next cycle 2026-08-30 00:00:15Z
  MISSED cycle for 2026-08-29 -- host past the 2h grace window.
      Book held; counter PAUSES at 0 (NOTES 51.4)
```

**The single-instance lock was proven against a real second launch**, not just
in the unit tests: `start_bot.bat` run again exits **2** with *"another xsmom
supervisor is already running (pid 50464) ... two supervisors would place the
same orders twice"*, and the original process and its lock are untouched.

One observation worth recording so it is not mistaken for a bug later: the
process list shows **two** `python.exe` entries with identical command lines.
They are a parent/child pair — the venv's launcher stub re-execing the real
interpreter — and only one acquires the lock. One logical supervisor.

Suites **115/115** (99 + 16 runner tests). Secret scan clean over 103 tracked
files; no key material anywhere in the repo, and the installer writes none.

### 51.8 The 28-day counter is reset to ZERO, and giving up a day is the right direction

`clock.json` and `status.json` disagreed on the count, and the disagreement is
worth recording rather than quietly picking a side.

Stage 12's day 1 was a **manual** cycle run at **05:02 UTC on 2026-08-29** —
about five hours past the 00:00:15 scheduled instant. Under §51.4, registered
earlier today and *before* the runner existed, that is beyond the 2-hour grace
window and classifies as **`missed_cycle`**, not a counted day. STAGE12 B.2
also starts the counter at "the first cycle that completes with all §46
instrumentation live", and the scheduler and clock accounting are part of that
instrumentation now and did not exist then.

**So the counter is 0 and the 28 days begin with the first supervisor-run
cycle**, scheduled for 2026-08-30 00:00:15 UTC. `status.json` was aligned to
`clock.json` with the reason recorded in its anomaly feed.

This gives up a day that had already been claimed. That is the conservative
direction, and it is the direction a policy registered in advance should push
in when it turns out to be inconvenient — the alternative is deciding after
the fact that the rule does not apply to the day already banked.

### 51.9 Status

- **Budget 15 of 25.** No trial; nothing in this stage measures a return.
- **Holdout sealed and untouched** — `holdout_log.json` absent, zero holdout
  rows, no 2025+ return data read at any point.
- **Strategy unchanged.** No parameter was touched. Still testnet-only, still
  no path to real money, still gated on the holdout decision (§49.3).
- **The bot is running now** and will attempt its first scheduled cycle at
  **2026-08-30 00:00:15 UTC**. Today is already recorded as missed, correctly,
  because the machine was not running the supervisor at 00:00.
- **Two things depend on the user**, and neither is something I should do
  silently: setting the power policy (`standby-timeout-ac 0`,
  `hibernate-timeout-ac 0`), and — if start-at-boot and restart-on-failure are
  wanted — running the elevated `schtasks` block in the runbook.

### 51.10 Log hygiene fix, and two defects it uncovered

**The reported bug.** A missed date re-logged its WARNING on every 30-second
poll tick — **45 identical lines for one date**. The guard was
`last_cycle_date != date`, which stays true forever on a missed day;
`record_missed` deduped the *list* but nothing deduped the *log*. Now guarded
on `date not in clock.missed_days`, which is the durable record, so it stays
quiet across restarts too and not merely across ticks.

The anomaly feed previously received **nothing** on a missed day, so the
dashboard showed a stalled counter with no explanation. It now gets exactly
one entry per missed date, idempotent by construction (the feed is scanned for
that date's marker first). That is a small addition beyond pure log hygiene
and is flagged as such. **No policy and no counter changed.**

**Defect 1 — `stop_bot.bat` never shut down cleanly.** On Windows `taskkill`
without `/f` only posts WM_CLOSE to GUI windows, so a console supervisor never
saw it and was force-killed — skipping the clean-shutdown path and leaving a
stale lock. The runbook claimed stopping was clean; it was not. Stop is now a
**sentinel file** both sides agree on, with force kept as a last resort after
20 seconds. Verified live: `stop requested via stop` → `shutting down` →
`stopped cleanly; lock released`.

**Defect 2 — a stopped bot stayed RED after restarting.** `_final_status`
sets `halted=True` so a stopped bot reads as stopped, and nothing cleared it
on the way back up. After one stop/start the dashboard showed RED "supervisor
stopped" while the bot was running. A status light that cries wolf gets
ignored, so startup now clears the flag.

**And the way both were found: my own tests were writing to LIVE state.**
`test_tick_records_a_missed_day` planted a `missed_cycle` entry dated
2026-08-30 in the running bot's anomaly feed, and `test_stale_stop_file` left
`halted=True` there — which is what turned the live dashboard RED. They
patched `CLOCK_PATH` but not `STATUS_PATH`. Fixed with an **autouse fixture**
that redirects every supervisor path into `tmp_path`, so no runner test can
reach live state whether or not its author remembered. Proven by checksumming
`status.json` and `clock.json` across a full suite run: **unchanged**.

The planted entries were scrubbed from the live feed. Suites **119/119**.

**State after the restart:** supervisor up (pid 19024), dashboard healthy
(200 AMBER — one anomaly today plus the day-1 shadow SKIP, which is truthful),
lock held, `missed_days = [2026-08-29]`, counter 0, zero repeated MISSED lines,
next cycle **2026-08-30 00:00:15 UTC**.

## 52. Stage 14a — closing the criteria gaps the audit found

**2026-08-29.** No trials; budget stays **15 of 25**. **No strategy parameter
was touched.** Holdout sealed. This is safety, accounting and reporting
machinery that §46.2 and §46.4 already required and that was **not built** —
found by auditing the six criteria against the code rather than against
memory, when asked whether the phase was now just a matter of waiting.

### 52.1 What the audit found, and the worst of it

Of the six §46.2 criteria: 1 and 6 were satisfied; **2, 4 and 5 were not
implemented at all**, and the demo for 3 had not been run.

The worst item was not a missing feature but a **false claim**: `status.json`
carried `kill_switch_armed: True` and `drawdown: 0.0` as **literals**.
Drawdown was never computed, the kill switch was never evaluated in the
running path, and `live/watchdog.py` existed but nothing started it. The
dashboard advertised a safety net that did not exist — the same class of fault
as the `halted` flag in §51.10, and in the more dangerous direction.

### 52.2 The risk layer (`live/risk.py`) — criterion 5

Paper equity is now measured, not asserted:

```
  paper_equity = capital + (exchange_balance - reference_balance)
```

The account holds ~5,000 USDT of play money against an $800 book; the two are
different numbers and are no longer conflated. Drawdown is peak-to-current on
that series, and the kill switch fires `flatten_all()` and halts at 30%.

**Testnet resets re-baseline and never fire the switch** (§46.5). A balance
move that the day's own fills, fees and funding cannot explain is classified
as a reset: the reference is re-baselined, paper equity is carried across
unbroken, and the event is recorded. The threshold is deliberately generous
(max($100, 25% of capital)) because the dangerous error is the *other*
direction — misclassifying a real loss as a reset would disarm the switch
exactly when it is needed. Tested both ways.

**The watchdog now runs as a separate child process**, not a thread: the
failure it defends against is a supervisor that is alive but *wedged*, and a
thread would share the wedge. Supervised with the same backoff as the
dashboard.

### 52.3 Criterion 2 — funding, recorded and reconciled

The Phase-2 cycle previously recorded **no funding at all**. It now queries
`FUNDING_FEE`, `COMMISSION` and `REALIZED_PNL` income each cycle, records each
settlement to the costlog with the `venue=testnet` tag, and reconciles the
total against the exchange's own income history to **$0.01 cumulative**, with
a drift beyond tolerance raised as a cycle error.

### 52.4 Criterion 4 — the four fixes (`live/fixes.py`)

1. **Multi-leg atomicity.** After fills, the *filled* book is checked against
   target for tracking error (>20% of gross) and residual beta (>±0.15).
2. **Stop-execution cascade.** Exchange-side reduce-only stops are now placed
   after every rebalance — **the Phase-2 path was placing none at all**, while
   Phase 1 had them. A stop fill is detected from before/after position state
   rather than a stream event, so a stream gap cannot become a missed cascade,
   and it triggers a reconcile and re-hedge.
3. **Funding reconstruction.** The position at each settlement is rebuilt from
   fill history instead of read off the current book — the case that breaks
   the naive version is a rebalance landing seconds after a settlement.
4. **POST idempotency.** An ambiguous POST (timeout/5xx) is resolved by
   **querying by `newClientOrderId` before any resubmit**. A lost response is
   not a rejection, and blind resubmission is how a book ends up at double
   size. Deterministic filter rejections are never retried; an unresolvable
   case raises `AmbiguousPost` rather than guessing.

**A silent hole caught while wiring this:** `check_atomicity` reads
`decision.betas`, and `Decision` had **no such field** — so residual beta
would have scored 0.000 on every book and half of fix 1 would never have
fired. The shrunk betas the hedge actually executes on are now carried on the
Decision. A check that always passes is worse than no check, and this project
has been caught by that shape before (the Stage 2e vacuity trap).

### 52.5 Demo 3 run — induced kill, unassisted recovery

```
  supervisor pid 47984 -> hard-killed (no signal, no clean shutdown)
  lock left behind holding dead pid 47984
  restart: "reclaimed a stale lock from dead pid 47984 (previous run did not
            shut down cleanly); recovery goes through reconcile on the next
            cycle"
  supervisor up (pid 44480), dashboard 200, watchdog restarted
```

**No manual repair of any kind.** Honest limitation: **the book was flat**, so
the reconcile-to-a-correct-*book* half of criterion 3 is not yet demonstrated.
It must be re-run on a day that holds positions, and until then criterion 3 is
**partially** satisfied, not satisfied.

### 52.6 Criteria status — stated so the 28-day verdict is not argued later

```
  1 shadow reconciliation   RUNNING   (vacuous while the book skips -- below)
  2 funding reconciles      BUILT + running, $0.01 tolerance enforced
  3 no unrecovered crash    PARTIAL   -- kill/recover shown on a FLAT book
  4 four fixes              BUILT + unit-tested; induced demos 1/2/4 owed
  5 kill switch + watchdog  BUILT + running; watchdog gap test green
  6 zero silent errors      running
```

**The vacuity warning, on the record:** if the book keeps skipping,
criterion 1 passes having compared nothing — 28 days of skips would satisfy it
while testing nothing. A meaningful pass needs trading days in it, and demos
1, 2 and 4 also need an order to act on. Neither is a reason to widen the
book: §46.7 and STAGE12 B.5 forbid tuning on paper behaviour, and that
prohibition is not suspended by being inconvenient.

### 52.7 Status

Suites **138/138**; live state provably untouched by the suite (checksummed
across a full run). Secret scan clean. The bot is running: supervisor,
dashboard and watchdog, next cycle **2026-08-30 00:00:15 UTC**, counter 0 of
28, holdout sealed.

## 53. THE PAPER BOOK DOES NOT FORM — measured 2026-08-30, day 1 of 28

Day 1 ran **on time and clean**: preflight, feed, decision, funding query,
shadow, status, daily report, heartbeat — every piece of the machinery built
in §52 worked. Counter 1 of 28, no errors, risk baselined (5,000 account →
800 paper equity).

**And it skipped, for a NEW reason.** That prompted a measurement, and the
measurement is the important part of this entry.

### 53.1 The measurement

Replaying the last 12 testnet days through the *same* decision path:

```
  08-18  SKIP  unhedgeable_beta               long leg beta 0.805 +/- 1.239
  08-19  SKIP  unhedgeable_beta               long leg beta 0.559 +/- 0.927
  08-20  SKIP  unhedgeable_beta               long leg beta 0.485 +/- 0.923
  08-21  SKIP  below_min_notional_post_hedge  leg reduced to 3L/2S
  08-22  SKIP  unhedgeable_beta               long leg beta 0.713 +/- 0.724
  08-23  SKIP  unhedgeable_beta               long leg beta 0.682 +/- 0.738
  08-24  SKIP  unhedgeable_beta               long leg beta 0.623 +/- 0.746
  08-25  SKIP  unhedgeable_beta               long leg beta 0.643 +/- 0.744
  08-26  SKIP  unhedgeable_beta               long leg beta 0.643 +/- 0.745
  08-27  SKIP  below_min_notional_post_hedge  leg reduced to 2L/4S
  08-28  SKIP  below_min_notional_post_hedge  leg reduced to 2L/4S
  08-29  SKIP  unhedgeable_beta               short leg beta 0.269 +/- 0.662

  book formed on 0 of 12 days (0%)
    unhedgeable_beta                9
    below_min_notional_post_hedge   3
```

**A first attempt at this replay covered 25 days and reported 11
`insufficient_candidates`. That was MY artifact and is withdrawn:** the live
feed fetches only 14 days of funding history, so any as_of older than that
starves every symbol on the funding-presence filter. Only the last ~12 days
are replayable, and the corrected window is what is reported above.

### 53.2 The cause — and it is the limitation I recorded, underestimated

Beta identifiability on today's shortlist:

```
  BTCUSDT 1.000 +/- 0.000    XRPUSDT 1.521 +/- 0.124    ETHUSDT 1.367 +/- 0.106
  BCHUSDT 1.667 +/- 0.202    DOGEUSDT 1.195 +/- 0.121   BNBUSDT 0.609 +/- 0.066
  ---- and then ----
  BTRUSDT 0.369 +/- 2.565    PROMUSDT 0.737 +/- 0.767   FXSUSDT -0.037 +/- 0.171
  我踏马来了USDT 0.192 +/- 0.407  COLLECTUSDT 0.695 +/- 0.568
```

**The majors are cleanly identified. The rest is noise.** They are in the
shortlist because the live path ranks candidates by **testnet 24-hour quote
volume, which is synthetic** — junk symbols carry fake volume and outrank real
majors. The momentum ranking then picks its extremes out of that junk, and the
beta hedge **correctly refuses** to hedge on an estimate smaller than its own
standard error (the §2e 5 guard doing exactly its job).

**The hedge is not the defect. The input to it is.** §49.4 limitation 1
recorded that synthetic testnet volumes make the paper composition unlike the
validated one. That was right but too mild: it does not merely change the
composition, **it prevents the book from forming at all.**

### 53.3 What this costs, stated plainly

At 0% book formation the 28-day phase cannot produce its deliverables:

- **Criterion 1 passes vacuously.** 28 days with nothing to compare is not 28
  days of agreement. This is the Stage 2e vacuity trap arriving exactly where
  §52.6 warned it would.
- **Criterion 4 demos 1, 2 and 4 are impossible** — all three act on an order.
- **Criterion 3's book-reconcile half is impossible** (§52.5 already had it as
  partial on a flat book).
- **The costlog stays empty**, so §46.6's deliverable — the fill dataset that
  is meant to replace the n=1 5 bps slippage assumption — is never produced.

The phase would still exercise scheduling, recovery, the risk layer, funding
queries, reporting and the dashboard. That is not nothing. It is also not what
§46.2 says the phase is for.

### 53.4 The options — NOT decided here

1. **Rank the shortlist by REAL volume** (the research store carries production
   quote volumes to 2026-07-31) intersected with what testnet lists, instead of
   by synthetic testnet volume. The universe rule (§48.1) says "median quote
   volume"; testnet volume is not that quantity, so this is arguably a
   *more* faithful implementation rather than a change to the rule.
   **Recommended.**
2. **Accept 0% and run the 28 days as a plumbing exercise**, grading criteria
   1, 3 and 4 as unproven rather than passed.
3. **Raise paper capital** so BTC/ETH seat. **Rejected**: it changes a strategy
   parameter in response to paper behaviour (§46.7, STAGE12 B.5), and §50.8
   already measured the higher-capital book as carrying a *lower* drift-stripped
   edge.

**Nothing was changed.** Option 1 alters what the paper book trades, and
although the argument for it is a data-fidelity one rather than a performance
one, it was prompted by watching paper behaviour — which is close enough to
the §46.7 line that it is the user's call, not mine, and the user is away.

### 53.5 The guard against a vacuous verdict

Recorded now, so it cannot be argued at day 28: **a day on which no book
formed is not evidence for criterion 1.** The daily report already records the
skip reason for every day, so the eventual verdict must be graded on *trading*
days, and a phase with zero trading days cannot pass criteria 1, 3 or 4
however many days the counter shows.

The clock is untouched and keeps running; the counter is honest about days
elapsed, and this section is what stops those days being mistaken for evidence.

## 54. Artefact map — where everything lives

Added because three items existed only in commit messages, and because a
reader returning after a gap should not have to reconstruct the layout from
`git log`.

### 54.1 The running system

```
  C:/Users/ASUS TUF/Desktop/App/
      install.bat       one-time: venv, deps, credential + clock check,
                        power advice, auto-start, verification tick. Idempotent.
      start_bot.bat     double-click to run
      stop_bot.bat      clean stop via the stop SENTINEL (51.10). Waits with
                        `ping`, not `timeout`, because `timeout` aborts when
                        stdin is redirected -- which happens whenever a
                        scheduler invokes it.
      RUNBOOK.md        start / know-it-is-alive / stop / update / lights
      logs/xsmom.log    rotating, 5 MB x 7
      .venv/            numpy + requests only
  app_dist/             VERSIONED copy of the four App files, so `git pull`
                        can restore them and launcher changes are reviewable
                        like any other code. .venv and logs are machine state
                        and are deliberately not versioned.
```

Auto-start is a **Startup-folder shortcut**, not a Scheduled Task: `schtasks
/create` needs elevation on this machine. Start-at-boot and restart-on-failure
need the elevated block in the runbook (§51.7).

### 54.2 State the bot owns (all under `live/state/`)

```
  status.json        what the bot believes NOW; overwritten each cycle.
                     The dashboard reads only this.
  clock.json         the 28-day counter, late days, missed days, resets
  risk.json          paper equity, peak, curve, testnet resets, cum PnL
  supervisor.lock    single-instance lock: pid + start time
  stop               the stop sentinel; present only between request and exit
  heartbeat          unix time, rewritten every tick; the watchdog reads the
                     timestamp INSIDE it, never the mtime
```

### 54.3 Append-only records (repo root)

```
  paper_daily.jsonl  one daily report block per cycle (STAGE10 7): counter,
                     equity, drawdown, skips, shadow, funding, atomicity,
                     stops, composition guard, errors. The AUDIT TRAIL --
                     status.json is "now", this is the history.
  paper_log.jsonl    one line per cycle: universe size, decision, deltas,
                     positions, shadow, guard, errors
  paper_costs.jsonl  every fill and funding row, tagged venue=testnet
  trials.jsonl       the trial ledger (15 of 25 spent)
  diagnostics.jsonl  every free diagnostic: sweeps, proofs, attributions
```

`paper_log.jsonl`, `paper_costs.jsonl` and `live/state/` are gitignored: they
are machine state, not source.

### 54.4 Data and provenance

```
  xsmom.db                              the frozen Stage 1 PIT store (732 MB)
  xsmom_demeaned.db                     per-symbol demeaned copy, for drift
  data/exchangeInfo_production_*.json   the user-supplied production dump
  data/underlying_classes_production.json  derived classifier metadata (882)
  data/underlying_classes.json          testnet metadata (733)
  data/train_validate_universe_symbols.json  346 symbols, Test 26 artefact
  holdout_log.json                      ABSENT, and that is the point
```

### 54.5 Keys

`%USERPROFILE%/.binance_testnet.env` — **outside the repo**, never written by
the installer, never committed. `tools/scan_secrets.py` enforces the repo half
on every run and in the suite. Treat them as already disclosed and rotate when
the paper phase ends (§46.8).

### 54.6 Where the decisions are

This document. Every stage from §2 to §53 records what was pre-registered,
what was run, what was found, and what was corrected — including the seven
corrections this session made to its own earlier claims (§45.0, §48.0, §48.8,
§48.10, §48.12, §51.8, §53.1). The corrections are load-bearing: they are the
evidence that the pre-registration discipline is doing work rather than
decorating it.

## 55. Stage 15 — unblock, arm, instrument: PRE-REGISTERED 2026-08-30

**Recorded BEFORE any Stage 15 code.** No trials; budget stays **15 of 25**.
**No strategy parameter is changed.** Holdout sealed.

### 55.1 Part A — the shortlist ranks by PRODUCTION volume

The live candidate shortlist will rank by **production median quote volume**,
read from the research store (currently through 2026-07-31), intersected with
what testnet lists and with the §48 crypto filter. It replaces the ranking by
testnet's synthetic 24-hour volume.

**The grounds, and why this is not a rule change.** §48.1's universe rule is
"top 15 by point-in-time **median quote volume**, among crypto-asset
perpetuals". Testnet's synthetic volume **is not that quantity**. The store's
production volume is. Ranking by the fake number was never an implementation
of the rule; it was an implementation of a corrupted version of it, and §53.2
measured the consequence: junk like `我踏马来了USDT` outranking real majors,
betas with standard errors 6x their estimates, and **0 books formed on 12 of
12 replay days**. The hedge guard was right to refuse to hedge on noise; the
input to it was wrong.

So this is a **more faithful implementation of the existing rule**. It is
still a change prompted by watching paper behaviour, which is why §53.4 left
it to the user rather than doing it unilaterally, and why the argument is
recorded here in full rather than assumed.

**What it is NOT:** it is not a performance choice, it is not a parameter
change, and it does not touch `lookback`, `skip`, N, k, vol target, capital or
the buffer. Universe *eligibility and ranking source* only.

### 55.2 The 28-day counter RESTARTS at this fix

Days run under the starved shortlist were a plumbing exercise, not phase
evidence: on those days the book could not form, so criteria 1, 3 and 4 had
nothing to observe (§53.3, §53.5).

The counter sits at **day 1**, so restarting costs one day now versus arguing
a graded mixture of evidential and non-evidential days at day 28. Recorded
here so the restart is a stated decision and not a silent reset, and so the
eventual verdict rests only on days the book could actually form.

### 55.3 Volume-reference staleness

The store's volumes end 2026-07-31 and will age. **Once the volume reference
is older than 60 days, the dashboard composition line goes AMBER** with
"volume reference stale — refresh store". Refreshing is a user-run
`pitdata` update; **nothing fetches volumes in-cycle from any new source.**
The store is the reference (STAGE15 "Do not").

Today the reference is 30 days old — inside the window, so no alert.

### 55.4 Part B — alerts fire on TRANSITIONS, never on states

The §51.10 lesson is already paid for: a missed date re-logged its warning on
every 30-second tick, 45 times. An alerter that pushes on *state* would do the
same thing to a phone, and a channel that cries wolf gets muted — which is
worse than no channel, because it fails silently exactly when it matters.

```
  kill switch fired / flatten_all                      CRITICAL
  watchdog: supervisor wedged / heartbeat stale        CRITICAL
  cycle error (incl. funding-reconcile drift)          ALERT
  shadow-reconcile MISMATCH                            ALERT
  any other dashboard-RED condition                    ALERT
  reset / late_cycle / composition or staleness AMBER  INFO (daily digest)
  daily line: date, traded-or-skip, equity, day N/28   INFO (one push a day)
```

Each CRITICAL/ALERT fires **once per condition instance**, with a cooldown,
and a **single "resolved" message** when the condition clears. **A dead
Telegram API must never block or crash a cycle**: send failures are logged
locally and retried with backoff, and the cycle proceeds regardless. Trading
must not depend on a chat server being up.

Secrets (bot token, chat id) live with the other credentials **outside the
repo**; `scan_secrets` stays clean. **No message may contain a key, and none
may contain any balance beyond the paper equity number.**

### 55.5 Part C — the fault class being closed

§51.10's `halted` flag and §52.1's `kill_switch_armed: True` literal are one
fault: **reported state that is not derived from measured state.** Twice is a
pattern. A test now drives the system through distinct simulated states —
armed vs fired, drawdown zero vs positive, halted vs running, heartbeat fresh
vs stale — and asserts every safety-relevant `status.json` field **changes
accordingly**. A field that stays constant across states it should distinguish
fails. The field list lives beside the test so new fields are enrolled by
default rather than by memory.

### 55.6 Part D — collect only, adopt nothing

None of the Part D instrumentation acts on anything.

- **D.1 shadow-maker.** For every taker order actually placed, log the
  counterfactual post-only price and whether it would have traded through.
  **No maker order is placed anywhere.** The Stage 2e rule stands: no
  maker-mode result is reportable until a fill-probability model exists. This
  builds the dataset such a model would need.
- **D.2 vol-shortfall diagnosis.** Attribute the persistent ~0.6–1.1 point
  realised-vol shortfall on train, as a free diagnostic to
  `diagnostics.jsonl`. **Adopt nothing.** Any estimator change moves position
  sizes and belongs to the post-holdout era with its own pre-registration.
- **D.3 regime context.** Cross-sectional dispersion of 14-day returns and
  mean pairwise correlation to BTC, from feeds the cycle already pulls. Two
  dashboard lines and a history file. **No thresholds, no filters, no
  actions.**

### 55.7 The stop condition for Part A

If the replay after the fix still shows book formation at or near **0%**, the
stage **stops and reports**. It does not stack a second guess on top of the
first. §53's measurement was worth more than §53's hypothesis, and the same
applies here.

### 55.8 PART A RESULT — the universe is fixed. The book still does not form. STOP.

**§55.7's stop condition has fired.** The fix is fully implemented, the
universe it produces is the right one, and book formation is **still 0 of 12**.
Reported rather than followed by a third attempt.

**My first implementation of Part A was incomplete, and the incompleteness is
worth recording.** I changed the *shortlist* to rank by production volume and
re-ran: still 0 of 12. The reason was not the hypothesis but the layer —
`compute_target_weights` applies `max_liquidity_rank=15` by reading
`quote_volume` **from the store the feed builds**, which held testnet's
synthetic volume. So the cap re-ranked the corrected shortlist by the fake
number and reinstated exactly the junk the shortlist had removed:

```
  after the shortlist-only fix, the traded top-15 still contained
    AKEUSDT BEATUSDT ESPORTSUSDT LABUSDT VELVETUSDT SYNUSDT USUSDT
  unidentified betas inside the traded universe: 5 of 15
```

Completing the fix at the store layer — the feed now writes **production**
median quote volume onto each bar, so the cap ranks on the real measure — is
the same fix applied where the ranking actually happens, not a second guess.
The result is a deliberate hybrid: **live prices, production liquidity**,
which is the correct pairing because `quote_volume` is only ever read as a
liquidity measure while price drives returns.

**The universe is now demonstrably correct:**

```
  traded top-15: 1000PEPE ADA AKE BANK BNB BTC DOGE ETH HYPE LAB
                 NEAR SOL WLD XRP ZEC
  unidentified betas: 2 of 15   (was 5 of 15, and 4 of 14 on the raw
                                 synthetic ranking)
```

**And formation is still 0 of 12:**

```
  unhedgeable_beta                 9
  below_min_notional_post_hedge    3
```

### 55.9 Why — and why no further attempt is legitimate

Individual betas among the majors are well identified (SE 0.07–0.29). The
failures are at the **leg** level: the long or short leg carries a weighted
beta of 0.04–0.86 against a leg SE of 0.72–1.23.

Two things combine, and neither is a defect:

1. **Momentum selects the extremes**, and on this venue the extreme movers are
   the recently-listed alts — BANK (SE 1.373), LAB (1.887), AKE (2.515) — whose
   betas are unidentifiable on 89 days of synthetic price history.
2. **A leg's SEs add in quadrature while its betas partially cancel.** Mixing
   high- and low-beta names gives a small net beta with a large combined error,
   which is precisely the condition `unhedgeable_beta` exists to catch.

**The guard is right and the data cannot support it.** Testnet supplies ~89
days of synthetic prices; the beta hedge needs identifiable 60-day betas
across ten names, and this venue provides that for roughly the top eight
majors only — which momentum does not reliably select.

Every remaining way to force formation is a **strategy change**: relaxing the
beta guard, dropping the hedge, filtering candidates by estimation quality,
lengthening the beta window, or raising capital. §46.7 and STAGE12 B.5 forbid
all of them on paper behaviour, §55.7 forbids stacking another guess, and
§50.8 already measured the capital route as carrying a *lower* drift-stripped
edge. **None is attempted.**

### 55.10 What is kept, and the counter

**The Part A change is kept.** It is a more faithful implementation of §48.1's
unchanged rule regardless of whether it unblocked formation, it was
pre-registered in §55.1, and it demonstrably produces the correct universe.
Keeping a correct implementation that failed to achieve a hoped-for side
effect is not the same as keeping a fix that did not work.

**The counter is restarted to 0 as §55.2 registered.** The restart's *purpose*
— separating starved days from evidential ones — is not achieved while
formation remains 0, so the restart is bookkeeping honesty rather than
progress, and is recorded as such.

**The blocker now belongs to the user**, with three honest options:

1. **Accept it.** Run the phase as a machine exercise — scheduling, recovery,
   risk layer, funding, reporting, alerting all get tested daily. Grade
   criteria 1, 3 and 4 **unproven**, never passed. Nothing is learned about
   execution or slippage.
2. **Change the venue.** The blocker is testnet's synthetic 89-day price
   history, not the code. Nothing else fixes beta identification.
3. **Pre-register a deliberate paper-only deviation** (for instance a longer
   beta window on the paper book alone), argued and recorded in advance as a
   venue accommodation, with its results explicitly not transferable to the
   validated config. Legitimate only if written down *before* it is run, and
   never carried into the holdout.

**Recommended: 1 or 2. Option 3 is available but earns nothing** — a paper
phase that only passes after the strategy is bent for the venue tests a
strategy nobody intends to deploy.

## 56. Stage 16 — real market, imaginary money: PRE-REGISTERED 2026-08-30

**Recorded BEFORE any Stage 16 code.** No trials; budget stays **15 of 25**.
**No strategy parameter changes.** Holdout sealed.

### 56.1 The venue amendment — data yes, trading no

**Production Binance MARKET DATA is permitted for the paper feed**: public,
unauthenticated, read-only `GET`. **Production TRADING remains forbidden** and
holdout-gated, exactly as §49.3 left it.

**Grounds.** §55.9 established that testnet's ~89 days of synthetic price
history cannot identify 60-day betas for the names momentum selects, so the
`unhedgeable_beta` guard — working exactly as designed — blocks book formation
on 12 of 12 days. The screen is not broken; the data is. On real data the
screen behaves as the research measured. A read-only feed risks no capital and
creates no path to one.

**This narrows a rule I have enforced absolutely, so the narrowing is written
down rather than assumed.** The standing rule (B1/B7, enforced by
`test_no_mainnet_anywhere_in_live`) is that no mainnet host string appears
anywhere in `live/`. Its purpose was that **no code path can reach a venue
that settles real money**. A GET-only client with no credential, no HMAC and
no POST cannot reach one.

So the test is **narrowed, not deleted**: every trading-capable module
(`client.py`, `killswitch.py`, `trader.py`, `phase2.py`) must still contain no
production host, and the one new module that may — `live/proddata.py` — is
separately proven unable to sign or to issue any non-GET request. A host
string is permitted only where signing is impossible.

**The combination rule:** `paper_feed=production` may only pair with
`execution=simulated`. `execution=live` with any production host is a
**startup refusal**, not a warning.

### 56.2 The fill model — fixed now so nobody tunes it later

Paper fills execute at **the open of the next 1-minute bar after the
decision, plus 5 bps adverse slippage**. That is the backtester's own
assumption (§2e 2 for the +1min open, §2c 4 for the 5 bps), now applied to
live real bars rather than historical ones.

**Recorded before any fill is simulated** so that no later result can be
produced by moving the fill model toward flattery. The 5 bps remains what it
has always been: a plausible magnitude from an n=1 synthetic fill, **not a
measurement**. Stage 16 §C.2's spread capture begins collecting the evidence
that will eventually replace it, and adopts nothing.

### 56.3 Criterion interpretations — fixed before grading

Following the §50.4 precedent of fixing an interpretation before it can be
argued from a result:

```
  criterion 1 (shadow matched)      satisfied on the real-data rehearsal
  criterion 2 (funding reconciles)  ledger half already demonstrated (52.3)
                                    + daily accrual against real published
                                      funding rates
  criterion 3 (no unrecovered crash) real-data rehearsal
  criterion 4 (four fixes)          demos 1, 2 and 4 on the TESTNET demo
                                    fixture (they need real orders, which
                                    only testnet allows); demo 3 already
                                    partially shown (52.5)
  criterion 5 (kill switch/watchdog) real-data rehearsal
  criterion 6 (zero silent errors)   real-data rehearsal
```

**The honest limitation of this split, stated now:** the execution machinery
is proven on testnet orders while the strategy rehearsal runs on production
data with simulated fills. **No single environment proves both at once.** That
is a real weakening of what "the machine works" means, it is the price of
refusing to trade real money before the holdout, and it must be quoted
alongside any eventual pass — not discovered later.

### 56.4 The counter

**The 28-day counter restarts at the first real-data cycle.** It currently
reads 0: §55.10's restart never began counting, because no book formed on any
day since. Nothing is being discarded.

### 56.5 Stop conditions

- **Part A:** any divergence between exchange response and local record stops
  the stage. The roundtrip is the evidence that the code can genuinely trade;
  a partial pass is not evidence.
- **Part D:** if the 12-day real-data replay again shows ~0% formation, **stop
  and report**. That would falsify §55.9's diagnosis — the third measurement of
  the same quantity — and would need eyes rather than another layer.

### 56.6 What this stage does not do

- Does not give the production client any secret, signing path or non-GET method
- Does not place any order outside the testnet demo fixture
- Does not tune the fill model beyond §56.2
- Does not change a strategy parameter
- Does not touch mainnet trading or the holdout

### 56.7 PART A RESULT — the order machinery genuinely trades. 30 of 30.

```
  BTCUSDT $55.07   ETHUSDT $24.66   SOLUSDT $14.83     account FLAT after
  ------------------------------------------------------------------------
  1  market order          FILLED on all three
  1b costlog fill row      fees recorded, venue=testnet, demo=True
  2  limit placed          rests 20% below bid
  2b limit cancelled       CANCELED, confirmed by query
  3  stop (layer 1)        REFUSED BY THE VENUE -- see below
  4  reconcile             exchange position == filled quantity, exactly
  5  close                 flat
  6  undersized order      classified FilterRejected, code -4164
  cleanup                  flat, verified per symbol
```

Every step produced the expected exchange response **and** the expected local
record. `flatten_all()` was also exercised against real positions during the
failed attempts and closed all three correctly — incidental but real evidence
that the kill switch's flatten path works on live positions.

**It took three attempts, and the two failures were mine.** They are recorded
because the failures are the evidence that the roundtrip tests something.

### 56.8 Three defects the roundtrip found

**1. My limit orders were sized off the mid, not their own price.** A limit
resting 20% below the bid carries 20% less notional, which put BTC ($44 at the
limit price) and ETH under their MIN_NOTIONAL floors and the venue refused
them. The exchange was right; the sizing was wrong. Fixed: a resting order is
sized by the price it rests at.

**2. The harness did not fail closed.** An uncaught exception mid-symbol
skipped that symbol's close step and left **three open positions on the
account**. A harness whose purpose is to prove fail-closed behaviour must
itself fail closed. Restructured: every step is individually guarded and
records against its own name, and `cleanup()` runs in a `finally` — cancel
everything, close anything, verify flat.

**3. `positionRisk` can lag a filled order.** Observed once: 0.0 returned
immediately after a `FILLED` market order, correct on a later call; a later
probe showed 0.14 at +0.0s, so it is intermittent rather than systematic.
**This matters beyond the roundtrip**: `run_cycle` reads `fetch_state`
straight after placing orders to compute the filled book, so a lagged read
would show missing legs and fire a FALSE atomicity breach (§52.4 fix 1). The
roundtrip now settles before asserting. **The same settle is owed in
`run_cycle` and is not yet applied** — recorded as outstanding rather than
quietly assumed harmless.

### 56.9 A venue capability finding: layer-1 stops are UNAVAILABLE

This testnet refuses **every** conditional order type on `/fapi/v1/order`:

```
  STOP_MARKET + closePosition=true        -4120
  STOP_MARKET + quantity + reduceOnly     -4120
  STOP_MARKET + quantity                  -4120
  TAKE_PROFIT_MARKET + closePosition      -4120
      "Order type not supported for this endpoint.
       Please use the Algo Order API endpoints instead."
```

**And `exchangeInfo.orderTypes` advertises all of them** — `['LIMIT',
'MARKET', 'STOP', 'STOP_MARKET', 'TAKE_PROFIT', 'TAKE_PROFIT_MARKET',
'TRAILING_STOP_MARKET']`. The metadata does not describe what this endpoint
accepts, which is worth knowing generally: capability cannot be inferred from
`exchangeInfo`.

**This falsifies part of §52.4.** Stage 14a recorded that exchange-side stops
"are now placed" after every rebalance. On this venue they cannot be. The
claim was never exercised because no book ever formed (§53), so the code has
never once succeeded at placing a stop — a check that has never run is not a
check that passes.

**Consequence, stated plainly: the three-layer protection is two layers on
this venue.** Layer 1 (an exchange-side stop that survives this process, this
machine and this ISP) is unavailable. Layer 2 (watchdog) and layer 3 (kill
switch) are unaffected and remain armed and tested.

`_place_stops` now records a -4120 **once** as a capability limitation rather
than as a per-symbol error every cycle (the §51.10 spam lesson applied
forward), and `CycleResult.stops_unsupported` carries it to the daily report.

**Not attempted:** routing stops through the Algo Order API. That is new
signed-endpoint surface on a venue the paper book is about to stop trading on
(Parts B–D move execution to simulation), so it would be work spent on a path
being retired. If real-money trading is ever approached, layer 1 must be
re-established and re-tested on the venue that will actually carry it.

Suites 138/138. Account flat. Budget 15 of 25. Holdout sealed.

### 56.10 PARTS B AND C — built, railed, tested

**Part B — `live/proddata.py`.** Read-only production market data: GET-only,
unsigned, allow-listed. It is the only module permitted to name a production
host, and the permission is earned by proof rather than asserted:

```
  imports no hmac/hashlib/cryptography/secrets   -- no signing PATH exists
  constructor accepts no key/secret/token param  -- a caller cannot pass one
  signs nothing even with credentials in the env -- adversarial test
  post/place_order/cancel_* all raise ReadOnlyViolation
  7 allow-listed read paths; /fapi/v1/order and /fapi/v2/account refused
  every request built with method="GET"; no other verb is constructed
  refuses a non-TLS base
  paper_feed=production may pair ONLY with execution=simulated
```

**`test_no_mainnet_anywhere_in_live` was NARROWED, not deleted** (§56.1).
Every trading-capable module — `client.py`, `killswitch.py`, `trader.py`,
`phase2.py` — is still checked for production hosts by name, and the
data-only exemption is a one-item list that shows up in any diff.

**Part B.2 `feedcheck` — the feed is live and consistent:**

```
  server skew -13 ms, rtt 124 ms
  last CLOSED 1m bar 53 s old (limit 180 s)          FRESH
  funding rates live, next settlement 16:00 UTC
  quotes move between successive calls                LIVE
  live 24h volume vs store medians: ratios 0.43-1.01  SAME ORDER
  exchangeInfo drift vs snapshot: +1 symbol           guard input
  20 GET requests, zero signed, zero orders
```

One number worth keeping: **realised half-spreads on majors were 0.01–0.47
bps against the 5 bps fill assumption.** That is the first real-data hint that
5 bps is generous for liquid names. **Nothing is adopted from it** — §56.2
fixed the fill model in advance precisely so this measurement cannot quietly
become an input.

**Part C — `live/fillsim.py`**, 10 tests. Fill at the open of the bar that
opens **strictly after** the decision (a bar already forming was partly
visible), 5 bps **adverse in both directions**, both fee schedules reported as
a pair, MIN_NOTIONAL refused exactly as the venue would, rows tagged
`venue=prod_data_sim` so they can never be confused with testnet or real
fills, plus spread capture and the shadow-maker counterfactual — logged,
never placed.

### 56.11 PART D RESULT — 0 of 12 on REAL data. My §55.9 diagnosis is FALSIFIED.

```
  testnet, synthetic ranking   (53.1)   0 of 12   (0%)
  testnet, production ranking  (55.8)   0 of 12   (0%)
  PRODUCTION DATA              (here)   0 of 12   (0%)

    unhedgeable_beta                6
    below_min_notional_post_hedge   6
```

**§55.9 said the blocker was testnet's ~89 days of synthetic price history.
That is wrong.** On real data beta identifiability is close to perfect — **1
of 15 unidentified**, against 2 of 15 on testnet and 5 of 15 before the §55.1
fix — and the book still never forms. The venue was never the blocker.

This is the second hypothesis of mine falsified by measurement in two stages
(§53.2's premise held; §55.9's did not). Both were caught by measuring rather
than by reasoning, which is the argument for the stop conditions being written
before the runs rather than after.

### 56.12 What the blocker actually is

Two causes, almost exactly half the days each.

**1. The floor, at this capital.** Only **2 of 15** names are unseatable at
the nominal band minimum — BTCUSDT ($50) and ETHUSDT ($20) against a $9.60
band minimum. But the band applies at the *rank-weight* stage; the beta hedge
then scales the short leg and the vol target scales everything, so realised
positions fall well below it and names with ordinary $5 floors (1000PEPE, ADA)
drop too. Each drop shrinks a leg, and a leg under `MIN_LEG_NAMES = 3` skips
the day. This is §32.3 and §39.7's mechanism, unchanged, biting harder.

**2. Recently-listed names with genuinely unidentifiable betas.** AKEUSDT
(beta −3.776 ± **2.504**), LABUSDT (2.342 ± 1.801), BANKUSDT (−0.458 ± 1.365)
are in the top-15 by real production volume. At a 0.2 weight, AKE alone
contributes 0.50 to a leg's standard error; two such names put a leg at ~0.7
before anything else, against a weighted beta that partially cancels to
0.2–0.8. The `unhedgeable_beta` guard then correctly refuses.

**The guard is right in both halves. The config cannot express itself in
today's market at $800.**

### 56.13 The finding, stated plainly

**The frozen config formed a book on ~78% of train days (2020–2023). It forms
one on 0% of the last 12 days of the real market.**

That is not a code fault, a venue fault or a guard fault — all three have now
been eliminated by measurement. It is the market having moved under a
configuration frozen against 2023 data: the same shape as §47's universe
drift, but affecting **tradability** rather than composition. §47 found the
universe rule selecting instruments the strategy was never validated on; this
finds the *sizing and hedging* assumptions no longer satisfiable by the
universe the rule now selects.

**Per §56.5 this stage stops here.** Parts B and C are built, tested and
keep their value; **Part D's cutover is not performed**, because cutting a
daily rehearsal over to a configuration that cannot form a book would produce
28 more days of nothing.

**Not attempted, and each would be a strategy change requiring its own
pre-registration:** raising capital (§50.8 measured the higher-capital book as
carrying a *lower* drift-stripped edge), relaxing `MIN_LEG_NAMES`, filtering
candidates on beta estimation quality, lengthening the beta window, or
loosening the hedge guard.

**The open question is now a research one, not an engineering one:** the
deployment config was validated on a market that no longer exists in the shape
it was validated against. Whether that calls for re-derivation at today's
universe, a different capital tier, or an explicit decision that the strategy
has aged out, is the user's call — and it is a larger question than the paper
phase.

**Budget 15 of 25. No strategy parameter changed. Zero orders on mainnet.
Holdout sealed and untouched.**

## 57. Stage 17 — review fixes and the feasibility surface: PRE-REGISTERED 2026-08-30

**Recorded BEFORE any Stage 17 code.** No trials; budget stays **15 of 25**.
**No strategy parameter of any validated config is changed** — Part II derives
candidates and selects nothing. Zero orders on mainnet. Holdout sealed.

### 57.1 The four defects, verified before being fixed

An external review named four. Each was checked against the code rather than
accepted, and all four are real:

1. **No settle primitive.** `tools/roundtrip_demo.py` polls with a bare
   `time.sleep(0.5)`; `live/phase2.py` reads `fetch_state` immediately after
   execution with **no settle at all**. Two behaviours, one of them absent —
   and §56.8-3 already measured `positionRisk` lagging a filled order, which
   would make the §52.4 atomicity check report a false breach.
2. **The floor model disagrees with itself.** `plan_rescale`'s docstring
   asserts "reduce-only orders are floor-exempt on Binance" and closes
   sub-floor positions outright; `fillsim.simulate` refuses **every** delta
   under `min_notional`, reduce-only included. One of them is wrong about the
   venue, and the trap is a position whose closing delta is sub-floor —
   unclosable under the strict rule.
3. **Research sizing never quantizes.** The store's schema persists
   `step_size` and `tick_size` and `insert_filters` writes them, but `PITView`
   exposes only `min_notional()`, and `compute_target_weights` computes
   `min_position_notional` from raw `weight x equity` with no lot-size
   rounding. Backtest, sim and live each size differently.
4. **`leg_beta_se` is broken and dead.** Its docstring promises
   `(|weighted leg beta contribution|, SE)`; it returns `(se, se)` and never
   computes the contribution. It has **zero callers** — authoritative-looking
   dead code.

### 57.2 I.2 — the floor rule is MEASURED, not argued

The venue can be asked, so it will be. One run on the demo fixture
(testnet, `demo=True`), logged verbatim:

```
  1. open ~$15 SOLUSDT
  2. reduce-only PARTIAL close of ~$3   (sub-floor)   -> accept or reject code
  3. reduce back to a sub-floor remnant
  4. reduce-only FULL close of that remnant           -> accept or reject code
  5. non-reduce-only sub-floor order (the control)    -> expected reject
```

Step 5 is the control: without it, an "everything was accepted" result cannot
distinguish a floor-exempt reduce-only rule from a venue that simply has no
floor today.

The measured rule is then encoded **once**, applied per delta class
(`open_increase | partial_reduce | full_close | flip`), and used by `fillsim`,
`plan_rescale` and the live path alike.

**If testnet's measured behaviour differs from documented mainnet behaviour**,
testnet's is encoded for the simulator, the discrepancy is recorded here, and
it is flagged as a **must-re-verify item for any future real-money venue** —
the same class of finding as §56.9's stops, where a capability the code
assumed turned out not to exist on the venue actually in use.

### 57.3 I.3 — the sizing module, and the scope rule that keeps history honest

One deterministic function is the single source of truth for backtest,
simulator and live:

```
  weight -> notional -> reference price -> raw qty
        -> step-quantized qty -> executable notional -> filter verdict
```

**Scope rule, fixed now.** The module is wired into Part II's surface and into
the paper/live path. It is **NOT retro-run against the frozen config's
recorded train and validate results.** Those stand exactly as recorded, with
an explicit caveat that they predate quantized sizing.

The reason is the one this project has applied throughout: **history is
annotated, never rewritten.** Re-running §44's validate through a new sizing
module would produce a number that no pre-registration ever authorised, and
which could not be compared with anything already recorded. The caveat is the
honest form; a silently improved history is not.

### 57.4 II.1 — the feasibility surface: pre-registration

**The question, verbatim:** *"Under what capital, universe-rank, and
identifiability conditions can the strategy definition (5L/5S, rank-weighted,
beta-hedged, vol-targeted) physically express a book in the current market?"*

**The outputs are probabilities of book formation. Never Sharpe, never PnL,
never returns.** Nothing in Part II reads any return series, and no 2025+
return data is touched.

**The axes, fixed in advance:**

```
  capital                 $800, $1.2k, $2k, $3k, $5k
  universe rank cap       top 15, 20, 25, 30
  identifiability screen  none | beta SE <= 0.3 | <= 0.5 | 60d-listed + SE cap
  vol target              10%, 12%, 14%
```

**Fixed, not swept:** N=10, k=5, the [0.5, 1.5] band, the hedge-guard
thresholds, `MIN_LEG_NAMES`. **The strategy definition is the thing being
tested for expressibility, so it does not bend during the test.** A grid that
also relaxed the guards would answer a different and much easier question.

**The estimator.** For each grid point, over a distribution of plausible
momentum rankings — **bootstrapped from recent real relative-strength
orderings, not one fixed ranking** — draw a top/bottom-k selection, size it
through the §57.3 module with real floors and step sizes, apply the hedge with
today's real betas and standard errors, and record whether a book forms and
which guard refused. Report `P(form)`, the failure-mode split (floor vs
identifiability vs leg-count) and median seated names.

Bootstrapping the ranking is the point: formation must be robust to *which*
names momentum picks, not conditioned on last Tuesday's ordering.

**The anti-tuning rule.** The 12 replay days of §56.11 are a **smoke test
only**. The surface is computed from structural facts — listings, floors, step
sizes, beta standard errors as of today. Candidate regions are **verified on
forward live-replay days as they accumulate, never fitted to the 12.** Fitting
to 12 known failures would produce a region defined by those failures and
tell us nothing about the next twelve days.

### 57.5 The reading — fixed before computing

| Pattern | Meaning |
|---|---|
| `P(form)` rises steeply with capital at a fixed screen | **capital-bound**: $800 is simply too small for today's structure. Report the tier where P ≥ 0.9 |
| low at all capitals without a screen, healthy with one | **universe-too-young**: identifiability binds, not money. The screen becomes the research object |
| low everywhere on the grid | **aged out** as defined; a new strategy generation is a bigger question than any grid |

Mixed patterns are reported as mixed rather than resolved.

**No cell is promoted to a deployment config.** The surface ends as a report
and a recommendation *for the user's decision*. If a viable region exists, the
report must state the validation problem that region inherits — a train era
that no longer resembles the market, a sealed holdout, and forward validation
as the only clean option left.

### 57.6 What this stage does not do

- Does not sleep instead of settling
- Does not argue the floor rule from documentation when the venue can be asked
- Does not re-run frozen-config history through the new sizing module
- Does not fit any Part II choice to the 12 replay days
- Does not read Sharpe, PnL or any 2025+ return data in Part II
- Does not promote a surface cell to a deployment config
- Does not split or edit `NOTES.md` — it stays canonical and append-only
- Does not touch mainnet or the holdout

### 57.7 PART I RESULT — the four defects, and one frozen-module decision

**I.1 the settle primitive.** `live/settle.py::await_reconciled_state` polls
for a *condition* — every expected position reflected within step tolerance —
and **raises** on the deadline rather than returning a book it does not
believe. Both call sites now import the same function; the bare
`time.sleep(0.5)` in the roundtrip is gone, and `run_cycle` settles before the
atomicity verdict instead of reading once. **The atomicity check is skipped
entirely when the settle times out**, because a verdict on unsettled state is
a guess. A test asserts exactly one implementation exists.

**I.2 the floor rule — measured, not argued.** Probed on the venue:

```
  1 open ~$15 (non-reduce-only)                ACCEPTED
  2 reduce-only PARTIAL close ~$3 (sub-floor)  ACCEPTED
  3 reduce-only down to a $3.21 remnant        ACCEPTED
  4 reduce-only FULL close of that remnant     ACCEPTED
  5 CONTROL: same size, NOT reduce-only        REJECTED -4164
      "Order's notional must be no smaller than 5
       (unless you choose reduce only)"
```

**Rule: `reduce_only_exempt`.** `plan_rescale` was right; **`fillsim` — which
I wrote in Stage 16 — was too strict** and would have made a sub-floor
position unclosable in the simulator. The control step is what makes this
conclusive: without it, "everything was accepted" could equally have meant a
venue with no floor at all.

This rule is **testnet-measured** and is flagged as a **must-re-verify item
for any real-money venue**, the same class as §56.9's stops finding.

**I.3 the shared sizing module** (`backtest/sizing.py`), with delta
classification, and it immediately paid for itself:

```
  BTCUSDT at $800/10%: target $19.20 -> raw qty 0.000305 -> step 0.001
                       -> quantized qty 0 -> NO POSITION AT ALL
```

Research sizing never quantized, so it believed that BTC position existed. The
step size kills it before the floor is ever consulted. `PITView.symbol_filters`
now exposes the `step_size` the schema has carried all along and nothing read.

**Scope rule honoured:** the frozen config's train and validate numbers are
**not re-run** through the new module. They stand as recorded, annotated as
predating quantized sizing (`docs/CURRENT_STATUS.md`). History is annotated,
never rewritten.

**I.4 `leg_beta_se`** returned `(se, se)` — never computing the contribution
its docstring promised — and had **zero callers**. Fixed to match the
docstring **and wired into `build()`**, replacing the open-coded duplicate, so
there is one implementation rather than a correct copy beside a wrong dead
one. **The refactor is proven inert**: the $800/10% train row still reproduces
§43.6 on all four pinned figures.

**A frozen-module decision, recorded rather than slipped through.** Adding
`PITView.symbol_filters` broke `test_every_public_reader_is_time_gated` and
`test_stage1_regression` — the frozen Stage 1 whitelist, doing exactly its
job by refusing an unenrolled reader. The method is **not** point-in-time: it
returns the earliest recorded filters, which is precisely and only the gap
`min_notional` already carries and `audit_filter_coverage()` already
quantifies. It was enrolled deliberately, with that reasoning written beside
the whitelist entry. Stage 1 is 13/13 again.

### 57.8 PART II RESULT — UNIVERSE-TOO-YOUNG. Identifiability binds, not money.

```
  P(form), rank-cap 15, vol 10%
  capital        none    se<=0.3    se<=0.5   60d+se<=0.5
  $   800         23%       100%       100%       100%
  $ 1,200         17%       100%       100%       100%
  $ 2,000         15%       100%       100%       100%
  $ 3,000         16%       100%       100%       100%
  $ 5,000         14%       100%       100%       100%

  capital effect ($800 -> $5k, no screen):   +3%
  screen effect (best screened - unscreened): +59%
```

**Capital does essentially nothing. The screen does everything.**

That is a direct answer to the review's tier question, and it **contradicts
the intuition behind Stage 13's $2k exploration**: more money does not buy
formation. §50.8 had already found the higher-capital book carrying a *lower*
drift-stripped edge; the surface now adds that it does not even buy the
ability to trade.

The frozen config's own cell: **P(form) = 23%**, failure split **floor 45%,
identifiability 32%, leg-count 0%**.

### 57.9 Two estimator defects I had to fix before believing any of it

**1. A uniform shuffle is not a momentum ranking.** The first implementation
drew orderings with `rng.permutation` and reported the frozen cell at **59%**
against a measured 0/12. §57.4 had registered "bootstrap from recent real
relative-strength orderings", and a uniform shuffle is not that. Momentum
picks **extremes**, and on this venue the extreme movers are systematically
the recent listings with the largest beta standard errors: a uniform draw puts
one in a leg occasionally, real momentum puts one there almost always.
Corrected to sample a real historical day and use its actual 14-day ordering
— frozen cell **59% -> 23%**.

**2. The floor was checked at the wrong point.** I applied it to band weights;
the pipeline applies it **post-hedge, post-vol-scale**, where positions are
smaller. The first corrected run reported **floor 0%** against a replay that
measured 6 of 12. With the hedge ratio applied before sizing, the split
becomes **floor 45% / identifiability 32%** — matching the replay's 6/6.

Neither correction was a fit to the 12 days: both were faithfulness repairs
against the pre-registered estimator, and both moved the answer *away* from
the flattering direction. The smoke test then agreed, which is what a smoke
test is for.

**The residual gap is stated, not smoothed:** the surface says 23% and the
replay measured 0 of 12. `P(0 of 12 | p = 0.23) ≈ 4%`, so the two are only
marginally consistent. The surface's gross proxy (`0.24 x vol/0.10`) and its
single-hedge-pass approximation are the likely remainder, and the surface
should be read as *the shape of the constraint*, not as a calibrated
probability.

### 57.10 The reading, and what is NOT being concluded

**Pattern: UNIVERSE-TOO-YOUNG.** The binding constraint is that the top of
today's crypto universe contains recently-listed names whose betas cannot be
identified on 60 days, and the strategy's own hedge guard — correctly —
refuses to hedge on them.

**No cell is promoted to a deployment config**, and the smallest cell reaching
P ≥ 0.9 (`$800 / top-15 / SE ≤ 0.3 / 10%`) is **a research object, not a
recommendation**. Adopting it would mean adding a screen the validated
strategy never had, and:

- it was derived from **today's** structure, on 30 symbols, at one moment
- a beta-SE screen is a **new selection rule**, and selection on estimation
  quality correlates with age, size and volatility — a tilt that would need
  its own attribution before anyone could claim the edge survived it
- the validated evidence is from a market whose composition has since drifted
  (§47) and whose tradability has since broken (§56.13)

**The validation problem any viable region inherits**, stated as §57.5
required: a train era that no longer resembles the market, a **sealed
holdout**, and **forward validation as the only clean option left**. A screen
adopted now could not be tested on train without re-opening a question the
train data can no longer answer honestly, and could not be tested on the
holdout without spending the one look on a rule invented after the fact.

### 57.11 Status

- **`docs/CURRENT_STATUS.md`**, `pyproject.toml` and a CI workflow added. CI
  runs the secret scan, the frozen lookahead suite and the fast suite; the
  732 MB store, network venues and the real-order roundtrip **cannot** run
  there and are marked locally-verified-only, so **CI green does not mean
  everything was checked**.
- **`NOTES.md` untouched as the ledger** — append-only, unsplit, canonical.
  `CURRENT_STATUS.md` says so and defers to it on any disagreement.
- Suites **169/169**. Secret scan clean over 129 tracked files.
- **Budget 15 of 25. No trial. No validated strategy parameter changed. Zero
  orders on mainnet. Holdout sealed and untouched.**

## 58. The RCM proposal (Strategy Generation 2) — REVIEWED, not implemented

**2026-08-31.** `Claude/RCM_Strategy_Proposal.md` received and reviewed. This
entry records the review, two measurements made to ground it, and the
questions that must be answered before any RCM stage can be pre-registered.
**Nothing is implemented, no trial is spent, XSMOM stays frozen as Generation
1, and the holdout is untouched.**

### 58.1 The verdict, in one paragraph

The proposal is sound, and its diagnosis is not a hypothesis — it is what this
project has already measured. "The portfolio construction can fail before the
alpha is expressed at all" is §56.13 and §57.8 verbatim: 0-of-12 book
formation with the failure split 45% floor / 32% identifiability, on a guard
that was working correctly. Every architectural change RCM proposes maps onto
a measured failure: continuous SE penalty ↔ the UNIVERSE-TOO-YOUNG verdict;
graceful degradation ↔ the binary `MIN_LEG_NAMES` / feasibility rejections;
BTC+ETH factor model ↔ §47.2's finding that a BTC-only hedge lost meaning as
the universe drifted; continuous selection ↔ the §57.8 result that momentum's
extreme-picking is exactly what feeds unhedgeable names into fixed top-k legs.
The kill criteria (§18 of the proposal) and staged methodology (§17) match the
house discipline. Keeping XSMOM frozen rather than patched is the same call
§57.10 made.

### 58.2 Two measurements made for this review

**1. The universe rule is expressible in today's market.** Crypto-only
(NOTES 48 filter) USDT perps: **514**. Of the top 80 by production volume,
**77 have ≥180 days of history** — plenty for a 20–30% tail selection
(~15–23 names per side against XSMOM's fixed 5).

**2. Maturity does NOT buy identifiability — measured, not assumed.**

```
  symbol     listed   beta(60d)     SE
  AKEUSDT      339d     -3.765   2.523
  LABUSDT      318d      2.632   1.795
  BANKUSDT     400d     -0.469   1.375
```

AKEUSDT has nearly a year of history and a beta standard error of 2.5. The
proposal's §3 maturity rule alone would still admit these names into the
book. **What carries the load is §7's continuous penalty**
(`S/(1+c·SE)`) — noisy names get less capital instead of either full weight
or a vetoed book. That is the single most important mechanism in the
proposal, and the review's one structural caution: the maturity rule should
be described as a data-quality precondition, not as the identifiability fix.

### 58.3 What transfers from Generation 1 — most of the machine

The PIT store and its 13/13 lookahead discipline; the crypto universe filter
and composition guard; `proddata` (read-only production data with proven
rails); the fill simulator and the pre-registered fill model; the shared
quantized sizing module — **which RCM's optimizer needs as a constraint set,
and which did not exist until Stage 17**; the settle primitive; the risk
layer, kill switch, watchdog, supervisor, dashboard, alert scaffolding; the
costlog with venue tags; the trial-budget and pre-registration conventions.
Generation 2 is mostly strategy-layer work on top of a machine that now
exists and is tested (169/169).

### 58.4 The questions that must be answered BEFORE an RCM stage is registered

**1. The data-era question — the big one, and it is the user's.** The
proposal's Stage B says "training-period evaluation" without naming the era.
§56.13 established that 2020–2023 no longer resembles the market; training
RCM there inherits the same dead-era problem XSMOM died of. But training on
2024–2026 consumes data that overlaps the sealed holdout window
(2025-01 → 2026-07), which was defined for Generation 1. **Does the seal bind
Generation 2?** Three coherent positions exist — (a) it binds absolutely and
RCM trains only through 2024, (b) it was Gen-1-specific and Gen 2 defines
fresh splits with the seal re-drawn forward, (c) forward-only validation with
no historical holdout at all, per §57.10. Each has different integrity costs.
**Not chosen here; nothing proceeds until it is.**

**2. The Gen-2 trial budget.** Gen 1 spent 15 of 25. A new generation needs
its own budget, pre-registered before the first backtest, and a definition of
what counts as a trial for a strategy whose parameters are continuous
(λ, c, γ, η, tanh scale, window weights) rather than a small grid. The
proposal's §15 restraint is right; the budget must make it enforceable.

**3. The direction-aware funding sign.** The proposal itself concedes
`S = Zmom − λ·Zfund` is not direction-correct for shorts and defers the fix
to "ultimately". It is the economics of half the book and it is cheap; it
belongs in v1, not later.

**4. The optimizer's engineering costs.** A QP introduces the project's first
solver dependency, and solver tolerance breaks bit-exact shadow
reconciliation — the identity check that anchored Gen 1's paper phase. The
shadow tolerance (1e-6 on weights) will need a pre-registered, argued
replacement for an optimization-based book.

**5. One math note for the eventual spec.** §5's nested windows (days 2–21
weighted 0.6+0.4, days 22–63 weighted 0.4) are fine as a scheme but should be
stated as deliberate; and the z-scores of overlapping sums will correlate the
two components — worth knowing before anyone interprets a weight sweep.

### 58.5 Status

Proposal committed to the repo. XSMOM remains frozen; its 0/12 failure is
already recorded (§56.13) as the proposal's §22 asks. Budget **15 of 25**
(Gen 1). Holdout **sealed** — and question 1 above is now the gating decision
for everything that follows.

## 59. Stage 18 — RCM GENERATION-2 GOVERNANCE, pre-registered 2026-08-31

**This stage wrote no code and accessed no market data, return data,
production snapshot, backtest result or performance diagnostic.** It writes
this one section: the governance under which Strategy Generation 2 (Residual
Carry Momentum) may be developed. Gen 1 (XSMOM) is frozen and complete.
Gen-1 budget **15 of 25**, unchanged. Gen-2 budget **0 of 20**. Holdout
sealed.

### 59.0 Why governance precedes mathematics

RCM was conceived **after** observing the 2026 structural failure.
Information from the 2025–2026 regime has already shaped the hypothesis class
— reliability weighting, continuous construction, the avoidance of fixed-rank
legs are all answers to what that regime did. A policy written after the
specification would be written by someone who already knows what the market
did. So the rules come first, while they can still bind.

### 59.1 Data policy

- **Development era: 2020–2024.** Implementation, structural diagnostics,
  signal decomposition, robustness work, parameter pre-registration.
- **2025-01 → 2026-07 is SEALED — and the seal is RE-AFFIRMED ON GEN-2
  GROUNDS, not merely inherited.** That window is *not* a Gen-2 validation
  period, because RCM's hypothesis class was influenced by that regime's
  observed structure; treating it as independent evidence would overstate its
  independence. This answers §58.4 question 1: neither option (b) nor a
  quiet consumption of the window — the seal binds Gen 2 for Gen-2-specific
  reasons.
- **Primary validation is forward-only paper trading**, on data postdating
  the frozen RCM v1 specification.
- **The sealed window becomes an optional final historical challenge set**:
  openable only after RCM v1 is frozen AND credible forward evidence exists.
  One look, ever, by deliberate user decision.
- **The seal is enforced structurally, not textually.** Stage 19 must specify
  — and the implementation must provide — a Gen-2 research runner that **hard
  rejects** any request whose timestamp range intersects the sealed interval.
  Opening the challenge set requires an explicit unlock flag PLUS a ledger
  entry written before execution. The `PITView` principle: a guarantee, not a
  promise.
- **The forward boundary is a commit and a timestamp.** The exact RCM-v1
  freeze commit hash and UTC timestamp are recorded at the freeze; forward
  validation counts strictly data after it, with **no retroactive backfill**
  into the forward period.

### 59.2 What forward validation can and cannot establish

**CAN:** feasibility (a recognizable book forms, with breadth, at intended
risk), machine correctness, execution and cost realism, operational
integrity — precisely the axes on which Gen 1 failed.

**CANNOT: confirm alpha.** Months of forward paper have negligible
statistical power — the Gen-1 MDE wall (§28.4, §45.9), which is indifferent
to data freshness. **A clean forward record is not evidence the edge works
and must never be cited as grounds for increasing capital.**

### 59.3 Trial budget — a ceiling, not an allowance

**20 Gen-2 trials maximum**, independent of Gen-1's ledger. The intent is
that RCM v1 consumes **very few**.

**No trial is consumed by** (no performance comparison occurs): PIT
alignment, solver feasibility, beta-estimator simulation, optimizer
constraint tests, exchange-filter validation, reconciliation tolerance
calibration, sizing/quantization tests, return-blind universe measurement,
null/synthetic harness tests.

**A trial is consumed by** any real-data run whose performance result could
cause a preference between alpha or portfolio specifications.

**59.3.1 INDETERMINATE is a valid outcome.** If an allowed comparison
produces a difference too small to resolve at the pre-registered statistical
precision, the result is **INDETERMINATE** — not a win for the larger point
estimate. The simpler / pre-registered baseline stands. **Mandatory
operationalization: the resolvable difference (MDE) and the test are stated
BEFORE the comparison runs**, so "too small to resolve" cannot be argued
after seeing numbers. This generalizes the §45 buffer ruling (a point-
estimate improvement that could not clear a paired CI was correctly not
adopted) into a standing Gen-2 rule.

**59.3.2 Frozen analytically — never swept.** Fixed by reasoning, with no
performance comparison, ever:

- *Signal & construction:* momentum windows **2–21 and 22–63
  (non-overlapping)**; 0.6/0.4 weighting; residual construction; BTC+ETH
  factors; the cross-sectional normalization and winsorization rule.
  (The non-overlapping windows supersede the proposal's §5 nested windows —
  resolving §58.4 question 5 by construction rather than by caveat.)
- *Estimation:* beta estimation method and window; covariance estimator and
  window; expected-funding estimator and forecast horizon; liquidity metric
  and window.
- *Portfolio:* reliability functional form; conviction mapping; optimizer
  constraints; solver tie-breaking rule; volatility target; daily rebalance;
  transaction-cost treatment; the 180-day minimum history.

The estimation block is listed explicitly because freezing the visible knobs
while leaving estimator choices open would defeat the purpose.

**59.3.3 Funding enters at economic value — λ eliminated by default.**
Expected momentum and expected funding are expressed in **comparable return
units**, so funding enters at its economic value:

```
    μ_total = μ_momentum − F        (λ = 1; not a free parameter)
```

A λ comparison is permitted **only if** Stage 19 demonstrates the momentum
signal cannot be put into comparable units — and then it is small,
pre-registered, trial-logged, and subject to §59.3.1. (This also resolves
§58.4 question 3's economics concern at the root: a sign-aware λ tuned by
sweep is replaced by funding entering as a return.)

### 59.4 Feasibility gates — three vacuities, plus leg symmetry

Degradation is permitted **only while the portfolio remains recognizably the
same strategy.**

1. **Concentration.** `N_eff = (Σ|w_i|)² / Σw_i²`, **computed per leg** —
   `N_eff,long` and `N_eff,short` with **independent minimums**. Total
   breadth can hide asymmetric collapse: a nominally neutral book with one
   meaningful short is not the hypothesis.
2. **Exposure.** `G_realized / G_target ≥ g_min`.
3. **Signal coverage.** `Σ|w_i||S_i|` retained after feasibility, relative to
   the same quantity on the **canonical pre-feasibility book** — the signed
   continuous target after alpha, risk and factor construction but **before**
   exchange/min-notional/quantization feasibility. Guards against a book that
   keeps breadth and gross while discarding the strongest signals because
   they are hardest to trade.

**Threshold derivation (Stage 19's job).** `N_eff ≥ 6` and
`G_realized/G_target ≥ 0.70` are **starting concepts, not adopted numbers**.
Stage 19 *may* use: algebra, the intended diversification and risk
architecture, exchange rules, synthetic fixtures, return-blind metadata.
Stage 19 *may NOT* use: historical PnL, Sharpe, realized alpha, historical
formation rates, or "which threshold would have traded more days."

A day failing any gate is a **skip**, recorded with the failing gate.

**59.4.1 Gate-skips are accidentally protective — the Gen-1 lesson made a
rule.** §42.6 measured it: the $400 book's flattering 14.78% drawdown existed
because skipped days sat out the losses; healing the skips moved it to
24.79%. Therefore **all development-era performance is computed on the full
calendar, never on formed days only**, and **formation rate is reported
beside every performance number.** Otherwise the gates that protect against
vacuity quietly flatter results.

### 59.5 Numerical reconciliation and optimizer determinism

Bit-exactness is not assumed for an optimizer-built book. Stage 19 freezes:
the solver and its version; deterministic seeding/threading; the weight
tolerance; **the primal feasibility tolerance; the maximum factor-constraint
residual; the maximum dollar-neutrality residual; the accepted solver
termination states; and the deterministic handling of multiple or
near-equivalent optima.** Weights agreeing within shadow tolerance are
insufficient if they subtly violate the economic constraints. A mismatch
beyond tolerance remains stop-and-diagnose. (This resolves §58.4 question 4
by naming the full determinism surface rather than only a weight tolerance.)

### 59.6 Inheritance from Generation 1

Reused unchanged: the PIT store and lookahead discipline, the crypto universe
filter and composition guard, the read-only production data path, the fill
simulator and pre-registered execution model, the shared quantized sizing
module, the settle/reconciliation primitives, the risk layer, kill switch,
watchdog, supervisor, dashboard and alerts, the cost log and venue tags, and
the trial-budget and pre-registration conventions.

Inherited **must-re-verify list for any real-money venue**: the
`reduce_only_exempt` floor rule (§57.2) and layer-1 stop availability
(§56.9) — both testnet-measured only.

### 59.7 Kill criteria — exact, with quantities deferred to Stage 19

RCM is **abandoned, not patched**, if:

- formation rate falls below the Stage-19 pre-registered minimum;
- the Stage-19 pre-registered residual-momentum statistic fails its exact
  criterion on development data;
- forward feasibility breaches its pre-registered rolling gate.

**Gen-1's ~78% formation rate is deliberately NOT inherited.** RCM must
derive its own minimum viable activity level from what it claims to be — a
daily-rebalanced cross-sectional strategy — via holding-period and turnover
logic, not from the outcome of the strategy it replaces.

### 59.8 Post-freeze change control

After the RCM-v1 freeze:

- any strategy-layer change altering target weights, eligibility, signals,
  risk allocation or execution intent creates **RCM v2** and **restarts the
  forward-validation clock**;
- a PnL-affecting bug fix **voids the affected forward segment** rather than
  continuing under the same evidence record;
- purely operational fixes that **provably do not alter intended targets**
  may remain v1, with the proof recorded.

This is what prevents forward validation from becoming another development
dataset.

### 59.9 The research order — fixed

```
  governance (this section)
    -> mathematical specification + threshold derivation      (Stage 19)
    -> synthetic/structural implementation and null tests
    -> 2020-2024 development
    -> FREEZE RCM v1  (commit hash + UTC timestamp recorded)
    -> forward paper validation
    -> (much later, optional) the sealed challenge set
```

**No optimizer, signal, or portfolio code exists before Stage 19 exists in
this ledger.**

### 59.10 Status

No code written. No market, return, snapshot, backtest or performance data
accessed in this stage. Gen-1 budget **15 of 25** unchanged; Gen-2 budget
**0 of 20**. Holdout sealed — now under two independent seals: the Gen-1 rule
(§29/§30) and the Gen-2 re-affirmation (§59.1), which stands on its own
grounds and survives even if the Gen-1 rule were ever revisited.

### 59.11 AMENDMENT (Stage 18a, 2026-08-31) — gate-failure semantics and feasibility attribution

**Appended; §59.0–§59.10 are unedited.** No code was written and no market,
return, snapshot, backtest or performance data was accessed. Gen-1 budget
**15 of 25**; Gen-2 **0 of 20**. Holdout sealed.

#### 59.11.1 BLOCKING FOR STAGE 19 — gate-failure state semantics are part of the specification

§59.4 defines a gate failure as a **skip** but never says what a skip does to
an **already-held portfolio**. The candidate answers — flatten to cash, hold
unchanged, risk-only rescale, partial rebalance, hold-N-days-then-flatten —
are **materially different strategies**, not implementation details.

> **Gate-failure state semantics are part of the strategy specification.**
> "Skip" states only that no new gate-passing target exists; it does not by
> itself specify what happens to an already-held portfolio. **Stage 19 must
> pre-register the exact state transition** — flatten, hold, risk-only
> rescale, or another explicitly defined rule — **before any return data are
> accessed. The rule may not be selected from historical performance.**

**Gen-1's skip semantics are NOT inherited.** §59.6 lists inherited
infrastructure and is silent here; silence must not become an unexamined
default. Gen-1's skip was all-or-nothing on a discrete 5L/5S book; RCM's
continuous optimizer can produce a **partially feasible** book — a state
Gen-1 never had. The rule is derived from RCM's own architecture.

**A single common transition is PREFERRED.** Gate-specific transitions are
permitted **only** where Stage 19 establishes that a common transition
violates a distinct risk or economic invariant, with that invariant named.
Bespoke behaviour per gate is a state machine's worth of degrees of freedom
before RCM has demonstrated anything.

**Concurrent gate failures must be deterministic.** Stage 19 specifies either
one common transition for any non-empty failure set, or an explicit
precedence/composition rule `T(failed_gates, current_state)`.
**Implementation order may not determine the economic outcome** — `if/elif`
ordering is not strategy logic.

**Any "hold" variant must state its leverage-drift consequence explicitly.**
Gen-1's runaway leverage (§13.1: 20x through a 3x cap, four bankruptcies in
grid v1) came from exactly this choice being made without being stated.

#### 59.11.2 Exhaustive calendar classification, with causal precedence

Every date is classified into **mutually exclusive, collectively exhaustive**
categories by a deterministic rule:

```
  D = D_formed ∪ D_gate ∪ D_structural ∪ D_operational      (disjoint)

  D_gate        failed one or more §59.4 feasibility gates
  D_structural  insufficient usable universe, missing market data,
                unavailable execution bar, factor-estimation failure,
                stale metadata
  D_operational solver failure, refusal, harness/host issues
```

**No date may be unaccounted for.**

**Precedence.** A date can satisfy more than one raw failure condition (gates
fail *and* the execution bar is missing). Since the categories are disjoint,
**Stage 19 must define deterministic precedence**, and it must reflect
**causal stage ordering** — the first stage at which the intended decision
became impossible — never `if/elif` placement. A plausible pipeline is
`structural eligibility → optimizer → feasibility gates → execution`, but
Stage 19 defines it.

#### 59.11.3 Attribution diagnostics — two deltas, both fenced

**One object, two uses.** The shadow book **is** the canonical
pre-feasibility book already defined in §59.4 as the signal-coverage
denominator. A second definition may not be introduced.

**The shadow return, frozen:**

```
  r_shadow(t+1) = w_pre(t)ᵀ · r_price(t+1)
```

Canonical **continuous** pre-feasibility weights; next-rebalance-horizon
**price** returns; **no fees, no slippage, no quantization**. Funding is
reported separately and never folded in.

**Domain:** `D_formed ∪ D_gate` only — no shadow target exists on
`D_structural` or `D_operational` dates. Those counts are reported and
explicitly excluded.

**Δ_gate — were rejected targets systematically different?**

```
  Δ_gate = E[r_shadow | formed] − E[r_shadow | gate_failed]     with 90% CI
```

Stationary-bootstrap interval. **Not significance-tested as pass/fail.**

**Δ_transition — what did the transition rule do about it?**

```
  Δ_transition = E[ r_actual_price − r_shadow | D_gate ]        with 90% CI
```

`r_actual_price` is the realised book's **price-only** return under the
pre-registered transition rule — price-only so it is comparable to
`r_shadow`. This separates *alpha selection caused by gate timing* (Δ_gate)
from *the performance effect of the chosen transition* (Δ_transition):

```
  signal → feasibility selection → state transition → realised strategy
```

**The decomposition is price-only and does NOT sum to realised net
performance.** The gap is execution cost, and it is **not neutral across
transition rules**: flatten *trades* on gate-failed days and pays closing
costs; hold trades nothing. The execution-cost term is reported as a separate
line, with the transition rule's own cost consequence named — otherwise a
positive Δ_transition under flatten reads as free protection when part of it
was paid for in fees.

**Interpretation — transition-induced exposure selection.**

> **Literal protective abstention exists only under flatten semantics.**
> Under hold, rescale, or other transitions, gate failures can still create
> protective or harmful **exposure selection** by preventing the canonical
> new target from replacing the existing book. Therefore **Δ_gate measures
> properties of rejected targets**, while the realised performance
> consequence of gating must be interpreted **jointly with the pre-registered
> transition rule** (Δ_transition). Every report of either diagnostic states
> the active transition rule beside it.

And, non-categorically:

> Systematically worse shadow alpha on gate-failed days is **evidence
> consistent with endogenous time selection induced by feasibility**. It is a
> potential contributor to performance and **may not be attributed to
> residual momentum without further decomposition** — volatility, liquidity
> and listing age can produce the same pattern.

**The fence.**

> Δ_gate and Δ_transition are **attribution only**. Neither may be used to
> tune feasibility thresholds, nor to choose flatten vs hold vs rescale after
> seeing returns, nor to convert a feasibility rule into an alpha filter.
> Any of those creates a **new strategy generation** with its own governance,
> not an amendment to RCM v1.

#### 59.11.4 The standard reporting tuple

Every performance table row reports: **calendar performance (full calendar,
§59.4.1); formation rate; feasibility-gate skip rate; structural skip rate;
operational skip rate; gate composition (which gates, with counts).**

Any Sharpe, return or drawdown computed on formed days only carries the
literal label:

**`DIAGNOSTIC — CONDITIONAL ON FORMATION — NOT STRATEGY PERFORMANCE`**

## 60. Stage 19 — RCM v1 MATHEMATICAL SPECIFICATION, pre-registered 2026-08-31

**No optimizer code was written and no market, return, backtest or
performance data was accessed in this stage.** Inputs: the ledger, committed
exchange metadata, algebra. Governed by §59 and §59.11. Gen-1 budget
**15 of 25** unchanged; Gen-2 **0 of 20** — this stage consumes none.
Holdout sealed.

### 60.0 The standing rule, and the estimation/selection distinction

Every number and functional form below is justified by **architecture,
economics, or arithmetic**. None was chosen by comparing performance.

**Estimation is not selection.** Fitting parameters from data by a
pre-registered PIT procedure — betas, covariances, the momentum-to-return
calibration — is *estimation*, is legitimate, and consumes no trial.
Choosing *among candidate procedures* by comparing Sharpe, PnL, drawdown or
formation rate is *selection* and is forbidden. Nothing in this section may
later be read as a prohibition on estimating; everything in it prohibits
selecting.

All quantities are in **daily units** unless stated; annualization uses 365
(perps trade every calendar day, §2's convention).

### 60.1 Factor model, with ETH orthogonalized

```
  r_i,t = α_i + β_BTC,i · f_BTC,t + β_ETHperp,i · f_ETHperp,t + ε_i,t

  f_BTC,t     = BTC daily simple return
  f_ETHperp,t = residual of ETH return regressed on BTC return
                over the same trailing window (orthogonal by construction)
```

**Why orthogonalize:** BTC and ETH returns are highly correlated; raw
two-column OLS yields unstable, sign-flipping coefficients. With
`f_ETHperp ⟂ f_BTC` in-window, the design matrix is orthogonal, the two
betas are separately interpretable, and `V_i` is (near-)diagonal.

**The estimation window: 90 days — bound by arithmetic, chosen inside the
bound.** The frozen 180-day minimum history (§59.3.2) and the frozen long
momentum window (residuals at lags 2–63, needing 63 daily residuals) impose

```
  window + 63 ≤ 180   ⇒   window ≤ 117 days.
```

Within [63, 117]: longer is better purely by estimation-error arithmetic
(`SE ∝ 1/√T`; Gen-1's 60-day windows produced the §57.8 identifiability
failures), shorter is better under beta drift. **90** is frozen: it improves
on 60 by the factor √(60/90) ≈ 0.82 in SE, and leaves 180−(90+63) = 27 days
(≈ 15%) of slack for data gaps and misalignment. The same 90-day window is
used for the ETH-on-BTC orthogonalization and every §60.7 covariance input —
**one estimation window in the whole system**, so no second window exists to
sweep.

**Estimator, frozen:** equal-weighted OLS on daily simple returns.
EWLS/robust variants are rejected for v1 on parsimony: each adds a half-life
or breakpoint knob with no non-performance way to set it. The per-asset
coefficient covariance

```
  V_i = σ̂²_ε,i · (XᵀX)⁻¹ ,   X = [f_BTC, f_ETHperp]  (90×2)
```

is retained for §60.3; with orthogonalized factors it is diagonal up to
numerical error:  `V_i ≈ diag( σ²_ε/(T·σ²_BTC), σ²_ε/(T·σ²_ETHperp) )`.

### 60.2 Momentum in expected-return units — and the carry-degeneration guard

**The score** (windows and weights frozen in §59.3.2): with residuals ε from
§60.1,

```
  M_i = 0.6 · Σ_{t-21..t-2} ε_i  +  0.4 · Σ_{t-63..t-22} ε_i
  Z_mom,i = cross-sectional z-score of M_i, winsorized at ±3
```

(±3 is a reporting convention, the same clip every z-score in this project
has used; recorded in the manifest as convention, not derivation.)

**60.2.1 The calibration to return units.** One PIT procedure, frozen: a
pooled cross-sectional predictive regression on an **expanding** window,

```
  ε_i,τ+1 = a_τ + b · Z_mom,i,τ + u_i,τ     pooled over all τ ≤ t in the
                                            development era, cross-sections
                                            demeaned
  μ̂_mom,i,t = b̃_t · Z_mom,i,t
```

Expanding rather than rolling **eliminates the window knob entirely** — the
strongest available non-performance justification. Winsorization of the
dependent variable at its own cross-sectional ±3σ, matching the score's
clip.

**60.2.2 Shrinkage, derived from estimation arithmetic.** The pooled slope is
precision-weighted toward zero:

```
  b̃_t = n_t / (n_t + n₀) · b̂_t ,      n_t = number of daily cross-sections
                                        pooled so far,  n₀ = 63
```

`n₀ = 63`: the slope must be supported by at least one full long-momentum
window of cross-sections before it can carry more weight than the prior —
the same evidence standard the signal itself is built on. Before 63
cross-sections exist, `b̃_t` is dominated by the zero prior and the book is
mostly carry, which the guard below makes loud rather than silent.

**Sign floor, deterministic:** if `b̃_t ≤ 0`, then `μ_mom := 0` for that day
and the day is flagged. A negative slope is the data claiming reversal;
trading it would silently run a different strategy, and flooring at the
estimate's sign boundary neither presumes the hypothesis nor flips it.

**60.2.3 CARRY-DEGENERATION GUARD — mandatory, reported every run.**
If `b̃_t → 0` then `μ_total ≈ −F` and RCM silently becomes a pure carry
trade — Gen-1 was exactly this and learned it after the fact (§36: ~60% of
PnL from funding in some configurations). Standing attribution, every run:

```
  s_mom(t) = Σ_i |μ_mom,i| / ( Σ_i |μ_mom,i| + Σ_i |F̂_i| )
```

reported per run and as a trailing 21-day mean. **Threshold: trailing
21-day mean s_mom < 0.5 ⇒ the book is declared "CARRY REGIME — NOT RCM"**
and every report carries that flag until it recovers. 0.5 is the semantic
boundary, not a tuned number: a strategy whose intended return is majority
funding is by definition not the residual-momentum hypothesis. Consequence:
**flag and report — never a silent continuation, never an automatic halt.**

**60.2.4 The funding forecast and the sign convention.** Holding horizon is
one day (§60.4), which spans exactly **3 funding settlements**. Frozen
estimator:

```
  F̂_i(t) = 3 × mean( last 21 settlement rates of i )
```

21 settlements = 7 calendar days: spans a full weekly cycle of the funding
pattern while remaining inside the short momentum window's time scale, and
divides the noise of a single settlement by √21 ≈ 4.6. Units: F̂ is a
1-day expected funding **rate**, the same units as μ_mom.

**Sign convention, explicit (the §58 error, closed):** a positive funding
rate means **longs pay, shorts receive**. With signed optimizer weights the
single expression

```
  μ_i = μ_mom,i − F̂_i        (λ = 1, per §59.3.3)
```

is automatically direction-correct: portfolio carry = −Σ w_i F̂_i, so a
short (w<0) of a positive-funding asset *earns* carry with no case split.
Direction-awareness falls out of the units; no λ exists to tune.

### 60.3 Beta uncertainty is a constraint, not an alpha haircut

**60.3.1 The reliability-multiplier form is REJECTED.**
`R_i = β²/(β²+SE²)` conflates "beta is small" with "beta is uncertain": a
precisely-estimated near-zero beta — the *ideal* asset for a neutral book —
gets R ≈ 0 and is punished hardest. The proposal's §7 form
`1/(1+c·SE)` avoids that pathology but still deducts a hedging-risk quantity
from an alpha estimate, mixing units. Both rejected.

**60.3.2 The chance constraint.** For each factor k ∈ {BTC, ETHperp}:

```
  |β̂_kᵀ w| + z · SE(β_kᵀ w) ≤ ε_β,k ,
  SE(β_kᵀ w) = sqrt( Σ_i w_i² · V_i[k,k] )
```

(independent estimation errors across assets — justified because residuals
are cross-sectionally idiosyncratic by construction; stated as an
assumption). Uncertain hedges become a continuous **risk cost**; nothing
about beta uncertainty touches the momentum signal. With orthogonalized
factors the two per-factor constraints approximately bound the joint
exposure; the joint quadratic version is deliberately omitted from v1
(parsimony) and noted.

**60.3.3 Deriving z and ε_β — the arithmetic.**

*ε_β:* the tolerable residual factor exposure comes from the risk
architecture. The book intends ~10 effective independent positions
(N_eff targets, §60.5). **Rule: the market factor may contribute at most as
much variance as any single intended position**, i.e. at most 1/10 of the
portfolio variance budget:

```
  (b_net · σ_k)² ≤ σ_target² / 10
  ⇒  ε_β,k(t) = σ_target / ( √10 · σ̂_k(t) )
```

with σ̂_k the 90-day factor vol from §60.1 — an *estimated input*, so ε_β is
a frozen **formula**, not a constant. (Illustration only: at σ_BTC = 40%
ann and σ_target = 10% ann, ε_β,BTC ≈ 0.079.)

*z = 1.645:* the neutrality claim must hold at the same confidence this
project has used for every interval since §26 — 90% two-sided / 95%
one-sided. A reporting-convention constant, aligned with the house standard,
not swept.

### 60.4 The event timeline — frozen, identical holding intervals

```
  t 00:00:00 UTC        daily bar closes (close_time 23:59:59.999) — data cutoff
  t 00:00 + compute     PIT view as_of the close; signal, optimizer, gates
  t_exec                open of the first 1-minute bar ≥ 00:01 UTC,
                        + 5 bps adverse (the frozen §56.2 fill model)
  t+1_exec              the same instant next day — mark
```

**Holding interval for BOTH `r_shadow` and `r_actual_price`:
[t_exec, t+1_exec), by construction identical** — both computed
open-to-open on the same 1-minute execution bars, price-only. Any
difference between them is therefore attributable to feasibility and
transition, never to timing (the Gen-1 millisecond-funding class of error,
§30, closed by construction).

**Funding accrual window:** `(t_exec, t+1_exec]`, containing exactly the
08:00, 16:00 and next-day 00:00 settlements — 3 settlements, matching F̂'s
horizon. The 00:00 settlement at entry belongs to the *previous* book
(Gen-1's intra-step order, retained).

### 60.5 The gates — bounded forms and derived thresholds

**60.5.1 Signal coverage, corrected (a correction to §59.4's formula,
recorded here by append; §59 is unedited).** The §59.4 wording admits values
> 1 under renormalization. Frozen bounded form, using pre-feasibility
weights of surviving names:

```
  C_signal = Σ_{i ∈ survive} |w_pre,i|·|S_i|  /  Σ_i |w_pre,i|·|S_i|   ∈ [0,1]
```

Orthogonality note: `C_signal` measures *which alpha was retained*;
`G_realized/G_target` measures *how much exposure was retained*. The
density ρ = C_signal / (G_realized/G_target) is **reported** as a
diagnostic (ρ < 1 ⇒ feasibility preferentially discarded strong signals)
but not gated in v1.

**60.5.2–60.5.4 The four thresholds, each with its derivation:**

- **`N_eff,long ≥ 6` and `N_eff,short ≥ 6`** (independent, per §59.4).
  Two arguments: (i) each RCM leg must be strictly broader than the entire
  Gen-1 leg (5 discrete names) — a leg less diverse than the one whose
  narrowness RCM exists to escape contradicts the architecture that
  motivates Gen 2; (ii) at N_eff = 6 no single name carries more than ~17%
  of leg variance, the largest concentration compatible with calling a leg
  "cross-sectional" rather than "a bet plus passengers". Starting concept
  adopted because the architecture argument lands on the same number.
- **`g_min = G_realized/G_target ≥ 0.70`.** Variance arithmetic: delivered
  variance scales as g², so the book must deliver **at least half its
  intended variance budget**: g² ≥ 0.5 ⇒ g ≥ √0.5 ≈ 0.707, rounded to the
  starting concept 0.70. Below that the strategy is running at less than
  half its designed risk and is a different strategy.
- **`C_signal,min = 0.50`.** The majority-identity principle, the same
  semantic boundary as the carry guard: the traded book must contain at
  least half of the intended signal mass, else it is not an expression of
  the signal that motivated it. (Not a variance argument — signal is
  linear — so the boundary is identity, not arithmetic, and is stated as
  such.)

A day failing any gate is a **skip**, recorded with the failing gate(s),
and handled by §60.6. Per §59.4.5, all development-era performance is
computed on the **full calendar** with formation rate beside every number.

### 60.6 The complete state-transition table

**One common transition for every non-formed category — HOLD-WITH-RISK-CAP —
plus a staleness ceiling.** Derived from RCM's own architecture, not
inherited (§59.11.1):

```
  T(any non-formed day, w_prev):
    1. hold w_prev unchanged, EXCEPT
    2. if drifted gross > G_cap: rescale ALL positions by the single scalar
       that restores gross to G_target (preserves optimizer proportions;
       re-selects nothing)
    3. if the day is the M-th CONSECUTIVE non-formed day, M = 7:
       flatten to cash; remain flat until the next formed day
```

*Why hold:* the signal's horizon is 21–63 days; the day-over-day target
autocorrelation is ≈ 62/63, so one stale day costs little tracking error,
while flattening trades twice (out and back) paying the full §60.7 cost
term for near-zero informational gain.
*Why the single-scalar rescale:* it is the unique transformation that
changes risk without changing selection — any per-name adjustment
re-decides the book on a day the pipeline declared no decision exists.
*Why M = 7:* one third of the short momentum window (21/3): a book stale by
more than a third of the window that motivated it no longer expresses that
window's signal. This ceiling is what prevents "hold indefinitely on
repeated solver failure" — the exact mechanism of Gen-1's runaway leverage
(§13.1) — from recurring.
*Leverage-drift consequence, stated (§59.11.1.4):* between rescale triggers,
gross may drift within (0, G_cap]; the drift is bounded above by the hard
cap and the exposure it represents is bounded in time by M. Layers 2 and 3
(watchdog, kill switch) remain independent backstops.
*Per-category deviation:* none. The only nuance: on `D_operational` days
where the harness cannot trade at all, steps 2–3 are *intended but possibly
unexecutable*; the unexecuted rescale/flatten is logged as an operational
failure of the transition, and the kill switch remains the catastrophic
bound. No named invariant demands a different economic rule per category,
so none is granted (§59.11.1.2).
*Concurrent failures:* the transition is common, so `T` is trivially
deterministic in the failure set (§59.11.1.3).

**60.6.1 Causal precedence for the disjoint calendar (§59.11.2).** Category
= the category of the **first pipeline stage at which the intended decision
became impossible**:

```
  S1 structural-pre   universe/data/factor/metadata insufficiency  → D_structural
  S2 operational      solver or harness failure                    → D_operational
  S3 gates            any §60.5 gate fails                         → D_gate
  S4 execution        execution bar unavailable                    → D_structural
```

A day with gate failures *and* a missing execution bar is `D_gate`: the
decision was already dead at S3. Classification is thereby invariant to
control-flow ordering.

### 60.7 Optimizer — an SOCP with two knobs eliminated

```
  max_w   μᵀw − η·‖w − w_prev‖₁

  s.t.    Σ_i w_i = 0                          dollar neutrality, EXACT
          wᵀ Σ w ≤ σ_target,daily²            vol target as a CONSTRAINT
          |β̂_kᵀw| + 1.645·SE(β_kᵀw) ≤ ε_β,k   k ∈ {BTC, ETHperp}  (60.3)
          Σ_i |w_i| ≤ G_cap = 3.0             runaway backstop (risk layer)
          |w_i| ≤ (G_target/2)/6              single-name cap
```

- **γ is eliminated.** With volatility as a hard constraint at the frozen
  target, a mean-variance risk-aversion coefficient is redundant; removing
  it removes an untunable free parameter.
- **η is not free — it is the cost.** η = 10 bps = the frozen per-side cost
  stack (5 bps taker + 5 bps slippage assumption, §56.2). The turnover
  penalty *is* the transaction-cost model at economic value, the same λ=1
  logic as funding.
- **Dollar neutrality is an exact equality** — costless in a continuous
  optimizer and eliminates a tolerance knob.
- **Single-name cap** `= leg gross / 6`: if every name sat at the cap,
  N_eff = 6 exactly — the cap and the gate are the same number seen from
  two sides.
- **Σ** is the factor-model covariance built from §60.1's single 90-day
  system: `Σ = B Σ_f Bᵀ + D`, Σ_f diagonal (orthogonal factors), D diagonal
  idio variances. No second estimation window exists.
- **Feasibility is guaranteed:** w = 0 satisfies every constraint, so the
  problem is never infeasible; a degenerate market shows up as a near-zero
  book that then **fails the gates** — degeneracy is caught by §60.5, not
  by solver errors.
- **Quantization is downstream:** the optimizer emits continuous `w_pre`
  (the §59.11.3 canonical book); the shared sizing module (§57.3) then
  quantizes, and the gates are evaluated on the sized book.

**Determinism (§59.5):** the problem is an SOCP (the vol and chance
constraints are second-order cones; the L1 term splits linearly). Frozen:
deterministic interior-point solver, single-threaded, assets in
lexicographic order (reproducibility of near-equivalent optima is
guaranteed by determinism of pipeline + solver, uniqueness is not claimed);
primal feasibility tolerance 1e-8; duality gap 1e-8; accepted termination
state: OPTIMAL only — anything else (ALMOST_OPTIMAL included) is
`D_operational`; shadow weight tolerance 1e-5 per name (three orders above
solver tolerance — margin, not precision theatre); max factor-constraint
residual ε_β,k + 1e-6; dollar-neutrality residual ≤ 1e-8·G_target.
**UNRESOLVED: the concrete solver package and version pin** — choosing it
requires installing software, which is implementation; the class
(deterministic SOCP interior-point) is frozen here, the pin is recorded in
the manifest at first install, before any data run.

### 60.8 Kill criteria — exact quantities, from RCM's own architecture

1. **Formation rate:** trailing **63-day formation rate < 0.60** on the full
   calendar ⇒ RCM v1 is abandoned. Derivation ties it to the transition
   ceiling: at p = 0.60 the expected maximum consecutive-failure run over a
   63-day window is ≈ ln(63)/−ln(0.40) ≈ 4.5 days, comfortably inside the
   M = 7 forced-flatten ceiling — the strategy operates without routinely
   being forcibly flattened. Below 0.60, forced flattens become routine and
   the realized strategy is no longer the specified one. (Gen-1's ~78% is
   not used; the number falls out of M and the window.)
2. **The residual-momentum statistic:** pooled cross-sectional **Spearman
   rank IC** between `Z_mom,i,t` and `ε_i,t+1` over the development era,
   with a stationary-bootstrap 90% CI. **Criterion: the CI lower bound must
   exceed 0.** Existence of the effect, not magnitude — the MDE wall (§28)
   makes magnitude claims dishonest at this history length. **This is a
   real-data run whose outcome causes a preference (continue vs kill): it
   consumes one Gen-2 trial when executed**, and its MDE statement per
   §59.3.1 must accompany the pre-registration of the run itself.
3. **Forward feasibility rolling gate:** trailing **21-day formation rate
   < 0.60** during forward validation ⇒ the forward record fails. Same
   threshold as (1) on the shorter standard window — one number, two
   horizons, no new knob.

### 60.9 Parameter manifest

```
  quantity                        value          fixed by
  ------------------------------  -------------  --------------------------------
  factor estimation window        90 d           window+63 ≤ 180 arithmetic; §60.1
  factor estimator                OLS, equal-wt  parsimony (no half-life knob)
  ETH orthogonalization           on 90 d BTC    collinearity; same window
  momentum windows / weights      2-21, 22-63 / 0.6, 0.4   frozen §59.3.2
  z-score winsorization           ±3             house convention (flagged as such)
  calibration regression          pooled, expanding   eliminates the window knob
  slope shrinkage                 n/(n+63) to 0  evidence ≥ one momentum window
  slope sign floor                μ_mom=0 if b̃≤0  deterministic, non-presumptive
  carry-guard threshold           s_mom < 0.5 (21d)   majority-identity semantics
  carry-guard consequence         flag + report  §59.3.3 / Stage-19 spec
  funding forecast                3 × mean(21 settlements)  horizon match; √21 noise
  funding sign                    +rate: longs pay   exchange definition
  lambda                          1 (eliminated) §59.3.3, return units
  chance-constraint z             1.645          house 90%/95% convention
  ε_β,k                           σ_tgt/(√10·σ̂_k)   factor ≤ one position's variance
  vol target                      10% ann        frozen §59.3.2
  G_cap                           3.0            inherited risk-layer backstop
  single-name cap                 leg gross / 6  = N_eff floor seen from the cap side
  η (turnover)                    10 bps         the frozen per-side cost stack
  γ                               eliminated     vol is a constraint
  dollar neutrality               Σw = 0 exact   equality removes a tolerance knob
  N_eff per leg                   ≥ 6            broader than the whole Gen-1 leg
  g_min                           0.70           g² ≥ ½ variance budget
  C_signal,min                    0.50           majority-identity semantics
  transition rule                 hold + scalar rescale at G_cap + flatten at M=7
  M (staleness ceiling)           7 d            21/3: one third of short window
  formation-rate kill             <0.60 @ 63d    run-length vs M arithmetic
  forward rolling gate            <0.60 @ 21d    same number, standard window
  IC criterion                    90% CI lower bound > 0   existence, not magnitude
  solver class                    deterministic SOCP interior-point, 1 thread
  primal/gap tolerances           1e-8 / 1e-8    solver-grade
  shadow weight tolerance         1e-5           3 orders above solver tolerance
  termination accepted            OPTIMAL only   anything else = D_operational
```

**UNRESOLVED (honestly, per §9 of the stage spec):**

1. **The concrete solver package and version pin.** The class is frozen;
   the pin requires installation and is recorded at first install, before
   any data run.
2. Nothing else. Every other quantity above carries a non-performance
   argument. Two entries are conventions rather than derivations and are
   labelled as such in the manifest (the ±3 winsorization; z = 1.645's
   specific confidence level).

### 60.10 Status

§59 and everything earlier are unedited. No code exists for any of the
above. No market, return, snapshot, backtest or performance data was
accessed. Gen-1 budget **15 of 25**; Gen-2 **0 of 20**. Holdout sealed.
Next per §59.9: synthetic/structural implementation and null tests — the
first Gen-2 code, all of it trial-free under §59.3.

### 60.11 AMENDMENT (Stage 19a v3, 2026-08-31) — corrections, contamination-audited

**Appended; §60.0–§60.10 are unedited.** No code; no market, return, backtest
or performance data accessed. Gen-2 **0 of 20** — no RCM performance result
exists, so these corrections cannot have been informed by one. Standing
principle: contradictions are corrected now; unresolved tolerances stay
**UNRESOLVED**; the coding agent makes no strategy-definition decision.

#### 60.11.1 BLOCKING — funding forecast: cadence-aware, horizon-matched, no new knob

**Supersedes** §60.2.4's `F̂ = 3 × mean(last 21 settlements)` and §60.4's
"exactly three settlements" sentence. Both hardcoded an 8-hour cadence; the
Gen-1 ledger (§30) established PIT cadence *inference* precisely because a
fixed cadence was wrong.

```
  F̂_i(t) = Σ_{s ∈ Ŝ_i,(t_exec, t+1_exec]}  f̂_i,s
```

- `Ŝ_i` — the settlement schedule inferred point-in-time from settlement
  timestamps known at `t`, **using the existing Gen-1 cadence-inference
  implementation**. Stage 20 invents no new estimator.
- `f̂_i,s` — estimated from **exactly the trailing 7 calendar days** of
  settlements, preserving §60.2.4's weekly-cycle intent while removing its
  cadence dependence.
- **Observability, with no minimum-count parameter:** the full trailing
  7-calendar-day window must be certified sufficiently observed by the
  **existing** funding-cadence / missing-settlement machinery; if it cannot
  be, the candidate is **unavailable for that decision**. Zero is never
  substituted (the Gen-1 precedent, §2d 5: absent funding history makes a
  name ineligible; otherwise the leg trades cost-free).
- On a cadence change mid-lookback: the settlement set follows PIT
  timestamps; the forward schedule follows the most recent inferred cadence.

#### 60.11.2 BLOCKING — calibration: PIT set-builder AND one horizon

**Supersedes** §60.2.1's pooling over all `τ ≤ t`. **The pooled slope as
specified was contaminated by future information** — at the decision cutoff,
the newest outcome interval had not closed — and its magnitude and sign are
not admissible evidence of anything; every quantity derived from it is
invalid for that timestamp. Non-directional severity: contamination is
disqualifying whichever way it points.

1. **The sample:** `D_t = { (τ, i) : outcome_end(τ, i) ≤ decision_cutoff(t) }`.
2. **The outcome object — a definition gap closed.** §60.1 defines the factor
   model on daily-close returns; §60.4 defines strategy returns on the
   00:01→00:01 execution interval. The calibration target is the **forward
   residual on the execution horizon**:

   ```
   ε_fwd,i,τ = r_exec,i,τ − β̂_BTC,i,τ·f_exec,BTC,τ − β̂_ETH⊥,i,τ·f_exec,ETH⊥,τ
   ```

   with betas fixed from information available at the signal date and
   `r_exec`, `f_exec` measured over the **identical** execution-to-execution
   interval used by `r_shadow` and `r_actual_price` — one horizon everywhere,
   so closing the timestamp leak does not leave a close-to-close vs
   open-to-open mismatch behind it.
3. **Actual timestamps, not index offsets:** at the 00:00:00 UTC decision on
   day D (execution 00:01 D), the newest admissible outcome interval is the
   one **ending 00:01 on day D−1** (opened 00:01 D−2). The interval opened
   00:01 D−1 has not finished and is excluded.
4. **Stage 20 must carry a test that fails if any calibration observation's
   `outcome_end` exceeds the decision cutoff.**

#### 60.11.3 BLOCKING — `G_target` was undefined and the name cap circular

1. **Split:** `G_pre = Σ|w_pre,i|` — the gate denominator, nothing else.
   `G_cap = 3.0` — the exogenous risk-layer backstop.
2. **The §60.7 single-name cap `(G_target/2)/6` is invalidated** — `G_target`
   was never defined. Adopted: `|w_i| ≤ G_cap/12 = 0.25`, a **hard safety cap
   only**, transparently derived (exact dollar neutrality and gross ≤ 3 give
   each leg ≤ 1.5; one sixth of a leg = 0.25). It does **not** ensure
   six-name breadth at low realized gross; the per-leg `N_eff` gate does
   that, post-optimizer.
3. **Rejected with reasons:** the draft cap `|w_i|·σ_i ≤ σ_target/√6` —
   whole-portfolio target applied per-leg; standalone vol ≠ marginal risk
   under correlation; `σ_i` undefined.
4. **Stage-20 synthetic compatibility test — required.** The split creates an
   intentional separation: optimizer → possibly-concentrated target →
   `N_eff` gate → skip. Stage 20 must construct a synthetic case where a
   broad gate-passing book is clearly feasible and verify the pipeline
   **produces** one rather than a concentrated target rejected downstream.
   Failure = an architecture incompatibility found before any real return —
   which is what synthetic testing is for.

#### 60.11.4 Correction — `N_eff` bounds no individual weight

`N_eff = 1/Σp_i²` is the Herfindahl-equivalent count. §60.7's "the cap and
the gate are the same number seen from two sides" was wrong: `N_eff ≥ 6`
does **not** imply any per-name bound. `N_eff,leg ≥ 6` (a portfolio-level
breadth floor) and `|w_i| ≤ 0.25` (an absolute per-name ceiling) are
**complementary and non-equivalent**, and are recorded as such.

#### 60.11.5 Correction — independence is an approximation; the stress test is deferred and fenced

1. The independent-error `SE(β_kᵀw)` and the diagonal `D` in Σ are
   **approximations**: orthogonality to BTC/ETH is not cross-coin
   independence, and correlated residuals make both optimistic.
2. With z = 1.645 applied to the **absolute** constraint, nominal two-sided
   coverage is 90% and the nominal total breach rate is **10%** (not 5%).
3. Stage 20 must **pre-register the correlated-residual stress fixture
   (correlation strength and structure) and its failure criteria together,
   before executing** — with derivations referencing prior invariants. **No
   tolerance is frozen here.** Neither fixture nor criteria may change after
   the result. Failure ⇒ a residual covariance treatment is required before
   any real data.
4. **The fence:** passing the declared synthetic fixture establishes
   robustness **only to that pre-registered scenario**. It does not establish
   that diagonal residual covariance is adequate in the real market.

#### 60.11.6 `g_min` — WITHDRAWN; UNRESOLVED

§60.5's derivation (`g² ≥ 0.5`) implicitly assumed `w_real = g · w_pre` —
a pure proportional scaling. Feasibility **drops and quantizes** names, so
delivered variance is not `g²` of intended variance and the derivation is
invalid. **Both 0.70 and √0.5 are withdrawn.** Renaming the same number an
"exposure-retention threshold" does not supply a derivation.

`g_min` is **UNRESOLVED** and must be fixed before any real-data run, by
either **(a)** a non-performance rationale for a gross-retention invariant,
or **(b)** replacing the gate with a direct predicted-variance-retention
measure using the existing covariance model — (b) is an architecture change
and gets its own derivation. **Stage 20 implements the gate with a symbolic,
configured threshold and no default value.**

#### 60.11.7 `S_i` — ADOPTED by the user's delegate

`S_i = |μ_mom,i|` in the §60.5 coverage formula (which §60 left undefined).
Rationale: signal coverage exists to detect feasibility discarding **the
hypothesis**; scoring with total `|μ_i|` would let a book keep funding-rich
names, drop the momentum names, and still report high coverage while
abandoning residual momentum. This is a strategy-definition decision made
explicitly here by the user's delegate, vetoable by the user; the coding
agent records it and does not re-decide it.

#### 60.11.8 Zero-denominator semantics — split, one part escalated

1. **`G_pre = 0` ⇒ `degenerate_target`** — a named deterministic state at
   the optimizer/feasibility boundary of the §60.6 causal order, classified
   into exactly one calendar category. Never a NaN.
2. **Zero signal mass (`Σ|w_pre||μ_mom| = 0`) ⇒ UNRESOLVED — escalated to
   the user.** With `S_i = |μ_mom|` this case is exactly *momentum has
   vanished; only funding remains*. §60.2.3 pre-registered the carry regime
   as **flag and report — never a halt**; converting the zero denominator
   into `degenerate_target` would silently amend that into "carry regime ⇒
   form no book", a strategy-layer change. The options:
   - **(a)** coverage is *not applicable* with no momentum mass to retain;
     the book may form, carrying the literal `CARRY REGIME — NOT RCM` label —
     consistent with the rule already on record;
   - **(b)** a deliberate amendment making the carry regime a halt.
   **Delegate's recommendation: (a).** Until the user decides, Stage 20
   treats the state as UNRESOLVED — raise, do not choose. NaN never decides
   strategy state; neither does a side effect.

#### 60.11.9 Manifest delta (§60.9 is not edited)

```
  funding forecast     SUPERSEDES the §60.9 row          §60.11.1
  calibration sample   SUPERSEDES the §60.9 row          §60.11.2
  optimizer name cap   SUPERSEDES the §60.9 row (0.25)   §60.11.3
  g_min                WITHDRAWN — UNRESOLVED            §60.11.6
  S_i                  NEW — adopted by delegate         §60.11.7
  zero momentum mass   UNRESOLVED — user decision        §60.11.8
  stress thresholds    UNRESOLVED — Stage 20 pre-registers  §60.11.5
```

## 61. Stage 20 — RCM v1 synthetic implementation: built, attacked, one real finding

**2026-08-31.** The first Generation-2 code: `rcm/` (seal, timeline, factors,
momentum, funding, optimizer, gates, state machine, attribution) plus 30
adversarial/null tests. **No real market or return data touched any RCM code
path** — an import-level test enforces it. Suites **198 passed + 1 xfailed**
(the xfail IS a finding, below); all Gen-1 suites green; live paper state
provably untouched. **Gen-2 budget 0 of 20 — unchanged, as required.**

### 61.1 The solver pin (resolving §60.7's one UNRESOLVED item)

**Clarabel 0.11.1 via cvxpy 1.9.2, pinned exactly in `pyproject.toml`.**
Engineering rationale, not strategy: the problem is an SOCP; assembling cone
matrices by hand is a correctness risk the modeling layer eliminates, and
determinism follows from a fixed problem-construction order (assets
lexicographic, asserted) plus a deterministic interior-point solver.
Verified: identical inputs give identical weights to < 1e-9; non-OPTIMAL
termination raises `OperationalFailure`; post-solve residuals checked at the
frozen maxima. w = 0 confirmed always feasible — a zero-alpha market yields a
near-zero book, caught by the gates, never by a solver exception.

### 61.2 FINDING F-1 — the optimizer and the N_eff gate are incompatible on
realistic alpha shapes

The §60.11.3.4 required fixture **failed, and the failure is the deliverable**:

```
  alpha profile   N_eff long   N_eff short   6/6 gate
  flat |μ|         ~9.9          ~9.8          PASS
  linear           5.4 – 5.9     5.6 – 5.9     FAIL  (every seed)
  steep (μ²)       3.7           3.6 – 3.8     FAIL  (badly)
```

**Mechanism:** the objective is linear in w, so it concentrates in proportion
to alpha dispersion; only the quadratic vol constraint spreads. **The more
informative the signal's cross-sectional shape, the more the optimizer
concentrates — and the gate then rejects the day.** Under real momentum
(graded, often steep profiles) this predicts chronic `D_gate` days: RCM's
version of Gen-1's formation failure, found synthetically before a single
real return, which is exactly what §60.11.3.4 said synthetic testing is for.

**Not patched** (Stage 20 §6: tuning a frozen quantity to make a fixture pass
is forbidden). The fixture is kept as a **strict xfail** so the suite stays
readable while the failure stays asserted — if it ever starts passing,
something changed and must be explained. **Resolution is a Stage-19-class
decision for the user**, options stated without preference:

- (a) add a concentration control to the optimizer objective/constraints —
  an architecture change requiring its own §59.4.4-compliant derivation;
- (b) re-derive the N_eff floor — only with a non-performance argument;
- (c) accept chronic gate-skips and rely on the transition rule — noting
  this runs directly against the 0.60 formation-rate kill criterion (§60.8).

### 61.3 Two mechanical resolutions, recorded and vetoable

Both were forced by §60.11 withdrawing symbols that §60.6 still referenced;
both are implementation-mechanical with stated rationale, neither is silent:

1. **The §60.6 rescale target.** "Restore gross to G_target" references a
   symbol §60.11.3 withdrew. Implemented as **clamp to G_cap = 3.0** — the
   minimal intervention that enforces the cap without re-deciding selection.
2. **`degenerate_target`'s calendar category = `D_structural`.** §60.11.8.1
   demanded exactly one category without naming it. `D_structural` is the
   attribution-consistent choice: §59.11.3.3 defines the shadow domain by
   "a shadow target exists", and a zero target is precisely a day on which
   none does. Mapping it to `D_gate` would inject meaningless zeros into
   Δ_gate.

### 61.4 What the fixtures established (each is a test that fails if false)

- **The seal is structural**: any one-day intersection with
  [2025-01-01, 2026-07-31] refuses; unlock needs an explicit token AND a
  marker in **committed** git history (working-tree entries count for
  nothing); an entry committed after the request is refused — no
  back-dating. The commit timestamp, not the entry's text, is the authority.
- **No RCM module can reach real data** (import-level, plus no venue or
  store names in source).
- **The calibration is PIT**: the §60.11.2.3 boundary pair behaves exactly
  as specified (D−2 admissible, D−1 excluded), and a poison-pill unclosed
  cross-section with a planted slope of 100 does not move `b̂`.
- **One horizon exists**: `r_shadow`, `r_actual_price` and the calibration
  outcome share one interval function; a one-minute offset is detectable.
- **Funding is cadence-aware with no new knob**: an 8h symbol yields 3
  forward settlements, a 4h symbol 6, from the same code path; a holey
  window makes the candidate unavailable (never zero-funded); an AST test
  proves the module contains no numeric constant beyond the day length and
  the frozen 7.
- **ETH⊥ works and matters**: exact in-window orthogonality, and the fixture
  shows raw two-column OLS swinging >0.3 in beta across half-samples.
- **Chance-constraint coverage ≈ nominal under independence** (breach ~10%
  where binding) — the correlated case is precisely the deferred §61.5 test.
- **UNRESOLVED raises**: `g_min` (no default exists), zero momentum mass
  (user decision pending, cannot fall through into an unlabelled carry
  book), uncertified funding windows.
- **Null canaries**: noise in ⇒ `b̂ ≈ 0`, `b̃` smaller, carry flag fires with
  the literal label; shuffling outcomes destroys a genuine planted slope
  (+0.004 → ~0); the machine manufactures no alpha.
- **Transitions and precedence**: one common rule verified across all three
  non-formed categories; the single-scalar property asserted; M=7 flatten
  and counter-reset verified; classification is a pure function of the
  failure set (permutation-invariant by construction).
- **Attribution recovers planted values**: Δ_gate and Δ_transition to
  tolerance, CIs bracket, the transition rule is named beside every number,
  the reporting tuple carries all six fields, and the formed-days label is
  the literal §59.11.4 string.
- **The Stage-17 quantization trap reproduces inside RCM** ($5.04 intended →
  $4.91 executable → rejected) because RCM uses the same shared module.

### 61.5 The correlated-residual stress test — PROPOSED, NOT RUN (§60.11.5.3)

**Fixture (fixed now):** 30 synthetic assets in 3 blocks of 10; intra-block
residual correlation ρ ∈ {0.3, 0.6} (two runs), cross-block 0; betas
~N(1, 0.3²) with per-asset SEs as configured in the §61.4 fixtures; the
frozen optimizer and gates unchanged.

**Failure criteria (fixed now), with the invariant chain:** the Gen-1 risk
architecture accepted a 20% design drawdown cap against a 30% kill switch —
a headroom factor of **1.5** (§43.3 / §29, inherited unchanged by §59.6).
Under-modelled volatility consumes that headroom multiplicatively, so:

1. **σ_realized / σ_model ≤ 1.5** on the solved book under the fixture; and
2. **chance-constraint breach frequency ≤ 2·Φ(−z/1.5) = 2·Φ(−1.097) ≈ 27.3%**
   — the same 1.5 factor propagated through the normal map at the frozen
   z = 1.645. One invariant, two consequences; no new tolerance invented.

**Per §60.11.5.3–4:** neither fixture nor criteria may change after the
result; passing establishes robustness **only to this scenario**, not to the
real market. **Awaiting the user's approval (ledger entry) before any
execution. Not run.** On failure: a residual covariance treatment becomes a
prerequisite stage — not a patch here.

### 61.6 Real-data prerequisites — what still blocks trial 1 of 20

```
  OPEN    g_min                    §60.11.6 — route (a) or (b), user
  OPEN    zero-momentum semantics  §60.11.8 — user decision (delegate: (a))
  OPEN    stress test              §61.5 — approval, run, pass (or covariance
                                   treatment adopted)
  OPEN    FINDING F-1              §61.2 — optimizer/gate incompatibility,
                                   Stage-19-class resolution
  DONE    solver pin               clarabel 0.11.1 / cvxpy 1.9.2, recorded
  DONE    determinism tests        green at the frozen tolerances
  DONE    funding observability    resolved by reusing Gen-1 machinery with
                                   an equality rule — no new parameter
```

**Gen-1 budget 15 of 25; Gen-2 0 of 20, as this stage was required to end.
Holdout sealed — and the seal is now code.**

## 62. Stage 19b — architecture amendment after F-1, pre-registered 2026-08-31

**No real market or return data. Gen-2 stays 0 of 20. Holdout sealed.** This
section records the decisions BEFORE the implementing code exists; §62.7 will
append the verification results afterwards. §61 is unedited.

### 62.0 Wording correction to §61.2

§61.2's "realistic alpha shapes" and "predicts chronic `D_gate` days under
real momentum" **overreached**: no Gen-2 real return has been examined. The
fixtures are **non-flat synthetic alpha profiles**. The established finding
is: **the current optimizer does not structurally guarantee the frozen
breadth invariant.** It does *not* establish that real RCM will chronically
fail formation. The first is sufficient to block progression, and is what
blocks it.

### 62.1 Part A — the witness, non-vacuous, and the diagnosis

The §61.2 record showed concentrated optimizer outputs but no feasible broad
alternative — the diagnosis was incomplete. Constructed now, on the identical
six instances (linear and steep profiles, seeds 21/22/23), **at exactly the
rejected target's gross** (non-vacuity: a witness that wins by scaling toward
zero proves nothing, since εw shrinks every risk quantity while preserving
N_eff; gross equality is coefficient-free and auditable):

```
  profile  seed  G_reject  reject NeffL/S   witness NeffL/S  vol slack  chance slack
  linear    21    0.8759     5.42/5.92        6.00/6.00       1.2e-04     1.3e-08
  linear    22    0.8835     5.85/5.56        6.00/6.00       1.2e-04     8.5e-03
  linear    23    0.8863     5.71/5.76        6.00/6.00       1.2e-04     1.9e-02
  steep     21    0.7030     3.67/3.61        6.00/6.00       1.1e-03     3.6e-03
  steep     22    0.7158     3.81/3.70        6.00/6.00       1.1e-03     1.5e-02
  steep     23    0.7158     3.79/3.78        6.00/6.00       1.1e-03     2.7e-02
```

Every §60 constraint satisfied with recorded slack; dollar residual ≤ 8e-17;
max single weight 0.104 against the 0.25 cap. **A broad gate-passing book
exists at the rejected gross on all six instances and the optimizer returns
the concentrated reject anyway: construction/gate incompatibility PROVEN.**
Part B runs.

### 62.2 Part B — the admissibility judgments

**Principle adopted (B.1):** per-leg effective breadth defines what an RCM
portfolio is, so construction must be breadth-aware. Rejected: (b)
re-deriving the floor because a fixture returned 5.4 — fitting the invariant
to a failed test; (c) tolerating chronic skips — recreates Gen-1's failure
and contradicts the frozen 0.60 formation-rate criterion.

**Candidates, judged by the B.2 rule** (no new free coefficient; breadth
enforced on the net portfolio mathematically):

1. **Split-variable SOC form — REJECTED.** `w = w⁺ − w⁻` with per-leg SOCs is
   coefficient-free but admits padding: `w⁺_i = 0.10, w⁻_i = 0.09` satisfies
   both leg constraints with a net 0.01 position. Exact complementarity
   `w⁺_i·w⁻_i = 0` is non-convex and cannot enter the SOCP without changing
   problem class to mixed-integer/non-convex — a solver-architecture change
   nobody has derived a need for. **Empirical absence of padding is not
   sufficient**; rejected as non-exact.
2. **Concentration penalty — REJECTED.** No prior invariant supplies its
   coefficient, and "the coefficient that makes the xfail pass" is not a
   derivation.
3. **Leg membership pre-assigned by signal sign — the ONE admissible
   candidate.** Assign each name to L or S by `sign(μ_i)` before the
   optimizer (`μ_i = 0` ⇒ excluded); constrain `w_i ≥ 0` on L, `w_i ≤ 0` on
   S. Split variables do not exist, so padding is **impossible by
   construction**; the per-leg SOCs `‖w_leg‖₂ ≤ (Σ|w_leg|)/√6` act on
   genuinely disjoint sign-restricted subvectors — **convex, exact,
   coefficient-free**, using the frozen 6. The mathematical guarantee: any
   nonzero leg has `N_eff ≥ 6`; an all-zero leg produces `G_pre = 0` and
   lands in `D_degenerate` (§62.4). **Stated cost:** the optimizer loses the
   freedom to hold a name against its signal for hedging, so the chance
   constraint can bind harder and non-formed days can rise — semantically
   close to what the hypothesis means (hold a name only on the side its
   residual momentum indicates), but a real cost, measured synthetically by
   the B.4 hedge-infeasibility fixture and reported, not assumed away.

**B.3 safeguard applied:** exactly one candidate satisfies the admissibility
rule, so no selection among survivors occurs and no synthetic metric is
consulted. **ADOPTED, conditional on the B.4 fixtures passing:** the
sign-pre-assigned construction with per-leg SOC breadth. The F-1 xfail may
flip **only** through this adoption, and §62.7 must explain the flip.

### 62.3 Part C — the stale-book risk invariant (supersedes §61.3.1)

§61.3.1's clamp-to-`G_cap = 3.0` permitted a book formed at 0.50 gross to
drift to 3.0 — Gen-1's leverage-drift failure by another route, and I wrote
it. Adopted instead, coefficient-free:

```
  G_ref = gross of the last successfully formed executable portfolio
  while carrying a stale book:  G_t ≤ G_ref
  on any non-formed day:        α_t = min(1, G_ref / G_t)   applied to all
```

**Downscale only.** Stale exposure grown past its last valid scale is
reduced to that scale; stale exposure that has shrunk is **never levered back
up** — increasing exposure without a current valid decision is adding risk
without a strategy. No deadband, no threshold. `G_cap` remains the
catastrophic backstop only. Lifecycle: `G_ref` undefined before any
formation; set at each successful formation; **cleared by the M=7 forced
flatten**; re-set at the next formation; persisted as bot-owned state
(§54.2 class) across restarts.

### 62.4 Part D — `D_degenerate`, a fifth calendar category (supersedes §61.3.2)

A valid `w = 0` is an **economic decision** — expected returns net of costs
do not justify exposure — not a data failure. §61.3.2's `D_structural`
misclassified it, and `D_formed` would be worse: repeated zero books would
let the 0.60 formation-rate criterion read 90% while exposure was held on
20% of days.

```
  D = D_formed ∪ D_gate ∪ D_structural ∪ D_operational ∪ D_degenerate
```

`D_degenerate`: counts in the calendar denominator; **not** formed; **not** a
gate failure; `r_shadow = 0` exactly; **excluded from Δ_gate** (a zero target
is not a rejected feasible target); reported as its own field **appended** to
the §59.11.4 tuple; placed in the causal order at the optimizer stage
(structural → operational → **degenerate** → gates → execution). Exactly one
category per date; no NaN.

### 62.5 Part E — the stress fixture: §61.5 is NOT APPROVED; status UNRESOLVED

The §61.5 chain fails Part E's audit, and the audit is right: the
correlations ρ ∈ {0.3, 0.6} reference no invariant; the 1.5 factor derives
from Gen-1's §43.3 20% **selection** cap, which §59.6 did not inherit as an
RCM model-error tolerance; and drawdown headroom does not map linearly to
volatility-model error under path dependence.

Attempting a re-derivation from RCM-owned invariants, honestly: **no RCM
invariant speaks to residual correlation strength** (σ_target, G_cap, ε_β
and the 10% nominal breach say nothing about how correlated crypto residuals
are), and any vol→drawdown mapping through the 30% kill switch requires a
distributional assumption nobody has defended. One candidate route is noted
for a future derivation — bounding the *unmodeled* variance share by the
same one-position budget that sizes ε_β — but it conflates a hedging budget
with a model-error budget and is **not defended here**.

**The stress test is UNRESOLVED.** Per Part E, ending this stage with that
status is acceptable; inventing ρ = 0.5 is not. It remains a real-data
prerequisite in exactly this state.

### 62.6 Part F — pending user decisions (unchanged, still blocking trial 1)

`g_min` (§60.11.6, routes (a)/(b)) and zero-momentum semantics (§60.11.8,
delegate recommends (a)).

### 62.7 Verification — the Stage-20 suite re-run under the amended architecture

**Implemented after the §62.1–§62.6 decisions were committed, exactly as
recorded there. Suites: 210 passed, 0 failed, 0 xfailed.** All Gen-1 suites
green; live paper state provably untouched; no real data touched any RCM
path. **Gen-2 remains 0 of 20.**

**The F-1 xfail flipped — and only through the adopted Part-B form.** The
sign-pre-assigned construction with exact per-leg SOC breadth
(`‖w_leg‖₂ ≤ Σ|w_leg|/√6` on sign-restricted subvectors) passes the original
F-1 fixture on **all six instances** (linear and steep, seeds 21/22/23), with
every frozen quantity unchanged — the 6, σ_target, ε_β, the 0.25 cap, G_cap —
verified by a dedicated fixture. Nothing was tuned; the construction changed,
by the §62.2 adoption.

**B.4 fixtures, all green:**

- **Sign agreement** (the padding analog for this form): no name is ever held
  against its signal, and zero-signal names hold exactly nothing — asserted
  per position, not sampled.
- **The hedge-infeasibility fixture reports the stated cost:** with all
  long-signal names at beta 1.6 and short-signal names at 0.4, the chance
  constraint throttles gross to ~ε_β/0.6 — a near-degenerate book. This is
  the §62.2 stated cost of the form, measured and reported, not hidden: on
  days when neutrality genuinely requires holding a name against its signal,
  the restricted construction shrinks toward `D_degenerate` instead.
- **G_ref lifecycle:** set at formation; a book formed at 0.30 gross that
  drifts to 2.9 (still under G_cap) is scaled back to **0.30, not allowed to
  run to 3.0** — the exact hole in my §61.3.1; shrunk exposure is never
  levered back up; the M=7 flatten clears the reference.
- **D_degenerate's five properties:** in the denominator; not formed (90%
  formation cannot hide 10% zero-books — asserted); not a gate failure;
  `r_shadow = 0` exactly; **excluded from Δ_gate** — proven by planting ±99
  on degenerate days and asserting the statistic does not move at all. The
  causal slot (structural → operational → degenerate → gates → execution)
  verified, including the operational-beats-degenerate ordering.
- The reporting tuple carries **`degenerate_rate` appended after the
  original six fields** — position asserted, not just presence.

**One numerical-hygiene note, recorded because it looks like a threshold
change and is not:** the adopted SOC binds at exactly 6, and interior-point
precision leaves realized N_eff ≈ 6 − 4e-8. The gate comparison uses
`6 − 1e-6` — the same 100×-above-solver-precision margin logic §60.7 froze
for the shadow tolerance. The frozen 6 is unchanged; the comparison is
protected from its own solver's last digit.

**Status.** The architecture now structurally guarantees the breadth
invariant it previously only checked after the fact. Still blocking trial 1
of 20: `g_min` (§60.11.6), zero-momentum semantics (§60.11.8), and the
stress test — **UNRESOLVED per §62.5**, awaiting a defensible derivation or
a user decision to supply one. Gen-1 **15 of 25**; Gen-2 **0 of 20**.
Holdout sealed.

### 62.8 AMENDMENT (Stage 19c v2, 2026-08-31) — leg membership must be shift-invariant

**Appended; §62.0–§62.7 unedited. No real data; Gen-2 stays 0 of 20; holdout
sealed. Status discipline: specified ≠ verified — nothing below is RESOLVED
until the §62.8.3 verification is appended.**

#### 62.8.1 The bug, in the construction I adopted in §62.2

§62.2 assigns leg membership by raw `sign(μ_i)`. The optimizer is exactly
dollar-neutral, `1ᵀw = 0`, so for any constant c:

```
  (μ + c·1)ᵀw = μᵀw + c·1ᵀw = μᵀw
```

**The economic problem is invariant to a common shift of all expected
returns; raw sign is not.** `μ = (−.02, −.01, +.01, +.02)` admits 2L/2S;
`μ + .03·1` is economically identical yet assigns every name long-only, and
neutrality forces w = 0. The construction's output depended on an arbitrary
zero level of the forecast.

**Carry-regime interaction:** with `μ_mom = 0`, `μ_total = −F̂`; if all
funding rates are positive, raw sign makes every name short-only and the
labelled CARRY REGIME book of §60.11.8(a) cannot form at all. The algebra
stands alone; no empirical claim about typical funding is made or needed.

#### 62.8.2 The correction — a derivation, not a preference

Let `P = I − (1/N)·1·1ᵀ`, the orthogonal projection onto the dollar-neutral
subspace. For every feasible w, `μᵀw = (Pμ)ᵀw`, so

```
  μ̃ = P·μ_total,   i.e.  μ̃_i = μ_total,i − μ̄_total
  μ̃_i > 0 ⇒ L      μ̃_i < 0 ⇒ S      μ̃_i = 0 ⇒ excluded
```

is **the canonical component of expected return the feasible set can see**;
the discarded component lies in a direction it is mathematically insensitive
to. Mean-centering is not one shift-invariant choice among several
(median-centering is also shift-invariant) — it is the **unique projection
implied by the existing constraint**. No coefficient, no threshold, no
return-data selection, no trial.

- **Total μ, not μ_mom:** membership uses `μ_total = μ_mom − F̂`. With
  `μ_mom = 0`, above-average funding goes short and below-average long — the
  common funding level has no value under neutrality, only the
  cross-sectional difference does. The carry book is well-defined.
- **Ordering, frozen — no recursive membership:**
  `PIT structural eligibility → funding/data eligibility → μ_total → μ̄ (once,
  over that eligible set) → μ̃ → sign partition → optimizer`. The mean is
  never recomputed after removing centered-sign names — that would be a
  hidden iterative selection rule.

#### 62.8.4 `D_degenerate` semantics broadened (supersedes §62.4's gloss)

§62.4 glossed w = 0 as "expected returns net of costs do not justify
exposure". Under the adopted construction w = 0 also arises from **valid
constraints interacting**: 4 positive vs 26 negative centered names (the long
leg cannot reach N_eff ≥ 6, so the SOC zeroes it and neutrality zeroes the
other), or the chance constraint throttling the sign-restricted book
(§62.7's own fixture).

> **`D_degenerate` = the optimizer stage completed without structural or
> operational failure but produced no meaningful nonzero admissible target.
> This may arise from economic no-trade OR from the interaction of valid
> portfolio constraints, and must not by itself be read as evidence that
> expected alpha was zero.**

Each `D_degenerate` day records its **cause** — `no_trade` or
`constraint_interaction` with the binding constraint named — and attribution
may not say "the model saw no opportunity" without that decomposition.

#### 62.8.5 Wording corrections to §62.2 (append; §62.2 unedited)

- "the ONE admissible candidate" → **"the simplest admissible candidate
  within the retained convex SOCP architecture"** — mixed-integer
  complementarity is another coefficient-free route, rejected for complexity,
  not proven nonexistent.
- "hold a name only on the side its residual momentum indicates" → **"hold a
  name only on the side indicated by its cross-sectional total
  expected-return advantage, after momentum and funding are combined."**

#### 62.8.6 Status before verification

F-1's structural resolution is **specified, not verified**. §62.8.3 (the
verification append) must show: common-shift invariance failing on the OLD
raw-sign path and passing on the corrected one, both shift signs, with a
nonzero w_prev case; sign agreement re-based to `sign(μ̃)`; the carry-regime
formation fixture; and all §62.7 fixtures green with every frozen quantity
unchanged. **Only then is F-1 marked RESOLVED.** Still blocking trial 1:
`g_min`, zero-momentum semantics, residual-correlation robustness.

#### 62.8.3 Verification — appended after the runs, as §62.8.6 required

*(Numbered out of order by design: §62.8.3 was reserved in the
pre-verification commit for this append. Everything above is byte-identical;
sha256 of the file before this append is recorded in the commit message.)*

**Step 1 — the OLD raw-sign path, recorded FAILING before removal** (synthetic
instance, 20 names, μ = linspace(+0.004, −0.004), seed-21 market):

```
  c=+0.006: gross 0.9011 -> 0.0000, max|dw|=0.1040, membership identical: False
  c=-0.006: gross 0.9011 -> 0.0000, max|dw|=0.1040, membership identical: False
```

Economically identical problems; the book vanished under a constant shift.
The bug was real, measured, and is on the record.

**Step 2 — the corrected path (μ̃ = P·μ_total, mean computed once), PASSING:**

```
  w_prev=0       c=+0.006: max|dw|=1.02e-13  membership identical: True  gross 0.9011
  w_prev=0       c=-0.006: max|dw|=8.14e-14  membership identical: True  gross 0.9011
  w_prev nonzero c=+0.006: max|dw|=1.88e-14  membership identical: True  gross 0.9766
  w_prev nonzero c=-0.006: max|dw|=6.15e-15  membership identical: True  gross 0.9766
```

Machine precision, both shift signs, turnover term included. The old path is
removed; the permanent 4-way parametrized test (`tests/test_rcm_stage19c.py`)
pins the invariant at ≤ 1e-8 with membership equality asserted.

**§3.2 sign agreement, re-based to sign(μ̃).** The 19b fixture now plants
μ̃ = 0 (not raw 0) via a dyadic construction — every value k·2⁻¹¹, shift
2⁻⁸, so all sums and means are EXACT in float and the planted names are
exactly zero after the optimizer's own centering. Raw and centered signs
disagree on 10 of 20 names in the fixture; the book (gross 0.8961) follows
μ̃ on every name and the two μ̃ = 0 names hold exactly nothing.

**§3.3 carry-regime formation.** μ_mom ≡ 0, F̂ = linspace(5, 85) bps, all
positive: the construction now FORMS the book raw sign made impossible —
gross 0.8838, shorts exactly the above-average-funding names, N_eff ≥ 6 both
legs — and the zero-momentum-mass state still raises UNRESOLVED downstream,
because §60.11.8 remains the user's decision. Formation is the construction's
property; the semantics stay escalated.

**§62.8.4 cause decomposition, verified — with one property worth recording:**

- `breadth`: the ledger's own example (4 positive / 26 negative centered
  names) yields the zero book and the cause string names the counts against
  the frozen 6.
- `chance`: the chance constraint is bounded, not conic — near w = 0 it is
  always slack, so it can THROTTLE a book but never zero one by itself. At
  SE = 1e4 the fixture leaves a 1.5e-5-gross micro-book (a gate-failure day
  once `g_min` exists, NOT D_degenerate); only when the throttle falls below
  solver precision (SE = 1e6) does the zero-clean produce the exact-zero
  book, and the diagnostic re-solve without the chance constraints then
  names `constraint_interaction:chance`. §62.7's hedge-infeasibility fixture
  is therefore gate territory, not degenerate — a clarification of the
  §62.8.4 example, verified rather than assumed.
- `no_trade`: a ±1e-6 centered forecast against the frozen η = 10 bps yields
  the zero book with the economic cause.

**§3.4 full re-run.** **219 passed, 0 failed** (210 from §62.7 + exactly the
9 new 19c tests). Every frozen quantity unchanged: the 6, σ_target, ε_β,
G_cap = 3.0, the 0.25 name cap, η = 0.0010, z = 1.645, SOLVER_TOL = 1e-8,
N_EFF_NUMERICAL_TOL = 1e-6. Working tree touched only `rcm/optimizer.py` and
the RCM test files; live paper state untouched; `scan_secrets` clean over
159 tracked files. **No real data touched any RCM path. Gen-2 remains
0 of 20. Holdout sealed.**

**FINDING F-1: RESOLVED.** The breadth requirement is now part of the
optimizer's CONSTRUCTION (sign-pre-assigned legs with exact per-leg SOC,
membership by the projection μ̃ = P·μ_total), the construction is invariant
to the forecast's arbitrary zero level, and the chain is closed — not to be
revisited unless another synthetic invariant breaks. Still blocking trial 1
of 20, unchanged: `g_min` (§60.11.6), zero-momentum semantics (§60.11.8),
residual-correlation robustness (§62.5).

## 63. Stage 21 — three decisions recorded, two implemented, one measured (2026-08-31)

**Prerequisite met: §62.8.3 shows F-1 RESOLVED post-verification. Appended
before any Stage-21 code. Gen-2 stays 0 of 20; holdout sealed; the Gen-2
runner's hard rejection of 2025-01→2026-07 stays in force. No performance
quantity is computed anywhere in this stage.**

### 63.1 The decisions (Part A), verbatim, dated 2026-08-31

#### 63.1.A.1 Zero-momentum semantics — USER DECISION: (a), trade

When `Σ|w_pre|·|μ_mom| = 0`, signal coverage is **N/A** (a distinct value;
not a gate failure); the book may form under the §62.8 centered construction
and carries the literal label `CARRY REGIME — NOT RCM` per §60.2.3. The rule
on record, confirmed. **§60.11.8 RESOLVED.** The `Unresolved` raise in
`c_signal` is removed by this decision and only by this decision.

#### 63.1.A.2 Exposure retention — USER DECISION: 40% of intended variance

The withdrawn gross gate (§60.11.6) is replaced by

```
  V_ret = (w_realᵀ Σ w_real) / (w_preᵀ Σ w_pre)  ≥  0.40
```

Recorded as **the risk owner's preference, stated before any Gen-2 return
exists** — same class as the 10% vol target and the 30% kill switch; not
derived from and not adjustable by performance. Units explicit: 40% of
*variance* ≈ 63.25% of intended *volatility* under proportional scaling; the
user has been informed. `G_realized / G_pre` stays in the tuple as
**diagnostic only**. **§60.11.6 RESOLVED** (route: replaced by a
user-preference gate; the withdrawn g² ≥ ½ derivation stays withdrawn).

**A.2.1 Σ is the optimizer's covariance model, whatever it is at freeze.**
Not "the diagonal model as of today": Part D exists precisely because the
diagonal approximation may be replaced before RCM v1 freezes. `w_pre` and
`V_ret` must always use **the same** estimator — one risk model, two uses.
The **0.40 is permanently fixed**; the model it is evaluated under is
whatever the frozen specification adopts.

**A.2.2 Companion invariant — the absolute ceiling still binds.** `V_ret` is
a lower bound only and does not prevent `V_ret > 1`. The executable book must
still satisfy the frozen `w_realᵀΣw_real ≤ σ²_target,daily`. `w_pre`
satisfies it by construction; quantization and composition changes happen
downstream and could regain risk (dropping a hedging short while its longs
survive RAISES modeled variance). Enforcing an existing invariant, not a new
tolerance.

#### 63.1.A.3 Residual-correlation robustness — DELEGATED

To the two reviewers, fixture and criteria both. Process: (i) Part D measures
development-era residual-correlation **structure**; (ii) the delegates
jointly record fixture + criteria + derivation in a §63 append **before**
any stress test executes; (iii) neither changes afterward.
**§62.5: UNRESOLVED → DELEGATED-PENDING-MEASUREMENT.**

**A.3.1 Naming, fixed now.** The resulting requirement is
**development-informed robustness calibration** — legitimate because
2020–2024 is the development set — and **may never later be cited as
independent evidence that the covariance model generalizes.**

#### 63.1.A.4 Reordering amendment — recorded explicitly

§59.9 fixed `synthetic/structural → 2020–2024 development`, and §60.11.5
placed the correlated-residual test in the pre-real-data structural work.
Stage 21 departs from that:

> §63 authorizes **one narrowly scoped development-era structure
> measurement** before completion of the correlated-residual synthetic
> stress test. This is a **deliberate amendment** to the prior
> synthetic→development ordering, justified because no defensible
> correlation fixture can be derived from existing architecture alone
> (§62.5). It accesses no alpha or performance result and **authorizes no
> other development-data use** before the stress requirement is frozen.

Consequence for the §59 no-real-data rule: it stands unweakened for every
`rcm/` module (the import-level test is unchanged); the one authorized
measurement module lives OUTSIDE `rcm/`, and its own quarantine is the
inverse — it may not reach portfolio, gate, optimizer, or PnL code.

### 63.1.5 Implementation interpretations — recorded BEFORE code

The five gaps between Part A and code, closed here so nothing is decided
inside an editor:

1. **Coverage N/A representation:** the string `"N/A"` — distinct from 0, 1,
   and NaN as required; JSON-serializable; arithmetic on it fails loudly
   rather than silently averaging.
2. **Carry label rule (Part B):** the label fires iff EITHER frozen condition
   holds — exact-zero momentum mass today (`Σ|w_pre||μ_mom| = 0`) OR the
   §60.2.3 trailing rule (21-day mean s_mom < 0.5). Absence requires both
   false. No new threshold; the OR of two existing rules.
3. **V_ret near-zero-denominator split (Part C):** `w_pre = 0` (exact, after
   the frozen zero-clean) ⇒ `D_degenerate` as before. `w_pre ≠ 0` with
   non-finite denominator, or modeled vol below the frozen solver precision
   RELATIVE to the book — `√(w_preᵀΣw_pre) < SOLVER_TOL · G_pre` — ⇒
   covariance/model integrity failure: fail closed, `D_structural`, alert.
   Scale-free on purpose: a legitimately throttled micro-book has per-unit
   vol ~ idio vol and is NOT flagged; only a nonzero book in a modeled
   nullspace is. Reuses the frozen 1e-8; no new threshold.
4. **Absolute ceiling comparison (A.2.2):** evaluated in the gate layer as
   `w_realᵀΣw_real ≤ σ²_target · (1 + 1e-6)` — the same
   100×-above-solver-precision relative margin logic §60.7 froze for the
   shadow tolerance and §62.7 reused for N_eff. The frozen 10% target is
   unchanged; the comparison is protected from the solver's last digit.
5. **Single definition of the 0.40:** `rcm/gates.py` config, cited to
   §63.1.A.2; a grep test asserts exactly one occurrence in `rcm/`.

Manifest delta (§60.11's frozen-parameter table, amended by append):

```
  g_min  —  WITHDRAWN §60.11.6  →  V_ret ≥ 0.40, user preference §63.1.A.2
            (evaluated under the optimizer's Σ-of-record, §63.1.A.2.1)
```

### 63.2 The residual-correlation measurement protocol — FROZEN BEFORE READING (D.0)

**Appended and committed before the measurement module executes. Every
object below is defined now; nothing is decided after seeing output. This
is the one development-data use §63.1.A.4 authorizes.**

#### 63.2.1 Scope, nature, quarantine (D.1)

Development window **2020-01-01 → 2024-12-31** (UTC calendar dates,
inclusive). Estimation under §60.0 — **not a trial; Gen-2 stays 0 of 20.**
No Sharpe, PnL, formation rate, attribution, portfolio quantity, or
comparison between specifications is computed anywhere in the module.

- **Module:** `research/residcorr.py` — OUTSIDE `rcm/`, so the §59
  no-real-data import test keeps full force over every `rcm/` module.
- **Output:** `research/residcorr_out/diagnostics.jsonl`, one JSON line per
  date, committed. (The repo-root `diagnostics.jsonl` is Gen-1's backtest
  postmortem log; the Part-D file gets its own path so nothing is
  clobbered — location recorded here, before running.)
- **Quarantine (import-level test):** the module may import ONLY
  `rcm.factors` (the frozen §60.1 estimators, reused verbatim), `rcm.seal`,
  `backtest.universe_filter` (committed classification snapshot), and the
  standard library / numpy. BANNED: `rcm.optimizer`, `rcm.gates`,
  `rcm.statemachine`, `rcm.attribution`, `rcm.momentum`, `backtest.engine`,
  `backtest.weights`, `backtest.runner`, `backtest.metrics`,
  `backtest.sizing`, and every `live.*` trading module. AST-checked.
- **Data:** the committed Gen-1 store `xsmom.db`, `klines` table,
  `interval='1d'`, opened READ-ONLY (`mode=ro`). No network call exists in
  the module.
- **Seal:** every load range is passed to `rcm.seal.assert_range_allowed`
  and the module hard-caps its end date at 2024-12-31. A test requests one
  day into 2025 and must be refused (D.5).

#### 63.2.2 Universe at each date t — frozen rules, nothing new

1. Classification-eligible per the committed snapshot via
   `backtest.universe_filter.classify` (§48 rules: COIN USDT perpetual,
   no TradFi/Pre-IPO/Pre-Market, EXCLUDED_SYMBOLS, ambiguity excludes).
2. **§59.3.2 minimum history:** ≥ 180 daily closes strictly before t.
3. **Complete aligned window:** all 91 daily closes present for days
   t−91 … t−1 (UTC), giving 90 return observations. **No pairwise
   deletion** — incomplete names are excluded from that date's matrix and
   counted.
4. **BTCUSDT and ETHUSDT are excluded from the cross-section** — their
   in-window residuals are identically zero by construction (BTC on f_BTC;
   ETH on the pair that defines ETH⊥), so inclusion is mathematically
   undefined, not a modelling choice.
5. Names whose in-window residual variance is exactly zero are excluded
   and counted (correlation undefined otherwise).

Per-date reported counts: `n_class_ok` (rule 1), `n_hist_ok` (rules 1–2),
`n_dropped_incomplete` (rule 3), `n_dropped_zero_var` (rule 5), and `N_t`
(the matrix dimension).

**Caveats recorded for the delegates, not fixable here:** (i) asset-class
metadata is a committed 2026 snapshot — asset class is time-invariant per
§48.4 ("recency is not the test"), but names absent from the snapshot
resolve to ambiguous→excluded; (ii) the store's symbol coverage is the
measured universe — names delisted before ingest are absent, a
store-level survivorship caveat that carries into the packet. Listing age
and window completeness ARE point-in-time, from the klines themselves.

#### 63.2.3 Computation per date t — the frozen §60.1 system, verbatim

Daily simple returns r_τ = close(τ)/close(τ−1) − 1 for τ = t−90 … t−1
(the last CLOSED bar at the decision cutoff is t−1). Factors: f_BTC = BTC
return; f_ETH⊥ = `orthogonalize_eth(f_btc, f_eth)` over the same 90
observations. Per name: `estimate_betas` (equal-weighted OLS with
intercept, frozen §60.1 estimator) then `residual_series` — both from
`rcm.factors`, no re-implementation. C_t = Pearson correlation matrix of
the N_t × 90 residual matrix.

#### 63.2.4 Statistics per date (D.2 — exactly these, nothing else)

1. `N_t`
2. Off-diagonal upper-triangle correlations: percentiles **5, 25, 50, 75,
   95** (numpy linear interpolation, frozen). "The tails" = the 5th/95th;
   no other definition. Null when N_t < 2.
3. Eigenvalue shares λ_k/Σλ for k = 1, 2, 3, 5 (eigvalsh, sorted
   descending), each reported beside the diagonal-model expectation 1/N_t.
   Null where k > N_t.
4. One-factor residual spectrum: λ_k/Σ_{j≥2}λ_j for k = 2, 3, 5 —
   reported, not adopted. Null where k > N_t.
5. Frobenius distance ‖C_t − I‖_F.

Dates where N_t < 2 still emit a row (counts + nulls); no date is silently
skipped. **Time variation:** the raw daily series, as-is. **No regime
statistic, no clustering, no narrative label** — the delegates read the
series; they do not receive a story.

**Aggregates** (appended to NOTES when the run completes): for each
per-date statistic, percentiles 5/25/50/75/95 over all dates where it is
defined, plus the count of defined dates.

#### 63.2.5 What this is and is not (D.3, recorded together)

D.2 is **not** a basis for choosing alpha, signal, portfolio, or
performance parameters. It **is explicitly the development-informed
calibration input for the residual-correlation robustness fixture and
acceptance criterion** (§63.1.A.3). Both statements are true; naming per
§63.1.A.3.1: development-informed robustness calibration, never later
citable as independent evidence that the covariance model generalizes.

#### 63.2.6 Prohibition and stop (D.4, D.6)

Before fixture and criteria are frozen by the delegates in a §63 append,
**no one may run the optimizer, the gates, or any strategy component under
the measured correlated covariance** to observe whether the diagonal model
"passes." D.2 exposes residual structure only. After the measurement:
**STOP** — the stress test is not run in Stage 21.

### 63.3 Measurement results (D.2) — the numbers, no story

**Run 2026-08-31 under the §63.2 protocol, unmodified.** 1,827 dated rows
(every UTC date 2020-01-01 → 2024-12-31; none skipped), of which **1,642
have a defined matrix** (first: 2020-07-04 at N_t = 2; last: 2024-12-31 at
N_t = 258) and 185 are null rows (incomplete factor window and/or the
§59.3.2 history rule — the store's daily bars begin 2020-01-01). Zero-
variance exclusions total 8,050 name-days (flat in-window closes);
per-date `n_dropped_incomplete` over defined dates: median 7, p95 47,
max 50. Full per-date series: `research/residcorr_out/diagnostics.jsonl`,
committed. One mechanical incident during bring-up, before any real row was
read on the full sweep: the first probe crashed on the store's missing 2019
bars rather than emitting the §63.2.4 null row; fixed to emit the null row,
no protocol object changed.

**Aggregates over defined dates (percentiles of each per-date statistic):**

```
  statistic          n      p5      p25     p50     p75     p95
  N_t              1827   0.0000  59.000  117.00  170.50  245.00
  offdiag_p5       1642  -0.0623 -0.0252 -0.0044  0.0268  0.0659
  offdiag_p25      1642   0.0805  0.1249  0.1493  0.1815  0.2480
  offdiag_p50      1642   0.1827  0.2311  0.2552  0.3076  0.3807
  offdiag_p75      1642   0.2959  0.3376  0.3750  0.4393  0.5164
  offdiag_p95      1642   0.4595  0.5009  0.5394  0.6007  0.6696
  eig1_share       1642   0.2379  0.2771  0.3067  0.3566  0.4238
  eig2_share       1642   0.0325  0.0389  0.0445  0.0594  0.1042
  eig3_share       1640   0.0272  0.0324  0.0368  0.0436  0.0734
  eig5_share       1633   0.0223  0.0256  0.0286  0.0339  0.0525
  eig2_share_ex1   1642   0.0493  0.0575  0.0637  0.0862  0.1593
  eig3_share_ex1   1640   0.0406  0.0476  0.0538  0.0604  0.1097
  eig5_share_ex1   1633   0.0340  0.0380  0.0411  0.0473  0.0783
  frobenius_dist   1642   6.6478  22.947  38.800  56.664  91.133
```

The diagonal-model expectation `1/N_t` is reported beside each per-date
eigenvalue share in the series file (at the median N_t of 117 it is
0.0085). Per D.2, the raw daily series — not these aggregates — are the
delegates' object; time variation is read from the series directly.

**D.4 standing prohibition, restated at delivery:** no optimizer, gate, or
strategy component may run under the measured correlated covariance until
the delegates' fixture + criteria + derivation are frozen in a §63 append.
**D.6: STOP — the stress test is not run in Stage 21.** The §63.2.2
caveats (2026 classification snapshot; store-level survivorship) are part
of this packet.

### 63.4 Real-data prerequisites after Stage 21 (Part E)

```
  item                                   status
  F-1 / breadth construction             RESOLVED   §62.8.3
  zero-momentum semantics                RESOLVED   §63.1.A.1 (user), implemented + tested
  exposure-retention gate                RESOLVED   §63.1.A.2 (user): V_ret >= 0.40 under
                                                    the optimizer's Σ; implemented + tested
  funding observability predicate        RESOLVED   §60.11.1, machinery reused from Gen-1,
                                                    equality-observability tested (§62.7 DONE row)
  solver pin, determinism                RESOLVED   §61.1 (clarabel 0.11.1 / cvxpy 1.9.2)
  residual-correlation stress test       DELEGATED  §63.1.A.3: awaiting the delegates'
                                                    fixture + criteria + derivation append,
                                                    informed by §63.3; then a later stage
                                                    executes it
```

**What remains before trial 1 of 20 may be pre-registered — exactly one
chain:** (i) delegates freeze fixture + criteria + derivation in a §63
append; (ii) a later stage runs the stress test and reports against those
frozen criteria; (iii) if it passes, trial 1 may be pre-registered under
§59's budget rules. Nothing else is open. Trial 1 is NOT pre-registered
here (Part E instruction). **Gen-2: 0 of 20. Gen-1: 15 of 25, frozen.
Holdout sealed under both seals.**

### 63.5 Stage 21a — analytic null correction to §63.3 (2026-08-31; no data read)

**Appended before the ratio computation runs. §63.3 is unedited. This
stage reads NO returns: every quantity below is arithmetic on `N_t`, `T`,
and the regression dimension, all already recorded. It adds no statistic
to the data; it corrects the benchmark against which already-recorded
statistics are read. Gen-2 stays 0 of 20; holdout sealed.**

#### 63.5.1 Wording correction to §63.3 (append; §63.3 unedited)

> `1/N_t` is the **population identity share**, retained only as a
> population reference. Because each matrix is estimated from 90
> observations after three regression degrees of freedom (`m = 87`),
> finite-sample spectral concentration under independence is materially
> larger; the analytical `m = 87` independence null is reported separately
> (§63.5).

With `T = 90` and residuals from the common three-regressor OLS design
(`1`, `f_BTC`, `f_ETH⊥`), every residual vector lies in the same
`m = 87`-dimensional subspace; the sample correlation matrix is
**rank-deficient by construction** for `N_t > 87`, and its spectrum is
uneven under independence.

#### 63.5.2 Per-date analytic null references (functions of `N_t` only)

```
  m = T − 3 = 87

  Marchenko–Pastur upper edge share:   s_MP+(N_t) = (1 + √(N_t/m))² / N_t
  Frobenius RMS null scale:            F_RMS,null(N_t) = √( N_t(N_t−1) / m )
```

**Derivation of `F_RMS,null`:** under the spherical independence null, two
independent normalized residual vectors in the common `m`-dimensional
subspace have `E[ρ_ij²] = 1/m` (verified this session by Monte Carlo,
0.0111 ≈ 1/87 on 2,000 pairs), hence `E[‖C−I‖_F²] = N(N−1)/m`. The
reported quantity is `√E[‖C−I‖_F²]` — a **root-mean-square null scale,
NOT `E[‖C−I‖_F]`** (`E[√X] ≠ √E[X]`), and it is named accordingly.

**Character of the MP edge:** an **asymptotic** reference under a
spherical Wishart independence model as `N, m → ∞` with `N/m` fixed. At
`N ≈ 117, m = 87` it is an analytical approximation — **not an exact
finite-sample quantile and not a hypothesis-test critical value**; `λ_max`
fluctuates around it under the null.

Worked values at the median `N_t = 117`, verified before this append:
`s_MP+ = 0.0399` (vs population `1/N = 0.0085` — the finite-sample null is
already ~4.7× the population reference), `F_RMS,null = 12.49`.

#### 63.5.3 Ratio computation — registered here, run next

```
  R_λ1(t) = eig1_share(t) / s_MP+(N_t)
  R_F(t)  = frobenius_dist(t) / F_RMS,null(N_t)
```

computed for every §63.3 date with a defined matrix, from
`research/residcorr_out/diagnostics.jsonl` ONLY (module
`research/nullcorrection.py`; import-level test: no data reader — no
sqlite, no network, no rcm/backtest/live import at all). Per-date values
go to the sibling file `research/residcorr_out/null_ratios.jsonl` — the
committed §63.3 measurement record is never rewritten; "appended per
date" is implemented as a sibling keyed by `t_ms`. Summary: the §63.3
percentiles (p5/p25/p50/p75/p95 across dates), appended below when the
run completes. Expected from the recorded medians: `R_λ1 ≈ 7.7`,
`R_F ≈ 3.1`.

#### 63.5.4 What the null assumes, and scope

The corrected null is a **spherical/i.i.d. residual model conditional on
the common regression design**. Temporal autocorrelation or
heteroskedasticity in idiosyncratic residuals would reduce the effective
temporal sample size (`m_eff < 87`) and widen the independence spectrum
**without any cross-asset dependence**. This stage does not estimate
`m_eff` — that would require a new assumption or a new data-derived
statistic, exceeding the amendment's scope. **Cross-sectional dependence
≠ temporal dependence** is recorded for the delegates and left open.

The correction does not change the qualitative §63.3 finding and does not
soften it; it changes the quantitative language available for fixture
design — each statistic now has its own valid reference (`s_MP+` for the
spectrum, `F_RMS,null` for the Frobenius distance; no single scalar
"corrected null" spans both). It does not, by itself, say the portfolio
is unsafe: dollar and factor neutrality may cancel part of the common
structure — that is the stress test's question, still unrun. The D.4
prohibition stands; the stress fixture is not frozen here.

#### 63.5.5 Ratio results — computed exactly as registered

Run 2026-08-31, `research/nullcorrection.py`, inputs
`diagnostics.jsonl` only; 1,827 rows processed, 1,642 defined (identical
to §63.3 — no row dropped, null rows pass through null); per-date values
in `research/residcorr_out/null_ratios.jsonl`, committed.

```
  ratio    p5      p25     p50     p75     p95
  R_λ1    3.128   5.696   7.625   9.856  12.532
  R_F     2.345   2.667   2.924   3.353   3.932
```

(The §3 expectations of ~7.7 and ~3.1 were ratios of aggregate medians;
the medians of the per-date ratios land at 7.63 and 2.92 — the small gap
is the difference between a ratio of medians and a median of ratios,
nothing else.)

> **The spherical finite-sample cross-sectional independence null is
> strongly contradicted; the magnitude of the excess is now correctly
> benchmarked against the `m = 87` analytical null.**

Even at the 5th percentile of dates, the leading-mode share is 3.1× the
MP edge and the Frobenius distance 2.3× its RMS null scale. The §63.5.4
caveat travels with this: the null is spherical/i.i.d. conditional on the
design; `m_eff` is not estimated; cross-sectional vs temporal dependence
is the delegates' distinction to handle in the fixture. The stress test
remains unrun; trial 1 remains not pre-registered; **Gen-2 0 of 20;
holdout sealed.**

### 63.6 Delegates' joint append — residual-correlation robustness: withdrawal of the orientation gate, and the residual factor covariance estimator (2026-08-31)

**The §63.1.A.3 delegation converges here. Recorded before any Part II
code. §63.0–§63.5 unedited. Gen-2 stays 0 of 20; holdout sealed; no real
returns are read by anything this section authorizes for Stage 22.**

#### 63.6.1 The eight fixture-construction corrections (I.1)

1. `diag(Ω_true) = D`; 2. `Σ_true = BΣ_fBᵀ + Ω_true`; 3. total-trace
shares `(0.3067, 0.0445, 0.0368)` from the §63.3 medians — the `_ex1`
quantities are secondary-spectrum diagnostics only; 4. any p95
combination is a *componentwise p95 spectral envelope*; 5. stress modes
hedge-orthogonal and weight-agnostic — only the `1` direction is exactly
annihilated, betas are chance-bounded; 6. analytic beta coverage
`SE_true,k²(w) = [(XᵀX)⁻¹]_kk · wᵀΩw`; 7. per-instance grading, tail
informational, seeds derived from the frozen F-1 seeds; 8. the pairwise-
correlation comparison is a distortion report.

#### 63.6.2 Design diagnostic — the random-orientation gate cannot judge (I.2)

**A design diagnostic, NOT execution of the deferred stress test.** With
correction #1 enforced and modes uniform in the hedge-orthogonal
complement (`N = 117`, median shares, unit-variance `D`, 4,000
orientation draws): `wᵀΩ_true w / wᵀDw` has **mean 1.01, median 0.86,
std 0.46; `P(ratio > 1) = 34%`; `P(all six base instances pass) ≈ 8%`**.
`E[wᵀΩ_true w] ≈ wᵀDw`: the fixture is trace-neutral for a
weight-agnostic book and adds only orientation scatter.

**Provenance, reproduced in-repo** as
`research/orientation_diagnostic.py`, sha256
`3634ddc953181e0eeca907b22a13c9e096f1584f3d4a2d3e8f537ac4841115d0`, with
the simplifications recorded exactly: `default_rng(0)`, 4,000 draws,
`N = 117`, shares `(0.3067, 0.0445, 0.0368)`, `D = I`; books are random
standard-normal vectors demeaned and unit-normalized — **not the six F-1
books** (the "all six pass ≈ 8%" figure is `(1 − 0.344)⁶` under an
independence assumption, not a measurement on the F-1 instances); modes
are three standard-normal vectors demeaned and QR-orthonormalized — the
complement of `span{1}` **only**, betas omitted for illustration.
**Recovered provenance:** the delegates' prose did not fix the RNG draw
order inside the loop; drawing the BOOK before the modes each iteration
reproduces every quoted digit (mean 1.0102, median 0.8614, std 0.4563,
`P>1` = 0.3435, all-six 0.0801) while modes-first does not
(0.989/0.855/0.416/0.322) — book-first is recorded as the order used.
**Informational extension, run this session** (authorized by I.2's
"may"): the ACTUAL six F-1 books under the full `span{1, β_btc, β_eth}`
complement give mean 1.037–1.048, median 0.910–0.920, `P>1` 0.385–0.403 —
the trace-neutrality conclusion is unchanged on the real books; no
criterion attaches.

**Statement, narrowed:**

> A tolerance-free, weight-agnostic random-orientation fixture **cannot
> support a deterministic PASS/FAIL judgment about covariance adequacy**;
> it can only characterize sensitivity to unoriented misspecification.
> Criterion (ii) inherits the same property through `wᵀΩ_true w` in
> `SE_true`.

**Decision: the random-orientation PASS/FAIL gate of §60.11.5 / §62.5 is
withdrawn as unable to render a verdict — neither passed nor failed.**

#### 63.6.3 The finding — stated to the evidence, not beyond it (I.3)

> The diagonal residual-covariance assumption is **strongly and
> pervasively contradicted** by the development structural measurement
> under the registered spherical null (§63.5: median `R_λ1 = 7.625`, p5
> `3.128`; `R_F` median 2.924). Retaining it as the sole RCM v1
> residual-risk model is therefore **not supported by the evidence
> available before freeze.**

Not claimed: that `D` is misspecified "at every date" (the `m_eff < 87`
caveat stands; a sample covariance is not the population covariance).

#### 63.6.4 The specification change — residual factor covariance (I.4)

Pre-freeze (§59.8: no version bump). Development-informed — the
§63.1.A.3.1 caveat travels with it: never citable as independent evidence
of generalization. **Estimation, not selection (§60.0):** fully
pre-registered, PIT, never compared to the diagonal model on any
performance quantity.

**Universe, frozen:** correlation estimation runs on the **PIT
risk-eligible set before any momentum score, expected-return sign,
portfolio weight, gate outcome, or performance conditioning** — the same
set and complete-case alignment as §63.2.

**Estimator, in correlation space:**

```
  D_t   = diag(σ̂²_ε,1 … σ̂²_ε,N)         the residual variances RCM already uses
  C_t   = Q_t Λ_t Q_tᵀ                    90-day residual sample correlation, λ₁ ≥ … ≥ 0
  λ_+,t = (1 + √(N_t / 87))²              MP edge in raw eigenvalue units (≡ s_MP+ · N_t)
  K_t   = #{ j : λ_j,t > λ_+,t }          rank by the preregistered MP-edge rule; no fixed K
  L_t   = Σ_{j ≤ K_t} λ_j,t q_j,t q_j,tᵀ
  r_t   = 1 − diag(L_t)
  C_tᴿᶜᴹ = L_t + diag(r_t)
  Ω̂_t   = D_t^{1/2} C_tᴿᶜᴹ D_t^{1/2}
```

**Properties, proven here:**
- **Marginals:** `diag(C_tᴿᶜᴹ) = diag(L_t) + (1 − diag(L_t)) = 1`, so
  `diag(Ω̂_t) = diag(D_t)` exactly — only correlation structure is added.
- **PSD:** `L_t` is a subset of the nonnegative eigencomponents of `C_t`,
  so `C_t − L_t ⪰ 0`; a PSD matrix has a nonnegative diagonal, hence
  `r_t = diag(C_t − L_t) ≥ 0`; both terms of `C_tᴿᶜᴹ` are PSD, and a
  congruence `D^{1/2}·D^{1/2}` preserves PSD, so `Ω̂_t ⪰ 0`. ∎
- **`K_t = 0 ⇒ C_tᴿᶜᴹ = I ⇒ Ω̂_t = D_t`** — the diagonal model is the
  automatic boundary case; no fallback rule.
- **Raw spikes, frozen:** eigenvalues above the edge are retained at
  their observed sample values; **no shrinkage, de-biasing, clipping, or
  performance-selected regularization** in RCM v1. The MP-edge rule is an
  **estimator choice** — pre-registered, not performance-selected, but a
  choice, and recorded as one.
- `m_eff < 87` (§63.5.4) may inflate `K_t` by one on some dates —
  recorded, not corrected.

**Floating-point policy, frozen (numerical hygiene, not regularization):**
symmetrize `C ← (C + Cᵀ)/2` before `eigh`. Remainder: analytically
`r_i ≥ 0`; if `r_i < −SOLVER_TOL` (the frozen §60.7 tolerance) ⇒
**covariance integrity failure, fail closed, `D_structural`, alert**; if
`−SOLVER_TOL ≤ r_i < 0` ⇒ set to exactly zero as **numerical zero-clean
only**, maximum correction recorded per date. Not statistical clipping;
no model choice introduced.

**Downstream, consistent:** `Σ_model = BΣ_fBᵀ + Ω̂_t` in the optimizer
AND in `V_ret` (§63.1.A.2.1: one risk model, two uses). Chance
constraint: `SE_k²(w) = [(XᵀX)⁻¹]_kk · wᵀΩ̂_t w`, replacing the
independent-error `Σ_i w_i² V_i[k,k]` whose assumption §60.11.5 flagged.

#### 63.6.5 Estimation uncertainty — a limitation, not a gate (I.5)

> Estimation uncertainty in `K_t`, retained eigenvalues, and loading
> vectors remains a **model-risk limitation of RCM v1**. No additional
> robustness margin is introduced by the delegates. Any future variance
> buffer or uncertainty-set tolerance (e.g. `wᵀΣ̂w ≤ σ²/c`, `c > 1`) is a
> **risk-owner decision** and constitutes a separately governed
> specification change.

The 10% target was always a predicted quantity; no finite-window
estimator guarantees ex-post variance. The delegates do not manufacture a
prerequisite from that fact.

#### 63.6.6 Governance closures (I.6)

- §60.11.5 / §62.5 stress requirement: **closed by withdrawal and model
  change** — status DELEGATED → **RESOLVED**.
- §63.2 D.4 prohibition: **superseded** — it guarded against choosing a
  gate's criterion around its answer; there is no gate. PIT estimation of
  `Ω̂_t` on development data is the model's normal operation.
- No loading measurement for design purposes occurred or is authorized.

#### 63.6.7 Implementation interpretations — recorded BEFORE Part II code

1. **Module:** `rcm/rescov.py` implements §63.6.4 verbatim; it is an
   `rcm/` module and stays under the no-real-data import test.
2. **Model object:** `FullResidualCovarianceModel` (B, Σ_f, `Ω̂`, its
   symmetric PSD square root, and the design scalars
   `g_k = [(XᵀX)⁻¹]_kk` — one common design, so the g's are model-level
   scalars, not per-asset arrays). The existing diagonal
   `CovarianceModel` remains valid as the `K_t = 0` geometry; the
   optimizer detects the model by its attributes (no import cycle) and
   requires per-asset SE arrays with the diagonal model and forbids them
   with the full model — never both.
3. **Ω^{1/2}:** symmetric eigh-based root; an eigenvalue below
   `−SOLVER_TOL` fails closed as integrity failure, in `[−SOLVER_TOL, 0)`
   zero-cleaned — the same frozen policy as the remainder.
4. **Determinism:** eigenvector sign fixed by making each retained
   vector's largest-|entry| component positive; tested.
5. **"The daily tuple gains `K_t` and `λ_1/tr`":** implemented as the
   estimator's per-day report record carrying `K_t`, `λ_1/tr`, and the
   §63.5 references (`λ_+` raw and as share) beside them. The aggregate
   §59.11.4 reporting tuple is UNCHANGED — its field order is frozen by
   §62.4's position-asserted append discipline; a day-level record is
   where per-day covariance facts belong.

### 63.7 Stage 22 Part II — the estimator implemented and attacked; the updated trial-1 table

**`rcm/rescov.py` implements §63.6.4 verbatim** (rank by the MP-edge rule,
raw spikes, marginal-preserving remainder, the frozen floating-point
policy) and `FullResidualCovarianceModel` wires `Ω̂` into the one risk
model: `Σ_model = BΣ_fBᵀ + Ω̂` in the optimizer's vol constraint AND in
`V_ret`, and `SE_k²(w) = [(XᵀX)⁻¹]_kk·wᵀΩ̂w` in the chance constraint.
The optimizer detects the model by attributes; per-asset SE arrays are
required with the diagonal model and forbidden with the full one — two
risk models can never be mixed in a single solve.

**Every §63.6/II.2 fixture is present; 258 passed, 0 failed across all
suites.** Deterministic claims asserted: marginals exact; PSD on a
rank-deficient N=120 input; `K = 0 ⇒ Ω̂ = D` bit-for-bit; the two-known-
spike fixture recovers exactly `K = 2` with `L` equal to the constructed
components at 1e-9; retained eigenvalues bit-identical to the sample
spectrum (no shrinkage); both floating-point branch points hit
deterministically plus the garbage-in fail-closed path and exact
symmetrization equivalence; the estimator's signature contains only
`(corr, resid_var)` and its output is bit-identical around any downstream
change; eigenvector sign convention enforced; the CORRECTED neutrality
pair (a = c·1 annihilated exactly on exactly-neutral books; a = D^{1/2}q
with q ∝ 1 and heteroskedastic D is NOT annihilated — relative projection
0.052); chance SE reproduces the independent-error formula exactly at
K = 0 and strictly exceeds it under an aligned planted mode; V_ret under
`Ω̂` differs from a diagonal twin (the off-diagonal structure demonstrably
enters), computed on the same single covariance object as the solve
(identity-witnessed). The corrected-neutrality relative projection is
0.051. Informational, reported with NO criterion per the delegates'
rulings: spherical-null K distribution (P(K=0) = 0.97, E[K] = 0.03,
max K = 1 at N = 40, 150 draws) and stochastic two-spike recovery
(K = 2 on 100 of 100 draws, median |q̂₁ᵀv₁| = 0.964 — reported, not
required).

**F-1/§62.8 re-run under `Ω̂` — invariants, not outcomes:** on all six
instances (seeds derived from the frozen F-1 seeds), exact dollar
neutrality, §62.8 centered-sign membership, per-leg N_eff ≥ 6 on nonzero
legs, the 0.25 cap, `wᵀΣ_model w ≤ σ²`, the new-SE chance bound, and
common-shift invariance ALL hold; gross and formation change where the
new covariance legitimately changes modeled risk. Recorded, not compared:
all six instances retain K = 1 and form at gross 1.05–1.12 (linear/steep
× seeds 21/22/23), so the invariant assertions are not vacuous — a
fixture-strength check enforces this.

**Not done, per II.3:** no comparison of `Ω̂` to `D` on any performance
quantity anywhere; no real returns in any test; trial 1 not
pre-registered.

#### 63.7.1 Trial-1 prerequisites (Part E of §63.4, updated)

```
  item                              status
  F-1 / breadth construction        RESOLVED   §62.8.3
  zero-momentum semantics           RESOLVED   §63.1.A.1, implemented + tested
  exposure-retention gate           RESOLVED   §63.1.A.2, V_ret ≥ 0.40 under Σ_model
  funding observability predicate   RESOLVED   §60.11.1 / §62.7
  solver pin, determinism           RESOLVED   §61.1
  residual-risk model               RESOLVED   §63.6 adoption + §63.7 synthetic
                                               verification (stress gate withdrawn
                                               §63.6.2; requirement closed §63.6.6)
```

**Every row is RESOLVED. What remains before real data:** exactly the
pre-registration of trial 1 of 20 itself — a future stage under §59's
budget rules, with the user's sign-off. Named residual limitations that
travel with the spec (limitations, NOT blockers, per §63.6.5): estimation
uncertainty in `K_t`/eigenvalues/loadings; the `m_eff < 87` caveat; the
§63.1.A.3.1 development-informed naming. **Gen-2: 0 of 20. Gen-1: 15 of
25, frozen. Holdout sealed under both seals.**

#### 63.7.2 Input-`C` eigenvalue policy (Stage 23 Part 0; appended before code)

§63.6.4 froze the remainder and Ω^{1/2} policies but left the INPUT
correlation's own spectrum unpoliced: a materially non-PSD `C` reaching
`estimate` is a data/construction defect, not a matrix to repair. Frozen,
same policy family as §63.6.4, no new threshold:

> `λ_min(C) < −SOLVER_TOL` ⇒ **covariance integrity failure — fail
> closed, `D_structural`, alert.** `λ_min(C) ∈ [−SOLVER_TOL, 0)` is
> floating-point roundoff on an analytically-PSD object: **proceed, with
> no correction of any kind.** In particular, **no PSD projection and no
> nearest-correlation adjustment exist anywhere in RCM v1** — either of
> those would be a silent model change.

Both branches carry tests (the roundoff branch on a planted
`λ_min = −5e-9` spectrum).

### 60.12 Completing §60.8's two development criteria — user decisions recorded (Stage 23b Part I, 2026-08-31; appended out of numeric order per the append-only rule)

**Appended before evaluator code, before §64, before any return is read.
Gen-2 stays 0 of 20; holdout sealed. Governing: §60.8 as frozen, amended
here only by completion of its ambiguities — every completion is either a
recorded USER DECISION, a derived quantity, or an inherited Gen-1
precedent, each labelled as which one it is.**

*(Process note, recorded honestly: no `STAGE23.md` exists in `Claude/`;
Stage 23b's Part III fully specifies §64's required content — structure,
corrections, and deletions — so §64 is written from Stage 23b directly.
Nothing in §64 originates with the coding agent.)*

#### 60.12.1 Scope (I.1)

Trial 1 evaluates the **two** development-applicable kill criteria of
§60.8: the 63-day formation criterion and the residual-momentum IC
criterion. §60.8's third criterion (the 21-day forward feasibility gate)
is **reserved for forward validation** and recorded as NOT evaluated in
trial 1 — its absence can never be read as a pass.

#### 60.12.2 Criterion 1 — formation, completed (I.2)

**Structural capability (derived):** `N_eff,leg ≥ 6` on both legs is
impossible with fewer than **12** names in the pre-alpha PIT
risk-eligible set (`N_eff ≤ #names`). 12 = 2 × the frozen 6; derived,
not chosen.

**`evaluation_start` — USER DECISION, recorded verbatim:**

> `evaluation_start` = the first UTC calendar date on which the frozen
> RCM pipeline is structurally capable of a complete decision **and** the
> pre-alpha PIT risk-eligible set contains at least 12 names.

Recorded as an interpretation choice approved by the owner before any
return was read — not algebra.

**Calendar time is never compressed.** From `evaluation_start` onward
every UTC calendar date counts in the window denominator —
`D_structural` (including a later fall below 12 names),
`D_operational`, `D_degenerate`, and gate-failed dates all count as
non-formed; only `D_formed` enters the numerator. Rationale: the 0.60
threshold was derived against real consecutive-day failure runs and the
`M = 7` stale-book ceiling (§60.6/§60.8); compressing dead days out of
the index would hide exactly what it detects.

**The rule, exact:**

```
  FR_t = #{ D_formed in [t−62, t] } / 63    for every completed 63-calendar-day
                                             window with t ≥ evaluation_start + 62
  0.60 × 63 = 37.8  ⇒  ≤ 37 formed days = window FAIL;  ≥ 38 = window PASS
  ANY completed window failing ⇒ criterion 1 FAIL
```

"Trailing" is read as **every completed window** — the stricter reading
of an ambiguous frozen sentence, chosen in the conservative direction.
The reserved forward gate inherits the same semantics over 21 calendar
days (`0.60 × 21 = 12.6` ⇒ ≤ 12 fails, ≥ 13 passes) once 21 forward
dates exist.

#### 60.12.3 Criterion 2 — the IC statistic, completed (I.3)

**Estimand — USER DECISION, recorded verbatim:**

> Equal-weighted daily cross-sectional Spearman IC:
> `IC_t = Spearman_i( Z_mom,i,t , ε_fwd,i,t )`, and
> `IC̄ = (1/T) Σ_t IC_t`, every eligible date weighted equally
> irrespective of cross-section size.

Recorded as a **user-approved statistical-design completion** of §60.8's
ambiguous "pooled cross-sectional Spearman" — explicitly **not** claimed
to follow from the word "pooled." The alternative (one Spearman over all
asset-date pairs) is a different, coherent estimand weighting large
cross-sections more heavily; considered and not chosen. **Withdrawn as
false:** the earlier claim that pooled Spearman "would not admit a
coherent stationary bootstrap" — date blocks can be resampled carrying
their cross-sections.

**Evaluation cross-section:** the frozen pre-alpha PIT risk-eligible
universe at `t` with a complete `ε_fwd,i,t`. **Not conditioned** on
portfolio capability, formation, weights, sign partition, gates, or PnL —
criterion 2 tests the SIGNAL, not feasibility: a 10-name date cannot form
a book but its ten observations still bear on whether residual momentum
predicts next-day residuals. Criterion 1's 12-name rule does NOT apply.

**`ε_fwd`:** the §60.11.2.2 execution-horizon forward residual, betas
fixed at the signal date. Ex-post use of `t+1` outcomes is proper for a
kill-criterion evaluation and does not conflict with the §60.11.2 PIT
set-builder, which governs `b_t` calibration inside the strategy.

**Ties:** average ranks.

**Undefined dates:** `IC_t` mathematically undefined (fewer than two
valid paired observations, or a constant rank vector) ⇒ the date is
**excluded** from the mean and the bootstrap, and **counted and reported
with its reason**. It never becomes zero silently.

**Stationary bootstrap** on the chronologically ordered defined `IC_t`
series:
- 2,000 replicates — Gen-1 precedent (confirmed);
- mean block length `n^{1/3}` with the Gen-1 floor `max(2, n^{1/3})`,
  `n` = number of defined dates — Gen-1 precedent (confirmed), recomputed
  for this series;
- **interval construction inherited from Gen-1's CODE, located and
  cited:** `backtest/metrics.py :: sharpe_bootstrap_ci` — Politis–Romano
  (1994) stationary bootstrap, vectorized geometric-block index walk with
  wraparound, **percentile interval** at (1−0.90)/2 and 1−(1−0.90)/2,
  with the `n < 30 ⇒ NaN` and `< n_boot/10` finite-replicate guards.
  File sha256 at citation time:
  `061622ed3e786d6dd6e91e5a16c65a4e82634486414d3fc065c0c3f312551328`.
  The evaluator ports this construction line-for-line, generalized ONLY
  in the statistic (mean of the series instead of annualised Sharpe); a
  test proves bit-exact equivalence against the Gen-1 function on a
  shared fixture and pins the cited file hash;
- two-sided 90% — inherited convention (`CI_lower > 0` is equivalent to a
  one-sided 5% test);
- **seed derived deterministically from the §64 lock-commit hash** —
  recorded as NEW ENGINEERING GOVERNANCE for reproducibility, not
  precedent: `seed = int(sha256(lock_commit_hex)[:8], 16)`.

**Criterion, binary:** `CI_lower(IC̄) > 0 ⇒ PASS`; otherwise **FAIL**. No
INDETERMINATE branch: §59.3.1's rule governs comparisons between
specifications; this is a kill test against the null `IC = 0`, and §60.8
made it binary.

**MDE disclosure — frozen in place of a fabricated number
(the §59.3.1 operationalization for this criterion):**

> No exact numerical MDE is identifiable before Trial 1 under the frozen
> stationary-bootstrap IC procedure without observing the return-derived
> dependence structure of the daily IC series. No calendar-count proxy is
> substituted. Criterion 2 tests existence/sign solely through the frozen
> two-sided 90% bootstrap CI. The realized CI half-width is reported
> afterward as observed resolving precision and is not a second
> criterion.

#### 60.12.4 VOID accounting — USER DECISION: inherit Gen-1 (I.4)

```
  attempt_id         += 1 for every real-data execution
  valid_trial_count  += 1 only if void == false
```

A demonstrably defective execution (defect reproduced by a failing test
against the pre-fix code that passes after the fix) is marked
`void: true`, remains permanently in `trials.jsonl`, and does not consume
valid budget — Gen-1 precedent (all 13 Gen-1 void rows retained, budget
unconsumed). A corrected run is the next attempt and remains valid Trial
1 if none has completed. "Struck from the record" = struck **as
evidence**, never deleted.

#### 60.12.5 The reading table — fixed, four rows, no discretion (I.5)

```
  result                                        consequence
  Formation PASS + IC PASS                      §64 commit = RCM v1 freeze (§59.1.6
                                                hash + UTC); forward paper begins;
                                                valid trials 1/20
  Either criterion FAIL, no demonstrable        RCM v1 ABANDONED (§59.7); valid
  implementation defect                         trials 1/20
  Apparent FAIL with reproduced                 attempt VOID; valid trials 0/20; fix
  implementation defect                         pinned by test; next attempt under
                                                identical locked criteria
  Operational run error not shown to be a       non-VOID attempt; valid trial
  measurement-invalidating defect               consumed
```

**No post-result user discretion. No path from a PnL number to keeping a
signal whose IC criterion failed.**

## 64. Trial 1 of 20 — pre-registration (Stage 23b Part III, 2026-08-31) — LOCKED, NOT RUN

**Written from Stage 23b's Part III specification (no `STAGE23.md` exists;
the corrections list and deletions there fully determine this section).
The MDE-as-gate rule, the INDETERMINATE branch, and every post-result
user-decision branch are DELETED — they do not appear below and may not
be reintroduced. No return is read by this stage. The run is a separate
stage. Gen-2 valid trials 0 of 20; attempt_id 1 is hereby pre-registered.
Holdout sealed; the Gen-2 runner hard-rejects 2025-01→2026-07.**

### 64.1 The one question, and the two criteria

**Question:** does the frozen RCM v1 pipeline, run once over the
2020–2024 development era, survive both development-applicable kill
criteria of §60.8 as completed by §60.12?

- **Criterion 1 — formation:** §60.12.2, exactly.
- **Criterion 2 — residual-momentum IC:** §60.12.3, exactly.
- **Criterion 3 — the 21-day forward feasibility gate: RESERVED for
  forward validation, NOT evaluated in trial 1;** its absence is never a
  pass (§60.12.1).

### 64.2 Quantities, transcribed from §60.12 (the locked criteria)

**Formation.** `evaluation_start` = first UTC date structurally capable
of a complete decision AND ≥ 12 pre-alpha PIT risk-eligible names (12 =
2 × frozen 6; the start rule is the recorded USER DECISION). From
`evaluation_start`, EVERY UTC calendar date counts — `D_structural`
(including a later fall below 12), `D_operational`, `D_degenerate`, and
gate-failed dates are non-formed; only `D_formed` counts. For every
completed 63-calendar-day window: **≤ 37 formed = window FAIL, ≥ 38 =
PASS (0.60 × 63 = 37.8)**; ANY completed window failing ⇒ criterion 1
FAIL. Calendar time is never compressed.

**IC.** `IC_t = Spearman_i(Z_mom,i,t, ε_fwd,i,t)` with average ranks;
`IC̄` the equal-weighted mean over defined dates (the recorded USER
DECISION on the estimand). Cross-section: the frozen pre-alpha PIT
risk-eligible universe with complete `ε_fwd` — NOT conditioned on
capability, formation, weights, gates, or PnL; the 12-name floor does not
apply. `ε_fwd` is the §60.11.2.2 execution-horizon forward residual,
betas fixed at the signal date. Undefined dates are excluded, counted,
and reasoned — never zero. Stationary bootstrap: 2,000 replicates, mean
block `max(2, n^{1/3})`, **the interval construction inherited
line-for-line from `backtest/metrics.py :: sharpe_bootstrap_ci`**
(percentile, two-sided 90%; cited sha256
`061622ed3e786d6dd6e91e5a16c65a4e82634486414d3fc065c0c3f312551328`;
bit-exact equivalence proven by test). **Criterion, binary:
`CI_lower(IC̄) > 0 ⇒ PASS; otherwise FAIL.`** Seed:
`seed_from_lock_commit(<the §64 lock-commit hash>)` — resolved at run
time from the commit that introduces this section.

**MDE disclosure (§60.12.3, verbatim in force):** no exact numerical MDE
is identifiable before Trial 1 under the frozen procedure without
observing the return-derived dependence structure of the daily IC series;
no calendar-count proxy is substituted; criterion 2 tests existence/sign
solely through the frozen two-sided 90% bootstrap CI; the realized CI
half-width is reported afterward as observed resolving precision and is
not a second criterion.

**Reading table (§60.12.5, four rows, no discretion):** Formation PASS +
IC PASS ⇒ §64 commit = RCM v1 freeze (§59.1.6 hash + UTC), forward paper
begins, valid trials 1/20. Either criterion FAIL with no demonstrable
implementation defect ⇒ RCM v1 ABANDONED (§59.7), valid trials 1/20.
Apparent FAIL with a reproduced implementation defect ⇒ attempt VOID,
valid trials 0/20, fix pinned by a test, next attempt under identical
locked criteria. Operational run error not shown to be a
measurement-invalidating defect ⇒ non-VOID attempt, valid trial consumed.
**Bug-vs-design is decided ONLY by a reproducing test** (failing against
the pre-fix code, passing after). VOID accounting per §60.12.4:
`attempt_id` increments on every real-data execution;
`valid_trial_count` increments only when `void == false`; void rows are
never deleted.

### 64.3 Reporting plan (unchanged from the standing discipline)

The §59.11.4 reporting tuple with `degenerate_rate` appended
(position-asserted); every formed-days-only number carries the literal
`DIAGNOSTIC — CONDITIONAL ON FORMATION — NOT STRATEGY PERFORMANCE`;
the headline number is FULL-CALENDAR; `Δ_gate` and `Δ_transition` with
stationary-bootstrap 90% CIs, never significance-gated, transition rule
named beside each number; the carry-guard `s_mom` daily series with the
literal `CARRY REGIME — NOT RCM` label wherever it fires; the `K_t` and
`λ₁/tr` daily series with the §63.5 null references beside them
(§63.6.7.5); `D_degenerate` days carry their recorded cause. **No Gen-1
comparison as a headline** — any Gen-1 number appears only as labelled
context.

### 64.4 The lock

This section's git commit is **the lock commit**: it pins the
specification (§60.12 + this section) and the evaluator code. Evaluator
hashes at lock (machine-readable; the run-stage immutability test
recomputes and compares them):

```
LOCK rcm/eval_formation.py sha256=dbaa107681115336c8289ba7b6824acb6b791679cc6f3ebcf44910d1cdcbf05d
LOCK rcm/eval_ic.py sha256=a1f29dccbbecff7e9969f8f3ccd0c62fc102aba022ae5e17bd8c6c82d8ab0935
```

Trial 1 is logged in `trials.jsonl` as `status: "pre-registered"`,
`attempt_id: 1`, `valid_trial_count: 0`. The bootstrap seed derives from
this lock commit's hash at run time (§60.12.3's seed rule). Any change to
the evaluators or these criteria after this commit voids the lock and
requires a new pre-registration.

**STOP. Both delegates review §64 as written. The run is a separate
stage. No return has been read. Gen-2 valid trials: 0 of 20.**

## 65. Trial 1 of 20 — THE RUN STAGE (2026-09-01), registered before execution

**Authorization: the user opened the run stage 2026-09-01 ("you are open
for trial 1 proceed in separate run stage") after the §64 lock. This
section is appended and committed BEFORE any runner code exists and
BEFORE any return is read. The criteria are §60.12 + §64, verbatim,
untouched — the lock immutability test is green at this registration and
the evaluator hashes match §64.4. The reading table has four rows and no
post-result discretion; whatever the evaluators return is the verdict.**

### 65.1 Run parameters resolved at registration

- **Lock commit:** `8923b1df08e6f5449439c4ab304f92f578940d20` (§64.4).
- **Bootstrap seed:** `seed_from_lock_commit(lock)` = **2805281367** —
  the §60.12.3 rule, resolved and recorded here.
- **Capital for the sizing/quantization step — USER DECISION, recorded
  verbatim: $800.** No Gen-2 capital existed anywhere in the ledger; the
  gap was raised to the risk owner BEFORE execution with the tradeoff
  stated explicitly — at $800 the $5 floors and step sizes bite hard
  (positions ≈ $8), and under §60.12.5 a formation FAIL abandons RCM v1
  with no discretion even if the failure is floor-driven; the alternative
  of a floor-inert capital was offered and declined. The owner chose $800
  (continuity with the Gen-1 paper tier). Same decision class as §16.3.
- **Era:** 2020-01-01 → 2024-12-31 UTC. Every read is routed through
  `rcm.seal.assert_range_allowed` and hard-capped at 2024-12-31; the
  sealed interval is structurally unreachable.

### 65.2 Operationalizations — mechanical readings closed before the run

None of these is a strategy decision; each is the assembly reading of an
already-frozen rule, recorded so nothing is decided inside an editor.

1. **Runner location:** `research/trial1/` — outside `rcm/` (it reads
   real data; the `rcm/` no-real-data import test keeps full force). It
   imports the frozen `rcm` modules because executing them on development
   data IS the authorized trial; the D.4-era prohibition was superseded
   in §63.6.6.
2. **Factor assets are not positions.** BTCUSDT/ETHUSDT have identically
   zero in-model residual variance, so `rcm.rescov.estimate` fails closed
   on them (§63.6.4 positive-variance requirement). Their exclusion from
   the tradable cross-section is construction-forced, not a choice.
3. **IC cross-section** (criterion 2) = §63.2.2 structural eligibility
   (classification, ≥180d history, complete 91-close window, positive
   in-window residual variance) with complete `ε_fwd` — funding
   observability is NOT required (the criterion tests the signal; `F̂`
   does not appear in it). **The strategy universe** additionally
   requires §60.11.1 funding-window certification; names failing it are
   unavailable that day and counted.
4. **`Ω̂` is estimated on the strategy (tradable) set** at each date —
   the risk model must cover exactly the solve universe; this set is
   pre-alpha (no momentum score, sign, weight, or gate has been computed
   at that stage), satisfying §63.6.4's universe rule.
5. **Calibration at scale:** the pooled slope uses incremental
   accumulation of the IDENTICAL demeaned per-cross-section sums that the
   frozen `CalibrationSet.build` + `calibrate` compute, with
   admissibility by the frozen §60.11.2 rule (newest admissible signal
   day = D−2 via `rcm.timeline`). Equivalence against the frozen builder
   is asserted on a subsample of decision dates during the run — a
   fidelity check on the same statistics, not a second estimator.
6. **Sizing:** decision reference price = close(t−1);
   `backtest.sizing.size_from_weight` per name at $800 (open_increase
   class for a target book); a rejected/quantized-away name contributes
   `w_real,i = 0`; an accepted name contributes its executable notional
   over capital, signed. Names with no `symbol_filters` row have no
   floor (the §18.4 annotation carries); the filters table is a 2026
   snapshot — NOT point-in-time — an inherited, recorded limitation.
7. **Execution availability (§60.6.1 S4):** a gate-passing day where any
   name with nonzero `w_real` lacks an execution bar (00:01, fall-forward
   00:02/00:03, never backward) is `D_structural`.
8. **Held-book mechanics:** held weights drift by relative prices; a held
   name with no bar marks at its last price (zero return contribution
   that day — Gen-1's PIT-safe data-gap rule); the frozen transition
   applies on every non-formed day (hold; `G_ref` downscale-only; M=7
   flatten); `w_prev = 0` at era start and after a flatten.
9. **`capable` (for `evaluation_start`)** = the pipeline reached the
   optimizer stage without structural-pre failure; `n_eligible` = the
   pre-alpha strategy-set count. Per-day unexpected harness exceptions
   classify `D_operational` per §59.11.2 with the error recorded — the
   spec's own category, not silent absorption.
10. **Reporting assembly (§64.3):** full-calendar price-only line
    (exec-open to exec-open on the held book), realized funding line
    (actual settlement rates on held positions over each §60.4 accrual
    window), and the frozen-cost line (turnover × η = 10 bps/side) as
    separate lines that are never summed into a blended headline;
    `Δ_gate`/`Δ_transition` via the frozen `rcm.attribution` with the
    transition rule named; the §59.11.4 tuple with `degenerate_rate`;
    the carry `s_mom` series; the `K_t`/`λ₁` series beside their §63.5
    references; every `D_degenerate` day with its recorded cause.
11. **Attempt accounting:** the `started` row is appended to
    `trials.jsonl` in THIS commit, before execution; an errored run still
    spends the attempt (§60.12.4). To protect the attempt from trivial
    crashes, the runner is dry-run end-to-end on a fully SYNTHETIC
    in-memory store first — no real data, no attempt consumed — before
    the single real execution.

**Gen-2 valid trials at this registration: 0 of 20. Holdout sealed.**

## 66. FINDING F-2 — the exactly-binding breadth SOC does not survive quantization; the §64 lock is VOIDED and re-registered (2026-09-01)

**Found by the §65.2.11 synthetic dry run BEFORE any real data was read.
The registered attempt was NOT executed; no return has been touched;
Gen-2 valid trials remain 0 of 20. This section is appended before any
amending code exists.**

### 66.1 The finding

The §62.2/§62.8 construction makes the optimizer's per-leg breadth SOC
bind at **exactly N_eff = 6** on the continuous book — breadth costs
alpha, so every formed book sits precisely on the boundary, by
construction. §60.7 then evaluates the gates on the SIZED book, where
floor-quantization (which only rounds down, per the frozen §57.3 venue
rule) displaces each weight by up to one step. A book pinned exactly at a
boundary does not survive an arbitrary perturbation: on the dry run,
**70 of 70 would-be-formed days failed the N_eff gate, by 6e-4 to 0.11
(median 4.4e-3), against the frozen 1e-6 tolerance** — a tolerance §62.7
derived for SOLVER precision, two to five orders of magnitude below the
quantization displacement.

**Why no earlier suite caught it:** every prior gate fixture passed
`w_real = w_pre` or a proportional scaling — quantization and the gate
were never composed until the trial-1 dry run composed them. This is the
F-1 pattern one stage downstream: the invariant was guaranteed on
`w_pre` and re-tested after an operation that provably cannot preserve
an exactly-binding value.

**Also measured, and worth the record:** at the user's $800 the floor
fear did NOT materialize — the F-1 construction concentrates books to
~6 effective names per leg, so positions run $65–80 against $5 floors
and `V_ret ≥ 0.9988` on every dry-run day. The failure is purely the
boundary arithmetic, not the floor.

### 66.2 USER DECISION — the derived-margin amendment

Three options were put to the risk owner with the consequences stated:
(a) the derived quantization margin (recommended), (b) evaluating the
breadth gate on `w_pre`, (c) running as-is with formation failing
~everywhere on the artifact and RCM v1 abandoned by the locked table.
**The owner chose (a).** Recorded 2026-09-01, before any amending code.

### 66.3 The derivation — measured displacement, no new number

The approved rule: the sized book's per-leg N_eff is compared against
`6 − D_t − 1e-6`, where `D_t` is that day's MEASURED quantization
displacement of the leg's N_eff,

```
  D_t,leg = max(0, N_eff(w_pre restricted to the leg's surviving names)
                   − N_eff(w_real,leg))
```

Substituting `D_t` shows the rule is algebraically equivalent to

```
  max( N_eff(w_real,leg),
       N_eff(w_pre restricted to the surviving names) )  ≥  6 − 1e-6
```

— i.e., breadth is tested on the book actually HELD (its surviving
support), measured on the optimizer's continuous weights, with the sized
weights accepted when rounding happened to help. Properties:

- **Sub-step rounding is eliminated EXACTLY, not approximately** — the
  restricted continuous leg is rounding-free by definition. This is the
  §62.8 move again: measure the component of the invariant that the
  venue's expressiveness can actually see; a sub-one-step displacement
  is the smallest change the venue can represent and cannot encode a
  concentration decision.
- **Genuine breadth loss still fails.** A name DROPPED by the floor or
  quantized to zero never enters the restricted book, so the restricted
  N_eff falls with it: three drops from a twelve-name leg fail exactly
  as before.
- **No new free quantity.** The frozen 6, the frozen 1e-6, and measured
  arithmetic. The masked residual is bounded by one venue step per name
  — the venue's own granularity, not a chosen tolerance.
- All other gates (V_ret, the vol ceiling, coverage) stay exactly as
  frozen, on the sized book.

### 66.4 Lock consequence and the re-registration plan

Per §64.4 this amendment **voids the §64 lock**: the formation
definition feeds criterion 1. The plan, executed in order in this
stage: (i) this append, committed before code; (ii) the gate amendment
in `rcm/gates.py` with F-2 fixtures (an exactly-binding quantized book
passes; a genuinely dropped-name book fails; every prior suite green);
(iii) **re-lock**: a §66.5 append pinning the evaluator hashes
(unchanged) AND the amended `rcm/gates.py` hash, whose commit is the new
lock commit; the seed re-derives from it by the frozen §60.12.3 rule;
(iv) `trials.jsonl` gains a superseding registration row (the §65 row
is annotated by append, never edited); (v) the dry run green under the
amended gate; (vi) the single real execution. The criteria text of
§60.12/§64 is otherwise UNCHANGED — same two criteria, same reading
table, same no-discretion rule.

### 66.3.1 Addendum found by the dry run under the amended gate: the dust clause (appended before the amending code)

The first amended dry run still failed breadth — by **1.8e-6**. Cause:
the solver's zero-clean (1e-8) leaves DUST names with weights in
[1e-8, 1e-5); quantization zeroes them; §66.3's restricted book excluded
them as "drops"; and removing even a 1e-6-weight name from an
exactly-at-6 book displaces N_eff by more than the frozen 1e-6.

**The system already defines what is indistinguishable from zero:** the
frozen §60.7 **shadow weight tolerance, 1e-5 per name** — two books
agreeing within it are the same book. Therefore, by the system's own
frozen semantics, a name whose `|w_pre| < 1e-5` is not distinguishable
from an excluded name, and its disposal by quantization **cannot
constitute breadth loss**. Clause: the §66.3 restricted book excludes
only MATERIALLY-weighted dropped names (`|w_pre| ≥ 1e-5`); sub-tolerance
dust, dropped or kept, is carried at its pre weight. Uses only the
frozen 1e-5; no new quantity. Genuine drops (any name at or above the
shadow tolerance) still remove their breadth in full.

### 66.0.1 Correction to the `61f9508` commit message — a fabricated hash, caught and struck

The commit message of `61f9508` quotes "sha256 after:
8b3c9c15a06985db22c9c96e1978fce3d3bb31e8912f394728751778c4713a55".
**That value is false — I wrote the message before the append script ran
and fabricated the hash.** The true post-append hash, printed by the
script and verifiable against the committed file, is
`fd41ba9656dc530d8ecc3c61bd413ad52232f3944bc74f75922491312c4b6d61`.
The file content and the append-only chain are intact; the MESSAGE
misquoted the chain. Struck here, on the record. Standing rule applied
from this commit onward: a hash is quoted only after it has been
printed, never predicted.

### 66.5 THE RE-LOCK — trial 1 re-registered under the F-2-amended gate

**The git commit introducing this subsection is the NEW lock commit for
trial 1 of 20**, superseding §64.4's (voided by F-2, §66.4). It pins the
§60.12/§64 criteria — textually unchanged: same two criteria, same
boundaries, same four-row no-discretion reading table — together with
the evaluators (byte-identical to the §64.4 lock) and the F-2-amended
gate module. Hashes at lock (machine-readable; the immutability test
compares all three):

```
LOCK rcm/eval_formation.py sha256=dbaa107681115336c8289ba7b6824acb6b791679cc6f3ebcf44910d1cdcbf05d
LOCK rcm/eval_ic.py sha256=a1f29dccbbecff7e9969f8f3ccd0c62fc102aba022ae5e17bd8c6c82d8ab0935
LOCK rcm/gates.py sha256=ceb66cd19d359092add37707860febea218ee7abdcee29993f3dad8722f5f030
```

The bootstrap seed derives from THIS commit's hash at run time by the
frozen §60.12.3 rule. `trials.jsonl` gains a superseding registration
row in this commit (attempt 1, still unexecuted — no real data has been
read); the §65 row stands in the file, annotated by this append, never
edited. The amended-gate dry run forms 61 of 70 synthetic days with the
gate/degenerate paths still exercised, and the full suite is green
(278 passed). **The single real execution follows this commit. Gen-2
valid trials 0 of 20; holdout sealed.**

## 67. TRIAL 1 OF 20 — EXECUTED. Both criteria FAIL. RCM v1 IS ABANDONED. (2026-09-01)

**One real-data execution, under the §66.5 lock (commit `fa78ddf9`, seed
1862893454), era 2020-01-01 → 2024-12-31, capital $800 (§65.1 USER
DECISION). 1,827 calendar days, none unaccounted; 14 calibration
equivalence asserts against the frozen builder, all passed; 349,832
funding stamps boundary-snapped per the §19.1 precedent; the sealed
interval was never touched (the era-end day's forward interval was
excluded rather than read, §65). The verdict below is the §60.12.5 table
read mechanically. No discretion was available and none was used.**

### 67.1 Criterion 2 — the residual-momentum IC: FAIL, decisively

```
  IC̄ = −0.0115    90% CI [ −0.0186, −0.0047 ]    ENTIRELY BELOW ZERO
  n_defined = 1,641 dates   exclusions: 0   mean block 11.8   B = 2,000
  criterion: CI_lower > 0 ⇒ PASS.  CI_lower = −0.0186.  FAIL.
```

The signal as frozen — residual momentum at lags 2–21/22–63, 0.6/0.4,
cross-sectional z, forward execution-horizon residual — predicts
**negatively**: residual REVERSAL, not momentum, at the one-day horizon
on the development era. This is not a power failure; the interval
excludes zero on the WRONG side with 1,641 equal-weighted dates and
nothing excluded. Per the §60.12.3 MDE disclosure, the realized
resolving precision is reported: CI half-width **0.0069** — the
procedure could resolve effects of that size, and the measured −0.0115
lies outside it. Criterion 2 is unconditioned on the portfolio, the
capital, the floors, and the solver: **no property of the sizing or the
optimizer touches this number.**

**The strategy's own guards agreed in real time:** the §60.2.2 sign
floor zeroed the calibrated slope on **1,048 of 1,642 decision days
(64%)**; mean s_mom was **0.051**; and **every one of the 1,642 decision
days carried the literal `CARRY REGIME — NOT RCM` label.** The system
spent the entire era correctly declaring that the residual-momentum
hypothesis was not present in its own book.

### 67.2 Criterion 1 — formation: FAIL, categorically

```
  evaluation_start 2020-08-03    completed windows 1,550
  windows failing: 1,550 of 1,550    min formed in any window: 0
  formed days: 22 of 1,827 (1.2%)
  calendar: gate 1,031 (56.4%) | operational 449 (24.6%) |
            structural 185 (10.1%) | degenerate 140 (7.7%) | formed 22
  gate composition: n_eff_long 909, n_eff_short 540, vol_ceiling 483,
                    exposure_retention 189, signal_coverage 91
  degenerate causes: constraint_interaction:breadth 85, no_trade 39,
                     constraint_interaction:chance 16
```

At $800 the floor did exactly what the §65.1 registration warned:
**material drops, not rounding** — on n_eff-failed days the surviving
min-leg breadth had median **4.16** (p5 = 1.0) against the frozen 6, far
beyond anything the §66.3 rounding rule addresses. `vol_ceiling` failed
483 days — dropped hedge names regaining modeled risk, §63.1.A.2.2's
mechanism measured at scale. `V_ret` spanned 0.000 to 2.098 (p5/p95).

### 67.3 FINDING F-3 — the frozen dollar-residual ceiling at real scale; shown non-invalidating

The 449 `D_operational` days are ONE failure repeated: the post-solve
dollar-neutrality check (`residual ≤ 1e-8 · G_cap = 3e-8`, frozen §60.7)
rejecting Clarabel residuals of **3e-8 to 1.3e-7** on real 100–260-name
problems — the F-2 class at the solve layer: a frozen numerical ceiling
calibrated on 20–30-name synthetic fixtures, unmet by the same solver's
honest arithmetic at ten times the dimension. Recorded as a finding.

**Row-3 diligence, discharged by margin arithmetic:** crediting ALL 449
days as formed gives at most 471 of 1,827 (26%) against the 60% bar,
with completed windows still at zero formed days — criterion 1 fails
under any reclassification; and criterion 2 involves no solve at all.
**No reproducible defect can flip either criterion, so the attempt is
NOT void** (§60.12.5 rows 3–4), and F-3 is a post-mortem observation,
not an escape.

### 67.4 The §64.3 reporting, full calendar

```
  calendar performance (price-only, full calendar): +4.8e-06/day mean;
      cumulative price +0.89%, realized funding +0.43%, frozen-cost line
      −0.16% (turnover 1.57 gross-units over the era) — the book existed
      on 1.2% of days; these lines describe near-permanent flatness
  Δ_gate       = +5.6e-04/day   90% CI [−5.2e-05, +1.3e-03]   (n 22/1031)
  Δ_transition = +9.9e-05/day   90% CI [−2.9e-04, +4.7e-04]   (n 1031)
      transition rule beside both: hold + G_ref downscale-only + M=7
      flatten (frozen). Neither interval excludes zero; neither is
      significance-gated; both are DIAGNOSTIC — CONDITIONAL ON
      FORMATION — NOT STRATEGY PERFORMANCE.
  K_t: median 2, p95 3, max 4 (vs MP edge; §63.5 references in the
      per-day file) — the real residuals carry 2–3 modes beyond BTC/ETH⊥,
      consistent with the §63.3 measurement.
  Per-day records: research/trial1/out/daily.jsonl (committed), causes
      on every degenerate day, coverage N/A where momentum mass was zero.
```

### 67.5 THE VERDICT — §60.12.5 row 2

> **Either criterion FAIL, no demonstrable implementation defect ⇒
> RCM v1 ABANDONED (§59.7); valid trials 1/20.**

Both failed. **RCM v1 is ABANDONED. Generation-2 valid trials: 1 of 20
consumed.** Per §59.7 the strategy is abandoned, not patched. Stated for
the record because it is the tempting move: the significantly NEGATIVE
IC is not a tradable discovery inside this governance — "flip the sign"
is a NEW hypothesis class requiring its own generation, governance,
budget and pre-registration; §60.2.2's sign floor exists precisely
because trading the reversal is a different strategy. No path from this
result to keeping any part of the signal exists (§60.12.5).

**What was learned, priced at one trial:** (i) one-day-horizon residual
momentum on crypto majors is measurably a small REVERSAL on 2020–2024;
(ii) the carry guard, the sign floor, the calendar, the causes, and the
attribution machinery all worked as specified on real data the first
time; (iii) $800 cannot express a 6-effective-name-per-leg book against
$5 floors (median surviving breadth 4.16); (iv) F-3. The holdout was
NEVER opened for either generation: 2025-01 → 2026-07 remains sealed,
with one look, ever, still unspent.

**Gen-1: frozen, 15 of 25. Gen-2: RCM v1 abandoned, 1 of 20 valid
trials spent. Holdout sealed.**

## 68. GENERATION 3 — governance (Stage G3-0 v2), appended 2026-09-02

**Process note, recorded first and honestly.** The G3-0 governance file
and the G3-A audit outputs were committed together by the owner in
`d8820ca` (2026-09-01) with the ledger append left as an owner step;
this append happens 2026-09-02, AFTER G3-A ran. The ordering deviation
is mitigated by three facts: the governance text below is transcribed
from the committed file and is diffable against it; G3-A produced no
return-based result that could have shaped this governance; and G3-A's
own report explicitly declined to write the ledger. Deviation recorded,
not repeated. User decisions are labelled as such throughout.

### 68.0 Design signature

> **Gen-3 replaces arbitrary gates with measured uncertainty.** Breadth,
> maturity, holding period, forecast confidence, allocation, and model
> trust are all continuous quantities estimated from data, not
> categorical rules chosen by a designer. Where a hard rule remains, it
> is a *safety* rule or a *data-integrity* rule, never a modelling
> convenience.

The structural difference from Gen-1/2, both of which died on fixed
invariants that were free at institutional scale and fatal at $800.

> **Correction (§68.12.8):** both suffered fixed-invariant formation
> failures; **Gen-2 additionally and independently failed at the signal
> level** — RCM v1's residual-momentum IC was −0.0115 with its entire CI
> below zero, independent of breadth, capital, solver behaviour, and
> formation. See §68.11.4.4.

### 68.1 Thesis and construction principle

**Thesis:** short-horizon crypto returns increasingly reflect
time-varying transmission of macro, cross-asset, and information
shocks; a useful system models the state and propagation of information
across markets and lets predictor relevance evolve.

**Construction principle — USER DECISION, verbatim:**

> **Breadth follows evidence.** No required number of names exists
> anywhere in Gen-3 — no `N_eff` floor, no `MIN_LEG_NAMES`, no forced
> substitution, no position added for diversification alone.
> `N_t = #{i : w_i,t ≠ 0}` is an **output**. Nothing qualifying ⇒
> **flat, a correct output**. One name qualifying ⇒ that name and cash.
> Twelve ⇒ a risk-sized twelve-name book, floor-aware by construction.
> Effective breadth is **reported**, never required.

**Why (68.1.3):** every non-formed day across three generations —
Gen-1's 21% train skips, the 0-of-12 live in 2026, Gen-2's 98.8%
non-formation with median surviving breadth 4.16 against a required 6 —
was a breadth requirement colliding with $800 against $5 floors.
*(The phrase "not one was a signal failure" is qualified by §68.11.4.4
below.)* **No artificial cash reserve**: the system protects risk
capacity and bank survival, not an idle-cash percentage; cash competes
in the allocator. **Derived sizing quantities:** conviction threshold
`|P(up) − 0.5| > c_rt/(2·E|r|)` (≈0.023 at 13 bps and 2.8% BTC mean
absolute move), updated mechanically; risk PENALTY `max_w μ̂ᵀw − λ wᵀΣw
− C(w − w_prev)` under hard caps, with **λ the owner's risk aversion, a
preference stated before data** (the fixed vol target is dropped; the
earlier "30–40% BTC" is a confidence-conditioned allocation preference
to be fixed as λ plus caps in the spec stage); `C(·)` is the frozen
cost model, not a free coefficient.

### 68.2 Adopted invariants

1. **Probability is not capital** — allocation weighs net-of-cost edge,
   calibrated probability, forecast uncertainty, tail risk, liquidity,
   cost, correlation, model health, and hard caps; never `P(up)` alone.
2. **Calibration precedes sizing**; a conservative reliability bound may
   size. Method fixed in the spec stage.
3. **Net-of-cost edge decides**: `μ_net ≤ 0 ⇒ position zero`, normally.
4. **Losses never increase exposure**: negative PnL ⇒ `|w_target| ≤
   |w_current|` for that asset. **Precedence, fixed now: §3.4 beats
   §3.5** (a strengthening forecast after a loss does not add). Owner
   preference, not a derivation.
5. **Every position continuously re-earns its place** — no mandatory
   holding period; the standing question is "if flat now, would current
   information justify opening this?"
6. **Two exit classes, both forecast-driven**: invalidation
   (`r_path < Q_α(F̂_entry)`) and updated-forecast (edge no longer covers
   cost + required risk compensation); a separate catastrophic stop
   exists; `α` fixed in the spec stage.
7. **The invalidation rate IS the model-health statistic** — realized
   rate materially above `α` is direct miscalibration evidence.
8. **Forecast confidence ≠ model-health confidence**: poor recent
   calibration inflates `σ_eff = κ_t σ_model` (κ ≥ 1) and shrinks size
   without flipping direction; recovery by a rule frozen before live.
9. **Losing money is not automatically a bug** — investigation triggers
   on incompatibility with the system's own uncertainty, risk model, or
   operational invariants (§60.12.5's rule, generalized).

### 68.3 Hierarchical cold-start — the juvenile architecture

For each asset `r_i = β_M,i·M + β_chain,i·F_chain + β_sector,i·F_sector
+ α_i + ε_i`, with **per-coefficient posterior shrinkage** toward group
priors: `E[β_i|D_i] = λ_i·β̂_i + (1−λ_i)·μ_group`, `λ_i = τ²_group /
(τ²_group + s²_i)`, with SEPARATE λ for market/chain/sector/alpha —
empirical Bayes, **no age curve, no 30/60/90-day rule, no promotion
decision, no owner discretion**. `juvenile`/`mature` may appear only as
dashboard labels with zero mechanical meaning.

**Posterior uncertainty propagates** into covariance, chance
constraints, and sizing. **Recorded precisely: hierarchical shrinkage
does not make young assets hedgeable — it converts hedgeability from a
binary certification problem into a continuous estimation-and-
uncertainty problem.** A `β = 1.4 ± 0.9` is a valid estimate and a poor
hedge; the risk engine sizes accordingly instead of the guard refusing
the day.

**PIT ancestry, frozen and boring:** chain from the token's canonical
chain; sector from a frozen metadata taxonomy assigned by a frozen rule
from information available at that time; **no LLM taxonomy decisions**
(the §66.0.1 fabrication risk applied to the input that picks a prior);
taxonomy changes are themselves PIT; **ambiguity ⇒ fewer parents**,
failing toward the broadest defensible prior; tree levels frozen before
any test. **Leave-one-out PIT group priors** — `μ_group,t` excludes the
forecast asset's own contribution (else the prior partially predicts
what it is meant to predict). **`UNMODELABLE` is the sole hard
maturity-related state** — a data-integrity floor: absent reliable
price/volume/depth/identity/venue/history ⇒ no forecast, zero
allocation. **Emergent sizing property:** an inherited-forecast juvenile
is economically a worse way to buy its parent, so a correlation-aware
allocator routes exposure to the parent without any juvenile haircut —
none is introduced unless survival analysis later shows the need.

### 68.4 Inheritance from Gen-1/2

Reused unchanged: PIT store + lookahead discipline; crypto universe
filter + composition guard; read-only production data client; fill
simulator + frozen execution model; shared quantized sizing;
settle/reconciliation; risk layer, kill switch, watchdog, supervisor,
dashboard, alerts; cost log; the §60.12 IC evaluator and Gen-1
bootstrap code; the §59.11.2/§62.4 calendar; the §63.6
factor-structured residual covariance and MP-edge rule;
pre-registration, trial-budget, INDETERMINATE, void, append-only-ledger
conventions; the §57.2/§56.9 must-re-verify list. **Not inherited:**
any breadth requirement; market-neutrality as a requirement (BTC is a
traded direction, not a hedged factor); the RCM residual-momentum
signal; the fixed volatility target.

### 68.5 Data policy

Development **2020-01-01 → 2024-12-31**, sequential-in-time only.
**2025-01 → 2026-07 SEALED, re-affirmed on Gen-3 grounds** — the thesis
was formed observing that window; runner rejection unchanged.
Validation forward-only from a recorded freeze commit + UTC timestamp;
forward validation cannot confirm alpha. Every external source carries
a first-public timestamp; event date and availability date never
conflated.

### 68.6 Standing rules

1. **Horizon by arithmetic, not doctrine** — trading admissible only
   where edge exceeds turnover-adjusted cost at that horizon; realized
   turnover disclosed beside every skill number (13 bps round-trip ⇒
   daily ≈47%/yr, weekly ≈7%/yr). Sub-daily measurement always allowed.
2. **M0 includes carry** (60% of Gen-1 PnL; Gen-2's guard fired on all
   1,642 days). Exogenous tests are incremental beyond state AND carry.
3. **Two numbers beside every skill test**: the statistical criterion
   and a pre-registered economic-relevance disclosure (never a gate).
4. **Feasibility at $800 by construction** (§68.1), tested.
5. **The neural network is a ladder rung, not the spine** — admissible
   only as M2+ under the same incremental criterion, all selection
   inside training windows by a frozen nested procedure; INDETERMINATE
   leaves the simpler model standing.
6. **The LLM event layer is quarantined to a later rung** — closed
   taxonomy, surface facts only, frozen model/prompt hashes, extraction
   source-hashed, tests with and without judgment fields, prior-leakage
   partly unmeasurable and recorded as such.
7. **Synthetic fixtures match real dimensions** — no lock without a
   development-scale-N dry run (F-2/F-3: fine at 25, broken at 200).
8. **Hard gates on the ladder** (amended by §68.11.4.2 below).
9. **No hand-labelled regimes** — continuous state variables only.
10. **Operational architecture required but deferred** (§68.7).

### 68.7 Operational architecture — adopted as requirement, built after the test

The refinements' §18–38 are adopted NOW as design requirements and
built ONLY after forecast skill is established (Gen-2's real cost was
engineering around a signal that did not exist): four safety layers;
Level 0–5 authority moving upward only; position/portfolio/
infrastructure supervisors with circuit-breaker scopes; **recovery vs
repair — the machine may restore known-good operation; it may not
redefine what "good" means**; bounded autonomous-recovery lists with
attempt/time/recurrence caps; persistent `HUMAN_INTERVENTION_REQUIRED`
latches surviving reboot, owner-clearable only; `H_*` fault families
with **`H_UNKNOWN` failing closed**; forbidden-autonomous-repair list;
forensic snapshots; owner-controlled resume; post-incident probation.
**Central rule, verbatim:** *If our understanding of reality becomes
questionable, stop trading first and investigate second.*

### 68.8 Trial budget and sequential protocol

**Ceiling 20 trials** (a trial = any real-data result that could cause
preference between forecast or portfolio specifications; structure
measurements are not trials). INDETERMINATE is valid — the simpler
model stands. **Sequential-in-time development**: expanding-window fits
refit at calendar-year boundaries; 2021–2024 forecasts each come from a
model that saw only prior years; 2024 never informs 2021's beliefs.

### 68.9 Phase one — the first killable test

**G3-A (no trial):** data audit and PIT policy for ES, NQ, VIX, US
2Y/10Y, DXY, gold, BTC, ETH; report procurement; stop only if a source
is unavailable at any price; substitutes reported without adoption.
**G3-B (no trial):** rolling lead/lag `Corr(r_X,t, r_BTC,t+k)` and
rolling betas at 1h/4h/daily where data permits — measurement, raw
series, no narrative labels, no thresholds; window, k-range, statistics
frozen in the ledger before reading. **G3-C (ONE trial):** two frozen
models, `M0` = crypto-native state + carry, `M1` = M0 + PIT-aligned
prior-period cross-asset state; scored on every out-of-sample-in-time
day at the daily horizon and one pre-registered sub-daily horizon:

```
  Q1  M0 BTC-direction skill vs climatology   Brier skill, bootstrap 90% CI (inherited code), CI_lower > 0
  Q2  M1 incremental over M0, BTC direction   paired Brier difference, CI_lower > 0; INDETERMINATE ⇒ M0
  Q3  M0 cross-sectional skill (mature)       §60.12 daily-IC evaluator, CI_lower > 0
  Q4  M1 incremental cross-sectional skill    paired daily-IC difference, CI_lower > 0; INDETERMINATE ⇒ M0
```

Beside each: calibration report; realized CI half-width as resolving
precision (§60.12.3 discipline — no fabricated MDE); the economic
disclosure. **Pre-registered consequences:** Q2 or Q4 pass ⇒ exogenous
thesis supported, next rung earns its turn; Q2 and Q4 fail but Q1 or
Q3 pass ⇒ exogenous thesis NOT supported, construction may proceed on
crypto-native forecasts relabelled as such; all four fail ⇒ **Gen-3
stops before any book exists.** The hierarchical layer (§68.3) is a
later rung, tested only after Q3/Q4, by the pre-registered
now-mature-token replay with leave-one-out priors. *(Q2/Q4 criteria
amended conjunctive by §68.11.4.1.)*

### 68.10 Kill criteria form

Exact quantities derived in the spec stage; form fixed now: (i) the
Q-table; (ii) for any construction, a minimum activity level derived
from what breadth-follows-evidence claims — expected conviction-day
frequency from the forecast's own calibration, never inherited from
Gen-1/2; (iii) a forward feasibility gate. **Abandon, not patch.**

#### 68.10.1 G3-A execution record (2026-09-01, commit `d8820ca`)

Seven raw series staged under `data/exogenous/` with `adopted: false`
and per-series PIT metadata in `MANIFEST.json`: FRED DGS2/DGS10
(primary), CBOE VIX + FRED VIXCLS cross-check (primary), FRED DTWEXBGS
(broad-dollar SUBSTITUTE for ICE DXY), FRED SP500/NASDAQ100 (cash
SUBSTITUTES for ES/NQ futures). Gold: no clean free source reachable
(FRED LBMA IDs discontinued; Stooq behind a JS proof-of-work wall;
Yahoo rate-limited/ToS-restricted) — MUST-PROCURE; §68.9's hard stop
did not fire (nothing unavailable at every price). BTC/ETH already in
the Stage 1 PIT store. Blocker evidence and per-instrument semantics in
`Claude/G3A_DATA_AUDIT.md`. No forecast fitted, no development returns
read, no trial consumed. **The audit's defects are §68.11's subject.**

### 68.11 Stage G3-A2 — audit, source-policy, and governance corrections (2026-09-02; both delegates converged)

**Appended before any correcting code. No forecast fitted; no
return-based comparison; no G3-B measurement; no trial consumed. Gen-3
0 of 20. Holdout sealed.**

#### 68.11.1 PIT hazards — both are lookahead (Part I)

**1. CRITICAL — never store a UTC constant for a release time.** The
manifest hardcoded "16:15 ET → 20:15 UTC", true only under US daylight
time; in winter the release is an hour later in UTC, so a model reading
between the two would consume a release that has not happened — **one
hour of lookahead for ~4.5 months of every year**, invisible at daily
resolution and destructive in any intraday branch. Every release time
becomes `release_timezone` (IANA) + `release_local_time` +
`release_calendar`, converted per-date with `zoneinfo`. Tests: a
January and a July observation of one source resolve to DIFFERENT UTC
instants; no `"20:15Z"`-style constant survives in the manifest or
loader (grep).

**2. CRITICAL — four timestamps, and the one that governs access.**
Per observation: `observation_time` (the economic period),
`underlying_public_time` (first public anywhere),
`source_available_time` (when THIS staged source first served it),
`retrieved_at_utc` (when we archived our copy). **Access rule, frozen:
a model may consume a value only when `source_available_time ≤ t`.** A
FRED-staged cash close may not be acted on hours before FRED served
it, however public the underlying was; a production feed swap
re-derives the manifest entry under the new feed's availability.
`retrieved_at_utc` answers auditability and never substitutes for
`source_available_time`. **Revisable series** (future macro rungs)
additionally carry `revision_id`/vintage — observation → publication →
revision → retrieval, four distinct fields — recorded now so CPI/PCE/
NFP cannot be added without it.

**Calendar implementation note (recorded with it):** release-day
shifting uses one conservative US business calendar — weekends,
observed federal holidays, and Good Friday, as the UNION of the Fed and
NYSE/CBOE closure sets. The union can only DELAY an assumed
availability, never advance it, so its errors are anti-lookahead by
construction; per-source exact calendars are an open refinement listed
in the manifest.

#### 68.11.2 Source policy and provenance (Part II)

**Panel split.** Panel A (daily baseline): BTC/ETH + carry, daily VIX,
daily rates, a daily USD measure, cash equity indices, daily gold —
audit sufficient to SPECIFY, subject to explicit adoption decisions;
available ≠ adopted. Panel B (event-driven/intraday): requires
time-resolved instruments (ES, NQ, rates proxy, DX/real-time USD, VX if
justified, GC); **cash indices are not substitutes there** — no Globex
session, and the US-close → Asia → Europe → next-US-open window is
precisely what a 24/7 book would exploit. Procurement reframes from
"do we need ES?" to **"do we activate the intraday/event-driven
branch?"** — if yes, futures-quality data is necessary.

**Adoption decisions, listed as OPEN, none made here:** DTWEXBGS in
place of ICE DXY (different basket — difference recorded); FRED cash
SP500/NASDAQ100 in place of ES/NQ for Panel A only (overnight-gap
limitation recorded); whether gold is included at all, and from where.

**Gold source UNVERIFIED:** the manifest entry becomes
`candidate_source: Nasdaq Data Link LBMA/GOLD`,
`verification_status: UNVERIFIED` until a key actually returns data;
the audit's confident phrasing is withdrawn.

**Manifest provenance per series:** timezone-aware `retrieved_at_utc`
(replacing `date.today()`), HTTP `Last-Modified`/`ETag`/status, source
timezone, publication rule, `revision_policy`, `vintage_support`,
`licence_class` ∈ {public_domain, redistribution_restricted, licensed}.
For the seven already-staged files the original wall-clock and HTTP
headers were NOT captured; `retrieved_at_utc` is recorded as bounded by
commit `d8820ca`'s timestamp with that caveat, and the corrected tool
captures true values on any future retrieval — recorded, not
back-filled.

#### 68.11.3 The licensing incident (Part III)

FRED's S&P 500 and NASDAQ-100 series are redistribution-restricted;
raw CSVs were committed to a public repository in `d8820ca`.
**Immediate:** the restricted CSVs stop being tracked (`git rm
--cached`) and move to `data/exogenous/raw/`, which is gitignored; the
CBOE VIX files are conservatively classified redistribution_restricted
and treated the same; the downloader, manifest, identifiers, hashes,
coverage statistics, and licence classes stay in Git. **Default policy,
recorded: vendor/index raw data is never committed; public-domain
series may be, by explicit classification** (the Fed H.15/H.10 series
remain tracked as `public_domain`). **Deliberately NOT done: purging
the blobs from public history.** A filter-repo rewrite changes every
commit SHA, and this project uses commit hashes as governance locks
(§63.6, §64, §66.5); rushing it would invalidate the audit trail.
**OPEN REMEDIATION ITEM:** whether to rewrite history, and if so how
recorded lock hashes are re-anchored (e.g., a mapping table appended to
the ledger before the rewrite) — a separate, deliberate decision.

#### 68.11.4 Governance repairs to §68 (Part IV)

**1. Q2/Q4 conjunctive criterion** (repairs a false-PASS: BSS(M0) =
−0.20, BSS(M1) = −0.10 would have "supported the thesis" while both
lose to climatology): require, with the frozen confidence criterion on
BOTH, `Skill(M1) > 0 AND Skill(M1) − Skill(M0) > 0`; identically for
Q4, `IC(M1) > 0 AND IC(M1) − IC(M0) > 0`. "Made a bad predictor less
bad" is not a usable predictor.

**2. Mechanism-specific ladder** (replaces §68.6 rule 8's over-strong
gate): the crypto-native baseline feeds TWO cheap independent rungs —
cross-asset and scheduled-macro — each on its own pre-registration; a
cross-asset failure does not forbid the scheduled-macro rung; **only if
both cheap exogenous mechanisms fail are the expensive rungs
(corporate | congressional | news/LLM) blocked.** Kill-cheap-first
preserved without conflating mechanisms.

**3. Feature budget — families capped AND transforms frozen** ("≤ 8
features" was impossible as written, M1 being M0 + seven series):
**≤ 8 feature FAMILIES per model, and the transformation set within
every family separately frozen before the trial**; a family may not be
expanded after observing OOS performance. (Example of a frozen family:
rates = {2Y level, 2Y Δ, 10Y level, 10Y Δ, 2s10s slope}.) Exact lists
fixed in the spec stage.

**4. Gen-2 historical wording — the independent rejection preserved,
verbatim:**

> Gen-1 and Gen-2 both suffered material portfolio-formation failures
> caused by fixed breadth/floor interactions at small capital.
> **Separately, Gen-2 RCM v1 was decisively rejected at the signal
> level by negative residual-momentum IC (−0.0115, CI entirely below
> zero); that rejection was independent of breadth, capital, solver
> behaviour, and portfolio formation.** RCM would not have worked with
> dynamic breadth.

**Stop point:** after the Part I–III code lands and its tests are
green, this stage STOPS — before G3-B, whose protocol must be frozen in
the ledger before any reading.

### 68.12 Stage G3-A3 — eight corrections before G3-B (2026-09-03; first delegate identified, second accepted; three correct the second delegate's own rules)

**Appended before any correcting code. No forecast fitted; no
return-based comparison; no G3-B measurement; no trial consumed. Gen-3
0 of 20. Holdout sealed.**

**Process note on the one authorized exception to strict byte-prefix
appending:** item 8 requires the Gen-2 correction pointer to be VISIBLE
AT §68.0 — its entire purpose is that a reader who stops at the design
signature not learn the wrong history — so the delegates' converged spec
explicitly authorizes appending a pointer blockquote to the end of
§68.0's block, editing nothing. Executed as a mid-file insertion of new
text only: the appending script asserts programmatically that removing
the inserted block restores the prior file byte-for-byte, and both
hashes are recorded in this commit's message from printed output.
§68.0's original words, and §68.1–§68.11, are bit-unchanged.

#### 68.12.1 Publisher vs aggregator — the source chain must not collapse

If the bytes come from FRED, the publisher's release (e.g. the Fed's
H.15) is `underlying_public_time`; **FRED's own serving time is
`source_available_time`**, and access is governed by the latter. Calling
FRED "primary" while using the Fed's release rule conflated the two.
Every manifest entry gains `publisher` (who originates the value) and
`retrieval_source` (from whom we obtain the bytes), with one of two
admissible resolutions recorded per series: pull from the publisher and
use its release rule, or keep the aggregator under a **conservative
aggregator-availability rule** — an assumed lag that can only DELAY,
never advance, availability (§68.11.1's anti-lookahead principle).
**Resolutions recorded now:** the FRED-sourced Fed series (DGS2, DGS10,
DTWEXBGS) keep the aggregator with source availability set to
**one business day after the publisher release, at the same local
time** — strictly later than the Fed release, delay-only; the FRED index
mirrors (SP500, NASDAQ100, VIXCLS) already carried a conservative
end-of-next-business-day mirror rule, kept; `cboe_VIX` genuinely has
`publisher == retrieval_source` (CBOE serves its own history file) and
keeps its same-day rule; gold's resolution is recorded as to-be-
established on procurement. A test asserts publisher and retrieval
source are distinct wherever they genuinely are.

#### 68.12.2 Unknown is not estimated — provenance follows the same rule

The A2 encoding put an inferred commit-timestamp bound INTO
`retrieved_at_utc`, a field later code would read as an observed
acquisition time. Replaced: `retrieved_at_utc = null`,
`retrieved_at_upper_bound_utc = <commit d8820ca timestamp>`,
`retrieval_time_quality = "upper_bound"`. The corrected tool records
true observed values (`retrieval_time_quality = "observed"`) on any
future retrieval; nothing is back-filled.

#### 68.12.3 "Minimum activity level" removed as a kill criterion (§68.10)

**Corrects a rule the second delegate wrote.** §68.10's minimum activity
level, derived from expected conviction-day frequency, reintroduced a
formation requirement under a new name — the precise invariant Gen-3
exists to remove (§68.1). **If the correct model finds two trades in a
month, two trades are correct.** Replacement: activity rate and expected
trade count are a DISCLOSURE beside every result, never a gate; too few
observations to resolve the hypothesis at the frozen precision ⇒
**INDETERMINATE, not FAIL**; a mechanical-feasibility test (minimum
notional, liquidity, execution viability) remains admissible — "the
strategy must trade often enough" does not.

#### 68.12.4 The probability threshold is an illustration, not governance (§68.1)

**Corrects a formula the second delegate derived.**
`|P(up) − 0.5| > c_rt/(2·E|r|)` holds only under payoff symmetry and
contradicts the adjacent rules that allocation uses the predicted return
distribution with asymmetric tails. Demoted to a special-case
illustrative derivation. **The general gate:**
`E[r_i | F_t] − C_i − required_risk_compensation > 0`.

#### 68.12.5 Invalidation — a path statistic, and one health metric among several

**Corrects two claims the second delegate endorsed.** A terminal-return
quantile is not a valid intratrade barrier: invalidation must be defined
against an explicitly forecast **path statistic** (maximum adverse
excursion, barrier-hit probability, or another named quantity), fixed in
the spec stage — `r_path < Q_α(F̂_entry)` as written is WITHDRAWN pending
that definition. And invalidation coverage is *a primary* model-health
statistic, not the entire health state: calibration drift, distribution
shift/OOD, ensemble disagreement (if used), tail severity, and
correlated simultaneous failures may carry independent information. The
composite health rule is deferred to the spec stage, not asserted.

#### 68.12.6 Cold-start sequencing depends on Q3 alone (§68.9)

The hierarchical juvenile rung asks whether cross-sectional forecasting
extends to insufficient-history names; its prerequisite is that the
mature-name cross-section works at all — **Q3 PASS**. Q4 determines only
whether M1 exogenous features are INHERITED into that rung, not whether
the rung may run: Q3 pass + Q4 fail still leaves a crypto-native +
chain/sector hierarchical cold-start model testable.

#### 68.12.7 The ladder's explicit INDETERMINATE branch (§68.11.4.2)

Frozen: cheap-rung outcomes map to the expensive rungs (corporate /
congressional / news-LLM) as — **≥1 PASS ⇒ may be proposed** (each on
its own pre-registration); **no PASS but ≥1 INDETERMINATE ⇒ DEFERRED,
not rejected**; **both FAIL ⇒ BLOCKED under the current generation**. An
unresolved cheap rung must not permanently block the expensive ones, and
a merely-deferred state must never later be read as a pass.

#### 68.12.8 §68.0 carries the Gen-2 correction itself

§68.0 said Gen-1/2 "both died on fixed invariants"; §68.11.4.4 corrected
this two hundred lines later. The correction pointer now sits inside
§68.0's own block (inserted per the process note above, verbatim as the
delegates specified), so the design signature cannot teach the wrong
history to a reader who stops there.

#### 68.12.9 Non-blocking note — leave-one-out scope (§68.3)

Recorded, not changed: LOO group priors remain frozen for the cold-start
EVALUATION rung, where they are the correct conservative benchmark rule.
In a properly specified hierarchical model, using an asset's own past
PIT observations to estimate shared hyperparameters is not inherently
lookahead; the real hazards are future information and double-counting
one observation in both prior estimation and asset likelihood. The
production hierarchical estimator is separately specified later; this
note prevents the benchmark rule from being read as a prohibition on
hierarchical estimation generally.

**Stop point: after the manifest/loader corrections and tests land
green, this stage STOPS — before G3-B, whose protocol must be frozen in
the ledger before any reading.**

### 69.0 Stage G3-B Part 0 — provenance quality (2026-09-02; frozen before any reading)

**No forecast fitted. No model compared. No trial consumed. Gen-3 0 of
20. Holdout sealed. This section and §69.1 are appended, in their own
commit, before any Part-0 code and before any series is read.**

Every staged Panel-A series' `source_available_time` rests on an
ASSUMED conservative rule (§68.12.1), not an observed publication
timestamp — including `cboe_VIX`, whose same-day publisher rule is a
release-time assumption even though the bytes come from the publisher.
Recorded per series in the manifest:

    source_availability_quality  = "conservative_assumption" | "observed"
    source_availability_basis    = the exact rule assumed

Values recorded now, all `"conservative_assumption"`:
  - fred_DGS2 / fred_DGS10 / fred_DTWEXBGS — basis: "publisher + 1
    business day, same local time"
  - fred_SP500 / fred_NASDAQ100 / fred_VIXCLS — basis: "end of next
    business day after the underlying close (mirror rule)"
  - cboe_VIX — basis: "same-day release at release_local_time in
    release_timezone (publisher's own file; assumed, not observed)"
  - gold_LBMA — fields NOT set: no availability rule exists yet
    (UNVERIFIED, §68.11.2); it receives them on procurement.

`"observed"` may be recorded only if genuine historical serving
timestamps are obtained or a direct publisher feed replaces the
aggregator — never assumed.

**Why this matters, recorded:** the conservative rule discards real
information. Without the quality field, a failed cross-asset result
cannot be distinguished between "the information genuinely does not
predict" and "we handicapped the data by ~24h because exact historical
aggregator timing was unavailable." Those are different findings.

**Two invariants, pinned by tests:**

    source_available_time >= underlying_public_time   (per observation, always)
    PIT reader: source_available_time <= decision_time, else the
    observation is NOT returned

The first is swept across a full calendar year (both DST transitions,
holidays, a leap day) for every staged series, not asserted on a single
date.

### 69.1 Stage G3-B — the measurement protocol, frozen before any series is read

Governed by §0, §63.2 protocol discipline, and §68 as amended (§68.11,
§68.12). Every statistical object below is defined HERE, before any
value is read. §69.2 will contain results reported against this section
with no post-hoc adjustment.

#### 69.1.1 CONTAMINATION CLOSURE — M1's lag structure is frozen first

G3-B measures which lags of each exogenous series lead BTC. The spec
stage then selects M1's features. If the map informed which lag enters
M1, that would be selection on development returns performed without
spending a trial — the trial-budget mechanism bypassed by a
"measurement." Frozen now, from architecture, before any series is
read:

> **M1 uses, for each adopted exogenous series, the single most recent
> PIT-available observation at the decision time — one lag, no lag
> search.** This is what the §68.11.1.2 access rule already implies. No
> lag, window, or transform in M1 may be chosen from the G3-B map.

The map is therefore **descriptive only**: it characterises
transmission and its variation over time. It is **quarantined from
feature selection**, and that quarantine is recorded here, not asserted
later. The return conventions defined in §69.1.3 below are conventions
OF THIS MAP alone; M1's feature transforms are chosen at the spec stage
from architecture and inherit nothing from them.

#### 69.1.2 Scope — Panel A only

Panel A (daily) alone. Panel B requires licensed futures data and an
owner decision on the intraday branch; G3-B does not wait on it and
makes no intraday claim from cash-index data (§68.11.2).

The measurement target is **BTC** — the §69.1.3 formula fixes
`r_BTC` as the reference leg. The comparator set X comprises the seven
staged Panel-A exogenous series (fred_DGS2, fred_DGS10, fred_DTWEXBGS,
cboe_VIX, fred_VIXCLS, fred_SP500, fred_NASDAQ100 — all
`adopted: false`, and measuring with them adopts nothing) **plus
ETHUSDT as the crypto-internal comparator** — the resolution of the
stage file's scope line "BTC, ETH (PIT store) and the adopted-pending
Panel A exogenous set": the formula names BTC as the sole target, so
every other in-scope series enters as an X. Recorded here as a
completion made before any reading; gold_LBMA is excluded (no data, no
availability rule).

Development window **2020-01-01 → 2024-12-31** only. Crypto legs come
from the PIT store's 1d UTC bars via a seal-checked, hard-capped,
read-only load (the §63.2.1 pattern, re-used verbatim); exogenous legs
come through `tools/g3_exogenous_loader.pit_view` under §69.0's
invariants, with observations additionally clamped to
`observation_time <= 2024-12-31`.

#### 69.1.3 Statistics — every object defined before reading

**Snapshot instant.** For UTC calendar date `t`, the snapshot instant
is `tau_t` = 00:00:00 UTC of the following day (the exact instant date
`t` ends). All availability comparisons are against `tau_t`.

**Crypto returns.** `r_BTC,t = ln(C_t / C_{t-1})` where `C_t` is the
1d-bar close for UTC date `t` (BTCUSDT; identically ETHUSDT for the
comparator). A date-`t` close is knowable exactly at `tau_t`.

**Exogenous stale-carry.** `V_X(t)` = the value of the most recent
observation of X with `source_available_time <= tau_t` (and
observation_time inside the development window). Undefined before the
first such observation. A date with no NEW observation becoming
available in `(tau_{t-1}, tau_t]` is a **stale (carried) date** for X.

**Exogenous returns — conventions of this map only (§69.1.1):**
  - fred_DGS2, fred_DGS10 (yield levels, percent):
    `r_X,t = V_X(t) - V_X(t-1)` — first difference in percentage
    points (log-differencing a near-zero 2021 short yield is not
    meaningful).
  - fred_DTWEXBGS, cboe_VIX (close column), fred_VIXCLS, fred_SP500,
    fred_NASDAQ100: `r_X,t = ln(V_X(t) / V_X(t-1))`.
  - A carried date has `r_X,t = 0` and `stale = 1` — staleness is a
    state to report, never to hide.

**Window.** The frozen 90-day window (§63.6 precedent). No other
window. **No partial windows:** a statistic at `t` exists only when
every one of its 90 aligned pairs is defined and every date it touches
lies inside the development window; otherwise the record is absent. The
sample size `n` (= 90 by construction) is still reported per record.

**Lead/lag.** For each X and each
`k in {-5,-4,-3,-2,-1,0,+1,+2,+3,+4,+5}` calendar days:

    rho_k,t = Corr( r_X,(t-89 ... t) , r_BTC,(t-89+k ... t+k) )

both Pearson and Spearman. Spearman is defined as Pearson on
average-rank-transformed data (ties receive the mean of the ranks they
occupy); implemented directly, no external statistics dependency.
`k > 0` shifts the BTC leg LATER in time; nothing here is named
"leading" or "lagging" in any output.

**Knowable-at rule.** `rho_k,t` is knowable at
`as_of(k, t) = t + max(k, 0)`: for `k > 0` the statistic does not
exist at `t` and appears only at `t + k`. The rule is deterministic and
recorded in every output header; a test asserts no `rho_k,t` uses any
input knowable only after `as_of(k, t)`, and no window touches any
date outside the development window.

**Rolling beta.** `beta_X,t` = the OLS slope (intercept included) of
`r_BTC` on `r_X` over the same 90 contemporaneous (`k = 0`) pairs, with
its classical standard error
`SE = sqrt( (RSS/(n-2)) / sum((r_X - mean(r_X))^2) )`. A window with
zero variance in either leg emits `null` with a reason string, for
beta and for the correlations alike.

**Emitted per (X, t), one JSONL record:** `rho_pearson[k]` and
`rho_spearman[k]` for all eleven k, `beta`, `se_beta`, `n`, the
per-date `stale` flag, and `stale_days_in_window` (carried dates among
the 90). **Across dates, per (X, k, statistic):** the p5/p25/p50/p75/
p95 distribution (§63.2.4.2 percentiles — the only percentiles).

**Destinations.** Full raw per-date series →
`research/g3b/out/diagnostics.jsonl`; the distributional summaries →
§69.2 and summary records in the same file. The first record of every
output file is a header stating: descriptive only; quarantined from
feature selection (§69.1.1); no adoption (`adopted: false` stands); no
trial consumed; no skill claim; Q1–Q4 remain the only skill criteria.

**No thresholds. No narrative labels. No "regime" statistic.** The
delegates read the series; they do not receive a story.

#### 69.1.4 Delay-cost sensitivity — descriptive, quarantined

The same map, computed a second time with availability =
`underlying_public_time` (publisher-release timing) instead of the
aggregator rule, written to `research/g3b/out/sensitivity.jsonl`. This
does NOT adopt publisher timing; its only purpose is to make §69.0's
distinction measurable — how much transmission the conservative rule
discards. Quarantined from feature selection and from any adoption
decision. Its header and EVERY record carry the label:

    SENSITIVITY — NOT THE PIT-VALID SERIES

#### 69.1.5 What G3-B is not

- Not a trial: no forecast is fitted, no specification is compared, no
  result can cause preference between models (§69.1.1 guarantees this
  by freezing the lag structure first).
- Not an adoption decision: the Panel A substitutes remain
  `adopted: false`; measuring with a series does not adopt it.
- Not evidence of predictability: a correlation map is not a forecast
  test. Q1–Q4 remain the only skill criteria.

#### 69.1.6 Execution order, frozen

1. Part 0 fields + tests land green (no development values read by the
   new code paths beyond what the existing A2/A3 tests already touch).
2. BEFORE the first measurement read, two refusals are exercised and
   their messages logged verbatim: (a) `rcm.seal.assert_range_allowed`
   on a range one day into 2025 → `SealViolation`; (b) a G3-B map
   request for `t = 2025-01-01` → refused by the module's own hard cap.
   Both quoted in §69.2 from printed output.
3. The §69.1.3 measurement runs once over the development window; then
   the §69.1.4 sensitivity; §69.2 is appended from printed output only
   (file sha256s included), and the stage STOPS — the spec stage
   (feature families and frozen transforms, forecast form, lambda and
   caps, calibration, the path-statistic invalidation definition, exact
   Q1–Q4, lock commit) follows under both delegates' review before the
   single G3-C trial.

### 69.2 Stage G3-B — execution record (2026-09-02; single measurement run; reported against §69.1 with no post-hoc adjustment)

**No forecast fitted. No model compared. No trial consumed. Nothing
adopted. No skill claim — Q1–Q4 remain the only skill criteria. Gen-3
0 of 20. Holdout sealed.** Everything below is quoted from printed
output; nothing was predicted.

#### 69.2.1 Refusals exercised BEFORE the first measurement read (§69.1.6 step 2)

Both refusals fired and were logged verbatim:

```
REFUSAL EXERCISED: SealViolation: request [1577836800000, 1735689600000] intersects the SEALED interval [1735689600000, 1785542399999] (2025-01-01 .. 2026-07-31). The seal is re-affirmed on Gen-2 grounds (NOTES 59.1); opening the challenge set requires an explicit UnlockToken AND a ledger entry committed beforehand — by deliberate user decision, once, ever.
REFUSAL EXERCISED: RangeRefused: G3-B request for 2025-01-01 > 2024-12-31 — the frozen development window (NOTES 69.1.2); refused before any read
```

#### 69.2.2 FINDING B-1 — the CBOE date format silently emptied cboe_VIX

The first execution of the frozen protocol produced **zero windows for
cboe_VIX** in both maps while every other series formed ~1,732–1,737.
Diagnosis: `tools/g3_exogenous_loader.load_series` parsed dates with
`date.fromisoformat` inside a `ValueError`-swallowing loop; the CBOE
history file uses `MM/DD/YYYY`, so **every CBOE row was silently
dropped** — while the manifest's row counter (the downloader's separate
`_coverage`, which only float-checks the value column) reported the file
fine. A defect in shared data access, not in the frozen statistics; no
statistical object was changed. Fix: `_parse_date` accepts ISO first,
then `MM/DD/YYYY`; pinned by `test_finding_b1_cboe_date_format_parses`
(cboe_VIX must never load empty). After the fix, cboe_VIX parses 9,262
rows (equal to the fred_VIXCLS mirror's row count) and forms 1,736
windows. The measurement was re-run in full; nothing from the first
run's outputs had been used for anything (its only reportable content
was the empty cboe_VIX block). First-run file hashes, superseded and
recorded for audit:

```
sha256 diagnostics.jsonl: da2ec7e016f2eb1bd49abd5577d881c7ca9eef8d79a23da5180fc3a40cd4748a
sha256 sensitivity.jsonl: d38f856a59e26141b90a89641f62a7dc77fbb4122e6ebcd5962522481c2f7116
```

#### 69.2.3 Outputs of record

Full raw per-date series: `research/g3b/out/diagnostics.jsonl`
(PIT-valid map) and `research/g3b/out/sensitivity.jsonl` (publisher-
timing map; its header and every record carry the frozen label
`SENSITIVITY — NOT THE PIT-VALID SERIES`). File hashes as printed:

```
sha256 diagnostics.jsonl: df19e8425c472a991c6cb5568fd090b7ac3fd108943ead45d40527a82bfc68c0
sha256 sensitivity.jsonl: da54b41ef2d9501d6dff3b220522aab75a1c50f96fb7584dbee012090aba3ed7
```

Artifact pins verified by test: window-end dates lie in
[2020-03-30, 2024-12-31]; the last k=+5 window-end is exactly
2024-12-26 (a +5 statistic needs BTC returns through t+5, all inside
the development window); headers state descriptive-only, the §69.1.1
quarantine, no adoption, no trial, no skill claim.

#### 69.2.4 Distributional summaries (§63.2.4.2 percentiles), quoted verbatim

Raw series and their distributions follow, exactly as printed. Per
§69.1.3 there are no thresholds, no narrative labels, and no regime
statistics; `k > 0` shifts the BTC leg later in time and nothing here
is named "leading" or "lagging". The stale-day profile appears as
`stale_days_in_window` percentiles per series; `n_windows_k0` is the
count of fully-formed contemporaneous windows (of 1,738 possible
window-end dates).

```
== PIT-VALID ==
-- ETHUSDT  (k=0 windows: 1737)
   beta                   p5=0.45444742 p25=0.58645081 p50=0.66625336 p75=0.7525617 p95=0.85749817
   rho_pearson_k+0        p5=0.6960273 p25=0.77151142 p50=0.84026414 p75=0.88814338 p95=0.95056367
   rho_pearson_k+1        p5=-0.26925842 p25=-0.15283373 p50=-0.0726957 p75=0.0075236 p95=0.078022
   rho_pearson_k+2        p5=-0.16241743 p25=-0.0712548 p50=0.02172417 p75=0.09938093 p95=0.1880159
   rho_pearson_k+3        p5=-0.18927198 p25=-0.0938263 p50=-0.00355622 p75=0.06469041 p95=0.14193717
   rho_pearson_k+4        p5=-0.12786281 p25=-0.05715324 p50=0.01057104 p75=0.10199855 p95=0.20721619
   rho_pearson_k+5        p5=-0.20872566 p25=-0.07686815 p50=-0.01110944 p75=0.04923946 p95=0.13829987
   rho_pearson_k-1        p5=-0.24430035 p25=-0.12959367 p50=-0.05503666 p75=0.01146568 p95=0.1381054
   rho_pearson_k-2        p5=-0.19552959 p25=-0.05671553 p50=0.04420169 p75=0.12626809 p95=0.23016927
   rho_pearson_k-3        p5=-0.12293374 p25=-0.03861494 p50=0.02129543 p75=0.07347243 p95=0.14482982
   rho_pearson_k-4        p5=-0.1913442 p25=-0.07139538 p50=-0.00392513 p75=0.03721343 p95=0.1841752
   rho_pearson_k-5        p5=-0.09605192 p25=-0.05146355 p50=0.01052438 p75=0.0742623 p95=0.18209994
   rho_spearman_k+0       p5=0.68154422 p25=0.76270321 p50=0.82860435 p75=0.87824011 p95=0.92193604
   rho_spearman_k+1       p5=-0.24550356 p25=-0.17786558 p50=-0.0983249 p75=-0.02940281 p95=0.07281146
   rho_spearman_k+2       p5=-0.17999588 p25=-0.06039429 p50=0.02463679 p75=0.08410915 p95=0.19128617
   rho_spearman_k+3       p5=-0.18846442 p25=-0.0861629 p50=-0.01392765 p75=0.05983866 p95=0.14893855
   rho_spearman_k+4       p5=-0.13609088 p25=-0.06734165 p50=0.00815739 p75=0.09611886 p95=0.17016257
   rho_spearman_k+5       p5=-0.21735029 p25=-0.11340906 p50=-0.03634194 p75=0.03219739 p95=0.14671276
   rho_spearman_k-1       p5=-0.23949459 p25=-0.14133844 p50=-0.06529201 p75=-0.01801457 p95=0.11324855
   rho_spearman_k-2       p5=-0.19766556 p25=-0.06510269 p50=0.02397827 p75=0.11587439 p95=0.17902951
   rho_spearman_k-3       p5=-0.14274355 p25=-0.05901963 p50=0.00961436 p75=0.07206239 p95=0.14331728
   rho_spearman_k-4       p5=-0.20802733 p25=-0.07901387 p50=-0.01844672 p75=0.03602914 p95=0.12887352
   rho_spearman_k-5       p5=-0.15443717 p25=-0.08214594 p50=-0.02676051 p75=0.03851916 p95=0.13967403
   se_beta                p5=0.02552888 p25=0.04010072 p50=0.04618738 p75=0.05380804 p95=0.06459473
   stale_days_in_window   p5=0.0 p25=0.0 p50=0.0 p75=0.0 p95=0.0
-- fred_DGS2  (k=0 windows: 1732)
   beta                   p5=-0.35878741 p25=-0.09892853 p50=-0.02725083 p75=0.04096217 p95=0.29428564
   rho_pearson_k+0        p5=-0.21752313 p25=-0.12309907 p50=-0.04150233 p75=0.08213799 p95=0.18093347
   rho_pearson_k+1        p5=-0.18045227 p25=-0.06627945 p50=0.00562675 p75=0.08443225 p95=0.14647101
   rho_pearson_k+2        p5=-0.31289299 p25=-0.15576445 p50=-0.04794327 p75=0.02932299 p95=0.13211365
   rho_pearson_k+3        p5=-0.19500482 p25=-0.09787081 p50=-0.02312053 p75=0.05296131 p95=0.13756328
   rho_pearson_k+4        p5=-0.12284234 p25=-0.04883347 p50=0.00382031 p75=0.06760436 p95=0.22349053
   rho_pearson_k+5        p5=-0.13951067 p25=-0.06273058 p50=0.01575651 p75=0.10708695 p95=0.20172403
   rho_pearson_k-1        p5=-0.24922372 p25=-0.13161041 p50=-0.03385509 p75=0.03802854 p95=0.1533612
   rho_pearson_k-2        p5=-0.30073421 p25=-0.12755292 p50=-0.0360107 p75=0.0686235 p95=0.14977255
   rho_pearson_k-3        p5=-0.12153608 p25=-0.05718124 p50=0.00667657 p75=0.07284497 p95=0.1534258
   rho_pearson_k-4        p5=-0.18596336 p25=-0.09552933 p50=-0.03023642 p75=0.10683206 p95=0.25350623
   rho_pearson_k-5        p5=-0.12684081 p25=-0.04208068 p50=-0.00173208 p75=0.05233947 p95=0.16480961
   rho_spearman_k+0       p5=-0.25359148 p25=-0.11957429 p50=-0.02578218 p75=0.05439401 p95=0.13837254
   rho_spearman_k+1       p5=-0.16719614 p25=-0.06760582 p50=0.00233759 p75=0.06273802 p95=0.13774971
   rho_spearman_k+2       p5=-0.22151067 p25=-0.11460603 p50=-0.04431658 p75=0.02150888 p95=0.09702845
   rho_spearman_k+3       p5=-0.15194656 p25=-0.07395106 p50=-0.00747283 p75=0.07674552 p95=0.15978973
   rho_spearman_k+4       p5=-0.12148972 p25=-0.05174913 p50=0.00319103 p75=0.071168 p95=0.23109185
   rho_spearman_k+5       p5=-0.14757123 p25=-0.07605066 p50=0.01326441 p75=0.1186489 p95=0.22053183
   rho_spearman_k-1       p5=-0.17953679 p25=-0.09441312 p50=0.0049935 p75=0.0657929 p95=0.13744323
   rho_spearman_k-2       p5=-0.20242466 p25=-0.11388105 p50=-0.0351476 p75=0.05389966 p95=0.21110242
   rho_spearman_k-3       p5=-0.1280644 p25=-0.05365772 p50=0.01906392 p75=0.10723556 p95=0.19671604
   rho_spearman_k-4       p5=-0.19020082 p25=-0.10155295 p50=-0.02102583 p75=0.07902961 p95=0.17296661
   rho_spearman_k-5       p5=-0.11375884 p25=-0.05526786 p50=-0.01636615 p75=0.04871784 p95=0.16295072
   se_beta                p5=0.02738886 p25=0.04597778 p50=0.05874464 p75=0.23929673 p95=0.41976006
   stale_days_in_window   p5=27.0 p25=28.0 p50=28.0 p75=29.0 p95=30.0
-- fred_DGS10  (k=0 windows: 1732)
   beta                   p5=-0.17964556 p25=-0.08897435 p50=-0.02340967 p75=0.04432119 p95=0.16795176
   rho_pearson_k+0        p5=-0.21024356 p25=-0.13603497 p50=-0.03918367 p75=0.07499978 p95=0.16389568
   rho_pearson_k+1        p5=-0.19379041 p25=-0.06736526 p50=-0.0039507 p75=0.07826371 p95=0.18436585
   rho_pearson_k+2        p5=-0.23949319 p25=-0.12591388 p50=-0.05660638 p75=0.01190321 p95=0.08200911
   rho_pearson_k+3        p5=-0.17139053 p25=-0.09134033 p50=-0.02499475 p75=0.06323168 p95=0.16460469
   rho_pearson_k+4        p5=-0.12558233 p25=-0.05515353 p50=0.00054317 p75=0.07356878 p95=0.18258175
   rho_pearson_k+5        p5=-0.1904298 p25=-0.07552884 p50=0.02200233 p75=0.07984012 p95=0.16048892
   rho_pearson_k-1        p5=-0.20642097 p25=-0.12075049 p50=-0.05478876 p75=0.01284914 p95=0.18582392
   rho_pearson_k-2        p5=-0.23709213 p25=-0.10096974 p50=-0.01926088 p75=0.05670866 p95=0.13845339
   rho_pearson_k-3        p5=-0.10713579 p25=-0.02623476 p50=0.0455212 p75=0.08534801 p95=0.15552566
   rho_pearson_k-4        p5=-0.11354408 p25=-0.05894414 p50=0.00946947 p75=0.09331755 p95=0.2419484
   rho_pearson_k-5        p5=-0.226717 p25=-0.0773482 p50=-0.02414578 p75=0.03187154 p95=0.17434835
   rho_spearman_k+0       p5=-0.19795741 p25=-0.08863278 p50=-0.00923226 p75=0.07753623 p95=0.14512835
   rho_spearman_k+1       p5=-0.19704539 p25=-0.09280661 p50=0.00591137 p75=0.07845289 p95=0.15043896
   rho_spearman_k+2       p5=-0.21849793 p25=-0.1258493 p50=-0.06265769 p75=0.00204895 p95=0.07555797
   rho_spearman_k+3       p5=-0.17441767 p25=-0.10088095 p50=-0.02890252 p75=0.06116879 p95=0.19864278
   rho_spearman_k+4       p5=-0.14365005 p25=-0.05001279 p50=0.01636427 p75=0.08752232 p95=0.19917232
   rho_spearman_k+5       p5=-0.17369233 p25=-0.05853149 p50=0.01697494 p75=0.11361022 p95=0.18899423
   rho_spearman_k-1       p5=-0.16745104 p25=-0.08936428 p50=-0.03616475 p75=0.02796318 p95=0.11767857
   rho_spearman_k-2       p5=-0.18725733 p25=-0.10381903 p50=-0.02702815 p75=0.08181478 p95=0.18526275
   rho_spearman_k-3       p5=-0.12145264 p25=-0.04269456 p50=0.04176169 p75=0.0989069 p95=0.17581048
   rho_spearman_k-4       p5=-0.1303882 p25=-0.06292787 p50=0.00237379 p75=0.07695352 p95=0.16917936
   rho_spearman_k-5       p5=-0.13538818 p25=-0.08218523 p50=-0.0263061 p75=0.03373487 p95=0.14875869
   se_beta                p5=0.03472414 p25=0.04595369 p50=0.06441441 p75=0.09956787 p95=0.15647516
   stale_days_in_window   p5=27.0 p25=28.0 p50=28.0 p75=29.0 p95=30.0
-- fred_DTWEXBGS  (k=0 windows: 1732)
   beta                   p5=-2.15511667 p25=-1.27062764 p50=-0.24835562 p75=0.81358398 p95=3.38219582
   rho_pearson_k+0        p5=-0.18460049 p25=-0.10572438 p50=-0.02085525 p75=0.08243165 p95=0.22566149
   rho_pearson_k+1        p5=-0.12545837 p25=-0.05657729 p50=0.00346792 p75=0.10066067 p95=0.21089245
   rho_pearson_k+2        p5=-0.20251174 p25=-0.09602534 p50=-0.03465237 p75=0.04604388 p95=0.10220075
   rho_pearson_k+3        p5=-0.14037463 p25=-0.0571844 p50=0.01260943 p75=0.06008399 p95=0.12527275
   rho_pearson_k+4        p5=-0.14064898 p25=-0.05940036 p50=-0.00618738 p75=0.05214185 p95=0.14815236
   rho_pearson_k+5        p5=-0.13429246 p25=-0.0428321 p50=0.01304152 p75=0.06315907 p95=0.1534465
   rho_pearson_k-1        p5=-0.14591323 p25=-0.06181228 p50=0.00598784 p75=0.09353914 p95=0.20535198
   rho_pearson_k-2        p5=-0.35361681 p25=-0.18873147 p50=-0.0918041 p75=0.00366145 p95=0.12702673
   rho_pearson_k-3        p5=-0.13125901 p25=-0.08249463 p50=-0.03784488 p75=0.02926186 p95=0.23362774
   rho_pearson_k-4        p5=-0.22301527 p25=-0.12256806 p50=-0.06763918 p75=0.00735847 p95=0.09732853
   rho_pearson_k-5        p5=-0.25927013 p25=-0.13293071 p50=-0.03403591 p75=0.03117374 p95=0.12046389
   rho_spearman_k+0       p5=-0.20149817 p25=-0.11424475 p50=-0.05243573 p75=0.01490254 p95=0.14639767
   rho_spearman_k+1       p5=-0.1300256 p25=-0.06574669 p50=0.02254731 p75=0.12616607 p95=0.24477211
   rho_spearman_k+2       p5=-0.17826106 p25=-0.05731175 p50=0.00080945 p75=0.05432829 p95=0.11849008
   rho_spearman_k+3       p5=-0.10142774 p25=-0.04056198 p50=0.01888936 p75=0.07779812 p95=0.15703987
   rho_spearman_k+4       p5=-0.18865506 p25=-0.09998144 p50=-0.0280322 p75=0.05397886 p95=0.14311046
   rho_spearman_k+5       p5=-0.16022352 p25=-0.05806572 p50=0.01907457 p75=0.06767985 p95=0.1572072
   rho_spearman_k-1       p5=-0.11738849 p25=-0.02770062 p50=0.05055581 p75=0.10620177 p95=0.25737298
   rho_spearman_k-2       p5=-0.26292501 p25=-0.17558645 p50=-0.10245862 p75=-0.01570291 p95=0.08890872
   rho_spearman_k-3       p5=-0.14772527 p25=-0.0901718 p50=-0.0500308 p75=0.00644417 p95=0.20077164
   rho_spearman_k-4       p5=-0.2331807 p25=-0.14346305 p50=-0.06693261 p75=0.0286808 p95=0.11859009
   rho_spearman_k-5       p5=-0.23709996 p25=-0.13126376 p50=-0.04098236 p75=0.02936619 p95=0.13790447
   se_beta                p5=0.77035311 p25=0.97344176 p50=1.20108492 p75=1.6580334 p95=2.12911653
   stale_days_in_window   p5=27.0 p25=28.0 p50=28.0 p75=29.0 p95=31.0
-- cboe_VIX  (k=0 windows: 1736)
   beta                   p5=-0.34773442 p25=-0.23349053 p50=-0.15369278 p75=-0.10848476 p95=-0.04015997
   rho_pearson_k+0        p5=-0.51709675 p25=-0.431327 p50=-0.33097381 p75=-0.20053723 p95=-0.05709563
   rho_pearson_k+1        p5=-0.11843244 p25=-0.05771972 p50=0.00831092 p75=0.0775297 p95=0.16388401
   rho_pearson_k+2        p5=-0.16167501 p25=-0.05875747 p50=0.02103122 p75=0.11786861 p95=0.21666375
   rho_pearson_k+3        p5=-0.1883269 p25=-0.07986157 p50=0.00746207 p75=0.09824414 p95=0.16636341
   rho_pearson_k+4        p5=-0.17215217 p25=-0.08422543 p50=-0.01369198 p75=0.07325046 p95=0.21845221
   rho_pearson_k+5        p5=-0.19290735 p25=-0.09765556 p50=-0.01759463 p75=0.0826004 p95=0.17005872
   rho_pearson_k-1        p5=-0.13709901 p25=-0.06907528 p50=0.01207488 p75=0.09035042 p95=0.18322482
   rho_pearson_k-2        p5=-0.11818579 p25=-0.06094233 p50=-0.016801 p75=0.06449253 p95=0.15957973
   rho_pearson_k-3        p5=-0.1580477 p25=-0.06510412 p50=-0.01447838 p75=0.0699846 p95=0.16742083
   rho_pearson_k-4        p5=-0.25435883 p25=-0.04553741 p50=0.02225822 p75=0.12481021 p95=0.24264814
   rho_pearson_k-5        p5=-0.10296324 p25=-0.01028464 p50=0.05181209 p75=0.11815645 p95=0.20079116
   rho_spearman_k+0       p5=-0.43634018 p25=-0.32975334 p50=-0.23380861 p75=-0.14623063 p95=-0.00710973
   rho_spearman_k+1       p5=-0.13638805 p25=-0.0485748 p50=0.01738255 p75=0.08965601 p95=0.18433078
   rho_spearman_k+2       p5=-0.15001006 p25=-0.05279022 p50=0.04095101 p75=0.12979134 p95=0.21922987
   rho_spearman_k+3       p5=-0.12294954 p25=-0.05332485 p50=0.01258734 p75=0.08130291 p95=0.17646326
   rho_spearman_k+4       p5=-0.17062833 p25=-0.08523199 p50=-0.00934232 p75=0.09772904 p95=0.16841857
   rho_spearman_k+5       p5=-0.19281382 p25=-0.07965774 p50=-0.0111654 p75=0.06312345 p95=0.18047005
   rho_spearman_k-1       p5=-0.20921364 p25=-0.06711575 p50=0.0130269 p75=0.08435937 p95=0.19336733
   rho_spearman_k-2       p5=-0.18068121 p25=-0.11281938 p50=-0.04038646 p75=0.02674196 p95=0.14300734
   rho_spearman_k-3       p5=-0.1578149 p25=-0.05533079 p50=0.01744619 p75=0.06312618 p95=0.16155522
   rho_spearman_k-4       p5=-0.15915403 p25=-0.03373339 p50=0.03246019 p75=0.11186139 p95=0.19616041
   rho_spearman_k-5       p5=-0.13525537 p25=-0.01371651 p50=0.0683262 p75=0.14441502 p95=0.21175883
   se_beta                p5=0.03029903 p25=0.04593537 p50=0.05502968 p75=0.06853929 p95=0.08235928
   stale_days_in_window   p5=25.0 p25=26.0 p50=27.0 p75=28.0 p95=29.0
-- fred_VIXCLS  (k=0 windows: 1735)
   beta                   p5=-0.09115732 p25=-0.03804249 p50=0.01092941 p75=0.0538721 p95=0.09983255
   rho_pearson_k+0        p5=-0.14512136 p25=-0.06183025 p50=0.01743699 p75=0.11817357 p95=0.19363582
   rho_pearson_k+1        p5=-0.17463368 p25=-0.08056993 p50=0.01095106 p75=0.08390241 p95=0.17711737
   rho_pearson_k+2        p5=-0.19756705 p25=-0.1062411 p50=-0.00631422 p75=0.07386869 p95=0.22228954
   rho_pearson_k+3        p5=-0.16560391 p25=-0.08637278 p50=-0.00284465 p75=0.11796464 p95=0.29947776
   rho_pearson_k+4        p5=-0.17208928 p25=-0.08206314 p50=-0.01259051 p75=0.05394666 p95=0.16444033
   rho_pearson_k+5        p5=-0.14951012 p25=-0.06402803 p50=0.02160761 p75=0.08430422 p95=0.15642754
   rho_pearson_k-1        p5=-0.49600962 p25=-0.34625081 p50=-0.24765175 p75=-0.13594099 p95=-0.01195007
   rho_pearson_k-2        p5=-0.16153728 p25=-0.0514632 p50=0.00466304 p75=0.05323429 p95=0.15428799
   rho_pearson_k-3        p5=-0.19443745 p25=-0.12480868 p50=-0.05405451 p75=0.02103764 p95=0.15785816
   rho_pearson_k-4        p5=-0.18896232 p25=-0.10966066 p50=-0.03030693 p75=0.05986148 p95=0.2440739
   rho_pearson_k-5        p5=-0.2696141 p25=-0.07154889 p50=0.02531594 p75=0.08080185 p95=0.26508295
   rho_spearman_k+0       p5=-0.15312291 p25=-0.04259142 p50=0.05286189 p75=0.15954826 p95=0.23803942
   rho_spearman_k+1       p5=-0.17729293 p25=-0.08524943 p50=0.01876337 p75=0.10502168 p95=0.17116617
   rho_spearman_k+2       p5=-0.15539824 p25=-0.0764267 p50=-0.00995669 p75=0.06216431 p95=0.14923215
   rho_spearman_k+3       p5=-0.12817645 p25=-0.05316116 p50=0.01897397 p75=0.10317349 p95=0.24781747
   rho_spearman_k+4       p5=-0.16863908 p25=-0.08378423 p50=-0.01804518 p75=0.0700182 p95=0.1620994
   rho_spearman_k+5       p5=-0.21789842 p25=-0.05961145 p50=0.02609632 p75=0.08874497 p95=0.170222
   rho_spearman_k-1       p5=-0.35944121 p25=-0.25576346 p50=-0.19527924 p75=-0.11061924 p95=0.04630139
   rho_spearman_k-2       p5=-0.21151496 p25=-0.05871787 p50=0.01379091 p75=0.07876346 p95=0.15429165
   rho_spearman_k-3       p5=-0.23540438 p25=-0.11953507 p50=-0.05149755 p75=0.02156281 p95=0.09252928
   rho_spearman_k-4       p5=-0.1950103 p25=-0.07671779 p50=-0.00053684 p75=0.05555646 p95=0.12298655
   rho_spearman_k-5       p5=-0.17116414 p25=-0.07221606 p50=-0.00405565 p75=0.06929114 p95=0.18234525
   se_beta                p5=0.03296703 p25=0.04902973 p50=0.05925214 p75=0.07193738 p95=0.08676108
   stale_days_in_window   p5=27.0 p25=28.0 p50=28.0 p75=29.0 p95=30.0
-- fred_SP500  (k=0 windows: 1735)
   beta                   p5=-0.68764631 p25=-0.39524367 p50=-0.20402417 p75=0.17346361 p95=0.87806717
   rho_pearson_k+0        p5=-0.25067963 p25=-0.13151153 p50=-0.0635079 p75=0.04227846 p95=0.15955798
   rho_pearson_k+1        p5=-0.1909948 p25=-0.09251222 p50=-0.01221551 p75=0.06271934 p95=0.14647204
   rho_pearson_k+2        p5=-0.1621091 p25=-0.08518669 p50=-0.03243919 p75=0.04329189 p95=0.13182842
   rho_pearson_k+3        p5=-0.218706 p25=-0.10320241 p50=0.00329916 p75=0.09038669 p95=0.18423162
   rho_pearson_k+4        p5=-0.11673646 p25=-0.03437179 p50=0.03137268 p75=0.10283548 p95=0.20045629
   rho_pearson_k+5        p5=-0.15446167 p25=-0.06975227 p50=-0.0010768 p75=0.05497601 p95=0.14170224
   rho_pearson_k-1        p5=-0.00093365 p25=0.15563924 p50=0.23926138 p75=0.38353247 p95=0.51493052
   rho_pearson_k-2        p5=-0.13379065 p25=-0.06637319 p50=0.01115963 p75=0.08487839 p95=0.18755421
   rho_pearson_k-3        p5=-0.24556869 p25=-0.0077529 p50=0.0675427 p75=0.14413973 p95=0.25136112
   rho_pearson_k-4        p5=-0.20423535 p25=-0.07318616 p50=0.02532653 p75=0.10558506 p95=0.20655721
   rho_pearson_k-5        p5=-0.26295146 p25=-0.06209179 p50=-0.005679 p75=0.08426809 p95=0.26945775
   rho_spearman_k+0       p5=-0.27348169 p25=-0.17044725 p50=-0.09779007 p75=0.03015149 p95=0.17720647
   rho_spearman_k+1       p5=-0.19438134 p25=-0.09739019 p50=-0.00445046 p75=0.10043325 p95=0.19618309
   rho_spearman_k+2       p5=-0.1573824 p25=-0.08470124 p50=-0.03727724 p75=0.032814 p95=0.12947313
   rho_spearman_k+3       p5=-0.24221278 p25=-0.09094642 p50=0.01044766 p75=0.07806763 p95=0.15700446
   rho_spearman_k+4       p5=-0.11635991 p25=-0.02251679 p50=0.04346197 p75=0.11217503 p95=0.23665131
   rho_spearman_k+5       p5=-0.18699524 p25=-0.08402871 p50=-0.03087793 p75=0.01958973 p95=0.14879884
   rho_spearman_k-1       p5=-0.01772471 p25=0.14743316 p50=0.22883712 p75=0.3054859 p95=0.4494933
   rho_spearman_k-2       p5=-0.18646188 p25=-0.1068524 p50=0.01812671 p75=0.11520728 p95=0.22671583
   rho_spearman_k-3       p5=-0.13836193 p25=-0.01765421 p50=0.06376325 p75=0.16407741 p95=0.27247759
   rho_spearman_k-4       p5=-0.13296212 p25=-0.06281094 p50=0.01181369 p75=0.08190373 p95=0.20825486
   rho_spearman_k-5       p5=-0.15318502 p25=-0.05685017 p50=0.00754368 p75=0.0714749 p95=0.18498872
   se_beta                p5=0.22485106 p25=0.28414507 p50=0.35873996 p75=0.51475225 p95=0.80761506
   stale_days_in_window   p5=27.0 p25=28.0 p50=28.0 p75=29.0 p95=30.0
-- fred_NASDAQ100  (k=0 windows: 1735)
   beta                   p5=-0.44724679 p25=-0.26690663 p50=-0.15267201 p75=0.02878438 p95=0.4224568
   rho_pearson_k+0        p5=-0.20824521 p25=-0.11359283 p50=-0.05742275 p75=0.00771267 p95=0.09391964
   rho_pearson_k+1        p5=-0.1927611 p25=-0.10277334 p50=-0.03161333 p75=0.04167259 p95=0.14411767
   rho_pearson_k+2        p5=-0.18492519 p25=-0.10618098 p50=-0.04061695 p75=0.0533799 p95=0.12041591
   rho_pearson_k+3        p5=-0.26258061 p25=-0.10370582 p50=0.02413544 p75=0.11714128 p95=0.19854801
   rho_pearson_k+4        p5=-0.12084524 p25=-0.03520637 p50=0.02952194 p75=0.10311367 p95=0.16765859
   rho_pearson_k+5        p5=-0.15290745 p25=-0.06825698 p50=-0.01667128 p75=0.05009444 p95=0.15560302
   rho_pearson_k-1        p5=0.00439548 p25=0.15361553 p50=0.25973677 p75=0.35557842 p95=0.52372269
   rho_pearson_k-2        p5=-0.16292716 p25=-0.07498581 p50=0.00236849 p75=0.06422846 p95=0.18282465
   rho_pearson_k-3        p5=-0.2733352 p25=-0.00105215 p50=0.07462998 p75=0.1331677 p95=0.24100534
   rho_pearson_k-4        p5=-0.22322199 p25=-0.07281043 p50=0.02631389 p75=0.10919793 p95=0.19205795
   rho_pearson_k-5        p5=-0.29227178 p25=-0.07340848 p50=-0.01905176 p75=0.04844796 p95=0.23194195
   rho_spearman_k+0       p5=-0.24753078 p25=-0.16545798 p50=-0.07312757 p75=0.04068064 p95=0.10905219
   rho_spearman_k+1       p5=-0.18004831 p25=-0.10079414 p50=-0.02691786 p75=0.0593331 p95=0.14434122
   rho_spearman_k+2       p5=-0.17755195 p25=-0.10238175 p50=-0.02577667 p75=0.03773812 p95=0.12349319
   rho_spearman_k+3       p5=-0.23245473 p25=-0.09722415 p50=0.01493665 p75=0.09262566 p95=0.1773426
   rho_spearman_k+4       p5=-0.11279398 p25=-0.02147328 p50=0.03914523 p75=0.0939178 p95=0.19351101
   rho_spearman_k+5       p5=-0.18150296 p25=-0.07946943 p50=-0.02733857 p75=0.01023475 p95=0.11758754
   rho_spearman_k-1       p5=-0.01617188 p25=0.13092582 p50=0.22131481 p75=0.29889354 p95=0.44454688
   rho_spearman_k-2       p5=-0.19424409 p25=-0.10987564 p50=0.00041791 p75=0.10707796 p95=0.20636539
   rho_spearman_k-3       p5=-0.1783272 p25=0.0051695 p50=0.06460133 p75=0.12595904 p95=0.24838763
   rho_spearman_k-4       p5=-0.12935365 p25=-0.04850225 p50=0.02302664 p75=0.09156327 p95=0.15554345
   rho_spearman_k-5       p5=-0.15736401 p25=-0.08401449 p50=-0.00827025 p75=0.04509043 p95=0.1779324
   se_beta                p5=0.17823311 p25=0.22361871 p50=0.25264537 p75=0.36262151 p95=0.63025084
   stale_days_in_window   p5=27.0 p25=28.0 p50=28.0 p75=29.0 p95=30.0

== SENSITIVITY — NOT THE PIT-VALID SERIES ==
-- ETHUSDT  (k=0 windows: 1737)
   beta                   p5=0.45444742 p25=0.58645081 p50=0.66625336 p75=0.7525617 p95=0.85749817
   rho_pearson_k+0        p5=0.6960273 p25=0.77151142 p50=0.84026414 p75=0.88814338 p95=0.95056367
   rho_pearson_k+1        p5=-0.26925842 p25=-0.15283373 p50=-0.0726957 p75=0.0075236 p95=0.078022
   rho_pearson_k+2        p5=-0.16241743 p25=-0.0712548 p50=0.02172417 p75=0.09938093 p95=0.1880159
   rho_pearson_k+3        p5=-0.18927198 p25=-0.0938263 p50=-0.00355622 p75=0.06469041 p95=0.14193717
   rho_pearson_k+4        p5=-0.12786281 p25=-0.05715324 p50=0.01057104 p75=0.10199855 p95=0.20721619
   rho_pearson_k+5        p5=-0.20872566 p25=-0.07686815 p50=-0.01110944 p75=0.04923946 p95=0.13829987
   rho_pearson_k-1        p5=-0.24430035 p25=-0.12959367 p50=-0.05503666 p75=0.01146568 p95=0.1381054
   rho_pearson_k-2        p5=-0.19552959 p25=-0.05671553 p50=0.04420169 p75=0.12626809 p95=0.23016927
   rho_pearson_k-3        p5=-0.12293374 p25=-0.03861494 p50=0.02129543 p75=0.07347243 p95=0.14482982
   rho_pearson_k-4        p5=-0.1913442 p25=-0.07139538 p50=-0.00392513 p75=0.03721343 p95=0.1841752
   rho_pearson_k-5        p5=-0.09605192 p25=-0.05146355 p50=0.01052438 p75=0.0742623 p95=0.18209994
   rho_spearman_k+0       p5=0.68154422 p25=0.76270321 p50=0.82860435 p75=0.87824011 p95=0.92193604
   rho_spearman_k+1       p5=-0.24550356 p25=-0.17786558 p50=-0.0983249 p75=-0.02940281 p95=0.07281146
   rho_spearman_k+2       p5=-0.17999588 p25=-0.06039429 p50=0.02463679 p75=0.08410915 p95=0.19128617
   rho_spearman_k+3       p5=-0.18846442 p25=-0.0861629 p50=-0.01392765 p75=0.05983866 p95=0.14893855
   rho_spearman_k+4       p5=-0.13609088 p25=-0.06734165 p50=0.00815739 p75=0.09611886 p95=0.17016257
   rho_spearman_k+5       p5=-0.21735029 p25=-0.11340906 p50=-0.03634194 p75=0.03219739 p95=0.14671276
   rho_spearman_k-1       p5=-0.23949459 p25=-0.14133844 p50=-0.06529201 p75=-0.01801457 p95=0.11324855
   rho_spearman_k-2       p5=-0.19766556 p25=-0.06510269 p50=0.02397827 p75=0.11587439 p95=0.17902951
   rho_spearman_k-3       p5=-0.14274355 p25=-0.05901963 p50=0.00961436 p75=0.07206239 p95=0.14331728
   rho_spearman_k-4       p5=-0.20802733 p25=-0.07901387 p50=-0.01844672 p75=0.03602914 p95=0.12887352
   rho_spearman_k-5       p5=-0.15443717 p25=-0.08214594 p50=-0.02676051 p75=0.03851916 p95=0.13967403
   se_beta                p5=0.02552888 p25=0.04010072 p50=0.04618738 p75=0.05380804 p95=0.06459473
   stale_days_in_window   p5=0.0 p25=0.0 p50=0.0 p75=0.0 p95=0.0
-- fred_DGS2  (k=0 windows: 1735)
   beta                   p5=-0.28052297 p25=-0.09275246 p50=-0.03242114 p75=0.00141703 p95=0.17218559
   rho_pearson_k+0        p5=-0.21486528 p25=-0.10701642 p50=-0.05275959 p75=0.00262494 p95=0.24220853
   rho_pearson_k+1        p5=-0.21898396 p25=-0.12302343 p50=-0.03854509 p75=0.05167354 p95=0.18227553
   rho_pearson_k+2        p5=-0.12215314 p25=-0.0455692 p50=0.01489787 p75=0.08958837 p95=0.17276739
   rho_pearson_k+3        p5=-0.31766186 p25=-0.1336133 p50=-0.02403605 p75=0.02781896 p95=0.14095465
   rho_pearson_k+4        p5=-0.19783179 p25=-0.09032942 p50=-0.01154959 p75=0.04344801 p95=0.10676528
   rho_pearson_k+5        p5=-0.14033117 p25=-0.08331102 p50=-0.03066894 p75=0.06547109 p95=0.18123827
   rho_pearson_k-1        p5=-0.26604378 p25=-0.11962547 p50=-0.04833614 p75=0.03926082 p95=0.14746498
   rho_pearson_k-2        p5=-0.0930851 p25=-0.03524392 p50=0.01225643 p75=0.0603588 p95=0.12716172
   rho_pearson_k-3        p5=-0.13333347 p25=-0.07261824 p50=-0.0051967 p75=0.09620038 p95=0.25495976
   rho_pearson_k-4        p5=-0.12694922 p25=-0.03100875 p50=0.01352967 p75=0.06346852 p95=0.15990168
   rho_pearson_k-5        p5=-0.14008229 p25=-0.06340146 p50=0.02552581 p75=0.15078657 p95=0.28964938
   rho_spearman_k+0       p5=-0.16812583 p25=-0.07029891 p50=-0.02218092 p75=0.05790737 p95=0.19509154
   rho_spearman_k+1       p5=-0.22130044 p25=-0.12073062 p50=-0.04373637 p75=0.05085977 p95=0.15323686
   rho_spearman_k+2       p5=-0.14747348 p25=-0.05601364 p50=0.00664819 p75=0.07346298 p95=0.13967611
   rho_spearman_k+3       p5=-0.20159817 p25=-0.12332354 p50=-0.05379781 p75=0.03326023 p95=0.14157798
   rho_spearman_k+4       p5=-0.13723951 p25=-0.06664654 p50=0.0022522 p75=0.07615775 p95=0.14247685
   rho_spearman_k+5       p5=-0.17465213 p25=-0.08258007 p50=-0.01756619 p75=0.05704763 p95=0.20223071
   rho_spearman_k-1       p5=-0.19038431 p25=-0.10588882 p50=-0.05187001 p75=0.02507138 p95=0.13751497
   rho_spearman_k-2       p5=-0.13640378 p25=-0.03517087 p50=0.02725888 p75=0.09224697 p95=0.17984318
   rho_spearman_k-3       p5=-0.13251166 p25=-0.06212016 p50=0.00113357 p75=0.07838162 p95=0.19716681
   rho_spearman_k-4       p5=-0.11006292 p25=-0.04902437 p50=0.00580926 p75=0.06179112 p95=0.15874473
   rho_spearman_k-5       p5=-0.16344662 p25=-0.07467317 p50=0.02013006 p75=0.1113239 p95=0.20915006
   se_beta                p5=0.02733643 p25=0.04584376 p50=0.05951929 p75=0.23999137 p95=0.41784758
   stale_days_in_window   p5=27.0 p25=28.0 p50=28.0 p75=29.0 p95=30.0
-- fred_DGS10  (k=0 windows: 1735)
   beta                   p5=-0.13920722 p25=-0.07786697 p50=-0.02672475 p75=0.02036157 p95=0.23231316
   rho_pearson_k+0        p5=-0.18900369 p25=-0.11455234 p50=-0.05200419 p75=0.03289389 p95=0.23223598
   rho_pearson_k+1        p5=-0.19507288 p25=-0.11195313 p50=-0.03122035 p75=0.06760527 p95=0.16904006
   rho_pearson_k+2        p5=-0.17696011 p25=-0.03734835 p50=0.01906931 p75=0.07614926 p95=0.21686333
   rho_pearson_k+3        p5=-0.22399879 p25=-0.14649898 p50=-0.05012249 p75=0.01590962 p95=0.11916523
   rho_pearson_k+4        p5=-0.13950033 p25=-0.08347759 p50=-0.01546861 p75=0.03866039 p95=0.13383157
   rho_pearson_k+5        p5=-0.16235056 p25=-0.06179488 p50=0.00570719 p75=0.06286421 p95=0.1598586
   rho_pearson_k-1        p5=-0.24730785 p25=-0.12601987 p50=-0.01702761 p75=0.04173849 p95=0.20648204
   rho_pearson_k-2        p5=-0.11658576 p25=-0.04202816 p50=0.02263314 p75=0.0734952 p95=0.17107475
   rho_pearson_k-3        p5=-0.11129888 p25=-0.03918212 p50=0.00099087 p75=0.07817243 p95=0.23819278
   rho_pearson_k-4        p5=-0.19205263 p25=-0.04649298 p50=-0.00134676 p75=0.05052864 p95=0.14518816
   rho_pearson_k-5        p5=-0.17836904 p25=-0.0677201 p50=0.03684511 p75=0.14050134 p95=0.26018471
   rho_spearman_k+0       p5=-0.16396333 p25=-0.09194957 p50=-0.00582269 p75=0.06743828 p95=0.18811191
   rho_spearman_k+1       p5=-0.18841247 p25=-0.06843688 p50=-0.00975829 p75=0.07514591 p95=0.13511287
   rho_spearman_k+2       p5=-0.18362575 p25=-0.07837204 p50=-0.01219771 p75=0.04862441 p95=0.12855386
   rho_spearman_k+3       p5=-0.21240113 p25=-0.13821092 p50=-0.07469654 p75=-0.0033267 p95=0.13908255
   rho_spearman_k+4       p5=-0.15341821 p25=-0.07100669 p50=-0.01885649 p75=0.04423422 p95=0.13423314
   rho_spearman_k+5       p5=-0.18069692 p25=-0.05830249 p50=0.01532831 p75=0.08552892 p95=0.1989997
   rho_spearman_k-1       p5=-0.2246325 p25=-0.08955406 p50=-0.00540084 p75=0.07479376 p95=0.20360259
   rho_spearman_k-2       p5=-0.15816945 p25=-0.05942158 p50=0.02138285 p75=0.08873594 p95=0.18334515
   rho_spearman_k-3       p5=-0.10735709 p25=-0.04407063 p50=0.01055338 p75=0.06789453 p95=0.15964129
   rho_spearman_k-4       p5=-0.15583117 p25=-0.03404951 p50=0.02043965 p75=0.08083499 p95=0.15260246
   rho_spearman_k-5       p5=-0.1996347 p25=-0.06453402 p50=0.00691201 p75=0.09096561 p95=0.21629128
   se_beta                p5=0.03468592 p25=0.04589651 p50=0.06346084 p75=0.09948398 p95=0.15601842
   stale_days_in_window   p5=27.0 p25=28.0 p50=28.0 p75=29.0 p95=30.0
-- fred_DTWEXBGS  (k=0 windows: 1735)
   beta                   p5=-2.48691432 p25=-0.76641043 p50=0.23556735 p75=1.1122492 p95=3.15659884
   rho_pearson_k+0        p5=-0.13976048 p25=-0.07088982 p50=0.0213657 p75=0.09485592 p95=0.2383871
   rho_pearson_k+1        p5=-0.12278951 p25=-0.06453579 p50=-0.00256403 p75=0.0619198 p95=0.14037171
   rho_pearson_k+2        p5=-0.14713801 p25=-0.06846267 p50=0.01171604 p75=0.11026386 p95=0.21176054
   rho_pearson_k+3        p5=-0.20390709 p25=-0.12705706 p50=-0.04008029 p75=0.04524815 p95=0.1319508
   rho_pearson_k+4        p5=-0.13347263 p25=-0.06447941 p50=-0.00325121 p75=0.05936714 p95=0.12851312
   rho_pearson_k+5        p5=-0.15212664 p25=-0.06621936 p50=0.00287695 p75=0.06565304 p95=0.15675825
   rho_pearson_k-1        p5=-0.37477978 p25=-0.22403134 p50=-0.1252489 p75=-0.04581412 p95=0.10679009
   rho_pearson_k-2        p5=-0.17997108 p25=-0.10010224 p50=-0.04925333 p75=0.00254227 p95=0.09593446
   rho_pearson_k-3        p5=-0.18509175 p25=-0.10274172 p50=-0.02133493 p75=0.03275402 p95=0.12955035
   rho_pearson_k-4        p5=-0.20178387 p25=-0.11128158 p50=-0.03174454 p75=0.04245564 p95=0.18379045
   rho_pearson_k-5        p5=-0.13798399 p25=-0.0731512 p50=-0.00765979 p75=0.05248474 p95=0.11095788
   rho_spearman_k+0       p5=-0.11939559 p25=-0.02859203 p50=0.05285679 p75=0.13598258 p95=0.25519236
   rho_spearman_k+1       p5=-0.16867246 p25=-0.08214662 p50=-0.03542383 p75=0.02905968 p95=0.1017065
   rho_spearman_k+2       p5=-0.17145807 p25=-0.05106153 p50=0.01912339 p75=0.12809183 p95=0.25918802
   rho_spearman_k+3       p5=-0.1886968 p25=-0.07550869 p50=-0.02700989 p75=0.03225538 p95=0.10920653
   rho_spearman_k+4       p5=-0.11673317 p25=-0.03382748 p50=0.01930944 p75=0.07574591 p95=0.16840787
   rho_spearman_k+5       p5=-0.21049935 p25=-0.10065125 p50=-0.01771765 p75=0.06140145 p95=0.15470556
   rho_spearman_k-1       p5=-0.31530113 p25=-0.21097072 p50=-0.12932528 p75=-0.0557005 p95=0.06074744
   rho_spearman_k-2       p5=-0.19538057 p25=-0.1225382 p50=-0.04676076 p75=0.01103398 p95=0.10993776
   rho_spearman_k-3       p5=-0.2139025 p25=-0.12547845 p50=-0.03811853 p75=0.05374715 p95=0.1447486
   rho_spearman_k-4       p5=-0.23721029 p25=-0.10699297 p50=-0.03018955 p75=0.03771839 p95=0.123762
   rho_spearman_k-5       p5=-0.1425492 p25=-0.07849325 p50=-0.01847982 p75=0.05874826 p95=0.13741611
   se_beta                p5=0.7636088 p25=0.97740592 p50=1.21359463 p75=1.6447562 p95=2.12918401
   stale_days_in_window   p5=27.0 p25=28.0 p50=28.0 p75=29.0 p95=31.0
-- cboe_VIX  (k=0 windows: 1736)
   beta                   p5=-0.34773442 p25=-0.23349053 p50=-0.15369278 p75=-0.10848476 p95=-0.04015997
   rho_pearson_k+0        p5=-0.51709675 p25=-0.431327 p50=-0.33097381 p75=-0.20053723 p95=-0.05709563
   rho_pearson_k+1        p5=-0.11843244 p25=-0.05771972 p50=0.00831092 p75=0.0775297 p95=0.16388401
   rho_pearson_k+2        p5=-0.16167501 p25=-0.05875747 p50=0.02103122 p75=0.11786861 p95=0.21666375
   rho_pearson_k+3        p5=-0.1883269 p25=-0.07986157 p50=0.00746207 p75=0.09824414 p95=0.16636341
   rho_pearson_k+4        p5=-0.17215217 p25=-0.08422543 p50=-0.01369198 p75=0.07325046 p95=0.21845221
   rho_pearson_k+5        p5=-0.19290735 p25=-0.09765556 p50=-0.01759463 p75=0.0826004 p95=0.17005872
   rho_pearson_k-1        p5=-0.13709901 p25=-0.06907528 p50=0.01207488 p75=0.09035042 p95=0.18322482
   rho_pearson_k-2        p5=-0.11818579 p25=-0.06094233 p50=-0.016801 p75=0.06449253 p95=0.15957973
   rho_pearson_k-3        p5=-0.1580477 p25=-0.06510412 p50=-0.01447838 p75=0.0699846 p95=0.16742083
   rho_pearson_k-4        p5=-0.25435883 p25=-0.04553741 p50=0.02225822 p75=0.12481021 p95=0.24264814
   rho_pearson_k-5        p5=-0.10296324 p25=-0.01028464 p50=0.05181209 p75=0.11815645 p95=0.20079116
   rho_spearman_k+0       p5=-0.43634018 p25=-0.32975334 p50=-0.23380861 p75=-0.14623063 p95=-0.00710973
   rho_spearman_k+1       p5=-0.13638805 p25=-0.0485748 p50=0.01738255 p75=0.08965601 p95=0.18433078
   rho_spearman_k+2       p5=-0.15001006 p25=-0.05279022 p50=0.04095101 p75=0.12979134 p95=0.21922987
   rho_spearman_k+3       p5=-0.12294954 p25=-0.05332485 p50=0.01258734 p75=0.08130291 p95=0.17646326
   rho_spearman_k+4       p5=-0.17062833 p25=-0.08523199 p50=-0.00934232 p75=0.09772904 p95=0.16841857
   rho_spearman_k+5       p5=-0.19281382 p25=-0.07965774 p50=-0.0111654 p75=0.06312345 p95=0.18047005
   rho_spearman_k-1       p5=-0.20921364 p25=-0.06711575 p50=0.0130269 p75=0.08435937 p95=0.19336733
   rho_spearman_k-2       p5=-0.18068121 p25=-0.11281938 p50=-0.04038646 p75=0.02674196 p95=0.14300734
   rho_spearman_k-3       p5=-0.1578149 p25=-0.05533079 p50=0.01744619 p75=0.06312618 p95=0.16155522
   rho_spearman_k-4       p5=-0.15915403 p25=-0.03373339 p50=0.03246019 p75=0.11186139 p95=0.19616041
   rho_spearman_k-5       p5=-0.13525537 p25=-0.01371651 p50=0.0683262 p75=0.14441502 p95=0.21175883
   se_beta                p5=0.03029903 p25=0.04593537 p50=0.05502968 p75=0.06853929 p95=0.08235928
   stale_days_in_window   p5=25.0 p25=26.0 p50=27.0 p75=28.0 p95=29.0
-- fred_VIXCLS  (k=0 windows: 1736)
   beta                   p5=-0.34773442 p25=-0.23349053 p50=-0.15369278 p75=-0.10848476 p95=-0.04015997
   rho_pearson_k+0        p5=-0.51709675 p25=-0.431327 p50=-0.33097381 p75=-0.20053723 p95=-0.05709563
   rho_pearson_k+1        p5=-0.11843244 p25=-0.05771972 p50=0.00831092 p75=0.0775297 p95=0.16388401
   rho_pearson_k+2        p5=-0.16167501 p25=-0.05875747 p50=0.02103122 p75=0.11786861 p95=0.21666375
   rho_pearson_k+3        p5=-0.1883269 p25=-0.07986157 p50=0.00746207 p75=0.09824414 p95=0.16636341
   rho_pearson_k+4        p5=-0.17215217 p25=-0.08422543 p50=-0.01369198 p75=0.07325046 p95=0.21845221
   rho_pearson_k+5        p5=-0.19290735 p25=-0.09765556 p50=-0.01759463 p75=0.0826004 p95=0.17005872
   rho_pearson_k-1        p5=-0.13709901 p25=-0.06907528 p50=0.01207488 p75=0.09035042 p95=0.18322482
   rho_pearson_k-2        p5=-0.11818579 p25=-0.06094233 p50=-0.016801 p75=0.06449253 p95=0.15957973
   rho_pearson_k-3        p5=-0.1580477 p25=-0.06510412 p50=-0.01447838 p75=0.0699846 p95=0.16742083
   rho_pearson_k-4        p5=-0.25435883 p25=-0.04553741 p50=0.02225822 p75=0.12481021 p95=0.24264814
   rho_pearson_k-5        p5=-0.10296324 p25=-0.01028464 p50=0.05181209 p75=0.11815645 p95=0.20079116
   rho_spearman_k+0       p5=-0.43634018 p25=-0.32975334 p50=-0.23380861 p75=-0.14623063 p95=-0.00710973
   rho_spearman_k+1       p5=-0.13638805 p25=-0.0485748 p50=0.01738255 p75=0.08965601 p95=0.18433078
   rho_spearman_k+2       p5=-0.15001006 p25=-0.05279022 p50=0.04095101 p75=0.12979134 p95=0.21922987
   rho_spearman_k+3       p5=-0.12294954 p25=-0.05332485 p50=0.01258734 p75=0.08130291 p95=0.17646326
   rho_spearman_k+4       p5=-0.17062833 p25=-0.08523199 p50=-0.00934232 p75=0.09772904 p95=0.16841857
   rho_spearman_k+5       p5=-0.19281382 p25=-0.07965774 p50=-0.0111654 p75=0.06312345 p95=0.18047005
   rho_spearman_k-1       p5=-0.20921364 p25=-0.06711575 p50=0.0130269 p75=0.08435937 p95=0.19336733
   rho_spearman_k-2       p5=-0.18068121 p25=-0.11281938 p50=-0.04038646 p75=0.02674196 p95=0.14300734
   rho_spearman_k-3       p5=-0.1578149 p25=-0.05533079 p50=0.01744619 p75=0.06312618 p95=0.16155522
   rho_spearman_k-4       p5=-0.15915403 p25=-0.03373339 p50=0.03246019 p75=0.11186139 p95=0.19616041
   rho_spearman_k-5       p5=-0.13525537 p25=-0.01371651 p50=0.0683262 p75=0.14441502 p95=0.21175883
   se_beta                p5=0.03029903 p25=0.04593537 p50=0.05502968 p75=0.06853929 p95=0.08235928
   stale_days_in_window   p5=25.0 p25=26.0 p50=27.0 p75=28.0 p95=29.0
-- fred_SP500  (k=0 windows: 1736)
   beta                   p5=0.22744323 p25=0.75237143 p50=1.2015969 p75=1.59321246 p95=2.09670365
   rho_pearson_k+0        p5=0.05316435 p25=0.20461351 p50=0.34360714 p75=0.46223765 p95=0.56382748
   rho_pearson_k+1        p5=-0.20055282 p25=-0.11215205 p50=-0.030451 p75=0.03670595 p95=0.11002692
   rho_pearson_k+2        p5=-0.21298149 p25=-0.09099046 p50=-0.01893267 p75=0.06317911 p95=0.15416438
   rho_pearson_k+3        p5=-0.14797018 p25=-0.08255828 p50=-0.024668 p75=0.03852359 p95=0.19767648
   rho_pearson_k+4        p5=-0.20127547 p25=-0.0698316 p50=0.00765533 p75=0.08031156 p95=0.17642625
   rho_pearson_k+5        p5=-0.16023923 p25=-0.04471964 p50=0.00341353 p75=0.07567958 p95=0.17856771
   rho_pearson_k-1        p5=-0.21009514 p25=-0.09322363 p50=0.00215448 p75=0.07381685 p95=0.1872022
   rho_pearson_k-2        p5=-0.25739309 p25=-0.08685106 p50=0.02897203 p75=0.08231229 p95=0.15344111
   rho_pearson_k-3        p5=-0.16939751 p25=-0.08577992 p50=0.00428838 p75=0.0696014 p95=0.1582132
   rho_pearson_k-4        p5=-0.19854693 p25=-0.09508925 p50=-0.03731506 p75=0.05956917 p95=0.26524645
   rho_pearson_k-5        p5=-0.17946659 p25=-0.11623789 p50=-0.0546902 p75=0.01876723 p95=0.12282162
   rho_spearman_k+0       p5=0.04787275 p25=0.17504737 p50=0.26894299 p75=0.38642052 p95=0.49976218
   rho_spearman_k+1       p5=-0.22738268 p25=-0.12592694 p50=-0.05756137 p75=0.03007424 p95=0.15549157
   rho_spearman_k+2       p5=-0.20585477 p25=-0.0990461 p50=-0.00332681 p75=0.08883016 p95=0.19386932
   rho_spearman_k+3       p5=-0.1761288 p25=-0.10446446 p50=-0.0290147 p75=0.02699675 p95=0.12222099
   rho_spearman_k+4       p5=-0.22287067 p25=-0.06199262 p50=0.01630408 p75=0.10139471 p95=0.19451651
   rho_spearman_k+5       p5=-0.15685276 p25=-0.05195427 p50=0.01324312 p75=0.08184229 p95=0.2372348
   rho_spearman_k-1       p5=-0.20894961 p25=-0.09573492 p50=-0.00495314 p75=0.09969905 p95=0.25516693
   rho_spearman_k-2       p5=-0.20927722 p25=-0.08049347 p50=0.04039482 p75=0.12876242 p95=0.20955743
   rho_spearman_k-3       p5=-0.15182941 p25=-0.08212053 p50=-0.01548194 p75=0.06370421 p95=0.14767204
   rho_spearman_k-4       p5=-0.17065535 p25=-0.10987534 p50=-0.0234571 p75=0.05206276 p95=0.16520435
   rho_spearman_k-5       p5=-0.19602764 p25=-0.11791059 p50=-0.04025818 p75=0.01922578 p95=0.0883412
   se_beta                p5=0.19990703 p25=0.25701339 p50=0.34678474 p75=0.49286683 p95=0.79275552
   stale_days_in_window   p5=26.0 p25=27.0 p50=28.0 p75=29.0 p95=30.0
-- fred_NASDAQ100  (k=0 windows: 1736)
   beta                   p5=0.1010889 p25=0.63371942 p50=0.90760396 p75=1.09158985 p95=1.48162226
   rho_pearson_k+0        p5=0.04096657 p25=0.21884585 p50=0.31976573 p75=0.45292857 p95=0.58409992
   rho_pearson_k+1        p5=-0.1859357 p25=-0.09194209 p50=-0.02353624 p75=0.02954008 p95=0.08425647
   rho_pearson_k+2        p5=-0.19051829 p25=-0.09452827 p50=-0.02737893 p75=0.04501915 p95=0.15263909
   rho_pearson_k+3        p5=-0.18550427 p25=-0.09073813 p50=-0.03851242 p75=0.02966551 p95=0.14591059
   rho_pearson_k+4        p5=-0.23194874 p25=-0.08439168 p50=0.00299193 p75=0.09315812 p95=0.169211
   rho_pearson_k+5        p5=-0.16175371 p25=-0.06099847 p50=0.01945493 p75=0.08545133 p95=0.16031491
   rho_pearson_k-1        p5=-0.23306625 p25=-0.11890258 p50=-0.01104091 p75=0.06550746 p95=0.16279945
   rho_pearson_k-2        p5=-0.2770621 p25=-0.09085433 p50=0.0306593 p75=0.08530274 p95=0.16475198
   rho_pearson_k-3        p5=-0.1613685 p25=-0.08886893 p50=-0.0058635 p75=0.06689467 p95=0.17023853
   rho_pearson_k-4        p5=-0.21324881 p25=-0.11577628 p50=-0.03586745 p75=0.03979064 p95=0.20709415
   rho_pearson_k-5        p5=-0.18334837 p25=-0.11830682 p50=-0.06437448 p75=0.00383534 p95=0.13687695
   rho_spearman_k+0       p5=0.03435428 p25=0.16989445 p50=0.24670534 p75=0.37658951 p95=0.51594332
   rho_spearman_k+1       p5=-0.20965438 p25=-0.11774463 p50=-0.03634949 p75=0.03129424 p95=0.10526455
   rho_spearman_k+2       p5=-0.17555894 p25=-0.10688628 p50=-0.02140633 p75=0.06028187 p95=0.16641791
   rho_spearman_k+3       p5=-0.18722422 p25=-0.10984196 p50=-0.0443899 p75=0.01791618 p95=0.12284596
   rho_spearman_k+4       p5=-0.2291471 p25=-0.09010059 p50=0.00505691 p75=0.08488239 p95=0.17824571
   rho_spearman_k+5       p5=-0.1249269 p25=-0.05019472 p50=0.01725953 p75=0.09986876 p95=0.18065061
   rho_spearman_k-1       p5=-0.19020787 p25=-0.10045829 p50=-0.0176527 p75=0.10536976 p95=0.2058931
   rho_spearman_k-2       p5=-0.19201119 p25=-0.07924259 p50=0.03319012 p75=0.11221674 p95=0.20016489
   rho_spearman_k-3       p5=-0.12903962 p25=-0.06851894 p50=0.00080168 p75=0.06495633 p95=0.13810274
   rho_spearman_k-4       p5=-0.1950601 p25=-0.12402276 p50=-0.04495229 p75=0.02481527 p95=0.15013009
   rho_spearman_k-5       p5=-0.17367961 p25=-0.10770452 p50=-0.04349424 p75=0.01183286 p95=0.10483844
   se_beta                p5=0.15849571 p25=0.19754264 p50=0.24398048 p75=0.35393744 p95=0.60468834
   stale_days_in_window   p5=26.0 p25=27.0 p50=28.0 p75=29.0 p95=30.0
```

**Stop point: G3-B ends here.** The spec stage follows — feature
families and their frozen transforms, forecast form, lambda and caps,
calibration method, the path-statistic invalidation definition, exact
Q1–Q4 criteria, and its lock commit — reviewed by both delegates before
the single G3-C trial. Per §69.1.1 nothing in this section may inform
any lag, window, or transform in M1.

### 70.1 Stage G3-C-SPEC — owner decisions, recorded verbatim before any fitting (2026-09-02)

**No forecast fitted. No return-based result. No trial consumed. Gen-3
0 of 20. Holdout sealed. §70.1–§70.4 are appended in their own commit
before any implementing code; §70.5 (the lock record) follows the code
in the lock commit itself.** Governing: §0, §68 as amended (§68.11,
§68.12), §69.

#### 70.1.1 Source timing — USER DECISION: publisher timing, on architectural grounds

> **The deployed system will read each exogenous value from its
> publisher's own public page on the evening it is published** (CBOE
> for VIX, the Federal Reserve for H.15/H.10, the index publisher for
> cash closes), at a fixed evening UTC time, using the existing
> supervisor's scheduled cycle. **Therefore publisher availability is
> what the deployed bot will actually possess, and training uses
> publisher timing.**

**The justification is architectural, not empirical.** It rests on the
intended production source, not on the §69 sensitivity map showing
anything — that map may not be cited as a reason (§69.1.1 quarantine).
The rule is the standing one: train at the timing you will deploy at.

Consequences, implemented in this stage:
- Loader source rules for the re-sourced series become their
  publishers' documented release rules: fred_DGS2 / fred_DGS10 /
  fred_DTWEXBGS → the H.15/H.10 rule (next business day, 16:15
  America/New_York); fred_SP500 / fred_NASDAQ100 → the cash-close rule
  (same day, 16:00 America/New_York); cboe_VIX already publisher-direct
  (same day, 16:15 America/New_York). The §68.12.1 conservative
  aggregator rules are retired FOR THESE SERIES ONLY.
- `source_availability_quality` becomes `"observed"` for the
  re-sourced series under §69.0's own clause ("a direct publisher feed
  replaces the aggregator"): the publisher's documented release
  schedule is a genuine publisher release time, not an invented
  aggregator lag. Recorded honestly: per-observation release delays
  are still not modelled; in deployment a missed or late fetch yields
  a STALE value with its true knowable-at stamp — never a substituted
  or forward-filled value pretending to be fresh.
- fred_VIXCLS is NOT re-sourced (VIX comes from CBOE); it keeps the
  conservative mirror rule and its `"conservative_assumption"` flag,
  and remains a cross-check, not an input.
- The manifest's `publisher` / `retrieval_source` fields are
  UNCHANGED: the staged historical bytes are still FRED's and their
  retrieval provenance stays true (§68.12.1 never collapses). What
  changes is whose release rule governs availability, per this owner
  decision.
- The §69.2 PIT-valid map remains the record of what the conservative
  rule produced; the sensitivity map is NOT retroactively relabelled.
- The invariants stand unchanged: `source_available_time >=
  underlying_public_time` (now with equality where source IS the
  publisher); the reader returns nothing when `source_available_time >
  decision_time`.
- At the daily decision grid (00:00 UTC boundaries) every publisher
  release above (16:00/16:15 America/New_York = 20:00–21:15 UTC) plus
  any same-evening fetch precedes the next boundary, so the daily
  alignment is identical for any post-release evening fetch hour.
- The fetch job itself (fixed evening UTC time on the existing
  supervisor, recording `retrieved_at_utc` with
  `retrieval_time_quality = "observed"` on every future retrieval; no
  live feed, no vendor, no subscription) is a DEPLOYMENT REQUIREMENT
  recorded now and built only if a construction stage is reached —
  §68.7's rule: operational architecture is adopted as requirement,
  built after the test.

#### 70.1.2 Adoption — USER DECISION: full Panel A, gold excluded

Adopted (`adopted: true` in the manifest for the exogenous set): **BTC,
ETH** (PIT store, with funding carry), **VIX** (cboe_VIX), **US 2Y**
(fred_DGS2), **US 10Y** (fred_DGS10), **the USD measure**
(fred_DTWEXBGS — the Fed broad-dollar SUBSTITUTE stands as the USD
measure of record; the basket difference from ICE DXY remains
disclosed), **S&P 500** (fred_SP500), **Nasdaq-100** (fred_NASDAQ100)
— each at publisher timing per §70.1.1 where re-sourced.
fred_VIXCLS: not adopted (cross-check). **Gold: NOT adopted** — no
verified source; §68.11.2's UNVERIFIED stands.

**Rates and USD are adopted despite looking weak in §69's
unconditional rolling correlations.** Dropping them after seeing that
map would be selection on development data. Weak-unconditionally is
not useless-conditionally; M1's coefficients decide, not the map.

The manifest's three `adoption_decisions_open` entries are annotated
RESOLVED by this section: DTWEXBGS adopted as the USD measure;
SP500/NASDAQ100 adopted for Panel A only (overnight-gap limitation
stands); gold not adopted.

#### 70.1.3 Panel B — DEFERRED (owner decision)

No intraday branch, no futures feed, no live market data. Rationale
recorded: price is set by the fastest marginal participant, so speed
cannot be this system's edge at this capital; if an edge exists it is
in slower persistence and aggregation of information. Panel B is
reconsidered ONLY if Q2 or Q4 pass. **Consequence recorded
explicitly:** §68.9's "one pre-registered sub-daily horizon" is
deferred WITH Panel B — G3-C scores the daily horizon only. This is an
owner decision recorded before any fitting, not a silent narrowing.

#### 70.1.4 Cross-sectional instantiation — USER DECISION (2026-09-02, choice put to the owner and answered "per-name transforms")

The §70.2 family table names families 1–2 at the BTC level, but Q3/Q4
score a shared-coefficient cross-sectional model, which can only rank
names by features that differ across names. Two readings were put to
the owner: (a) families 1–2's frozen transforms instantiated PER NAME
for the cross-sectional model; (b) the literal table, leaving funding
level as the sole per-name regressor. **The owner chose (a):** each
name's trailing {1, 5, 21}d returns and 21d vol / vol-of-vol enter the
cross-sectional model per name, alongside per-name funding level
(family 3); the direction model for Q1/Q2 uses the BTC instantiation;
the family count stays 4 + 8 and no new transform is introduced. The
§68.3 hierarchical cold-start rung, if reached, inherits per-name
coefficients to shrink.

### 70.2 Model specification, frozen before fitting

Every completion below is labelled: USER DECISION, DERIVED, INHERITED
precedent, or CONVENTION (a standard reading fixed here, before any
data is read, quarantined from M1 feature choice by §69.1.1).

#### 70.2.1 Feature families — exactly 4 + 8, transforms frozen

```
M0 — crypto-native state + carry            (families 1–4)
  1 trend       : returns at fixed lags {1, 5, 21}d
  2 vol         : realised vol {21}d; vol-of-vol {21}d
  3 carry       : funding level {current}; funding dispersion {cross-sectional, current}
  4 dispersion  : cross-sectional return dispersion {current}; breadth of positive returns {current}

M1 — M0 + cross-asset state                 (families 5–8)
  5 equities    : SP500 return {1d}; NASDAQ100 return {1d}
  6 volatility  : VIX level; VIX Δ {1d}
  7 rates       : 2Y level; 2Y Δ {1d}; 10Y level; 10Y Δ {1d}; 2s10s slope
  8 usd         : USD measure return {1d}
```

Any addition, removal, or transform change after the lock commit is a
new specification requiring its own pre-registration. Every feature
uses the single most recent PIT-available observation at the decision
time — one lag, no lag search (§69.1.1).

**Completions:**
- Decision instant for target day `t`: the 00:00:00 UTC boundary at
  which day `t` begins; features use only data knowable then
  (CONVENTION, the τ construction of §69.1.3).
- "returns at fixed lags {1, 5, 21}d" = TRAILING log returns over the
  past 1, 5 and 21 days ending at the decision instant (CONVENTION —
  the standard horizon-return reading; single-day returns lagged 5 and
  21 days were considered and not chosen).
- realised vol = std (ddof=1) of the trailing 21 daily log returns;
  vol-of-vol = std (ddof=1) over the trailing 21 days of the daily
  realised-vol series (CONVENTION).
- funding level = the most recent settled 8h funding rate at the
  decision instant (one-lag rule); funding dispersion = std (ddof=1)
  of that quantity across the eligible universe (CONVENTION).
- cross-sectional return dispersion = std (ddof=1) of the prior day's
  1d log returns across the eligible universe; breadth = fraction of
  the eligible universe with strictly positive prior-day return
  (CONVENTION).
- exogenous 1d returns / Δ = the change between the TWO most recent
  distinct PIT-available observations (never zero-filled staleness;
  this is what the deployed reader will actually possess)
  (CONVENTION); levels (VIX, 2Y, 10Y) = most recent observation;
  2s10s = 10Y level − 2Y level. Yields difference in percentage
  points; equity/USD returns are log returns (INHERITED from §69.1.3's
  convention, restated for M1's features — chosen from architecture,
  not from the map).
- Direction model (Q1/Q2), BTC instantiation: M0 = 9 features (3
  trend + 2 vol + 2 carry + 2 dispersion), M1 = 19 (M0 + 10
  cross-asset). Cross-sectional model (Q3/Q4), per §70.1.4: per name
  i, M0 = 9 (per-name 3 trend + 2 vol + 1 funding, plus the 3 common
  features funding dispersion / return dispersion / breadth), M1 = 19
  (M0 + the 10 common cross-asset features).
- **Cross-sectional target = the RAW next-day log return of each name
  (DERIVED, not chosen):** with a shared-coefficient linear model and
  common (per-date) features, a cross-sectionally demeaned target
  makes every common feature exactly inert, which would render Q4
  mechanically undecidable. The IC statistic is rank-based per date,
  so the criterion itself is invariant to the common component; the
  raw target only lets common state purify the per-name coefficients.
- Eligible universe ("mature", Q3/Q4): the INHERITED Gen-1/Gen-2
  classification — `backtest/universe_filter.classify` eligibility
  with ≥ 180 days of history at the decision date (§59.3.2 precedent,
  as in §63.2). BTC and ETH are members like any other eligible name;
  juveniles (< 180d) are excluded here and belong to the §68.3 rung.
- Direction target: `y_t = 1` if BTC's day-`t` log return > 0 else 0
  (CONVENTION; an exactly-zero return is class 0 and is expected to be
  measure-zero on real data).

#### 70.2.2 Forecast form and regularisation

- Direction: **L2-regularised logistic regression** (intercept
  unpenalised), fitted by deterministic Newton/IRLS in closed
  iteration — no stochastic optimiser, no architecture.
- Cross-section: **ridge regression** (intercept unpenalised) pooled
  over name-days of the training window.
- Standardisation: features z-scored with training-window mean/std
  (ddof=1); a constant feature maps to zero (guard, reported if it
  occurs). Targets are never standardised.
- **Regularisation grid, frozen:** λ ∈ {10⁻³, 10⁻², 10⁻¹, 1, 10, 10²,
  10³} — seven log-spaced values, both models.
- **Nested expanding-window inner split, frozen:** order the training
  window chronologically; three inner folds — fit on the first 40% /
  validate on the next 15%; fit 55% / validate 15%; fit 70% / validate
  15% (the final 15% is left untouched by selection). Selection metric:
  mean log loss (logistic) / mean squared error (ridge) across the
  three validation segments; ties break toward the LARGER λ. The
  procedure — not a value — is frozen; the chosen λ is refit on the
  full training window and reported per segment.
- Nothing is ever selected against out-of-sample-in-time results.

#### 70.2.3 Sequential protocol (§68.8, as implemented)

Fit on 2020 → forecast 2021; refit 2020–21 → 2022; refit 2020–22 →
2023; refit 2020–23 → 2024. At no point does a later year inform an
earlier forecast; a test asserts every forecast's fit window ends
before its target date. ~1,461 out-of-sample-in-time daily forecasts.
Development window only; the seal and the §69 refusal machinery stand.

#### 70.2.4 Calibration — Platt scaling, fixed in this commit

- **Method (fixed here, as §70's spec requires): Platt scaling** — a
  two-parameter monotone logistic map. Rationale recorded: with ~300
  usable calibration points per segment, isotonic regression overfits
  the tails and produces flat spans; Platt is the lower-variance
  choice and is deterministic. Isotonic was considered and not chosen.
- **Procedure, frozen:** within each training window, fit the model
  (at the λ chosen by §70.2.2) on the first 80% of the window; predict
  the last 20%; fit Platt on those predictions; refit the model on
  100% of the window; apply the Platt map to the forecast year.
  Fitted inside the training window only, applied point-in-time.
- Calibration quality is REPORTED (reliability curve on 10 equal-width
  bins; Brier decomposition reliability/resolution/uncertainty on the
  same bins) and is NOT a criterion. Sizing, when it exists, uses a
  conservative lower reliability bound (§68.2.2) — deferred with the
  book.

#### 70.2.5 Deferred, explicitly

No book is built in G3-C, so not specified here: λ_risk and position
caps, the path statistic for invalidation (§68.12.5), the composite
model-health rule, the hierarchical cold-start estimator (§68.12.6 —
gated on Q3 PASS). Specified only if a construction stage is reached.

### 70.3 Criteria, fixed before the run

#### 70.3.1 The four questions (conjunctive per §68.11.4.1)

```
Q1  M0 BTC-direction skill vs climatology   CI_lower(BSS_M0) > 0
Q2  M1 incremental, BTC direction           CI_lower(BSS_M1) > 0  AND  CI_lower(BSS_M1 − BSS_M0) > 0
Q3  M0 cross-sectional skill (mature)       CI_lower(IC̄_M0) > 0     (§60.12 evaluator)
Q4  M1 incremental, cross-sectional         CI_lower(IC̄_M1) > 0  AND  CI_lower(IC̄_M1 − IC̄_M0) > 0
```

- **BSS** = 1 − ΣBS_model / ΣBS_clim pooled over all defined
  out-of-sample days 2021–2024. **Climatology (frozen):** the constant
  probability equal to the training-window frequency of up days,
  recomputed per sequential segment and applied to that segment's
  forecast year.
- **IC** = the §60.12.3 estimand exactly: equal-weighted daily
  cross-sectional Spearman between the model's per-name forecast and
  the realized next-day return, average ranks for ties, undefined
  dates excluded / counted / reported with reason, never zero-filled.
  Evaluated by the §60.12 machinery (`rcm/eval_ic.py`), inherited by
  import and pinned by hash in the lock.

#### 70.3.2 Bootstrap — inherited construction, generalized only in the statistic

`rcm/eval_ic.stationary_bootstrap_ci` (the Gen-1 construction ported
line-for-line per §60.12.3: Politis–Romano stationary bootstrap,
geometric-block index walk with wraparound, percentile interval,
n < 30 ⇒ NaN and < n_boot/10 finite-replicate guards; 2,000
replicates; mean block max(2, n^{1/3}) recomputed per series;
two-sided 90%) is REUSED DIRECTLY for every criterion. **Paired
statistics (frozen):** the series handed to the walker is the day
index 0..n−1 of the defined evaluation days; `stat_fn` maps each
resampled index row to the statistic computed on the PAIRED per-day
components (BS_M0, BS_M1, BS_clim for Q1/Q2; IC_M0, IC_M1 for Q3/Q4).
One index draw per criterion family, so differences are paired by
construction — the §68.9 "paired Brier difference" and "paired
daily-IC difference" as frozen. A test proves the index-series call
reproduces the direct call bit-for-bit on a shared fixture and seed.

**Seed (INHERITED §60.12.3 governance):**
`seed = int(sha256(lock_commit_hex)[:8], 16)` — the lock commit is the
one recording §70.5; the value is derived and printed at the run
stage, never predicted.

**INDETERMINATE (frozen definition):** a criterion is INDETERMINATE
when its interval is undefined under the inherited guards (fewer than
30 defined days, or fewer than n_boot/10 finite replicates) — the
evaluator cannot resolve the hypothesis at the frozen precision
(§68.12.3). Otherwise PASS iff CI_lower > 0, else FAIL. For Q2/Q4 the
conjunction is evaluated per §68.11.4.1; if any leg is INDETERMINATE
and no leg FAILS, the criterion is INDETERMINATE (⇒ M0 per §68.9).

#### 70.3.3 MDE disclosure (§60.12.3 form)

No numerical MDE is fabricated. The realized CI half-width of every
interval is reported afterward as observed resolving precision and is
not a second criterion.

#### 70.3.4 Economic disclosure (§68.6 rule 3 — disclosure, never a gate)

Beside each result: implied edge versus turnover-adjusted cost at the
daily horizon under the INHERITED Gen-1 cost model
(`backtest/costs.py`, `fee_mode="taker"` — the conservative mode;
pinned by hash in the lock), and the expected trade count at the
§68.12.4 general gate `E[r] − C − required_risk_compensation > 0`
(risk compensation set to zero for the disclosure — the weakest gate;
anything failing it fails every stronger one). Activity is a
DISCLOSURE; too few observations to resolve ⇒ INDETERMINATE, not FAIL
(§68.12.3).

#### 70.3.5 Consequences, pre-registered (no post-result discretion)

```
Q2 or Q4 PASS                       exogenous thesis supported at that level; next rung earns its turn (§68.12.7)
Q2 and Q4 fail/INDET, Q1 or Q3 PASS exogenous thesis NOT supported; §68.1 construction may proceed on crypto-native forecasts, relabelled as such
All four fail                       Gen-3 STOPS before any book exists; recorded as a clean result
Q3 PASS                             the hierarchical cold-start rung becomes testable (§68.12.6); Q4 decides only feature inheritance
Reproduced implementation defect    attempt VOID (void: true, retained, budget unconsumed — §60.12.4 accounting); corrected run is the next attempt
```

No path from an attractive PnL or correlation to keeping a
specification whose criterion failed.

### 70.4 The lock plan

The lock commit (the one recording §70.5) pins, via `LOCK <path>
sha256=<hash>` lines quoted from printed output: `g3/features.py`,
`g3/models.py`, `g3/calibration.py`, `g3/sequential.py`, `g3/eval.py`,
`rcm/eval_ic.py`, `backtest/costs.py`. An immutability test parses the
LOCK lines from this ledger and fails if any pinned file's hash
drifts — the run stage cannot alter any frozen quantity between
pre-registration and execution. Deterministic evaluator tests land
BEFORE the lock: planted positive skill ⇒ CI_lower > 0; planted zero ⇒
the CI straddles zero; planted negative ⇒ FAIL; the conjunctive
criterion rejects BSS_M0 = −0.20, BSS_M1 = −0.10. All model and
evaluator tests run on SYNTHETIC data only in this stage; no
development return is read. Trial 1 is logged in §70.5 as
`status: pre-registered`, `attempt_id = 1`, `valid_trial_count = 0`.
**After §70.5, this stage STOPS for both delegates' review; G3-C is a
separate stage.**

### 70.5 The specification lock (2026-09-02) — this commit is the lock commit

Implementation landed exactly as §70.1–§70.4 froze it: publisher-timing
re-sourcing in `tools/g3_exogenous_loader.py` and the manifest (six
series `adopted: true` per §70.1.2, quality `"observed"` per §70.1.1;
fred_VIXCLS keeps the conservative mirror; gold not adopted); the `g3/`
package (features / models / calibration / sequential / eval) built on
the inherited `rcm/eval_ic.py` machinery; every §70.4 deterministic
evaluator test green on SYNTHETIC data (planted positive ⇒ CI_lower >
0; planted zero ⇒ straddle; planted negative ⇒ FAIL; the conjunctive
criterion rejects BSS_M0 = −0.20 / BSS_M1 = −0.10 while its difference
leg alone would pass); the sequential-protocol test proves every fit
window ends before its target dates and counts 1,461 OOS days; the
bit-equivalence test proves the paired index-walk call reproduces the
inherited walker exactly. No development return was read.

The pinned code, hashes computed from the files at lock time (the
immutability test `tests/test_g3c_lock.py` re-derives these on every
run; any drift voids the pre-registration):

```
LOCK-G3 g3/features.py sha256=6fd56644fb4cf06ae022c67d5864861698378d0f37d791cad2c3fb66315bea09
LOCK-G3 g3/models.py sha256=e108d936132bb6025a1800bd1261d6eb5474ed8f826eb70fb1167f13f811d9ba
LOCK-G3 g3/calibration.py sha256=3f4c88b662509b40142fdf930efd38e12e07ca44a2b67ad62fcea8e72c89e8cc
LOCK-G3 g3/sequential.py sha256=c0bdd4c52a83262d59d25d461dc84ae1d44a40e3cc8b3f150c413de38c71bfea
LOCK-G3 g3/eval.py sha256=76cbc280ab5359563e8d1f68101edcb9046631939978a0d761a73bf796ccad81
LOCK-G3 rcm/eval_ic.py sha256=a1f29dccbbecff7e9969f8f3ccd0c62fc102aba022ae5e17bd8c6c82d8ab0935
LOCK-G3 backtest/costs.py sha256=cbd40c2735c85fab60479c833042b47d455f8aeb2896e9c5e1d317f40ab586a5
```

`rcm/eval_ic.py` remains bit-identical to its §66.5 Gen-2 lock;
`backtest/costs.py` is pinned for the §70.3.4 disclosure
(`fee_mode="taker"`).

**Trial pre-registration:**

```
G3-TRIAL-1 status=pre-registered attempt_id=1 valid_trial_count=0
```

Seed at the run stage: `int(sha256(lock_commit_hex)[:8], 16)` with
`lock_commit_hex` = this commit's hash, printed at run time, never
predicted (§70.3.2, inheriting §60.12.3 governance).

**STOP. Both delegates review §70 before G3-C executes. G3-C is a
separate stage; it consumes Gen-3 trial 1 of 20 when it runs. No
forecast has been fitted on real data; the holdout is sealed.**

### 70.6 Stage G3-C-SPEC v2 — eleven corrections from delegate review (2026-09-02; appended before any correcting code)

**No forecast fitted on real data. No return-based result. No trial
consumed. Gen-3 0 of 20. Holdout sealed.** The first delegate's review
of §70.1–§70.5 found eleven items — including one ALGEBRAIC DEGENERACY
that would have wasted the trial — and the second accepted. This
section supersedes the named parts of §70.1–§70.4; §70.5's LOCK-G3
lines are VOID (the code they pinned changes here) and are replaced by
§70.7's at the new lock commit. Trial 1 remains
`status: pre-registered, attempt_id = 1, valid_trial_count = 0` — v1
never ran, so nothing is consumed by re-specification.

#### 70.6.1 Source timing — possession time, not publisher time (supersedes the timing rule of §70.1.1)

v1 trained at publisher release time. **Wrong:** publisher time is not
the time the bot knows the value — if the Fed publishes at 21:15 UTC
and the scheduled job runs at 22:00 UTC, the bot possesses it at
22:00. Frozen:

    t_usable = the first scheduled fetch instant >= the publisher's
               release (equivalently max(t_publisher,
               t_scheduled_retrieval) on the fetch day)

**Train at the earliest timestamp the deployed acquisition system
would actually possess the observation.** Training at t_publisher
would hand the model up to ~45 minutes it will not have.

**Completion, recorded:** the scheduled fetch is **daily at 22:00:00
UTC** — the §70.6.3 decision instant, so possession and decision
coincide and no possessed value waits. This is a genuine UTC constant
because it is OUR OWN supervisor schedule (cron runs in UTC,
DST-invariant by design); the §68.11.1.1 ban on UTC release constants
governs PUBLISHER schedules, which remain zoneinfo-local throughout.
Every publisher release in the adopted set (16:00/16:15
America/New_York = 20:00–21:15 UTC) precedes the same-evening fetch in
both DST regimes, so t_usable lands on the release's own UTC date.
Single definition: the fetch hour and the t_usable grid live in
`g3/timing.py`; `tools/g3_exogenous_loader.py` consumes them
(`usable_utc`, `pit_view_usable`) — the §69.2 record and the
publisher-availability view are unchanged underneath.

The §70.1.1 architectural justification stands (never the §69
sensitivity map); the invariants stand; a missed fetch yields a STALE
value with its true knowable-at stamp, never a forward-filled value
posing as fresh. The `"observed"` quality flags stand — the basis
strings gain the possession clause.

#### 70.6.2 Panel B — deferred, NOT conditional (supersedes §70.1.3's gate)

v1's "reconsidered only if Q2 or Q4 pass" was CIRCULAR: a daily test
cannot falsify an intraday transmission hypothesis — an effect lasting
twenty minutes would fail Q2 and still be real. Frozen: Panel B
remains deferred and may later be proposed as an INDEPENDENT,
separately pre-registered mechanism if justified by architecture and
resources. **A daily G3-C PASS is not a prerequisite.**

#### 70.6.3 The four timestamps of the daily arm (new; sharpens §70.2.1's decision instant)

    22:00 UTC day D    decision_time = feature_cutoff_time
                       (features: only observations with t_usable <= 22:00 D)
    00:00 UTC D+1      target_start_time (hypothetical execution)
    00:00 UTC D+2      target_end_time — the next COMPLETE UTC day

**Stated so it cannot be misread later: the strategy does not
notionally enter at 22:00.** The 22:00 observation is a decision
snapshot; execution is defined at the next 00:00 UTC boundary, and the
target is the next complete UTC-day return. The two-hour gap is
deliberately forgone — losing two hours of possible edge is preferred
to ambiguous training/live alignment. No information arriving between
22:00 and 00:00 may enter that forecast. Panel B may recover the gap
later. Crypto features use COMPLETE UTC days only: at the day-D
cutoff, the most recent complete day is D−1. A test asserts every
feature's t_usable <= 22:00 D and every target window is
[00:00 D+1, 00:00 D+2).

#### 70.6.4 Two models, distinct semantics (supersedes §70.2.1's single table)

v1 conflated the direction and cross-sectional feature semantics; the
conflation is what produced the Q4 defect. Frozen separately:

    M0-dir  (families 1–4)
      1 btc_trend : BTC log-return over {1, 5, 21} complete UTC days
      2 btc_vol   : realised vol = sample std (ddof=1) of daily BTC
                    log-returns over 21 complete days, NOT annualised;
                    vol_of_vol = sample std of that 21d series over 21 days
      3 carry     : BTC funding rate, most recent settlement with
                    t_usable <= cutoff; cross-sectional MEAN funding
                    over the eligible universe, same rule
                    (v1's funding DISPERSION is superseded)
      4 dispersion: cross-sectional sample std (ddof=1) of 1-day
                    log-returns over the eligible universe; breadth =
                    fraction with positive 1-day log-return
    M1-dir = M0-dir + (families 5–8)
      5 equities  : SP500 1-day log-return; NASDAQ100 1-day log-return
      6 volatility: VIX level; VIX 1-day change (levels, not log)
      7 rates     : 2Y level; 2Y Δ1d; 10Y level; 10Y Δ1d; 2s10s = 10Y − 2Y
      8 usd       : USD measure 1-day log-return

Direction: M0 = 9 features, M1 = 19. Funding settlements use the
frozen 8h grid; "most recent with t_usable <= 22:00 D" = the 16:00 UTC
day-D settlement in normal operation.

#### 70.6.5 The Q4 degeneracy and the exposure-interaction fix (supersedes §70.1.4's instantiation and §70.2.1's cross-sectional table)

**The degeneracy, recorded:** with exogenous terms common to every
asset, r̂ᴹ¹_i = r̂ᴹ⁰_i + γᵀX_t, every pairwise difference is unchanged
and the cross-sectional ranking is IDENTICAL BY CONSTRUCTION —
Spearman exactly 1.0. v1's Q4 could not return anything but zero (its
only live channel was coefficient purification of the per-name terms).
It was not a weak test; it was not a test. v1's raw-target derivation
is MOOT under this fix and is withdrawn with it.

    M0-xs   per-asset predictors, market state as conditioning only
      1 asset_trend : log-return over {1, 5, 21} complete UTC days, per asset
      2 asset_vol   : sample std (ddof=1) of daily log-returns over 21
                      days, per asset  (NO per-asset vol-of-vol)
      3 asset_carry : the asset's funding rate, most recent settlement
                      with t_usable <= cutoff
      4 market_state: the M0-dir family-4 dispersion and breadth
                      (shared; conditioning only)
    M1-xs = M0-xs + asset-specific EXPOSURE × exogenous state
      5 equity_beta_int : β^SPX_{i,t}·r_SPX,t ;  β^NDX_{i,t}·r_NDX,t
      6 vol_beta_int    : β^VIX_{i,t}·ΔVIX_t
      7 rates_beta_int  : β^2Y_{i,t}·Δ2Y_t ;  β^10Y_{i,t}·Δ10Y_t
      8 usd_beta_int    : β^USD_{i,t}·r_USD,t

Cross-section: M0 = 7 features, M1 = 13. The same market move now
affects a high-exposure and a low-exposure coin differently; Q4 asks a
real question. The cross-sectional target remains the next complete
UTC-day log return per asset (raw; ranks are demeaning-invariant).

#### 70.6.6 The exposure estimator — frozen, or it becomes the next tuning surface (new)

    β^X_{i,t} = Cov(r_i, r_X) / Var(r_X)   over the trailing 90 complete
                                           UTC days ending at the cutoff
      intercept        : yes (OLS with intercept; β is the slope)
      min observations : 60 valid paired days, else the asset's
                         interaction features are MISSING for that date
      missing data     : the date/asset is excluded from that day's
                         cross-section, counted and reported; never
                         zero-filled
      standard error   : computed and recorded (not a feature in Trial 1)
      shrinkage        : NONE in Trial 1
      estimated        : separately per X ∈ {SPX, NDX, VIX, 2Y, 10Y, USD}
      universe         : mature names only — assets meeting the
                         min-observation rule (supersedes v1's 180d
                         eligibility inheritance for this evaluation)

**No hierarchical shrinkage in Trial 1**, deliberately: sliding the
juvenile solution into Q4 would let it earn credit before the juvenile
hypothesis has its own pre-registered trial (§68.12.6, gated on Q3
PASS).

**Completion (paired evaluation):** Q3's own criterion runs on M0-xs's
defined cross-sections; Q4's difference leg runs on the per-date
INTERSECTION cross-section (assets valid for both models that day),
with both ICs recomputed there — the paired construction of §70.3.2,
extended from dates to assets. Exclusions counted and reported per
model and per date.

#### 70.6.7 Regularisation — explicit grids, calendar folds, strongest-tie-break (supersedes §70.2.2)

Form unchanged: L2 logistic (direction), L2 linear/ridge
(cross-section); simplest forms admitting the features; the neural
network stays a later rung (§68.6 rule 5).

    logistic grid : C ∈ {0.01, 0.03, 0.1, 0.3, 1, 3, 10}
                    objective: Σ nll + (1/(2C))·‖w_slopes‖²  (intercept unpenalised)
    ridge grid    : α ∈ {0.001, 0.01, 0.1, 1, 10}
                    objective: ‖y − Aw‖² + α·‖w_slopes‖²      (intercept unpenalised)
    inner folds   : expanding-window CV inside the training years only,
                    split at CALENDAR-YEAR boundaries (fit the first k
                    years, validate year k+1, for every k); for the
                    single-year 2020 first fit, at QUARTER boundaries
                    (fit Q1 → val Q2; fit Q1–Q2 → val Q3; fit Q1–Q3 → val Q4)
    metric        : mean inner-fold log-loss (direction) / MSE (cross-section)
    tie-break     : the STRONGEST regularisation among exact ties —
                    smallest C / largest α (supersedes v1's weakest-wins)
    standardise   : training-window statistics only (ddof=1); constant
                    feature ⇒ zero (guard)

v1's 7-point λ grid, 40/55/70+15% fractional folds, and larger-λ
tie-break are superseded.

#### 70.6.8 Calibration fitted OUT-OF-FOLD (supersedes §70.2.4's 80/20 procedure)

Method unchanged: Platt scaling, chosen for stability at these sample
sizes. The calibrator is NEVER fitted to in-sample fitted
probabilities. Frozen: after penalty selection, refit at the chosen
value on each §70.6.7 inner fold's fit span and predict its validation
span; fit Platt on the concatenated time-respecting OUT-OF-FOLD
predictions; fit the final model on the full training window; forecast
the target year; apply the already-fitted calibrator. Reliability
curve and Brier decomposition reported, never a criterion.

#### 70.6.9 Descriptive reporting only — the economic gate is REMOVED (supersedes §70.3.4)

v1 asked a classifier for E[r] it does not produce: a calibrated model
gives P(r > 0 | X), not E[r | X]; deriving one would require an
invented magnitude mapping. Removed entirely — no trade count, no cost
gate, no profitability claim, and `backtest/costs.py` LEAVES the lock
set. Reported instead, descriptively, with no pass/fail effect:
Brier/BSS; reliability curve; probability-bin counts; and realized
subsequent returns conditional on calibrated-probability bin (the ten
§70.6.8 reporting bins: n, actual up-rate, mean and median next-day
return per bin). The expected-return machinery belongs to a later
stage whose model produces a return distribution.

#### 70.6.10 Consequences — the macro rung survives (supersedes §70.3.5 row 3; wording fixed)

    Q2 or Q4 PASS                        cross-asset exogenous hypothesis supported at that
                                         level; next rung per the §68.12.7 ladder
    Q2 and Q4 fail/INDET, Q1 or Q3 PASS  cross-asset exogenous hypothesis unsupported; the
                                         §68.1 construction may proceed on crypto-native
                                         forecasts, relabelled as such
    All four fail                        the crypto-native + cross-asset PREDICTIVE BRANCH
                                         fails; the scheduled-macro cheap rung remains
                                         eligible on its own pre-registration — NOT the
                                         death of Gen-3 (§68.12.7)
    Q3 PASS                              hierarchical cold-start rung testable (§68.12.6);
                                         Q4 decides only feature inheritance
    Reproduced implementation defect     attempt VOID (void: true, retained, budget
                                         unconsumed — §60.12.4); corrected run is next

Wording fixed throughout: "CROSS-ASSET exogenous hypothesis", never
"exogenous hypothesis" — scheduled macro, filings, and news are
different mechanisms. No post-result discretion.

#### 70.6.11 The new lock plan (supersedes §70.4's file list; voids §70.5's LOCK-G3 lines)

§70.7 pins, at the new lock commit: `g3/timing.py` (NEW — the fetch
hour, t_usable grid, B.1 four timestamps), `g3/features.py` (both
feature tables + the exposure estimator), `g3/models.py`,
`g3/calibration.py`, `g3/sequential.py`, `g3/eval.py`, and
`rcm/eval_ic.py`. `backtest/costs.py` leaves the set (§70.6.9). The
immutability test keeps last-lock-wins semantics, so §70.7's lines
supersede §70.5's mechanically. Deterministic evaluator tests gain the
DEGENERACY PIN: a shared additive term applied to every asset's
forecast leaves the per-date cross-sectional Spearman IC exactly
unchanged (the v1 failure, pinned so it cannot recur). Bootstrap,
INDETERMINATE guards, MDE disclosure, and the seed rule are unchanged
from §70.3 — the seed's lock_commit_hex becomes the §70.7 lock
commit's. **After §70.7: STOP for both delegates; G3-C is a separate
stage.**

### 70.7 The v2 specification lock (2026-09-02) — this commit is the lock commit; supersedes §70.5

Implementation landed exactly as §70.6 froze it: the possession rule
(`g3/timing.py` + `usable_utc`/`pit_view_usable` in the loader, daily
scheduled fetch at 22:00:00+00:00), the B.1 four timestamps, the split
direction/cross-sectional feature tables (9/19 and 7/13), the frozen
exposure estimator (90d OLS slope, min 60 paired days, SE recorded, no
shrinkage), the explicit C/α grids with calendar-boundary expanding
folds and strongest-regularisation tie-break, and Platt fitted
out-of-fold. The §70.4 deterministic evaluator tests remain green and
now include the DEGENERACY PIN: a shared per-date term added to every
asset's forecast leaves each date's cross-sectional Spearman IC
exactly unchanged — bit-for-bit, not approximately. All tests run on
synthetic data; the possession-rule tests read only a public-domain
dev-era CSV through the loader (the established A2 pattern); no return
was read and nothing was fitted on real data.

The pinned code (the §70.5 LOCK-G3 lines are superseded; the
immutability test keeps last-lock-wins semantics and asserts exactly
this set — `backtest/costs.py` left the set per §70.6.9):

```
LOCK-G3 g3/timing.py sha256=d5748592e2115ca07152e65041d650d7f05a26470ae690fc926935cc4783118d
LOCK-G3 g3/features.py sha256=61aee9b90f8d2f6f099eaacdc9e55b84a4666a7b437b6a8a5626b395741f8c78
LOCK-G3 g3/models.py sha256=13ff69575f0833b71ee18e102180dac6336a1756f94556386cc3e310f8697307
LOCK-G3 g3/calibration.py sha256=80a2af64d5f769c3bdbd1b7432687bc00722d0ec8d9292f882641e6a3be842e9
LOCK-G3 g3/sequential.py sha256=dc2e52f4c6751bf92b18ca42c039fa8c80afff88e5f4fd56b4c47e73009acc0d
LOCK-G3 g3/eval.py sha256=76cbc280ab5359563e8d1f68101edcb9046631939978a0d761a73bf796ccad81
LOCK-G3 rcm/eval_ic.py sha256=a1f29dccbbecff7e9969f8f3ccd0c62fc102aba022ae5e17bd8c6c82d8ab0935
```

**Trial pre-registration, re-affirmed unchanged:**

```
G3-TRIAL-1 status=pre-registered attempt_id=1 valid_trial_count=0
```

Seed at the run stage: `int(sha256(lock_commit_hex)[:8], 16)` with
`lock_commit_hex` = THIS commit's hash, printed at run time, never
predicted.

**STOP. Both delegates review §70 (as amended by §70.6) before G3-C
executes. G3-C is a separate stage; it consumes Gen-3 trial 1 of 20
when it runs. No forecast has been fitted on real data; the holdout is
sealed.**

### 70.8 Stage G3-C-SPEC v3 — vintage provenance, then the re-lock (2026-09-02)

**No forecast fitted on real data. No return-based result. No trial
consumed. Gen-3 0 of 20. Holdout sealed.** Closes the last delegate
hold. §70.0–§70.7 unedited; this section supersedes §70.7's lock on
completion (§70.7's LOCK-G3 lines struck as superseded — retained,
never deleted; the immutability test's last-lock-wins semantics make
the strike mechanical).

#### 70.8.0 The defect, the label split, and the pre-registered method — BEFORE any comparison

**The defect, stated exactly.** Today's FRED archive supplies the
CURRENT VINTAGE of each historical observation. §70.6 assigns those
bytes a historical availability time derived from the publisher's
release schedule. If a value was later revised, the model receives a
number that did not exist at the timestamp it is stamped with. The
timestamp arithmetic is flawless; the VALUE is potentially wrong —
vintage leakage. §68.11's `revision_policy` / `vintage_support` fields
anticipated this; §70 did not use them.

**Scope, recorded:** exogenous panel only. BTC/ETH bars and funding
come from the PIT store, are exchange-settled, and do not revise —
M0-dir and M0-xs are unaffected. **Q1 and Q3 are unaffected by this
defect.** Only M1 depends on the resolution.

**Availability quality — the label split.** §69 defined `"observed"`
as genuine historical serving timestamps or a direct publisher feed;
§70.1.1 applied it to a documented schedule, which is weaker evidence.
Restored:

    source_availability_quality = "documented_schedule"
        publication_schedule    = the per-source local-time rule
        usable_time_rule        = "first scheduled acquisition >= publication"
    source_availability_quality = "observed"
        reserved for actual publication/retrieval timestamps we possess
        (held for NO series today)

Every adopted exogenous series is re-labelled `"documented_schedule"`;
fred_VIXCLS keeps `"conservative_assumption"`.

**Per-series verification — no blanket policy.** For each of the six
adopted exogenous series (cboe_VIX, fred_DGS2, fred_DGS10,
fred_DTWEXBGS, fred_SP500, fred_NASDAQ100) the manifest gains:
`historical_value_source`, `production_source`, `revision_policy` (as
documented), `vintage_support`, `publisher_value_equivalence`
(VERIFIED | UNVERIFIED), `verification_method`,
`verification_evidence`. Verification means COMPARISON, not
assertion; methods in order of strength: (1) vintage-archive
comparison (ALFRED as-of vintages); (2) independent publisher archive;
(3) documented non-revision policy plus a spot check. If none is
achievable, UNVERIFIED — equivalence is never inferred from "probably
not revising."

**THE SAMPLE RULE, FIXED NOW, BEFORE ANY COMPARISON:**

- Observation sample, per series: every 10th business day of the
  development window 2020-01-02 → 2024-12-31 under the frozen union
  calendar (`us_market_holidays`), PLUS the first and last business
  day of each calendar year 2020–2024. No FOMC calendar exists in
  this repository, so no event-conditioned dates are added — recorded
  rather than improvised.
- Annual vintage snapshots (method 1), per FRED-archived id (DGS2,
  DGS10, DTWEXBGS, SP500, NASDAQ100, VIXCLS): as-of
  {2021-01-15, 2022-01-14, 2023-01-13, 2024-01-12, 2025-01-15}. Each
  sampled observation date is compared between the EARLIEST snapshot
  containing it and today's archive; revisions BETWEEN consecutive
  snapshots on sampled dates are counted too.
- Tight vintage sub-sample, per id: the first business day of each
  half-year of the development window (10 dates), vintage as-of the
  observation date + 10 calendar days; if the service rejects that
  exact as-of date, the first later date it accepts, recorded.
- Endpoint honesty guard: an ALFRED response is USED only if its CSV
  value-column header carries the requested vintage date (the
  vintage-suffixed column name); otherwise the request is treated as
  failed — a mirror silently serving the current vintage must not
  produce a false "no discrepancies."
- Comparison precision: values compare EQUAL only after rounding both
  sides to the coarser of the two sources' published decimal
  precisions; any difference beyond that is a DISCREPANCY. Missing-in-
  vintage sampled dates are UNCOMPARABLE — counted and reported, never
  discrepancies, never silently dropped.
- **VERIFIED for a series requires, fixed in advance: comparable
  coverage >= 80% of its sampled observation dates AND zero
  discrepancies among the comparable ones.** Anything else ⇒
  UNVERIFIED. A discrepancy rate is reported, never thresholded into a
  pass by judgement after the fact.
- cboe_VIX (no FRED archive of its own file): method 2 — today's CBOE
  close against the EARLIEST ALFRED VIXCLS vintage containing each
  sampled date (an independent publisher-sourced archive with true
  vintages), same precision and coverage rules; CBOE's documented
  revision policy recorded beside it.

**The mixed timing rule (the outcome):**

    publisher_value_equivalence = VERIFIED    ⇒ publisher-time reconstruction
                                                (t_usable = first 22:00Z fetch >= release)
    publisher_value_equivalence = UNVERIFIED  ⇒ the CONSERVATIVE §68.12.1 rule
                                                (publisher + 1 business day, same
                                                local time) — handicapped, never
                                                dropped: costs information, cannot
                                                leak

The manifest records the rule per series; the LOADER enforces it per
series by reading the manifest's verification status (a test asserts a
series marked UNVERIFIED cannot be read at publisher timing).

**Two prohibitions, fixed in advance:** a discrepancy ⇒ UNVERIFIED and
the conservative rule, recorded rather than argued around; and
verification status may NOT be revisited after any Q1–Q4 result — a
series that runs handicapped through Trial 1 stays handicapped for
that trial's record, whatever the outcome.

**Information age (non-blocking, recorded not adopted):** the
two-most-recent-observations convention means the model does not know
whether an observation is 2 or 50 hours old. §69.2 already measured
the staleness profile; expanding the frozen feature set immediately
before Trial 1 is precisely what the lock exists to prevent. Recorded
as a candidate for a later pre-registered improvement in the manifest's
`open_refinements` — not in M0/M1.

Raw vintage downloads land in the gitignored
`data/exogenous/raw/vintages/` (the SP500/NASDAQ100 vintages are
restricted like their parents); their sha256s are recorded in the
execution record. Evidence is quoted from printed output only.

#### 70.8.1 Vintage verification — execution record (2026-09-02; the §70.8.0 rule executed exactly; evidence quoted verbatim from printed output)

Raw vintage downloads (30 annual-snapshot files + the tight-vintage
responses) live in the gitignored `data/exogenous/raw/vintages/`;
their sha256 prefixes appear in the transcript below. The endpoint
honesty guard was live: every used response carries its
vintage-suffixed value column; every fred_SP500 snapshot FAILED the
guard and produced no comparison — recorded as absence of a vintage
archive, never inferred equivalence.

```
sample rule: 132 observation dates (every 10th business day + year edges); tight sub-sample 10 half-year firsts: 2020-01-02 2020-07-01 2021-01-04 2021-07-01 2022-01-03 2022-07-01 2023-01-03 2023-07-03 2024-01-02 2024-07-01
  snapshot DGS2 as-of 2021-01-15: suffix=20210115 sha256=9899a8c536d7a15c.. n=281
  snapshot DGS2 as-of 2022-01-14: suffix=20220114 sha256=1fbeb9ebf3775835.. n=532
  snapshot DGS2 as-of 2023-01-13: suffix=20230113 sha256=b4cfc8db16a1129b.. n=780
  snapshot DGS2 as-of 2024-01-12: suffix=20240112 sha256=bfa3ca5d3e2c6a2a.. n=1030
  snapshot DGS2 as-of 2025-01-15: suffix=20250115 sha256=a30c72f5e25d10df.. n=1272
  snapshot DGS10 as-of 2021-01-15: suffix=20210115 sha256=7f53ad1b1c4ecb2c.. n=281
  snapshot DGS10 as-of 2022-01-14: suffix=20220114 sha256=68fa05d6f160056f.. n=532
  snapshot DGS10 as-of 2023-01-13: suffix=20230113 sha256=5315b5689092f57d.. n=780
  snapshot DGS10 as-of 2024-01-12: suffix=20240112 sha256=337189350522d75c.. n=1030
  snapshot DGS10 as-of 2025-01-15: suffix=20250115 sha256=2f1cfa1c140b3c99.. n=1272
  snapshot DTWEXBGS as-of 2021-01-15: suffix=20210115 sha256=62ecc736cbadb33d.. n=275
  snapshot DTWEXBGS as-of 2022-01-14: suffix=20220114 sha256=5c2f362afbc398b1.. n=524
  snapshot DTWEXBGS as-of 2023-01-13: suffix=20230113 sha256=50a98dd59a6078f5.. n=773
  snapshot DTWEXBGS as-of 2024-01-12: suffix=20240112 sha256=b65c4b2884c5c856.. n=1022
  snapshot DTWEXBGS as-of 2025-01-15: suffix=20250115 sha256=2f3b1ae4491a46da.. n=1269
  snapshot SP500 as-of 2021-01-15: FAILED/guard
  snapshot SP500 as-of 2022-01-14: FAILED/guard
  snapshot SP500 as-of 2023-01-13: FAILED/guard
  snapshot SP500 as-of 2024-01-12: FAILED/guard
  snapshot SP500 as-of 2025-01-15: FAILED/guard
  snapshot NASDAQ100 as-of 2021-01-15: suffix=20210115 sha256=9cd68176453b2b69.. n=284
  snapshot NASDAQ100 as-of 2022-01-14: suffix=20220114 sha256=9315979727114bf8.. n=536
  snapshot NASDAQ100 as-of 2023-01-13: suffix=20230113 sha256=b4317ba07ded905d.. n=786
  snapshot NASDAQ100 as-of 2024-01-12: suffix=20240112 sha256=4a5027ac1d57c88c.. n=960
  snapshot NASDAQ100 as-of 2025-01-15: suffix=20250115 sha256=56952a7bff6912a6.. n=1279
  snapshot VIXCLS as-of 2021-01-15: suffix=20210115 sha256=ae5708f9bfcc5797.. n=284
  snapshot VIXCLS as-of 2022-01-14: suffix=20220114 sha256=5c85b5fdf1238470.. n=536
  snapshot VIXCLS as-of 2023-01-13: suffix=20230113 sha256=fc32b5833900a505.. n=792
  snapshot VIXCLS as-of 2024-01-12: suffix=20240112 sha256=e1308610144513be.. n=1049
  snapshot VIXCLS as-of 2025-01-15: suffix=20250115 sha256=c5ca4ba9d2fbae91.. n=1298

== fred_DGS2 (ALFRED id DGS2)
   sampled 132  comparable 132  coverage 100.0%  discrepancies 0  between-snapshot revisions 0  tight: 10 comparable, 0 discrepancies
   VERDICT fred_DGS2: VERIFIED
== fred_DGS10 (ALFRED id DGS10)
   sampled 132  comparable 132  coverage 100.0%  discrepancies 0  between-snapshot revisions 0  tight: 10 comparable, 0 discrepancies
   VERDICT fred_DGS10: VERIFIED
== fred_DTWEXBGS (ALFRED id DTWEXBGS)
   sampled 132  comparable 131  coverage 99.2%  discrepancies 131  between-snapshot revisions 106  tight: 10 comparable, 10 discrepancies
   DISC 2020-01-02 vintage(asof 2021-01-15)=115.0169 today=114.9745
   DISC 2020-01-16 vintage(asof 2021-01-15)=115.0544 today=115.0006
   DISC 2020-01-31 vintage(asof 2021-01-15)=115.7850 today=115.7345
   DISC 2020-02-14 vintage(asof 2021-01-15)=116.4479 today=116.4223
   DISC 2020-03-02 vintage(asof 2021-01-15)=116.9457 today=116.8144
   REV  2020-01-02: asof 2021-01-15=115.0169 -> asof 2022-01-14=114.9755
   REV  2020-01-16: asof 2021-01-15=115.0544 -> asof 2022-01-14=114.9986
   REV  2020-01-31: asof 2021-01-15=115.7850 -> asof 2022-01-14=115.7337
   REV  2020-02-14: asof 2021-01-15=116.4479 -> asof 2022-01-14=116.4170
   REV  2020-03-02: asof 2021-01-15=116.9457 -> asof 2022-01-14=116.8165
   TDISC 2020-01-02 vintage(suffix 20200112)=115.0172 today=114.9745
   TDISC 2020-07-01 vintage(suffix 20200711)=120.4595 today=120.0808
   TDISC 2021-01-04 vintage(suffix 20210114)=111.5465 today=111.2075
   TDISC 2021-07-01 vintage(suffix 20210711)=113.0361 today=112.7400
   TDISC 2022-01-03 vintage(suffix 20220113)=115.4344 today=115.4101
   uncomparable (first 3): ['2021-12-31']
   VERDICT fred_DTWEXBGS: UNVERIFIED
== fred_SP500 (ALFRED id SP500)
   sampled 132  comparable 0  coverage 0.0%  discrepancies 0  between-snapshot revisions 0  tight: 0 comparable, 0 discrepancies
   uncomparable (first 3): ['2020-01-02', '2020-01-16', '2020-01-31']
   VERDICT fred_SP500: UNVERIFIED
== fred_NASDAQ100 (ALFRED id NASDAQ100)
   sampled 132  comparable 132  coverage 100.0%  discrepancies 19  between-snapshot revisions 18  tight: 9 comparable, 2 discrepancies
   DISC 2020-01-02 vintage(asof 2021-01-15)=8872.219 today=8872.220
   DISC 2020-01-31 vintage(asof 2021-01-15)=8991.512 today=8991.510
   DISC 2020-02-14 vintage(asof 2021-01-15)=9623.582 today=9623.580
   DISC 2020-03-02 vintage(asof 2021-01-15)=8877.977 today=8877.980
   DISC 2020-03-16 vintage(asof 2021-01-15)=7020.375 today=7020.380
   REV  2020-01-02: asof 2024-01-12=8872.219 -> asof 2025-01-15=8872.220
   REV  2020-01-31: asof 2024-01-12=8991.512 -> asof 2025-01-15=8991.510
   REV  2020-02-14: asof 2024-01-12=9623.582 -> asof 2025-01-15=9623.580
   REV  2020-03-02: asof 2024-01-12=8877.977 -> asof 2025-01-15=8877.980
   REV  2020-03-16: asof 2024-01-12=7020.375 -> asof 2025-01-15=7020.380
   TDISC 2020-01-02 vintage(suffix 20200112)=8872.219000 today=8872.220
   TDISC 2020-07-01 vintage(suffix 20200711)=10279.246000 today=10279.250
   VERDICT fred_NASDAQ100: UNVERIFIED
== fred_VIXCLS (ALFRED id VIXCLS)
   sampled 132  comparable 132  coverage 100.0%  discrepancies 0  between-snapshot revisions 0  tight: 10 comparable, 0 discrepancies
   VERDICT fred_VIXCLS: VERIFIED
== cboe_VIX (method 2: CBOE file vs earliest ALFRED VIXCLS vintage)
   sampled 132  comparable 132  coverage 100.0%  discrepancies 0
   VERDICT cboe_VIX: VERIFIED

SUMMARY fred_DGS2:VERIFIED | fred_DGS10:VERIFIED | fred_DTWEXBGS:UNVERIFIED | fred_SP500:UNVERIFIED | fred_NASDAQ100:UNVERIFIED | fred_VIXCLS:VERIFIED | cboe_VIX:VERIFIED
```

**Outcomes under the pre-registered rule (no judgement applied):**

- fred_DGS2 — **VERIFIED** (132/132 comparable, 0 discrepancies,
  0 between-snapshot revisions, tight 10/10 clean).
- fred_DGS10 — **VERIFIED** (same, clean).
- cboe_VIX — **VERIFIED** by method 2 (132/132 against the earliest
  ALFRED VIXCLS vintages, 0 discrepancies).
- fred_VIXCLS (cross-check, not adopted) — VERIFIED on the record; its
  timing stays the conservative mirror regardless.
- fred_DTWEXBGS — **UNVERIFIED**: 131 of 131 comparable dates
  DISCREPANT (magnitudes ~0.03–0.4 index points), 106 between-snapshot
  revisions, tight 10/10 discrepant — the H.10 broad-dollar history is
  recomputed on re-weighting. The §69 unconditional map's DTWEXBGS
  rows were computed from current-vintage values; recorded as a known
  limitation of that descriptive record.
- fred_SP500 — **UNVERIFIED**: 0% comparable — no ALFRED vintage
  archive passes the honesty guard, no independent archive held.
- fred_NASDAQ100 — **UNVERIFIED**: 19/132 discrepancies + 18
  between-snapshot revisions + tight 2/9, all at the third decimal
  (e.g. 2020-01-02: vintage 8872.219 vs today 8872.220; ALFRED's
  serving precision changed in the 2025 vintage). These are plausibly
  representational — and §70.8.0 prohibits exactly that judgement
  after the fact, so UNVERIFIED stands and the conservative rule
  applies. If a delegate later wants a precision-aware comparison, it
  requires its own pre-registered rule BEFORE re-running; the status
  cannot change after any Q result regardless (§70.8.0).

**Mixed timing applied and enforced:** the loader now BUILDS its
per-series source rules from the manifest's
`publisher_value_equivalence` — VERIFIED ⇒ publisher rule (DGS2,
DGS10, cboe_VIX); UNVERIFIED ⇒ publisher + 1 business day, same local
time (DTWEXBGS → offset 2 @ 16:15 America/New_York; SP500, NASDAQ100 →
offset 1 @ 16:00 America/New_York); fred_VIXCLS keeps the mirror. The
quality label split is applied (all six adopted series
`"documented_schedule"` with `publication_schedule` and
`usable_time_rule`; NO series `"observed"`). Tests pin: per-series
enforcement (an UNVERIFIED series is unreadable at publisher timing —
checked at the 22:00Z fetch on the publisher's release date),
verification-block presence, the label split, and the offsets. The
information-age candidate is recorded in `open_refinements`, not in
M0/M1.

#### 70.8.2 The v3 re-lock (2026-09-02) — this commit is the lock commit; supersedes §70.7

§70.7's LOCK-G3 lines are struck as superseded — retained above,
never deleted; last-lock-wins makes the strike mechanical. The set
GROWS: the loader (whose per-series timing is now load-bearing for
M1's integrity) and the manifest (the verification statuses the loader
reads) are pinned beside everything §70.7 held. g3/ code is unchanged
from §70.7 (hashes below match §70.7's for the unchanged files).

```
LOCK-G3 g3/timing.py sha256=d5748592e2115ca07152e65041d650d7f05a26470ae690fc926935cc4783118d
LOCK-G3 g3/features.py sha256=61aee9b90f8d2f6f099eaacdc9e55b84a4666a7b437b6a8a5626b395741f8c78
LOCK-G3 g3/models.py sha256=13ff69575f0833b71ee18e102180dac6336a1756f94556386cc3e310f8697307
LOCK-G3 g3/calibration.py sha256=80a2af64d5f769c3bdbd1b7432687bc00722d0ec8d9292f882641e6a3be842e9
LOCK-G3 g3/sequential.py sha256=dc2e52f4c6751bf92b18ca42c039fa8c80afff88e5f4fd56b4c47e73009acc0d
LOCK-G3 g3/eval.py sha256=76cbc280ab5359563e8d1f68101edcb9046631939978a0d761a73bf796ccad81
LOCK-G3 rcm/eval_ic.py sha256=a1f29dccbbecff7e9969f8f3ccd0c62fc102aba022ae5e17bd8c6c82d8ab0935
LOCK-G3 tools/g3_exogenous_loader.py sha256=b9716df0924a14ba16b525097160789bb319fe1982e5ecf744cf2b0db5fb8ff7
LOCK-G3 data/exogenous/MANIFEST.json sha256=24b93feb3eb98b2f45e6ffe65e5f2c79a2c58e706408fc0d82968bcb59a55eff
```

**Trial pre-registration, re-affirmed unchanged:**

```
G3-TRIAL-1 status=pre-registered attempt_id=1 valid_trial_count=0
```

Seed at the run stage: `int(sha256(lock_commit_hex)[:8], 16)` with
`lock_commit_hex` = THIS commit's hash, printed at run time, never
predicted.

**STOP. Both delegates review §70.8. G3-C remains a separate stage; it
consumes Gen-3 trial 1 of 20 when it runs. No return was read beyond
the pre-registered vintage comparisons of exogenous values; no
forecast was fitted on real data; the holdout is sealed.**

### 70.9 Stage G3-C-SPEC v4 — vintage integrity (2026-09-02; §§70.9.1–70.9.4 appended before any reconstruction is attempted)

**No forecast fitted on real data. No return-based result. No trial
consumed. Gen-3 0 of 20. Holdout sealed.** §70.0–§70.8 unedited; this
section supersedes §70.8's fallback and, on completion, §70.8.2's
lock.

#### 70.9.1 The withdrawn claim

§70.8 stated that serving a current-vintage value at
publisher-time-plus-one-business-day "cannot leak." **Withdrawn as
false — and the §70.8.1 audit itself supplied the proof:**

    DTWEXBGS 2020-01-02
      2020 vintage (what was knowable then) : 115.0172
      2021 vintage                          : 115.0169
      today's 2026 archive                  : 114.9745  <- what the loader holds

    §70.8 fallback: serve 114.9745 stamped 2020-01-03 22:00 — a 2026
    recomputation delivered to a January 2020 decision, one day late.
    Still the wrong number. Delay fixes WHEN a value was knowable;
    vintage fixes WHICH value was knowable.

**Frozen rule, recorded verbatim:**

> A timestamp delay cannot substitute for vintage integrity. For any
> series whose current historical values are not proven equivalent to
> historical publisher values, current-vintage observations may not be
> exposed at historical decision times. Such a series must either be
> reconstructed from point-in-time vintages — each value timestamped
> no earlier than that vintage's actual availability — or be
> UNAVAILABLE to the model. No imputation, zero-filling,
> current-vintage substitution, proxy substitution, or replacement
> factor is permitted.

For the record: the §70.8 audit produced the evidence that condemned
the §70.8 fallback. That is the apparatus working, not failing.

#### 70.9.2 Three provenance states, replacing the binary

    VALUE_EQUIVALENT   current archive proven equivalent to historical
                       vintages ⇒ served at historical possession times
                       (t_usable = first 22:00Z fetch >= publisher release)
    PIT_RECONSTRUCTED  not equivalent, but historical as-of vintages
                       reconstructed ⇒ serve the actual PIT vintage
                       (§70.9.3), NEVER the current archive
    UNAVAILABLE        neither ⇒ the feature does not exist for Trial 1;
                       the series is unreadable by the model at any timing

Carried forward from §70.8.1: fred_DGS2, fred_DGS10, cboe_VIX =
VALUE_EQUIVALENT (fred_VIXCLS likewise on the record; it stays a
cross-check under the mirror rule and is not an M1 component).
fred_DTWEXBGS, fred_NASDAQ100, fred_SP500 = pending §70.9.3.

#### 70.9.3 The reconstruction and inclusion algorithm — FROZEN BEFORE EXECUTION

**The reconstructed object** is a revision store, not a value table:
rows `(observation_date, vintage_available_time, value)`, and the PIT
query at each historical decision instant t is: the row for each
observation with the LATEST `vintage_available_time <= t` — never
today's value for that observation date.

**Vintage timestamping, frozen (with the service's semantics recorded
honestly):** the §70.8.1 runs established that ALFRED's value-column
suffix ECHOES the requested as-of date (a Sunday request returns a
Sunday suffix), so true internal vintage dates are not observable
through this endpoint; what IS proven by the honesty guard is that the
response contains the data as known on the requested as-of date.
Therefore: a value first appearing (or changing) in the as-of-V
response is stamped available from **22:00:00 UTC on V + 1 calendar
day** — the first scheduled fetch strictly after the as-of date. No
intraday time is invented; the stamp is conservative and delay-only
(§68.11 anti-lookahead).

**Enumeration, frozen:** as-of requests for every BUSINESS DAY (frozen
union calendar) from 2019-12-16 through 2024-12-31, ascending, per
series — dense enough that every decision date in
2020-01-01 → 2024-12-31 has a preceding vintage, with pre-window
as-ofs so the first decision dates resolve. The store keeps DIFFS: a
row is written when an observation first appears or its value changes
versus the previous as-of state; each row carries the as-of date and
the response's sha256. A failed request or a response failing the
§70.8.0 honesty guard contributes nothing (never interpolated,
forward-filled, or synthesised).

**Integrity and coverage, fixed now:** every decision date in
2020-01-01 → 2024-12-31 must resolve to some vintage row
(equivalently: the earliest stored vintage availability is
<= 2020-01-01T22:00Z and the store is non-empty through the window);
the honesty guard applies to every response used; **anything less than
100% ⇒ UNAVAILABLE.** No partial-coverage series enters M1.

**Store locations by licence (§68.11.3):** the DTWEXBGS store
(public-domain Fed data) is TRACKED under `data/exogenous/`; the
NASDAQ100 store (redistribution-restricted values) lives in the
gitignored `data/exogenous/raw/`, with its sha256 pinned in the
re-lock and checked locally by the immutability test — the same
tracked/untracked split §68.11.3 froze for the raw archives.

**fred_NASDAQ100 — no after-the-fact tolerance:** §70.8.1's
third-decimal discrepancies are NOT judged representational; the
reconstruction route is taken (preferred: requires no new rule). The
alternative — a precision-aware comparison at the source's officially
documented historical precision — would require its own ledger entry
BEFORE re-running, and is not taken here.

**fred_SP500 — no substitute may appear:** §70.8.1 found 0% vintage
coverage (no archive passes the honesty guard, none held). No frozen
acquisition/verification protocol for a legitimate historical archive
exists, so §70.9.3 CANNOT be executed for it ⇒ **UNAVAILABLE**, by the
algorithm, before any fetch. No substitute equity index may be
introduced in v4 — swapping an index changes the hypothesis and would
need its own pre-registration.

**The contraction rule — frozen, mechanical:**

> For each pre-registered M1 exogenous component, attempt the
> procedure above. If it yields valid PIT observations meeting the
> integrity and coverage requirements, the component REMAINS.
> Otherwise it becomes UNAVAILABLE and is REMOVED from Trial-1 M1. No
> zero-filling, current-vintage substitution, proxy substitution, or
> replacement factor.

Whatever falls out is the result. **This is not feature selection:**
no forecast has been fitted and no Q result seen; it is the discovery
that a sensor lacks historical records. Recorded because the two look
identical from outside and are opposite in epistemics. If the
contraction changes M1's dimensionality, the feature specification is
re-locked with the reduced family list stated explicitly (already
knowable now: fred_SP500 is UNAVAILABLE, so `sp500_ret_1d` leaves
M1-dir and `name_int_spx` leaves M1-xs whatever else happens;
DTWEXBGS and NASDAQ100 depend on their §70.9.3 outcomes).

#### 70.9.4 The pinning tests — the exact bug, made impossible

    given   historical vintage = 110.10
            current archive    = 110.35
            decision_time      = historical date + 1 business day
    assert  110.35 is NEVER returned
            (110.10 if its PIT vintage is held; otherwise MISSING)

Plus: a series marked UNAVAILABLE is unreadable by the model reader at
any timing; a series marked PIT_RECONSTRUCTED reads ONLY from the
revision store (a test proves the current-archive path is never
touched for it); VALUE_EQUIVALENT retains §70.8's behaviour. The
model reader (`pit_view_usable` and the run-stage feature path)
dispatches on the manifest's provenance state, so the enforcement is
structural.

**Execution follows in §70.9.5 (evidence verbatim) and §70.9.6 (the
re-lock superseding §70.8.2, last-wins). Trial 1 remains
pre-registered, attempt 1, valid_trial_count 0; the run seed derives
from the NEW lock commit hash, printed at run time, never predicted.**

#### 70.9.5 Reconstruction executed — evidence verbatim (2026-09-02; the §70.9.3 algorithm run exactly as frozen)

Build: business-daily as-of requests, 2019-12-16 → 2024-12-31, per
series, honesty guard live on every response; diff store; stamps
22:00:00 UTC on as-of + 1 day. **One mechanical normalization,
recorded honestly:** the first build diffed on SERVED STRINGS and
captured representation flaps (e.g. `8570.336` vs `8570.336000`,
numerically identical); since any numeric change is also a string
change, the string-diff store is a strict superset, and a single
post-pass kept only rows whose value differs from the previous kept
row under EXACT Decimal equality — nothing numeric was lost, no
refetch was needed, both raw and normalized row counts are reported.

```
== fred_DTWEXBGS
   as-of responses used 1258  failed as-of dates 0 []
   raw diff rows 4198 -> normalized rows 4198 (representation flaps removed by exact Decimal equality; every numeric change is also a string change, so nothing numeric was lost)
   distinct observations 1268  REAL revision rows 2930  revision |magnitude| median 0.0228 max 1.3293
   earliest vintage_available 2019-12-17T22:00:00+00:00  latest 2024-12-31T22:00:00+00:00
   coverage: earliest available <= 2020-01-01T22:00Z: True  => 100% of decision dates resolve: True
   store file data\exogenous\vintage_store_fred_DTWEXBGS.csv  sha256=94cd90ee575b6d63fd6303eb3e31c81a84a2cf0d19a45cba1c8bc795f550603b
   VERDICT fred_DTWEXBGS: PIT_RECONSTRUCTED

== fred_NASDAQ100
   as-of responses used 1256  failed as-of dates 2 ['2021-05-25', '2021-06-17']
   raw diff rows 4699 -> normalized rows 1720 (representation flaps removed by exact Decimal equality; every numeric change is also a string change, so nothing numeric was lost)
   distinct observations 1279  REAL revision rows 441  revision |magnitude| median 0.002000 max 80.210
   earliest vintage_available 2019-12-17T22:00:00+00:00  latest 2025-01-01T22:00:00+00:00
   coverage: earliest available <= 2020-01-01T22:00Z: True  => 100% of decision dates resolve: True
   store file data\exogenous\raw\vintage_store_fred_NASDAQ100.csv  sha256=ac1ce1bd6bdcbadf5740992bf092cf2bf440b89f33629e6dbb6e9006dc21be74
   VERDICT fred_NASDAQ100: PIT_RECONSTRUCTED

fred_SP500: no vintage archive passes the honesty guard (70.8.1); no frozen acquisition protocol exists; the 70.9.3 procedure cannot be executed  => VERDICT fred_SP500: UNAVAILABLE
```

**Verdicts, by the frozen rule (no judgement applied):**
`fred_DTWEXBGS` → **PIT_RECONSTRUCTED** — and the §70.9.1 example is
now served correctly: at any decision from 2020-01-07 22:00Z the store
returns **115.0172** for obs 2020-01-02 (the true 2020 vintage), never
today's 114.9745; the store carries **2,930 real revision rows**
(median |Δ| 0.0228, max 1.3293) across 1,268 observations — the H.10
history is rewritten continually, exactly as §70.9.1 charged.
`fred_NASDAQ100` → **PIT_RECONSTRUCTED** — 441 real revisions across
1,279 observations with |Δ| up to **80.21 index points**: the §70.8.1
third-decimal reading UNDERSTATED the problem; genuine large next-day
close corrections exist, so the refusal to wave them off as
representational was not merely procedural. Two failed as-of dates
(2021-05-25, 2021-06-17) recorded; delay-only by construction.
`fred_SP500` → **UNAVAILABLE** — the §70.9.3 procedure cannot be
executed (no archive passes the guard, no acquisition protocol
frozen). By the mechanical contraction rule `sp500_ret_1d` leaves
M1-dir (now 18 features) and `name_int_spx` leaves M1-xs (now 12);
the equities family is NASDAQ100 alone; no substitute index appears.
This is data availability, not feature selection: no forecast has
been fitted and no Q result seen.

Implementation landed: manifest three-state `vintage_provenance` with
store paths/hashes and reconstruction evidence; the model reader
(`pit_view_usable`) dispatches structurally — VALUE_EQUIVALENT serves
the current archive at t_usable, PIT_RECONSTRUCTED serves ONLY the
revision store (`pit_view_reconstructed`; a test proves the
current-archive path is never touched), UNAVAILABLE raises
`SeriesUnavailable` at any timing. §70.9.4's pins are green, including
the exact-bug case on real data (115.0172 served, 114.9745 never;
MISSING before the vintage's availability; a later decision serves a
later vintage) and store-integrity sweeps (no flap rows, stamps
exactly as-of + 1 day at 22:00:00 UTC, earliest availability precedes
the first decision date).

#### 70.9.6 The v4 re-lock (2026-09-02) — this commit is the lock commit; supersedes §70.8.2

§70.8.2's LOCK-G3 lines are struck as superseded — retained, never
deleted; last-lock-wins makes the strike mechanical. The set grows to
eleven: the two revision stores join (the NASDAQ100 store is
untracked-restricted per §68.11.3 and is hash-checked locally, like
the raw archives); `g3/features.py`, the loader and the manifest are
re-pinned as changed; everything else is unchanged from §70.8.2.

```
LOCK-G3 g3/timing.py sha256=d5748592e2115ca07152e65041d650d7f05a26470ae690fc926935cc4783118d
LOCK-G3 g3/features.py sha256=ff4f34d2c30901728e5578f8ca0995a699d9c53792d8b7179c22f05104596201
LOCK-G3 g3/models.py sha256=13ff69575f0833b71ee18e102180dac6336a1756f94556386cc3e310f8697307
LOCK-G3 g3/calibration.py sha256=80a2af64d5f769c3bdbd1b7432687bc00722d0ec8d9292f882641e6a3be842e9
LOCK-G3 g3/sequential.py sha256=dc2e52f4c6751bf92b18ca42c039fa8c80afff88e5f4fd56b4c47e73009acc0d
LOCK-G3 g3/eval.py sha256=76cbc280ab5359563e8d1f68101edcb9046631939978a0d761a73bf796ccad81
LOCK-G3 rcm/eval_ic.py sha256=a1f29dccbbecff7e9969f8f3ccd0c62fc102aba022ae5e17bd8c6c82d8ab0935
LOCK-G3 tools/g3_exogenous_loader.py sha256=84b4698a9dfde4a609c9f05b8eb7c30afdc6e94f5e4550ec69e0f864f4f7277a
LOCK-G3 data/exogenous/MANIFEST.json sha256=a63225c4bfedecd2c73c4d071f422d49cf9b799c94de9506c664f64247b6cb41
LOCK-G3 data/exogenous/vintage_store_fred_DTWEXBGS.csv sha256=94cd90ee575b6d63fd6303eb3e31c81a84a2cf0d19a45cba1c8bc795f550603b
LOCK-G3 data/exogenous/raw/vintage_store_fred_NASDAQ100.csv sha256=ac1ce1bd6bdcbadf5740992bf092cf2bf440b89f33629e6dbb6e9006dc21be74
```

**Trial pre-registration, re-affirmed unchanged:**

```
G3-TRIAL-1 status=pre-registered attempt_id=1 valid_trial_count=0
```

Seed at the run stage: `int(sha256(lock_commit_hex)[:8], 16)` with
`lock_commit_hex` = THIS commit's hash, printed at run time, never
predicted.

**STOP. Both delegates review §70.9. G3-C remains a separate stage; it
consumes Gen-3 trial 1 of 20 when it runs. No return was read beyond
the pre-registered vintage reconstruction of exogenous values; no
forecast was fitted on real data; the holdout is sealed.**

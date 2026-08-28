# Stage 5 — Majors reconstruction, fee-isolated, three-way train comparison

Tests whether concentrating on liquid majors improves the strategy, and
isolates the USDC fee advantage as a controlled variable rather than a
confound.

§0 of `STAGE2_PROMPT.md` remains in force.

**This spends trials and requires a budget expansion — see §6. It does NOT
touch validate or holdout.**

---

## 0. The design

The insight this stage is built on: **the majors universe can be backtested
on USDT price history even though USDC perps did not exist before 2024**,
because the majors traded as USDT pairs the whole time. The USDC universe is a
*name list*; running it on real USDT prices is a legitimate reconstruction,
not fabricated data.

Three configs, all on **train 2020–2023**, each isolating one effect:

| Config | Universe | Fees | Isolates |
|---|---|---|---|
| **A** (frozen, §19.5) | full USDT (~800) | USDT 0.05% | baseline |
| **B1** | top-15 majors, PIT | USDT 0.05% | the **universe** change alone |
| **B2** | top-15 majors, PIT | USDC 0.036% | the **fee** change, same trades as B1 |

A→B1 measures majors-only. B1→B2 measures the venue. No confound: B1 and B2
are the identical position series at two fee schedules.

---

## 1. THE SURVIVORSHIP TRAP — the one thing that invalidates everything

The USDC universe is defined by names that have a USDC perp **today**.
Dropping that list into 2020 imports hindsight: every coin that pumped and
died in 2020–2023 is excluded because dead coins never got a USDC listing.
That makes the reconstruction look **better than reality** and is exactly the
bias the point-in-time store exists to prevent.

**The fix, mandatory:**

- The B universe is selected **point-in-time by liquidity** — top 15 by median
  quote volume *as of each rebalance date*, through `PITView`, using the
  survivorship-safe symbol list (delisted names included on the dates they
  traded).
- "Has a USDC pair today" is used **only** to confirm the strategy is
  buildable live — it is **not** a historical filter and must not touch
  universe selection on any past date.
- Assert in code that B's universe on any date is computed from bars with
  `close_time <= as_of` and from the full symbol list, never from a
  present-day name set.

If B's universe is ever built from today's USDC list applied to a past date,
the result is void. State in `NOTES` how this was prevented, with the assertion
that enforces it.

## 2. Fee handling — the controlled variable

B1 and B2 are the **same run** at two fee schedules. Implement as one position
series costed twice, not two independent backtests — otherwise a stray RNG or
ordering difference contaminates the comparison the whole stage is designed
around.

- B1 fees: USDT taker 0.05% (matches A)
- B2 fees: USDC taker 0.036% (with BNB discount; state the assumption)
- Maker is **not** used. §2e §4 stands: no maker result is reportable until a
  fill-probability model exists. USDC's 0% maker is a live-execution question,
  not a backtest one.

## 3. Funding — state the proxy

USDC funding did not exist in 2020–2023. B uses **USDT funding** for those
names, which §31.1 measured at 0.86× the USDC tail — close, and the direction
(USDT slightly richer) means the reconstruction is if anything generous to
funding, not stingy. Label it a proxy everywhere it appears; do not present it
as USDC funding.

## 4. Configuration

Identical to frozen except universe and fees:

```
lookback = 14, skip = 0          (frozen)
N = 10, k = 5                     (UNCHANGED — this is the pure majors test,
                                   no middle-rank bet; do not widen k)
universe = top 15 by median quote volume, point-in-time
vol_target = 0.20, max_gross = 3.0, min_gross = 0.0
beta-neutral, +1min fill, 5bps slippage
capital = 400
```

**k stays at 5.** Widening k was considered and rejected: §24.1 showed
ranks 6–11 flip sign by year, so a wider k adds a middle-rank bet the user
explicitly chose not to make. If the top-15 universe cannot support k=5 at the
floor (15 names, k=5 → smallest position must clear $5), report it and stop;
do not silently shrink k.

## 5. The comparison

Paired bootstrap on **daily** PnL differences — same days, so common market
noise cancels, the Stage 3d method. Block length recomputed for each
difference series (do not reuse a prior figure).

Report, each with 90% CI:

1. **B1 − A** — the universe effect. This is the hypothesis.
2. **B2 − B1** — the fee effect. Expected small and positive by construction.
3. **B2 − A** — the combined proposition.

Plus, for each of A, B1, B2 on train: Sharpe with bootstrap CI, per-year
table in the §19.3 format, price/funding split, fee drag, turnover, realised
leverage and beta, max drawdown, active days.

### 5.1 The reading — write into NOTES §33 before running

| B1 − A (universe effect) | Reading |
|---|---|
| CI entirely above zero | Majors-only helps out of the noise. Strong basis to validate B |
| CI straddles zero, point estimate positive | Consistent with helping, not established — the honest most-likely outcome |
| CI entirely below zero | Majors-only *hurts* on train — the concentration removed more signal than tail-noise. Do not validate B |

And separately: whether B's per-year price PnL avoids the uncapped config's
+163 → +110 → +30 → **−37** collapse. If B's price PnL stays positive through
2023 where A's went negative, that is the mechanism working even if the paired
CI is wide.

Do not adjust the reading after seeing intervals.

## 6. Trial budget — expand deliberately

This stage is **2 trials**: B1 and B2 (B2 is a re-costing of B1, but log both).
A already exists and is not re-run.

Current budget is 7 of 20. **Expand to 25**, logged in `NOTES` §33 with:
today's date, the reason (a distinct strategy-family question the original 20
did not anticipate), and the Deflated Sharpe recomputed at the new count for
every prior reported result. The expansion is a recorded decision, not a
silent drift — state the new denominator before running.

After this stage: **9 of 25** spent. Validate and holdout still untouched.

## 7. What this does and does not establish

- It tests the **universe** hypothesis on real history, cleanly. It does not
  make the signal non-decaying — if majors-momentum faded too, B will show it.
- The reconstruction is **generous** on two axes (survivorship handled but
  USDC-list membership still correlates with having-survived-to-2024; funding
  proxy slightly rich). A marginal B result should be read as an upper bound.
- USDC **execution** remains a separate live decision, answered by measuring
  post-only fill rates in paper trading, not by this backtest.
- One year of validate still cannot **confirm** whichever config wins — MDE
  ~1.65. It can refute. That is unchanged.

## 8. Order of work

1. §5.1 reading and §6 budget expansion into `NOTES` §33, dated, **before**
   running
2. Build B's point-in-time top-15 universe; assert survivorship safety (§1)
3. Run B1; re-cost to B2 from the identical position series
4. Paired bootstraps (§5); per-config tables
5. State which branch fired; report B's per-year price PnL vs A's
6. **Stop. Report. Do not run validate or holdout.**

## 9. Acceptance

- §33 reading and budget expansion recorded before any run
- B universe point-in-time, survivorship assertion in code and described
- B1/B2 a single position series costed twice, not two runs
- Funding labelled a USDT proxy throughout
- k = 5, unchanged; if infeasible at the floor, reported not silently shrunk
- Three paired bootstraps with CIs; per-config per-year tables
- Branch stated plainly; B's 2023 price PnL compared to A's −37
- DSR recomputed at 25 for all prior results
- Budget **9 of 25**; validate and holdout untouched

## 10. Do not

- Build B's universe from today's USDC name list applied to past dates
- Run B1 and B2 as independent backtests
- Widen k, or shrink it silently if k=5 is infeasible
- Report a maker-mode figure
- Present USDT funding as USDC funding
- Expand the budget without recording the new denominator and recomputing DSR
- Touch validate or holdout

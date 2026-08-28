# Stage 6 — Validate B on 2024 (ONE trial, the real test)

The first out-of-sample look. Config **B at 20% vol** (top-15 PIT majors) on
**2024**. One config, one year, one look. Pre-registered rule below is fixed
before the run and not adjusted after.

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 0. What this is

- **Config:** B1/B2 — top-15 point-in-time majors, `lookback=14`, `skip=0`,
  N=10, k=5, 20% vol target, 3× cap, beta-neutral, +1min fill, 5bps slippage,
  $400. Run at **both** fee schedules (USDT 0.05% and USDC 0.036%), reported
  together as before.
- **Window:** 2024 only. Not 2020–2023 (train, used). Not 2025+ (holdout,
  sealed).
- **Cost:** ONE trial. Budget 9 → 10 of 25.
- **What it answers:** does majors-momentum survive out of sample.
- **What it cannot do:** confirm. MDE on one year is ~1.65 Sharpe (§28.4). It
  can **refute**. A non-negative, non-breaching result is "not refuted," which
  is the best obtainable outcome and is **not** proof.

Log the trial before running (`status: started`, commit hash). An errored run
still spends it.

---

## 1. THE DRIFT ADJUSTMENT — the number that must not be forgotten

§36.4 found **41% of B's train Sharpe is drift**, which does not repeat out of
sample. So even if B's real momentum holds perfectly into 2024, expected
validate Sharpe is:

```
1.114 (train) × (1 − 0.41) ≈ 0.66     before any decay at all
```

**A validate Sharpe of 0.5–0.7 is therefore CONSISTENT WITH SUCCESS, not
failure.** Any threshold that demands ~1.0 would reject a working strategy.
This adjustment is baked into §2 below and must not be quietly dropped.

---

## 2. THE RULE — write into NOTES §37 before running, do not adjust after

B is graded on three axes the ledger identified, not on headline Sharpe alone.
Each is a number, fixed now.

### 2.1 Tier 1 — hard gates. Any one failing = refuted.

| # | Test | Refuted if | Why |
|---|---|---|---|
| G1 | **Price PnL sign** | 2024 price PnL **< 0** | B is 77% price. Negative price PnL means the momentum leg is gone, drift or no drift. The single most important gate |
| G2 | **Drawdown** | max DD **> 30%** on the USDT-fee run | Breaches the pre-registered kill switch. **This is not a failed test — it is the parked vol question answering itself:** B at 20% vol is too fragile to run. Record it as decisive, not as noise |
| G3 | **Sharpe floor** | Sharpe **< 0.30** at USDC fees | The project's standing stop threshold. Below it, even "not refuted" does not justify the holdout |

### 2.2 Tier 2 — mechanism checks. Reported, inform the read, don't auto-refute.

- **Structural invariants** (from §30.1 train ranges): realised beta within
  ±0.15; realised gross leverage in a sane band; dollar-tilt identity to 1e-9;
  active-days fraction ≥ 80%. A breach here means the harness behaved
  differently OOS — investigate before trusting anything else.
- **Price/funding split.** Train B was 77/23. If 2024 flips to funding-carried,
  B has become A — note it explicitly.
- **Drift check on 2024.** Run the demeaned decomposition on the 2024 result.
  If the 2024 drift fraction is far above train's 41%, the "momentum" that
  survived is mostly artifact — a caveat on any positive read.

### 2.3 The reading

| Outcome | Meaning |
|---|---|
| All Tier 1 pass, Sharpe 0.5–0.7, price-driven | **Best realistic outcome.** Consistent with momentum surviving. Justifies the holdout — but see §4 first |
| All Tier 1 pass, Sharpe > 0.7 | Stronger than the drift-adjusted expectation. Genuinely encouraging, still not confirmation |
| G1 fails (price PnL < 0) | Momentum did not survive. B refuted. Do not proceed to holdout |
| G2 fails (DD > 30%) | B at 20% vol is unrunnable. The vol question is answered: B needs lower vol, which is a **new config** and a **new validate** — the holdout stays sealed |
| G3 fails (Sharpe < 0.3) | Not refuted but too weak to spend the holdout on |

Write all of this before the run. Nothing here is adjusted after seeing 2024.

---

## 3. Report

1. Every Tier 1 gate, pass/fail, both fee schedules
2. Tier 2 invariants and the price/funding split
3. Per-2024 breakdown: price PnL, funding PnL, long/short split, Sharpe with
   bootstrap CI, max drawdown with date, turnover, realised leverage and beta,
   active days
4. 2024 drift fraction vs train's 41%
5. **Who is paying, for 2024** — did the §36.6 momentum-payer story hold, or
   did the composition move?

---

## 4. After the result — STOP, do not chain to holdout

Whatever 2024 shows, **this stage ends at the report.** The holdout is a
separate, deliberate decision made by the user with the validate result in
hand — not an automatic next step in the same session.

Two reasons this must be a hard stop:

- Even the best outcome here is "not refuted," and the parked vol question
  (§35.6) still gates live deployment. A clean 2024 does not clear it.
- Chaining validate → holdout in one session is how the last look gets spent in
  the momentum of a good number. The holdout is one look, ever. It gets its own
  decision with a clear head.

State in the report: which Tier 1 gates passed, whether the drift-adjusted read
is positive, and that the holdout decision is deferred to the user.

---

## 5. Order of work

1. §2 rule into `NOTES` §37, dated, committed **before** the run
2. Log trial 10 (`status: started`)
3. Run B on 2024, both fee schedules
4. 2024 drift decomposition (diagnostics, not a trial)
5. Grade every gate; report §3
6. **Stop.** Holdout untouched, decision deferred

## 6. Acceptance

- §37 rule committed before the run, in its own commit
- Trial logged before execution; budget 9 → 10 of 25
- All three Tier 1 gates graded on both fee schedules
- Tier 2 invariants and 2024 drift fraction reported
- Per-2024 table; who-is-paying re-derived
- Holdout untouched; decision explicitly deferred to the user
- No threshold adjusted after seeing the result

## 7. Do not

- Adjust any §37 threshold after seeing 2024
- Demand a Sharpe near train's 1.11 — the drift adjustment makes ~0.66 the
  success expectation
- Read G2 (drawdown breach) as a wasted trial — it is a decisive answer
- Chain into the holdout in this session
- Touch the holdout (2025-01 → 2026-07) under any outcome

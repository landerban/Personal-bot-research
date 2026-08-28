# Stage 6a — Vol-target sweep for B (train diagnostic, zero trials)

Settles the parked vol/kill-switch question by finding the deployment vol for
B. A **train diagnostic**: vol target is a risk knob that rescales the same
positions, not a signal parameter, so this is not a strategy search and costs
no trial.

§0 of `STAGE2_PROMPT.md` remains in force. Budget stays **10 of 25**; validate
(2024) already spent once and not re-run here; holdout sealed.

---

## 0. Why this is not a trial

Vol targeting rescales identical positions up or down. It does not change the
signal, the universe, or which names are selected — only position size, and
therefore drawdown depth and whether the `MIN_NOTIONAL` floor or the kill
switch binds. There is no edge being searched for (Sharpe is theoretically
vol-invariant), so there is nothing to select on that would consume a trial.

What IS being decided: the vol at which B is deployed. That is a risk-sizing
decision, made on train, pre-registered.

---

## 1. The sweep

Run B1 (top-15 PIT majors, USDT fees, all else frozen) on **train 2020–2023**
at four vol targets: **12%, 13%, 14%, 15%.**

For reference, also report the already-known 20% result (§34) in the same
table. Do not re-run it — cite it.

---

## 2. The selection rule — write into NOTES §39 BEFORE running

**Deploy the highest vol target whose MEASURED max drawdown ≤ 20%.**

- The cap is **20% drawdown**, chosen for headroom: B's train drawdown at 20%
  vol was 29.73%, 0.27 points from the kill switch. A 20% drawdown cap roughly
  halves the distance-to-death and leaves ~10 points of buffer.
- **Measured, not estimated.** The arithmetic (ratio ~1.49) predicts ~13% vol
  wins, but the floor may distort the drawdown/vol relationship at low vol. The
  rule uses the drawdown each vol *actually produces* in the sweep.
- **Highest qualifying vol**, not the one with the best Sharpe. Sharpe is
  vol-invariant; selecting on it is selecting on noise. The tie-break is return
  (higher vol → higher return), which is why "highest qualifying" is the rule.
- If **no vol** produces measured drawdown ≤ 20%, report that and stop — the
  answer becomes "B needs vol below 12%," which reopens the floor question
  rather than resolving it, and is a user decision.

Do not adjust the 20% cap after seeing the drawdowns.

## 3. The floor is the real output — not Sharpe

All four targets are lower vol than 20%, so positions shrink and more risk
falling under the $5 `MIN_NOTIONAL`. A vol that only "works" by skipping half
its rebalances is not runnable at any Sharpe. **Report, at each vol:**

| Must report per vol | Why |
|---|---|
| Skip rate by reason | a book skipping heavily is not the same strategy |
| Count of names dropped under `MIN_NOTIONAL` per rebalance | the floor mechanism directly |
| **Realised** vol vs target | if realised vol < target, the floor is capping size and the vol target is not actually being hit |
| Median and p95 position notional | how close to the $5 floor the book sits |
| Measured max drawdown, with date | the selection input |
| Sharpe (reported, not selected on) | vol-invariance is the check — if Sharpe moves a lot with vol, something non-linear (the floor) is interfering |

### 3.1 The disqualifier, fixed in advance

A vol target is **disqualified regardless of drawdown** if its skip rate
exceeds the 20%-vol run's skip rate by more than ~10 points, or if realised
vol falls more than ~2 points short of target. Either means the floor is
distorting the strategy rather than merely resizing it. State this before
running.

## 4. What this settles and what it does not

- **Settles:** the deployment vol for B, and whether the §35 fragility is
  fixable by sizing (it is, if a qualifying vol exists that the floor doesn't
  break).
- **Does not settle:** whether B's edge is real — that was Stage 6, not
  re-opened here. This sweep assumes the edge and sizes the risk around it.
- **Does not re-touch 2024.** The vol chosen here is a train decision. Whether
  to re-validate B at the new vol on 2024, or proceed to holdout at it, is the
  next decision and is deferred.

### 4.1 A note on the 2024 result and the new vol

Stage 6 validated B at **20% vol** and it passed with an 18.52% drawdown — so
2024 did not need the lower vol. But the holdout contains the deep 2025
drawdown, where the extra headroom matters more. Choosing the deployment vol
now, on train, means the eventual holdout look tests the config actually
intended for live use rather than the 20% version that happened to survive one
mild year. State this reasoning in §39 so the vol choice is not later mistaken
for post-hoc fitting.

## 5. Order of work

1. §2 selection rule and §3.1 disqualifier into `NOTES` §39, dated, before
   running
2. Sweep 12/13/14/15% on train; cite the 20% reference
3. Report the §3 table for every vol
4. Apply the rule: highest vol with measured DD ≤ 20% and not disqualified by
   the floor
5. State the chosen deployment vol, or "none qualifies" if that is the result
6. **Stop. Report. Spend no trial. Do not touch 2024 or holdout.**

## 6. Acceptance

- §39 rule and disqualifier recorded before any run
- Four vol targets swept on train; 20% cited for reference
- Floor diagnostics (skip rate, sub-floor counts, realised vs target vol,
  position sizes) reported at every vol — this is the primary output
- Selection applied mechanically from measured drawdown, not Sharpe
- Chosen deployment vol stated, or "none qualifies"
- Budget **10 of 25**; 2024 not re-run; holdout sealed

## 7. Do not

- Log any sweep run as a trial (they are risk-sizing diagnostics on train)
- Select the vol by Sharpe instead of the drawdown cap
- Adjust the 20% cap or the §3.1 disqualifier after seeing results
- Re-run or re-touch the 2024 validate window
- Touch the holdout

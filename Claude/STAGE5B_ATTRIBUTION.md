# Stage 5b — Free attribution on B (zero trials)

Everything that can be learned about the majors config **without spending a
trial** — attribution and bootstrap of runs that already exist. Sharpens the
validate decision without touching validate.

§0 of `STAGE2_PROMPT.md` remains in force. Budget stays **9 of 25**; validate
and holdout untouched.

---

## 0. Why these, and why now

B (top-15 PIT majors) beat A on mechanism — 2023 collapse gone, funding
dependence 60% → 23% — but B1−A straddles zero and the vol/kill-switch
question is parked. Before deciding whether to spend a look, extract what
train can still say for free. None of this spends a trial: every item is
attribution or resampling of the **existing** B1 run.

**The most important one is §2 (drift).** If B's apparent momentum is
substantially the same finite-sample drift artifact that inflated A, then B is
not the cleaner momentum strategy it appears to be, and that changes the
validate decision. This is the one that could overturn the Stage 5 read.

---

## 1. Per-year bootstrap CIs on B

§34.3 gave B's per-year price PnL as point estimates (+178, +175, −40, +137)
with no intervals. Add block-bootstrap 90% CIs per year, same method as §24.1
(recompute block length for B's series).

Report per year 2020–2023: price PnL, funding PnL, Sharpe, each with 90% CI.

**Reading, fixed before computing:** the question is not whether 2022 is
negative — it is — but whether the three *positive* years are individually
distinguishable from zero, or whether B's whole edge rests on one or two years
the way A's rested on 2023 funding. Write this expectation into `NOTES` §36
before running.

## 2. Drift decomposition on B — the one that can overturn Stage 5

A's drift decomposition (§22 / STAGE3A) found ~44% of Sharpe attributable to
persistent cross-sectional drift against an ~18% synthetic floor. **B has
never had this run.**

Repeat it on B1: build the demeaned database (per-symbol full-sample mean
removed, existing `tools/build_demeaned_db.py`), restricted to B's top-15 PIT
majors universe, and run B1 on it. Report:

- `Sharpe_real` vs `Sharpe_demeaned` for B
- the drift component as a fraction, against the ~18% synthetic floor
- the same split per year

**Reading, fixed before computing:**

| If B's drift fraction... | Then |
|---|---|
| Comparable to A's ~44% | B's "cleaner momentum" is partly the same drift artifact. The mechanism story weakens; validate with that caveat explicit |
| Materially below A's | B genuinely harvests more trend and less drift — the majors concentration removed drift-prone names. Strengthens B |
| Near or below the 18% floor | B's edge is essentially all trend-continuation. Strongest possible read for B |

This uses full-sample means and is **not runnable live** — label it a
diagnostic everywhere, log to `diagnostics.jsonl`, not `trials.jsonl`.

## 3. Turnover on B vs A

§32.4 gave A's turnover split (68% boundary-crossing, 32% adjustment). **B has
no turnover figure.** A 15-name universe should cross the rank boundary less
often than 800 names, so B may be structurally cheaper beyond the fee saving.

Report for B1, from the existing run:

- annualised turnover multiple vs A's ~109×
- boundary-crossing vs adjustment split
- fee drag already known (27.7% USDT / 20% USDC) — tie it to the turnover

If B's turnover is materially lower, that is an additional, unpriced advantage
of the majors universe and belongs in the B-vs-A ledger.

## 4. Who is paying, for B

A's answer was corrected three times (§18.2 → §22.3 → §25.3) and settled as
mostly funding from crowded-long liquidations. **B's composition is different**
— 77% price, 23% funding — so the answer must be re-derived, not inherited.

State, from B's attribution:

- the momentum-payer story for the price leg (77% of net) — who is late, who
  is liquidated into weakness, and why it would persist in liquid majors
- the residual funding story (23%) — same crowded-short mechanism as §22.3, or
  different in a majors-only book
- whether either leg's counterparty story is one that **decays with capital**
  (the momentum leg is the one the literature says crowds out)

## 5. The B-vs-A ledger — assemble, do not decide

Collect into one `NOTES` §36 table, so the validate decision is made against a
complete picture rather than scattered findings:

| Dimension | A (frozen) | B (majors) |
|---|---|---|
| Train Sharpe (CI) | 0.80 [−0.02, 1.57] | 1.11 [0.36, 1.90] |
| B−A paired | — | +0.32 [−0.60, +1.24] straddles |
| Price / funding split | 60 / 40 | 77 / 23 |
| 2023 price PnL | −37 | +137 |
| 2022 price PnL | +30 | −40 |
| Max drawdown | 27.87% | 29.73% (USDT) / 27.47% (USDC) |
| Drift fraction | ~44% | **§2** |
| Turnover | 109× | **§3** |
| Carry-decay exposure | high | lower |
| Kill-switch headroom | 2.1 pts | 0.27 pts (USDT) — **parked** |

Fill the bold cells. **Do not pick a config here** — this is the evidence
table the eventual validate decision reads from.

## 6. What none of this establishes

- All in-sample on train. None of it confirms B's edge is real — only validate
  can refute that, and only weakly (MDE ~1.65).
- Drift, turnover, and payer analysis characterise *what B is*, not *whether it
  works out of sample*.
- The parked vol/kill-switch question is not answered here and still gates live
  deployment.

## 7. Order of work

1. §1 and §2 readings into `NOTES` §36, dated, before computing
2. §1 per-year bootstrap CIs
3. §2 drift decomposition — the decisive one; `diagnostics.jsonl`
4. §3 turnover; §4 payer analysis
5. §5 ledger assembled
6. **Stop. Report. Spend no trial.**

## 8. Acceptance

- Per-year 90% CIs for B, price and funding
- Drift fraction for B vs A's 44% and the 18% floor, per year, in diagnostics
- Turnover multiple and boundary/adjustment split for B
- Payer story re-derived for B's 77/23 composition
- §36 ledger complete, all bold cells filled, no config chosen
- Budget **9 of 25**; validate and holdout untouched

## 9. Do not

- Log the demeaned run as a trial
- Pick A or B here — assemble evidence only
- Read any in-sample result as confirmation of edge
- Reopen the parked vol question
- Touch validate or holdout

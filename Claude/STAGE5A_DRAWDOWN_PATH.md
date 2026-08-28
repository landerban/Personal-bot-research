# Stage 5a — Un-truncated drawdown path (diagnostic, zero trials)

A **train-only diagnostic**. Shows B's full drawdown path with the kill switch
disabled, to answer: *if the strategy had not stopped at −30%, would it have
recovered or kept falling?*

§0 of `STAGE2_PROMPT.md` remains in force.

---

## 0. What this is and is not

**IS:** re-running the B1 majors config on **train (2020–2023)** with the 30%
kill switch disabled, purely to see the un-truncated equity path. Train data,
already used. **No trial. No configuration change to the strategy. No
validate, no holdout.**

**IS NOT:** removing the kill switch from the strategy. The switch stays in the
strategy definition for every future run. This diagnostic disables it in **one
throwaway train run** to inspect the path, then the disabling is discarded.

The kill switch is a **live operational rule**, not a backtest mechanic — a
backtest never "liquidates" from it, it simply truncates reporting. Disabling
it here changes nothing except that the equity curve is shown in full instead
of stopping at −30%.

**Hard rule:** the kill-switch-disabled flag exists only in this diagnostic
script's output. It must not be written into `Config`'s default, must not
appear in any logged trial, and must not touch validate or holdout code paths.
If implementing it requires a `Config` field, default it to switch-enabled and
set it True only in this one script.

## 1. Run

B1 config (top-15 PIT majors, USDT fees, all else frozen), train window,
kill switch disabled. One run. Not logged as a trial — it is a re-run of an
existing config with reporting un-truncated, not a new configuration.

## 2. Report

1. **Full daily equity curve**, 2020–2023, un-truncated.
2. **The deepest drawdown**: depth, peak date, trough date, and **recovery
   date** (when equity regained the prior peak) — or "did not recover within
   train" if it never did.
3. **Time underwater**: days from peak to recovery for the worst drawdown.
4. **Every excursion past −25%**: how many, how deep each, how long to recover.
5. The date the −30% switch **would** have fired, and what the strategy did in
   the 30 / 60 / 90 days *after* that date had it kept running.

## 3. The reading — write into NOTES §35 before running

| If, after the −30% point... | Then |
|---|---|
| Equity recovers within ~1–3 months | The 30% switch is **too tight** — it would stop at the bottom and forfeit the rebound. Argues for lower vol target (staying under the switch) rather than a looser switch |
| Equity keeps falling toward −40% or worse | The switch is **correctly protective**. B needs the vol reduction to survive, not switch removal |
| Recovers but slowly (6+ months underwater) | Ambiguous — survivable but painful. A judgement call for the user, not an automatic reading |

Write this before looking. Do not adjust after.

## 4. What it cannot tell you

- This is **in-sample** on train. The worst real drawdown is out-of-sample and
  unseen. A train path that recovers is not a promise the holdout path will.
- It says nothing about whether B's edge is real — that is the validate
  question, untouched here.
- Removing the kill switch is **not** a strategy improvement and must not be
  read as one. The takeaway is about **vol target and switch level**, not about
  running without a switch.

## 5. Order of work

1. §3 reading into `NOTES` §35, dated, before running
2. Diagnostic run; report §2
3. State which branch fired
4. **Stop.** Kill-switch-disabled flag stays out of `Config` defaults, out of
   trial logs, out of validate/holdout paths

## 6. Do not

- Write the disabled switch into any config that could reach validate/holdout
- Log this as a trial (budget stays 9 of 25)
- Read "recovers on train" as evidence the strategy works
- Touch validate or holdout

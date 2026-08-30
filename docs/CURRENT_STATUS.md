# Current status

_Regenerable summary. **`NOTES.md` is the canonical ledger** — append-only,
never split, never edited. Nothing here is authoritative; if the two disagree,
NOTES wins._

Generated at `9f03e2a` on 2026-08-30.

## In one line

**Research blocked; not deployment-ready.** The frozen config cannot form a
book in the current market, and the reason is now measured rather than
suspected.

## The frozen config

```
B: top-15 crypto majors, lookback 14, skip 0, N=10, k=5,
   vol target 10%, capital $800, rank_buffer 0, 3x gross cap,
   beta-neutral, +1min fill, 5 bps/side
```

Validated on train (2020–2023) and one validate look at 2024 (§44: all three
gates passed). **Its recorded train/validate numbers predate quantized
sizing** (Stage 17 I.3) and are annotated, never re-run — history is not
rewritten here.

## The blocker

| Measurement | Result |
|---|---|
| testnet, synthetic volume ranking (§53.1) | 0 of 12 days formed |
| testnet, production volume ranking (§55.8) | 0 of 12 |
| **production market data (§56.11)** | **0 of 12** |

Code, venue and guard were each eliminated by measurement in turn. The
feasibility surface (§57, Part II) says the binding constraint is
**identifiability, not capital**: P(form) moves +3% from \$800 to \$5k, and
+59% when a beta-SE screen is applied.

## Holdout

**SEALED.** `holdout_log.json` does not exist; `trials.jsonl` contains zero
holdout rows. Budget **15 of 25** trials.

## Module map

```
pitdata/     Stage 1 point-in-time store. FROZEN. 13/13 lookahead tests.
backtest/    engine, weights, sizing (shared), universe_filter, metrics, runner
live/        client (testnet-only), proddata (production READ-ONLY), phase2,
             fillsim, risk, settle, fixes, costlog, reconcile, watchdog,
             killswitch
xsmom/       supervisor, scheduler, single-instance lock
dashboard/   read-only local UI (no keys, no exchange client, GET only)
tools/       diagnostics, probes, proofs — every one logs to diagnostics.jsonl
```

## Not covered by CI

The 732 MB store, any network venue, and the real-order roundtrip are
locally-verified only. **CI green does not mean everything was checked.**

## Where the decisions live

`NOTES.md`, §2 through §57 — every pre-registration, result and correction,
including the ones that falsified earlier claims of mine.

# Stage 17 — Review fixes + the feasibility surface

Two halves. **Part I** lands the external review's four defects — the settle
primitive, the floor-semantics inconsistency (settled empirically), the shared
quantized sizing module, the broken helper. **Part II** answers the §56.13
research question structurally: under what (capital, universe, screen)
conditions can this strategy definition physically express a book in the 2026
market — with no Sharpe, no PnL, and no tuning on the 12 failure days.

§0 of `STAGE2_PROMPT.md` remains in force. **No trials. No strategy parameter
changes to any validated config** (Part II derives candidates; it selects
nothing for deployment). Zero orders on mainnet. Holdout sealed. Budget
**15 of 25**.

---

# PART I — The four fixes

## I.1 `await_reconciled_state` — the settle primitive (review #1, §56.8-3)

One shared primitive, used by BOTH the roundtrip harness and `run_cycle`:

```
await_reconciled_state(expected_deltas, timeout, poll_interval)
```

Polls `fetch_state` until (a) every terminal order is reflected in positions,
or (b) observed position deltas match confirmed fills within step-size
tolerance — else raises on the deadline. **No bare sleep.** The atomicity
check (§52.4 fix 1) runs only against the settled snapshot.

Tests: a mocked laggy exchange (correct on Nth poll) passes; a genuinely
missing leg still trips atomicity after the deadline; the roundtrip and
`run_cycle` both import the same function (a test asserts there is exactly one
implementation).

## I.2 Floor semantics — measure, then unify (review #2)

The model currently disagrees with itself: `fillsim` refuses every sub-floor
delta; `plan_rescale` assumes reduce-only closes are floor-exempt. Resolve by
measurement, not by argument:

1. **Empirical probe on the demo fixture** (testnet, `demo=True`): open ~$15
   SOL; attempt a reduce-only partial close ~$3 (sub-floor); attempt a
   reduce-only full close of a sub-floor remnant; record accept/reject codes.
   One run, logged verbatim.
2. **Delta classification** in one place: `open_increase | partial_reduce |
   full_close | flip`, with the measured venue rule applied per class, used by
   `fillsim`, `plan_rescale`, and the live path alike.
3. If testnet and documented mainnet behaviour differ, encode testnet's
   measured rule for the sim, record the discrepancy in NOTES §57, and flag it
   as a must-re-verify item for any future real-money venue (same class as
   the §56.9 stops finding).

Tests: each delta class against the encoded rule; the specific trap the review
named — a position whose closing delta is sub-floor — must be closable in the
sim if and only if the measured rule allows it.

## I.3 The shared sizing module (review #3)

One deterministic function, single source of truth for backtest, fillsim, and
live:

```
desired weight → desired notional → reference price
→ raw qty → step-size quantized qty → executable notional
→ filter verdict (min_notional, min_qty)
```

- Reads `step_size` / `tick_size` / `min_notional` from the store (already
  persisted; until now ignored by research sizing).
- **Scope rule:** the module is wired into Part II's feasibility surface and
  into the paper/live path. It is **not** retro-run against the frozen
  config's recorded train/validate results — those stand as recorded, with an
  explicit NOTES §57 caveat that they predate quantized sizing. History is
  annotated, never rewritten.
- Tests: quantization edge cases (the review's `$5.04 → $4.91 → rejected`),
  zero-step symbols, and an invariance check that live and sim produce
  identical verdicts for identical inputs.

## I.4 `leg_beta_se` (review #4)

Fix to match its docstring — return `(|weighted contribution|, se)` — with a
unit test, or delete it and grep for callers. No authoritative-looking dead
code.

## I.5 Hygiene rider (small, bounded)

- `docs/CURRENT_STATUS.md` (< 2 pages): frozen-config status, the 0/12
  finding, holdout SEALED, "research blocked; not deployment-ready," module
  map. Regenerable at will.
- `pyproject.toml` with pinned deps + pytest config; a GitHub Actions workflow
  running the fast suite per commit and the adversarial/null suite nightly.
- **The ledger is not touched.** NOTES.md stays canonical, append-only,
  un-split. Views may summarize it; nothing edits it. README may be shortened
  to point at CURRENT_STATUS.md.

# PART II — The feasibility surface

## II.1 Pre-register in NOTES §57 before computing

The question, verbatim: *"Under what capital, universe-rank, and
identifiability conditions can the strategy definition (5L/5S, rank-weighted,
beta-hedged, vol-targeted) physically express a book in the current market?"*

The outputs are **probabilities of book formation** — never Sharpe, never
PnL, never returns. Nothing in Part II reads any return series. The
pre-registration records:

- the axes and their grid (below), fixed in advance
- the formation-probability estimator (below)
- the anti-tuning rule: **the 12 replay days are a smoke test only.** The
  surface is computed from structural facts (listings, floors, step sizes,
  beta SEs as of today); candidate regions are then *verified* on forward
  live-replay days as they accumulate — never fitted to the 12.
- what Part II does not do: select a deployment config, touch 2025+ returns,
  or modify any validated artifact.

## II.2 The axes

| Axis | Grid | Why |
|---|---|---|
| capital | $800, 1.2k, 2k, 3k, 5k | the review's tier question, incl. current |
| universe rank cap | top 15, 20, 25, 30 | deeper cap = more seasoned names available |
| identifiability screen | none; beta SE ≤ 0.3; ≤ 0.5; "60d listed + SE cap" | the §56.12 cause-2 axis — a *candidate* rule, derived structurally, adopted by no one here |
| vol target | 10%, 12%, 14% | sizing interacts with floors via realised gross |

Fixed, not swept: N=10, k=5, band, hedge guard thresholds, `MIN_LEG_NAMES` —
the strategy *definition* is the thing being tested for expressibility, so it
does not bend during the test.

## II.3 The estimator — structural, using today's market

For each grid point, over a distribution of plausible momentum rankings
(bootstrap the ranking from recent real relative-strength orderings rather
than one fixed ranking — formation must be robust to *which* names momentum
picks, not conditioned on last Tuesday's):

1. draw a top/bottom-k selection consistent with the ranking draw
2. size it through the **I.3 shared module** (real floors, real step sizes)
3. apply the hedge with real current betas and SEs
4. record: book forms? which guard refuses? how many names dropped?

Report per grid point: `P(form)`, the failure-mode split
(floor vs identifiability vs leg-count), and median seated names.

## II.4 The reading — fixed before computing

The surface distinguishes the review's three worlds:

| Pattern | Meaning |
|---|---|
| `P(form)` rises steeply with capital at fixed screen | **capital-bound**: $800 is simply too small for today's structure; report the tier where P ≥ 0.9 |
| `P(form)` stays low at all capitals without a screen, healthy with one | **universe-too-young**: identifiability, not money, is binding; the screen is the research object |
| `P(form)` low everywhere on the grid | **aged out** as defined; a new strategy generation is a bigger question than any grid |

Mixed patterns are reported as mixed. **No cell is promoted to a deployment
config.** The surface ends as a report and a recommendation *for the user's
decision*, including — if a viable region exists — the honest statement of the
validation problem that region inherits (dead train era, sealed holdout,
forward-validation option), per the standing fork.

## Order of work

1. NOTES §57: I.2 probe plan, I.3 scope rule, II.1 pre-registration — dated,
   before code
2. Part I in order: settle primitive → floor probe + unification → sizing
   module → helper fix → hygiene rider; suites green throughout
3. Part II surface on the I.3 module; smoke-check against the 12 days
   (explain, don't fit); report per §II.4
4. Report. **Stop.** No deployment selection, no trials, holdout sealed.

## Acceptance

- One settle primitive, shared, tested; atomicity runs on settled state only
- Floor rule measured on-venue, encoded once, delta-classified, tested against
  the unclosable-position trap
- Shared sizing module with quantization; frozen-config history annotated, not
  re-run
- `leg_beta_se` fixed-with-test or deleted
- CURRENT_STATUS.md, pyproject, CI live; NOTES.md untouched as ledger
- Surface pre-registered, structural, bootstrap-ranked; 12 days used only as
  smoke test; §II.4 pattern stated with failure-mode splits
- No config selected; no return series read in Part II; budget **15 of 25**;
  holdout sealed

## Do not

- Sleep instead of settle
- Argue the floor rule from documentation when the venue can be asked
- Re-run frozen-config history through the new sizing module
- Fit any Part II choice to the 12 replay days
- Read Sharpe, PnL, or any 2025+ return data in Part II
- Promote a surface cell to a deployment config
- Split or edit NOTES.md
- Touch mainnet or the holdout

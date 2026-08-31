# Stage 20 — RCM v1 synthetic implementation: try to break it

The first Generation-2 code. Its job is **not** to show RCM works — it is to
build the machine faithfully to §59/§60/§60.11 and then attack it with
synthetic fixtures until every structural claim has been tested. **No real
market or return data touches any RCM code path in this stage.**

§0 of `STAGE2_PROMPT.md`, §59, §60, §60.11 govern. Gen-1 frozen, 15 of 25.
Holdout sealed. **Gen-2 budget: 0 of 20 — synthetic work consumes none, and
this stage must end at 0.**

Prerequisite: `NOTES` §60.11 appended (Stage 19a v3). Do not begin otherwise.

---

## 0. Ground rules for the first Gen-2 code

1. **Synthetic only.** Every input is generated or fixtured. A test asserts
   that no RCM module imports the production data client, the research store's
   real-data readers, or any path that could yield real returns. The seal is
   irrelevant to this stage only because *nothing real* is read at all.
2. **UNRESOLVED means raise.** `g_min` has no default; the zero-momentum-mass
   case raises unless the user has confirmed option (a) in the ledger; the
   funding-observability predicate reuses Gen-1's machinery or raises. **No
   placeholder value may be silently supplied to make a test pass.**
3. **The agent makes no strategy-definition decision.** Anything ambiguous in
   the spec is reported as a gap, not resolved in code.
4. **Pre-register before executing** anything with a pass/fail — in
   particular the correlated-residual stress fixture (§5).

## 1. Structure

A new `rcm/` package parallel to `backtest/`, reusing Gen-1 infrastructure
unchanged (§59.6): PIT store interfaces, shared quantized sizing, fill
simulator, risk layer, cost log, reconciliation primitives, dashboard hooks.
Frozen solver and version pinned in `pyproject.toml` (§60.7's UNRESOLVED
solver pin is resolved here by **choosing the pin and recording why**; this is
an engineering choice, not a strategy one — say so).

Modules, each mapping to a §60 section: factor model with ETH⊥ (§60.1);
momentum calibration with PIT set-builder and carry guard (§60.2, §60.11.2);
chance-constrained SOCP (§60.3, §60.7); timeline (§60.4); gates (§60.5,
§60.11.6–8); state machine and calendar classifier with causal precedence
(§60.6, §59.11.2); funding forecast with PIT cadence (§60.11.1); attribution
(`Δ_gate`, `Δ_transition`, reporting tuple, literal label — §59.11.3–4).

## 2. The seal, structurally (§59.1.5)

Before any other module: the Gen-2 research runner **hard-rejects** any
request whose timestamp range intersects `[2025-01-01, 2026-07-31]`. Unlock
requires an explicit flag **and** a ledger-entry check. Tests: a request
touching the interval by one day is refused; the unlock path without a ledger
entry is refused; the unlock path with a fake entry is refused if the entry
predates the request's own creation (no back-dating).

## 3. Null and canary tests — the machine must not manufacture alpha

Inherit the Gen-1 discipline on synthetic data:

- **Random-signal canary:** replace `Z_mom` with noise ⇒ calibrated `b̃_t`
  shrinks to the prior, `s_mom` collapses, carry flag fires, calendar Sharpe
  of the momentum leg ≈ 0.
- **Shuffled-return canary:** permute forward residuals ⇒ same.
- **Zero-alpha, nonzero-funding fixture:** the book becomes the labelled carry
  book (or raises, per §60.11.8 status) — never an unlabelled one.

## 4. Adversarial fixtures — one per structural claim

Each fixture is a test that fails if the claim is false:

| Claim | Fixture |
|---|---|
| PIT calibration (§60.11.2) | any observation with `outcome_end > decision_cutoff` ⇒ test fails; construct the boundary pair at 00:00/00:01 and assert it is excluded |
| Residual horizon match | `ε_fwd` and `r_actual_price` computed on identical intervals; a deliberate one-minute offset fixture is detected |
| Funding cadence (§60.11.1) | 8h→4h switch mid-window ⇒ `F̂` uses the PIT schedule; no constant multiplier; the two-cadence day sums the correct settlement count |
| Funding observability | a window the Gen-1 predicate cannot certify ⇒ candidate unavailable; **no new tolerance exists** (grep test for magic numbers in the module) |
| ETH orthogonalization | `corr(f_BTC, f_ETH⊥) ≈ 0` on the fixture; two-column OLS instability fixture shows why |
| Chance constraint (§60.3) | fixture with known `V_i` ⇒ realized breach ≈ 10% nominal under independent residuals |
| `N_eff` semantics (§60.11.4) | a book with `N_eff = 6` and one name at 30% passes `N_eff` and fails nothing else — documenting the non-equivalence |
| Optimizer→gate compatibility (§60.11.3.4) | a broad gate-passing book is feasible ⇒ pipeline produces one, not a concentrated reject |
| `C_signal` bounded, `S_i = |μ_mom|` | survivors renormalized ⇒ `C_signal ≤ 1`; funding-rich-but-momentum-poor survivors ⇒ low coverage |
| `degenerate_target` | `G_pre = 0` ⇒ named state, exactly one calendar category, no NaN anywhere |
| Zero momentum mass | raises `UNRESOLVED` (or, if the user has confirmed (a), forms a book carrying the literal `CARRY REGIME — NOT RCM` label and coverage N/A) |
| State transitions (§60.6) | every non-formed category × held-book ⇒ the pre-registered transition; concurrent failures give the same result under permuted evaluation order |
| Calendar precedence (§59.11.2.1) | a date failing at two stages classifies by the *earlier causal stage* under permuted check order |
| Solver determinism (§60.7) | same inputs ⇒ weights within tolerance, constraint residuals within their pinned maxima, termination state in the accepted set; near-degenerate optimum fixture resolves deterministically |
| Quantization | the shared sizing module's `$5.04 → $4.91 → rejected` case reproduces inside RCM |
| Reporting tuple | every performance row carries all six fields; a formed-days-only Sharpe without the literal label fails the test |
| Attribution | `Δ_gate`, `Δ_transition` computed on a fixture with known planted values; execution-cost term reported separately |

## 5. The correlated-residual stress test — pre-register, get approval, then run

**5.1 Propose in `NOTES` §61, before executing:** the fixture (residual
correlation structure and strength — e.g. block/sector correlation at stated
levels), **and** the failure criteria for `σ_realized/σ_model` and
chance-constraint coverage, **with a derivation that references a prior
invariant** (the inherited 30% kill switch, the 10% target, `G_cap`,
`ε_β`, the 10% nominal breach). If no derivation references a prior invariant,
mark the criteria **UNRESOLVED** and do not run.

**5.2 The user approves the proposal** (ledger entry) before execution.
Neither fixture nor criteria change afterward.

**5.3 Run; report.** Record §60.11.5.4's fence beside the result: passing
establishes robustness only to the pre-registered scenario.

**5.4** Failure ⇒ a residual covariance treatment is required before any real
data; report it as the next stage, do not patch it here.

## 6. What this stage must not do

- Read any real market data, return series, or production snapshot into an
  RCM path
- Supply a default for `g_min`, the zero-momentum case, or the observability
  predicate
- Run the stress test before §5.2 approval
- Tune any frozen quantity to make a fixture pass — a failing fixture is a
  finding
- Consume a trial

## 7. Deliverable

- `rcm/` package with the §1 modules; pinned solver; all Gen-1 suites still
  green
- Every §3/§4 test present and green, or present and **failing with the
  failure reported as a finding**
- `NOTES` §61: solver pin rationale; the §5.1 proposal (or UNRESOLVED);
  after approval, the stress result with fence; the full list of structural
  findings; the list of real-data prerequisites still open
- A one-page `docs/RCM_STATUS.md`

## 8. Real-data prerequisites — state them at the end

The first 2020–2024 research run (trial 1 of 20) may not happen until:

- `g_min` resolved (§60.11.6)
- zero-momentum semantics confirmed by the user (§60.11.8)
- funding-observability predicate resolved (§60.11.1)
- stress test approved, run, and passed — or a covariance treatment adopted
- solver pin recorded; determinism tests green
- every §4 fixture green or its failure adjudicated in the ledger

List which are open at the end of the report. The stage ends at **0 of 20**.

## Acceptance

- Seal hard-rejection tested including the back-dating case
- No RCM path can reach real data (import-level test)
- Null/canary tests green
- All §4 fixtures present; failures reported as findings, not patched away
- §61 stress proposal with invariant-referencing derivation or UNRESOLVED;
  no execution before approval
- No defaults supplied for UNRESOLVED items
- Real-data prerequisites listed; Gen-2 **0 of 20**; holdout sealed

## Do not

- Touch real data, the holdout, or mainnet
- Resolve any UNRESOLVED item in code
- Run the stress test unapproved, or change it after seeing the result
- Treat a passing synthetic fixture as evidence about the real market
- Spend a trial

# Strategy Generation 2 — Residual Carry Momentum (RCM)

## Status

**Research proposal / pre-registration candidate**

This document describes a proposed replacement for the current frozen XSMOM strategy.

The existing XSMOM strategy should remain frozen as a completed research generation. RCM should be treated as a **new strategy generation**, not as a patch or silent modification of the old system.

---

# 1. Motivation

The current XSMOM implementation appears mechanically fragile in the present Binance perpetual-futures universe.

The main problem is not necessarily that momentum alpha has disappeared. Rather, the existing portfolio construction can fail before the alpha is expressed at all:

1. Rank a fixed number of longs and shorts.
2. Estimate beta exposures.
3. Attempt to beta-hedge the book.
4. Quantize positions to exchange constraints.
5. Reject the entire portfolio if beta uncertainty, minimum notionals, or leg-count constraints are violated.

This creates a highly binary portfolio-construction process.

A strategy can therefore fail to trade even when the underlying cross-sectional signal remains informative.

RCM is designed to preserve the strongest parts of the existing project:

- point-in-time data discipline;
- realistic exchange filters;
- explicit transaction costs;
- market-neutral research;
- strict separation of training and holdout data;
- falsifiable strategy generations;
- production-oriented execution infrastructure.

At the same time, it removes several fragile assumptions:

- raw price momentum as the only alpha source;
- exact top-N / bottom-N selection;
- BTC-only market exposure;
- hard beta-estimation rejection as the primary feasibility mechanism;
- all-or-nothing portfolio formation.

---

# 2. Strategy Thesis

The proposed strategy combines two effects:

1. **Residual momentum**
   - Trade persistent relative price movements that remain after removing broad crypto-market exposure.

2. **Perpetual-futures carry**
   - Prefer positions whose funding economics support the trade.
   - Penalize positions where funding makes the directional view expensive to maintain.

The intended interpretation is:

> Momentum identifies relative winners and losers.  
> Residualization removes the obvious market-direction component.  
> Funding measures the cost or benefit of maintaining the position.  
> Portfolio optimization decides how much risk can realistically be allocated.

---

# 3. Universe

The universe should be defined using structural rules that do not depend on strategy performance.

Initial candidate rules:

- Binance USDT-margined perpetual futures;
- instrument actively tradable at the decision timestamp;
- at least **180 days** of usable historical data;
- minimum trailing liquidity threshold;
- acceptable bid/ask spread and/or order-book depth;
- exclude stablecoins and mechanically pegged assets;
- exclude extremely recent listings;
- sufficient historical observations for factor and volatility estimation;
- valid exchange filters and contract metadata.

The maturity requirement should be considered a structural market-quality rule rather than a parameter selected because it improves recent PnL.

---

# 4. Factor Model

For each asset \(i\), estimate its exposure to the broad crypto market.

A simple initial model:

\[
r_{i,t}
=
\alpha_i
+
\beta_{BTC,i}r_{BTC,t}
+
\beta_{ETH,i}r_{ETH,t}
+
\epsilon_{i,t}
\]

where:

- \(r_{i,t}\) is the asset return;
- \(r_{BTC,t}\) is BTC return;
- \(r_{ETH,t}\) is ETH return;
- \(\epsilon_{i,t}\) is the residual return.

BTC and ETH are used rather than BTC alone because they jointly capture a larger portion of broad crypto-market variation.

The first implementation should prefer a simple and stable estimator.

Possible later alternatives, only if necessary:

- EWLS;
- ridge regression;
- beta shrinkage;
- robust regression;
- rolling covariance-based factor estimation.

These should not all be introduced simultaneously.

---

# 5. Residual Momentum Signal

Instead of ranking raw asset returns, calculate momentum from the residual return series.

Initial proposed formulation:

\[
M_i
=
0.6
\sum_{t=2}^{21}
\epsilon_{i,t}
+
0.4
\sum_{t=2}^{63}
\epsilon_{i,t}
\]

The most recent daily observation is skipped initially.

The reason is to reduce exposure to:

- very-short-term reversal;
- liquidation-driven spikes;
- transient news shocks;
- microstructure effects close to the rebalance timestamp.

Cross-sectionally normalize:

\[
Z^{mom}_i = z(M_i)
\]

A positive score indicates strong positive residual momentum.

A negative score indicates weak or negative residual momentum.

---

# 6. Funding / Carry Adjustment

Funding should become part of the alpha decision rather than being modeled only as a cost.

Estimate expected near-term funding:

\[
F_i
=
E[
\text{funding}_{i,t+1:t+k}
]
\]

The first version should remain intentionally simple.

Potential inputs:

- current funding rate;
- recent funding-rate EWMA;
- recent premium-index behavior;
- persistence of funding sign.

Avoid introducing a complex machine-learning funding predictor in the first generation.

Normalize:

\[
Z^{funding}_i = z(F_i)
\]

Conceptually:

### Long position

Preferred:

- positive residual momentum;
- neutral or negative funding.

Less attractive:

- positive residual momentum;
- extremely positive funding.

### Short position

Preferred:

- negative residual momentum;
- positive funding.

Less attractive:

- negative residual momentum;
- deeply negative funding.

A simplified combined signal can initially be represented as:

\[
S_i
=
Z^{mom}_i
-
\lambda Z^{funding}_i
\]

However, implementation should ultimately make the funding penalty direction-aware so that the economics are correct for both longs and shorts.

---

# 7. Beta-Uncertainty Treatment

The current strategy can effectively turn beta estimation into a binary eligibility condition.

RCM should instead treat estimation uncertainty continuously whenever possible.

One candidate adjustment:

\[
S_i^*
=
S_i
\cdot
\frac{1}
{1+c\,SE(\beta_i)}
\]

where:

- \(SE(\beta_i)\) measures uncertainty in the factor exposure;
- \(c\) controls the strength of the penalty.

This means:

- cleanly estimated assets retain most of their signal;
- noisy assets receive less capital;
- assets do not disappear solely because they crossed an arbitrary hard threshold.

A hard maximum uncertainty threshold may still exist as an extreme safety constraint, but it should not be the main portfolio-construction mechanism.

---

# 8. Continuous Position Selection

Do **not** force the strategy to select exactly 5 longs and 5 shorts.

Instead, form a broader candidate set.

Example:

- top 20–30% of eligible assets;
- bottom 20–30% of eligible assets.

Convert signal strength into continuous conviction.

For example:

\[
q_i
=
\tanh
\left(
\frac{S_i^*}{1.5}
\right)
\]

Then scale by idiosyncratic volatility:

\[
\tilde{w}_i
=
\frac{q_i}
{\sigma_{idio,i}}
\]

where:

\[
\sigma_{idio,i}
=
StdDev(\epsilon_i)
\]

This produces:

- larger weights for stronger signals;
- smaller weights for noisier assets;
- less dependence on arbitrary rank cutoffs.

---

# 9. Portfolio Optimization

The portfolio should be constructed as an optimization problem rather than a sequence of hard heuristics.

A generic objective:

\[
\max_w
\quad
S^\top w
-
\gamma
w^\top
\Sigma
w
-
\eta
\|w-w_{prev}\|_1
\]

where:

- \(S\) = alpha scores;
- \(\Sigma\) = covariance estimate;
- \(\gamma\) = risk-aversion parameter;
- \(\eta\) = turnover penalty;
- \(w_{prev}\) = current portfolio.

Possible constraints:

\[
\sum_i w_i \approx 0
\]

Dollar neutrality.

\[
\beta_{BTC}^\top w \approx 0
\]

BTC neutrality.

\[
\beta_{ETH}^\top w \approx 0
\]

ETH neutrality.

\[
|w_i| \leq w_{max}
\]

Single-name risk cap.

Additional constraints:

- maximum gross exposure;
- maximum long exposure;
- maximum short exposure;
- minimum tradable quantity;
- minimum notional;
- liquidity-based position limits;
- exchange step size;
- leverage constraints.

---

# 10. Graceful Portfolio Degradation

This is one of the largest architectural differences from XSMOM.

The strategy should not immediately reject an entire book because an ideal portfolio cannot be constructed exactly.

Examples:

If $800 cannot support 14 positions:

> Hold 8 valid positions.

If beta neutrality cannot be reached at full gross exposure:

> Reduce gross exposure.

If one symbol fails minimum notional:

> Remove that symbol and re-optimize.

If one position exceeds liquidity limits:

> Cap it and redistribute the remaining risk.

If residual beta remains slightly non-zero:

> Accept it within a pre-registered tolerance.

Reject the entire portfolio only when no economically sensible and risk-compliant solution exists.

---

# 11. Volatility Targeting

Start with a portfolio volatility target of approximately:

\[
\sigma_{target} = 10\% \text{ annualized}
\]

The exact value should be frozen before performance evaluation.

Portfolio gross exposure should respond to predicted risk rather than remain mechanically fixed.

Conceptually:

\[
G_t
=
\frac{\sigma_{target}}
{\hat{\sigma}_{portfolio,t}}
\]

subject to:

- maximum leverage;
- liquidity constraints;
- exchange constraints;
- per-name caps.

---

# 12. Rebalance Frequency

Initial recommendation:

**Once per day.**

Reasons:

- reduces turnover;
- reduces sensitivity to microstructure noise;
- decreases fee burden;
- makes PIT alignment easier;
- keeps the research problem focused on medium-horizon cross-sectional effects.

Intraday rebalancing should be considered only if the daily strategy first demonstrates robust signal quality.

---

# 13. Transaction Costs

The backtest must explicitly include:

- taker fees;
- maker fees if actually achievable;
- spread;
- slippage;
- funding;
- quantity rounding;
- minimum notional;
- contract filters;
- rejected or partially executable orders;
- turnover.

The strategy should be evaluated both:

1. before costs;
2. after realistic costs.

If performance exists only before costs, the strategy should be considered failed.

---

# 14. Proposed Version 1

The first serious RCM implementation should remain intentionally small.

## Universe

- mature Binance USDT perpetuals;
- ≥180 days usable history;
- liquidity threshold;
- valid market metadata.

## Factors

- BTC;
- ETH.

## Signal

- approximately 1-month residual momentum;
- approximately 3-month residual momentum;
- skip most recent day;
- simple funding adjustment.

## Weighting

- continuous signal weights;
- inverse idiosyncratic volatility.

## Neutralization

- dollar neutral;
- approximately BTC neutral;
- approximately ETH neutral.

## Risk

- 10% annualized portfolio volatility target;
- gross cap;
- individual-name cap;
- turnover penalty.

## Rebalancing

- daily.

That is enough for the first generation.

---

# 15. What NOT to Add Yet

Do not immediately add:

- machine-learning return prediction;
- ten technical indicators;
- market regime classifier;
- liquidation data;
- order-flow imbalance;
- social sentiment;
- open-interest forecasting;
- volatility forecasting model;
- adaptive thresholds;
- dozens of momentum windows.

Every additional feature creates another researcher degree of freedom.

The first question should be:

> Does residual cross-sectional momentum combined with carry contain a robust after-cost signal?

Only after answering that should the strategy become more complex.

---

# 16. Possible Later Extension — Dispersion Regime

A possible later feature is cross-sectional return dispersion.

For example:

\[
D_t
=
StdDev_i(r_{i,t})
\]

Then exposure could be scaled:

\[
w^{final}_t
=
G(D_t)w_t
\]

For example:

- normal dispersion → 100% target risk;
- very high dispersion → reduced gross exposure.

This should **not** be included in the first RCM backtest unless independently justified and pre-registered.

---

# 17. Research Methodology

RCM should be created as:

> **Strategy Generation 2**

The existing XSMOM strategy should remain frozen.

Recommended research process:

### Stage A — Structural implementation

Verify:

- point-in-time universe;
- factor alignment;
- residual calculation;
- funding alignment;
- execution filters;
- optimization constraints;
- quantity sizing;
- cost accounting.

No strategy-performance optimization during this stage.

### Stage B — Training-period evaluation

Evaluate:

- CAGR;
- Sharpe;
- Sortino;
- maximum drawdown;
- turnover;
- long contribution;
- short contribution;
- funding contribution;
- residual market beta;
- BTC beta;
- ETH beta;
- performance by year;
- performance by market regime.

### Stage C — Robustness tests

Test reasonable parameter perturbations.

Examples:

Momentum windows:

- 14 / 42 days;
- 21 / 63 days;
- 30 / 90 days.

Universe maturity:

- 120 days;
- 180 days;
- 270 days.

Funding weighting:

- weak;
- medium;
- zero.

The strategy should not require one exact parameter combination to survive.

### Stage D — Cost stress

Evaluate at:

- baseline costs;
- 1.5× costs;
- 2× costs.

### Stage E — Null / placebo tests

Examples:

- shuffled asset ranks;
- lagged signals;
- randomized signals;
- sign-flipped funding;
- random portfolios with identical constraints.

### Stage F — Holdout

Only after the strategy definition is frozen.

The holdout should remain untouched until then.

---

# 18. Kill Criteria

RCM should be abandoned if any of the following occur:

1. After-cost Sharpe is weak even in training.
2. Performance is dominated by one year or one coin.
3. The result disappears under modest parameter changes.
4. The edge disappears under realistic transaction costs.
5. Returns are mostly explained by BTC/ETH directional exposure.
6. Funding adjustment contributes nothing and only adds complexity.
7. Portfolio turnover becomes economically unrealistic.
8. PIT-correct implementation materially reduces the result.
9. Holdout performance is inconsistent with the training hypothesis.
10. Strong placebo/null strategies perform similarly.

The goal is not to save the strategy.

The goal is to determine whether it deserves to survive.

---

# 19. Why This Strategy Is Preferable to Patching XSMOM

RCM solves several structural problems directly.

## XSMOM

- exact top-N / bottom-N selection;
- raw momentum;
- BTC-centric hedge;
- binary feasibility;
- potentially fragile beta uncertainty guard;
- portfolio can fail entirely due to a few exchange constraints.

## RCM

- broad continuous ranking;
- residual momentum;
- BTC + ETH factor model;
- funding-aware signal;
- uncertainty-aware sizing;
- optimization-based construction;
- graceful degradation;
- risk-targeted portfolio.

The result should be a strategy that is easier to express in the actual futures market and easier to reason about statistically.

---

# 20. Core Hypothesis

The central falsifiable hypothesis is:

> Among sufficiently mature and liquid crypto perpetual futures, assets exhibiting persistent price movement unexplained by broad BTC/ETH market exposure continue to exhibit relative momentum, and the economic quality of that signal improves when the cost or benefit of perpetual funding is incorporated into position selection.

The portfolio-construction hypothesis is:

> A continuous, risk-scaled, factor-neutral portfolio can express this signal more reliably than a fixed top-N / bottom-N portfolio with hard feasibility rejection.

---

# 21. Minimal Mathematical Specification

Factor model:

\[
r_i
=
\alpha_i
+
\beta_{BTC,i}r_{BTC}
+
\beta_{ETH,i}r_{ETH}
+
\epsilon_i
\]

Residual momentum:

\[
M_i
=
0.6R^{res}_{21,i}
+
0.4R^{res}_{63,i}
\]

Cross-sectional score:

\[
Z_i
=
z(M_i)
-
\lambda z(F_i)
\]

Uncertainty adjustment:

\[
Z_i^*
=
\frac{Z_i}
{1+cSE(\beta_i)}
\]

Raw conviction:

\[
q_i
=
\tanh(Z_i^*/k)
\]

Risk scaling:

\[
\tilde{w}_i
=
\frac{q_i}
{\sigma_{idio,i}}
\]

Portfolio construction:

\[
\max_w
\quad
Z^{*\top}w
-
\gamma w^\top\Sigma w
-
\eta\|w-w_{prev}\|_1
\]

subject to:

\[
\sum_i w_i \approx 0
\]

\[
\beta_{BTC}^\top w \approx 0
\]

\[
\beta_{ETH}^\top w \approx 0
\]

\[
|w_i|\leq w_{max}
\]

plus execution, leverage, liquidity, and exchange constraints.

---

# 22. Final Recommendation

Do not continue incrementally modifying the frozen XSMOM strategy until it starts producing books.

Record its present failure as part of the research history.

Create a separate strategy generation:

> **RCM — Residual Carry Momentum**

The first implementation should focus only on:

1. mature/liquid PIT universe;
2. BTC/ETH residual returns;
3. residual momentum;
4. simple funding adjustment;
5. continuous volatility-scaled weights;
6. BTC/ETH-neutral portfolio optimization;
7. realistic execution and costs;
8. daily rebalance.

If that simple specification cannot demonstrate robust after-cost alpha, it should be killed before additional complexity is introduced.

If it survives, more sophisticated risk and regime features can be researched later.

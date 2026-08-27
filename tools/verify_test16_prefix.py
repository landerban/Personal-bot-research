#!/usr/bin/env python3
"""
Stage 2c 1.1 verification: prove Test 16 reproduces the bug it guards.

  git show 7f2ea6d:backtest/engine.py > /tmp/engine_prefix.py
  # append the daily_leverage trace lines (additive only), then:
  python tools/verify_test16_prefix.py /tmp/engine_prefix.py

Loads a PRE-FIX engine (hold-on-skip) and runs the skip-heavy
breach_market() fixture through it. Test 16 must FAIL there; a regression
test that cannot reproduce its bug proves nothing. Recorded result:
peak 35.71x, 8 days over the 3x cap, bankrupt at -$146 (NOTES 14).

Exits non-zero if Test 16 unexpectedly passes on the pre-fix path.
"""

import importlib.util, sys
sys.path.insert(0, r"C:\Stock"); sys.path.insert(0, r"C:\Stock\tests")
import test_backtest as T
spec = importlib.util.spec_from_file_location("engine_prefix", sys.argv[1])
E = importlib.util.module_from_spec(spec); sys.modules["engine_prefix"] = E; spec.loader.exec_module(E)
closes, cutoff = T.breach_market()
res = E.run_backtest(T.build_store(closes), T.CFG, T.T0 + T.DAY - 1, T.T0 + 400 * T.DAY - 1,
                     signal_fn=T.index_signal_until(cutoff))
peak = max(L for _, L in res.daily_leverage if L != float("inf"))
days_over = sum(1 for _, L in res.daily_leverage if L > T.CFG.max_gross_leverage)
print(f"PRE-FIX (hold on skip): rebalances={len(res.rebalances)} skips={len(res.skips)} "
      f"bankrupt={res.bankrupt} peak_leverage={peak:.2f}x days_over_cap={days_over} "
      f"min_equity={min(res.equity):.2f}")
try:
    T.check_leverage_cap_every_day(res, T.CFG.max_gross_leverage)
    print("Test 16 on pre-fix path: PASSED (unexpected - test cannot reproduce the bug)")
    sys.exit(1)
except AssertionError as e:
    print("Test 16 on pre-fix path: FAILS as required ->", e)

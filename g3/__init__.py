"""
Gen-3 G3-C specification package (NOTES 70.2/70.3), written at the spec
stage and LOCKED before the trial (NOTES 70.4/70.5).

Quarantine: modules here import only the standard library, numpy, the
inherited evaluator machinery (rcm.eval_ic) and the inherited cost model
(backtest.costs). No live/, no research/, no database path — the run
stage injects data. Everything is unit-tested on SYNTHETIC data only in
the spec stage; no development return is read before G3-C executes.
"""

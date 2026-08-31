"""
rcm -- Strategy Generation 2 (Residual Carry Momentum), v1.

SYNTHETIC-ONLY inside this package: no module here may read real market
data, and an import-level test enforces it. (Stage 21's one authorized
development-era structure measurement lives OUTSIDE rcm/ under its own
quarantine — NOTES 63.1.A.4.) Governance: NOTES 59; math spec: NOTES 60 +
60.11 + 62.8. Gen-2 trial budget 0 of 20.

UNRESOLVED means raise (never default). Formerly-raising items now resolved
by ledger entry: g_min -> V_ret >= V_RET_MIN (63.1.A.2), zero momentum mass ->
trade with the CARRY REGIME label, coverage N/A (63.1.A.1). Uncertified
funding windows still raise (60.11.1).
"""

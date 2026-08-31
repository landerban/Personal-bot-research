"""
rcm -- Strategy Generation 2 (Residual Carry Momentum), v1.

SYNTHETIC-ONLY at this stage (Stage 20): no module here may read real market
data, and an import-level test enforces it. Governance: NOTES 59; math spec:
NOTES 60 + 60.11. Gen-2 trial budget 0 of 20.

UNRESOLVED items raise (never default): g_min (60.11.6), zero momentum mass
(60.11.8, user decision pending), uncertified funding windows (60.11.1).
"""

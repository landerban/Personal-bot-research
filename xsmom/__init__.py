"""
xsmom -- the standalone paper-trading runner (Stage 14 Part B).

    python -m xsmom            start the supervisor (dashboard + daily cycle)
    python -m xsmom --once     one supervision tick, then exit

TESTNET ONLY. Real-money trading remains gated on the holdout decision.
"""

__all__ = ["supervisor", "schedule", "lock"]

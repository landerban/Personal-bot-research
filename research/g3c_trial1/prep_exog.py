"""Phase A (pre-`started` assembly): build the exogenous feature cache
through the locked reader. Pure memoization; no fit, no target read."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.g3c_trial1.runner import exog_series  # noqa: E402

if __name__ == "__main__":
    out = exog_series()
    print("exog cache complete:", sorted(out))

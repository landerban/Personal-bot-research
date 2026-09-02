"""Phase B (pre-`started` assembly): compute the locked 70.6.6 exposure
estimator per (name, market move) into a resumable cache. Pure
memoization of the locked beta_exposure; no fit, no target read."""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backtest.universe_filter import classify, load_snapshot  # noqa: E402
from g3 import features as feat                               # noqa: E402
from research.g3c_trial1.runner import (                      # noqa: E402
    CACHE, INT_TO_MOVE, exog_series, load_crypto, log_returns,
)

BUDGET_S = 480          # save-and-exit under the process cap; rerun to resume

if __name__ == "__main__":
    t0 = time.time()
    closes, _ = load_crypto()
    snap = load_snapshot()
    eligible = sorted(s for s in closes if classify(s, snap).eligible)
    exog = exog_series()
    bpath = CACHE / "betas.npz"
    cache = dict(np.load(bpath)) if bpath.exists() else {}
    done = n_new = 0
    for s in eligible:
        keys = [f"{s}|{i}" for i in INT_TO_MOVE]
        if all(k in cache for k in keys):
            done += 1
            continue
        r = log_returns(closes[s])
        for iname, (_, move) in INT_TO_MOVE.items():
            ck = f"{s}|{iname}"
            if ck in cache:
                continue
            beta, se, _ = feat.beta_exposure(r, exog[move])
            cache[ck] = beta
            cache[ck + "|se"] = se
        done += 1
        n_new += 1
        if time.time() - t0 > BUDGET_S:
            break
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(bpath, **cache)
    print(f"betas: {done}/{len(eligible)} symbols cached "
          f"({n_new} new this pass); "
          f"{'COMPLETE' if done == len(eligible) else 'RERUN TO RESUME'}")

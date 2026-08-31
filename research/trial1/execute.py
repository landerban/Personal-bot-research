"""
Trial 1 of 20 — THE REAL EXECUTION (lock: NOTES §66.5, commit fa78ddf9...).
Run exactly once. The verdict is the evaluators' output read through the
§60.12.5 four-row table; nothing here has discretion.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rcm.eval_formation import evaluate as eval_formation  # noqa: E402
from rcm.eval_ic import evaluate as eval_ic, seed_from_lock_commit  # noqa: E402
from research.trial1 import runner as R  # noqa: E402

LOCK_COMMIT = "fa78ddf9c50353e02f0dff9d9d38b648ce6a24bf"


def main():
    seed = seed_from_lock_commit(LOCK_COMMIT)
    print(f"lock {LOCK_COMMIT}  seed {seed}", flush=True)
    t0 = time.time()
    store = R.load_store()
    print(f"store loaded in {time.time()-t0:.0f}s: "
          f"{len(store['class_ok'])} classified symbols, "
          f"{store['n_snapped']} funding stamps snapped", flush=True)

    # progress: wrap the runner's per-day loop via a light monkeypatch on
    # its json writer is intrusive; instead run in slices? No — one call,
    # with the runner printing nothing; poll the daily.jsonl line count
    # from outside. Keep this process single-purpose.
    t1 = time.time()
    res = R.run(store)
    print(f"era complete in {(time.time()-t1)/60:.1f} min; counts "
          f"{res['counts']}; calib checks "
          f"{res['n_calib_equivalence_checks']}", flush=True)

    fv = eval_formation(res["day_records"])
    iv = eval_ic(res["ic_records"], seed=seed)

    formation_pass = bool(fv.passed)
    ic_pass = bool(iv.passed)
    if formation_pass and ic_pass:
        verdict = ("PASS+PASS: RCM v1 FREEZE per §60.12.5 row 1 — forward "
                   "paper begins; valid trials 1/20")
    else:
        verdict = ("FAIL: RCM v1 ABANDONED per §60.12.5 row 2 unless a "
                   "measurement-invalidating implementation defect is "
                   "reproduced by a failing test (row 3); valid trials 1/20")

    summary = {
        "lock_commit": LOCK_COMMIT,
        "seed": seed,
        "counts": res["counts"],
        "n_snapped_funding_stamps": store["n_snapped"],
        "formation": {
            "passed": formation_pass,
            "evaluation_start_ms": fv.evaluation_start_ms,
            "n_windows": fv.n_windows,
            "n_failing_windows": fv.n_failing_windows,
            "first_failing_window_end_ms": fv.first_failing_window_end_ms,
            "min_formed_in_any_window": fv.min_formed_in_any_window,
        },
        "ic": {
            "passed": ic_pass,
            "ic_mean": iv.ic_mean,
            "ci90": list(iv.ci90),
            "n_defined": iv.n_defined,
            "n_excluded": iv.n_excluded,
            "exclusion_reasons": iv.exclusion_reasons,
            "mean_block": iv.mean_block,
            "n_boot": iv.n_boot,
        },
        "reporting_tuple": res["reporting_tuple"],
        "delta_gate": {"point": res["delta_gate"].point,
                       "ci90": list(res["delta_gate"].ci90),
                       "n_formed": res["delta_gate"].n_formed,
                       "n_gate": res["delta_gate"].n_gate,
                       "transition_rule": res["delta_gate"].transition_rule},
        "delta_transition": {"point": res["delta_transition"].point,
                             "ci90": list(res["delta_transition"].ci90),
                             "n_gate": res["delta_transition"].n_gate},
        "price_pnl_full_calendar": res["price_pnl_full_calendar"],
        "funding_pnl_realized": res["funding_pnl_realized"],
        "cost_line_frozen_eta": res["cost_line_frozen_eta"],
        "turnover_total": res["turnover_total"],
        "verdict": verdict,
    }
    out = R.OUT / "summary.json"
    out.write_text(json.dumps(summary, indent=2, default=float),
                   encoding="utf-8")
    print(json.dumps(summary, indent=2, default=float), flush=True)
    print("VERDICT:", verdict, flush=True)


if __name__ == "__main__":
    main()

"""
Gen-3 Trial 1 execution + mechanical grading (STAGE_G3_C_RUN §§4-7).
Prints every number for NOTES §71; writes out/daily.jsonl and
out/summary.json. No interpretation anywhere.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from g3 import calibration as cal                              # noqa: E402
from g3 import eval as ev                                      # noqa: E402
from rcm.eval_ic import seed_from_lock_commit, spearman_ic     # noqa: E402
from research.g3c_trial1.runner import (                       # noqa: E402
    DATES, build_panel, run_direction, run_xsec,
)

LOCK_COMMIT = "07c9f604ea1a74e09808821a9dc8c32dab2e13d8"       # 70.10.5
OUT = Path(__file__).resolve().parent / "out"


def _f(x):
    return None if x is None or (isinstance(x, float) and not
                                 np.isfinite(x)) else round(float(x), 8)


def main() -> None:
    seed = seed_from_lock_commit(LOCK_COMMIT)
    print(f"lock_commit_hex: {LOCK_COMMIT}")
    print(f"SEED: {seed}")
    OUT.mkdir(parents=True, exist_ok=True)

    print("building panel...", flush=True)
    panel = build_panel()

    print("direction models...", flush=True)
    d0 = run_direction(panel, "M0")
    d1 = run_direction(panel, "M1")
    print("cross-sectional models...", flush=True)
    x0 = run_xsec(panel, "M0")
    x1 = run_xsec(panel, "M1")

    # ---------------- direction series (common defined OOS days)
    common_T = sorted(set(d0) & set(d1))
    bs_m0 = np.array([(d0[T][0] - d0[T][2]) ** 2 for T in common_T])
    bs_m1 = np.array([(d1[T][0] - d1[T][2]) ** 2 for T in common_T])
    bs_cl = np.array([(d0[T][1] - d0[T][2]) ** 2 for T in common_T])
    q1 = ev.q1_direction(bs_m0, bs_cl, seed)
    q2 = ev.q2_incremental_direction(bs_m0, bs_m1, bs_cl, seed)

    # ---------------- IC series
    all_T = sorted(set(x0) | set(x1))
    ic_m0_full = np.full(len(all_T), np.nan)
    ic_m1 = np.full(len(all_T), np.nan)
    ic_m0_int = np.full(len(all_T), np.nan)
    reasons: dict[str, int] = {}
    n_m0_names, n_m1_names = [], []
    for j, T in enumerate(all_T):
        if T in x0:
            preds = x0[T]
            ic, why = spearman_ic(
                np.array([v[0] for v in preds.values()]),
                np.array([v[1] for v in preds.values()]))
            if ic is None:
                reasons[f"m0:{why}"] = reasons.get(f"m0:{why}", 0) + 1
            else:
                ic_m0_full[j] = ic
            n_m0_names.append(len(preds))
        if T in x1:
            preds1 = x1[T]
            names1 = sorted(preds1)
            ic, why = spearman_ic(
                np.array([preds1[s][0] for s in names1]),
                np.array([preds1[s][1] for s in names1]))
            if ic is None:
                reasons[f"m1:{why}"] = reasons.get(f"m1:{why}", 0) + 1
            else:
                ic_m1[j] = ic
            n_m1_names.append(len(names1))
            if T in x0:
                inter = [s for s in names1 if s in x0[T]]
                ic0i, why0 = spearman_ic(
                    np.array([x0[T][s][0] for s in inter]),
                    np.array([x0[T][s][1] for s in inter]))
                if ic0i is None:
                    reasons[f"m0int:{why0}"] = \
                        reasons.get(f"m0int:{why0}", 0) + 1
                else:
                    ic_m0_int[j] = ic0i
    q3 = ev.q3_cross_sectional(ic_m0_full, seed)
    q4 = ev.q4_incremental_cross_sectional(ic_m0_int, ic_m1, seed)

    # ---------------- 70.6 D.3 descriptive block (direction, pooled OOS)
    y = np.array([d0[T][2] for T in common_T])
    desc = {}
    for label, dd in (("M0", d0), ("M1", d1)):
        p = np.array([dd[T][0] for T in common_T])
        rep = cal.reliability_report(p, y)
        nxt = np.array([panel["r_btc"][T] for T in common_T])
        bins = np.clip((p * 10).astype(int), 0, 9)
        bin_rows = []
        for b in range(10):
            m = bins == b
            if m.sum() == 0:
                bin_rows.append({"bin": f"{b/10:.1f}-{(b+1)/10:.1f}",
                                 "n": 0})
                continue
            bin_rows.append({
                "bin": f"{b/10:.1f}-{(b+1)/10:.1f}", "n": int(m.sum()),
                "up_rate": _f(y[m].mean()),
                "mean_next_ret": _f(nxt[m].mean()),
                "median_next_ret": _f(np.median(nxt[m]))})
        desc[label] = {"reliability": rep, "prob_bins": bin_rows,
                       "bss": _f(ev.bss(
                           np.array([(dd[T][0] - dd[T][2]) ** 2
                                     for T in common_T]), bs_cl))}

    # ---------------- write artifacts
    with (OUT / "daily.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "header": True, "trial": "G3-trial-1", "seed": seed,
            "lock_commit": LOCK_COMMIT,
            "oos_days_direction": len(common_T)}) + "\n")
        for j, T in enumerate(all_T):
            fh.write(json.dumps({
                "date": DATES[T].isoformat(),
                "ic_m0": _f(ic_m0_full[j]), "ic_m1": _f(ic_m1[j]),
                "ic_m0_intersection": _f(ic_m0_int[j])}) + "\n")
        for T in common_T:
            fh.write(json.dumps({
                "date": DATES[T].isoformat(), "p_m0": _f(d0[T][0]),
                "p_m1": _f(d1[T][0]), "p_clim": _f(d0[T][1]),
                "y": d0[T][2]}) + "\n")

    summary = {
        "trial": "G3-trial-1", "seed": seed, "lock_commit": LOCK_COMMIT,
        "oos_days_direction": len(common_T),
        "ic_dates_m0_defined": int(np.isfinite(ic_m0_full).sum()),
        "ic_dates_m1_defined": int(np.isfinite(ic_m1).sum()),
        "ic_dates_diff_defined": int(
            (np.isfinite(ic_m0_int) & np.isfinite(ic_m1)).sum()),
        "ic_undefined_reasons": reasons,
        "xsec_names_mean_m0": _f(np.mean(n_m0_names)),
        "xsec_names_mean_m1": _f(np.mean(n_m1_names)),
        "Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4,
        "descriptive_d3": desc,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # ---------------- print the record
    print()
    print(f"OOS direction days (both models defined): {len(common_T)}")
    print(f"IC dates defined: M0 {summary['ic_dates_m0_defined']}  "
          f"M1 {summary['ic_dates_m1_defined']}  paired-diff "
          f"{summary['ic_dates_diff_defined']}")
    print(f"IC undefined reasons: {reasons}")
    print(f"mean cross-section size: M0 "
          f"{summary['xsec_names_mean_m0']}  M1 "
          f"{summary['xsec_names_mean_m1']}")
    print()
    print(f"Q1  BSS_M0 = {q1['stat']:+.6f}  CI90 = "
          f"[{q1['ci90'][0]:+.6f}, {q1['ci90'][1]:+.6f}]  "
          f"half-width {q1['ci_half_width']:.6f}  VERDICT {q1['verdict']}")
    l1, l2 = q2["leg_bss_m1"], q2["leg_bss_diff"]
    print(f"Q2  BSS_M1 = {l1['stat']:+.6f}  CI90 = "
          f"[{l1['ci90'][0]:+.6f}, {l1['ci90'][1]:+.6f}]  leg "
          f"{l1['verdict']}")
    print(f"    BSS_M1-BSS_M0 = {l2['stat']:+.6f}  CI90 = "
          f"[{l2['ci90'][0]:+.6f}, {l2['ci90'][1]:+.6f}]  leg "
          f"{l2['verdict']}   VERDICT {q2['verdict']}")
    print(f"Q3  IC_M0 = {q3['stat']:+.6f}  CI90 = "
          f"[{q3['ci90'][0]:+.6f}, {q3['ci90'][1]:+.6f}]  "
          f"half-width {q3['ci_half_width']:.6f}  VERDICT {q3['verdict']}")
    l1, l2 = q4["leg_ic_m1"], q4["leg_ic_diff"]
    print(f"Q4  IC_M1 = {l1['stat']:+.6f}  CI90 = "
          f"[{l1['ci90'][0]:+.6f}, {l1['ci90'][1]:+.6f}]  leg "
          f"{l1['verdict']}")
    print(f"    IC_M1-IC_M0(paired) = {l2['stat']:+.6f}  CI90 = "
          f"[{l2['ci90'][0]:+.6f}, {l2['ci90'][1]:+.6f}]  leg "
          f"{l2['verdict']}  n_common {q4['n_common_dates']}   "
          f"VERDICT {q4['verdict']}")
    print()
    for label in ("M0", "M1"):
        d = desc[label]
        print(f"D.3 {label}: BSS {d['bss']}  log_loss "
              f"{d['reliability']['log_loss']}  brier_binned "
              f"{d['reliability']['brier_binned']}")
        for row in d["prob_bins"]:
            if row["n"]:
                print(f"    P(up) {row['bin']}: n={row['n']} "
                      f"up_rate={row['up_rate']} "
                      f"mean={row['mean_next_ret']} "
                      f"median={row['median_next_ret']}")
    print()
    print("VERDICTS: Q1", q1["verdict"], "| Q2", q2["verdict"],
          "| Q3", q3["verdict"], "| Q4", q4["verdict"])


if __name__ == "__main__":
    main()

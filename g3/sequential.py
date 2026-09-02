"""
The sequential-in-time protocol (NOTES 70.2.3 / 68.8): fit on 2020,
forecast 2021; refit 2020-21, forecast 2022; refit 2020-22, forecast
2023; refit 2020-23, forecast 2024. A later year never informs an
earlier forecast; the harness enforces it structurally and a test pins
it.
"""

from __future__ import annotations

from datetime import date

SEGMENTS = (
    (date(2020, 1, 1), date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31)),
    (date(2020, 1, 1), date(2021, 12, 31), date(2022, 1, 1), date(2022, 12, 31)),
    (date(2020, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2023, 12, 31)),
    (date(2020, 1, 1), date(2023, 12, 31), date(2024, 1, 1), date(2024, 12, 31)),
)


def inner_folds(dates: list[date]) -> list[tuple[list[int], list[int]]]:
    """The FROZEN 70.6.7 inner split of one training window: expanding
    folds at CALENDAR-YEAR boundaries (fit the first k years, validate
    year k+1, for every k); a single-year window splits at QUARTER
    boundaries instead (fit Q1 -> val Q2; Q1-Q2 -> Q3; Q1-Q3 -> Q4).
    `dates` are the window's own dates, chronological; returned index
    pairs are positions within the window."""
    years = sorted({d.year for d in dates})
    folds = []
    if len(years) > 1:
        for k in range(1, len(years)):
            fit = [i for i, d in enumerate(dates) if d.year in years[:k]]
            val = [i for i, d in enumerate(dates) if d.year == years[k]]
            if fit and val:
                folds.append((fit, val))
    else:
        q = {i: (d.month - 1) // 3 for i, d in enumerate(dates)}
        for k in range(1, 4):
            fit = [i for i in q if q[i] < k]
            val = [i for i in q if q[i] == k]
            if fit and val:
                folds.append((fit, val))
    if not folds:
        raise SequentialViolation("no admissible inner folds")
    for fit, val in folds:
        if max(dates[i] for i in fit) >= min(dates[i] for i in val):
            raise SequentialViolation("inner fold reaches its validation")
    return folds


class SequentialViolation(RuntimeError):
    """A fit window reached its own target dates. Not a warning."""


def run_sequential(dates: list[date], fit_and_forecast) -> dict:
    """dates: the full chronological development axis.
    fit_and_forecast(fit_idx, target_idx, segment_no) -> per-segment
    payload. The harness slices indices and REFUSES any overlap or any
    fit date at/after the first target date."""
    out = {}
    for seg_no, (fs, fe, ts, te) in enumerate(SEGMENTS, start=1):
        fit_idx = [i for i, d in enumerate(dates) if fs <= d <= fe]
        tgt_idx = [i for i, d in enumerate(dates) if ts <= d <= te]
        if not fit_idx or not tgt_idx:
            raise SequentialViolation(f"segment {seg_no}: empty window")
        if max(dates[i] for i in fit_idx) >= min(dates[i] for i in tgt_idx):
            raise SequentialViolation(
                f"segment {seg_no}: fit window reaches its target dates")
        if set(fit_idx) & set(tgt_idx):
            raise SequentialViolation(f"segment {seg_no}: overlap")
        out[seg_no] = fit_and_forecast(fit_idx, tgt_idx, seg_no)
    return out

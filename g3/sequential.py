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

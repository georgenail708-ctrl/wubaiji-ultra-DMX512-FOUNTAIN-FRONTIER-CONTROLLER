"""Job-local cooperation budget for the unchanged owned-input checkpoints.

Reference: existing R205/R191 cancellation and mutable-input contracts; Python
3.13 time.perf_counter monotonic timing. No project inputs or results are cached.
"""
from __future__ import annotations

import time


_WORK_SECONDS = 0.003
_YIELD_SECONDS = 0.0015


class InputYieldBudget:
    """Yield after a short work interval, with a new instance for each job.

    check_cancel still checks retirement both before and after every invocation.
    Sample again after sleep so time spent asleep is outside the work budget.
    The caller retains its original checkpoint locations and foreign callbacks.
    """

    __slots__ = ("_resumed_at",)

    def __init__(self):
        self._resumed_at = time.perf_counter()

    def __call__(self):
        if time.perf_counter() - self._resumed_at < _WORK_SECONDS:
            return
        time.sleep(_YIELD_SECONDS)
        self._resumed_at = time.perf_counter()

"""Timed step runner for split Fusion builds: one execute call per context, steps run in the given order."""

from __future__ import annotations

import time
from collections.abc import Callable


def run_steps(steps: dict[str, Callable[[], object]], context: str) -> dict[str, float]:
    """Run the steps named in comma-separated `context`; return seconds per step.

    Empty context, unknown or duplicate names raise ValueError before anything runs.
    """
    names = [n.strip() for n in context.split(",") if n.strip()]
    valid = ", ".join(steps)
    if not names:
        raise ValueError(f"empty context; valid steps: {valid}")
    unknown = [n for n in names if n not in steps]
    if unknown:
        raise ValueError(f"unknown step(s) {unknown}; valid steps: {valid}")
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"duplicate step(s) {dupes}; each step may run once per call")
    timings: dict[str, float] = {}
    t_all = time.perf_counter()
    for name in names:
        t0 = time.perf_counter()
        steps[name]()
        timings[name] = time.perf_counter() - t0
        print(f"STEP {name} {timings[name]:.1f} s")
    print(f"STEPS total {time.perf_counter() - t_all:.1f} s")
    return timings

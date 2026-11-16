"""fusion_kit.steps.run_steps: order, validation before running, timings, STEP lines. Offline, no adsk."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

from fusion_kit.steps import run_steps


def _make(log: list[str]) -> dict[str, Callable[[], object]]:
    def make(name: str) -> Callable[[], object]:
        def step() -> None:
            log.append(name)

        return step

    return {name: make(name) for name in ("a", "b", "c")}


def test_runs_in_context_order() -> None:
    log: list[str] = []
    run_steps(_make(log), "c, a,b")
    assert log == ["c", "a", "b"]


def test_skips_empty_items() -> None:
    log: list[str] = []
    run_steps(_make(log), " a,, b ,")
    assert log == ["a", "b"]


def test_unknown_name_runs_nothing() -> None:
    log: list[str] = []
    with pytest.raises(ValueError, match="zzz") as err:
        run_steps(_make(log), "a,zzz,b")
    assert log == []
    assert "a, b, c" in str(err.value)


def test_empty_context_raises() -> None:
    log: list[str] = []
    for context in ("", " , ,"):
        with pytest.raises(ValueError, match="empty context"):
            run_steps(_make(log), context)
    assert log == []


def test_duplicate_name_runs_nothing() -> None:
    log: list[str] = []
    with pytest.raises(ValueError, match="duplicate"):
        run_steps(_make(log), "a,b,a")
    assert log == []


def test_timings_keys_and_values() -> None:
    timings = run_steps(_make([]), "b,a")
    assert set(timings) == {"a", "b"}
    assert all(isinstance(v, float) and v >= 0.0 for v in timings.values())


def test_prints_step_lines(capsys: pytest.CaptureFixture[str]) -> None:
    run_steps(_make([]), "a,b")
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert re.fullmatch(r"STEP a \d+\.\d s", lines[0])
    assert re.fullmatch(r"STEP b \d+\.\d s", lines[1])
    assert re.fullmatch(r"STEPS total \d+\.\d s", lines[2])


def test_failing_step_stops_run() -> None:
    log: list[str] = []
    steps = _make(log)

    def boom() -> None:
        raise RuntimeError("boom")

    steps["b"] = boom
    with pytest.raises(RuntimeError, match="boom"):
        run_steps(steps, "a,b,c")
    assert log == ["a"]

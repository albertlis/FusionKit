"""probe_sketch: PROBE line format, FAIL shared-coordinate points, rollback raise. Offline, sketch faked."""

from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit.probe import probe_sketch

OK_LINE = "PROBE tri ok constraints=3 dims=2 fully=False"
FAIL_LINE = "PROBE bad FAIL VCS_SKETCH_OVER_CONSTRAINTS constraints=3 dims=2 fully=False"


def _fake_sketch(points: list[tuple[float, float]]) -> Any:
    """Sketch stand-in: points in sketch cm, 3 constraints, 2 dims, not fully constrained."""
    return SimpleNamespace(
        geometricConstraints=SimpleNamespace(count=3),
        sketchDimensions=SimpleNamespace(count=2),
        isFullyConstrained=False,
        sketchPoints=[SimpleNamespace(geometry=SimpleNamespace(x=x, y=y)) for x, y in points],
    )


def _ok(_sk: Any) -> None:
    return None


def _boom(_sk: Any) -> None:
    raise RuntimeError("VCS_SKETCH_OVER_CONSTRAINTS")


def test_ok_line_format() -> None:
    assert probe_sketch(_fake_sketch([]), [("tri", _ok)], rollback=False) == OK_LINE


def test_step_receives_sketch() -> None:
    seen: list[Any] = []
    sk = _fake_sketch([])
    probe_sketch(sk, [("tri", seen.append)], rollback=False)
    assert seen == [sk]


def test_fail_reports_shared_coordinate_points() -> None:
    sk = _fake_sketch([(1.0, 2.0), (1.0, 5.0), (3.0, 5.0)])
    lines = probe_sketch(sk, [("bad", _boom)], rollback=False).splitlines()
    assert lines[0] == FAIL_LINE
    assert lines[1] == "PROBE bad SHARED x=10.000mm pts=(10.000,20.000) (10.000,50.000)"
    assert lines[2] == "PROBE bad SHARED y=50.000mm pts=(10.000,50.000) (30.000,50.000)"
    assert len(lines) == 3


def test_fail_without_shared_points_has_no_shared_lines() -> None:
    log = probe_sketch(_fake_sketch([(0.0, 0.0), (1.0, 2.0)]), [("bad", _boom)], rollback=False)
    assert log == FAIL_LINE


def test_failure_does_not_stop_later_steps() -> None:
    log = probe_sketch(_fake_sketch([]), [("bad", _boom), ("tri", _ok)], rollback=False)
    assert [ln.split()[2] for ln in log.splitlines()] == ["FAIL", "ok"]


def test_rollback_raises_with_log() -> None:
    with pytest.raises(RuntimeError, match="PROBE ROLLBACK") as info:
        probe_sketch(_fake_sketch([]), [("tri", _ok), ("bad", _boom)])
    assert OK_LINE in str(info.value)
    assert "PROBE bad FAIL VCS_SKETCH_OVER_CONSTRAINTS" in str(info.value)

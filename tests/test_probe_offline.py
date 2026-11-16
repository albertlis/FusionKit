"""inside_runs / merge_runs / edges_at_xy: offline, adsk faked by tests/conftest.py.

Geometry is faked: body = pointContainment closure over z-spans (mm), edges = SimpleNamespace bboxes.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import enums
from fusion_kit.features import _xy_hit, edges_at_xy
from fusion_kit.report import inside_runs, merge_runs

T, F = True, False


def _fake_body(spans: list[tuple[float, float]], seen: list[tuple[float, float]]) -> Any:
    """Body inside z-spans (mm) on vertical line; ON at span ends, INSIDE between, OUTSIDE else."""

    def containment(p: Any) -> Any:
        x, y, z = p.x * 10, p.y * 10, p.z * 10
        seen.append((round(x, 6), round(y, 6)))
        for lo, hi in spans:
            if abs(z - lo) < 1e-6 or abs(z - hi) < 1e-6:
                return enums.POINT_ON
            if lo < z < hi:
                return enums.POINT_INSIDE
        return enums.POINT_OUTSIDE

    return SimpleNamespace(pointContainment=containment)


def _edge(x0: float, y0: float, x1: float, y1: float) -> SimpleNamespace:
    """Edge with XY bbox from mm corners; stored in cm like Fusion."""
    bb = SimpleNamespace(
        minPoint=SimpleNamespace(x=min(x0, x1) / 10, y=min(y0, y1) / 10),
        maxPoint=SimpleNamespace(x=max(x0, x1) / 10, y=max(y0, y1) / 10),
    )
    return SimpleNamespace(boundingBox=bb)


def test_merge_runs_empty() -> None:
    assert merge_runs([], []) == []


def test_merge_runs_single_run_inclusive_ends() -> None:
    assert merge_runs([0, 1, 2, 3, 4], [F, T, T, T, F]) == [(1, 3)]


def test_merge_runs_two_runs() -> None:
    assert merge_runs([0, 1, 2], [T, F, T]) == [(0, 0), (2, 2)]


def test_merge_runs_trailing_open_run_kept() -> None:
    assert merge_runs([0, 1, 2], [F, T, T]) == [(1, 2)]


def test_merge_runs_all_true() -> None:
    assert merge_runs([0, 1, 2], [T, T, T]) == [(0, 2)]


def test_merge_runs_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        merge_runs([0, 1], [T])


@pytest.mark.usefixtures("point3d_xyz")
def test_inside_runs_one_span_on_boundaries() -> None:
    seen: list[tuple[float, float]] = []
    body = _fake_body([(2.0, 5.0)], seen)
    assert inside_runs(body, 0.0, 0.0, 0.0, 10.0, step_mm=0.5) == [(2.0, 5.0)]


@pytest.mark.usefixtures("point3d_xyz")
def test_inside_runs_two_spans_two_runs() -> None:
    seen: list[tuple[float, float]] = []
    body = _fake_body([(2.0, 3.0), (6.0, 7.0)], seen)
    assert inside_runs(body, 0.0, 0.0, 0.0, 10.0, step_mm=0.5) == [(2.0, 3.0), (6.0, 7.0)]


@pytest.mark.usefixtures("point3d_xyz")
def test_inside_runs_samples_count_and_mm_to_cm() -> None:
    seen: list[tuple[float, float]] = []
    body = _fake_body([(2.0, 3.0)], seen)
    inside_runs(body, 1.5, -2.0, 0.0, 10.0, step_mm=0.5)
    assert len(seen) == 21  # 0..10 step 0.5, both ends
    assert set(seen) == {(1.5, -2.0)}  # mm handed to pointContainment as cm, read back as mm


@pytest.mark.usefixtures("point3d_xyz")
def test_inside_runs_no_inside_is_empty() -> None:
    body = _fake_body([(20.0, 30.0)], [])
    assert inside_runs(body, 0.0, 0.0, 0.0, 10.0, step_mm=0.5) == []


@pytest.mark.usefixtures("point3d_xyz")
def test_inside_runs_rejects_bad_step() -> None:
    with pytest.raises(ValueError):
        inside_runs(_fake_body([], []), 0.0, 0.0, 0.0, 1.0, step_mm=0.0)


def test_xy_hit_inside_and_tolerance() -> None:
    lo, hi = (0.0, 0.0), (10.0, 10.0)
    assert _xy_hit(lo, hi, 5.0, 5.0, 0.05)
    assert _xy_hit(lo, hi, 10.04, 5.0, 0.05)  # within tol past max x
    assert not _xy_hit(lo, hi, 10.06, 5.0, 0.05)  # beyond tol
    assert _xy_hit(lo, hi, 5.0, -0.04, 0.05)  # within tol below min y
    assert not _xy_hit(lo, hi, 5.0, -0.06, 0.05)


def test_edges_at_xy_picks_bbox_hits() -> None:
    horiz = _edge(0.0, 0.0, 10.0, 0.0)  # bbox y=0 line
    far = _edge(20.0, 20.0, 30.0, 30.0)
    diag = _edge(0.0, 0.0, 10.0, 10.0)  # bbox covers (1,9) though edge does not
    body = SimpleNamespace(edges=[horiz, far, diag])
    got: Callable[..., Any] = edges_at_xy
    result = got(body, 5.0, 0.0, 0.05)
    assert len(result) == 2
    assert result[0] is horiz and result[1] is diag
    diag_only = got(body, 1.0, 9.0, 0.05)
    assert len(diag_only) == 1 and diag_only[0] is diag


def test_edges_at_xy_none_found() -> None:
    body = SimpleNamespace(edges=[_edge(0.0, 0.0, 10.0, 0.0)])
    assert edges_at_xy(body, 50.0, 50.0) == []

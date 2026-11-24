"""Offline test: sketch.circle offsets its initial centre in-plane by the shared jitter (not exact).

No Fusion runtime: adsk stubs and Point3D fake come from conftest.py (point3d_xyz fixture).
"""

from types import SimpleNamespace
from typing import Any

import pytest

import fusion_kit.sketch as fk_sketch
from fusion_kit.sketch import JITTER_MM, circle


class _Circles:
    def __init__(self) -> None:
        self.centres: list[Any] = []

    @property
    def count(self) -> int:
        return len(self.centres)

    def addByCenterRadius(self, centre: Any, radius: float) -> Any:
        self.centres.append(centre)
        return SimpleNamespace(centerSketchPoint=SimpleNamespace(geometry=centre))


def _sketch() -> tuple[Any, _Circles]:
    circles = _Circles()
    sk = SimpleNamespace(modelToSketchSpace=lambda p: p, sketchCurves=SimpleNamespace(sketchCircles=circles))
    return sk, circles


def test_circle_centre_jittered_in_plane(point3d_xyz: None) -> None:
    sk, circles = _sketch()
    circle(sk, 10.0, 5.0, 2.0)
    centre = circles.centres[0]
    assert (centre.x * 10, centre.y * 10) == pytest.approx((10.0 + JITTER_MM, 5.0 + JITTER_MM))
    assert centre.z == 0.0


def test_two_circles_same_centre_get_distinct_centres(point3d_xyz: None) -> None:
    # Constant jitter put both centres on one point -> Fusion infers coincidence -> VCS_SKETCH_OVER_CONSTRAINTS.
    sk, circles = _sketch()
    circle(sk, 10.0, 5.0, 2.0)
    circle(sk, 10.0, 5.0, 1.0)
    first, second = circles.centres
    assert (first.x, first.y) != (second.x, second.y)


def test_circle_expr_centre_evaluated_for_initial_placement(point3d_xyz: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Expression strings (no mm() call in part code): evaluated via mm() for the drawn position only, then jittered.
    monkeypatch.setattr(fk_sketch, "mm", lambda expr: float(expr))  # fake: mm() needs a live Fusion design
    sk, circles = _sketch()
    circle(sk, "10", "5", 2.0, "0")
    centre = circles.centres[0]
    assert (centre.x * 10, centre.y * 10, centre.z) == pytest.approx((10.0 + JITTER_MM, 5.0 + JITTER_MM, 0.0))


def test_circle_mixed_float_and_expr_centre(point3d_xyz: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fk_sketch, "mm", lambda expr: float(expr))
    sk, circles = _sketch()
    circle(sk, 10.0, "5", 2.0)
    centre = circles.centres[0]
    assert (centre.x * 10, centre.y * 10) == pytest.approx((10.0 + JITTER_MM, 5.0 + JITTER_MM))

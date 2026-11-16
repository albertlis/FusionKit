"""Offline tests for fusion_kit.sketch.poly: returned handles, lock_axes H/V constraints.

No Fusion runtime: adsk stubs and Point3D fake come from conftest.py (point3d_xyz fixture).
Model mm in, sketch cm out (to_sketch divides by 10).
"""

from types import SimpleNamespace
from typing import Any

from fusion_kit.sketch import poly

L_SHAPE = [(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)]
TRIANGLE = [(0, 0), (10, 5), (3, 12)]


class _GC:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def addHorizontal(self, line: Any) -> None:
        self.calls.append(("H", line))

    def addVertical(self, line: Any) -> None:
        self.calls.append(("V", line))


class _Lines:
    def addByTwoPoints(self, a: Any, b: Any) -> Any:
        return SimpleNamespace(startSketchPoint=a, endSketchPoint=b)


def _sketch() -> Any:
    return SimpleNamespace(
        modelToSketchSpace=lambda p: p,
        sketchCurves=SimpleNamespace(sketchLines=_Lines()),
        geometricConstraints=_GC(),
    )


def test_lock_axes_l_shape_adds_3h_3v(point3d_xyz: None) -> None:
    s = _sketch()
    lines = poly(s, L_SHAPE, lock_axes=True)
    # edges: 0 H, 1 V, 2 H, 3 V, 4 H, 5 V (5 = closing edge back to vertex 0)
    assert s.geometricConstraints.calls == [
        ("H", lines[0]),
        ("V", lines[1]),
        ("H", lines[2]),
        ("V", lines[3]),
        ("H", lines[4]),
        ("V", lines[5]),
    ]


def test_lock_axes_triangle_slanted_adds_none(point3d_xyz: None) -> None:
    s = _sketch()
    lines = poly(s, TRIANGLE, lock_axes=True)
    assert len(lines) == 3
    assert s.geometricConstraints.calls == []


def test_default_lock_axes_false_adds_none(point3d_xyz: None) -> None:
    s = _sketch()
    lines = poly(s, L_SHAPE)
    assert len(lines) == 6
    assert s.geometricConstraints.calls == []


def test_returns_handles_with_start_points_in_vertex_order(point3d_xyz: None) -> None:
    s = _sketch()
    lines = poly(s, L_SHAPE)
    starts = [(ln.startSketchPoint.x, ln.startSketchPoint.y) for ln in lines]
    assert starts == [
        (0.0, 0.0),
        (2.0, 0.0),
        (2.0, 1.0),
        (1.0, 1.0),
        (1.0, 2.0),
        (0.0, 2.0),
    ]

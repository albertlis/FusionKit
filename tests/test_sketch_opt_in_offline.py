"""Offline tests for opt-in sketch helpers: param_poly(mode="segments"), circle(concentric_to=), polar_point().

Same mocks as test_param_poly_hv_offline.py: adsk stubs + Point3D fake from conftest.py (point3d_xyz), mm() replaced by a
symbol table. Sketch = identity map of model coords (mm/10 = cm), sketch z = model z (normal).
"""

import math
from types import SimpleNamespace
from typing import Any

import pytest

import fusion_kit.sketch as fk_sketch
from fusion_kit.enums import HORIZONTAL_DIM, VERTICAL_DIM
from fusion_kit.sketch import circle, param_poly, polar_point

VALUES = {"W": 40.0, "T": 5.0, "H": 30.0, "Z": 5.0}  # mm

L_POLY = [("0", "0", "Z"), ("W", "0", "Z"), ("W", "T", "Z"), ("T", "T", "Z"), ("T", "H", "Z"), ("0", "H", "Z")]
TRI_DISTINCT = [("W", "T", "Z"), ("H", "0", "Z"), ("T", "H", "Z")]


class _Pt:
    def __init__(self, x: float, y: float) -> None:
        self.geometry = SimpleNamespace(x=x, y=y, z=0.0)

    def move(self, v: Any) -> bool:
        raise AssertionError("no move expected in these polygons")


class _Lines:
    def __init__(self) -> None:
        self.items: list[Any] = []

    @property
    def count(self) -> int:
        return len(self.items)

    def addByTwoPoints(self, a: Any, b: Any) -> Any:
        sa = a if isinstance(a, _Pt) else _Pt(a.x, a.y)
        sb = b if isinstance(b, _Pt) else _Pt(b.x, b.y)
        line = SimpleNamespace(startSketchPoint=sa, endSketchPoint=sb, isConstruction=False)
        self.items.append(line)
        return line


class _Circles:
    def __init__(self) -> None:
        self.items: list[Any] = []

    @property
    def count(self) -> int:
        return len(self.items)

    def addByCenterRadius(self, c: Any, r: float) -> Any:
        circ = SimpleNamespace(centerSketchPoint=_Pt(c.x, c.y), radius=r)
        self.items.append(circ)
        return circ


class _GC:
    """Records each constraint with its DOF weight: coincident removes 2, every other constraint removes 1."""

    def __init__(self, log: list[tuple[str, int]]) -> None:
        self._log = log

    def __iter__(self) -> Any:
        return iter([])  # no pre-existing constraints in this fake

    def addCoincident(self, a: Any, b: Any) -> None:
        self._log.append(("coincident", 2))

    def addHorizontal(self, line: Any) -> None:
        self._log.append(("horizontal", 1))

    def addVertical(self, line: Any) -> None:
        self._log.append(("vertical", 1))

    def addHorizontalPoints(self, a: Any, b: Any) -> None:
        self._log.append(("horizontal_points", 1))

    def addVerticalPoints(self, a: Any, b: Any) -> None:
        self._log.append(("vertical_points", 1))

    def addConcentric(self, a: Any, b: Any) -> None:
        self._log.append(("concentric", 1))


class _Dims:
    def __init__(self, log: list[tuple[str, int]], orientations: list[Any]) -> None:
        self._log = log
        self._orient = orientations

    def addDistanceDimension(self, a: Any, b: Any, orientation: Any, pos: Any) -> Any:
        self._log.append(("dim", 1))
        self._orient.append(orientation)
        return SimpleNamespace(parameter=SimpleNamespace(expression=None))

    def addAngularDimension(self, a: Any, b: Any, pos: Any, is_driving: bool = True) -> Any:
        self._log.append(("angular", 1))
        return SimpleNamespace(parameter=SimpleNamespace(expression=None))

    def addDiameterDimension(self, entity: Any, pos: Any) -> Any:
        self._log.append(("diameter", 1))
        return SimpleNamespace(parameter=SimpleNamespace(expression=None))


class _Sketch(SimpleNamespace):
    lines: _Lines
    circles: _Circles
    orientations: list[Any]


def _sketch(log: list[tuple[str, int]]) -> Any:
    lines, circles, orient = _Lines(), _Circles(), []
    sk = _Sketch(
        modelToSketchSpace=lambda p: p,
        sketchCurves=SimpleNamespace(sketchLines=lines, sketchCircles=circles),
        geometricConstraints=_GC(log),
        sketchDimensions=_Dims(log, orient),
        originPoint=_Pt(0.0, 0.0),
    )
    sk.lines, sk.circles, sk.orientations = lines, circles, orient
    return sk


@pytest.fixture
def fake_mm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fk_sketch, "mm", lambda expr: float(eval(expr, {"__builtins__": {}}, dict(VALUES))))


def _count(log: list[tuple[str, int]], kind: str) -> int:
    return sum(1 for k, _ in log if k == kind)


def _dof(log: list[tuple[str, int]]) -> int:
    return sum(w for _, w in log)


def _segments(pts: list[tuple[str, str, str]]) -> tuple[list[tuple[str, int]], Any]:
    log: list[tuple[str, int]] = []
    sk = _sketch(log)
    param_poly(sk, pts, mode="segments")
    return log, sk


def test_segments_l_shape_one_link_per_edge_redundant_lengths_skipped(point3d_xyz: None, fake_mm: None) -> None:
    log, sk = _segments(L_POLY)
    assert _count(log, "horizontal") == 3  # edges 0-1, 2-3, 4-5 (sketch y constant)
    assert _count(log, "vertical") == 3  # edges 1-2, 3-4, 5-0 (sketch x constant)
    assert _count(log, "dim") == 4  # 6 edge lengths, 2 implied by closure -> skipped
    assert _count(log, "coincident") == 1  # vertex 0 at origin
    assert _dof(log) == 2 * len(L_POLY)  # fully constrained, same DOF as vertex mode
    assert sorted(sk.orientations, key=str) == sorted(
        [HORIZONTAL_DIM, HORIZONTAL_DIM, VERTICAL_DIM, VERTICAL_DIM], key=str
    )


def test_segments_rejects_diagonal_edge(point3d_xyz: None, fake_mm: None) -> None:
    with pytest.raises(ValueError, match="not axis-parallel"):
        _segments(TRI_DISTINCT)


def test_segments_rejects_hv_combination(point3d_xyz: None, fake_mm: None) -> None:
    with pytest.raises(ValueError, match="neither hv nor skip_axes"):
        param_poly(_sketch([]), L_POLY, mode="segments", hv=True)


def test_default_mode_unchanged(point3d_xyz: None, fake_mm: None) -> None:
    default: list[tuple[str, int]] = []
    explicit: list[tuple[str, int]] = []
    param_poly(_sketch(default), L_POLY)
    param_poly(_sketch(explicit), L_POLY, mode="vertices")
    assert default == explicit
    assert _count(default, "dim") == 8


def test_circle_default_has_no_concentric(point3d_xyz: None) -> None:
    log: list[tuple[str, int]] = []
    circle(_sketch(log), 0.0, 0.0, 5.0)
    assert _count(log, "concentric") == 0


def test_circle_concentric_to_ties_centre_and_keeps_diameter(point3d_xyz: None) -> None:
    log: list[tuple[str, int]] = []
    sk = _sketch(log)
    ref = circle(sk, 0.0, 0.0, 5.0)
    inner = circle(sk, 99.0, 99.0, 3.0, concentric_to=ref, diameter="6 mm")
    assert _count(log, "concentric") == 1
    assert _count(log, "diameter") == 1
    assert (
        abs(inner.centerSketchPoint.geometry.x - ref.centerSketchPoint.geometry.x) < 0.01
    )  # placed at ref centre, not 99 mm


def test_polar_point_dims_and_construction_lines(point3d_xyz: None) -> None:
    log: list[tuple[str, int]] = []
    sk = _sketch(log)
    p = polar_point(sk, sk.originPoint, "R", "A", radius_mm=10.0, angle_deg=30.0)
    assert _count(log, "horizontal") == 1  # reference line along sketch X
    assert _count(log, "dim") == 2  # reference end radius (H dim) + point radius
    assert _count(log, "angular") == 1
    assert sk.orientations.count(HORIZONTAL_DIM) == 1
    assert all(ln.isConstruction for ln in sk.lines.items)
    assert abs(p.geometry.x - 10.0 * math.cos(math.radians(30.0)) / 10) < 0.01  # cm, jitter 0.05 mm
    assert abs(p.geometry.y - 10.0 * math.sin(math.radians(30.0)) / 10) < 0.01


@pytest.mark.parametrize("bad", [0.0, 180.0, -5.0])
def test_polar_point_rejects_angle_outside_open_interval(point3d_xyz: None, bad: float) -> None:
    sk = _sketch([])
    with pytest.raises(ValueError, match="angle_deg"):
        polar_point(sk, sk.originPoint, "R", "A", radius_mm=10.0, angle_deg=bad)

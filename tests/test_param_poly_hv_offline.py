"""Offline tests for fusion_kit.sketch.param_poly(hv=True): H/V links on edges with equal coordinate exprs, one dim per
linked set. Degrees of freedom (DOF) of the polygon must stay 2 * n (fully constrained).

No Fusion runtime: adsk stubs and Point3D fake come from conftest.py (point3d_xyz fixture). mm() is replaced by a
symbol table. Sketch = identity map of model coords (mm/10 = cm), sketch z = model z (normal).
"""

from types import SimpleNamespace
from typing import Any

import pytest

import fusion_kit.sketch as fk_sketch
from fusion_kit.sketch import param_poly

VALUES = {"W": 40.0, "T": 5.0, "H": 30.0, "Z": 5.0}  # mm

# L-shape, 6 vertices: x/y coords repeat along the edges (W, T, H, 0), so H/V links apply to every edge.
L_POLY = [("0", "0", "Z"), ("W", "0", "Z"), ("W", "T", "Z"), ("T", "T", "Z"), ("T", "H", "Z"), ("0", "H", "Z")]
# Triangle with no equal coordinate exprs on any edge: hv=True must add nothing.
TRI_DISTINCT = [("W", "T", "Z"), ("H", "0", "Z"), ("T", "H", "Z")]


class _SketchPoint:
    def __init__(self, x: float, y: float) -> None:
        self.geometry = SimpleNamespace(x=x, y=y, z=0.0)

    def move(self, v: Any) -> bool:
        raise AssertionError("no move expected: no wrong-side coordinate in these polygons")


class _Lines:
    def addByTwoPoints(self, a: Any, b: Any) -> Any:
        sa = a if isinstance(a, _SketchPoint) else _SketchPoint(a.x, a.y)
        sb = b if isinstance(b, _SketchPoint) else _SketchPoint(b.x, b.y)
        return SimpleNamespace(startSketchPoint=sa, endSketchPoint=sb)


class _GC:
    """Records each constraint with its DOF weight: coincident removes 2, every other constraint removes 1."""

    def __init__(self, log: list[tuple[str, int]]) -> None:
        self._log = log

    def __iter__(self) -> Any:
        return iter([])  # no drawing inference in this fake

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


class _Dims:
    def __init__(self, log: list[tuple[str, int]]) -> None:
        self._log = log

    def addDistanceDimension(self, a: Any, b: Any, orientation: Any, pos: Any) -> Any:
        self._log.append(("dim", 1))
        return SimpleNamespace(parameter=SimpleNamespace(expression=None))


def _sketch(log: list[tuple[str, int]]) -> Any:
    return SimpleNamespace(
        modelToSketchSpace=lambda p: p,
        sketchCurves=SimpleNamespace(sketchLines=_Lines()),
        geometricConstraints=_GC(log),
        sketchDimensions=_Dims(log),
        originPoint=_SketchPoint(0.0, 0.0),
    )


@pytest.fixture
def fake_mm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fk_sketch, "mm", lambda expr: float(eval(expr, {"__builtins__": {}}, dict(VALUES))))


def _count(log: list[tuple[str, int]], kind: str) -> int:
    return sum(1 for k, _ in log if k == kind)


def _dof(log: list[tuple[str, int]]) -> int:
    return sum(w for _, w in log)


def _run(pts: list[tuple[str, str, str]], hv: bool) -> list[tuple[str, int]]:
    log: list[tuple[str, int]] = []
    param_poly(_sketch(log), pts, hv=hv)
    return log


def test_default_one_dim_per_nonzero_coord(point3d_xyz: None, fake_mm: None) -> None:
    log = _run(L_POLY, hv=False)
    assert _count(log, "dim") == 8
    assert _count(log, "horizontal") == _count(log, "vertical") == 0
    assert _dof(log) == 2 * len(L_POLY)


def test_hv_links_and_one_dim_per_linked_set(point3d_xyz: None, fake_mm: None) -> None:
    log = _run(L_POLY, hv=True)
    assert _count(log, "horizontal") == 3  # edges 0-1, 2-3, 4-5 (sketch y constant)
    assert _count(log, "vertical") == 3  # edges 1-2, 3-4, 5-0 (sketch x constant)
    assert _count(log, "dim") == 4  # W, T (x-chain 3-4), T (y-chain 2-3), H (y-chain 4-5)
    assert _count(log, "coincident") == 1  # vertex 0 = origin (both axes zero), anchor() path as before
    assert _dof(log) == 2 * len(L_POLY)  # fully constrained, same DOF as default


def test_hv_no_shared_exprs_same_as_default(point3d_xyz: None, fake_mm: None) -> None:
    assert _run(TRI_DISTINCT, hv=True) == _run(TRI_DISTINCT, hv=False)

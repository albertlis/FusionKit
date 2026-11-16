"""Offline tests for fusion_kit.sketch.param_poly: vertex jitter, delete of constraints created by this call, per-vertex fix_point.

No Fusion runtime: adsk stubs and Point3D fake come from conftest.py (point3d_xyz fixture).
mm() and fix_point() are monkeypatched (they need the Fusion units manager / sketch dims).
"""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

import fusion_kit.sketch as fk_sketch
from fusion_kit.sketch import param_poly

TRI = [("0", "0", "5"), ("10", "0", "5"), ("10", "10", "5")]
TRI2 = [("20", "0", "5"), ("30", "0", "5"), ("30", "10", "5")]


class _Constraint:
    """Stand-in for GeometricConstraint: entityToken identifies it, deleteMe removes it from its sketch."""

    def __init__(self, token: str, owner: list[Any]) -> None:
        self.entityToken = token
        self.deleted = False
        self._owner = owner

    def deleteMe(self) -> None:
        self.deleted = True
        self._owner.remove(self)


class _DrawingLines:
    """Fusion-like sketchLines: every drawn line adds one inferred constraint to the sketch, as Fusion does."""

    def __init__(self, constraints: list[Any]) -> None:
        self._constraints = constraints
        self._n = 0
        self.made: list[_Constraint] = []

    def addByTwoPoints(self, a: Any, b: Any) -> Any:
        self._n += 1
        c = _Constraint(f"inf{self._n}", self._constraints)
        self._constraints.append(c)
        self.made.append(c)
        return SimpleNamespace(startSketchPoint=a, endSketchPoint=b)


def _sketch(constraints: list[Any], swap_yz: bool = False) -> Any:
    """swap_yz: sketch z = model y (normal axis = model y), sketch y = model z; else identity."""
    if swap_yz:
        to_s = lambda p: SimpleNamespace(x=p.x, y=p.z, z=p.y)
    else:
        to_s = lambda p: p
    return SimpleNamespace(
        modelToSketchSpace=to_s,
        sketchCurves=SimpleNamespace(sketchLines=_DrawingLines(constraints)),
        geometricConstraints=constraints,
    )


@pytest.fixture
def fake_mm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fk_sketch, "mm", lambda expr: float(expr))


@pytest.fixture
def fix_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[tuple[Any, tuple[str, str, str]]]]:
    calls: list[tuple[Any, tuple[str, str, str]]] = []
    monkeypatch.setattr(fk_sketch, "fix_point", lambda s, sp, p: calls.append((sp, p)))
    yield calls


def _mm_xyz(lines: list[Any]) -> list[float]:
    """Flat [x0, y0, z0, x1, ...] of line start points in model mm (sketch cm * 10)."""
    return [c * 10 for ln in lines for c in (ln.startSketchPoint.x, ln.startSketchPoint.y, ln.startSketchPoint.z)]


def test_default_jitter_in_plane_only(point3d_xyz: None, fake_mm: None, fix_calls: list[Any]) -> None:
    lines: list[Any] = param_poly(_sketch([]), TRI)
    # normal = z: x,y offset 0.05 + 0.013*k*(axis+1) mm; z exact
    assert _mm_xyz(lines) == pytest.approx([0.05, 0.05, 5.0, 10.063, 0.076, 5.0, 10.076, 10.102, 5.0])


def test_jitter_mm_zero_keeps_only_index_term(point3d_xyz: None, fake_mm: None, fix_calls: list[Any]) -> None:
    lines: list[Any] = param_poly(_sketch([]), TRI, jitter_mm=0.0)
    got = _mm_xyz(lines)
    assert [got[0], got[1], got[3], got[4], got[6], got[7]] == pytest.approx([0.0, 0.0, 10.013, 0.026, 10.026, 10.052])


def test_out_of_plane_axis_stays_exact(point3d_xyz: None, fake_mm: None, fix_calls: list[Any]) -> None:
    # sketch z = model y -> normal index 1: model y must stay exact (5 mm), model x and z are jittered
    tri = [("0", "5", "0"), ("10", "5", "0"), ("10", "5", "10")]
    lines: list[Any] = param_poly(_sketch([], swap_yz=True), tri)
    # sketch (x, y, z) = model (x, z, y): model x -> sketch x, model z -> sketch y, model y -> sketch z
    assert _mm_xyz(lines)[:3] == pytest.approx([0.05, 0.05, 5.0])


def test_inferred_constraints_deleted(point3d_xyz: None, fake_mm: None, fix_calls: list[Any]) -> None:
    sk = _sketch([])
    param_poly(sk, TRI)
    made = sk.sketchCurves.sketchLines.made
    assert len(made) == 3 and all(c.deleted for c in made)
    assert sk.geometricConstraints == []


def test_user_and_earlier_polygon_constraints_not_deleted(
    point3d_xyz: None, fake_mm: None, fix_calls: list[Any]
) -> None:
    constraints: list[Any] = []
    user = _Constraint("user", constraints)
    constraints.append(user)  # user constraint present before the first call
    sk = _sketch(constraints)
    drawing = sk.sketchCurves.sketchLines
    param_poly(sk, TRI)
    first_made = list(drawing.made)
    assert not user.deleted
    assert all(c.deleted for c in first_made)
    user2 = _Constraint("user2", constraints)
    constraints.append(user2)  # user adds a constraint between the two calls
    param_poly(sk, TRI2)
    second_made = drawing.made[len(first_made) :]
    assert len(second_made) == 3 and all(c.deleted for c in second_made)
    assert not user.deleted and not user2.deleted
    assert constraints == [user, user2]


def test_fix_point_per_vertex_with_original_exprs(point3d_xyz: None, fake_mm: None, fix_calls: list[Any]) -> None:
    lines = param_poly(_sketch([]), TRI)
    assert [p for _, p in fix_calls] == TRI
    assert all(sp is ln.startSketchPoint for (sp, _), ln in zip(fix_calls, lines))


def test_rect_two_corners_dims_and_anchor(monkeypatch: pytest.MonkeyPatch, fake_mm: None) -> None:
    # rect() mocked: returns [bottom, right, top, left]; dims go bottom=side_h, right=side_v; corner a = bottom start
    bottom = SimpleNamespace(startSketchPoint="p0", endSketchPoint="p1")
    right = SimpleNamespace(startSketchPoint="p1", endSketchPoint="p2")
    dims: list[tuple[Any, Any, str]] = []
    fixes: list[tuple[Any, tuple[str, str, str]]] = []
    monkeypatch.setattr(fk_sketch, "rect", lambda s, a, b: [bottom, right, None, None])
    monkeypatch.setattr(fk_sketch, "dim", lambda s, a, b, expr, *o: dims.append((a, b, expr)))
    monkeypatch.setattr(fk_sketch, "fix_point", lambda s, sp, p: fixes.append((sp, p)))
    fk_sketch.rect_two_corners(_sketch([]), ("0", "0", "5"), ("10", "20", "5"), "10 mm", "20 mm")
    assert dims == [("p0", "p1", "10 mm"), ("p1", "p2", "20 mm")]
    assert fixes == [("p0", ("0", "0", "5"))]

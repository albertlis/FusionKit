"""Offline tests for fusion_kit.sketch.fixed_poly: exact vertices, isFixed on every vertex, no dims,
delete of constraints created by this call only.

No Fusion runtime: adsk stubs and Point3D fake come from conftest.py (point3d_xyz fixture).
mm() is monkeypatched (it needs the Fusion units manager).
"""

from types import SimpleNamespace
from typing import Any

import pytest

import fusion_kit.sketch as fk_sketch
from fusion_kit.sketch import fixed_poly

TRI = [("0", "0", "5"), ("10", "0", "5"), ("10", "10", "5")]


class _Constraint:
    """Stand-in for GeometricConstraint: entityToken identifies it, deleteMe removes it from its sketch."""

    def __init__(self, token: str, owner: list[Any]) -> None:
        self.entityToken = token
        self.deleted = False
        self._owner = owner

    def deleteMe(self) -> None:
        self.deleted = True
        self._owner.remove(self)


class _Lines:
    """sketchLines: every drawn line adds one inferred constraint to the sketch, as Fusion does."""

    def __init__(self, constraints: list[Any]) -> None:
        self._constraints = constraints
        self.made: list[_Constraint] = []

    def addByTwoPoints(self, a: Any, b: Any) -> Any:
        c = _Constraint(f"inf{len(self.made) + 1}", self._constraints)
        self._constraints.append(c)
        self.made.append(c)
        return SimpleNamespace(startSketchPoint=a, endSketchPoint=b)


def _sketch(constraints: list[Any]) -> Any:
    return SimpleNamespace(
        modelToSketchSpace=lambda p: p,
        sketchCurves=SimpleNamespace(sketchLines=_Lines(constraints)),
        geometricConstraints=constraints,
    )


@pytest.fixture
def fake_mm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fk_sketch, "mm", lambda expr: float(expr))


@pytest.fixture
def no_dims(monkeypatch: pytest.MonkeyPatch) -> None:
    """fixed_poly must not dimension anything: any fix_point / anchor call fails the test."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("fixed_poly must not add dims")

    monkeypatch.setattr(fk_sketch, "fix_point", boom)
    monkeypatch.setattr(fk_sketch, "anchor", boom)


def _mm_xyz(lines: list[Any]) -> list[float]:
    """Flat [x0, y0, z0, x1, ...] of line start points in model mm (sketch cm * 10)."""
    return [c * 10 for ln in lines for c in (ln.startSketchPoint.x, ln.startSketchPoint.y, ln.startSketchPoint.z)]


def test_vertices_exact_and_all_fixed(point3d_xyz: None, fake_mm: None, no_dims: None) -> None:
    lines = fixed_poly(_sketch([]), TRI)
    assert _mm_xyz(lines) == pytest.approx([0.0, 0.0, 5.0, 10.0, 0.0, 5.0, 10.0, 10.0, 5.0])
    assert [ln.startSketchPoint.isFixed for ln in lines] == [True, True, True]


def test_inferred_constraints_deleted(point3d_xyz: None, fake_mm: None, no_dims: None) -> None:
    sk = _sketch([])
    fixed_poly(sk, TRI)
    made = sk.sketchCurves.sketchLines.made
    assert len(made) == 3 and all(c.deleted for c in made)
    assert sk.geometricConstraints == []


def test_user_constraint_kept(point3d_xyz: None, fake_mm: None, no_dims: None) -> None:
    constraints: list[Any] = []
    user = _Constraint("user", constraints)
    constraints.append(user)
    sk = _sketch(constraints)
    fixed_poly(sk, TRI)
    assert not user.deleted
    assert constraints == [user]

"""Offline tests: features.pattern_rectangular and sketch.anchor(ref=) — no Fusion runtime.

adsk stubs come from conftest.py. Geometry is faked with plain objects; sketch units are Fusion cm.
"""

import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import enums, features
from fusion_kit import features_pattern
from fusion_kit.features import pattern_rectangular
from fusion_kit.sketch import anchor

_CORE: Any = sys.modules["adsk.core"]
_FUS: Any = sys.modules["adsk.fusion"]


# ---------------------------------------------------------------------------
# enums: PatternComputeOptions members
# ---------------------------------------------------------------------------
def test_pattern_compute_option_constants_map_to_stub_members() -> None:
    assert enums.OPTIMIZED_PATTERN is _FUS.PatternComputeOptions.OptimizedPatternCompute
    assert enums.IDENTICAL_PATTERN is _FUS.PatternComputeOptions.IdenticalPatternCompute
    assert enums.ADJUST_PATTERN is _FUS.PatternComputeOptions.AdjustPatternCompute


# ---------------------------------------------------------------------------
# pattern_rectangular(): fake comp.features.rectangularPatternFeatures
# ---------------------------------------------------------------------------
@pytest.fixture
def pattern_comp(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, Any, Any]]:
    monkeypatch.setattr(features_pattern, "_count", lambda c, e: float(e))  # qty "4" -> 4.0 (no Fusion runtime)
    coll = MagicMock(name="coll")
    _CORE.ObjectCollection.create.return_value = coll
    rpf = MagicMock(name="rectangularPatternFeatures")
    inp = MagicMock(name="rin")
    rpf.createInput.return_value = inp
    comp = SimpleNamespace(features=SimpleNamespace(rectangularPatternFeatures=rpf))
    yield comp, coll, inp
    _CORE.ObjectCollection.create.return_value = MagicMock(name="default_coll")


def test_pattern_rectangular_default_spacing_and_optimized_compute(
    pattern_comp: tuple[Any, Any, Any],
) -> None:
    comp, coll, inp = pattern_comp
    feat, axis = MagicMock(name="feat"), MagicMock(name="axis")
    pattern_rectangular(comp, [feat], axis, "4", "10 mm", label="t_pattern")
    rpf = comp.features.rectangularPatternFeatures
    rpf.createInput.assert_called_once()
    args = rpf.createInput.call_args.args
    assert args[0] is coll
    assert args[1] is axis
    assert args[4] is enums.PATTERN_SPACING
    coll.add.assert_called_once_with(feat)
    _CORE.ValueInput.createByString.assert_any_call("4")
    _CORE.ValueInput.createByString.assert_any_call("10 mm")
    assert inp.patternComputeOption is enums.OPTIMIZED_PATTERN
    # direction two forced to qty 1 (unset -> Fusion default 3 copies per instance)
    inp.setDirectionTwo.assert_called_once()
    assert inp.setDirectionTwo.call_args.args[0] is axis
    _CORE.ValueInput.createByString.assert_any_call("1")
    rpf.add.assert_called_once_with(inp)


def test_pattern_rectangular_extent_type_passed_through(
    pattern_comp: tuple[Any, Any, Any],
) -> None:
    comp, _, _ = pattern_comp
    pattern_rectangular(
        comp,
        [MagicMock()],
        MagicMock(),
        "4",
        "30 mm",
        distance_type=enums.PATTERN_EXTENT,
        label="t_pattern",
    )
    args = comp.features.rectangularPatternFeatures.createInput.call_args.args
    assert args[4] is enums.PATTERN_EXTENT


# ---------------------------------------------------------------------------
# anchor(ref=): fake sketch. P3 is the stub Point3D.create -> side_effect returns plain xyz (cm).
# ---------------------------------------------------------------------------
class _Pt:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y
        self.geometry = SimpleNamespace(x=x, y=y)


class _Dim:
    def __init__(self) -> None:
        self.parameter = SimpleNamespace(expression=None)


class _Rec:
    """Records geometricConstraints calls: addCoincident(p, o), addVerticalPoints(o, p), ..."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any) -> None:
            self.calls.append((name, args))

        return call


class _FSketch:
    def __init__(self) -> None:
        self.originPoint = _Pt(0.0, 0.0)
        self.geometricConstraints = _Rec()
        self.points: list[_Pt] = []
        self.dims: list[tuple[Any, Any, Any, _Dim]] = []
        self.sketchPoints = SimpleNamespace(add=self._add_point)
        self.sketchDimensions = SimpleNamespace(addDistanceDimension=self._add_dim)

    def modelToSketchSpace(self, p: Any) -> Any:
        return p  # identity: XY sketch, P3 side_effect already yields cm xyz

    def _add_point(self, p: Any) -> _Pt:
        pt = _Pt(p.x, p.y)
        self.points.append(pt)
        return pt

    def _add_dim(self, a: Any, b: Any, orient: Any, mid: Any) -> _Dim:
        d = _Dim()
        self.dims.append((a, b, orient, d))
        return d


@pytest.fixture
def sketch(point3d_xyz: None) -> Iterator[Any]:
    yield _FSketch()


def _sketch_point(s: _FSketch, x_cm: float, y_cm: float) -> Any:
    """Stand-in for a SketchPoint (has .geometry); distinct object so tests can tell p from ref."""
    return SimpleNamespace(geometry=SimpleNamespace(x=x_cm, y=y_cm))


def test_anchor_without_ref_measures_from_origin(sketch: Any) -> None:
    p = _sketch_point(sketch, 1.0, 0.5)
    anchor(sketch, p, "10 mm", "5 mm")
    assert len(sketch.dims) == 2
    assert sketch.dims[0][0] is sketch.originPoint
    assert sketch.dims[0][3].parameter.expression == "10 mm"
    assert sketch.dims[0][2] is enums.HORIZONTAL_DIM
    assert sketch.dims[1][2] is enums.VERTICAL_DIM
    assert sketch.points == []  # no ref point created


def test_anchor_ref_adds_fixed_point_and_measures_from_it(sketch: Any) -> None:
    p = _sketch_point(sketch, 1.0, 0.5)
    anchor(sketch, p, "(cx) - (-50 mm)", "(cy) - (-50 mm)", ref=(-50.0, -50.0, 0.0))
    # ref point at model (-50,-50) mm = cm (-5,-5)
    assert len(sketch.points) == 1
    ref_pt = sketch.points[0]
    assert (ref_pt.x, ref_pt.y) == (-5.0, -5.0)
    # ref fixed to origin by |coord| dims, unsigned, from origin
    assert sketch.dims[0][0] is sketch.originPoint and sketch.dims[0][1] is ref_pt
    assert sketch.dims[0][3].parameter.expression == "50 mm"
    assert sketch.dims[1][3].parameter.expression == "50 mm"
    # p measured from ref point, not origin
    assert sketch.dims[2][0] is ref_pt and sketch.dims[2][1] is p
    assert sketch.dims[2][3].parameter.expression == "(cx) - (-50 mm)"
    assert sketch.dims[3][0] is ref_pt and sketch.dims[3][1] is p


def test_anchor_ref_zero_coord_uses_alignment_not_zero_dim(sketch: Any) -> None:
    p = _sketch_point(sketch, 1.0, 0.5)
    anchor(sketch, p, "5 mm", None, ref=(0.0, -50.0, 0.0))
    # ref x == 0 -> vertical alignment with origin, no 0 mm dimension (Fusion rejects 0 dims)
    assert (
        "addVerticalPoints",
        (sketch.originPoint, sketch.points[0]),
    ) in sketch.geometricConstraints.calls
    assert all(d[3].parameter.expression != "0 mm" for d in sketch.dims)


def test_anchor_ref_both_none_is_coincident_to_ref(sketch: Any) -> None:
    p = _sketch_point(sketch, 1.0, 0.5)
    anchor(sketch, p, ref=(-50.0, -50.0, 0.0))
    ref_pt = sketch.points[0]
    assert ("addCoincident", (p, ref_pt)) in sketch.geometricConstraints.calls
    assert not any(c[0] == "addCoincident" and c[1][1] is sketch.originPoint for c in sketch.geometricConstraints.calls)


class _FSketchXZ(_FSketch):
    """XZ-like sketch: model (x, y, z) -> sketch (x, -z). Model y is the sketch normal, so y=0 is NOT a sketch zero."""

    def modelToSketchSpace(self, p: Any) -> Any:
        return SimpleNamespace(x=p.x, y=-p.z, z=0.0)


@pytest.fixture
def xz_sketch(point3d_xyz: None) -> Iterator[Any]:
    yield _FSketchXZ()


def test_anchor_ref_on_xz_sketch_dims_use_sketch_coords(xz_sketch: Any) -> None:
    # model (-50, 0, -50) -> sketch (-5, 5) cm. Model y=0 must NOT be read as a sketch-y zero.
    p = _sketch_point(xz_sketch, 1.0, 0.5)
    anchor(xz_sketch, p, "10 mm", "20 mm", ref=(-50.0, 0.0, -50.0))
    ref_pt = xz_sketch.points[0]
    assert (ref_pt.x, ref_pt.y) == (-5.0, 5.0)
    assert (
        "addHorizontalPoints",
        (xz_sketch.originPoint, ref_pt),
    ) not in xz_sketch.geometricConstraints.calls
    # ref y must be fixed by a 50 mm dim from origin (second dim), not left to alignment
    assert xz_sketch.dims[1][0] is xz_sketch.originPoint and xz_sketch.dims[1][1] is ref_pt
    assert xz_sketch.dims[1][3].parameter.expression == "50 mm"

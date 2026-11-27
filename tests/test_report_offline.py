"""Offline tests: fusion_kit.report — inside, world_bbox, interference (+ PointContainment enums).

No Fusion runtime: adsk stubs come from conftest.py. Geometry is faked: bodies are box containers,
occurrences are SimpleNamespace; cast() is faked per kind.
"""

import math
import sys
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from fusion_kit import enums
from fusion_kit.report import (
    design_stats,
    format_report,
    inside,
    inside_runs,
    interference,
    overhangs,
    report_data,
    world_bbox,
)

_FUS: Any = sys.modules["adsk.fusion"]
_CORE: Any = sys.modules["adsk.core"]

Xyz = tuple[float, float, float]


@pytest.fixture
def cast_by_kind() -> Iterator[None]:
    """Occurrence.cast / BRepBody.cast answer by the fake's .kind, like Fusion's cast (None on mismatch)."""
    _FUS.Occurrence.cast.side_effect = lambda e: e if getattr(e, "kind", "") == "occ" else None
    _FUS.BRepBody.cast.side_effect = lambda e: e if getattr(e, "kind", "") == "body" else None
    yield
    _FUS.Occurrence.cast.side_effect = None
    _FUS.BRepBody.cast.side_effect = None


def _box_body(lo_cm: Xyz, hi_cm: Xyz) -> Any:
    """Fake BRepBody: pointContainment(cm point) answers for an axis-aligned box in ITS OWN frame."""
    body = MagicMock(name="body")

    def contain(p: Any) -> Any:
        pt = (p.x, p.y, p.z)
        closed = all(a <= c <= b for a, c, b in zip(lo_cm, pt, hi_cm))
        strict = all(a < c < b for a, c, b in zip(lo_cm, pt, hi_cm))
        if strict:
            return enums.POINT_INSIDE
        if closed:
            return enums.POINT_ON
        return enums.POINT_OUTSIDE

    body.pointContainment.side_effect = contain
    return body


# ---------------------------------------------------------------------------
# enums: PointContainment members
# ---------------------------------------------------------------------------
def test_point_containment_constants_map_to_stub_members() -> None:
    assert enums.POINT_INSIDE is _FUS.PointContainment.PointInsidePointContainment
    assert enums.POINT_ON is _FUS.PointContainment.PointOnPointContainment
    assert enums.POINT_OUTSIDE is _FUS.PointContainment.PointOutsidePointContainment


# ---------------------------------------------------------------------------
# inside(): mm -> cm mapping and enum comparison
# ---------------------------------------------------------------------------
def test_inside_converts_mm_to_cm_before_containment(point3d_xyz: None) -> None:
    body = _box_body((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    inside(body, 35.0, 5.0, 5.0)
    p = body.pointContainment.call_args.args[0]
    assert (p.x, p.y, p.z) == (3.5, 0.5, 0.5)


def test_inside_true_only_for_point_inside(point3d_xyz: None) -> None:
    body = MagicMock(name="body")
    body.pointContainment.return_value = enums.POINT_INSIDE
    assert inside(body, 5.0, 5.0, 5.0) is True
    body.pointContainment.return_value = enums.POINT_ON
    assert inside(body, 5.0, 5.0, 5.0) is False
    body.pointContainment.return_value = enums.POINT_OUTSIDE
    assert inside(body, 5.0, 5.0, 5.0) is False


def test_inside_uses_body_frame_no_offset(point3d_xyz: None) -> None:
    # Native box 0..10 mm: (5,5,5) in, (15,5,5) out, (35,5,5) out (world offset does not apply to native body).
    native = _box_body((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert inside(native, 5.0, 5.0, 5.0) is True
    assert inside(native, 15.0, 5.0, 5.0) is False
    assert inside(native, 35.0, 5.0, 5.0) is False
    # Proxy box 30..40 mm (world): (35,5,5) in, (5,5,5) out.
    proxy = _box_body((3.0, 0.0, 0.0), (4.0, 1.0, 1.0))
    assert inside(proxy, 35.0, 5.0, 5.0) is True
    assert inside(proxy, 5.0, 5.0, 5.0) is False
    assert inside(proxy, 15.0, 5.0, 5.0) is False


def test_inside_surface_point_is_not_inside(point3d_xyz: None) -> None:
    body = _box_body((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert inside(body, 0.0, 5.0, 5.0) is False  # on face x=0


def test_inside_runs_reversed_range_raises() -> None:
    """z1 < z0 used to return [] silently; must raise before any containment sample."""
    body = MagicMock(name="body")
    with pytest.raises(ValueError, match="z1_mm"):
        inside_runs(body, 5.0, 5.0, 10.0, 0.0)
    body.pointContainment.assert_not_called()


# ---------------------------------------------------------------------------
# world_bbox(): min/max over occurrence proxies, cm -> mm
# ---------------------------------------------------------------------------
class _Coll:
    """Stand-in for BRepBodies: iterable with a .count attribute."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self.count = len(items)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._items)


def _body_with_box(lo_cm: Xyz, hi_cm: Xyz) -> Any:
    bb = SimpleNamespace(
        minPoint=SimpleNamespace(x=lo_cm[0], y=lo_cm[1], z=lo_cm[2]),
        maxPoint=SimpleNamespace(x=hi_cm[0], y=hi_cm[1], z=hi_cm[2]),
    )
    return SimpleNamespace(boundingBox=bb)


def _approx_pair(got: tuple[Xyz, Xyz], lo: Xyz, hi: Xyz) -> None:
    assert got[0] == pytest.approx(lo)
    assert got[1] == pytest.approx(hi)


def test_world_bbox_single_moved_body_in_mm() -> None:
    occ: Any = SimpleNamespace(
        name="probe",
        bRepBodies=_Coll([_body_with_box((3.0, 0.0, 0.0), (4.0, 1.0, 1.0))]),
    )
    _approx_pair(world_bbox(occ), (30.0, 0.0, 0.0), (40.0, 10.0, 10.0))


def test_world_bbox_min_max_over_all_bodies() -> None:
    bodies = _Coll(
        [
            _body_with_box((3.0, 0.0, 0.0), (4.0, 1.0, 1.0)),
            _body_with_box((3.5, 0.2, -0.5), (4.5, 0.8, 0.5)),
        ]
    )
    occ: Any = SimpleNamespace(name="probe", bRepBodies=bodies)
    _approx_pair(world_bbox(occ), (30.0, 0.0, -5.0), (45.0, 10.0, 10.0))


def test_world_bbox_no_bodies_raises() -> None:
    occ: Any = SimpleNamespace(name="empty", bRepBodies=_Coll([]))
    with pytest.raises(ValueError, match="no bodies"):
        world_bbox(occ)


# ---------------------------------------------------------------------------
# interference(): pairs, mm3 conversion, filters, names
# ---------------------------------------------------------------------------
def _occ(name: str) -> Any:
    return SimpleNamespace(kind="occ", name=name)


def _body(assembly_occ: Any, comp_name: str) -> Any:
    return SimpleNamespace(
        kind="body",
        assemblyContext=assembly_occ,
        parentComponent=SimpleNamespace(name=comp_name),
    )


def _result(e1: Any, e2: Any, vol_cm3: float) -> Any:
    return SimpleNamespace(entityOne=e1, entityTwo=e2, interferenceBody=SimpleNamespace(volume=vol_cm3))


def _design(results: list[Any]) -> tuple[Any, Any]:
    """Fake Design: returns (design, interference input). analyzeInterference yields `results`."""
    inp = MagicMock(name="interference_input")
    res = SimpleNamespace(count=len(results), item=lambda i: results[i])
    design = MagicMock(name="design")
    design.createInterferenceInput.return_value = inp
    design.analyzeInterference.return_value = res
    return design, inp


def test_interference_reports_pair_in_mm3(cast_by_kind: None, object_collection: MagicMock) -> None:
    a, b = _occ("A"), _occ("B")
    design, inp = _design([_result(a, b, 0.2)])  # 0.2 cm3 = 200 mm3
    assert interference(design, [a, b]) == [("A", "B", 200.0)]
    assert inp.areCoincidentFacesIncluded is False
    design.createInterferenceInput.assert_called_once_with(object_collection)
    assert object_collection.add.call_args_list == [call(a), call(b)]


def test_interference_skips_zero_and_sliver_volumes(cast_by_kind: None, object_collection: MagicMock) -> None:
    a, b, c = _occ("A"), _occ("B"), _occ("C")
    design, _ = _design([_result(a, b, 0.0), _result(a, c, 1e-7)])  # 1e-4 mm3 sliver
    assert interference(design, [a, b, c]) == []


def test_interference_none_when_analysis_empty(cast_by_kind: None, object_collection: MagicMock) -> None:
    design, _ = _design([])
    assert interference(design, [_occ("A"), _occ("C")]) == []


def test_interference_native_body_uses_component_name(cast_by_kind: None, object_collection: MagicMock) -> None:
    a = _occ("A")
    native = _body(None, "NativeComp")  # root-level body: no assembly occurrence
    proxy = _body(_occ("B"), "Inner")  # occurrence proxy: occurrence name wins
    design, _ = _design([_result(native, proxy, 0.5)])
    assert interference(design, [a, proxy]) == [("NativeComp", "B", 500.0)]


def test_interference_unknown_entity_raises(cast_by_kind: None, object_collection: MagicMock) -> None:
    junk = SimpleNamespace(kind="sketch")
    design, _ = _design([_result(junk, _occ("B"), 0.1)])
    with pytest.raises(ValueError, match="neither Occurrence nor BRepBody"):
        interference(design, [_occ("B")])


# ---------------------------------------------------------------------------
# overhangs(): downward faces with -n_z > limit; bed and small-hole skips
# ---------------------------------------------------------------------------
@pytest.fixture
def cast_surface() -> Iterator[None]:
    """Cylinder.cast / Plane.cast answer by the fake's .tag (None on mismatch); Point2D.create -> plain xy."""
    _CORE.Cylinder.cast.side_effect = lambda g: g if getattr(g, "tag", "") == "cyl" else None
    _CORE.Plane.cast.side_effect = lambda g: g if getattr(g, "tag", "") == "plane" else None
    _CORE.Point2D.create.side_effect = lambda x, y: SimpleNamespace(x=x, y=y)
    yield
    _CORE.Cylinder.cast.side_effect = None
    _CORE.Plane.cast.side_effect = None
    _CORE.Point2D.create.side_effect = None


def _geom(tag: str, radius_cm: float = 0.0) -> Any:
    return SimpleNamespace(tag=tag, radius=radius_cm)


def _face(
    geom: Any,
    max_z_cm: float,
    point_z_cm: float,
    n_z: float,
    *,
    n_z_at: Callable[[float, float], float] | None = None,
    on_face: Callable[[float, float], bool] = lambda _u, _v: True,
) -> Any:
    """Fake BRepFace: bbox maxZ and pointOnFace z in cm.

    getNormalAtPoint -> (0, 0, n_z). Grid API over u, v in [0, 1]: parametricRange,
    isParameterOnFace (on_face), getNormalAtParameter (n_z_at, default constant n_z).
    """
    normal_at = n_z_at or (lambda _u, _v: n_z)
    return SimpleNamespace(
        geometry=geom,
        boundingBox=SimpleNamespace(maxPoint=SimpleNamespace(z=max_z_cm)),
        pointOnFace=SimpleNamespace(x=0.0, y=0.0, z=point_z_cm),
        evaluator=SimpleNamespace(
            getNormalAtPoint=lambda _p: (True, SimpleNamespace(x=0.0, y=0.0, z=n_z)),
            parametricRange=lambda: SimpleNamespace(
                minPoint=SimpleNamespace(x=0.0, y=0.0),
                maxPoint=SimpleNamespace(x=1.0, y=1.0),
            ),
            isParameterOnFace=lambda p: on_face(p.x, p.y),
            getNormalAtParameter=lambda p: (
                True,
                SimpleNamespace(x=0.0, y=0.0, z=normal_at(p.x, p.y)),
            ),
        ),
    )


def _part(faces: list[Any], min_z_cm: float = 0.0) -> Any:
    return SimpleNamespace(
        boundingBox=SimpleNamespace(minPoint=SimpleNamespace(z=min_z_cm)),
        faces=faces,
    )


def _overhang_sig(got: list[tuple[str, float, float]]) -> list[tuple[str, float]]:
    return [(kind, n_z) for kind, n_z, _ in got]


def test_overhangs_bed_face_skipped(cast_surface: None) -> None:
    bed = _face(_geom("plane"), max_z_cm=0.0, point_z_cm=0.0, n_z=-1.0)
    assert overhangs(_part([bed])) == []


def test_overhangs_face_above_bed_tol_is_checked(cast_surface: None) -> None:
    # maxZ 0.5 mm above body minZ (> 0.01 mm bed tol) -> not bed -> downward normal flagged
    face = _face(_geom("plane"), max_z_cm=0.05, point_z_cm=0.05, n_z=-1.0)
    got = overhangs(_part([face]))
    assert _overhang_sig(got) == [("plane", -1.0)]
    assert got[0][2] == pytest.approx(0.5)


def test_overhangs_45_deg_downward_flagged(cast_surface: None) -> None:
    # n_z = -sin(45 deg) = -0.7071 -> -n_z = 0.7071 > 0.707
    n_z = -math.sin(math.radians(45.0))
    face = _face(_geom("plane"), max_z_cm=0.5, point_z_cm=0.5, n_z=n_z)
    got = overhangs(_part([face]))
    assert _overhang_sig(got) == [("plane", -0.707)]
    assert got[0][2] == pytest.approx(5.0)


def test_overhangs_44_deg_ok(cast_surface: None) -> None:
    # face 46° from horizontal, |n_z| = cos 46° = 0.6947 < 0.707 -> not flagged
    n_z = -math.sin(math.radians(44.0))
    face = _face(_geom("plane"), max_z_cm=0.5, point_z_cm=0.5, n_z=n_z)
    assert overhangs(_part([face])) == []


def test_overhangs_limit_param_overrides_default(cast_surface: None) -> None:
    face = _face(_geom("plane"), max_z_cm=0.5, point_z_cm=0.5, n_z=-math.sin(math.radians(45.0)))
    assert overhangs(_part([face]), limit=0.8) == []


def test_overhangs_small_hole_cylinder_skipped(cast_surface: None) -> None:
    hole = _face(_geom("cyl", radius_cm=0.35), max_z_cm=0.5, point_z_cm=0.5, n_z=-1.0)  # d = 7 mm
    assert overhangs(_part([hole])) == []


def test_overhangs_big_cylinder_flagged(cast_surface: None) -> None:
    big = _face(_geom("cyl", radius_cm=0.5), max_z_cm=0.5, point_z_cm=0.5, n_z=-1.0)  # d = 10 mm
    got = overhangs(_part([big]))
    assert _overhang_sig(got) == [("cylinder", -1.0)]
    assert got[0][2] == pytest.approx(5.0)


def test_overhangs_grid_samples_crown_missed_by_point_on_face(
    cast_surface: None,
) -> None:
    # Bore side: pointOnFace normal sideways (n_z=0); only grid corner u=1 has a downward crown.
    def crown_at(u: float, _v: float) -> float:
        return -1.0 if u == 1.0 else 0.0

    crown = _face(
        _geom("cyl", radius_cm=0.5),
        max_z_cm=0.5,
        point_z_cm=0.5,
        n_z=0.0,
        n_z_at=crown_at,
    )
    assert _overhang_sig(overhangs(_part([crown]))) == [("cylinder", -1.0)]
    # Same crown, but only at a parameter off the trimmed face -> ignored.
    off_face = _face(
        _geom("cyl", radius_cm=0.5),
        max_z_cm=0.5,
        point_z_cm=0.5,
        n_z=0.0,
        n_z_at=crown_at,
        on_face=lambda u, _v: u < 1.0,
    )
    assert overhangs(_part([off_face])) == []


def test_overhangs_upward_face_ignored(cast_surface: None) -> None:
    top = _face(_geom("plane"), max_z_cm=1.0, point_z_cm=1.0, n_z=1.0)
    assert overhangs(_part([top])) == []


def test_overhangs_unknown_surface_kind_is_other(cast_surface: None) -> None:
    cone = _face(_geom("cone"), max_z_cm=0.5, point_z_cm=0.5, n_z=-1.0)
    assert _overhang_sig(overhangs(_part([cone]))) == [("other", -1.0)]


def test_overhangs_mixed_part_flags_only_45_deg(cast_surface: None) -> None:
    faces = [
        _face(_geom("plane"), max_z_cm=0.0, point_z_cm=0.0, n_z=-1.0),  # bed
        _face(_geom("plane"), 0.5, 0.5, -math.sin(math.radians(45.0))),  # FAIL
        _face(_geom("plane"), 0.5, 0.5, -math.sin(math.radians(44.0))),  # ok
        _face(_geom("cyl", radius_cm=0.35), 0.5, 0.5, -1.0),  # small hole
        _face(_geom("plane"), max_z_cm=1.0, point_z_cm=1.0, n_z=1.0),  # top
    ]
    assert _overhang_sig(overhangs(_part(faces))) == [("plane", -0.707)]


# ---------------------------------------------------------------------------
# report_data(): suppressed timeline features are not unhealthy
# ---------------------------------------------------------------------------
def _feature(name: str, *, health: Any, suppressed: bool) -> Any:
    return SimpleNamespace(
        name=name,
        isGroup=False,
        isSuppressed=suppressed,
        healthState=health,
        entity=SimpleNamespace(objectType="adsk::fusion::ExtrudeFeature"),
    )


@pytest.fixture
def report_design() -> Iterator[Callable[[list[Any]], None]]:
    """Design.cast -> fake timeline holding the given items. Restored afterwards."""

    def install(items: list[Any]) -> None:
        design = SimpleNamespace(
            timeline=SimpleNamespace(count=len(items), item=lambda i: items[i]),
            allComponents=[],
        )
        _FUS.Design.cast.side_effect = lambda _e: design

    yield install
    _FUS.Design.cast.side_effect = None


def _empty_comp() -> Any:
    return SimpleNamespace(
        name="holder",
        bRepBodies=_Coll([]),
        sketches=SimpleNamespace(count=0, item=lambda i: None),
    )


def test_report_data_suppressed_error_feature_not_unhealthy(report_design: Callable[[list[Any]], None]) -> None:
    error = _FUS.FeatureHealthStates.ErrorFeatureHealthState
    report_design(
        [
            _feature("screw_hole_a", health=error, suppressed=True),  # suppressed + unhealthy state -> not counted
            _feature("screw_hole_b", health=error, suppressed=False),  # real failure -> counted
        ],
    )
    data = report_data(_empty_comp(), dfam=False)
    assert (data.unhealthy, data.suppressed) == (1, 1)
    assert "design_timeline_unhealthy=1 " in format_report(data)
    assert " suppressed=1" in format_report(data)


def test_report_data_all_suppressed_error_features_zero_unhealthy(report_design: Callable[[list[Any]], None]) -> None:
    error = _FUS.FeatureHealthStates.ErrorFeatureHealthState
    report_design(
        [_feature(f"screw_hole_{k}", health=error, suppressed=True) for k in range(3)],
    )
    data = report_data(_empty_comp(), dfam=False)
    assert (data.unhealthy, data.suppressed) == (0, 3)


class _CountedItems(list[Any]):
    """Timeline item list that counts index reads (test double only)."""

    gets = 0

    def __getitem__(self, i: Any) -> Any:
        self.gets += 1
        return super().__getitem__(i)


def test_report_data_with_stats_reads_no_timeline(report_design: Callable[[list[Any]], None]) -> None:
    """design_stats walks the timeline once; report_data(stats=...) per comp must not walk it again."""
    error = _FUS.FeatureHealthStates.ErrorFeatureHealthState
    items = _CountedItems([_feature("broken", health=error, suppressed=False)])
    report_design(items)
    stats = design_stats(_FUS.Design.cast(None))
    assert (stats.unhealthy, stats.suppressed) == (1, 0)
    items.gets = 0
    for _ in range(3):
        assert report_data(_empty_comp(), dfam=False, stats=stats, skq=False).unhealthy == 1
    assert items.gets == 0

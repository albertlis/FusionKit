"""Offline geometry tests: dfam adapter chamfer leg + full-cylinder hole rule.

Pure helpers (chamfer_leg_mm, is_full_cylinder) + facts_from_body on fake faces/edges in cm (Fusion units).
No Fusion runtime: adsk stubs from conftest.py. features helpers monkeypatched (as in test_dfam_adapter_offline).
"""

import math
import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features
from fusion_kit.dfam import (
    _cone_width_mm,
    chamfer_leg_mm,
    check_edges,
    dfam_check,
    facts_from_body,
    is_full_cylinder,
)

_CORE: Any = sys.modules["adsk.core"]
_S = 1 / math.sqrt(2)
_NO_ATTR = SimpleNamespace(itemByName=lambda group, name: None)


def _v(x: float, y: float, z: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


def _arc_face(start: float, span: float) -> SimpleNamespace:
    """Cylinder evaluator, axis through (1,1) cm. parametricRange = untrimmed u 0..2pi (as Fusion reports);
    trimmed face = arc [start, start+span] rad, answered by isParameterOnFace (getParameterAtPoint = identity)."""

    def on_face(p: SimpleNamespace) -> bool:
        return (math.atan2(p.y - 1.0, p.x - 1.0) - start) % (2 * math.pi) <= span

    return SimpleNamespace(
        parametricRange=lambda: SimpleNamespace(minPoint=_v(0, 0, 0), maxPoint=_v(2 * math.pi, 0, 0)),
        getParameterAtPoint=lambda p: (True, p),
        isParameterOnFace=on_face,
    )


@pytest.fixture
def casts() -> Iterator[None]:
    for cls, tag in (("Plane", "plane"), ("Cylinder", "cyl"), ("Cone", "cone"), ("Line3D", "line"), ("Torus", "torus")):
        getattr(_CORE, cls).cast.side_effect = lambda g, _t=tag: g if getattr(g, "tag", "") == _t else None
    yield
    for cls in ("Plane", "Cylinder", "Cone", "Line3D", "Torus"):
        getattr(_CORE, cls).cast.side_effect = None


@pytest.fixture
def geom_features(monkeypatch: pytest.MonkeyPatch, point3d_xyz: None) -> None:
    monkeypatch.setattr(features, "face_normal", lambda face, point=None: face.normal)
    monkeypatch.setattr(
        features,
        "planar_faces",
        lambda body, axis, at_mm, normal_sign=None, tol_mm=0.01: [f for f in body.faces if f.name.startswith("bed")],
    )
    monkeypatch.setattr(features, "convex_edges", lambda body, edges, probe_mm=0.1: list(edges))


def _face(
    name: str,
    normal: tuple[float, float, float],
    lo: tuple[float, float, float],
    hi: tuple[float, float, float],
    *,
    tag: str = "plane",
    area: float = 1.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        tempId=f"f:{name}",
        geometry=SimpleNamespace(tag=tag),
        normal=_v(*normal),
        boundingBox=SimpleNamespace(minPoint=_v(*lo), maxPoint=_v(*hi)),
        pointOnFace=_v(*lo),
        area=area,
        edges=[],
        loops=[],
        attributes=_NO_ATTR,
    )


def _edge(
    token: str,
    fa: SimpleNamespace,
    fb: SimpleNamespace,
    p: tuple[float, float, float],
    q: tuple[float, float, float],
) -> SimpleNamespace:
    e = SimpleNamespace(
        tempId=token,
        geometry=SimpleNamespace(tag="line"),
        faces=[fa, fb],
        startVertex=SimpleNamespace(geometry=_v(*p)),
        endVertex=SimpleNamespace(geometry=_v(*q)),
        pointOnEdge=_v(*(sum(c) / 2 for c in zip(p, q))),
        length=math.dist(p, q),
    )
    fa.edges.append(e)
    fb.edges.append(e)
    return e


def _body(faces: list[SimpleNamespace], edges: list[SimpleNamespace]) -> MagicMock:
    body = MagicMock(
        boundingBox=SimpleNamespace(minPoint=_v(0, 0, 0), maxPoint=_v(2, 2, 1)),
        faces=faces,
        edges=edges,
        shells=SimpleNamespace(count=1),
        attributes=_NO_ATTR,
    )
    body.name = "t"  # MagicMock(name=...) names the mock repr, not the .name attribute
    return body


def _chamfer_body(leg_cm: float) -> MagicMock:
    """2 x 2 x 1 cm block, bed / x0-wall edge chamfered 45 deg. Leg on bed = leg_cm (x-z cross-section)."""
    width = leg_cm * math.sqrt(2)
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    wall = _face("wall", (-1, 0, 0), (0, 0, leg_cm), (0, 2, 1))
    chamfer = _face("chamfer", (-_S, 0, -_S), (0, 0, 0), (leg_cm, 2, leg_cm), area=width * 2)
    e_bc = _edge("bc", bed, chamfer, (leg_cm, 0, 0), (leg_cm, 2, 0))
    e_cw = _edge("cw", chamfer, wall, (0, 0, leg_cm), (0, 2, leg_cm))
    bed.loops = [SimpleNamespace(isOuter=True, edges=[e_bc])]
    return _body([bed, wall, chamfer], [e_bc, e_cw])


def _hole_body(halves: int) -> MagicMock:
    """2 x 2 x 1 cm block, vertical hole r=0.2 cm at (1,1). halves=1: one 360 deg face; 2: two 180 deg faces."""
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    top = _face("top", (0, 0, 1), (0, 0, 1), (2, 2, 1))
    points = [0.8] if halves == 1 else [1.2, 0.8]
    arcs = (
        [(0.0, 2 * math.pi)] if halves == 1 else [(-math.pi / 2, math.pi), (math.pi / 2, math.pi)]
    )  # i=0 right, i=1 left
    cyls = []
    for i, px in enumerate(points):
        cyl = _face(f"cyl{i}", ((1.0 - px) / 0.2, 0, 0), (0.8, 0.8, 0), (1.2, 1.2, 1), tag="cyl")
        cyl.pointOnFace = _v(px, 1, 0.5)
        cyl.geometry = SimpleNamespace(tag="cyl", axis=_v(0, 0, 1), origin=_v(1, 1, 0), radius=0.2)
        cyl.evaluator = _arc_face(*arcs[i])
        _edge(f"ct{i}", cyl, top, (0.8, 1, 1), (1.2, 1, 1))
        _edge(f"cb{i}", cyl, bed, (0.8, 1, 0), (1.2, 1, 0))
        cyls.append(cyl)
    if halves == 2:
        _edge("seam", cyls[0], cyls[1], (1.2, 1, 0.5), (0.8, 1, 0.5))
    return _body([bed, top, *cyls], [])


def _loop(lo: tuple[float, float, float], hi: tuple[float, float, float], *, outer: bool = False) -> SimpleNamespace:
    """Bed-face loop whose single edge spans lo..hi (cm). Inner loop = hole / groove outline."""
    edge = SimpleNamespace(tempId=f"loop:{lo}:{hi}", boundingBox=SimpleNamespace(minPoint=_v(*lo), maxPoint=_v(*hi)))
    return SimpleNamespace(isOuter=outer, edges=[edge])


def _vhole(walls: list[SimpleNamespace], arc: tuple[float, float] = (0.0, 2 * math.pi)) -> MagicMock:
    """2 x 2 x 1 cm block, vertical hole r=0.2 cm at (1,1): one cylinder (arc = start, span rad; default 360 deg)
    meeting bed, top and walls."""
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    top = _face("top", (0, 0, 1), (0, 0, 1), (2, 2, 1))
    cyl = _face("cyl0", (1.0, 0, 0), (0.8, 0.8, 0), (1.2, 1.2, 1), tag="cyl")
    cyl.pointOnFace = _v(0.8, 1, 0.5)
    cyl.geometry = SimpleNamespace(tag="cyl", axis=_v(0, 0, 1), origin=_v(1, 1, 0), radius=0.2)
    cyl.evaluator = _arc_face(*arc)
    _edge("cb", cyl, bed, (0.8, 1, 0), (1.2, 1, 0))
    _edge("ct", cyl, top, (0.8, 1, 1), (1.2, 1, 1))
    for w in walls:
        _edge(f"c_{w.name}", cyl, w, (0.8, 1, 0.5), (1.2, 1, 0.5))
    return _body([bed, top, cyl, *walls], [])


def test_chamfer_leg_45deg_width_gives_leg_1() -> None:
    assert chamfer_leg_mm(math.sqrt(2), (-_S, 0, -_S), (0, 0, -1)) == pytest.approx(1.0)
    assert chamfer_leg_mm(1.414, (-_S, 0, -_S), (0, 0, -1)) == pytest.approx(1.0, abs=1e-3)


def test_chamfer_leg_asymmetric_each_face() -> None:
    # legs: 0.5 on wall, 1.0 on bed -> width = hypot(0.5, 1.0), normal ~ (leg_wall, leg_bed) / width
    w = math.hypot(0.5, 1.0)
    n = (-0.5 / w, 0.0, -1.0 / w)
    assert chamfer_leg_mm(w, n, (-1, 0, 0)) == pytest.approx(0.5)
    assert chamfer_leg_mm(w, n, (0, 0, -1)) == pytest.approx(1.0)


@pytest.mark.parametrize(("span", "full"), [(90.0, False), (299.9, False), (300.0, True), (360.0, True)])
def test_is_full_cylinder(span: float, full: bool) -> None:
    assert is_full_cylinder(span) is full


def test_chamfer_leg_036_is_fail(casts: None, geom_features: None) -> None:
    leg_cm = 0.036  # 0.36 mm leg -> width 0.509 mm
    _, holes, edges = facts_from_body(_chamfer_body(leg_cm))
    assert holes == []
    assert len(edges) == 1
    assert (edges[0].kind, edges[0].treatment, edges[0].room_mm) == ("bed", "chamfer", 10.0)
    assert edges[0].size_mm == pytest.approx(0.36)
    assert [(f.rule, f.level) for f in check_edges(edges)] == [("bed_chamfer", "FAIL")]


def _asym_chamfer_body(bed_leg_cm: float, wall_leg_cm: float) -> MagicMock:
    """2 x 2 x 1 cm block, bed / x0-wall edge chamfered with unequal legs (two-distance): bed leg a, wall leg b (cm)."""
    a, b = bed_leg_cm, wall_leg_cm
    width = math.hypot(a, b)
    norm = math.hypot(1 / a, 1 / b)
    n = (-(1 / a) / norm, 0.0, -(1 / b) / norm)
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    wall = _face("wall", (-1, 0, 0), (0, 0, b), (0, 2, 1))
    chamfer = _face("chamfer", n, (0, 0, 0), (a, 2, b), area=width * 2)
    e_bc = _edge("bc", bed, chamfer, (a, 0, 0), (a, 2, 0))
    e_cw = _edge("cw", chamfer, wall, (0, 0, b), (0, 2, b))
    bed.loops = [SimpleNamespace(isOuter=True, edges=[e_bc])]
    return _body([bed, wall, chamfer], [e_bc, e_cw])


def test_bed_chamfer_size_is_bed_leg(casts: None, geom_features: None) -> None:
    # 46 deg two-distance chamfer: bed leg 0.96 mm, wall leg 1.00 mm -> size = bed leg 0.96, above limit 0.9457 -> passes
    _, _holes, edges = facts_from_body(_asym_chamfer_body(0.096, 0.100))
    assert len(edges) == 1
    assert edges[0].size_mm == pytest.approx(0.96, abs=1e-6)
    assert check_edges(edges) == []


def test_bed_chamfer_steep_wall_leg_fails(casts: None, geom_features: None) -> None:
    # steep chamfer: bed leg 0.2 mm, wall leg 1.0 mm. max(legs) passed it; bed leg must FAIL
    _, _holes, edges = facts_from_body(_asym_chamfer_body(0.020, 0.100))
    assert edges[0].size_mm == pytest.approx(0.2, abs=1e-6)
    assert [(f.rule, f.level) for f in check_edges(edges)] == [("bed_chamfer", "FAIL")]


def test_bed_chamfer_46deg_1mm_passes(casts: None, geom_features: None) -> None:
    # 46 deg chamfer, wall leg 1.0 mm -> bed leg cot46 = 0.9657 mm, limit 0.9657 - 0.02 = 0.9457 -> passes
    bed_cm = 0.100 / math.tan(math.radians(46.0))
    _, _holes, edges = facts_from_body(_asym_chamfer_body(bed_cm, 0.100))
    assert edges[0].size_mm == pytest.approx(0.9657, abs=1e-3)
    assert check_edges(edges) == []


def test_bed_chamfer_45deg_1mm_passes(casts: None, geom_features: None) -> None:
    _, _holes, edges = facts_from_body(_asym_chamfer_body(0.100, 0.100))
    assert edges[0].size_mm == pytest.approx(1.0, abs=1e-6)
    assert check_edges(edges) == []


def test_hole_on_full_cylinder_one_hole(casts: None, geom_features: None) -> None:
    holes = facts_from_body(_hole_body(1))[1]
    assert len(holes) == 1
    assert holes[0].axis_vertical is True
    assert holes[0].radius_mm == pytest.approx(2.0)
    assert holes[0].has_seam_notch is False
    assert holes[0].on_bed is True


@pytest.mark.parametrize("halves", [1, 2])
def test_split_full_cylinder_is_one_hole_seam_fail(casts: None, geom_features: None, halves: int) -> None:
    # 360 deg as one face or two 180 deg halves -> exactly one hole, not two.
    assert [f.rule for f in dfam_check(_hole_body(halves))] == ["seam_notch"]
    assert len(facts_from_body(_hole_body(halves))[1]) == 1


def test_concave_fillet_90deg_is_not_hole(casts: None, geom_features: None) -> None:
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    wall_a = _face("wa", (1, 0, 0), (0.8, 1, 0), (0.8, 2, 1))
    wall_b = _face("wb", (0, 1, 0), (1, 0.8, 0), (2, 0.8, 1))
    fillet = _face("fillet", (_S, _S, 0), (0.8, 0.8, 0), (1, 1, 1), tag="cyl")
    fillet.pointOnFace = _v(1 - 0.2 * _S, 1 - 0.2 * _S, 0.5)
    fillet.geometry = SimpleNamespace(tag="cyl", axis=_v(0, 0, 1), origin=_v(1, 1, 0), radius=0.2)
    fillet.evaluator = _arc_face(
        math.pi, math.pi / 2
    )  # trimmed 90 deg arc at 180..270 deg about (1,1); untrimmed u = 2pi
    e_a = _edge("fa", fillet, wall_a, (0.8, 1, 0), (0.8, 1, 1))
    e_b = _edge("fb", fillet, wall_b, (1, 0.8, 0), (1, 0.8, 1))
    body = _body([bed, wall_a, wall_b, fillet], [e_a, e_b])
    _, holes, edges = facts_from_body(body)
    assert holes == []
    assert edges == []  # tangent fillet seams stay skipped
    assert dfam_check(body) == []


def test_round_through_hole_in_plate_has_no_warp_grooves(casts: None, geom_features: None) -> None:
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (6, 6, 0))  # 60 x 60 mm plate
    bed.loops = [_loop((0, 0, 0), (6, 6, 0), outer=True), _loop((2.75, 2.75, 0), (3.25, 3.25, 0))]  # O5 hole
    assert facts_from_body(_body([bed], []))[0].has_warp_grooves is False


def test_plate_with_two_blind_grooves_has_warp_grooves(casts: None, geom_features: None) -> None:
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (6, 6, 0))
    bed.loops = [
        _loop((0, 0, 0), (6, 6, 0), outer=True),
        _loop((0.5, 1.0, 0), (5.5, 1.08, 0)),  # 50 x 0.8 mm
        _loop((0.5, 3.0, 0), (5.5, 3.08, 0)),
    ]
    assert facts_from_body(_body([bed], []))[0].has_warp_grooves is True


def test_plate_with_two_through_grooves_has_warp_grooves(casts: None, geom_features: None) -> None:
    # 90 x 90 mm plate, 0.8 mm grooves at y 29.6-30.4 and 59.6-60.4 mm -> three bed strips
    strips = [
        _face("bed0", (0, 0, -1), (0, 0, 0), (9, 2.96, 0)),
        _face("bed1", (0, 0, -1), (0, 3.04, 0), (9, 5.96, 0)),
        _face("bed2", (0, 0, -1), (0, 6.04, 0), (9, 9, 0)),
    ]
    assert facts_from_body(_body(strips, []))[0].has_warp_grooves is True


def test_one_through_groove_is_not_two_grooves(casts: None, geom_features: None) -> None:
    strips = [
        _face("bed0", (0, 0, -1), (0, 0, 0), (9, 2.96, 0)),
        _face("bed1", (0, 0, -1), (0, 3.04, 0), (9, 9, 0)),
    ]
    assert facts_from_body(_body(strips, []))[0].has_warp_grooves is False


def test_chamfered_top_hole_is_not_seam_notch(casts: None, geom_features: None) -> None:
    # hole through a 45 deg chamfered top: chamfer normal not perpendicular to axis -> no V notch
    chamfer = _face("chamfer", (0, -_S, _S), (0.8, 0.8, 0.5), (1.2, 1.2, 1))
    holes = facts_from_body(_vhole([chamfer]))[1]
    assert len(holes) == 1
    assert holes[0].has_seam_notch is False


@pytest.mark.parametrize(
    ("angle", "notch"),
    [(84.0, True), (96.0, True), (60.0, False), (120.0, False)],
)
def test_v_notch_needs_walls_at_90_deg(casts: None, geom_features: None, angle: float, notch: bool) -> None:
    a = math.radians(angle)
    wa = _face("wa", (1.0, 0.0, 0.0), (0.8, 0.8, 0), (1.2, 1.2, 1))
    wb = _face("wb", (math.cos(a), math.sin(a), 0.0), (0.8, 0.8, 0), (1.2, 1.2, 1))
    holes = facts_from_body(_vhole([wa, wb]))[1]
    assert len(holes) == 1
    assert holes[0].has_seam_notch is notch


def test_notched_330_deg_hole_passes(casts: None, geom_features: None) -> None:
    # 330 deg cylinder (30 deg notch gap, span >= FULL_CYLINDER_DEG) + two walls 90 deg apart = seam notch: one hole, no seam FAIL
    wa = _face("wa", (1.0, 0.0, 0.0), (0.8, 0.8, 0), (1.2, 1.2, 1))
    wb = _face("wb", (0.0, 1.0, 0.0), (0.8, 0.8, 0), (1.2, 1.2, 1))
    body = _vhole([wa, wb], arc=(math.radians(15.0), math.radians(330.0)))
    holes = facts_from_body(body)[1]
    assert len(holes) == 1
    assert holes[0].has_seam_notch is True
    assert "seam_notch" not in [f.rule for f in dfam_check(body)]


_CONE_N = (-0.5, -0.5, -_S)  # unit normal, 45 deg incline: |n_z| = 1/sqrt(2)


def _arc_edge(
    token: str,
    fa: SimpleNamespace,
    fb: SimpleNamespace,
    mid: tuple[float, float, float],
    length_cm: float,
) -> SimpleNamespace:
    """Curved (non-Line3D) edge between fa and fb, midpoint mid (cm), length_cm (Fusion cm)."""
    e = SimpleNamespace(
        tempId=token,
        geometry=SimpleNamespace(tag="arc"),
        faces=[fa, fb],
        startVertex=SimpleNamespace(geometry=_v(*mid)),
        endVertex=SimpleNamespace(geometry=_v(*mid)),
        boundingBox=SimpleNamespace(minPoint=_v(*mid), maxPoint=_v(*mid)),
        pointOnEdge=_v(*mid),
        length=length_cm,
    )
    fa.edges.append(e)
    fb.edges.append(e)
    return e


def _corner_body(adj: str) -> MagicMock:
    """2 x 2 x 1 cm block, bed corner arc r=0.2 cm between bed and adj (cm).

    adj='cone': 45 deg conical bed chamfer, bed arc r0.2 -> wall arc r0.3 over z 0..0.1 (1 mm leg).
    adj='cyl': convex vertical cylinder r0.3 cm, no chamfer (sharp bed arc).
    """
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    bed_arc = math.pi / 2 * 0.2
    mid_bed = (0.3 - 0.2 * _S, 0.3 - 0.2 * _S, 0.0)
    if adj == "cone":
        cone_area = (math.pi / 4) * (0.2 + 0.3) * (0.1 * math.sqrt(2))  # lateral area of 90 deg frustum
        cone = _face("cone", _CONE_N, (0, 0, 0), (0.3, 0.3, 0.1), tag="cone", area=cone_area)
        wall = _face("wall", (-_S, -_S, 0), (0, 0, 0.1), (0.3, 0.3, 1))
        _arc_edge("top", cone, wall, (0.3 - 0.3 * _S, 0.3 - 0.3 * _S, 0.1), math.pi / 2 * 0.3)
        e_bed = _arc_edge("bed_arc", bed, cone, mid_bed, bed_arc)
        bed.loops = [SimpleNamespace(isOuter=True, edges=[e_bed])]
        return _body([bed, cone], [e_bed])
    cyl = _face("cyl", (-_S, -_S, 0), (0, 0, 0), (0.3, 0.3, 1))
    cyl.geometry = SimpleNamespace(tag="cyl", axis=_v(0, 0, 1), origin=_v(0.3, 0.3, 0), radius=0.3)
    cyl.pointOnFace = _v(0.3 - 0.3 * _S, 0.3 - 0.3 * _S, 0.5)
    e_bed = _arc_edge("bed_arc", bed, cyl, mid_bed, bed_arc)
    bed.loops = [SimpleNamespace(isOuter=True, edges=[e_bed])]
    return _body([bed, cyl], [e_bed])


def test_bed_arc_with_inclined_cone_is_chamfer(casts: None, geom_features: None) -> None:
    _, holes, edges = facts_from_body(_corner_body("cone"))
    assert holes == []
    assert [(e.kind, e.treatment) for e in edges] == [("bed", "chamfer")]
    assert edges[0].size_mm == pytest.approx(1.0, abs=1e-6)
    assert check_edges(edges) == []


def test_bed_arc_with_vertical_cylinder_stays_sharp(casts: None, geom_features: None) -> None:
    _, _, edges = facts_from_body(_corner_body("cyl"))
    assert [(e.kind, e.treatment) for e in edges] == [("bed", "sharp")]
    assert [(f.rule, f.level) for f in check_edges(edges)] == [("bed_chamfer", "FAIL")]


def _tangent_fillet_body(kind: str) -> MagicMock:
    """2 x 2 x 1 cm block, bed edge rounded: neighbour tangent to the bed (same normal at the edge, no crease).

    kind='torus': circular bed edge r0.2 cm, Torus face minor radius 0.1 cm (1 mm fillet).
    kind='bspline': straight bed edge at x=1, non-planar face of unknown radius (BSpline / variable fillet).
    """
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    if kind == "torus":
        fil = _face("torus", (0, 0, -1), (0, 0, 0), (0.3, 0.3, 0.1), tag="torus")
        fil.geometry = SimpleNamespace(tag="torus", minorRadius=0.1, majorRadius=0.2)
        e_bed = _arc_edge("bed_arc", bed, fil, (0.3 - 0.2 * _S, 0.3 - 0.2 * _S, 0.0), math.pi / 2 * 0.2)
    else:
        fil = _face("spline", (0, 0, -1), (0, 0, 0), (1.1, 2, 0.1), tag="bspline")
        e_bed = _edge("bed_line", bed, fil, (1.0, 0, 0), (1.0, 2, 0))
    bed.loops = [SimpleNamespace(isOuter=True, edges=[e_bed])]
    return _body([bed, fil], [e_bed])


@pytest.mark.parametrize(("kind", "radius"), [("torus", 1.0), ("bspline", 0.0)])
def test_bed_edge_tangent_fillet_is_fillet_fail(casts: None, geom_features: None, kind: str, radius: float) -> None:
    # tangent non-planar neighbour on a bed edge = fillet (was dropped as smooth seam); radius 0.0 = unknown
    _, holes, edges = facts_from_body(_tangent_fillet_body(kind))
    assert holes == []
    assert [(e.kind, e.treatment) for e in edges] == [("bed", "fillet")]
    assert edges[0].size_mm == pytest.approx(radius, abs=1e-6)
    assert [(f.rule, f.level) for f in check_edges(edges)] == [("bed_chamfer", "FAIL")]


def _pin_body(*, inward_wall: bool = False) -> MagicMock:
    """2 x 2 x 1 cm: plate top z=0.5 cm, convex pin r=0.3 cm at (1,1) up to z=1 cm.

    Concave base arc (plate / wall, z=0.5) and convex tip arc (wall / top, z=1). inward_wall=True: wall normal
    points at the axis (= hole wall), no pin.
    """
    plate = _face("plate", (0, 0, 1), (0, 0, 0.5), (2, 2, 0.5))
    tip = _face("tip", (0, 0, 1), (0.7, 0.7, 1), (1.3, 1.3, 1))
    s = -1.0 if inward_wall else 1.0
    wall = _face("wall", (s * -_S, s * -_S, 0), (0.7, 0.7, 0.5), (1.3, 1.3, 1))
    wall.geometry = SimpleNamespace(tag="cyl", axis=_v(0, 0, 1), origin=_v(1, 1, 0), radius=0.3)
    wall.pointOnFace = _v(1 - 0.3 * _S, 1 - 0.3 * _S, 0.75)
    wall.evaluator = _arc_face(0.0, 2 * math.pi)
    e_base = _arc_edge("base_arc", plate, wall, (1 - 0.3 * _S, 1 - 0.3 * _S, 0.5), math.pi / 2 * 0.3)
    e_tip = _arc_edge("tip_arc", tip, wall, (1 - 0.3 * _S, 1 - 0.3 * _S, 1.0), math.pi / 2 * 0.3)
    return _body([plate, tip, wall], [e_base, e_tip])


def test_pin_base_sharp_fails_tip_rim_ignored(
    casts: None, geom_features: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # base concave, tip rim convex (outer corner): only base is a pin_base fact
    monkeypatch.setattr(
        features, "convex_edges", lambda body, edges, probe_mm=0.1: [e for e in edges if e.tempId == "tip_arc"]
    )
    _, holes, edges = facts_from_body(_pin_body())
    assert holes == []
    assert [(e.kind, e.treatment) for e in edges] == [("pin_base", "sharp")]
    assert [(f.rule, f.level) for f in check_edges(edges)] == [("pin_base_chamfer", "FAIL")]


def test_hole_wall_not_pin_base(casts: None, geom_features: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # same geometry, wall normal toward axis = hole: no pin_base fact, no pin_base_chamfer finding
    monkeypatch.setattr(
        features, "convex_edges", lambda body, edges, probe_mm=0.1: [e for e in edges if e.tempId == "tip_arc"]
    )
    _, _holes, edges = facts_from_body(_pin_body(inward_wall=True))
    assert [e for e in edges if e.kind == "pin_base"] == []
    assert [f for f in check_edges(edges) if f.rule == "pin_base_chamfer"] == []


def _cone_face(split: bool) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Cone face of the 45 deg bed chamfer in _corner_body('cone'). split=True: bed arc as two half arcs.

    Returns (cone face, one bed-level edge of it).
    """
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    cone_area = (math.pi / 4) * (0.2 + 0.3) * (0.1 * math.sqrt(2))
    cone = _face("cone", _CONE_N, (0, 0, 0), (0.3, 0.3, 0.1), tag="cone", area=cone_area)
    wall = _face("wall", (-_S, -_S, 0), (0, 0, 0.1), (0.3, 0.3, 1))
    _arc_edge("top", cone, wall, (0.3 - 0.3 * _S, 0.3 - 0.3 * _S, 0.1), math.pi / 2 * 0.3)
    if not split:
        return cone, _arc_edge("bed_arc", bed, cone, (0.3 - 0.2 * _S, 0.3 - 0.2 * _S, 0.0), math.pi / 2 * 0.2)
    half = math.pi / 4 * 0.2
    first = _arc_edge("bed_a", bed, cone, (0.1, 0.3, 0.0), half)
    _arc_edge("bed_b", bed, cone, (0.3, 0.1, 0.0), half)
    return cone, first


def test_cone_width_split_bed_circle_equals_single_arc() -> None:
    single_cone, single_bed = _cone_face(split=False)
    split_cone, split_bed = _cone_face(split=True)
    w_single = _cone_width_mm(single_cone, single_bed)
    assert w_single == pytest.approx(0.1 * math.sqrt(2) * 10, abs=1e-6)  # 1.414 mm slant width
    assert _cone_width_mm(split_cone, split_bed) == pytest.approx(w_single, abs=1e-6)


def test_cone_width_without_top_edge_is_none() -> None:
    cone = _face("cone", _CONE_N, (0, 0, 0), (0.3, 0.3, 0.1), tag="cone", area=1.0)
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    e_bed = _arc_edge("bed_arc", bed, cone, (0.2, 0.2, 0.0), 0.3)
    assert _cone_width_mm(cone, e_bed) is None


def test_cone_width_top_is_max_z_edges_only() -> None:
    # Measured live on pawl chamfer cone (mm): bed arc 1.2674 (z=0), top arc 0.8174 (z max), NURBS 1.1125 (z max),
    # slant line 1.5987 (spans bed->top), lifted line 1.1036 (mid height). Old code summed the lifted line into L_top.
    # Mid-height z of the lifted line assumed (0.05 cm); the 0.2319 NURBS not modelled (placement unknown).
    bed_cm, top_cm, nurbs_cm, slant_cm, lifted_cm = 0.12674, 0.08174, 0.11125, 0.15987, 0.11036
    old_mean_cm = (bed_cm + (top_cm + nurbs_cm + lifted_cm)) / 2
    area_cm2 = 1.05 / 10 * old_mean_cm  # old code gives 1.05 mm on these edges
    cone = _face("cone", _CONE_N, (0, 0, 0), (0.3, 0.3, 0.1), tag="cone", area=area_cm2)
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    e_bed = _arc_edge("bed_arc", bed, cone, (0.2, 0.2, 0.0), bed_cm)
    e_bed.boundingBox = SimpleNamespace(minPoint=_v(0, 0, 0), maxPoint=_v(0, 0, 0))

    def add(token: str, lo: float, hi: float, length_cm: float) -> None:
        e = SimpleNamespace(
            tempId=token,
            boundingBox=SimpleNamespace(minPoint=_v(0, 0, lo), maxPoint=_v(0, 0, hi)),
            length=length_cm,
        )
        cone.edges.append(e)

    add("top_arc", 0.1, 0.1, top_cm)
    add("nurbs_top", 0.1, 0.1, nurbs_cm)
    add("slant", 0.0, 0.1, slant_cm)
    add("lifted", 0.05, 0.05, lifted_cm)
    w = _cone_width_mm(cone, e_bed)
    assert w == pytest.approx(area_cm2 / ((bed_cm + top_cm + nurbs_cm) / 2) * 10, abs=1e-6)
    assert w == pytest.approx(1.4124, abs=1e-3)
    assert w != pytest.approx(1.05, abs=1e-3)  # old code result

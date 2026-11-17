"""Offline tests: dfam bed footprint at non-90 interior dihedrals (75 / 90 / 105 deg), 46 deg chamfer face.

No Fusion runtime. Fake faces: bed normal (0,0,-1) (print frame, outward); wall outward normal (-sin phi, 0, cos phi)
gives interior dihedral phi; chamfer normal (-sin 46, 0, -cos 46) gives face angle 46 from bed.
Fake edges carry start/end vertex geometry (cm) and length: the chamfer top edge = edge parallel to the bed edge.
Hand values: sin(151)/sin(105) = 0.484810/0.965926 = 0.501912 (105); sin(121)/sin(75) = 0.857167/0.965926 = 0.887405 (75).
"""

import math
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import dfam, features


def _v(x: float, y: float, z: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


def _face(tid: str, normal: tuple[float, float, float]) -> Any:  # fake BRepFace (duck-typed)
    return SimpleNamespace(tempId=tid, normal=_v(*normal), edges=[])


def _edge(
    a: SimpleNamespace,
    b: SimpleNamespace,
    start: tuple[float, float, float] = (0.0, 0.0, 0.0),
    end: tuple[float, float, float] = (0.0, 1.0, 0.0),
) -> Any:  # fake BRepEdge (duck-typed)
    """Straight edge between faces a and b, start..end (cm). Default: along +y, 1 cm (parallel to other defaults)."""
    e = SimpleNamespace(
        tempId=f"{a.tempId}-{b.tempId}",
        faces=[a, b],
        startVertex=SimpleNamespace(geometry=_v(*start)),
        endVertex=SimpleNamespace(geometry=_v(*end)),
        pointOnEdge=_v(*(sum(c) / 2 for c in zip(start, end))),
        length=math.dist(start, end),
    )
    a.edges.append(e)
    b.edges.append(e)
    return e


@pytest.fixture(autouse=True)
def _patch_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(features, "face_normal", lambda face, point=None: face.normal)


def _wall_normal(phi_deg: float) -> tuple[float, float, float]:
    p = math.radians(phi_deg)
    return (-math.sin(p), 0.0, math.cos(p))


def _chamfer_normal() -> tuple[float, float, float]:
    a = math.radians(46.0)
    return (-math.sin(a), 0.0, -math.cos(a))


def _corner(phi_deg: float) -> tuple[Any, Any, Any]:
    bed = _face("bed", (0.0, 0.0, -1.0))
    wall = _face("wall", _wall_normal(phi_deg))
    g = _face("chamfer", _chamfer_normal())
    _edge(bed, g)
    _edge(g, wall)
    return bed, wall, g


def test_footprint_90_equals_width_times_cos46() -> None:
    # today's square-corner value must not change
    _, _, g = _corner(90.0)
    assert dfam._bed_leg_mm(g, 1.0, _chamfer_normal()) == pytest.approx(math.cos(math.radians(46.0)), abs=1e-9)


def test_footprint_105_hand_value() -> None:
    # sin(151)/sin(105) = 0.484810/0.965926 = 0.501912 per unit width; old 90-deg estimate gave cos46 = 0.694658 (FAILS before)
    _, _, g = _corner(105.0)
    assert dfam._bed_leg_mm(g, 1.0, _chamfer_normal()) == pytest.approx(0.501912, abs=1e-5)


def test_footprint_75_hand_value() -> None:
    # sin(121)/sin(75) = 0.887405 per unit width
    _, _, g = _corner(75.0)
    assert dfam._bed_leg_mm(g, 1.0, _chamfer_normal()) == pytest.approx(0.887405, abs=1e-5)


def test_footprint_dovetail_builder_wall_gives_90_deg_footprint() -> None:
    # bed_chamfer(distance=1 mm) at 105 deg: wall leg = cot46 / ratio(105); chamfer width w = wall * sin(phi)/sin(alpha)
    # (law of sines). Footprint must then be cot46 = 0.965685 mm = the 90-deg footprint of the requested 1 mm.
    cot46 = 1.0 / math.tan(math.radians(46.0))
    wall_leg = dfam.bed_wall_ratio(90.0, 46.0) / dfam.bed_wall_ratio(105.0, 46.0)
    width = wall_leg * math.sin(math.radians(105.0)) / math.sin(math.radians(46.0))
    _, _, g = _corner(105.0)
    assert dfam._bed_leg_mm(g, width, _chamfer_normal()) == pytest.approx(cot46, abs=1e-9)


def test_bed_wall_ratio_105_hand_value() -> None:
    # sin(105)/tan(46) + cos(105) = 0.932784 - 0.258819 = 0.673965 (wall leg -> bed leg)
    assert dfam.bed_wall_ratio(105.0, 46.0) == pytest.approx(0.673965, abs=1e-5)


def test_footprint_no_wall_is_zero() -> None:
    # only the bed neighbour: no wall to read the dihedral from -> 0.0 (FAIL, not a silent 90 deg guess)
    bed = _face("bed", (0.0, 0.0, -1.0))
    g = _face("chamfer", _chamfer_normal())
    _edge(bed, g)
    assert dfam._bed_leg_mm(g, 1.0, _chamfer_normal()) == 0.0


def test_adjacent_bed_chamfer_is_not_a_wall() -> None:
    # neighbour with the same face angle (46 deg from bed) at a bed-outline corner: skipped, no wall -> 0.0
    bed = _face("bed", (0.0, 0.0, -1.0))
    g = _face("chamfer", _chamfer_normal())
    other = _face("chamfer2", (0.0, -math.sin(math.radians(46.0)), -math.cos(math.radians(46.0))))
    _edge(bed, g)
    _edge(g, other)
    assert dfam._bed_leg_mm(g, 1.0, _chamfer_normal()) == 0.0


def test_trapezoid_chamfer_width_is_area_over_mean_of_bed_and_top_edge() -> None:
    # trapezoid face: bed edge 14.08 mm, top edge 12.15 mm, area 18.235 mm^2 (fake in cm).
    # width = 18.235 / mean(14.08, 12.15) = 1.390 mm; footprint at phi 90 = 1.390 * cos46 = 0.966.
    # old width = area / bed edge = 1.295 mm -> footprint 0.900 -> false FAIL (< 0.95).
    bed = _face("bed", (0.0, 0.0, -1.0))
    wall = _face("wall", _wall_normal(90.0))
    g = _face("chamfer", _chamfer_normal())
    g.area = 0.18235  # cm^2
    e_bed = _edge(bed, g, (0.0, 0.0, 0.0), (0.0, 1.408, 0.0))
    _edge(g, wall, (1.0, 0.0, 0.3), (1.0, 1.215, 0.3))  # top edge: parallel to bed edge, other face = wall
    _edge(g, _face("end", (0.0, -1.0, 0.0)), (0.0, 0.0, 0.0), (1.0, 0.0, 0.3))  # side edge: not parallel
    width = dfam._planar_width_mm(g, e_bed)
    assert width == pytest.approx(1.390, abs=5e-4)
    assert dfam._bed_leg_mm(g, width, _chamfer_normal()) == pytest.approx(0.966, abs=0.005)


def test_planar_width_without_top_edge_is_area_over_bed_edge() -> None:
    # no top edge (only the bed edge on g): old formula, width = area / bed edge length
    bed = _face("bed", (0.0, 0.0, -1.0))
    g = _face("chamfer", _chamfer_normal())
    g.area = 0.2  # cm^2
    e_bed = _edge(bed, g, (0.0, 0.0, 0.0), (0.0, 2.0, 0.0))
    assert dfam._planar_width_mm(g, e_bed) == pytest.approx(1.0, abs=1e-9)


def test_sliver_with_chamfer_top_neighbour_is_zero() -> None:
    # 1.29 mm sliver between two 46 deg chamfers: top neighbour = adjacent chamfer -> 0.0 (honest FAIL).
    # Old code skipped that chamfer and took the vertical end face on the side edge (phi 90) -> 0.896 mm.
    bed = _face("bed", (0.0, 0.0, -1.0))
    g = _face("chamfer", _chamfer_normal())
    other = _face("chamfer2", (0.0, -math.sin(math.radians(46.0)), -math.cos(math.radians(46.0))))
    end = _face("end", _wall_normal(90.0))
    _edge(bed, g, (0.0, 0.0, 0.0), (0.0, 0.2, 0.0))  # bed edge
    _edge(g, other, (0.0, 0.0, 0.1), (0.0, 0.2, 0.1))  # top: ridge to adjacent chamfer, parallel to bed edge
    _edge(g, end, (0.0, 0.0, 0.0), (0.0, 0.0, 0.1))  # side: perpendicular, meets vertical end face
    assert dfam._bed_leg_mm(g, 1.29, _chamfer_normal()) == 0.0


def test_bed_wall_ratio_rejects_no_chamfer() -> None:
    with pytest.raises(ValueError, match="bed_chamfer"):
        dfam.bed_wall_ratio(150.0, 46.0)  # sin150/tan46 + cos150 = -0.383 <= 0: no chamfer


def test_bed_footprint_rejects_bad_dihedral() -> None:
    with pytest.raises(ValueError, match="interior dihedral"):
        dfam.bed_footprint_mm(1.0, 46.0, 180.0)


def _split_bed_face() -> tuple[Any, Any, Any]:
    """Live side_wall chamfer g: bed edge split into 210 (10.768 mm, z 0) + 261 (11.162 mm, z 0.004 mm, other face =
    adjacent chamfer) + seam (0.0062 mm, bed). Top 254 = 21.334 mm at z 1.0 mm, other face = wall (phi 90)."""
    bed = _face("bed", (0.0, 0.0, -1.0))
    wall = _face("wall", _wall_normal(90.0))
    g = _face("chamfer", _chamfer_normal())
    adj = _face("adj", (0.0, -math.sin(math.radians(46.0)), -math.cos(math.radians(46.0))))
    g.area = 0.3047  # cm^2 = 30.47 mm^2
    e210 = _edge(bed, g, (0.0, 0.0, 0.0), (0.0, 1.0768, 0.0))
    e210.tempId = "210"
    top = _edge(g, wall, (1.0, 0.0, 0.1), (1.0, 2.1334, 0.1))  # created before 261: old `<` tie-break lets 261 win
    top.tempId = "254"
    e261 = _edge(g, adj, (0.0, 1.0768, 0.0004), (0.0, 2.1930, 0.0004))
    e261.tempId = "261"
    seam = _edge(bed, g, (0.0, 2.1930, 0.0), (0.0, 2.19362, 0.0))
    seam.tempId = "seam"
    return g, e210, top


def test_split_bed_edge_face_footprint_and_top_edge() -> None:
    # old code: _top_edge picked 261 (bed-level, adjacent chamfer) -> sliver -> leg 0.0 (false FAIL).
    # new: top = 254; bed span = 10.768 + 11.162 (seam 0.0062 mm skipped);
    # width = 30.47 / mean(21.930, 21.334) = 1.4085 mm; footprint = 1.4085 * cos46 = 0.978 (PASS at 0.95).
    g, e210, top = _split_bed_face()
    assert dfam._top_edge(g, e210) is top
    width = dfam._planar_width_mm(g, e210)
    assert width == pytest.approx(30.47 / ((21.930 + 21.334) / 2), abs=5e-4)
    assert dfam._bed_leg_mm(g, width, _chamfer_normal()) == pytest.approx(0.979, abs=0.005)

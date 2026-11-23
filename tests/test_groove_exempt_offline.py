"""Offline tests: warp-groove exemption (dfam bed_chamfer / vertical_corner skip, printability groove-rect exemption).

Pure core only (no adsk). Groove geometry: 0.8 mm gap / ceiling, 0.6 mm deep, pitch 30 mm (helper defaults).
"""

from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import printability
from fusion_kit.dfam import (
    EdgeFact,
    EdgeKind,
    EdgeTreatment,
    _groove_rects,
    _warp_family,
    _warp_grooves,
    check_edges,
    in_warp_groove,
)
from fusion_kit.printability import IDENTITY, Mesh, check, rot_z, sweep


@pytest.fixture(autouse=True)
def _identity_print_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    # check_body(None, ...) in this file: no body attributes; bed '-z' by definition -> print frame = body itself
    monkeypatch.setattr(printability, "print_frame_body", lambda body: body)


Vec = tuple[float, float, float]
# Two parallel 0.8 mm gaps, 30 mm pitch -> warp family (>= 2 grooves along x)
GROOVE: list[Any] = [(0, (0.0, 29.6, 90.0, 30.4)), (0, (0.0, 59.6, 90.0, 60.4))]


def _box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> Mesh:
    """Closed axis-aligned box, outward winding (each triangle oriented away from the box centre)."""
    v: list[Vec] = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    centre = tuple(sum(p[k] for p in v) / 8 for k in range(3))
    tris: list[tuple[int, int, int]] = []
    for a, b, c, d in quads:
        for t in ((a, b, c), (a, c, d)):
            p0, p1, p2 = v[t[0]], v[t[1]], v[t[2]]
            u = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
            w = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
            n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
            mid = tuple((p0[k] + p1[k] + p2[k]) / 3 - centre[k] for k in range(3))
            tris.append(t if n[0] * mid[0] + n[1] * mid[1] + n[2] * mid[2] >= 0 else (t[0], t[2], t[1]))
    return Mesh(verts=v, tris=tris)


def _ceiling_fan(z: float = 0.3) -> Mesh:
    """20 x 20 mm down-facing ceiling at z, fan from corner (0,0). Boundary vertex (20,0.5) makes the fan
    triangle (0,0),(20,0),(20,0.5) a 0.5 mm high sliver (bbox narrow side 0.5). Fan area sum = 400 mm2."""
    verts: list[Vec] = [(0.0, 0.0, z), (20.0, 0.0, z), (20.0, 0.5, z), (20.0, 20.0, z), (0.0, 20.0, z)]
    tris = [(0, 2, 1), (0, 3, 2), (0, 4, 3)]  # CCW fan reversed -> normal -Z
    return Mesh(verts=verts, tris=tris)


def _ceiling_strip(z: float = 0.3) -> Mesh:
    """20 x 20 mm down-facing ceiling, 4 strips 5 mm wide, 8 triangles of 50 mm2 each (sum 400)."""
    verts: list[Vec] = [(5.0 * i, 0.0, z) for i in range(5)] + [(5.0 * i, 20.0, z) for i in range(5)]
    tris: list[tuple[int, int, int]] = []
    for i in range(4):
        b0, b1, t0, t1 = i, i + 1, 5 + i, 6 + i
        tris += [(b0, t1, b1), (b0, t0, t1)]  # CCW pair reversed -> normal -Z
    return Mesh(verts=verts, tris=tris)


def _with_bed(m: Mesh) -> Mesh:
    """Adds a bed plate (x 40-50, z 0-0.3) far from m. Keeps min z = 0 so rotate_mesh does not shift m's ceiling
    (rotate_mesh normalises min z to 0; a lone ceiling at z 0.3 would become the bed and be dropped by is_bed)."""
    bed = _box(40.0, 0.0, 0.0, 50.0, 10.0, 0.3)
    off = len(m.verts)
    return Mesh(verts=m.verts + bed.verts, tris=m.tris + [(a + off, b + off, c + off) for a, b, c in bed.tris])


def _v(x: float, y: float, z: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


def _strip(lo: tuple[float, float], hi: tuple[float, float]) -> Any:
    """Bed face (cm, Fusion units) spanning lo..hi at z=0; no inner loops."""
    return SimpleNamespace(
        boundingBox=SimpleNamespace(minPoint=_v(lo[0], lo[1], 0.0), maxPoint=_v(hi[0], hi[1], 0.0)),
        loops=[],
    )


def _slot_face(x0: float, y0: float, x1: float, y1: float) -> Any:
    """Bed face (mm given, stored in cm) with one blind inner loop = slot x0..x1, y0..y1 mm. Outer loop has no edges."""
    edge = SimpleNamespace(
        boundingBox=SimpleNamespace(minPoint=_v(x0 / 10, y0 / 10, 0.0), maxPoint=_v(x1 / 10, y1 / 10, 0.0))
    )
    outer = SimpleNamespace(isOuter=True, edges=[])
    inner = SimpleNamespace(isOuter=False, edges=[edge])
    return SimpleNamespace(
        boundingBox=SimpleNamespace(minPoint=_v(0.0, 0.0, 0.0), maxPoint=_v(10.0, 10.0, 0.0)),
        loops=[outer, inner],
    )


def _edge(kind: EdgeKind, treatment: EdgeTreatment, in_groove: bool = False, size: float = 0.0) -> EdgeFact:
    return EdgeFact(label="e", kind=kind, treatment=treatment, size_mm=size, room_mm=10.0, in_warp_groove=in_groove)


def _flag(bed: list[Any], point: Vec) -> bool:
    """Edge flag as facts_from_body computes it: shared predicate over the bed's groove rects, bed z = 0."""
    return in_warp_groove(point, _groove_rects(bed), 0.0)


# ---------------------------------------------------------------------------
# dfam: groove rim / crossing corners not FAIL
# ---------------------------------------------------------------------------
def test_groove_rim_edge_sharp_bed_no_bed_chamfer_fail() -> None:
    # sharp bed rim edge on a groove wall: 0.8 mm groove cannot take a 1 mm chamfer
    assert check_edges([_edge("bed", "sharp", in_groove=True)]) == []


def test_groove_rim_edge_fillet_bed_no_bed_chamfer_fail() -> None:
    assert check_edges([_edge("bed", "fillet", in_groove=True, size=3.0)]) == []


def test_groove_crossing_corner_sharp_vertical_no_fail() -> None:
    assert check_edges([_edge("vertical", "sharp", in_groove=True)]) == []


def test_outer_sharp_vertical_corner_still_fails() -> None:
    out = check_edges([_edge("vertical", "sharp", in_groove=False)])
    assert [(f.rule, f.level) for f in out] == [("vertical_corner", "FAIL")]


def test_outer_sharp_bed_edge_still_fails() -> None:
    out = check_edges([_edge("bed", "sharp", in_groove=False)])
    assert [(f.rule, f.level) for f in out] == [("bed_chamfer", "FAIL")]


def test_mating_edge_in_groove_not_exempt() -> None:
    out = check_edges([_edge("mating", "sharp", in_groove=True)])
    assert [(f.rule, f.level) for f in out] == [("joint_chamfer", "FAIL")]


def test_lone_blind_slot_edge_not_exempt() -> None:
    # single 0.8 x 10 mm blind slot in the bed: no parallel family -> no warp pattern -> rim stays FAIL
    flag = _flag([_slot_face(20.0, 29.6, 30.0, 30.4)], (25.0, 29.6, 0.0))
    assert flag is False
    out = check_edges([_edge("bed", "sharp", in_groove=flag)])
    assert [(f.rule, f.level) for f in out] == [("bed_chamfer", "FAIL")]


def test_two_pads_0p8_apart_edge_not_exempt() -> None:
    # two 10 x 10 mm bed pads, 0.8 mm apart: one gap, no parallel family -> pad edge stays FAIL
    flag = _flag([_strip((0.0, 0.0), (1.0, 1.0)), _strip((1.08, 0.0), (2.08, 1.0))], (10.0, 5.0, 0.0))
    assert flag is False
    out = check_edges([_edge("bed", "sharp", in_groove=flag)])
    assert [(f.rule, f.level) for f in out] == [("bed_chamfer", "FAIL")]


# ---------------------------------------------------------------------------
# dfam: shared predicate + groove detection
# ---------------------------------------------------------------------------
def test_predicate_groove_floor_and_rim_true() -> None:
    assert in_warp_groove((45.0, 30.0, 0.0), GROOVE, 0.0) is True
    assert in_warp_groove((45.0, 29.6, 0.0), GROOVE, 0.0) is True  # rim on gap wall
    assert in_warp_groove((90.0, 30.4, 0.3), GROOVE, 0.0) is True  # groove end at outer wall, ceiling height


def test_predicate_lone_groove_false() -> None:
    # one groove is not a warp pattern: no exemption even inside its footprint
    assert in_warp_groove((45.0, 30.0, 0.0), [GROOVE[0]], 0.0) is False


def test_predicate_dashed_collinear_pair_not_family() -> None:
    # two pieces of ONE groove line, 15 mm apart (dashed slot): one centre line, no warp pattern
    dashed: list[Any] = [(0, (0.0, 0.0, 5.0, 0.8)), (0, (20.0, 0.0, 25.0, 0.8))]
    assert in_warp_groove((2.0, 0.4, 0.0), dashed, 0.0) is False


def test_predicate_two_parallel_lines_exempt() -> None:
    # two parallel groove lines 30 mm apart: qualifying family
    two: list[Any] = [(0, (0.0, 10.0, 90.0, 10.8)), (0, (0.0, 40.0, 90.0, 40.8))]
    assert in_warp_groove((45.0, 10.4, 0.0), two, 0.0) is True


def test_predicate_above_band_false() -> None:
    assert in_warp_groove((45.0, 30.0, 2.0), GROOVE, 0.0) is False


def test_predicate_outside_gap_false() -> None:
    assert in_warp_groove((45.0, 45.4, 0.0), GROOVE, 0.0) is False


def test_predicate_wide_gap_not_groove() -> None:
    wide: list[Any] = [(0, (0.0, 20.0, 90.0, 25.0))]  # 5 mm gap: not a warp groove
    assert in_warp_groove((45.0, 22.0, 0.0), wide, 0.0) is False


def test_predicate_outer_corner_false() -> None:
    assert in_warp_groove((90.0, 0.0, 0.0), GROOVE, 0.0) is False


def test_groove_rects_two_through_strips() -> None:
    # 90 x 90 mm plate in cm: strips y 0-29.6 and 30.4-90 mm -> one 0.8 mm gap along x
    strips = [_strip((0.0, 0.0), (9.0, 2.96)), _strip((0.0, 3.04), (9.0, 9.0))]
    rects = _groove_rects(strips)
    assert len(rects) == 1
    ax, rect = rects[0]
    assert ax == 0
    assert rect == pytest.approx(
        (-0.579, 29.6, 90.579, 30.4), abs=0.005
    )  # no span: chamfer run 0.579 mm each end (depth 0.6)
    assert _warp_grooves(rects) is False  # one groove is not two
    assert _warp_grooves(rects + [(0, (0.0, 59.6, 90.0, 60.4))]) is True


def _strip_mm(lo: tuple[float, float], hi: tuple[float, float]) -> Any:
    """Bed face given in mm (x, y); _strip takes cm."""
    return _strip((lo[0] / 10, lo[1] / 10), (hi[0] / 10, hi[1] / 10))


def test_groove_rects_unequal_span_dovetail_pairs_recognised() -> None:
    # 3 grooves along y at x = 0, +-30 mm. Outer strips reach 0.583 mm further in -y (dovetail tongue) than the
    # middle strips. Pairs at x = +-30 have unequal y spans, shared y interval -81.034..-72.966 (8.068 mm).
    lo_y_out, lo_y_mid, hi_y = -81.617, -81.034, -72.966
    strips = [
        _strip_mm((-45.0, lo_y_out), (-30.4, hi_y)),
        _strip_mm((-29.6, lo_y_mid), (-0.4, hi_y)),
        _strip_mm((0.4, lo_y_mid), (29.6, hi_y)),
        _strip_mm((30.4, lo_y_out), (45.0, hi_y)),
    ]
    rects = _groove_rects(strips)
    assert len(rects) == 3
    assert all(ax == 1 for ax, _r in rects)
    got = sorted(r for _ax, r in rects)
    # y ends run 0.579 mm past the bed-face overlap each side (chamfer run at depth 0.6, dfam._groove_rects)
    assert got[0] == pytest.approx((-30.4, lo_y_mid - 0.579, -29.6, hi_y + 0.579), abs=0.005)
    assert got[1] == pytest.approx((-0.4, lo_y_mid - 0.579, 0.4, hi_y + 0.579), abs=0.005)
    assert got[2] == pytest.approx((29.6, lo_y_mid - 0.579, 30.4, hi_y + 0.579), abs=0.005)
    assert len(_warp_family(rects)) == 3


def test_groove_rects_arm_tip_full_cover_groove_recognised() -> None:
    # universal_holder live bed faces (mm, x y). Grooves x 4.6..5.4 and 24.6..25.4, both along y.
    # Pair B|C: shared y interval 12.97..13.97 = 1.0 mm = whole span of C (narrow arm tip) -> full cover, min overlap 0.5.
    # Before fix: 1.0 < 2.0 -> B|C dropped -> one line only -> x=5 groove not exempt either (false bed-edge FAIL).
    strips = [
        _strip_mm((0.97, 0.97), (4.60, 13.97)),  # A
        _strip_mm((5.40, 0.97), (24.60, 13.97)),  # B
        _strip_mm((25.40, 12.97), (29.03, 13.97)),  # C
    ]
    rects = _groove_rects(strips)
    assert len(rects) == 2
    assert all(ax == 1 for ax, _r in rects)
    got = sorted(r for _ax, r in rects)
    # y ends run 0.579 mm past the shared interval each side (chamfer run at depth 0.6, dfam._groove_rects)
    assert got[0] == pytest.approx((4.6, 0.97 - 0.579, 5.4, 13.97 + 0.579), abs=0.005)
    assert got[1] == pytest.approx((24.6, 12.97 - 0.579, 25.4, 13.97 + 0.579), abs=0.005)
    assert len(_warp_family(rects)) == 2  # two parallel lines (x=5, x=25) -> family qualifies
    assert _warp_grooves(rects) is True


def test_groove_rects_overlap_below_threshold_not_groove() -> None:
    # 0.8 mm gap along x, but shared y interval 29..30 mm = 1 mm < max(2 mm, 0.5 x shorter span 30 mm) -> no groove
    strips = [
        _strip_mm((-30.0, 0.0), (-0.4, 30.0)),
        _strip_mm((0.4, 29.0), (30.0, 60.0)),
    ]
    rects = _groove_rects(strips)
    assert rects == []
    assert _warp_family(rects) == []


# ---------------------------------------------------------------------------
# printability: exemption only for triangles fully inside a qualifying groove rect
# ---------------------------------------------------------------------------
def test_narrow_low_ceiling_not_overhang_in_rect() -> None:
    # 0.8 mm wide, 30 mm long ceiling at z=0.3 inside a qualifying groove rect
    rep = check(_box(0.0, 0.0, 0.3, 30.0, 0.8, 3.0), exempt_rects=[(0, (0.0, 0.0, 30.0, 0.8))])
    assert rep.overhang_area_mm2 == 0.0
    assert rep.overhangs == []


def test_narrow_low_ceiling_counted_without_rects() -> None:
    # no rects (default None) -> no exemption; bbox narrow side alone never exempts
    rep = check(_box(0.0, 0.0, 0.3, 30.0, 0.8, 3.0))
    assert rep.overhang_area_mm2 == pytest.approx(30.0 * 0.8)


def test_narrow_low_ceiling_counted_when_rect_elsewhere() -> None:
    rep = check(_box(0.0, 0.0, 0.3, 30.0, 0.8, 3.0), exempt_rects=[(0, (0.0, 50.0, 30.0, 50.8))])
    assert rep.overhang_area_mm2 == pytest.approx(30.0 * 0.8)


def test_wide_low_ceiling_still_counted() -> None:
    rep = check(_box(0.0, 0.0, 0.3, 30.0, 5.0, 3.0))
    assert rep.overhang_area_mm2 == pytest.approx(30.0 * 5.0)


def test_narrow_ceiling_above_band_still_counted() -> None:
    rep = check(_box(0.0, 0.0, 2.0, 30.0, 0.8, 4.0))
    assert rep.overhang_area_mm2 == pytest.approx(30.0 * 0.8)


def test_narrow_ceiling_above_band_in_rect_still_counted() -> None:
    rep = check(_box(0.0, 0.0, 2.0, 30.0, 0.8, 4.0), exempt_rects=[(0, (0.0, 0.0, 30.0, 0.8))])
    assert rep.overhang_area_mm2 == pytest.approx(30.0 * 0.8)


def test_ceiling_fan_tessellation_full_area_counted() -> None:
    # 20 x 20 ceiling fan with 0.5 mm sliver triangle: whole 400 mm2 is overhang, sliver not dropped
    rep = check(_ceiling_fan())
    assert rep.overhang_area_mm2 == pytest.approx(400.0)


def test_ceiling_strip_tessellation_full_area_counted() -> None:
    rep = check(_ceiling_strip())
    assert rep.overhang_area_mm2 == pytest.approx(400.0)


def test_rotated_orientation_no_exemption() -> None:
    # rot_z(90) keeps the ceiling down-facing; rects are design frame -> rotated candidate gets no exemption
    rects = [(0, (0.0, 0.0, 30.0, 0.8))]
    mesh = _with_bed(_box(0.0, 0.0, 0.3, 30.0, 0.8, 3.0))
    assert check(mesh).overhang_area_mm2 == pytest.approx(30.0 * 0.8)  # precondition: ceiling is overhang
    res = dict(sweep(mesh, {"id": IDENTITY, "rz": rot_z(90.0)}, exempt_rects=rects))
    assert res["id"].overhang_area_mm2 == 0.0
    assert res["rz"].overhang_area_mm2 == pytest.approx(30.0 * 0.8)


def _no_body() -> Any:
    # check_body is wired to monkeypatched helpers that ignore the body: None stands in for BRepBody.
    # Any so basedpyright accepts it for the typed body arg (same convention as test_holes_offline._body).
    return None


def test_check_body_rects_identity_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # check_body wiring: family rects from body passed for identity orientation only; geometric groove roof
    # (print frame, any orientation) exempts the 0.3 mm roof when the part has warp grooves
    mesh = _with_bed(_box(0.0, 0.0, 0.3, 30.0, 0.8, 3.0))
    monkeypatch.setattr(printability, "mesh_from_body", lambda body, quality: mesh)
    monkeypatch.setattr(printability, "warp_family_mm", lambda body: [(0, (0.0, 0.0, 30.0, 0.8))])
    monkeypatch.setattr(printability, "_brep_overhang_deg", lambda body, point: None)  # no B-rep lookup (body None)
    monkeypatch.setattr(printability, "_brep_slit_ceiling", lambda body, points: False)  # same, slit path
    assert printability.check_body(_no_body(), IDENTITY).overhang_area_mm2 == 0.0
    assert printability.check_body(_no_body(), rot_z(90.0)).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)
    monkeypatch.setattr(printability, "warp_family_mm", lambda body: [])  # no warp pattern -> no exemption
    assert printability.check_body(_no_body(), IDENTITY).overhang_area_mm2 == pytest.approx(30.0 * 0.8)


def test_groove_rect_extends_to_body_footprint() -> None:
    # bed strips end at x=1 / 89 mm (bed chamfer inset); groove ceiling runs out to the plate edge (x=0.773 mm live).
    strips = [_strip((0.1, 0.0), (8.9, 2.96)), _strip((0.1, 3.04), (8.9, 5.96)), _strip((0.1, 6.04), (8.9, 9.0))]
    rects = _groove_rects(strips, ((0.0, 0.0, 0.0), (90.0, 90.0, 0.0)))
    got = sorted(r for _ax, r in rects)
    assert len(got) == 2
    assert got[0] == pytest.approx((0.0, 29.6, 90.0, 30.4))
    assert got[1] == pytest.approx((0.0, 59.6, 90.0, 60.4))
    assert in_warp_groove((0.773, 30.0, 0.3), rects, 0.0) is True  # groove end segment at plate edge
    assert (
        in_warp_groove((0.3, 30.0, 0.3), _groove_rects(strips), 0.0) is False
    )  # no span: rect starts 1 - 0.579 = 0.421, pad 0.05


def test_groove_rect_far_end_not_extended() -> None:
    # strip ends 10 mm from plate edges (x 0 / 90): beyond WARP_END_INSET_MAX_MM -> rect keeps the strip ends
    strips = [_strip((1.0, 0.0), (8.0, 2.96)), _strip((1.0, 3.04), (8.0, 5.96)), _strip((1.0, 6.04), (8.0, 9.0))]
    rects = _groove_rects(strips, ((0.0, 0.0, 0.0), (90.0, 90.0, 0.0)))
    got = sorted(r for _ax, r in rects)
    assert got[0] == pytest.approx((9.421, 29.6, 80.579, 30.4), abs=0.005)  # chamfer run 0.579 mm each end (depth 0.6)
    assert got[1] == pytest.approx((9.421, 59.6, 80.579, 60.4), abs=0.005)  # chamfer run 0.579 mm each end (depth 0.6)
    assert in_warp_groove((5.0, 30.0, 0.3), rects, 0.0) is False  # collinear bed notch 5 mm past strip end: not exempt
    flag = in_warp_groove((5.0, 30.0, 0.0), rects, 0.0)
    assert [(f.rule, f.level) for f in check_edges([_edge("bed", "sharp", in_groove=flag)])] == [
        ("bed_chamfer", "FAIL")
    ]
    rep = check(_with_bed(_box(5.0, 29.6, 0.3, 10.0, 30.4, 3.0)), exempt_rects=rects)
    assert rep.overhang_area_mm2 == pytest.approx(5.0 * 0.8)  # ceiling 5 mm past strip end still counted


def test_groove_end_1p062_mm_from_edge_extended() -> None:
    # live demo: strip end 1.062 mm from plate edge (within inset), ceiling at x 0.773 -> end segment exempt
    strips = [
        _strip((0.1062, 0.0), (8.9, 2.96)),
        _strip((0.1062, 3.04), (8.9, 5.96)),
        _strip((0.1062, 6.04), (8.9, 9.0)),
    ]
    rects = _groove_rects(strips, ((0.0, 0.0, 0.0), (90.0, 90.0, 0.0)))
    assert in_warp_groove((0.773, 30.0, 0.3), rects, 0.0) is True


def test_ceiling_end_segment_at_plate_edge_exempt() -> None:
    # ceiling strip from plate edge x=0.773 to 14.6 at groove y 14.6-15.4 (mesh triangle from live check_body)
    rep = check(_box(0.773, 14.6, 0.3, 14.6, 15.4, 3.0), exempt_rects=[(0, (0.0, 14.6, 90.0, 15.4))])
    assert rep.overhang_area_mm2 == 0.0


# ---------------------------------------------------------------------------
# groove crossings: collinear pieces bridged across the perpendicular groove
# ---------------------------------------------------------------------------
def _grid_bed(x_lo: float, x_hi: float) -> list[Any]:
    """Bed faces (cm) of a 90 x 90 mm plate cut by 0.8 mm grooves at x=15/45/75 and y=15/45 mm: one face per cell.
    Cells stop 0.4 mm from each groove centre line, so every crossing square is bed-free."""

    def spans(lo: float, hi: float, cuts: tuple[float, ...]) -> list[tuple[float, float]]:
        edges = [lo]
        for c in cuts:
            edges += [c - 0.4, c + 0.4]
        edges.append(hi)
        return [(edges[k], edges[k + 1]) for k in range(0, len(edges) - 1, 2)]

    return [
        _strip((xa / 10, ya / 10), (xb / 10, yb / 10))
        for xa, xb in spans(x_lo, x_hi, (15.0, 45.0, 75.0))
        for ya, yb in spans(0.0, 90.0, (15.0, 45.0))
    ]


@pytest.mark.parametrize(
    ("cx", "cy"),
    [(15.0, 15.0), (45.0, 15.0), (75.0, 15.0), (15.0, 45.0), (45.0, 45.0), (75.0, 45.0)],
)
def test_groove_crossing_square_ceiling_exempt(cx: float, cy: float) -> None:
    # 0.8 x 0.8 ceiling on a groove crossing: exempt only when the pieces of both lines bridge over the crossing
    rects = _warp_family(_groove_rects(_grid_bed(0.0, 90.0), ((0.0, 0.0, 0.0), (90.0, 90.0, 0.0))))
    assert in_warp_groove((cx, cy, 0.3), rects, 0.0) is True
    rep = check(_with_bed(_box(cx - 0.4, cy - 0.4, 0.3, cx + 0.4, cy + 0.4, 3.0)), exempt_rects=rects)
    assert rep.overhang_area_mm2 == 0.0


def test_groove_collinear_notch_10mm_past_end_still_counted() -> None:
    # groove line y=15 starts at x=11 (body-edge rule: not extended); notch ceiling x 0..1 is 10 mm past that end
    rects = _warp_family(_groove_rects(_grid_bed(11.0, 90.0), ((0.0, 0.0, 0.0), (90.0, 90.0, 0.0))))
    assert in_warp_groove((0.5, 15.0, 0.3), rects, 0.0) is False
    rep = check(_with_bed(_box(0.0, 14.6, 0.3, 1.0, 15.4, 3.0)), exempt_rects=rects)
    assert rep.overhang_area_mm2 == pytest.approx(1.0 * 0.8)

"""Offline tests for fusion_kit.printability pure core: rotate_mesh, compose, check, sweep, PrintReport.line.

No Fusion runtime: conftest.py stubs adsk; mesh_from_body / check_body are not exercised.
Meshes are hand-built in mm with print-up = +Z and outward winding.
"""

import math
import random
import time
from collections.abc import Callable

import pytest

from fusion_kit.printability import (
    IDENTITY,
    Mesh,
    check,
    clear_rays,
    compose,
    march_thickness,
    mesh_from_body,
    rot_x,
    rot_y,
    rot_z,
    rotate_mesh,
    sweep,
    wall_thickness_mm,
    weld,
)

Vec = tuple[float, float, float]
Tri = tuple[int, int, int]


# ---------------------------------------------------------------------------
# Mesh builders
# ---------------------------------------------------------------------------
def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _convex(points: list[Vec], faces: list[Tri]) -> Mesh:
    """Convex solid: every face oriented away from the points' centroid."""
    count = len(points)
    center: Vec = (
        sum(p[0] for p in points) / count,
        sum(p[1] for p in points) / count,
        sum(p[2] for p in points) / count,
    )
    tris: list[Tri] = []
    for a, b, c in faces:
        n = _cross(_sub(points[b], points[a]), _sub(points[c], points[a]))
        mid: Vec = (
            (points[a][0] + points[b][0] + points[c][0]) / 3.0,
            (points[a][1] + points[b][1] + points[c][1]) / 3.0,
            (points[a][2] + points[b][2] + points[c][2]) / 3.0,
        )
        if _dot(n, _sub(mid, center)) < 0.0:
            b, c = c, b
        tris.append((a, b, c))
    return Mesh(verts=list(points), tris=tris)


def _box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> Mesh:
    pts: list[Vec] = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    quads = [
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (3, 7, 6, 2),
        (0, 4, 7, 3),
        (1, 2, 6, 5),
    ]
    faces = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    return _convex(pts, faces)


def _cube(side: float) -> Mesh:
    return _box(0.0, 0.0, 0.0, side, side, side)


def _post() -> Mesh:
    """Bed anchor far from test geometry: keeps a floating feature's min z above 0."""
    return _box(-10.0, -1.0, 0.0, -8.0, 1.0, 5.0)


def _union(*meshes: Mesh) -> Mesh:
    verts: list[Vec] = []
    tris: list[Tri] = []
    for m in meshes:
        off = len(verts)
        verts.extend(m.verts)
        tris.extend((a + off, b + off, c + off) for a, b, c in m.tris)
    return Mesh(verts=verts, tris=tris)


def _tilted(theta_deg: float, facing_up: bool = False) -> Mesh:
    """One 10 mm triangle, edge A-B on bed, normal (0, -sin, -cos): tilted theta from horizontal."""
    t = math.radians(theta_deg)
    verts: list[Vec] = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (0.0, -10.0 * math.cos(t), 10.0 * math.sin(t)),
    ]
    tri: Tri = (0, 2, 1) if facing_up else (0, 1, 2)
    return Mesh(verts=verts, tris=[tri])


def _fan(slope_deg: float, span: float = 20.0) -> Mesh:
    """Inverted-V valley: shared edge P-Q at z=5 rising slope_deg over span; both faces apex at z=50."""
    rise = span * math.tan(math.radians(slope_deg))
    verts: list[Vec] = [
        (0.0, 0.0, 5.0),
        (span, 0.0, 5.0 + rise),
        (span / 2.0, 10.0, 50.0),
        (span / 2.0, -10.0, 50.0),
    ]
    return Mesh(verts=verts, tris=[(0, 2, 1), (1, 3, 0)])


def _corner_rot() -> tuple[Vec, Vec, Vec]:
    """Rotation putting cube corner (10,10,10) lowest: body diagonal -> -Z."""
    return compose(rot_x(180.0 + math.degrees(math.atan(math.sqrt(2.0)))), rot_z(45.0))


# ---------------------------------------------------------------------------
# Overhang faces
# ---------------------------------------------------------------------------
def test_face_tilted_below_min_angle_is_overhang() -> None:
    rep = check(_tilted(49.0), min_angle_deg=50.0)
    assert rep.overhang_area_mm2 > 0.0
    assert rep.worst_overhang_deg == pytest.approx(49.0)


def test_face_tilted_above_min_angle_is_not_overhang() -> None:
    rep = check(_tilted(51.0), min_angle_deg=50.0)
    assert rep.overhang_area_mm2 == 0.0


def test_face_exactly_at_min_angle_is_not_overhang() -> None:
    # theta 0 is exact in float (acos(1.0)); 50 deg is not bit-exact after normalisation.
    flat = Mesh(verts=[(0.0, 0.0, 5.0), (10.0, 0.0, 5.0), (0.0, 10.0, 5.0)], tris=[(0, 2, 1)])
    rep = check(_union(flat, _post()), min_angle_deg=0.0)
    assert rep.overhang_area_mm2 == 0.0


def test_bed_faces_are_not_counted_as_overhang() -> None:
    rep = check(_cube(10.0))
    assert rep.overhang_area_mm2 == 0.0
    assert rep.overhangs == []


# ---------------------------------------------------------------------------
# Hanging edges
# ---------------------------------------------------------------------------
def test_hanging_edge_sloped_above_min_angle_not_flagged() -> None:
    rep = check(_union(_fan(50.0), _post()), min_angle_deg=45.0)
    assert rep.hanging_max_mm == 0.0


def test_hanging_edge_sloped_below_min_angle_flagged() -> None:
    rep = check(_union(_fan(40.0), _post()), min_angle_deg=45.0)
    assert rep.hanging_max_mm == pytest.approx(20.0 / math.cos(math.radians(40.0)))


def _sloped_reflex_quad() -> Mesh:
    """Planar face z = 19 - x (45 deg) split by diagonal a-b; quad is non-convex (reflex at a).
    Both third vertices sit above max(z_a, z_b), so the diagonal passes the height test;
    both triangles share one normal (coplanar), so the fold rule must reject it."""
    pts: list[Vec] = [
        (0.0, 0.0, 19.0),
        (10.0, 0.0, 9.0),
        (-2.0, 5.0, 21.0),
        (-2.0, -5.0, 21.0),
    ]
    return Mesh(verts=pts, tris=[_down(pts, 0, 2, 1), _down(pts, 0, 1, 3)])


def _down(points: list[Vec], a: int, b: int, c: int) -> Tri:
    n = _cross(_sub(points[b], points[a]), _sub(points[c], points[a]))
    return (a, b, c) if n[2] < 0.0 else (a, c, b)


def test_coplanar_diagonal_on_sloped_face_is_not_hanging() -> None:
    mesh = _union(_sloped_reflex_quad(), _post())
    assert check(mesh, min_angle_deg=50.0, fold_min_deg=0.0).hanging_max_mm == pytest.approx(math.sqrt(200.0))
    assert check(mesh, min_angle_deg=50.0).hanging_max_mm == 0.0


def _per_face_copies(mesh: Mesh) -> Mesh:
    """Duplicate verts per quad (tri pairs (a,b,c),(a,c,d) as built by _box): mimics per-face nodes."""
    verts: list[Vec] = []
    tris: list[Tri] = []
    for k in range(0, len(mesh.tris), 2):
        a, b, c = mesh.tris[k]
        d = mesh.tris[k + 1][2]
        base = len(verts)
        verts.extend([mesh.verts[a], mesh.verts[b], mesh.verts[c], mesh.verts[d]])
        tris.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
    return Mesh(verts=verts, tris=tris)


def _unwelded_ridge_20mm() -> Mesh:
    """_fan(0) where each triangle owns its copies of the shared valley edge (no index sharing)."""
    p0, p1, p2, p3 = _fan(0.0).verts
    verts: list[Vec] = [p0, p2, p1, p1, p3, p0]
    return _union(Mesh(verts=verts, tris=[(0, 1, 2), (3, 4, 5)]), _post())


def test_unwelded_ridge_is_invisible_without_weld() -> None:
    assert check(_unwelded_ridge_20mm()).hanging_max_mm == 0.0


def test_weld_restores_hanging_ridge_20() -> None:
    assert check(weld(_unwelded_ridge_20mm())).hanging_max_mm == pytest.approx(20.0)


def test_weld_cube_24_copies_to_8_verts_closed() -> None:
    copies = _per_face_copies(_cube(10.0))
    assert len(copies.verts) == 24
    welded = weld(copies)
    assert len(welded.verts) == 8
    assert len(welded.tris) == 12
    rep = check(welded)
    assert rep.boundary_edges == 0
    assert rep.nonmanifold_edges == 0


_SLAT_L = 91.0


def _chevron_slat(tip_z: float) -> Mesh:
    """Closed prism along X (0..91): '^' cross-section, 50 deg legs, 2.5 mm vertical tips at y=+-20.
    tip_z = z of the tip bottoms. Profile is CCW in (y,z), so caps fan from the notch apex (index 1)
    with x=L cap as-is and x=0 cap reversed; side quads wind outward."""
    t = math.tan(math.radians(50.0))
    w = 20.0
    notch = tip_z + w * t
    prof: list[tuple[float, float]] = [
        (-w, tip_z),
        (0.0, notch),
        (w, tip_z),
        (w, tip_z + 2.5),
        (0.0, notch + 2.5),
        (-w, tip_z + 2.5),
    ]
    n = len(prof)
    verts: list[Vec] = [(0.0, y, z) for y, z in prof] + [(_SLAT_L, y, z) for y, z in prof]
    tris: list[Tri] = []
    for i in range(n):
        j = (i + 1) % n
        tris.append((i, j, n + j))
        tris.append((i, n + j, n + i))
    for a, b, c in ((0, 1, 5), (5, 1, 4), (1, 3, 4), (1, 2, 3)):
        tris.append((a, c, b))
        tris.append((n + a, n + b, n + c))
    return Mesh(verts=verts, tris=tris)


def test_chevron_slat_off_bed_hangs_on_both_tip_edges() -> None:
    rep = check(_union(_chevron_slat(5.0), _post()), min_angle_deg=50.0)
    assert rep.hanging_max_mm == pytest.approx(91.0)
    assert rep.hanging_total_mm == pytest.approx(182.0)


def test_chevron_slat_tips_on_bed_has_no_hanging_edge() -> None:
    rep = check(_union(_chevron_slat(0.0), _post()), min_angle_deg=50.0)
    assert rep.hanging_max_mm == 0.0


def test_box_hanging_in_air_is_overhang() -> None:
    rep = check(_union(_box(0.0, 0.0, 5.0, 10.0, 10.0, 15.0), _post()))
    assert rep.overhang_area_mm2 == pytest.approx(100.0)


def test_inverted_ridge_20mm_gives_hanging_max_20() -> None:
    rep = check(_union(_fan(0.0), _post()))
    assert rep.hanging_max_mm == pytest.approx(20.0)


def _plate_at(z_low: float) -> Mesh:
    """20 x 30 x 2 mm plate tilted 50 deg about X (rot_x convention); lowest edge (local y=0, z=0)
    at world Z = z_low. Top face up, underside down at 50 deg."""
    a = math.radians(50.0)
    c, s = math.cos(a), math.sin(a)
    box = _box(0.0, 0.0, 0.0, 20.0, 30.0, 2.0)
    verts: list[Vec] = [(x, c * y - s * z, s * y + c * z + z_low) for x, y, z in box.verts]
    return Mesh(verts=verts, tris=box.tris)


def test_plate_tilted_50_deg_lowest_edge_in_air_hangs() -> None:
    rep = check(_union(_plate_at(3.0), _post()))
    assert rep.ok is False
    # 20 mm lowest edge at Z=3 + two 2 mm cap-side edges at 40 deg (chain ends at Z=4.29)
    assert rep.hanging_max_mm == pytest.approx(24.0)


def test_plate_tilted_50_deg_lowest_edge_on_bed_not_hanging() -> None:
    assert check(_union(_plate_at(0.0), _post())).hanging_max_mm == 0.0


def _knife_plate_at(z_low: float) -> Mesh:
    """Wedge 20 x 30 mm, 2 mm thick at back, sharp blade at local (y=0, z=0), tilted 50 deg about X
    (rot_x convention). Blade = lowest edge at world Z = z_low; top face UP, underside DOWN at 50 deg;
    no end cap under the blade (user case: blade with an up-facing neighbour)."""
    a = math.radians(50.0)
    c, s = math.cos(a), math.sin(a)
    local: list[Vec] = [
        (0.0, 0.0, 0.0),
        (0.0, 30.0, 0.0),
        (0.0, 30.0, 2.0),
        (20.0, 0.0, 0.0),
        (20.0, 30.0, 0.0),
        (20.0, 30.0, 2.0),
    ]
    world: list[Vec] = [(x, c * y - s * z, s * y + c * z + z_low) for x, y, z in local]
    faces: list[Tri] = [
        (0, 1, 4),
        (0, 4, 3),
        (1, 2, 5),
        (1, 5, 4),
        (0, 2, 5),
        (0, 5, 3),
        (0, 1, 2),
        (3, 4, 5),
    ]
    return _convex(world, faces)


def test_knife_blade_with_up_facing_top_hangs() -> None:
    rep = check(_union(_knife_plate_at(3.0), _post()))
    assert rep.ok is False
    assert rep.hanging_max_mm == pytest.approx(20.0)
    assert rep.hanging_chains[0][1][2] == pytest.approx(3.0)
    assert rep.hanging_chains[0][2][2] == pytest.approx(3.0)


def test_knife_blade_on_bed_not_hanging() -> None:
    assert check(_union(_knife_plate_at(0.0), _post())).hanging_max_mm == 0.0


def _pocket_cup(floor_z: float = 5.0) -> Mesh:
    """20 x 20 x 10 mm block with 10 x 10 mm pocket from the top; pocket floor at floor_z.
    Outward winding, print-up = +Z. Pocket floor faces UP, walls vertical: nothing hangs."""
    verts: list[Vec] = []
    tris: list[Tri] = []

    def square(lo: float, hi: float, z: float) -> int:
        base = len(verts)
        verts.extend([(lo, lo, z), (hi, lo, z), (hi, hi, z), (lo, hi, z)])
        return base

    def add_quad(a: int, b: int, c: int, d: int, outward: Vec) -> None:
        for x, y, z in ((a, b, c), (a, c, d)):
            n = _cross(_sub(verts[y], verts[x]), _sub(verts[z], verts[x]))
            tris.append((x, y, z) if _dot(n, outward) > 0.0 else (x, z, y))

    ob = square(0.0, 20.0, 0.0)
    ot = square(0.0, 20.0, 10.0)
    it = square(5.0, 15.0, 10.0)
    fl = square(5.0, 15.0, floor_z)
    dirs: list[Vec] = [
        (0.0, -1.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (-1.0, 0.0, 0.0),
    ]
    add_quad(ob, ob + 1, ob + 2, ob + 3, (0.0, 0.0, -1.0))
    add_quad(fl, fl + 1, fl + 2, fl + 3, (0.0, 0.0, 1.0))
    for k in range(4):
        j = (k + 1) % 4
        add_quad(ob + k, ob + j, ot + j, ot + k, dirs[k])
        add_quad(ot + k, ot + j, it + j, it + k, (0.0, 0.0, 1.0))
        add_quad(fl + k, fl + j, it + j, it + k, (-dirs[k][0], -dirs[k][1], -dirs[k][2]))
    return Mesh(verts=verts, tris=tris)


def test_pocket_cup_from_top_has_no_hanging_edge() -> None:
    # pocket floor = local Z minimum, but its perimeter edges have only up/vertical faces: supported
    rep = check(_pocket_cup())
    assert rep.hanging_max_mm == 0.0
    assert rep.hanging_total_mm == 0.0
    assert rep.ok is True


# ---------------------------------------------------------------------------
# Floating points and bed contact
# ---------------------------------------------------------------------------
def test_downward_pyramid_tip_is_one_floating_point() -> None:
    apex: Vec = (5.0, 5.0, 5.0)
    pyr = _convex(
        [
            (0.0, 0.0, 10.0),
            (10.0, 0.0, 10.0),
            (10.0, 10.0, 10.0),
            (0.0, 10.0, 10.0),
            apex,
        ],
        [(0, 1, 2), (0, 2, 3), (4, 0, 1), (4, 1, 2), (4, 2, 3), (4, 3, 0)],
    )
    rep = check(_union(pyr, _post()))
    assert rep.floating_points == 1
    assert rep.floating_coords == [apex]


def test_cube_on_bed_is_printable() -> None:
    rep = check(_cube(10.0))
    assert rep.bed_contact_kind == "area"
    assert rep.ok is True


def test_cube_balanced_on_corner_is_point_contact_not_ok() -> None:
    rep = check(rotate_mesh(_cube(10.0), _corner_rot()))
    assert rep.bed_contact_kind == "point"
    assert rep.ok is False


def test_bed_area_exactly_min_bed_mm2_counts_as_area() -> None:
    rep = check(_cube(5.0))  # two bottom triangles = 25.0 mm2 == default min_bed_mm2
    assert rep.bed_contact_mm2 == 25.0
    assert rep.bed_contact_kind == "area"


def test_bed_area_below_min_is_edge_not_area() -> None:
    rep = check(_cube(4.0))  # 16 mm2 < 25, footprint span > 0.5 mm
    assert rep.bed_contact_kind == "edge"


# ---------------------------------------------------------------------------
# Winding
# ---------------------------------------------------------------------------
def test_reversed_winding_is_not_auto_fixed() -> None:
    cube = _cube(10.0)
    inward = Mesh(verts=cube.verts, tris=[(a, c, b) for a, b, c in cube.tris])
    rep = check(inward)
    assert rep.overhang_area_mm2 == pytest.approx(100.0)  # inward top face reads as 0 deg overhang
    assert rep.bed_contact_kind == "edge"


# ---------------------------------------------------------------------------
# sweep ordering
# ---------------------------------------------------------------------------
def test_sweep_puts_non_area_bed_orientation_last() -> None:
    out = sweep(_cube(10.0), {"corner": _corner_rot(), "on_bed": IDENTITY})
    assert [name for name, _ in out] == ["on_bed", "corner"]


def test_sweep_prefers_lower_hanging_when_bed_kind_ties() -> None:
    # 50 mm post, not 5: flipped, a 5 mm post floats 45 mm above bed = genuine hang under local-min rule
    tall_post = _box(-10.0, -1.0, 0.0, -8.0, 1.0, 50.0)
    out = sweep(_union(_fan(0.0), tall_post), {"identity": IDENTITY, "flipped": rot_x(180.0)})
    assert [name for name, _ in out] == ["flipped", "identity"]
    assert [rep.hanging_max_mm for _, rep in out] == pytest.approx([0.0, 20.0])


# ---------------------------------------------------------------------------
# rotate_mesh / compose / PrintReport.line
# ---------------------------------------------------------------------------
def test_rotate_mesh_lands_lowest_vertex_on_bed() -> None:
    rotated = rotate_mesh(_cube(10.0), rot_y(37.0))
    assert min(v[2] for v in rotated.verts) == 0.0


def test_compose_applies_rightmost_rotation_first() -> None:
    v: Vec = (0.0, 1.0, 0.0)
    r = compose(rot_z(90.0), rot_x(90.0))
    got = tuple(sum(r[i][j] * v[j] for j in range(3)) for i in range(3))
    assert got == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)


def test_line_reports_bed_kind_and_ok_flag() -> None:
    line = check(_cube(10.0)).line()
    assert "bed=area" in line
    assert "ok=True" in line
    assert "boundary=0 nonmanifold=0" in line


# ---------------------------------------------------------------------------
# clear_rays: line-of-sight, axis-aligned grid path + general-direction path
# ---------------------------------------------------------------------------
def _slot_plate() -> Mesh:
    """Plate x 0..30, z 0..10, thickness y 0..2; through-slot (along Y) x 10..20, z 3..7."""
    return _union(
        _box(0.0, 0.0, 0.0, 10.0, 2.0, 10.0),
        _box(20.0, 0.0, 0.0, 30.0, 2.0, 10.0),
        _box(10.0, 0.0, 0.0, 20.0, 2.0, 3.0),
        _box(10.0, 0.0, 7.0, 20.0, 2.0, 10.0),
    )


def _random_mesh(count: int, seed: int) -> Mesh:
    rng = random.Random(seed)
    verts: list[Vec] = []
    tris: list[Tri] = []
    for _ in range(count):
        bx, by, bz = (
            rng.uniform(0.0, 100.0),
            rng.uniform(0.0, 100.0),
            rng.uniform(0.0, 100.0),
        )
        off = len(verts)
        for _k in range(3):
            verts.append(
                (
                    bx + rng.uniform(-3.0, 3.0),
                    by + rng.uniform(-3.0, 3.0),
                    bz + rng.uniform(-3.0, 3.0),
                )
            )
        tris.append((off, off + 1, off + 2))
    return Mesh(verts=verts, tris=tris)


def test_clear_rays_through_cube_blocked_beside_clear() -> None:
    got = clear_rays(_cube(10.0), [(5.0, -5.0, 5.0), (15.0, -5.0, 5.0)], (0.0, 1.0, 0.0), 20.0)
    assert got == [(15.0, -5.0, 5.0)]


def test_clear_rays_segment_stops_at_length() -> None:
    cube = _cube(10.0)
    assert clear_rays(cube, [(5.0, -5.0, 5.0)], (0.0, 1.0, 0.0), 4.0) == [(5.0, -5.0, 5.0)]
    assert clear_rays(cube, [(5.0, -5.0, 5.0)], (0.0, 1.0, 0.0), 5.5) == []


def test_clear_rays_exact_edge_and_corner_hit_counts_as_blocked() -> None:
    cube = _cube(10.0)
    # (0, 0, 5) is on cube edge x=0/y=0; ray runs parallel to face x=0
    assert clear_rays(cube, [(0.0, -5.0, 5.0)], (0.0, 1.0, 0.0), 20.0) == []
    # exact corner (0, 0, 0)
    assert clear_rays(cube, [(0.0, -5.0, 0.0)], (0.0, 1.0, 0.0), 20.0) == []


def test_clear_rays_slab_only_slot_rays_clear() -> None:
    origins = [(x, -5.0, z) for x in (5.0, 15.0, 25.0) for z in (1.0, 5.0, 9.0)]
    assert clear_rays(_slot_plate(), origins, (0.0, 1.0, 0.0), 20.0) == [(15.0, -5.0, 5.0)]


def test_clear_rays_general_direction_edge_miss_and_length() -> None:
    cube = _cube(10.0)
    # line (-5+s, -5+s, 5) passes exactly through cube edge (0, 0, 5)
    assert clear_rays(cube, [(-5.0, -5.0, 5.0)], (1.0, 1.0, 0.0), 30.0) == []
    assert clear_rays(cube, [(-5.0, -5.0, 5.0)], (1.0, 1.0, 0.0), 4.0) == [(-5.0, -5.0, 5.0)]
    # diverging away from cube in y: misses
    assert clear_rays(cube, [(-5.0, -5.0, 5.0)], (1.0, -1.0, 0.0), 30.0) == [(-5.0, -5.0, 5.0)]


def test_clear_rays_axis_path_matches_general_path() -> None:
    mesh = _random_mesh(300, seed=1)
    rng = random.Random(2)
    origins: list[Vec] = [
        (rng.uniform(-10.0, 110.0), rng.uniform(-20.0, -5.0), rng.uniform(-10.0, 110.0)) for _ in range(200)
    ]
    axis = clear_rays(mesh, origins, (0.0, 1.0, 0.0), 130.0)
    tilted = clear_rays(mesh, origins, (0.0, 1.0, 1e-8), 130.0)
    assert axis == tilted
    assert 0 < len(axis) < len(origins)


def test_clear_rays_rejects_zero_direction_and_bad_length() -> None:
    with pytest.raises(ValueError):
        clear_rays(_cube(10.0), [(0.0, 0.0, 0.0)], (0.0, 0.0, 0.0), 1.0)
    with pytest.raises(ValueError):
        clear_rays(_cube(10.0), [(0.0, 0.0, 0.0)], (0.0, 1.0, 0.0), 0.0)


def test_clear_rays_3000_rays_5000_tris_under_10s() -> None:
    mesh = _random_mesh(5000, seed=3)
    origins: list[Vec] = [(i * 2.0, -5.0, j * 2.0) for i in range(50) for j in range(60)]
    t0 = time.perf_counter()
    got = clear_rays(mesh, origins, (0.0, 1.0, 0.0), 130.0)
    elapsed = time.perf_counter() - t0
    assert len(origins) == 3000
    assert set(got) <= set(origins)
    assert elapsed < 10.0, f"clear_rays took {elapsed:.2f} s"


def _slab_inside(x0: float, x1: float) -> Callable[[Vec], bool]:
    return lambda p: x0 < p[0] < x1


def _ball_inside(r: float) -> Callable[[Vec], bool]:
    return lambda p: p[0] * p[0] + p[1] * p[1] + p[2] * p[2] < r * r


def test_march_thickness_slab_hit_within_tolerance() -> None:
    t = march_thickness(_slab_inside(0.0, 7.333), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 50.0, 0.05)
    assert t == pytest.approx(7.333, abs=1e-4)


def test_march_thickness_ball_through_centre_is_diameter() -> None:
    t = march_thickness(_ball_inside(5.0), (5.0, 0.0, 0.0), (-1.0, 0.0, 0.0), 50.0, 0.05)
    assert t == pytest.approx(10.0, abs=1e-4)


def test_march_thickness_ball_oblique_chord() -> None:
    # chord from surface point (5,0,0) along (-1,1,0)/sqrt2 of r=5 ball: t = 5*sqrt2
    t = march_thickness(_ball_inside(5.0), (5.0, 0.0, 0.0), (-1.0, 1.0, 0.0), 50.0, 0.05)
    assert t == pytest.approx(5.0 * math.sqrt(2.0), abs=1e-4)


def test_march_thickness_void_larger_than_step_is_found() -> None:
    # solid 0..10 with void 2.0..2.2: first void sample 2.05 -> wall ends at 2.0
    void_solid = lambda p: 0.0 < p[0] < 10.0 and not (2.0 < p[0] < 2.2)
    t = march_thickness(void_solid, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 50.0, 0.05)
    assert t == pytest.approx(2.0, abs=1e-4)


def test_march_thickness_void_thinner_than_step_is_stepped_over() -> None:
    # documented blind spot: void 2.01..2.02 lies between samples 2.00 and 2.05 -> run reported as 10
    void_solid = lambda p: 0.0 < p[0] < 10.0 and not (2.01 < p[0] < 2.02)
    t = march_thickness(void_solid, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 50.0, 0.05)
    assert t == pytest.approx(10.0, abs=1e-4)


def test_march_thickness_start_facing_out_raises() -> None:
    with pytest.raises(ValueError, match="no material"):
        march_thickness(_slab_inside(0.0, 7.3), (0.0, 0.0, 0.0), (-1.0, 0.0, 0.0), 50.0, 0.05)


def test_march_thickness_wall_thinner_than_first_step_is_found() -> None:
    # wall 0..0.4, first sample 0.5 already outside -> bisect (0, 0.5) instead of raising
    t = march_thickness(_slab_inside(0.0, 0.4), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 10.0, 0.5)
    assert t == pytest.approx(0.4, abs=0.5 / 2**10)


def test_march_thickness_pure_air_beyond_first_step_still_raises() -> None:
    with pytest.raises(ValueError, match="no material"):
        march_thickness(lambda _p: False, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 10.0, 0.5)


def test_march_thickness_no_exit_within_max_raises() -> None:
    with pytest.raises(ValueError, match="no exit within max_mm"):
        march_thickness(_slab_inside(0.0, 100.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 10.0, 0.05)


def test_march_thickness_rejects_bad_args() -> None:
    inside = _slab_inside(0.0, 7.3)
    with pytest.raises(ValueError):
        march_thickness(inside, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 50.0, 0.0)
    with pytest.raises(ValueError):
        march_thickness(inside, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 50.0, 0.05)
    with pytest.raises(ValueError):
        march_thickness(inside, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 0.01, 0.05)


def test_mesh_from_body_none_raises() -> None:
    with pytest.raises(ValueError, match="body is None"):
        mesh_from_body(None)


def test_wall_thickness_none_body_raises() -> None:
    with pytest.raises(ValueError, match="body is None"):
        wall_thickness_mm(None, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))

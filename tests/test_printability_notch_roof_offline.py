"""Offline tests: teardrop notch top (NOTCH_BRIDGE_MAX_MM, flanked), warp-groove roof (print frame, any rotation),
short ledge (SHORT_LEDGE_MAX_MM, attached along its long side) and bed-adjacent 44 deg face (BED_ADJACENT_MAX_MM).

No Fusion runtime. Meshes hand-built in mm, print-up = +Z, outward winding.
"""

import math

import pytest

from fusion_kit.printability import (
    GROOVE_ROOF_MAX_MM,
    NOTCH_BRIDGE_MAX_MM,
    Mesh,
    check,
    rot_x,
    rotate_mesh,
)

Vec = tuple[float, float, float]
Tri = tuple[int, int, int]

L = 2.25  # hole axis length (Y), matches live Ø8 case
H = 4.4  # flat top height = r + 0.4 for r=4
DROP = 1.0  # flank run: 45 deg flank from (half, H) to (half + DROP, H - DROP)


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _oriented(verts: list[Vec], a: int, b: int, c: int, want: Vec) -> Tri:
    """Triangle (a,b,c) wound so its normal points along want (air side = outward)."""
    n = _cross(_sub(verts[b], verts[a]), _sub(verts[c], verts[a]))
    return (a, b, c) if n[0] * want[0] + n[1] * want[1] + n[2] * want[2] >= 0.0 else (a, c, b)


def _teardrop_top(half: float, flanks: tuple[bool, bool] = (True, True)) -> Mesh:
    """Flat top x in [-half, half], y in [0, L] at z=H; optional 45 deg flanks on +x / -x sides."""
    verts: list[Vec] = [(-half, 0.0, H), (half, 0.0, H), (half, L, H), (-half, L, H)]
    tris: list[Tri] = [
        _oriented(verts, 0, 1, 2, (0.0, 0.0, -1.0)),
        _oriented(verts, 0, 2, 3, (0.0, 0.0, -1.0)),
    ]
    if flanks[0]:
        verts += [(half + DROP, 0.0, H - DROP), (half + DROP, L, H - DROP)]  # 4, 5
        tris += [
            _oriented(verts, 1, 4, 5, (-1.0, 0.0, -1.0)),
            _oriented(verts, 1, 5, 2, (-1.0, 0.0, -1.0)),
        ]
    if flanks[1]:
        verts += [(-half - DROP, 0.0, H - DROP), (-half - DROP, L, H - DROP)]  # 6, 7 (or 4, 5 if no +x flank)
        base = len(verts) - 2
        tris += [
            _oriented(verts, 0, base, base + 1, (1.0, 0.0, -1.0)),
            _oriented(verts, 0, base + 1, 3, (1.0, 0.0, -1.0)),
        ]
    return Mesh(verts=verts, tris=tris)


def _roof(z: float, width: float = 1.0) -> Mesh:
    """Flat down-facing strip x in [0, width], y in [0, L] at height z."""
    verts: list[Vec] = [(0.0, 0.0, z), (width, 0.0, z), (width, L, z), (0.0, L, z)]
    tris: list[Tri] = [
        _oriented(verts, 0, 1, 2, (0.0, 0.0, -1.0)),
        _oriented(verts, 0, 2, 3, (0.0, 0.0, -1.0)),
    ]
    return Mesh(verts=verts, tris=tris)


def _walled_roof(
    z: float,
    width: float = 1.0,
    wall_bottom: float = 0.0,
    walls: tuple[bool, bool] = (True, True),
    length: float = L,
) -> Mesh:
    """Flat down-facing roof x in [0, width], y in [0, length] at height z, plus vertical side walls at x=0 (walls[0])
    and x=width (walls[1]) running from wall_bottom up to the roof. Walls share the roof's edge vertices (welded)."""
    verts: list[Vec] = [(0.0, 0.0, z), (width, 0.0, z), (width, length, z), (0.0, length, z)]
    tris: list[Tri] = [
        _oriented(verts, 0, 1, 2, (0.0, 0.0, -1.0)),
        _oriented(verts, 0, 2, 3, (0.0, 0.0, -1.0)),
    ]
    if walls[0]:
        b0 = len(verts)
        verts += [(0.0, 0.0, wall_bottom), (0.0, length, wall_bottom)]
        tris += [
            _oriented(verts, b0, b0 + 1, 3, (-1.0, 0.0, 0.0)),
            _oriented(verts, b0, 3, 0, (-1.0, 0.0, 0.0)),
        ]
    if walls[1]:
        b1 = len(verts)
        verts += [(width, 0.0, wall_bottom), (width, length, wall_bottom)]
        tris += [
            _oriented(verts, b1, b1 + 1, 2, (1.0, 0.0, 0.0)),
            _oriented(verts, b1, 2, 1, (1.0, 0.0, 0.0)),
        ]
    return Mesh(verts=verts, tris=tris)


def _strut_on_wall(length: float, width: float, z: float = 5.4) -> Mesh:
    """Down-facing strip x in [0, length], y in [0, width] at height z, attached ONLY at its short end x=0 to a vertical
    wall (x=0, z in [0, z]). Both long sides free: a cantilever strut."""
    verts: list[Vec] = [
        (0.0, 0.0, z),
        (length, 0.0, z),
        (length, width, z),
        (0.0, width, z),
        (0.0, 0.0, 0.0),
        (0.0, width, 0.0),
    ]
    tris: list[Tri] = [
        _oriented(verts, 0, 1, 2, (0.0, 0.0, -1.0)),
        _oriented(verts, 0, 2, 3, (0.0, 0.0, -1.0)),
        _oriented(verts, 4, 5, 3, (-1.0, 0.0, 0.0)),
        _oriented(verts, 4, 3, 0, (-1.0, 0.0, 0.0)),
    ]
    return Mesh(verts=verts, tris=tris)


def _with_bed_tri(mesh: Mesh) -> Mesh:
    """Append one bed triangle at z=0 far away so min Z = 0 (bed tri is never overhang)."""
    base = len(mesh.verts)
    verts = mesh.verts + [(10.0, 0.0, 0.0), (12.0, 0.0, 0.0), (12.0, 2.0, 0.0)]
    tris = mesh.tris + [_oriented(verts, base, base + 1, base + 2, (0.0, 0.0, -1.0))]
    return Mesh(verts=verts, tris=tris)


def test_constants_live_values() -> None:
    assert NOTCH_BRIDGE_MAX_MM == pytest.approx(2.6)
    assert GROOVE_ROOF_MAX_MM == pytest.approx(0.65)


def test_notch_top_with_flanks_exempt() -> None:
    mesh = _teardrop_top(half=0.5)
    assert check(mesh).overhang_area_mm2 == pytest.approx(1.0 * L, abs=1e-6)
    rep = check(mesh, notch_bridge=True)
    assert rep.overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_flat_top_without_flanks_still_overhang() -> None:
    mesh = _teardrop_top(half=0.5, flanks=(False, False))
    rep = check(mesh, notch_bridge=True)
    assert rep.overhang_area_mm2 == pytest.approx(1.0 * L, abs=1e-6)


def test_flat_top_one_flank_only_still_overhang() -> None:
    mesh = _teardrop_top(half=0.5, flanks=(True, False))
    rep = check(mesh, notch_bridge=True)
    assert rep.overhang_area_mm2 == pytest.approx(1.0 * L, abs=1e-6)


def test_notch_width_3_mm_still_overhang() -> None:
    mesh = _teardrop_top(half=1.5)
    rep = check(mesh, notch_bridge=True)
    assert rep.overhang_area_mm2 == pytest.approx(3.0 * L, abs=1e-6)


def test_notch_flag_off_keeps_flat_top_overhang() -> None:
    mesh = _teardrop_top(half=0.5)
    assert check(mesh, notch_bridge=False).overhang_area_mm2 == pytest.approx(1.0 * L, abs=1e-6)


def test_groove_roof_exempt_after_rotation_round_trip() -> None:
    # design frame = print frame rotated by rot_x(90); check_body applies up_rot = rot_x(-90) before check()
    print_mesh = _with_bed_tri(_roof(0.3))
    design = rotate_mesh(print_mesh, rot_x(90.0))
    printed = rotate_mesh(design, rot_x(-90.0))
    assert check(printed).overhang_area_mm2 == pytest.approx(1.0 * L, abs=1e-6)
    assert check(printed, groove_roof=True).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_groove_roof_at_2mm_still_overhang() -> None:
    mesh = _roof(2.0)
    rep = check(mesh, groove_roof=True)
    assert rep.overhang_area_mm2 == pytest.approx(1.0 * L, abs=1e-6)


def test_groove_roof_at_0_3mm_exempt_direct() -> None:
    assert check(_roof(0.3), groove_roof=True).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_groove_roof_wider_than_1mm_still_overhang() -> None:
    mesh = _roof(0.3, width=1.5)
    assert check(mesh, groove_roof=True).overhang_area_mm2 == pytest.approx(1.5 * L, abs=1e-6)


def _sloped_face(h: float, angle_deg: float = 44.0) -> Mesh:
    """Down-facing face at angle_deg from horizontal, from bed edge (z=0, x=0) up to z=h. Run r = h / tan(angle). Print-up = +Z."""
    r = h / math.tan(math.radians(angle_deg))
    verts: list[Vec] = [(0.0, 0.0, 0.0), (0.0, L, 0.0), (r, 0.0, h), (r, L, h)]
    want: Vec = (h, 0.0, -r)
    tris: list[Tri] = [_oriented(verts, 0, 1, 3, want), _oriented(verts, 0, 3, 2, want)]
    return Mesh(verts=verts, tris=tris)


def _sloped_area(h: float, angle_deg: float = 44.0) -> float:
    return L * math.hypot(h, h / math.tan(math.radians(angle_deg)))


def test_short_ledge_0_3_exempt() -> None:
    mesh = _with_bed_tri(_walled_roof(5.4, width=0.3, walls=(True, False)))
    assert check(mesh).overhang_area_mm2 == pytest.approx(0.3 * L, abs=1e-6)
    assert check(mesh, short_ledge=True).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_short_ledge_0_8_still_overhang() -> None:
    mesh = _with_bed_tri(_walled_roof(5.4, width=0.8, walls=(True, False)))
    assert check(mesh, short_ledge=True).overhang_area_mm2 == pytest.approx(0.8 * L, abs=1e-6)


def test_short_ledge_groove_wall_33mm_long_side_exempt() -> None:
    # 0.3 mm ledge attached along its full 33 mm long side to a vertical wall
    mesh = _with_bed_tri(_walled_roof(5.4, width=0.3, walls=(True, False), length=33.0))
    assert check(mesh).overhang_area_mm2 == pytest.approx(0.3 * 33.0, abs=1e-6)
    assert check(mesh, short_ledge=True).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_short_ledge_cantilever_strut_on_short_end_flagged() -> None:
    # 0.5 x 30 mm strut, attached only at its 0.5 mm short end: min span 0.5 but perpendicular-to-edge width 30
    mesh = _with_bed_tri(_strut_on_wall(length=30.0, width=0.5))
    assert check(mesh).overhang_area_mm2 == pytest.approx(30.0 * 0.5, abs=1e-6)
    assert check(mesh, short_ledge=True).overhang_area_mm2 == pytest.approx(30.0 * 0.5, abs=1e-6)


def test_bed_adjacent_44_deg_zmax_0_97_exempt() -> None:
    mesh = _sloped_face(0.97)
    assert check(mesh).overhang_area_mm2 == pytest.approx(_sloped_area(0.97), abs=1e-6)
    assert check(mesh, bed_adjacent=True).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_bed_adjacent_44_deg_zmax_1_5_still_overhang() -> None:
    mesh = _sloped_face(1.5)
    assert check(mesh, bed_adjacent=True).overhang_area_mm2 == pytest.approx(_sloped_area(1.5), abs=1e-6)


def test_bed_adjacent_10_deg_zmax_0_97_flagged() -> None:
    # shallow face touching bed (zmax <= 1) is not a bed chamfer: must stay overhang
    mesh = _sloped_face(0.97, angle_deg=10.0)
    assert check(mesh, bed_adjacent=True).overhang_area_mm2 == pytest.approx(_sloped_area(0.97, 10.0), abs=1e-6)


def test_bed_adjacent_45_deg_zmax_0_97_exempt() -> None:
    mesh = _sloped_face(0.97, angle_deg=45.0)
    assert check(mesh, bed_adjacent=True).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_new_exemptions_keep_notch_top_flagged() -> None:
    mesh = _teardrop_top(half=0.5, flanks=(False, False))
    assert check(mesh, short_ledge=True, bed_adjacent=True).overhang_area_mm2 == pytest.approx(1.0 * L, abs=1e-6)

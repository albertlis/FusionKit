"""Offline tests: warp-groove ceiling exemption judged per triangle (GROOVE_ROOF_MAX_WIDTH_MM, GROOVE_ROOF_MAX_MM).

A crossing grid of two groove families is one connected down-facing region whose hull is wide, so the region-level
min-width test flags it. Each strip / crossing cell is still narrow: the exemption must judge triangles locally.

No Fusion runtime. Meshes hand-built in mm, print-up = +Z, outward winding.
"""

import pytest

from fusion_kit.printability import GROOVE_ROOF_MAX_MM, GROOVE_ROOF_MAX_WIDTH_MM, Mesh, check

Vec = tuple[float, float, float]
Tri = tuple[int, int, int]

L = 2.25  # strip length (Y), same as the notch / roof tests
GRID_AREA_MM2 = 16.0 + 7.36  # sum of cell areas of _grid_roof(): strip A 16.0 + strip B 7.36


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _oriented(verts: list[Vec], a: int, b: int, c: int, want: Vec) -> Tri:
    """Triangle (a,b,c) wound so its normal points along want (air side = outward)."""
    n = _cross(_sub(verts[b], verts[a]), _sub(verts[c], verts[a]))
    return (a, b, c) if n[0] * want[0] + n[1] * want[1] + n[2] * want[2] >= 0.0 else (a, c, b)


def _lattice_roof(z: float, xs: list[float], ys: list[float], cells: list[tuple[int, int]]) -> Mesh:
    """Down-facing roof at height z made of lattice cells (i, j): cell spans xs[i]..xs[i+1] x ys[j]..ys[j+1].
    Two triangles per cell; lattice vertices are shared, so edge-adjacent cells form one connected region."""
    index: dict[tuple[int, int], int] = {}
    verts: list[Vec] = []

    def vid(i: int, j: int) -> int:
        if (i, j) not in index:
            index[(i, j)] = len(verts)
            verts.append((xs[i], ys[j], z))
        return index[(i, j)]

    tris: list[Tri] = []
    down: Vec = (0.0, 0.0, -1.0)
    for i, j in cells:
        a, b, c, d = vid(i, j), vid(i + 1, j), vid(i + 1, j + 1), vid(i, j + 1)
        tris.append(_oriented(verts, a, b, c, down))
        tris.append(_oriented(verts, a, c, d, down))
    return Mesh(verts=verts, tris=tris)


def _grid_roof(z: float) -> Mesh:
    """Two crossing 0.8 mm groove families: strip A y in [3, 3.8] over x in [0, 20]; strip B x in [4, 4.8] over
    y in [0, 10]. Union = 5 lattice cells, one connected region, hull 20 x 10 mm (min width 10 mm)."""
    return _lattice_roof(
        z,
        xs=[0.0, 4.0, 4.8, 20.0],
        ys=[0.0, 3.0, 3.8, 10.0],
        cells=[(0, 1), (1, 0), (1, 1), (1, 2), (2, 1)],
    )


def _strip(z: float, width: float) -> Mesh:
    """Flat down-facing strip x in [0, width], y in [0, L] at height z."""
    verts: list[Vec] = [(0.0, 0.0, z), (width, 0.0, z), (width, L, z), (0.0, L, z)]
    down: Vec = (0.0, 0.0, -1.0)
    tris: list[Tri] = [_oriented(verts, 0, 1, 2, down), _oriented(verts, 0, 2, 3, down)]
    return Mesh(verts=verts, tris=tris)


def _single_tri(apex_y: float, z: float = 0.3) -> Mesh:
    """One down-facing triangle (0,0), (10,0), (5, apex_y): min altitude = apex_y (altitude onto the 10 mm base)."""
    verts: list[Vec] = [(0.0, 0.0, z), (10.0, 0.0, z), (5.0, apex_y, z)]
    return Mesh(verts=verts, tris=[_oriented(verts, 0, 1, 2, (0.0, 0.0, -1.0))])


def test_constants_live_values() -> None:
    assert GROOVE_ROOF_MAX_MM == pytest.approx(0.65)
    assert GROOVE_ROOF_MAX_WIDTH_MM == pytest.approx(1.0)


def test_grid_fixture_is_one_region_too_wide_for_region_test() -> None:
    # baseline (no exemption) = sum of cell areas, so the fixture is what the tests claim it is
    assert check(_grid_roof(0.6)).overhang_area_mm2 == pytest.approx(GRID_AREA_MM2, abs=1e-6)


def test_crossing_grid_0_8_at_0_6_exempt() -> None:
    assert check(_grid_roof(0.6), groove_roof=True).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_crossing_grid_0_8_at_2_0_still_overhang() -> None:
    assert check(_grid_roof(2.0), groove_roof=True).overhang_area_mm2 == pytest.approx(GRID_AREA_MM2, abs=1e-6)


def test_wide_ceiling_2mm_at_0_6_still_overhang() -> None:
    # 2 mm strip: triangle min altitude = 2 * 2.25 / sqrt(4 + 2.25^2) = 1.495 > 1 -> flagged
    assert check(_strip(0.6, 2.0), groove_roof=True).overhang_area_mm2 == pytest.approx(2.0 * L, abs=1e-6)


def test_single_strip_0_8_at_0_6_exempt() -> None:
    assert check(_strip(0.6, 0.8), groove_roof=True).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_narrow_ceiling_0_8_at_2_0_still_overhang() -> None:
    assert check(_strip(2.0, 0.8), groove_roof=True).overhang_area_mm2 == pytest.approx(0.8 * L, abs=1e-6)


def test_width_edge_min_altitude_exactly_1_exempt() -> None:
    # triangle area 5.0, longest side 10 -> min altitude 1.0 == GROOVE_ROOF_MAX_WIDTH_MM -> exempt
    assert check(_single_tri(apex_y=1.0), groove_roof=True).overhang_area_mm2 == pytest.approx(0.0, abs=1e-9)


def test_width_edge_min_altitude_1_05_still_overhang() -> None:
    # min altitude 1.05 > 1.0 -> flagged, area 10 * 1.05 / 2
    assert check(_single_tri(apex_y=1.05), groove_roof=True).overhang_area_mm2 == pytest.approx(5.25, abs=1e-6)


def test_grid_flag_off_stays_overhang() -> None:
    assert check(_grid_roof(0.6), groove_roof=False).overhang_area_mm2 == pytest.approx(GRID_AREA_MM2, abs=1e-6)

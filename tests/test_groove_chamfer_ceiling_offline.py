"""Offline repro: groove ceiling runs past the bed-face bbox where the groove meets a bed chamfer.

Live side_wall (print frame, groove y 29.6-30.4): bed rects x 0.0-11.03 and 27.30-34.33 (window edges at 11.03 / 27.30).
Ceiling triangles at z 0.3 reach x 0.676-11.324 and 27.01-33.658 -> 0.29 mm past the rect ends (0.3 / tan 46 deg).
Old code: rect stops at the bed-face bbox, ceiling vertex 11.324 / 27.01 outside -> false overhang.
"""

from types import SimpleNamespace
from typing import Any

from fusion_kit.dfam import _groove_rects, _warp_family, in_warp_groove
from fusion_kit.printability import Mesh, check

Vec = tuple[float, float, float]
SPAN = ((0.0, 0.0, 0.0), (34.33, 90.0, 0.0))


def _box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> Mesh:
    """Closed axis-aligned box, outward winding (same convention as test_groove_exempt_offline._box)."""
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


def _with_bed(m: Mesh) -> Mesh:
    """Bed plate x 40-50, z 0-0.3 far from m: keeps min z = 0 (rotate_mesh normalises min z)."""
    bed = _box(40.0, 0.0, 0.0, 50.0, 10.0, 0.3)
    off = len(m.verts)
    return Mesh(verts=m.verts + bed.verts, tris=m.tris + [(a + off, b + off, c + off) for a, b, c in bed.tris])


def _face(x0: float, y0: float, x1: float, y1: float) -> Any:
    """Bed face given in mm, stored in cm (Fusion units), z = 0, no inner loops."""
    return SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=x0 / 10, y=y0 / 10, z=0.0),
            maxPoint=SimpleNamespace(x=x1 / 10, y=y1 / 10, z=0.0),
        ),
        loops=[],
    )


def _side_bed(x0: float, x1: float) -> list[Any]:
    """Bed strips of one side of the window: three rows separated by two 0.8 mm grooves at y 29.6-30.4, 59.6-60.4."""
    return [_face(x0, 0.0, x1, 29.6), _face(x0, 30.4, x1, 59.6), _face(x0, 60.4, x1, 90.0)]


def _rects() -> list[tuple[int, Any]]:
    # window opening x 11.03-27.30 has no bed; both sides end at the bed chamfer (bbox end)
    bed = _side_bed(0.0, 11.03) + _side_bed(27.30, 34.33)
    return _groove_rects(bed, SPAN)


def test_window_end_rects_reach_ceiling_end() -> None:
    # live: ceiling vertices x 11.324 and 27.01 -> rect ends must cover them (old code: 11.03 / 27.30)
    line = sorted(r for ax, r in _rects() if ax == 0 and abs((r[1] + r[3]) / 2 - 30.0) < 0.01)
    assert len(line) == 2
    assert line[0][2] >= 11.324 - 0.05
    assert line[1][0] <= 27.01 + 0.05
    assert line[0][0] == 0.0  # body-boundary end still clamped to plate edge
    assert line[1][2] == 34.33


def test_window_ceiling_not_overhang() -> None:
    # live ceiling triangles at z 0.3, groove y 29.6-30.4: x 0.676-11.324 and 27.01-33.658
    mesh = _with_bed(_box(0.676, 29.6, 0.3, 11.324, 30.4, 3.0))
    mesh = Mesh(
        verts=mesh.verts + _box(27.01, 29.6, 0.3, 33.658, 30.4, 3.0).verts,
        tris=mesh.tris + [(a + 16, b + 16, c + 16) for a, b, c in _box(27.01, 29.6, 0.3, 33.658, 30.4, 3.0).tris],
    )
    rep = check(mesh, exempt_rects=_warp_family(_rects()))
    assert rep.overhang_area_mm2 == 0.0


def test_window_ceiling_end_in_rect() -> None:
    rects = _warp_family(_rects())
    assert in_warp_groove((11.324, 30.0, 0.3), rects, 0.0) is True
    assert in_warp_groove((27.01, 30.0, 0.3), rects, 0.0) is True

"""FDM printability checker: pure mesh core + Fusion adapter.

Pure core (Mesh, rot_x/rot_y/rot_z, compose, rotate_mesh, check, sweep, clear_rays, march_thickness) imports no adsk,
so it runs offline. Adapter (mesh_from_body, check_body) imports fusion_kit.enums (adsk)
inside the function body only. Units: mm. Up = +Z after rotation. Mesh winding: outward.
"""

from __future__ import annotations

import functools
import math
from collections import defaultdict
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NamedTuple

from fusion_kit._vec import _cross, _dot, _sub
from fusion_kit.dfam import (
    SLIT_MAX_MM,
    WARP_EDGE_PAD_MM,
    WARP_GROOVE_DEPTH_MM,
    GrooveRect,
    in_groove_rect,
    print_frame_body,
    warp_family_mm,
)
from fusion_kit.dfam import (
    annulus_width_mm as _annulus_width_mm,
)

if TYPE_CHECKING:
    import adsk.core
    import adsk.fusion

Vec = tuple[float, float, float]
Rot = tuple[Vec, Vec, Vec]
Tri = tuple[int, int, int]
BedKind = Literal["area", "edge", "point", "none"]

EPS_MM = 1e-6  # strict "above" margin for hanging-edge and floating-vertex tests
DOWN_EPS = 1e-2  # unit normal n_z below -DOWN_EPS = triangle faces downward (underside); 1e-2 (~0.6 deg) absorbs tessellation tilt of vertical walls (false hanging chains)
UP_EPS = 1e-6  # unit normal n_z above +UP_EPS = triangle faces upward (top); |n_z| <= UP_EPS = vertical, neutral
DEGEN_MM = 1e-12  # |cross product| below this = degenerate triangle / zero-length edge
POINT_SPAN_MM = 0.5  # on-bed vertices spanning less than this = point contact
TOP_N = 20
BREP_SNAP_TOL_MM = 0.1  # B-rep projection farther than this from a facet point (centroid or vertex) = not the same surface (chordal tol)
IDENTITY: Rot = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
SLIT_FOOT_TOL_MM = 0.05  # wall facet is a chord of a curved wall: foot point may sit this far outside its triangle
WALL_NZ = 0.3  # |n_z| below this = wall-like face (within ~17 deg of vertical) for the slit test
SHORT_BRIDGE_MAX_MM = 1.0  # flat down-facing B-rep face whose shortest in-plane span <= this = short bridge, PETG bridges it (user P25 2026-11-17)
CANTILEVER_MAX_MM = 1.0  # flat down-facing 1-loop face whose reach from ONE supported edge's line <= this = short cantilever (user P26 2026-11-17)
SHORT_BRIDGE_STEP_DEG = (
    2.0  # in-plane direction step for min-width; discrete directions only overestimate width (safe side: no exemption)
)
BRIDGE_PLANE_TOL_MM = 0.05  # boundary samples this far off the face plane = not a planar face
BRIDGE_SAMPLE_MM = 0.1  # edge sampling pitch for boundary points of a short-bridge candidate
SUPPORT_DROP_MM = (
    0.1  # short-bridge long side counts as supported only if a neighbour reaches this far below the bridge plane
)
PLANE_NORMAL_TOL = (
    1e-4  # unit normals within this per component = same plane (edge-connected triangles merge into one region)
)
GROOVE_ROOF_MAX_MM = (
    WARP_GROOVE_DEPTH_MM + 0.05
)  # warp-groove ceiling: down-facing region this high above bed (depth 0.6 + eps)
GROOVE_ROOF_MAX_WIDTH_MM = 1.0  # warp-groove ceiling: min width of the down-facing region <= this
NOTCH_BRIDGE_MAX_MM = (
    2.6  # teardrop flat top (hole d<=8): flat down-facing region <= this wide, flanked on BOTH long sides
)
SHORT_LEDGE_MAX_MM = 0.5  # down-facing planar region, width PERPENDICULAR to its attached long side <= this = ledge (0.3 mm out of a wall), prints fine
SHORT_LEDGE_ATTACH_FRAC = (
    0.9  # short ledge: long-side edges shared with a non-down face must cover >= this x its long extent
)
SHORT_LEDGE_PARALLEL_DEG = 2.0  # boundary edge within this angle of the long axis = long-side edge
BED_ADJACENT_FLOOR_MM = 0.05  # down-facing region with min Z <= this above bed = touches bed (first layers)
BED_ADJACENT_MAX_MM = 1.0  # ... and max Z <= this (44 deg bed chamfer, zmax 0.97 live), prints fine
BED_ADJACENT_MIN_ANGLE_DEG = 44.0  # ... and face slope from horizontal >= this: bed chamfer live 44 deg, dfam BED_CHAMFER_ANGLE_DEG 46 minus 2 tolerance; shallow faces flagged


@dataclass
class Mesh:
    verts: list[Vec]
    tris: list[Tri]


@dataclass
class PrintReport:
    overhang_area_mm2: float
    worst_overhang_deg: float  # smallest theta from horizontal among failing faces; inf if none
    overhangs: list[tuple[float, float, Vec]]  # (area, theta_deg, centroid), top 20 by area
    hanging_max_mm: float
    hanging_total_mm: float
    hanging_chains: list[tuple[float, Vec, Vec]]  # (length, start, end), top 20 by length
    floating_points: int
    floating_coords: list[Vec]  # top 20, lowest z first
    bed_contact_mm2: float
    bed_contact_kind: BedKind  # area >= min_bed_mm2 | edge (span >= 0.5 mm) | point | none
    height_mm: float
    footprint_xy_mm: tuple[float, float]
    tip_ratio: float
    boundary_edges: int
    nonmanifold_edges: int
    ok: bool

    def line(self) -> str:
        return (
            f"print: overhang_mm2={self.overhang_area_mm2:.2f} "
            f"worst_deg={self.worst_overhang_deg:.1f} "
            f"hanging_max_mm={self.hanging_max_mm:.2f} "
            f"hanging_total_mm={self.hanging_total_mm:.2f} "
            f"floating={self.floating_points} "
            f"bed_mm2={self.bed_contact_mm2:.2f} "
            f"bed={self.bed_contact_kind} "
            f"height={self.height_mm:.2f} "
            f"tip={self.tip_ratio:.2f} "
            f"boundary={self.boundary_edges} "
            f"nonmanifold={self.nonmanifold_edges} "
            f"ok={self.ok}"
        )


# ---------------------------------------------------------------------------
# Vector / matrix helpers (pure)
# ---------------------------------------------------------------------------
def _centroid(verts: list[Vec], tri: Tri) -> Vec:
    a, b, c = verts[tri[0]], verts[tri[1]], verts[tri[2]]
    return (
        (a[0] + b[0] + c[0]) / 3.0,
        (a[1] + b[1] + c[1]) / 3.0,
        (a[2] + b[2] + c[2]) / 3.0,
    )


def _tri_geom(verts: list[Vec], tri: Tri) -> tuple[Vec, float] | None:
    """(unit normal from winding, area mm2); None if degenerate."""
    n = _cross(_sub(verts[tri[1]], verts[tri[0]]), _sub(verts[tri[2]], verts[tri[0]]))
    length = math.sqrt(_dot(n, n))
    if length <= DEGEN_MM:
        return None
    return (n[0] / length, n[1] / length, n[2] / length), length / 2.0


def _faces_down(g: tuple[Vec, float] | None) -> bool:
    return g is not None and g[0][2] < -DOWN_EPS


def _faces_up(g: tuple[Vec, float] | None) -> bool:
    return g is not None and g[0][2] > UP_EPS


def _cs(deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    return math.cos(r), math.sin(r)


def rot_x(deg: float) -> Rot:
    c, s = _cs(deg)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def rot_y(deg: float) -> Rot:
    c, s = _cs(deg)
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def rot_z(deg: float) -> Rot:
    c, s = _cs(deg)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def _mm(a: Rot, b: Rot) -> Rot:
    def cell(i: int, j: int) -> float:
        return a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j]

    return (
        (cell(0, 0), cell(0, 1), cell(0, 2)),
        (cell(1, 0), cell(1, 1), cell(1, 2)),
        (cell(2, 0), cell(2, 1), cell(2, 2)),
    )


def compose(*mats: Rot) -> Rot:
    """Matrix product m0 @ m1 @ ... ; compose(a, b) applies b first, then a."""
    out = IDENTITY
    for m in mats:
        out = _mm(out, m)
    return out


def _apply(r: Rot, v: Vec) -> Vec:
    return (
        r[0][0] * v[0] + r[0][1] * v[1] + r[0][2] * v[2],
        r[1][0] * v[0] + r[1][1] * v[1] + r[1][2] * v[2],
        r[2][0] * v[0] + r[2][1] * v[1] + r[2][2] * v[2],
    )


def rotate_mesh(mesh: Mesh, rot: Rot) -> Mesh:
    """Rotate verts by rot, then translate so min Z = 0 (bed plane)."""
    rotated = [_apply(rot, v) for v in mesh.verts]
    min_z = min((v[2] for v in rotated), default=0.0)
    return Mesh(verts=[(x, y, z - min_z) for x, y, z in rotated], tris=list(mesh.tris))


def weld(mesh: Mesh, tol_mm: float = 1e-4) -> Mesh:
    """Merge vertices in the same tol_mm grid cell (first seen kept), remap tris, drop collapsed tris.

    Fusion tessellates per B-rep face: shared seam nodes come out duplicated, so seam edges look
    like boundaries and real folds are skipped. Grid rounding can split two points lying within
    tol of a cell border; harmless for exact duplicates (measured: copies are bit-identical).
    """
    remap: list[int] = []
    cells: dict[tuple[int, int, int], int] = {}
    verts: list[Vec] = []
    for v in mesh.verts:
        key = (round(v[0] / tol_mm), round(v[1] / tol_mm), round(v[2] / tol_mm))
        idx = cells.get(key)
        if idx is None:
            idx = len(verts)
            cells[key] = idx
            verts.append(v)
        remap.append(idx)
    tris: list[Tri] = []
    for a, b, c in mesh.tris:
        t: Tri = (remap[a], remap[b], remap[c])
        if len(set(t)) == 3:
            tris.append(t)
    return Mesh(verts=verts, tris=tris)


# ---------------------------------------------------------------------------
# Printability checks (pure)
# ---------------------------------------------------------------------------
class _Buckets:
    """XY uniform grid (GRID_CELL_MM) over per-item boxes ((x_lo, y_lo), (x_hi, y_hi)) in mm. query(x, y) = ids whose box
    may hold (x, y): a superset, the caller's exact test decides. None box or more than GRID_MAX_CELLS cells -> `wide`,
    always returned. Keeps candidate x item scans out of check()."""

    def __init__(self, boxes: list[tuple[tuple[float, float], tuple[float, float]] | None]) -> None:
        self.cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.wide: list[int] = []
        for i, box in enumerate(boxes):
            if box is None:
                self.wide.append(i)
                continue
            (x0, y0), (x1, y1) = box
            ix0, iy0 = math.floor(x0 / GRID_CELL_MM), math.floor(y0 / GRID_CELL_MM)
            ix1, iy1 = math.floor(x1 / GRID_CELL_MM), math.floor(y1 / GRID_CELL_MM)
            if (ix1 - ix0 + 1) * (iy1 - iy0 + 1) > GRID_MAX_CELLS:
                self.wide.append(i)
                continue
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    self.cells[(ix, iy)].append(i)

    def query(self, x: float, y: float) -> list[int]:
        return self.wide + self.cells.get((math.floor(x / GRID_CELL_MM), math.floor(y / GRID_CELL_MM)), [])


class _SlitWalls(NamedTuple):
    """_slit_walls output: wall tuples (z_lo, z_hi, unit normal, tri) + XY buckets; index.query gives positions in walls."""

    walls: list[tuple[float, float, Vec, Tri]]
    index: _Buckets


def _rect_buckets(grooves: list[tuple[int, GrooveRect]]) -> _Buckets:
    """XY buckets of groove footprints grown by WARP_EDGE_PAD_MM (the footprint test of in_groove_rect)."""
    return _Buckets(
        [
            ((r[0] - WARP_EDGE_PAD_MM, r[1] - WARP_EDGE_PAD_MM), (r[2] + WARP_EDGE_PAD_MM, r[3] + WARP_EDGE_PAD_MM))
            for _ax, r in grooves
        ]
    )


def _tri_in_groove(verts: list[Vec], t: Tri, grooves: list[tuple[int, GrooveRect]], index: _Buckets) -> bool:
    """All 3 vertices inside ONE qualifying groove rect (in_groove_rect, bed z = 0). Triangle bbox never exempts.
    index = _rect_buckets(grooves). A rect holding all 3 vertices holds t[0] in its XY footprint, so only rects
    bucketed at t[0] are tested (same result as the full scan)."""
    v0 = verts[t[0]]
    return any(all(in_groove_rect(verts[i], grooves[j][1], 0.0) for i in t) for j in index.query(v0[0], v0[1]))


def _is_identity(rot: Rot) -> bool:
    return all(abs(rot[i][j] - IDENTITY[i][j]) <= 1e-9 for i in range(3) for j in range(3))


def _foot_outside_mm(verts: list[Vec], t: Tri, f: Vec, n: Vec) -> float:
    """Distance (mm) from f, on the plane of t (unit winding normal n), beyond the nearest edge of t; <= 0 inside."""
    worst = -math.inf
    for k in range(3):
        p = verts[t[k]]
        q = verts[t[(k + 1) % 3]]
        e = _sub(q, p)
        worst = max(worst, -_dot(_cross(e, _sub(f, p)), n) / math.sqrt(_dot(e, e)))
    return worst


def _slit_reach_mm(verts: list[Vec], s: Tri) -> float:
    """Max in-plane distance (mm) from triangle s of a point within SLIT_FOOT_TOL_MM outside all three edges
    (_foot_outside_mm). That region is s offset outward by the tol: its corner at vertex i lies tol / sin(angle_i / 2)
    from vertex i, the largest at the smallest angle. inf = degenerate (zero-length edge, zero angle)."""
    sin_half_min = math.inf
    for k in range(3):
        p = verts[s[k]]
        a = _sub(verts[s[(k + 1) % 3]], p)
        b = _sub(verts[s[(k + 2) % 3]], p)
        la = math.sqrt(_dot(a, a))
        lb = math.sqrt(_dot(b, b))
        if la <= DEGEN_MM or lb <= DEGEN_MM:
            return math.inf
        cos_a = max(-1.0, min(1.0, _dot(a, b) / (la * lb)))
        sin_half_min = min(sin_half_min, math.sqrt((1.0 - cos_a) / 2.0))
    if sin_half_min <= 0.0:
        return math.inf
    return SLIT_FOOT_TOL_MM / sin_half_min


def _slit_walls(verts: list[Vec], tris: list[Tri], geoms: list[tuple[Vec, float] | None]) -> _SlitWalls:
    """Wall-like faces (|n_z| < WALL_NZ) once per mesh: (z_lo, z_hi, unit normal, tri) + XY buckets. Each wall's XY box is
    its footprint grown by SLIT_MAX_MM + _slit_reach_mm: a centroid that _slit_ceiling accepts lies inside that box.
    Input to _slit_ceiling."""
    walls: list[tuple[float, float, Vec, Tri]] = []
    boxes: list[tuple[tuple[float, float], tuple[float, float]] | None] = []
    for s, g in zip(tris, geoms):
        if g is None or abs(g[0][2]) >= WALL_NZ:
            continue
        zs = [verts[i][2] for i in s]
        walls.append((min(zs) - EPS_MM, max(zs) + EPS_MM, g[0], s))
        reach = _slit_reach_mm(verts, s)
        if math.isinf(reach):
            boxes.append(None)
            continue
        m = SLIT_MAX_MM + reach + EPS_MM
        xs = [verts[i][0] for i in s]
        ys = [verts[i][1] for i in s]
        boxes.append(((min(xs) - m, min(ys) - m), (max(xs) + m, max(ys) + m)))
    return _SlitWalls(walls=walls, index=_Buckets(boxes))


def _slit_ceiling(verts: list[Vec], walls: _SlitWalls, t: Tri) -> bool:
    """Down-facing face t is the ceiling of a slot <= SLIT_MAX_MM wide. Test: centroid c sits in air between two
    wall-like faces (|n_z| < WALL_NZ, from _slit_walls) that face each other: c on each wall's normal side, distance
    d1, d2 with d1 + d2 <= SLIT_MAX_MM, footprint of c's foot point within SLIT_FOOT_TOL_MM of the wall triangle (curved
    walls are tessellated chords). A solid bridge or a thin fin has no such pair (its walls face away from c), so only
    true slits pass."""
    c = _centroid(verts, t)
    near: list[tuple[float, Vec]] = []
    for i in walls.index.query(c[0], c[1]):
        z_lo, z_hi, n, s = walls.walls[i]
        if not (z_lo <= c[2] <= z_hi):
            continue
        d = _dot(_sub(c, verts[s[0]]), n)
        if not (0.0 < d <= SLIT_MAX_MM):
            continue
        foot = (c[0] - d * n[0], c[1] - d * n[1], c[2] - d * n[2])
        if _foot_outside_mm(verts, s, foot, n) <= SLIT_FOOT_TOL_MM:
            near.append((d, n))
    return any(
        d1 + d2 <= SLIT_MAX_MM and _dot(n1, n2) < 0.0 for i, (d1, n1) in enumerate(near) for d2, n2 in near[i + 1 :]
    )


def _same_plane(n1: Vec, n2: Vec) -> bool:
    return max(abs(n1[0] - n2[0]), abs(n1[1] - n2[1]), abs(n1[2] - n2[2])) <= PLANE_NORMAL_TOL


def _long_side_attachment(
    verts: list[Vec],
    tris: list[Tri],
    geoms: list[tuple[Vec, float] | None],
    idxs: list[int],
    rid: int,
    edge_tris: dict[tuple[int, int], list[int]],
    region_of: list[int],
    n: Vec,
) -> tuple[float, float, float] | None:
    """Attachment of flat down-facing region `idxs` (id rid, unit normal n) to NON-down-facing neighbours (walls, upward
    faces). Long axis u = longest attached boundary edge; width = span of region points along d = n x u, i.e.
    perpendicular to that edge. Returns (width_mm, covered_mm, long_extent_mm): covered = summed length of attached
    boundary edges parallel to u (SHORT_LEDGE_PARALLEL_DEG), long_extent = span along u. None if no attached edge."""
    attached: set[tuple[int, int]] = set()
    for ti in idxs:
        t = tris[ti]
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            key = (min(a, b), max(a, b))
            if any(region_of[o] != rid and geoms[o] is not None and not _faces_down(geoms[o]) for o in edge_tris[key]):
                attached.add(key)
    if not attached:
        return None

    def edge_vec(key: tuple[int, int]) -> Vec:
        return _sub(verts[key[1]], verts[key[0]])

    longest = max(attached, key=lambda k: _dot(edge_vec(k), edge_vec(k)))
    ev = edge_vec(longest)
    ev_len = math.sqrt(_dot(ev, ev))
    if ev_len <= DEGEN_MM:
        return None
    u = (ev[0] / ev_len, ev[1] / ev_len, ev[2] / ev_len)
    d = _cross(n, u)
    d_len = math.sqrt(_dot(d, d))
    if d_len <= DEGEN_MM:
        return None
    d = (d[0] / d_len, d[1] / d_len, d[2] / d_len)
    pts = [verts[i] for i in {v for ti in idxs for v in tris[ti]}]
    across = [_dot(p, d) for p in pts]
    along = [_dot(p, u) for p in pts]
    cos_par = math.cos(math.radians(SHORT_LEDGE_PARALLEL_DEG))
    covered = 0.0
    for key in attached:
        e = edge_vec(key)
        e_len = math.sqrt(_dot(e, e))
        if e_len > DEGEN_MM and abs(_dot(e, u)) >= cos_par * e_len:
            covered += e_len
    return max(across) - min(across), covered, max(along) - min(along)


def _exempt_flat_regions(
    verts: list[Vec],
    tris: list[Tri],
    geoms: list[tuple[Vec, float] | None],
    min_angle_deg: float,
    angle_tol_deg: float,
    groove_roof: bool,
    notch_bridge: bool,
    short_ledge: bool,
    bed_adjacent: bool,
) -> set[int]:
    """Triangle ids of flat planar regions (edge-connected, equal normal) exempt from overhang. Only regions already
    below min_angle are tested.
    groove_roof: all verts <= GROOVE_ROOF_MAX_MM above bed and min width <= GROOVE_ROOF_MAX_WIDTH_MM (warp-groove
    ceiling, judged in the frame passed to check(), any orientation).
    notch_bridge: span across the flanks <= NOTCH_BRIDGE_MAX_MM and BOTH long sides flanked by down-facing regions at >= min_angle
    - tol whose horizontal normals face each other (teardrop flanks). One flank only = ledge, stays flagged.
    short_ledge: attached along its long side (see _long_side_attachment): perpendicular width <= SHORT_LEDGE_MAX_MM and
    long-side attached edges cover >= SHORT_LEDGE_ATTACH_FRAC of the long extent (narrow ledge, prints fine; a
    cantilever off one short end fails the width test).
    bed_adjacent: min Z <= BED_ADJACENT_FLOOR_MM and max Z <= BED_ADJACENT_MAX_MM and slope >= BED_ADJACENT_MIN_ANGLE_DEG
    (bed chamfer at first layers, prints fine; shallow faces touching the bed stay flagged)."""
    parent = list(range(len(tris)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edge_tris: dict[tuple[int, int], list[int]] = defaultdict(list)
    for ti, t in enumerate(tris):
        if geoms[ti] is None:
            continue
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edge_tris[(min(a, b), max(a, b))].append(ti)
    for adj in edge_tris.values():
        for i in adj:
            gi = geoms[i]
            for j in adj:
                gj = geoms[j]
                if i < j and gi is not None and gj is not None and _same_plane(gi[0], gj[0]):
                    parent[find(i)] = find(j)
    region_of = [find(i) for i in range(len(tris))]
    members: dict[int, list[int]] = defaultdict(list)
    for ti, g in enumerate(geoms):
        if g is not None:
            members[region_of[ti]].append(ti)

    exempt: set[int] = set()
    for rid, idxs in members.items():
        g0 = geoms[idxs[0]]
        if g0 is None or not _faces_down(g0):
            continue
        slope_deg = math.degrees(math.acos(min(1.0, -g0[0][2])))
        if slope_deg >= min_angle_deg - angle_tol_deg:
            continue
        pts = [verts[i] for i in {v for ti in idxs for v in tris[ti]}]
        width = _min_width_mm(pts, g0[0])
        if (
            groove_roof
            and width <= GROOVE_ROOF_MAX_WIDTH_MM + EPS_MM
            and max(p[2] for p in pts) <= GROOVE_ROOF_MAX_MM + EPS_MM
        ):
            exempt.update(idxs)
            continue
        attach = (
            _long_side_attachment(verts, tris, geoms, idxs, rid, edge_tris, region_of, g0[0]) if short_ledge else None
        )
        # attach = (perp width, covered long-side length, long extent)
        if (
            short_ledge
            and attach is not None
            and attach[0] <= SHORT_LEDGE_MAX_MM + EPS_MM
            and attach[1] >= SHORT_LEDGE_ATTACH_FRAC * attach[2] - EPS_MM
        ):
            exempt.update(idxs)
            continue
        if (
            bed_adjacent
            and min(p[2] for p in pts) <= BED_ADJACENT_FLOOR_MM + EPS_MM
            and max(p[2] for p in pts) <= BED_ADJACENT_MAX_MM + EPS_MM
            and slope_deg >= BED_ADJACENT_MIN_ANGLE_DEG - EPS_MM
        ):
            exempt.update(idxs)
            continue
        if notch_bridge:
            flanks: dict[int, Vec] = {}
            for ti in idxs:
                t = tris[ti]
                for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                    for o in edge_tris[(min(a, b), max(a, b))]:
                        go = geoms[o]
                        if region_of[o] == rid or go is None or not _faces_down(go):
                            continue
                        if math.degrees(math.acos(min(1.0, -go[0][2]))) >= min_angle_deg - angle_tol_deg:
                            flanks[region_of[o]] = go[0]
            normals = list(flanks.values())
            pair = next(
                ((p, q) for k, p in enumerate(normals) for q in normals[k + 1 :] if p[0] * q[0] + p[1] * q[1] < 0.0),
                None,
            )
            if pair is not None:
                # notch width = in-plane span ACROSS the flanks (direction from flank q to flank p), not min span
                n = g0[0]
                d = _sub(pair[0], pair[1])
                k_n = _dot(d, n)
                d = (d[0] - k_n * n[0], d[1] - k_n * n[1], d[2] - k_n * n[2])
                d_len = math.sqrt(_dot(d, d))
                if d_len > DEGEN_MM:
                    proj = [_dot(p, d) / d_len for p in pts]
                    if max(proj) - min(proj) <= NOTCH_BRIDGE_MAX_MM + EPS_MM:
                        exempt.update(idxs)
    return exempt


def check(
    mesh: Mesh,
    min_angle_deg: float = 45.0,
    bed_tol_mm: float = 0.05,
    bridge_ok_mm: float = 0.0,
    min_bed_mm2: float = 25.0,
    angle_tol_deg: float = 0.05,
    fold_min_deg: float = 2.0,
    exempt_rects: list[tuple[int, GrooveRect]] | None = None,
    overhang_ok: Callable[[Tri], bool] | None = None,
    groove_roof: bool = False,
    notch_bridge: bool = False,
    short_ledge: bool = False,
    bed_adjacent: bool = False,
) -> PrintReport:
    """Printability metrics for a mesh already rotated so print-up = +Z (min z = 0).

    exempt_rects (default None = no exemption): qualifying warp-groove rects (dfam.warp_family_mm) in THIS mesh
    frame. A down-facing triangle with all 3 vertices inside one rect (z <= 0.5 mm) is not overhang. Rects are
    design frame: pass them only for the identity orientation (sweep / check_body do this).

    Overhang: face fails if theta < min_angle_deg - angle_tol_deg, except slot ceilings (_slit_ceiling: <= SLIT_MAX_MM
    between facing walls, closed by slicer). Hanging edge: slope < min_angle_deg
    (same tolerance), adjacent normals differ by >= fold_min_deg (a coplanar diagonal inside a sloped
    face is not a fold), and its low end lies on a local-minimum plateau: the group of connected
    equal-Z vertices has every outside neighbour strictly higher, and sits above bed. And one of the
    two faces on the edge points down (n_z < -UP_EPS): material above, air below. A pocket floor
    (up-facing, vertical walls) is supported. A plate bottom edge tilted 50 deg has an up-facing
    neighbour but a down-facing underside, so it still hangs.
    Floating point: all neighbours higher, no incident triangle faces up, at least one faces down;
    vertices with 1-2 hanging edges excluded (chain ends), 3+ = point tip, counted as floating.
    bed_contact_kind: "area" if down-facing bed area >= min_bed_mm2 (and > 0); else "none" if no
    vertex on bed; "point" if on-bed vertices span < 0.5 mm; else "edge".
    ok requires overhang 0, hanging_max <= bridge_ok_mm, no floating points, bed kind "area".
    groove_roof / notch_bridge / short_ledge / bed_adjacent (default False): geometric flat-region exemptions, see _exempt_flat_regions.
    """
    verts = mesh.verts
    tris = mesh.tris
    geoms = [_tri_geom(verts, t) for t in tris]

    def is_bed(t: Tri) -> bool:
        return all(verts[i][2] <= bed_tol_mm for i in t)

    # 1. overhang faces: face tilted theta from horizontal, facing down (n_z = -cos theta).
    #    Fails when theta < min_angle_deg  <=>  n_z < -cos(min_angle_deg).
    overhangs: list[tuple[float, float, Vec]] = []
    flat_ok: set[int] = (
        _exempt_flat_regions(
            verts, tris, geoms, min_angle_deg, angle_tol_deg, groove_roof, notch_bridge, short_ledge, bed_adjacent
        )
        if groove_roof or notch_bridge or short_ledge or bed_adjacent
        else set()
    )
    # Cheap angle test first: groove / slit / B-rep tests run only on real overhang candidates (pure AND, same result).
    slit_walls = _slit_walls(verts, tris, geoms)
    groove_index = _rect_buckets(exempt_rects or [])
    for ti, (t, g) in enumerate(zip(tris, geoms)):
        if g is None or is_bed(t) or ti in flat_ok:
            continue
        n, area = g
        if n[2] >= 0.0:
            continue
        theta = math.degrees(math.acos(min(1.0, -n[2])))
        if theta >= min_angle_deg - angle_tol_deg:
            continue
        if exempt_rects and _tri_in_groove(verts, t, exempt_rects, groove_index):
            continue
        if not (overhang_ok is not None and overhang_ok(t)) and not _slit_ceiling(verts, slit_walls, t):
            overhangs.append((area, theta, _centroid(verts, t)))
    overhang_area = sum((o[0] for o in overhangs), 0.0)
    worst = min((o[1] for o in overhangs), default=math.inf)
    overhangs.sort(key=lambda o: -o[0])
    del overhangs[TOP_N:]

    # 2a. neighbours, any_up/any_down, Z-plateaus (connected equal-Z vertex groups).
    #     plateau_is_min: above bed, has outside neighbours, all strictly higher = local Z minimum.
    nbrs: dict[int, set[int]] = defaultdict(set)
    any_up: dict[int, bool] = {}
    any_down: dict[int, bool] = {}
    for ti, t in enumerate(tris):
        g = geoms[ti]
        if g is None:
            continue
        for a in t:
            any_up[a] = any_up.get(a, False) or _faces_up(g)
            any_down[a] = any_down.get(a, False) or _faces_down(g)
            for b in t:
                if a != b:
                    nbrs[a].add(b)
    plateau_of: dict[int, int] = {}
    plateau_is_min: list[bool] = []
    for seed in nbrs:
        if seed in plateau_of:
            continue
        pid = len(plateau_is_min)
        z0 = verts[seed][2]
        plateau_of[seed] = pid
        members = [seed]
        stack = [seed]
        while stack:
            v = stack.pop()
            for w in nbrs[v]:
                if w not in plateau_of and abs(verts[w][2] - z0) <= EPS_MM:
                    plateau_of[w] = pid
                    members.append(w)
                    stack.append(w)
        outside = [w for v in members for w in nbrs[v] if plateau_of.get(w) != pid]
        plateau_is_min.append(z0 > bed_tol_mm and len(outside) > 0 and all(verts[w][2] > z0 + EPS_MM for w in outside))

    # 2. hanging edges: shallow edge (slope < min_angle), off bed, not a coplanar diagonal, whose low
    #    end lies on a local-minimum plateau and one adjacent face points down (see hang rule below).
    edge_tris: dict[tuple[int, int], list[int]] = defaultdict(list)
    for ti, t in enumerate(tris):
        if geoms[ti] is None:
            continue
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edge_tris[(min(a, b), max(a, b))].append(ti)
    boundary = 0
    nonmanifold = 0
    hang_len: dict[tuple[int, int], float] = {}
    hang_deg: dict[int, int] = defaultdict(int)
    for (a, b), adj in edge_tris.items():
        if len(adj) == 1:
            boundary += 1
            continue
        if len(adj) > 2:
            nonmanifold += 1
            continue
        za, zb = verts[a][2], verts[b][2]
        if za <= bed_tol_mm or zb <= bed_tol_mm:
            continue
        length = math.dist(verts[a], verts[b])
        if length <= DEGEN_MM:
            continue
        horiz = math.hypot(verts[b][0] - verts[a][0], verts[b][1] - verts[a][1])
        slope = math.degrees(math.atan2(abs(zb - za), horiz))
        if slope >= min_angle_deg - angle_tol_deg:
            continue
        g0, g1 = geoms[adj[0]], geoms[adj[1]]
        if g0 is None or g1 is None:
            continue
        fold = math.degrees(math.acos(max(-1.0, min(1.0, _dot(g0[0], g1[0])))))
        if fold < fold_min_deg:
            continue
        low = a if za <= zb else b
        # hanging = material above, air below: at least one adjacent face points down.
        # Pocket floor (up-facing) + vertical walls -> supported, not hanging.
        if plateau_is_min[plateau_of[low]] and (_faces_down(g0) or _faces_down(g1)):
            hang_len[(a, b)] = length
            hang_deg[a] += 1
            hang_deg[b] += 1

    # chain connected hanging edges (shared vertices)
    adj_v: dict[int, list[int]] = defaultdict(list)
    for a, b in hang_len:
        adj_v[a].append(b)
        adj_v[b].append(a)
    comp_of: dict[int, int] = {}
    comp_verts: list[list[int]] = []
    for s in adj_v:
        if s in comp_of:
            continue
        cid = len(comp_verts)
        comp_verts.append([])
        comp_of[s] = cid
        stack = [s]
        while stack:
            v = stack.pop()
            comp_verts[cid].append(v)
            for w in adj_v[v]:
                if w not in comp_of:
                    comp_of[w] = cid
                    stack.append(w)
    comp_len = [0.0] * len(comp_verts)
    for (a, _b), length in hang_len.items():
        comp_len[comp_of[a]] += length
    chains: list[tuple[float, Vec, Vec]] = []
    for cid, vs in enumerate(comp_verts):
        ends = [v for v in vs if len(adj_v[v]) == 1]
        s_v, e_v = (ends[0], ends[-1]) if len(ends) >= 2 else (vs[0], vs[0])
        chains.append((comp_len[cid], verts[s_v], verts[e_v]))
    chains.sort(key=lambda c: -c[0])
    hang_max = max((c[0] for c in chains), default=0.0)
    hang_total = sum(hang_len.values(), 0.0)
    del chains[TOP_N:]

    # 3. floating points: vertex off bed, every neighbour higher, no incident up-facing tri,
    #    at least one incident down-facing tri; chain-end/valley verts (1-2 hanging edges) excluded,
    #    point tip (3+ hanging edges meeting) counted here
    floating: list[int] = []
    for v, ns in nbrs.items():
        z = verts[v][2]
        if z <= bed_tol_mm or 0 < hang_deg.get(v, 0) < 3 or any_up.get(v, False) or not any_down.get(v, False):
            continue
        if all(verts[w][2] > z + EPS_MM for w in ns):
            floating.append(v)
    floating.sort(key=lambda v: verts[v][2])
    floating_coords = [verts[v] for v in floating[:TOP_N]]

    # 4. bed contact: on-bed triangles facing down (area), on-bed vertex span (kind)
    bed_area = 0.0
    bed_pts: list[Vec] = []
    for t, g in zip(tris, geoms):
        if g is None or not is_bed(t):
            continue
        n, area = g
        if n[2] < -0.99:
            bed_area += area
            bed_pts.extend(verts[i] for i in t)
    on_bed = [v for v in verts if v[2] <= bed_tol_mm]
    if on_bed:
        span = math.hypot(
            max(v[0] for v in on_bed) - min(v[0] for v in on_bed),
            max(v[1] for v in on_bed) - min(v[1] for v in on_bed),
        )
    else:
        span = 0.0
    bed_kind: BedKind
    if bed_area > 0.0 and bed_area >= min_bed_mm2:
        bed_kind = "area"
    elif not on_bed:
        bed_kind = "none"
    elif span < POINT_SPAN_MM:
        bed_kind = "point"
    else:
        bed_kind = "edge"

    height = max((v[2] for v in verts), default=0.0)
    if bed_pts:
        dx = max(p[0] for p in bed_pts) - min(p[0] for p in bed_pts)
        dy = max(p[1] for p in bed_pts) - min(p[1] for p in bed_pts)
    else:
        dx = dy = 0.0
    short = min(dx, dy)
    tip = height / short if bed_pts and short > 0.0 else math.inf

    ok = overhang_area == 0.0 and hang_max <= bridge_ok_mm and len(floating) == 0 and bed_kind == "area"
    return PrintReport(
        overhang_area_mm2=overhang_area,
        worst_overhang_deg=worst,
        overhangs=overhangs,
        hanging_max_mm=hang_max,
        hanging_total_mm=hang_total,
        hanging_chains=chains,
        floating_points=len(floating),
        floating_coords=floating_coords,
        bed_contact_mm2=bed_area,
        bed_contact_kind=bed_kind,
        height_mm=height,
        footprint_xy_mm=(dx, dy),
        tip_ratio=tip,
        boundary_edges=boundary,
        nonmanifold_edges=nonmanifold,
        ok=ok,
    )


def sweep(
    mesh: Mesh,
    candidates: dict[str, Rot],
    min_angle_deg: float = 45.0,
    bed_tol_mm: float = 0.05,
    bridge_ok_mm: float = 0.0,
    min_bed_mm2: float = 25.0,
    angle_tol_deg: float = 0.05,
    fold_min_deg: float = 2.0,
    exempt_rects: list[tuple[int, GrooveRect]] | None = None,
) -> list[tuple[str, PrintReport]]:
    """check() every candidate orientation. Best first: bed "area" before any other bed kind,
    then by (floating, hanging_max, overhang, -bed, height). exempt_rects applies to the identity candidate only."""
    out = [
        (
            name,
            check(
                rotate_mesh(mesh, rot),
                min_angle_deg=min_angle_deg,
                bed_tol_mm=bed_tol_mm,
                bridge_ok_mm=bridge_ok_mm,
                min_bed_mm2=min_bed_mm2,
                angle_tol_deg=angle_tol_deg,
                fold_min_deg=fold_min_deg,
                exempt_rects=exempt_rects if _is_identity(rot) else None,
            ),
        )
        for name, rot in candidates.items()
    ]
    out.sort(
        key=lambda r: (
            0 if r[1].bed_contact_kind == "area" else 1,
            r[1].floating_points,
            r[1].hanging_max_mm,
            r[1].overhang_area_mm2,
            -r[1].bed_contact_mm2,
            r[1].height_mm,
        )
    )
    return out


# ---------------------------------------------------------------------------
# Line-of-sight (pure). Ray = segment origin + t*direction, 0 <= t <= length_mm.
# ---------------------------------------------------------------------------
RAY_EPS = 1e-9  # Moller-Trumbore barycentric / parametric tolerance: edge and vertex hits count
RAY_PAR_EPS = 1e-9  # |det| below this = ray parallel to triangle plane: no hit
GRID_PAD_MM = 1e-5  # bbox padding when binning triangles into the 2D grid; only adds candidates
_PERP_AXES: tuple[tuple[int, int], ...] = (
    (1, 2),
    (0, 2),
    (0, 1),
)  # perpendicular (u, v) axes for X, Y, Z
_Row = tuple[Vec, Vec, Vec, Vec, Vec, Vec, float]  # v0, v1, v2, e1, e2, p = d x e2, det


def _axis_of(d: Vec) -> int | None:
    """Index k if unit d is parallel to axis k within RAY_EPS, else None."""
    for k in range(3):
        if abs(d[k]) >= 1.0 - RAY_EPS and all(abs(d[j]) <= RAY_EPS for j in range(3) if j != k):
            return k
    return None


def _mt_hit(row: _Row, o: Vec, d: Vec, length_mm: float) -> bool:
    """Moller-Trumbore for segment o + t*d, 0 <= t <= length_mm. Boundary (edge/vertex) counts as hit."""
    v0, _, _, e1, e2, p, det = row
    s = _sub(o, v0)
    u = _dot(s, p) / det
    if u < -RAY_EPS or u > 1.0 + RAY_EPS:
        return False
    q = _cross(s, e1)
    v = _dot(d, q) / det
    if v < -RAY_EPS or u + v > 1.0 + RAY_EPS:
        return False
    t = _dot(e2, q) / det
    return -RAY_EPS <= t <= length_mm + RAY_EPS


def clear_rays(mesh: Mesh, origins: list[Vec], direction: Vec, length_mm: float) -> list[Vec]:
    """Origins whose segment hits NO triangle of mesh. Pure Python, stdlib only.

    Axis-aligned direction: triangles binned into a 2D grid over the two perpendicular axes,
    only the ray's cell is tested (O(rays x tris-per-cell)). Any other direction: every
    triangle is tested (O(rays x tris)). Rays parallel to a triangle plane never hit it.
    Raises ValueError if length_mm <= 0 or direction is zero.
    """
    if length_mm <= 0.0:
        raise ValueError(f"length_mm must be > 0, got {length_mm}")
    norm = math.sqrt(_dot(direction, direction))
    if norm == 0.0:
        raise ValueError("direction is zero")
    d: Vec = (direction[0] / norm, direction[1] / norm, direction[2] / norm)
    verts = mesh.verts
    rows: list[_Row] = []
    for a, b, c in mesh.tris:
        v0, v1, v2 = verts[a], verts[b], verts[c]
        e1 = _sub(v1, v0)
        e2 = _sub(v2, v0)
        p = _cross(d, e2)
        det = _dot(e1, p)
        if abs(det) < RAY_PAR_EPS:
            continue
        rows.append((v0, v1, v2, e1, e2, p, det))
    if not rows:
        return list(origins)
    clear: list[Vec] = []
    k = _axis_of(d)
    if k is None:
        for o in origins:
            if not any(_mt_hit(r, o, d, length_mm) for r in rows):
                clear.append(o)
        return clear
    a_ax, b_ax = _PERP_AXES[k]
    corners = [p for r in rows for p in (r[0], r[1], r[2])]
    u_lo = min(q[a_ax] for q in corners) - GRID_PAD_MM
    u_hi = max(q[a_ax] for q in corners) + GRID_PAD_MM
    v_lo = min(q[b_ax] for q in corners) - GRID_PAD_MM
    v_hi = max(q[b_ax] for q in corners) + GRID_PAD_MM
    n = max(1, int(math.sqrt(len(rows))))
    cu = (u_hi - u_lo) / n
    cv = (v_hi - v_lo) / n

    def cell(x: float, lo: float, size: float) -> int:
        # int() is monotone and the clamp preserves that, so a point inside a bbox always lands
        # in a cell the bbox was binned into.
        return min(n - 1, max(0, int((x - lo) / size)))

    cells: list[list[int]] = [[] for _ in range(n * n)]
    for idx, r in enumerate(rows):
        us = [r[0][a_ax], r[1][a_ax], r[2][a_ax]]
        vs = [r[0][b_ax], r[1][b_ax], r[2][b_ax]]
        for ci in range(
            cell(min(us) - GRID_PAD_MM, u_lo, cu),
            cell(max(us) + GRID_PAD_MM, u_lo, cu) + 1,
        ):
            for cj in range(
                cell(min(vs) - GRID_PAD_MM, v_lo, cv),
                cell(max(vs) + GRID_PAD_MM, v_lo, cv) + 1,
            ):
                cells[ci * n + cj].append(idx)
    for o in origins:
        ci = cell(o[a_ax], u_lo, cu)
        cj = cell(o[b_ax], v_lo, cv)
        if not any(_mt_hit(rows[idx], o, d, length_mm) for idx in cells[ci * n + cj]):
            clear.append(o)
    return clear


WALL_BISECT_STEPS = 10  # bisection halvings after the march: error <= resolution_mm / 2**10


def march_thickness(
    is_inside: Callable[[Vec], bool],
    point_mm: Vec,
    direction: Vec,
    max_mm: float,
    resolution_mm: float,
) -> float:
    """Length (mm) of the solid run from point_mm along direction, to the first non-INSIDE point. Pure.

    Samples at k * resolution_mm (k = 1, 2, ... up to max_mm); stops at the first sample where
    is_inside is False (outside or on surface). Bisects WALL_BISECT_STEPS times between the last
    inside sample and that sample (they are exactly resolution_mm apart), so the error is at most
    resolution_mm / 2**WALL_BISECT_STEPS for one solid run. If the first sample is already outside,
    the run ends inside (0, resolution_mm): bisect that interval with point_mm counted as inside.
    Known blind spot: a void thinner than resolution_mm lying between two samples is stepped over
    -> run reported too long; keep resolution_mm below the smallest feature of interest.
    Raises ValueError: resolution_mm <= 0 or max_mm < resolution_mm; direction zero; no INSIDE point
    in (0, resolution_mm] (air beside point_mm along direction); no exit within max_mm.
    """
    if resolution_mm <= 0.0 or max_mm < resolution_mm:
        raise ValueError(f"need 0 < resolution_mm <= max_mm, got resolution_mm={resolution_mm}, max_mm={max_mm}")
    norm = math.sqrt(_dot(direction, direction))
    if norm == 0.0:
        raise ValueError("direction is zero")
    d: Vec = (direction[0] / norm, direction[1] / norm, direction[2] / norm)

    def at(s: float) -> Vec:
        return (point_mm[0] + s * d[0], point_mm[1] + s * d[1], point_mm[2] + s * d[2])

    last_in: float | None = None
    first_out: float | None = None
    k_max = int(max_mm / resolution_mm + 1e-9)
    for k in range(1, k_max + 1):
        s = k * resolution_mm
        if is_inside(at(s)):
            last_in = s
        else:
            first_out = s
            break
    if first_out is None:
        raise ValueError(f"no exit within max_mm={max_mm}: solid continues past max_mm along direction")
    found_inside = last_in is not None
    if last_in is None:
        last_in = 0.0  # first sample outside: wall (if any) lies in (0, resolution_mm)
    lo, hi = last_in, first_out
    for _ in range(WALL_BISECT_STEPS):
        mid = (lo + hi) / 2.0
        if is_inside(at(mid)):
            lo = mid
            found_inside = True
        else:
            hi = mid
    if not found_inside:
        raise ValueError("no material just beyond point_mm along direction (no INSIDE sample in (0, resolution_mm])")
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Fusion adapter (adsk imported lazily; cm -> mm here)
# ---------------------------------------------------------------------------
def mesh_from_body(
    body: adsk.fusion.BRepBody | None,
    quality: adsk.fusion.TriangleMeshQualityOptions | None = None,
) -> Mesh:
    """Triangulate one BRepBody -> Mesh in mm, body frame.

    Default quality enums.NORMAL_QUALITY_MESH. Winding: global vote of geometric normals vs
    TriangleMesh.normalVectorsAsDouble (per node); whole mesh flipped if vote is negative.
    If normalVectorsAsDouble is empty, no flip is applied (winding unverified).
    Raises ValueError if body is None or createMeshCalculator / setQuality / calculate fail.
    Result is welded (weld()): coincident per-face copies merged so seam edges are shared.
    """
    if body is None:
        raise ValueError("mesh_from_body: body is None")
    if quality is None:
        from fusion_kit import enums

        quality = enums.NORMAL_QUALITY_MESH
    calc = body.meshManager.createMeshCalculator()
    if calc is None:
        raise ValueError("createMeshCalculator returned None")
    if not calc.setQuality(quality):
        raise ValueError("setQuality returned False")
    tm = calc.calculate()
    if tm is None:
        raise ValueError("calculate returned None")
    coords = tm.nodeCoordinatesAsDouble
    idx = tm.nodeIndices
    if len(idx) != 3 * tm.triangleCount:
        raise ValueError(f"nodeIndices {len(idx)} != 3*triangleCount {tm.triangleCount}")
    verts: list[Vec] = [
        (coords[i] * 10.0, coords[i + 1] * 10.0, coords[i + 2] * 10.0) for i in range(0, len(coords), 3)
    ]
    tris: list[Tri] = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, len(idx), 3)]
    normals = tm.normalVectorsAsDouble
    if len(normals) == len(coords):
        vote = 0.0
        for t in tris:
            g = _tri_geom(verts, t)
            if g is None:
                continue
            for i in t:
                nv: Vec = (normals[3 * i], normals[3 * i + 1], normals[3 * i + 2])
                vote += _dot(g[0], nv)
        if vote < 0.0:
            tris = [(a, c, b) for a, b, c in tris]
    return weld(Mesh(verts=verts, tris=tris))


class _FaceBox(NamedTuple):
    """B-rep face with its bbox read once. lo / hi = bbox min / max corners in mm (body frame)."""

    face: adsk.fusion.BRepFace
    lo: Vec
    hi: Vec


class _GeomCache:
    """Per check_body B-rep read cache (one per printed body; active only while check_body runs with identity rot).
    boxes: face boxes, filled on first snap (lazy: a call that never snaps reads no faces). by_tid: box by face tempId
    (also neighbour faces of edges). grid: GRID_CELL_MM buckets of 1 mm-expanded face boxes -> indices into boxes.
    wide: indices of faces spanning more than GRID_MAX_CELLS buckets (always candidates). edge_pts / face_bool /
    face_width: per-tempId memo of pure B-rep reads."""

    def __init__(self, body: adsk.fusion.BRepBody) -> None:
        self.body = body
        self.boxes: list[_FaceBox] | None = None
        self.by_tid: dict[object, _FaceBox] = {}
        self.grid: dict[tuple[int, int, int], list[int]] = {}
        self.wide: list[int] = []
        self.edge_pts: dict[object, list[Vec] | None] = {}
        self.face_bool: dict[tuple[str, object], bool] = {}
        self.face_width: dict[object, float | None] = {}


GRID_CELL_MM = 5.0  # bucket size of the snap grid
GRID_MAX_CELLS = 4096  # face boxes covering more buckets than this go to _GeomCache.wide

# check_body sets a _GeomCache while check() runs. Keeps the cache out of the _brep_overhang_deg / _brep_slit_ceiling
# signatures (their call shape is monkeypatched by tests).
_FACE_BOXES: ContextVar[_GeomCache | None] = ContextVar("_FACE_BOXES", default=None)


def _grid_add(cache: _GeomCache, i: int, box: _FaceBox) -> None:
    """Register face box i in every grid bucket its 1 mm-expanded box touches (same expansion as _project_on_face)."""
    lo = [math.floor((box.lo[k] - 1.0) / GRID_CELL_MM) for k in range(3)]
    hi = [math.floor((box.hi[k] + 1.0) / GRID_CELL_MM) for k in range(3)]
    cells = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
    if cells > GRID_MAX_CELLS:
        cache.wide.append(i)
        return
    for x in range(lo[0], hi[0] + 1):
        for y in range(lo[1], hi[1] + 1):
            for z in range(lo[2], hi[2] + 1):
                cache.grid.setdefault((x, y, z), []).append(i)


def _memo_face_bool(
    name: str,
) -> Callable[[Callable[[adsk.fusion.BRepFace], bool]], Callable[[adsk.fusion.BRepFace], bool]]:
    """Memoize a pure per-B-rep-face bool predicate by (name, tempId) for the active check_body. No cache: call through.
    Exceptions are not stored (next call re-raises)."""

    def deco(fn: Callable[[adsk.fusion.BRepFace], bool]) -> Callable[[adsk.fusion.BRepFace], bool]:
        @functools.wraps(fn)
        def wrapper(face: adsk.fusion.BRepFace) -> bool:
            cache = _FACE_BOXES.get()
            tid = getattr(face, "tempId", None)
            if cache is None or tid is None:
                return fn(face)
            key = (name, tid)
            if key not in cache.face_bool:
                cache.face_bool[key] = fn(face)
            return cache.face_bool[key]

        return wrapper

    return deco


def _face_annulus_width(face: adsk.fusion.BRepFace) -> float | None:
    """_annulus_width_mm (dfam.annulus_width_mm) memoized per tempId for the active check_body."""
    cache = _FACE_BOXES.get()
    tid = getattr(face, "tempId", None)
    if cache is None or tid is None:
        return _annulus_width_mm(face)
    if tid not in cache.face_width:
        cache.face_width[tid] = _annulus_width_mm(face)
    return cache.face_width[tid]


def _face_box(face: adsk.fusion.BRepFace) -> _FaceBox:
    """One face.boundingBox read (Fusion COM call) -> _FaceBox with corners in mm."""
    bb = face.boundingBox
    return _FaceBox(
        face,
        (bb.minPoint.x * 10, bb.minPoint.y * 10, bb.minPoint.z * 10),
        (bb.maxPoint.x * 10, bb.maxPoint.y * 10, bb.maxPoint.z * 10),
    )


def _face_boxes(body: adsk.fusion.BRepBody) -> list[_FaceBox]:
    """Face boxes of body: the check_body cache when set for this body (read once), else read now."""
    cache = _FACE_BOXES.get()
    if cache is None or cache.body is not body:
        return [_face_box(f) for f in body.faces]
    if cache.boxes is None:
        cache.boxes = [_face_box(f) for f in body.faces]
        for i, box in enumerate(cache.boxes):
            tid = getattr(box.face, "tempId", None)
            if tid is not None:
                cache.by_tid[tid] = box
            _grid_add(cache, i, box)
    return cache.boxes


def _snap_boxes(body: adsk.fusion.BRepBody, point_mm: Vec) -> list[_FaceBox]:
    """Face boxes that can hold point_mm (1 mm-expanded box contains it), in body.faces order. No cache for body: all
    faces. Drops only faces that _project_on_face rejects on its own bbox test, so snap results are unchanged."""
    boxes = _face_boxes(body)
    cache = _FACE_BOXES.get()
    if cache is None or cache.body is not body:
        return boxes
    cell = (
        math.floor(point_mm[0] / GRID_CELL_MM),
        math.floor(point_mm[1] / GRID_CELL_MM),
        math.floor(point_mm[2] / GRID_CELL_MM),
    )
    picked = sorted(set(cache.grid.get(cell, [])) | set(cache.wide))
    return [boxes[i] for i in picked]


def _project_on_face(
    face: adsk.fusion.BRepFace, point_mm: Vec, box: _FaceBox | None = None
) -> tuple[adsk.core.Point3D, float] | None:
    """(projected point, distance mm) of point_mm on the TRIMMED face (body frame, mm), or None.

    None = bbox (+1 mm) misses the point, projection off the trimmed face (evaluator.isParameterOnFace: untrimmed
    surface only), or distance > BREP_SNAP_TOL_MM. A point on a shared boundary edge projects onto both faces.
    box = cached _FaceBox of face (no boundingBox read); None = read it here.
    """
    import adsk.core

    if box is None:
        box = _face_box(face)
    lo = (box.lo[0] - 1, box.lo[1] - 1, box.lo[2] - 1)
    hi = (box.hi[0] + 1, box.hi[1] + 1, box.hi[2] + 1)
    if not all(lo[k] <= point_mm[k] <= hi[k] for k in range(3)):
        return None
    pt = adsk.core.Point3D.create(point_mm[0] / 10.0, point_mm[1] / 10.0, point_mm[2] / 10.0)
    ok, param = face.evaluator.getParameterAtPoint(pt)
    if not ok or not face.evaluator.isParameterOnFace(param):
        return None
    ok, on = face.evaluator.getPointAtParameter(param)
    if not ok:
        return None
    d = math.dist(point_mm, (on.x * 10, on.y * 10, on.z * 10))
    if d > BREP_SNAP_TOL_MM:
        return None
    return on, d


def _snap_face(body: adsk.fusion.BRepBody, point_mm: Vec) -> tuple[_FaceBox, adsk.core.Point3D] | None:
    """(face box, projected point) of the B-rep face nearest point_mm (body frame, mm) among _project_on_face hits, or None."""
    best: tuple[float, _FaceBox, adsk.core.Point3D] | None = None
    for box in _snap_boxes(body, point_mm):
        hit = _project_on_face(box.face, point_mm, box)
        if hit is None:
            continue
        if best is None or hit[1] < best[0]:
            best = (hit[1], box, hit[0])
    if best is None:
        return None
    return best[1], best[2]


def _brep_overhang_deg(body: adsk.fusion.BRepBody, points_mm: list[Vec]) -> float | None:
    """Overhang angle (deg from horizontal, down-facing) of the B-rep face the facet centroid snaps to.

    points_mm[0] = facet centroid: picks the face (nearest _snap_face hit) and gives the angle at its projection.
    The rest = facet vertices: each must lie ON that same face (_project_on_face hit). Their own nearest face is not
    checked: a vertex on a shared boundary edge is on both faces, so it counts. Else None (facet stays overhang):
    a sliver next to a steep chamfer keeps its flag when a vertex lies off that chamfer. ValueError from face_normal
    (degenerate point / apex) propagates; check_body catches it.
    Used by check_body to drop chordal tessellation facets whose true B-rep face is steep enough.
    """
    from fusion_kit.features import face_normal

    hit = _snap_face(body, points_mm[0])
    if hit is None:
        return None
    box, on = hit
    if any(_project_on_face(box.face, p, box) is None for p in points_mm[1:]):
        return None
    n = face_normal(box.face, on)
    ln = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z)
    if ln == 0.0:
        return None
    return math.degrees(math.acos(min(1.0, -n.z / ln)))


def _brep_slit_ceiling(body: adsk.fusion.BRepBody, points_mm: list[Vec]) -> bool:
    """True if the B-rep face the facet centroid snaps to is a down-facing annulus <= SLIT_MAX_MM wide.

    Same snap as _brep_overhang_deg (centroid picks face, all vertices must lie on it). Covers the flat slot ceiling
    that chord-sagged mesh facets cannot prove. ValueError (no normal) propagates.
    """
    from fusion_kit.features import face_normal

    hit = _snap_face(body, points_mm[0])
    if hit is None:
        return False
    box, on = hit
    face = box.face
    if any(_project_on_face(face, p, box) is None for p in points_mm[1:]):
        return False
    if face_normal(face, on).z >= 0.0:
        return False
    width = _face_annulus_width(face)
    if width is not None and width <= SLIT_MAX_MM:
        return True
    return _brep_short_bridge(face) or _brep_short_cantilever(
        face
    )  # bridge <= SHORT_BRIDGE_MAX_MM (P25); cantilever (P26)


def _hull_idx(xy: list[tuple[float, float]]) -> list[int]:
    """Indices of the convex hull vertices of 2D points (monotone chain, collinear points dropped). Max / min of any
    linear projection over the set are attained on the hull, so projecting only these gives the same extremes."""
    order = sorted(range(len(xy)), key=lambda i: xy[i])
    if len(order) <= 2:
        return order

    def turn(o: int, a: int, b: int) -> float:
        return (xy[a][0] - xy[o][0]) * (xy[b][1] - xy[o][1]) - (xy[a][1] - xy[o][1]) * (xy[b][0] - xy[o][0])

    lower: list[int] = []
    for i in order:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], i) <= 0.0:
            lower.pop()
        lower.append(i)
    upper: list[int] = []
    for i in reversed(order):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], i) <= 0.0:
            upper.pop()
        upper.append(i)
    return lower[:-1] + upper[:-1]


def _min_span(points: list[Vec], normal: Vec) -> tuple[float, Vec]:
    """(min width mm, unit in-plane direction of that width) of a point set lying in a plane with normal `normal`:
    min over in-plane directions (every SHORT_BRIDGE_STEP_DEG, 0..180) of max-min of the projection. Pure. Discrete
    directions only overestimate width."""
    n_len = math.sqrt(_dot(normal, normal))
    n = (normal[0] / n_len, normal[1] / n_len, normal[2] / n_len)
    helper: Vec = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = _cross(n, helper)
    e1_len = math.sqrt(_dot(e1, e1))
    e1 = (e1[0] / e1_len, e1[1] / e1_len, e1[2] / e1_len)
    e2 = _cross(n, e1)
    xy = [(_dot(p, e1), _dot(p, e2)) for p in points]
    hull = [
        points[i] for i in _hull_idx(xy)
    ]  # width extremes sit on the hull: same result, O(n log n) + h per direction
    best = math.inf
    best_d: Vec = e1
    for k in range(int(180.0 / SHORT_BRIDGE_STEP_DEG)):
        a = math.radians(k * SHORT_BRIDGE_STEP_DEG)
        d: Vec = (
            math.cos(a) * e1[0] + math.sin(a) * e2[0],
            math.cos(a) * e1[1] + math.sin(a) * e2[1],
            math.cos(a) * e1[2] + math.sin(a) * e2[2],
        )
        proj = [_dot(p, d) for p in hull]
        width = max(proj) - min(proj)
        if width < best:
            best, best_d = width, d
    return best, best_d


def _min_width_mm(points: list[Vec], normal: Vec) -> float:
    """Min width (mm) of a point set in a plane with normal `normal` (see _min_span)."""
    return _min_span(points, normal)[0]


def _edge_samples(e: adsk.fusion.BRepEdge) -> list[Vec] | None:
    """Samples (mm, body frame) of one edge every BRIDGE_SAMPLE_MM via edge.evaluator. None if the evaluator refuses
    a parameter (ok=False). Fusion RuntimeError (e.g. "3 : invalid argument parameter" on some side_wall bed faces)
    -> ValueError: check_body.overhang_ok catches ValueError, so the facet stays flagged instead of crashing."""
    n = max(2, min(400, math.ceil(e.length * 10.0 / BRIDGE_SAMPLE_MM) + 1))
    ok, s, t = e.evaluator.getParameterExtents()
    if not ok:
        return None
    pts: list[Vec] = []
    for k in range(n):
        try:
            ok, p = e.evaluator.getPointAtParameter(s + (t - s) * k / (n - 1))
        except RuntimeError as exc:
            raise ValueError(f"_edge_points_mm: evaluator raised {exc}") from exc
        if not ok:
            return None
        pts.append((p.x * 10.0, p.y * 10.0, p.z * 10.0))
    return pts


def _edge_points_mm(e: adsk.fusion.BRepEdge) -> list[Vec] | None:
    """_edge_samples memoized per edge tempId for the active check_body (short-bridge and cantilever share edges)."""
    cache = _FACE_BOXES.get()
    tid = getattr(e, "tempId", None)
    if cache is None or tid is None:
        return _edge_samples(e)
    if tid not in cache.edge_pts:
        cache.edge_pts[tid] = _edge_samples(e)
    return cache.edge_pts[tid]


def _edge_side(pts: list[Vec], d: Vec, lo: float, hi: float) -> int:
    """-1 if every sample projects on d at the low end of the span (<= lo + tol), +1 if all at the high end, else 0."""
    proj = [_dot(p, d) for p in pts]
    if max(proj) <= lo + BRIDGE_PLANE_TOL_MM:
        return -1
    if min(proj) >= hi - BRIDGE_PLANE_TOL_MM:
        return 1
    return 0


def _neighbour_min_z_mm(g: adsk.fusion.BRepFace) -> float:
    """min Z (mm) of a neighbour face's bbox: check_body cache by tempId (one boundingBox read per face), else read now.
    No cache: reads minPoint.z only, as before the cache existed."""
    cache = _FACE_BOXES.get()
    tid = getattr(g, "tempId", None)
    if cache is None or tid is None:
        return g.boundingBox.minPoint.z * 10.0
    box = cache.by_tid.get(tid)
    if box is None:
        box = _face_box(g)
        cache.by_tid[tid] = box
    return box.lo[2]


def _edge_supported(e: adsk.fusion.BRepEdge, face: adsk.fusion.BRepFace, z_mm: float) -> bool:
    """True if a face other than `face` shares edge e and reaches SUPPORT_DROP_MM below the bridge plane z_mm: material
    under the edge carries the bridge. Open edge or wall rising from the plane = not supported."""
    return any(g.tempId != face.tempId and _neighbour_min_z_mm(g) < z_mm - SUPPORT_DROP_MM for g in e.faces)


@_memo_face_bool("short_bridge")
def _brep_short_bridge(face: adsk.fusion.BRepFace) -> bool:
    """True if B-rep face is a flat down-facing bridge whose shortest in-plane span <= SHORT_BRIDGE_MAX_MM.
    Caller passes only faces whose angle is below min_angle (overhang candidates). Rules:
      - 2 loops: concentric annulus, width = _annulus_width_mm (ring r 6.0/6.1 yes, 6/8 no; off-centre no).
        Support: every edge of both loops is _edge_supported (material under the whole ring).
      - 1 loop: boundary samples must lie on one plane (BRIDGE_PLANE_TOL_MM), width = _min_span.
        Support: the two long sides (edges wholly at the min / max end of the span) each need >= 1 edge, all _edge_supported.
        Free cantilever (open or up-going long sides) = not exempt.
      - 0 or >2 loops, up-facing, non-planar boundary, unreadable evaluator = False (stays overhang).
    ValueError from face_normal propagates (check_body catches it)."""
    from fusion_kit.features import face_normal

    loops = list(face.loops)
    if not loops:
        return False
    if len(loops) == 2:
        width = _face_annulus_width(face)
        if width is None or width > SHORT_BRIDGE_MAX_MM:
            return False
    elif len(loops) != 1:
        return False
    n = face_normal(face)
    normal: Vec = (n.x, n.y, n.z)
    if normal[2] >= 0.0:
        return False
    z_mm = face.pointOnFace.z * 10.0  # planar bridge: any point of the face lies in the bridge plane (cm -> mm)
    if len(loops) == 2:
        return all(_edge_supported(e, face, z_mm) for lp in loops for e in lp.edges)
    edges = list(loops[0].edges)
    per_edge: list[list[Vec]] = []
    for e in edges:
        ep = _edge_points_mm(e)
        if ep is None:
            return False
        per_edge.append(ep)
    pts = [p for ep in per_edge for p in ep]
    if not pts:
        return False
    p0 = pts[0]
    if any(abs(_dot(_sub(p, p0), normal)) > BRIDGE_PLANE_TOL_MM for p in pts):
        return False
    width, d = _min_span(pts, normal)
    if width > SHORT_BRIDGE_MAX_MM:
        return False
    proj = [_dot(p, d) for p in pts]
    lo, hi = min(proj), max(proj)
    sides: dict[int, list[adsk.fusion.BRepEdge]] = {-1: [], 1: []}
    for e, ep in zip(edges, per_edge):
        side = _edge_side(ep, d, lo, hi)
        if side != 0:
            sides[side].append(e)
    for s in (-1, 1):  # both long sides must rest on material
        if not sides[s] or not all(_edge_supported(e, face, z_mm) for e in sides[s]):
            return False
    return True


def _line_dist_mm(p: Vec, a: Vec, u: Vec) -> float:
    """Distance (mm) of point p from the line a + t*u (u unit)."""
    v = _sub(p, a)
    t = _dot(v, u)
    perp: Vec = (v[0] - t * u[0], v[1] - t * u[1], v[2] - t * u[2])
    return math.sqrt(_dot(perp, perp))


@_memo_face_bool("short_cantilever")
def _brep_short_cantilever(face: adsk.fusion.BRepFace) -> bool:
    """True if B-rep face is a flat down-facing 1-loop cantilever with reach <= CANTILEVER_MAX_MM from ONE supported edge.
    Reach = max distance of every boundary point from the line through that edge's sampled endpoints. Exempt if at
    least one _edge_supported edge gives reach <= CANTILEVER_MAX_MM (+EPS_MM). Rules:
      - no supported edge, 0 or >1 loops, up-facing, non-planar boundary = False (stays overhang).
      - supported edge with degenerate chord (closed / point edge) gives no line = skipped.
    ValueError from face_normal / edge evaluator propagates (check_body catches it)."""
    from fusion_kit.features import face_normal

    loops = list(face.loops)
    if len(loops) != 1:
        return False
    n = face_normal(face)
    normal: Vec = (n.x, n.y, n.z)
    if normal[2] >= 0.0:
        return False
    z_mm = face.pointOnFace.z * 10.0  # planar bridge: any point of the face lies in the bridge plane (cm -> mm)
    edges = list(loops[0].edges)
    supported = [_edge_supported(e, face, z_mm) for e in edges]
    if not any(supported):
        return False
    samples: list[list[Vec]] = []
    for e in edges:
        ep = _edge_points_mm(e)
        if ep is None:
            return False
        samples.append(ep)
    pts = [p for ep in samples for p in ep]
    if not pts:
        return False
    p0 = pts[0]
    if any(abs(_dot(_sub(p, p0), normal)) > BRIDGE_PLANE_TOL_MM for p in pts):
        return False
    for ep, sup in zip(samples, supported):
        if not sup:
            continue
        a = ep[0]
        chord = _sub(ep[-1], a)
        clen = math.sqrt(_dot(chord, chord))
        if clen < BRIDGE_SAMPLE_MM:
            continue  # closed or point edge: no line to measure from
        u: Vec = (chord[0] / clen, chord[1] / clen, chord[2] / clen)
        reach = max(_line_dist_mm(p, a, u) for p in pts)
        if reach <= CANTILEVER_MAX_MM + EPS_MM:
            return True
    return False


def check_body(
    body: adsk.fusion.BRepBody,
    up_rot: Rot = IDENTITY,
    min_angle_deg: float = 45.0,
    bed_tol_mm: float = 0.05,
    bridge_ok_mm: float = 0.0,
    min_bed_mm2: float = 25.0,
    quality: adsk.fusion.TriangleMeshQualityOptions | None = None,
    angle_tol_deg: float = 0.05,
    fold_min_deg: float = 2.0,
) -> PrintReport:
    """mesh_from_body -> rotate by up_rot -> check(). Frame = PRINT frame: bed face (dfam/bed, '-z' default) on min Z
    via print_frame_body; up_rot is applied on top of it. Groove rects (warp_family_mm) exempt only when up_rot is
    identity (rects are design-frame). Groove roofs (geometric, rotated frame) and teardrop notch tops
    (NOTCH_BRIDGE_MAX_MM, flanked) are exempt in any orientation. Short ledges (SHORT_LEDGE_MAX_MM) and bed-adjacent
    chamfer faces (BED_ADJACENT_MAX_MM, slope >= BED_ADJACENT_MIN_ANGLE_DEG) too. Same bed-at-z=0 assumption as check().

    Slot ceiling <= SLIT_MAX_MM (0.2 mm) between facing walls is not overhang (slicer closes it);
    B-rep flat annulus (2 concentric loops) of width <= SLIT_MAX_MM exempt (`_brep_slit_ceiling`).
    Flat down-facing B-rep bridge (1 planar loop, or annulus) with min in-plane width <=
    SHORT_BRIDGE_MAX_MM (1.0 mm) exempt ONLY if supported: both long sides (edges at both ends of
    the min-width span) border a face going below the bridge plane (bbox minZ < bridge z - 0.1 mm);
    annulus: every edge of both loops supported. Cantilever (unsupported) stays flagged
    (`_brep_short_bridge`; user P25 2026-11-17). Short cantilever (P26): planar down-facing 1-loop
    face with reach <= 1.0 mm (`CANTILEVER_MAX_MM`) from one supported edge's line exempt
    (`_brep_short_cantilever`).
    """
    pbody = print_frame_body(body)
    mesh = mesh_from_body(pbody, quality)
    ident = _is_identity(up_rot)
    grooves = warp_family_mm(pbody)
    rects = grooves if ident else None

    def overhang_ok(t: Tri) -> bool:
        # tessellated facet below min angle but true B-rep face at >= min angle: chordal error, not overhang.
        # Flat slot ceiling <= SLIT_MAX_MM wide (B-rep annulus): mesh sag cannot prove it, B-rep does (_brep_slit_ceiling).
        # Centroid AND all 3 vertices must snap to one such face; ValueError (no normal) keeps the facet flagged.
        pts = [_centroid(mesh.verts, t)] + [mesh.verts[i] for i in t]
        try:
            angle = _brep_overhang_deg(pbody, pts)
            if angle is not None and angle >= min_angle_deg:
                return True
            return _brep_slit_ceiling(pbody, pts)
        except ValueError:
            return False

    # face bboxes read at most once per face for this call (filled on first snap; overhang_ok runs per facet + vertex)
    cache_token = _FACE_BOXES.set(_GeomCache(pbody)) if ident else None
    try:
        return check(
            rotate_mesh(mesh, up_rot),
            min_angle_deg=min_angle_deg,
            bed_tol_mm=bed_tol_mm,
            bridge_ok_mm=bridge_ok_mm,
            min_bed_mm2=min_bed_mm2,
            angle_tol_deg=angle_tol_deg,
            fold_min_deg=fold_min_deg,
            exempt_rects=rects,
            overhang_ok=overhang_ok if ident else None,
            groove_roof=bool(grooves),
            notch_bridge=True,
            short_ledge=True,
            bed_adjacent=True,
        )
    finally:
        if cache_token is not None:
            _FACE_BOXES.reset(cache_token)


def wall_thickness_mm(
    body: adsk.fusion.BRepBody | None,
    point_mm: Vec,
    direction: Vec,
    max_mm: float = 50.0,
    resolution_mm: float = 0.05,
) -> float:
    """Solid run (mm) from point_mm along direction through body; body frame, same as mesh_from_body.

    point_mm should lie on a face, direction should point into material. Wraps march_thickness with
    BRepBody.pointContainment (cm; mm -> cm here). INSIDE = material; ON / OUTSIDE = exit.
    Step resolution = resolution_mm (default 0.05 mm); result error <= resolution_mm / 2**10.
    Cost ~ max_mm / resolution_mm + 10 pointContainment calls. Errors: see march_thickness.
    Raises ValueError if body is None.
    """
    if body is None:
        raise ValueError("wall_thickness_mm: body is None")
    import adsk.core

    from fusion_kit import enums

    def is_inside(p: Vec) -> bool:
        pt = adsk.core.Point3D.create(p[0] / 10.0, p[1] / 10.0, p[2] / 10.0)
        return body.pointContainment(pt) == enums.POINT_INSIDE

    return march_thickness(is_inside, point_mm, direction, max_mm, resolution_mm)

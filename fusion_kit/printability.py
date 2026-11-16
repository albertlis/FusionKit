"""FDM printability checker: pure mesh core + Fusion adapter.

Pure core (Mesh, rot_x/rot_y/rot_z, compose, rotate_mesh, check, sweep, clear_rays, march_thickness) imports no adsk,
so it runs offline. Adapter (mesh_from_body, check_body) imports fusion_kit.enums (adsk)
inside the function body only. Units: mm. Up = +Z after rotation. Mesh winding: outward.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from fusion_kit._vec import _cross, _dot, _sub
from fusion_kit.dfam import GrooveRect, in_groove_rect, warp_family_mm

if TYPE_CHECKING:
    import adsk.core
    import adsk.fusion

Vec = tuple[float, float, float]
Rot = tuple[Vec, Vec, Vec]
Tri = tuple[int, int, int]
BedKind = Literal["area", "edge", "point", "none"]

EPS_MM = 1e-6  # strict "above" margin for hanging-edge and floating-vertex tests
DOWN_EPS = 1e-6  # unit normal n_z below -DOWN_EPS = triangle faces downward (underside)
UP_EPS = 1e-6  # unit normal n_z above +UP_EPS = triangle faces upward (top); |n_z| <= UP_EPS = vertical, neutral
DEGEN_MM = 1e-12  # |cross product| below this = degenerate triangle / zero-length edge
POINT_SPAN_MM = 0.5  # on-bed vertices spanning less than this = point contact
TOP_N = 20
BREP_SNAP_TOL_MM = 0.1  # B-rep projection farther than this from a facet point (centroid or vertex) = not the same surface (chordal tol)
IDENTITY: Rot = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


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
def _tri_in_groove(verts: list[Vec], t: Tri, grooves: list[tuple[int, GrooveRect]]) -> bool:
    """All 3 vertices inside ONE qualifying groove rect (in_groove_rect, bed z = 0). Triangle bbox never exempts."""
    return any(all(in_groove_rect(verts[i], r, 0.0) for i in t) for _ax, r in grooves)


def _is_identity(rot: Rot) -> bool:
    return all(abs(rot[i][j] - IDENTITY[i][j]) <= 1e-9 for i in range(3) for j in range(3))


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
) -> PrintReport:
    """Printability metrics for a mesh already rotated so print-up = +Z (min z = 0).

    exempt_rects (default None = no exemption): qualifying warp-groove rects (dfam.warp_family_mm) in THIS mesh
    frame. A down-facing triangle with all 3 vertices inside one rect (z <= 0.5 mm) is not overhang. Rects are
    design frame: pass them only for the identity orientation (sweep / check_body do this).

    Overhang: face fails if theta < min_angle_deg - angle_tol_deg. Hanging edge: slope < min_angle_deg
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
    """
    verts = mesh.verts
    tris = mesh.tris
    geoms = [_tri_geom(verts, t) for t in tris]

    def is_bed(t: Tri) -> bool:
        return all(verts[i][2] <= bed_tol_mm for i in t)

    # 1. overhang faces: face tilted theta from horizontal, facing down (n_z = -cos theta).
    #    Fails when theta < min_angle_deg  <=>  n_z < -cos(min_angle_deg).
    overhangs: list[tuple[float, float, Vec]] = []
    for t, g in zip(tris, geoms):
        if g is None or is_bed(t):
            continue
        n, area = g
        if n[2] >= 0.0:
            continue
        if exempt_rects and _tri_in_groove(verts, t, exempt_rects):
            continue
        theta = math.degrees(math.acos(min(1.0, -n[2])))
        if theta < min_angle_deg - angle_tol_deg and not (overhang_ok is not None and overhang_ok(t)):
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


def _project_on_face(face: adsk.fusion.BRepFace, point_mm: Vec) -> tuple[adsk.core.Point3D, float] | None:
    """(projected point, distance mm) of point_mm on the TRIMMED face (body frame, mm), or None.

    None = bbox (+1 mm) misses the point, projection off the trimmed face (evaluator.isParameterOnFace: untrimmed
    surface only), or distance > BREP_SNAP_TOL_MM. A point on a shared boundary edge projects onto both faces.
    """
    import adsk.core

    bb = face.boundingBox
    lo = (bb.minPoint.x * 10 - 1, bb.minPoint.y * 10 - 1, bb.minPoint.z * 10 - 1)
    hi = (bb.maxPoint.x * 10 + 1, bb.maxPoint.y * 10 + 1, bb.maxPoint.z * 10 + 1)
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


def _snap_face(body: adsk.fusion.BRepBody, point_mm: Vec) -> tuple[adsk.fusion.BRepFace, adsk.core.Point3D] | None:
    """(face, projected point) of the B-rep face nearest point_mm (body frame, mm) among _project_on_face hits, or None."""
    best: tuple[float, adsk.fusion.BRepFace, adsk.core.Point3D] | None = None
    for f in body.faces:
        hit = _project_on_face(f, point_mm)
        if hit is None:
            continue
        if best is None or hit[1] < best[0]:
            best = (hit[1], f, hit[0])
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
    face, on = hit
    if any(_project_on_face(face, p) is None for p in points_mm[1:]):
        return None
    n = face_normal(face, on)
    ln = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z)
    if ln == 0.0:
        return None
    return math.degrees(math.acos(min(1.0, -n.z / ln)))


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
    """mesh_from_body -> rotate by up_rot -> check(). Groove rects (warp_family_mm) exempt only when up_rot is
    identity (rects are body frame). Same bed-at-z=0 assumption as check()."""
    mesh = mesh_from_body(body, quality)
    ident = _is_identity(up_rot)
    rects = warp_family_mm(body) if ident else None

    def overhang_ok(t: Tri) -> bool:
        # tessellated facet below min angle but true B-rep face at >= min angle: chordal error, not overhang.
        # Centroid AND all 3 vertices must snap to one such face; ValueError (no normal) keeps the facet flagged.
        pts = [_centroid(mesh.verts, t)] + [mesh.verts[i] for i in t]
        try:
            angle = _brep_overhang_deg(body, pts)
        except ValueError:
            return False
        return angle is not None and angle >= min_angle_deg

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
    )


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

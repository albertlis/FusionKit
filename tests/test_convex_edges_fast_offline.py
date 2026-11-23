"""Offline tests: convex_edges topology path (planar pairs, zero pointContainment) == containment probe.

Bodies: right prisms over a CCW polygon (cm), z 0..h. Faces: planar geometry, outward evaluator normal, loops with
coEdges in BRep convention (face on the left of each coedge seen from the outward normal). pointContainment is
exact for the prism, so the probe path is the ground truth. Expected counts are derived by hand from the polygon.

Fake shape mirrors the documented API (adsk.fusion stubs): collections are read as count + item(i);
edge.coEdges -> coedge.loop.face; face.loops -> loop.coEdges -> coedge.edge / isOpposedToEdge.
"""

import math
from types import SimpleNamespace
from typing import Any

from fusion_kit import enums, features
from fusion_kit._vec import _cross, _unit_strict
from fusion_kit.features import convex_edges

Vec = tuple[float, float, float]


def _v(x: float, y: float, z: float) -> Any:
    return SimpleNamespace(x=x, y=y, z=z)


def _tuple_of(v: Any) -> Vec:
    return (v.x, v.y, v.z)


class _Coll(list):
    """Fake BRep collection: count property + item(index), like adsk.fusion BRepCoEdges / BRepLoops / BRepEdges."""

    @property
    def count(self) -> int:
        return len(self)

    def item(self, index: int) -> Any:
        return self[index]


def _on_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> bool:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)) <= 1e-9


def _ray_inside(poly: list[tuple[float, float]], x: float, y: float) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _classify(poly: list[tuple[float, float]], h: float, c: Vec) -> Any:
    x, y, z = c
    if z < -1e-9 or z > h + 1e-9:
        return enums.POINT_OUTSIDE
    n = len(poly)
    on_edge = any(_on_segment((x, y), poly[i], poly[(i + 1) % n]) for i in range(n))
    if not (on_edge or _ray_inside(poly, x, y)):
        return enums.POINT_OUTSIDE
    if on_edge or abs(z) <= 1e-9 or abs(z - h) <= 1e-9:
        return enums.POINT_ON
    return enums.POINT_INSIDE


class _Prism:
    """Right prism over poly (CCW, cm), height h. Counts pointContainment calls."""

    def __init__(self, poly: list[tuple[float, float]], h: float, *, coedges: bool) -> None:
        self.calls = 0
        self._poly = poly
        self._h = h
        self.faces: list[Any] = []
        self.edges: list[Any] = []
        self._build(coedges)

    def _build(self, coedges: bool) -> None:
        poly, h = self._poly, self._h
        n = len(poly)
        normals: dict[str, Vec] = {"top": (0.0, 0.0, 1.0), "bottom": (0.0, 0.0, -1.0)}
        lefts: list[Vec] = []
        dirs: list[Vec] = []
        for i in range(n):
            (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
            d = _unit_strict((x1 - x0, y1 - y0, 0.0))
            dirs.append(d)
            lefts.append((-d[1], d[0], 0.0))  # interior of a CCW polygon is on the left of each edge
            normals[f"side{i}"] = (d[1], -d[0], 0.0)  # outward = right of CCW edge
        into: dict[tuple[str, int], Vec] = {}
        touching: dict[str, list[Any]] = {key: [] for key in normals}

        def add(a: Vec, b: Vec, owners: list[tuple[str, Vec]]) -> None:
            eid = len(self.edges) + 1
            mid = tuple((p + q) / 2 for p, q in zip(a, b))
            edge = SimpleNamespace(
                tempId=eid,
                startVertex=SimpleNamespace(geometry=_v(*a)),
                endVertex=SimpleNamespace(geometry=_v(*b)),
                pointOnEdge=_v(*mid),
                faces=[],
                coEdges=_Coll(),
            )
            self.edges.append(edge)
            for key, u in owners:
                into[(key, eid)] = u
                touching[key].append(edge)

        for i in range(n):
            (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
            add((x0, y0, h), (x1, y1, h), [("top", lefts[i]), (f"side{i}", (0.0, 0.0, -1.0))])
            add((x0, y0, 0.0), (x1, y1, 0.0), [("bottom", lefts[i]), (f"side{i}", (0.0, 0.0, 1.0))])
            prev = (i - 1) % n
            add(
                (x0, y0, 0.0),
                (x0, y0, h),
                [(f"side{i}", dirs[i]), (f"side{prev}", (-dirs[prev][0], -dirs[prev][1], 0.0))],
            )
        for key, normal in normals.items():
            owned = touching[key]
            coedge_list: list[Any] = []
            for e in owned:
                if coedges:
                    u = into[(key, e.tempId)]
                    t = _cross(u, normal)  # n x t = u  <=>  t = u x n
                    a = _tuple_of(e.startVertex.geometry)
                    b = _tuple_of(e.endVertex.geometry)
                    opposed = sum((b[k] - a[k]) * t[k] for k in range(3)) < 0
                    coedge_list.append(SimpleNamespace(edge=e, isOpposedToEdge=opposed))
            loop = SimpleNamespace(isOuter=True, edges=_Coll(owned), coEdges=_Coll(coedge_list), face=None)
            for co in coedge_list:
                co.loop = loop
                co.edge.coEdges.append(co)
            face = SimpleNamespace(
                tempId=len(self.faces) + 1,
                geometry=SimpleNamespace(normal=_v(*normal)),
                evaluator=SimpleNamespace(getNormalAtPoint=lambda _p, nn=normal: (True, _v(*nn))),
                loops=_Coll([loop]),
                edges=owned,
            )
            loop.face = face
            self.faces.append(face)
            for e in owned:
                e.faces.append(face)

    def pointContainment(self, p: Any) -> Any:
        self.calls += 1
        return _classify(self._poly, self._h, (p.x, p.y, p.z))


SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
L_PROFILE = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0)]  # reflex vertex (1,1)
GROOVE = [  # 3 x 1 strip with 1 x 0.5 notch in the top: reflex vertices (2,0.5) and (1,0.5)
    (0.0, 0.0),
    (3.0, 0.0),
    (3.0, 1.0),
    (2.0, 1.0),
    (2.0, 0.5),
    (1.0, 0.5),
    (1.0, 1.0),
    (0.0, 1.0),
]


def _reference(body: _Prism, edges: list[Any]) -> list[Any]:
    """Containment-only reference: same normals and parallel skip as convex_edges, probe for every edge."""
    out = []
    for e in edges:
        fa, fb = e.faces
        mid = e.pointOnEdge
        na = _unit_strict(features._xyz(features.face_normal(fa, mid)))
        nb = _unit_strict(features._xyz(features.face_normal(fb, mid)))
        if math.sqrt(sum(c * c for c in _cross(na, nb))) < 1e-6:
            continue
        if features._probe_convex(body, mid, na, nb, 0.01):
            out.append(e)
    return out


def _vertical_at(body: _Prism, x: float, y: float) -> list[Any]:
    return [
        e
        for e in body.edges
        if abs(e.startVertex.geometry.x - x) < 1e-9
        and abs(e.startVertex.geometry.y - y) < 1e-9
        and abs(e.endVertex.geometry.z - e.startVertex.geometry.z) > 0.5
    ]


def _stats_delta(before: dict[str, int]) -> dict[str, int]:
    return {k: features.CONVEX_STATS[k] - before.get(k, 0) for k in ("topo", "probe_fallback")}


def test_square_prism_topo_matches_probe_no_containment(point3d_xyz: None) -> None:
    body = _Prism(SQUARE, 1.0, coedges=True)
    got = convex_edges(body, body.edges)
    assert body.calls == 0
    assert len(got) == 12
    assert got == _reference(body, body.edges)


def test_l_prism_concave_reflex_vertical_excluded(point3d_xyz: None) -> None:
    body = _Prism(L_PROFILE, 1.0, coedges=True)
    got = convex_edges(body, body.edges)
    assert body.calls == 0
    assert len(got) == 17  # 6 top + 6 bottom + 5 vertical; reflex (1,1) vertical is concave
    assert not any(e in got for e in _vertical_at(body, 1.0, 1.0))
    assert got == _reference(body, body.edges)


def test_groove_strip_both_notch_bottoms_concave(point3d_xyz: None) -> None:
    body = _Prism(GROOVE, 1.0, coedges=True)
    got = convex_edges(body, body.edges)
    assert body.calls == 0
    assert len(got) == 22  # 8 top + 8 bottom + 6 vertical; reflex (2,0.5) and (1,0.5) excluded
    for x, y in ((2.0, 0.5), (1.0, 0.5)):
        assert not any(e in got for e in _vertical_at(body, x, y))
    assert got == _reference(body, body.edges)


def test_reversed_face_order_same_result(point3d_xyz: None) -> None:
    body = _Prism(L_PROFILE, 1.0, coedges=True)
    for e in body.edges:
        e.faces.reverse()
    got = convex_edges(body, body.edges)
    assert body.calls == 0
    assert len(got) == 17


def test_no_coedge_info_falls_back_to_probe(point3d_xyz: None) -> None:
    body = _Prism(L_PROFILE, 1.0, coedges=False)
    got = convex_edges(body, body.edges)
    assert body.calls > 0  # no coedges on any edge or loop -> containment path actually used
    assert len(got) == 17
    assert got == _reference(body, body.edges)


def test_topo_path_counted_in_stats(point3d_xyz: None) -> None:
    body = _Prism(L_PROFILE, 1.0, coedges=True)
    before = dict(features.CONVEX_STATS)
    convex_edges(body, body.edges)
    assert _stats_delta(before) == {"topo": 18, "probe_fallback": 0}  # L prism: 6 top + 6 bottom + 6 vertical


def test_missing_coedges_counted_as_probe_fallback(point3d_xyz: None) -> None:
    body = _Prism(L_PROFILE, 1.0, coedges=False)
    before = dict(features.CONVEX_STATS)
    got = convex_edges(body, body.edges)
    assert _stats_delta(before) == {"topo": 0, "probe_fallback": 18}
    assert len(got) == 17


def test_face_loop_lookup_when_edge_coedges_empty(point3d_xyz: None) -> None:
    body = _Prism(L_PROFILE, 1.0, coedges=True)
    for e in body.edges:
        e.coEdges.clear()  # force the second lookup: face.loops -> loop.coEdges -> coedge.edge
    before = dict(features.CONVEX_STATS)
    got = convex_edges(body, body.edges)
    assert body.calls == 0
    assert _stats_delta(before) == {"topo": 18, "probe_fallback": 0}
    assert len(got) == 17

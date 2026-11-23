"""Offline tests for the printability per-check_body B-rep cache, fast paths and XY bucket indexes.

Covers: _min_span (convex hull) equivalence vs the direction-loop reference; snap grid equivalence vs full face scan;
edge-support neighbour bbox reads bounded by faces (not edges x faces); per-tempId memo of edge samples and face
predicates; slit-ceiling and groove bucket indexes equal to the full scan. No Fusion runtime: adsk stubs from conftest.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterator
from contextvars import Token
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fusion_kit import printability as pb

if TYPE_CHECKING:
    import adsk.core
    import adsk.fusion

Vec = tuple[float, float, float]
Box2 = tuple[tuple[float, float], tuple[float, float]]


def _face(x: object) -> adsk.fusion.BRepFace:
    """Test fake -> B-rep face type for pb signatures (runtime no-op)."""
    return cast("adsk.fusion.BRepFace", x)


def _body(x: object) -> adsk.fusion.BRepBody:
    return cast("adsk.fusion.BRepBody", x)


def _edge(x: object) -> adsk.fusion.BRepEdge:
    return cast("adsk.fusion.BRepEdge", x)


def _point(x: float, y: float, z: float) -> adsk.core.Point3D:
    return cast("adsk.core.Point3D", SimpleNamespace(x=x, y=y, z=z))


def _all_wide(n: int) -> pb._Buckets:
    """Index with every item in `wide`: query returns all ids, i.e. the plain full scan."""
    boxes: list[Box2 | None] = [None] * n
    return pb._Buckets(boxes)


@pytest.fixture
def geom_cache() -> Iterator[Callable[[object], None]]:
    """Activate a fresh _GeomCache for body; reset on teardown. Call with body."""
    tokens: list[Token[pb._GeomCache | None]] = []

    def activate(body: object) -> None:
        tokens.append(pb._FACE_BOXES.set(pb._GeomCache(_body(body))))

    yield activate
    while tokens:
        pb._FACE_BOXES.reset(tokens.pop())


def _ref_min_span(points: list[Vec], normal: Vec) -> tuple[float, Vec]:
    """Reference: the pre-hull direction loop, every point projected at every direction."""
    n_len = math.sqrt(pb._dot(normal, normal))
    n = (normal[0] / n_len, normal[1] / n_len, normal[2] / n_len)
    helper: Vec = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = pb._cross(n, helper)
    e1_len = math.sqrt(pb._dot(e1, e1))
    e1 = (e1[0] / e1_len, e1[1] / e1_len, e1[2] / e1_len)
    e2 = pb._cross(n, e1)
    best = math.inf
    best_d: Vec = e1
    for k in range(int(180.0 / pb.SHORT_BRIDGE_STEP_DEG)):
        a = math.radians(k * pb.SHORT_BRIDGE_STEP_DEG)
        d: Vec = (
            math.cos(a) * e1[0] + math.sin(a) * e2[0],
            math.cos(a) * e1[1] + math.sin(a) * e2[1],
            math.cos(a) * e1[2] + math.sin(a) * e2[2],
        )
        proj = [pb._dot(p, d) for p in points]
        width = max(proj) - min(proj)
        if width < best:
            best, best_d = width, d
    return best, best_d


def _unit(v: Vec) -> Vec:
    ln = math.sqrt(pb._dot(v, v))
    return (v[0] / ln, v[1] / ln, v[2] / ln)


def _rand_vec(rng: random.Random, lo: float, hi: float) -> Vec:
    return (rng.uniform(lo, hi), rng.uniform(lo, hi), rng.uniform(lo, hi))


@pytest.mark.parametrize("seed", range(60))
def test_min_span_matches_reference(seed: int) -> None:
    rng = random.Random(seed)
    normal = _unit((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))
    helper: Vec = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _unit(pb._cross(normal, helper))
    v = pb._cross(normal, u)
    count = rng.randint(1, 80)
    pts: list[Vec] = []
    for _ in range(count):
        a = rng.uniform(-10.0, 10.0)
        b = rng.uniform(-10.0, 10.0)
        if seed % 3 == 0:  # integer grid: duplicates and collinear runs
            a, b = round(a), round(b)
        c = rng.uniform(-0.01, 0.01) if seed % 2 else 0.0  # off-plane noise on half the seeds
        pts.append(
            (
                a * u[0] + b * v[0] + c * normal[0],
                a * u[1] + b * v[1] + c * normal[1],
                a * u[2] + b * v[2] + c * normal[2],
            )
        )
    width, d = pb._min_span(pts, normal)
    ref_width, _ref_d = _ref_min_span(pts, normal)
    assert width == pytest.approx(ref_width, rel=1e-9, abs=1e-9)
    proj = [pb._dot(p, d) for p in pts]
    assert max(proj) - min(proj) == pytest.approx(width, rel=1e-9, abs=1e-9)


def test_min_span_empty_raises_like_reference() -> None:
    with pytest.raises(ValueError):
        pb._min_span([], (0.0, 0.0, 1.0))
    with pytest.raises(ValueError):
        _ref_min_span([], (0.0, 0.0, 1.0))


class _Face:
    """Fake B-rep face: tempId + bbox (mm, stored as cm in the Fusion-shaped boundingBox). Reads are counted."""

    def __init__(self, tid: int, lo_mm: Vec, hi_mm: Vec, counts: list[int] | None = None) -> None:
        self.tempId = tid
        self._lo = lo_mm
        self._hi = hi_mm
        self._counts = counts

    @property
    def boundingBox(self) -> SimpleNamespace:
        if self._counts is not None:
            self._counts[0] += 1
        lo, hi = self._lo, self._hi
        return SimpleNamespace(
            minPoint=SimpleNamespace(x=lo[0] / 10.0, y=lo[1] / 10.0, z=lo[2] / 10.0),
            maxPoint=SimpleNamespace(x=hi[0] / 10.0, y=hi[1] / 10.0, z=hi[2] / 10.0),
        )


def _fake_project(face: _Face, point_mm: Vec, box: pb._FaceBox | None = None) -> tuple[adsk.core.Point3D, float] | None:
    """Stand-in for _project_on_face: same 1 mm bbox test, distance = deterministic function of point and face."""
    if box is None:
        box = pb._face_box(_face(face))
    lo = [box.lo[k] - 1.0 for k in range(3)]
    hi = [box.hi[k] + 1.0 for k in range(3)]
    if not all(lo[k] <= point_mm[k] <= hi[k] for k in range(3)):
        return None
    return _point(float(face.tempId), 0.0, 0.0), math.dist(point_mm, box.lo)


def _snap_key(res: tuple[pb._FaceBox, adsk.core.Point3D] | None) -> tuple[int, float] | None:
    if res is None:
        return None
    box, point = res
    return box.face.tempId, point.x


def test_snap_grid_matches_full_scan(monkeypatch: pytest.MonkeyPatch, geom_cache: Callable[[object], None]) -> None:
    monkeypatch.setattr(pb, "_project_on_face", _fake_project)
    rng = random.Random(7)
    faces: list[_Face] = []
    for i in range(120):
        centre = [rng.uniform(-60.0, 60.0) for _ in range(3)]
        size = [rng.choice([0.0, 1.0, 5.0, 20.0, 80.0, 300.0]) for _ in range(3)]  # 300 mm -> wide path
        lo = (centre[0] - size[0] / 2.0, centre[1] - size[1] / 2.0, centre[2] - size[2] / 2.0)
        hi = (centre[0] + size[0] / 2.0, centre[1] + size[1] / 2.0, centre[2] + size[2] / 2.0)
        faces.append(_Face(i, lo, hi))
    body = SimpleNamespace(faces=faces)
    points: list[Vec] = [_rand_vec(rng, -80.0, 80.0) for _ in range(500)]

    ref = [_snap_key(pb._snap_face(_body(body), p)) for p in points]
    geom_cache(body)
    got = [_snap_key(pb._snap_face(_body(body), p)) for p in points]
    assert any(k is not None for k in ref)
    assert got == ref


def test_edge_support_reads_each_neighbour_box_once(geom_cache: Callable[[object], None]) -> None:
    counts = [0]
    faces = [_Face(i, (0.0, 0.0, float(i)), (10.0, 10.0, float(i) + 1.0), counts) for i in range(10)]
    body = SimpleNamespace(faces=faces)
    edges = [SimpleNamespace(tempId=100 + k, faces=[faces[0], faces[k]]) for k in range(1, 10)]
    geom_cache(body)
    pb._face_boxes(_body(body))
    assert counts[0] == len(faces)
    for _ in range(5):
        for e in edges:
            pb._edge_supported(_edge(e), _face(faces[0]), 5.0)
    assert counts[0] == len(faces)  # 45 edge checks, still one read per face


def test_edge_samples_memoized_per_check_body(
    monkeypatch: pytest.MonkeyPatch, geom_cache: Callable[[object], None]
) -> None:
    calls = [0]

    def fake_samples(e: object) -> list[Vec]:
        calls[0] += 1
        return [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]

    monkeypatch.setattr(pb, "_edge_samples", fake_samples)
    e = SimpleNamespace(tempId=5)
    geom_cache(SimpleNamespace(faces=[]))
    for _ in range(3):
        assert pb._edge_points_mm(_edge(e)) == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
    assert calls[0] == 1
    pb._FACE_BOXES.set(None)
    pb._edge_points_mm(_edge(e))
    pb._edge_points_mm(_edge(e))
    assert calls[0] == 3  # no cache -> no memo


def test_face_predicate_memoized_per_tempId(geom_cache: Callable[[object], None]) -> None:
    calls = [0]

    @pb._memo_face_bool("probe")
    def probe(face: object) -> bool:
        calls[0] += 1
        return True

    face = SimpleNamespace(tempId=9)
    assert probe(_face(face)) is True  # no cache: call through
    assert calls[0] == 1
    geom_cache(SimpleNamespace(faces=[]))
    assert probe(_face(face)) is True
    assert probe(_face(face)) is True
    assert calls[0] == 2  # one more call inside the cache, then memo hit


def _mesh_from_triangles(triangles: list[tuple[Vec, Vec, Vec]]) -> pb.Mesh:
    verts: list[Vec] = []
    tris: list[pb.Tri] = []
    for a, b, c in triangles:
        base = len(verts)
        verts.extend((a, b, c))
        tris.append((base, base + 1, base + 2))
    return pb.Mesh(verts=verts, tris=tris)


def _random_mesh(seed: int) -> pb.Mesh:
    rng = random.Random(seed)
    tris = [(_rand_vec_xy_z(rng), _rand_vec_xy_z(rng), _rand_vec_xy_z(rng)) for _ in range(120)]
    return _mesh_from_triangles(tris)


def _rand_vec_xy_z(rng: random.Random) -> Vec:
    return (rng.uniform(-20.0, 20.0), rng.uniform(-20.0, 20.0), rng.uniform(0.0, 12.0))


@pytest.mark.parametrize("seed", range(20))
def test_slit_index_matches_full_scan(seed: int) -> None:
    mesh = _random_mesh(seed)
    geoms = [pb._tri_geom(mesh.verts, t) for t in mesh.tris]
    indexed = pb._slit_walls(mesh.verts, mesh.tris, geoms)
    full = pb._SlitWalls(walls=indexed.walls, index=_all_wide(len(indexed.walls)))
    assert indexed.walls, "random mesh must contain wall-like faces"
    for t, g in zip(mesh.tris, geoms):
        if g is None:
            continue
        assert pb._slit_ceiling(mesh.verts, indexed, t) == pb._slit_ceiling(mesh.verts, full, t)


def test_slit_index_slot_ceiling_found_and_far_ceiling_not() -> None:
    """Two facing walls 0.1 mm apart (SLIT_MAX_MM 0.2) with a down-facing ceiling between them: True through the index,
    same as full scan. Same ceiling moved 10 mm away from the walls: False."""

    def walls_at(x: float) -> list[tuple[Vec, Vec, Vec]]:
        p0, p1, p2, p3 = (x, -5.0, 0.0), (x, 5.0, 0.0), (x, 5.0, 10.0), (x, -5.0, 10.0)
        return [(p0, p1, p2), (p0, p2, p3)]

    left = walls_at(0.0)
    right = [(c, b, a) for a, b, c in walls_at(0.1)]  # reversed winding: normal faces -x
    ceiling_near: tuple[Vec, Vec, Vec] = ((0.0, -5.0, 10.0), (0.1, 5.0, 10.0), (0.1, -5.0, 10.0))

    def shift(p: Vec) -> Vec:
        return (p[0] + 10.0, p[1], p[2])

    ceiling_far = (shift(ceiling_near[0]), shift(ceiling_near[1]), shift(ceiling_near[2]))
    for ceiling, expect in ((ceiling_near, True), (ceiling_far, False)):
        mesh = _mesh_from_triangles(left + right + [ceiling])
        geoms = [pb._tri_geom(mesh.verts, t) for t in mesh.tris]
        indexed = pb._slit_walls(mesh.verts, mesh.tris, geoms)
        full = pb._SlitWalls(walls=indexed.walls, index=_all_wide(len(indexed.walls)))
        t_ceiling = mesh.tris[-1]
        got = pb._slit_ceiling(mesh.verts, indexed, t_ceiling)
        assert got == pb._slit_ceiling(mesh.verts, full, t_ceiling)
        assert got is expect


def _in_rect_vec(rng: random.Random, r: pb.GrooveRect) -> Vec:
    return (rng.uniform(r[0], r[2]), rng.uniform(r[1], r[3]), rng.uniform(0.0, 0.3))


@pytest.mark.parametrize("seed", range(30))
def test_groove_index_matches_full_scan(seed: int) -> None:
    rng = random.Random(seed)
    grooves: list[tuple[int, pb.GrooveRect]] = []
    for _ in range(rng.randint(1, 12)):
        x0 = rng.uniform(-15.0, 14.0)
        y0 = rng.uniform(-15.0, 14.0)
        grooves.append((0, (x0, y0, x0 + 0.8, y0 + rng.uniform(1.0, 20.0))))
    tris: list[tuple[Vec, Vec, Vec]] = []
    for _ in range(60):
        if rng.random() < 0.5:  # triangle inside a random groove footprint, low z: qualifies
            _ax, r = grooves[rng.randrange(len(grooves))]
            tris.append(
                (
                    _in_rect_vec(rng, r),
                    _in_rect_vec(rng, r),
                    _in_rect_vec(rng, r),
                )
            )
        else:
            tris.append((_rand_vec(rng, -20.0, 20.0), _rand_vec(rng, -20.0, 20.0), _rand_vec(rng, -20.0, 20.0)))
    mesh = _mesh_from_triangles(tris)
    index = pb._rect_buckets(grooves)
    full = _all_wide(len(grooves))
    for t in mesh.tris:
        assert pb._tri_in_groove(mesh.verts, t, grooves, index) == pb._tri_in_groove(mesh.verts, t, grooves, full)

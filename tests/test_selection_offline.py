"""Offline tests for fusion_kit.features selection helpers: faces_by_normal, convex_edges, select_edges(face_normal=).

No Fusion runtime: adsk stubs from conftest.py. Bodies are faked as axis-aligned unions of boxes (cm) with
pointContainment; faces/edges carry only what the helpers read (evaluator normal, pointOnFace, pointOnEdge,
faces, tempId, geometry). Includes the L-corner case that the old bisector-of-normals probe gets wrong.
"""

import itertools
import math
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import enums, features
from fusion_kit.features import convex_edges, faces_by_normal, select_edges

_ids = itertools.count(1)
Box = tuple[tuple[float, float, float], tuple[float, float, float]]


class _Solid:
    """Body = union of boxes (cm). pointContainment: strict interior of any box -> POINT_INSIDE, else OUTSIDE."""

    def __init__(self, boxes: list[Box], faces: list[Any], edges: list[Any]) -> None:
        self._boxes = boxes
        self.faces = faces
        self.edges = edges
        self.by_key: dict[tuple[int, float], Any] = {}

    def pointContainment(self, p: Any) -> Any:
        c = (p.x, p.y, p.z)
        for lo, hi in self._boxes:
            if all(lo[i] < c[i] < hi[i] for i in range(3)):
                return enums.POINT_INSIDE
        for lo, hi in self._boxes:  # closed box, on one of its faces -> ON (surface of the union)
            if all(lo[i] - 1e-9 <= c[i] <= hi[i] + 1e-9 for i in range(3)) and any(
                abs(c[i] - lo[i]) <= 1e-9 or abs(c[i] - hi[i]) <= 1e-9 for i in range(3)
            ):
                return enums.POINT_ON
        return enums.POINT_OUTSIDE


def _xyz(v: tuple[float, float, float]) -> Any:
    return SimpleNamespace(x=v[0], y=v[1], z=v[2])


def _triple(vals: list[float]) -> tuple[float, float, float]:
    """Mutable 3-list -> fixed-size 3-tuple (tuple(list) would be typed tuple[float, ...])."""
    x, y, z = vals
    return (x, y, z)


def _face(normal: tuple[float, float, float], point: tuple[float, float, float]) -> Any:
    """Planar face fake: constant evaluator normal, pointOnFace given."""
    return SimpleNamespace(
        tempId=next(_ids),
        pointOnFace=_xyz(point),
        evaluator=SimpleNamespace(
            getNormalAtPoint=lambda _p: (True, _xyz(normal)),
        ),
    )


def _edge(mid: tuple[float, float, float], f1: Any, f2: Any) -> Any:
    return SimpleNamespace(
        tempId=next(_ids),
        pointOnEdge=_xyz(mid),
        faces=[f1, f2],
        geometry=None,
    )


def _cube() -> _Solid:
    """Unit cube 0..1 cm: 6 faces (by_key[(axis, side)]), 12 edges, all convex 2-face creases."""
    by_key: dict[tuple[int, float], Any] = {}
    for axis in range(3):
        for side in (0.0, 1.0):
            normal = [0.0, 0.0, 0.0]
            normal[axis] = 1.0 if side == 1.0 else -1.0
            center = [0.5, 0.5, 0.5]
            center[axis] = side
            by_key[(axis, side)] = _face(_triple(normal), _triple(center))
    edges: list[Any] = []
    for ka, kb in itertools.combinations(by_key, 2):
        if ka[0] == kb[0]:
            continue
        mid = [0.5, 0.5, 0.5]
        mid[ka[0]] = ka[1]
        mid[kb[0]] = kb[1]
        edges.append(_edge(_triple(mid), by_key[ka], by_key[kb]))
    solid = _Solid([((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))], list(by_key.values()), edges)
    solid.by_key = by_key
    return solid


def _l_solid() -> _Solid:
    """L prism: [0,2]x[0,1] u [0,1]x[1,2] (z 0..1 cm). Single concave edge at (1,1,0.5)."""
    face_x = _face((1.0, 0.0, 0.0), (1.0, 1.5, 0.5))  # x=1 for y in 1..2, material on x<1
    face_y = _face((0.0, 1.0, 0.0), (1.5, 1.0, 0.5))  # y=1 for x in 1..2, material on y<1
    concave = _edge((1.0, 1.0, 0.5), face_x, face_y)
    return _Solid(
        [((0.0, 0.0, 0.0), (2.0, 1.0, 1.0)), ((0.0, 1.0, 0.0), (1.0, 2.0, 1.0))],
        [face_x, face_y],
        [concave],
    )


def _old_bisector_says_convex(body: _Solid, edge: Any) -> bool:
    """Previous live probe, reproduced: mid + eps*unit(n_A + n_B) OUTSIDE => convex."""
    fa, fb = edge.faces
    m = edge.pointOnEdge
    na = features.face_normal(fa, m)
    nb = features.face_normal(fb, m)
    b = (na.x + nb.x, na.y + nb.y, na.z + nb.z)
    ln = math.sqrt(b[0] ** 2 + b[1] ** 2 + b[2] ** 2)
    probe = _xyz((m.x + 0.01 * b[0] / ln, m.y + 0.01 * b[1] / ln, m.z + 0.01 * b[2] / ln))
    return body.pointContainment(probe) == enums.POINT_OUTSIDE


# ---------------------------------------------------------------------------
# convex_edges()
# ---------------------------------------------------------------------------
def test_convex_edges_unit_cube_all_twelve_convex(point3d_xyz: None) -> None:
    cube = _cube()
    got = convex_edges(cube, cube.edges)
    assert len(got) == 12
    assert got == cube.edges


def test_convex_edges_l_concave_corner_rejected(point3d_xyz: None) -> None:
    body = _l_solid()
    assert convex_edges(body, body.edges) == []


def test_old_bisector_probe_marks_l_concave_corner_convex() -> None:
    """Why the old probe is not used: outward-normal bisector lands OUTSIDE for convex AND concave corners."""
    cube = _cube()
    cube_corner = next(e for e in cube.edges if (e.pointOnEdge.x, e.pointOnEdge.y, e.pointOnEdge.z) == (1.0, 1.0, 0.5))
    l_body = _l_solid()
    assert _old_bisector_says_convex(cube, cube_corner) is True  # correct by luck
    assert _old_bisector_says_convex(l_body, l_body.edges[0]) is True  # WRONG: concave


def _l_top_face_solid(top_point: tuple[float, float, float]) -> tuple[_Solid, Any]:
    """L prism (z 0..1 cm), top face = one L-shaped face. Convex edge at x=1, z=1, y in 1..2 (top + wall x=1).

    top_point = fake pointOnFace of the top face. (1.5, 0.5, 1) lies in the far leg, beyond the edge line x=1.
    """
    top = _face((0.0, 0.0, 1.0), top_point)
    wall = _face((1.0, 0.0, 0.0), (1.0, 1.5, 0.5))
    edge = _edge((1.0, 1.5, 1.0), top, wall)
    body = _Solid(
        [((0.0, 0.0, 0.0), (2.0, 1.0, 1.0)), ((0.0, 1.0, 0.0), (1.0, 2.0, 1.0))],
        [top, wall],
        [edge],
    )
    return body, edge


@pytest.mark.parametrize("top_point", [(1.5, 0.5, 1.0), (0.5, 0.5, 1.0)])
def test_convex_edges_l_top_face_pointonface_sign_independent(
    point3d_xyz: None, top_point: tuple[float, float, float]
) -> None:
    """Old code: u_top from pointOnFace in far leg flips -> convex edge dropped. New: containment, same answer."""
    body, edge = _l_top_face_solid(top_point)
    assert convex_edges(body, [edge]) == [edge]


def test_convex_edges_ambiguous_probe_dropped(point3d_xyz: None) -> None:
    """Neither +u nor -u lands ON the surface (no body there) -> edge dropped, not guessed."""
    body, edge = _l_top_face_solid((1.5, 0.5, 1.0))
    body._boxes = []
    assert convex_edges(body, [edge]) == []


def test_convex_edges_drops_flat_and_single_face_edges(point3d_xyz: None) -> None:
    flat_a = _face((0.0, 0.0, 1.0), (0.0, 0.0, 1.0))
    flat_b = _face((0.0, 0.0, 1.0), (1.0, 0.0, 1.0))
    flat = _edge((0.5, 0.0, 1.0), flat_a, flat_b)
    lone = SimpleNamespace(
        tempId=next(_ids),
        pointOnEdge=_xyz((0.0, 0.0, 0.5)),
        faces=[flat_a],
        geometry=None,
    )
    body = _Solid([], [flat_a, flat_b], [flat, lone])
    assert convex_edges(body, body.edges) == []


# ---------------------------------------------------------------------------
# faces_by_normal()
# ---------------------------------------------------------------------------
def test_faces_by_normal_only_faces_within_min_dot() -> None:
    cube = _cube()
    # normal is normalized: (0,0,5) -> +z only, side faces cos 0
    assert faces_by_normal(cube, (0.0, 0.0, 5.0)) == [cube.by_key[(2, 1.0)]]
    # (1,1,0)/sqrt2 -> cos 0.707 with +x and +y faces, >= 0.6
    assert faces_by_normal(cube, (1.0, 1.0, 0.0)) == [
        cube.by_key[(0, 1.0)],
        cube.by_key[(1, 1.0)],
    ]


def test_faces_by_normal_min_dot_cut() -> None:
    cube = _cube()
    assert faces_by_normal(cube, (1.0, 1.0, 0.0), min_dot=0.8) == []
    assert len(faces_by_normal(cube, (1.0, 1.0, 0.0), min_dot=0.7)) == 2


def test_faces_by_normal_zero_normal_raises() -> None:
    with pytest.raises(ValueError, match="zero-length"):
        faces_by_normal(_cube(), (0.0, 0.0, 0.0))


# ---------------------------------------------------------------------------
# select_edges(face_normal=, min_dot=)
# ---------------------------------------------------------------------------
def test_select_edges_default_unchanged() -> None:
    cube = _cube()
    assert select_edges(cube) == cube.edges


def test_select_edges_face_normal_keeps_edges_touching_matching_face() -> None:
    cube = _cube()
    top = cube.by_key[(2, 1.0)]
    got = select_edges(cube, face_normal=(0.0, 0.0, 1.0))
    assert len(got) == 4  # top rim only; bottom and vertical edges have no +z face
    assert all(top in e.faces for e in got)


def test_select_edges_face_normal_min_dot_applies() -> None:
    cube = _cube()
    assert select_edges(cube, face_normal=(1.0, 1.0, 0.0), min_dot=0.8) == []
    assert len(select_edges(cube, face_normal=(1.0, 1.0, 0.0), min_dot=0.6)) > 0

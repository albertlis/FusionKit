"""Offline tests: check_body caches face bboxes per call (one boundingBox read per face, not per point).

Fake body: one down-facing mesh triangle tilted 44.93 deg (below 45 - 0.05 tol) + fake planar B-rep faces. Every fake
face counts its boundingBox reads and the body counts its faces iterations. No Fusion runtime: adsk stubs from conftest.
"""

import math
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from fusion_kit import features as _features  # imported at collection: adsk stubs intact when printability runs
from fusion_kit import printability
from fusion_kit.printability import IDENTITY, Mesh


@dataclass
class _Counts:
    reads: int = 0  # face.boundingBox accesses
    iters: int = 0  # body.faces iterations


class _Face:
    """Planar B-rep face over the triangle bbox (cm), normal tilt phi_deg, facing down. boundingBox read is counted."""

    def __init__(
        self,
        phi_deg: float,
        counts: _Counts,
        *,
        on_face: bool = True,
        shift_mm: float = 0.0,
        shift_if: Callable[[SimpleNamespace], bool] = lambda _q: True,
    ) -> None:
        self._counts = counts
        self._bb = SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0), maxPoint=SimpleNamespace(x=1.0, y=1.0, z=1.0)
        )
        k = math.tan(math.radians(phi_deg))
        norm = math.sqrt(1.0 + k * k)
        self._n = SimpleNamespace(x=-k / norm, y=0.0, z=-1.0 / norm)

        def _project(q: SimpleNamespace) -> tuple[bool, SimpleNamespace]:
            dz = shift_mm / 10.0 if shift_if(q) else 0.0
            return True, SimpleNamespace(x=q.x, y=q.y, z=q.z + dz)

        self.evaluator = SimpleNamespace(
            getParameterAtPoint=lambda p: (True, p),
            getPointAtParameter=_project,
            getNormalAtPoint=lambda _p: (True, self._n),
            isParameterOnFace=lambda _q: on_face,
        )
        self.loops: list[object] = []  # planar sloped face: not a slit annulus

    @property
    def boundingBox(self) -> SimpleNamespace:
        self._counts.reads += 1
        return self._bb


class _Faces(list[_Face]):
    def __init__(self, items: list[_Face], counts: _Counts) -> None:
        super().__init__(items)
        self._counts = counts

    def __iter__(self) -> Iterator[_Face]:
        self._counts.iters += 1
        return super().__iter__()


def _sloped_mesh(phi_deg: float) -> Mesh:
    """One down-facing triangle, underside tilted phi_deg from horizontal (plane z = 10 - k x)."""
    k = math.tan(math.radians(phi_deg))
    verts = [(0.0, 0.0, 10.0), (0.0, 10.0, 10.0), (10.0, 0.0, 10.0 - 10.0 * k)]
    return Mesh(verts=verts, tris=[(0, 1, 2)])


@pytest.fixture(autouse=True)
def _identity_print_frame(monkeypatch: pytest.MonkeyPatch, point3d_xyz: None) -> None:
    # fake bodies carry no attributes -> print frame = body itself; Point3D.create -> real xyz (snap distance)
    monkeypatch.setattr(printability, "print_frame_body", lambda body: body)
    monkeypatch.setattr(printability, "mesh_from_body", lambda body, quality=None: _sloped_mesh(44.93))
    monkeypatch.setattr(printability, "warp_family_mm", lambda body: [])
    # other test modules purge fusion_kit.* from sys.modules; printability re-imports features at call time, and that
    # re-execution fails on the adsk mock (ExtrudeFeature). Pin the collection-time module back for this test.
    monkeypatch.setitem(sys.modules, "fusion_kit.features", _features)


def _body(counts: _Counts, faces: list[_Face]) -> SimpleNamespace:
    return SimpleNamespace(faces=_Faces(faces, counts))


def test_one_bbox_read_per_face_per_check_body() -> None:
    # 4 faces; facet = centroid + 3 vertices, each point scans all faces -> reads must not scale with points
    counts = _Counts()
    faces = [
        _Face(46.0, counts),
        _Face(46.0, counts, on_face=False),
        _Face(40.0, counts, shift_mm=0.5),
        _Face(20.0, counts, on_face=False),
    ]
    rep = printability.check_body(_body(counts, faces), IDENTITY)
    assert rep.overhang_area_mm2 == 0.0  # nearest on-face candidate is the 46 deg face
    assert counts.reads == len(faces)
    assert counts.iters == 1


def test_reads_scale_with_check_body_calls_only() -> None:
    counts = _Counts()
    faces = [_Face(46.0, counts), _Face(40.0, counts, on_face=False)]
    body = _body(counts, faces)
    printability.check_body(body, IDENTITY)
    printability.check_body(body, IDENTITY)
    assert counts.reads == 2 * len(faces)
    assert counts.iters == 2


def test_cache_cleared_after_check_body() -> None:
    counts = _Counts()
    body = _body(counts, [_Face(46.0, counts)])
    printability.check_body(body, IDENTITY)
    assert printability._FACE_BOXES.get() is None


@pytest.mark.parametrize(
    ("build", "expect_overhang"),
    [
        pytest.param(lambda c: [_Face(46.0, c)], False, id="46_on_face_chordal"),
        pytest.param(lambda c: [_Face(40.0, c)], True, id="40_real_overhang"),
        pytest.param(lambda c: [_Face(46.0, c, on_face=False)], True, id="46_off_trim_no_drop"),
        pytest.param(
            lambda c: [_Face(46.0, c, on_face=False), _Face(40.0, c, shift_mm=0.05)],
            True,
            id="nearest_on_face_is_40",
        ),
        pytest.param(
            lambda c: [_Face(46.0, c, shift_mm=0.5, shift_if=lambda q: q.x > 0.5)],
            True,
            id="vertex_off_face_keeps_flag",
        ),
        pytest.param(
            lambda c: [_Face(0.0, c, shift_mm=0.05, shift_if=lambda q: q.x > 0.1 and q.y > 0.1), _Face(46.0, c)],
            False,
            id="shared_boundary_dropped",
        ),
    ],
)
def test_results_unchanged(build: Callable[[_Counts], list[_Face]], expect_overhang: bool) -> None:
    counts = _Counts()
    faces = build(counts)
    rep = printability.check_body(_body(counts, faces), IDENTITY)
    assert (rep.overhang_area_mm2 > 0.0) is expect_overhang
    assert counts.reads == len(faces)

"""Offline tests: check_body B-rep overhang override (chordal tessellation facet on an inclined chamfer).

Fake body: one down-facing mesh triangle tilted 44.93 deg (below 45 - 0.05 tol) + fake planar B-rep faces whose
normal tilt, trim (isParameterOnFace) and point offset are set per test. No Fusion runtime: adsk stubs from conftest.
"""

import math
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from fusion_kit import features, printability
from fusion_kit.printability import IDENTITY, Mesh


@pytest.fixture(autouse=True)
def _identity_print_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    # fake bodies carry no attributes; bed '-z' by definition -> print frame = body itself
    monkeypatch.setattr(printability, "print_frame_body", lambda body: body)


def _sloped_mesh(phi_deg: float) -> Mesh:
    """One down-facing triangle, underside tilted phi_deg from horizontal (plane z = 10 - k x, k = tan phi)."""
    k = math.tan(math.radians(phi_deg))
    verts = [(0.0, 0.0, 10.0), (0.0, 10.0, 10.0), (10.0, 0.0, 10.0 - 10.0 * k)]
    return Mesh(verts=verts, tris=[(0, 1, 2)])


def _brep_face(
    phi_brep_deg: float,
    *,
    on_face: bool = True,
    shift_mm: float = 0.0,
    shift_if: Callable[[SimpleNamespace], bool] = lambda _q: True,
) -> SimpleNamespace:
    """One planar B-rep face over the triangle's bbox (cm), normal tilt phi_brep, facing down (orientation-aware).

    on_face: evaluator.isParameterOnFace answer (trim). shift_mm: projected point offset in z (distance to centroid).
    shift_if: which projected queries get shift_mm (default: all).
    """
    k = math.tan(math.radians(phi_brep_deg))
    norm = math.sqrt(1.0 + k * k)
    n = SimpleNamespace(x=-k / norm, y=0.0, z=-1.0 / norm)

    def _project(q: SimpleNamespace) -> tuple[bool, SimpleNamespace]:
        dz = shift_mm / 10.0 if shift_if(q) else 0.0
        return True, SimpleNamespace(x=q.x, y=q.y, z=q.z + dz)

    evaluator = SimpleNamespace(
        getParameterAtPoint=lambda p: (True, p),
        getPointAtParameter=_project,
        getNormalAtPoint=lambda p: (True, n),
        isParameterOnFace=lambda q: on_face,
    )
    return SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0), maxPoint=SimpleNamespace(x=1.0, y=1.0, z=1.0)
        ),
        evaluator=evaluator,
        loops=[],  # planar sloped face: not a slit annulus (_annulus_width_mm)
    )


def _brep_body(phi_brep_deg: float) -> SimpleNamespace:
    return SimpleNamespace(faces=[_brep_face(phi_brep_deg)])


@pytest.fixture
def sloped_44p93(monkeypatch: pytest.MonkeyPatch, point3d_xyz: None) -> None:  # Point3D.create -> real xyz (snap dist)
    monkeypatch.setattr(printability, "mesh_from_body", lambda body, quality=None: _sloped_mesh(44.93))
    monkeypatch.setattr(printability, "warp_family_mm", lambda body: [])


def test_mesh_facet_precondition_is_overhang(sloped_44p93: None) -> None:
    # mesh-only view: the facet alone is an overhang at 44.93 deg (sanity for the tests below)
    rep = printability.check(_sloped_mesh(44.93))
    assert rep.overhang_area_mm2 > 0.0
    assert rep.worst_overhang_deg == pytest.approx(44.93, abs=1e-3)


def test_chordal_facet_on_46deg_brep_not_overhang(sloped_44p93: None) -> None:
    # facet 44.93 deg is tessellation error of a 46 deg B-rep chamfer -> not an overhang
    rep = printability.check_body(_brep_body(46.0), IDENTITY)
    assert rep.overhang_area_mm2 == 0.0


def test_facet_on_40deg_brep_still_overhang(sloped_44p93: None) -> None:
    # B-rep really 40 deg -> overhang stays
    rep = printability.check_body(_brep_body(40.0), IDENTITY)
    assert rep.overhang_area_mm2 > 0.0


def test_brep_face_off_face_no_drop(sloped_44p93: None) -> None:
    # nearest B-rep face is 46 deg but the projected point is off its trimmed face -> no candidate, facet stays overhang
    body = SimpleNamespace(faces=[_brep_face(46.0, on_face=False)])
    rep = printability.check_body(body, IDENTITY)
    assert rep.overhang_area_mm2 > 0.0


def test_brep_nearest_off_face_skipped(sloped_44p93: None) -> None:
    # off-face 46 deg face sits exactly at the centroid (d = 0) but is untrimmed-only; on-face 40 deg face is 0.05 mm away
    # -> the on-face candidate is used (40 deg) -> overhang stays. Old code took the off-face one (46) and dropped it.
    body = SimpleNamespace(faces=[_brep_face(46.0, on_face=False), _brep_face(40.0, shift_mm=0.05)])
    rep = printability.check_body(body, IDENTITY)
    assert rep.overhang_area_mm2 > 0.0


def test_brep_projection_beyond_snap_tol_no_drop(sloped_44p93: None) -> None:
    # on-face 46 deg face but projected point 0.5 mm away (> 0.1 mm snap tol) -> no drop, facet stays overhang
    body = SimpleNamespace(faces=[_brep_face(46.0, shift_mm=0.5)])
    rep = printability.check_body(body, IDENTITY)
    assert rep.overhang_area_mm2 > 0.0


def test_vertex_far_from_brep_face_keeps_facet_flagged(sloped_44p93: None) -> None:
    # centroid (x 3.3 mm) snaps to the 46 deg face, but vertex (10 mm, 0) projects 0.5 mm off it -> that vertex has no
    # candidate -> facet stays overhang. Old code checked the centroid only and dropped it.
    body = SimpleNamespace(faces=[_brep_face(46.0, shift_mm=0.5, shift_if=lambda q: q.x > 0.5)])
    rep = printability.check_body(body, IDENTITY)
    assert rep.overhang_area_mm2 > 0.0


def test_face_normal_value_error_keeps_facet_flagged(sloped_44p93: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # degenerate point / apex: face_normal raises ValueError -> recheck must not crash check_body; facet stays overhang
    def _no_normal(face: object, point: object = None) -> None:
        raise ValueError("face_normal: evaluator returned no normal (point not on face?)")

    monkeypatch.setattr(features, "face_normal", _no_normal)
    rep = printability.check_body(_brep_body(46.0), IDENTITY)
    assert rep.overhang_area_mm2 > 0.0


def test_boundary_vertices_shared_with_adjacent_face_dropped(sloped_44p93: None) -> None:
    # facet vertices sit on the chamfer boundary edge: they project onto the adjacent face too (d = 0 for both).
    # centroid (query in cm: x, y = 0.33) is 0.05 mm off the adjacent face only -> centroid face = 46 deg chamfer.
    # adjacent face listed first: old code takes it as every vertex's nearest (tie) -> index mismatch -> stays flagged.
    adjacent = _brep_face(0.0, shift_mm=0.05, shift_if=lambda q: q.x > 0.1 and q.y > 0.1)
    body = SimpleNamespace(faces=[adjacent, _brep_face(46.0)])
    rep = printability.check_body(body, IDENTITY)
    assert rep.overhang_area_mm2 == 0.0

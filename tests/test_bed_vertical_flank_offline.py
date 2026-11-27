"""Offline test: vertical 45 deg flank at a bed edge is not a bed chamfer (V seam notch wall, dfam_facts).

Vertical faces (|n_z| ~ 0) are never chamfer faces, even when their normal passes the axis test (max|n| < AXIS_PARALLEL).
No Fusion runtime: adsk stubs from conftest.py. features helpers monkeypatched (as in test_dfam_adapter_geom_offline).
"""

import math
import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features
from fusion_kit.dfam import check_edges, facts_from_body

_CORE: Any = sys.modules["adsk.core"]
_S = 1 / math.sqrt(2)
_NO_ATTR = SimpleNamespace(itemByName=lambda group, name: None)


def _v(x: float, y: float, z: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


@pytest.fixture
def casts() -> Iterator[None]:
    for cls, tag in (("Plane", "plane"), ("Cylinder", "cyl"), ("Cone", "cone"), ("Line3D", "line"), ("Torus", "torus")):
        getattr(_CORE, cls).cast.side_effect = lambda g, _t=tag: g if getattr(g, "tag", "") == _t else None
    yield
    for cls in ("Plane", "Cylinder", "Cone", "Line3D", "Torus"):
        getattr(_CORE, cls).cast.side_effect = None


@pytest.fixture
def bed_features(monkeypatch: pytest.MonkeyPatch, point3d_xyz: None) -> None:
    monkeypatch.setattr(features, "face_normal", lambda face, point=None: face.normal)
    monkeypatch.setattr(
        features,
        "planar_faces",
        lambda body, axis, at_mm, normal_sign=None, tol_mm=0.01: [f for f in body.faces if f.name.startswith("bed")],
    )
    monkeypatch.setattr(features, "convex_edges", lambda body, edges, probe_mm=0.1: list(edges))


def _face(
    name: str,
    normal: tuple[float, float, float],
    lo: tuple[float, float, float],
    hi: tuple[float, float, float],
    *,
    area: float = 1.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        tempId=f"f:{name}",
        geometry=SimpleNamespace(tag="plane"),
        normal=_v(*normal),
        boundingBox=SimpleNamespace(minPoint=_v(*lo), maxPoint=_v(*hi)),
        pointOnFace=_v(*lo),
        area=area,
        edges=[],
        loops=[],
        attributes=_NO_ATTR,
    )


def _edge(
    token: str,
    fa: SimpleNamespace,
    fb: SimpleNamespace,
    p: tuple[float, float, float],
    q: tuple[float, float, float],
) -> SimpleNamespace:
    e = SimpleNamespace(
        tempId=token,
        geometry=SimpleNamespace(tag="line"),
        faces=[fa, fb],
        startVertex=SimpleNamespace(geometry=_v(*p)),
        endVertex=SimpleNamespace(geometry=_v(*q)),
        pointOnEdge=_v(*(sum(c) / 2 for c in zip(p, q))),
        length=math.dist(p, q),
    )
    fa.edges.append(e)
    fb.edges.append(e)
    return e


def _body(faces: list[SimpleNamespace], edges: list[SimpleNamespace]) -> MagicMock:
    body = MagicMock(
        boundingBox=SimpleNamespace(minPoint=_v(0, 0, 0), maxPoint=_v(2, 2, 1)),
        faces=faces,
        edges=edges,
        shells=SimpleNamespace(count=1),
        attributes=_NO_ATTR,
    )
    body.name = "t"  # MagicMock(name=...) names the mock repr, not the .name attribute
    return body


def _flank_body() -> MagicMock:
    """2 x 2 x 1 cm block, bed edge (0,0,0)-(1,1,0) cm shared with a 45 deg VERTICAL flank, normal (s,-s,0).

    Flank = wall of a V seam notch. No chamfer anywhere.
    """
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    flank = _face("flank", (_S, -_S, 0), (0, 0, 0), (1, 1, 1))
    e_bf = _edge("bf", bed, flank, (0, 0, 0), (1, 1, 0))
    bed.loops = [SimpleNamespace(isOuter=True, edges=[e_bf])]
    return _body([bed, flank], [e_bf])


def _chamfer_46_body() -> MagicMock:
    """2 x 2 x 1 cm block, bed / x0-wall edge chamfered 46 deg, wall leg 1.0 mm (real chamfer, must still be measured)."""
    b = 0.100
    a = b / math.tan(math.radians(46.0))
    width = math.hypot(a, b)
    norm = math.hypot(1 / a, 1 / b)
    n = (-(1 / a) / norm, 0.0, -(1 / b) / norm)
    bed = _face("bed", (0, 0, -1), (0, 0, 0), (2, 2, 0))
    wall = _face("wall", (-1, 0, 0), (0, 0, b), (0, 2, 1))
    chamfer = _face("chamfer", n, (0, 0, 0), (a, 2, b), area=width * 2)
    e_bc = _edge("bc", bed, chamfer, (a, 0, 0), (a, 2, 0))
    e_cw = _edge("cw", chamfer, wall, (0, 0, b), (0, 2, b))
    bed.loops = [SimpleNamespace(isOuter=True, edges=[e_bc])]
    return _body([bed, wall, chamfer], [e_bc, e_cw])


def test_vertical_45deg_flank_is_not_bed_chamfer(casts: None, bed_features: None) -> None:
    _, holes, edges = facts_from_body(_flank_body())
    assert holes == []
    assert [(e.kind, e.treatment) for e in edges] == [("bed", "sharp")]
    assert edges[0].size_mm == 0.0


def test_real_46deg_chamfer_still_measured(casts: None, bed_features: None) -> None:
    _, _holes, edges = facts_from_body(_chamfer_46_body())
    assert [(e.kind, e.treatment) for e in edges] == [("bed", "chamfer")]
    assert edges[0].size_mm == pytest.approx(0.9657, abs=1e-3)
    assert check_edges(edges) == []

"""Offline tests for fusion_kit.features.offset_plane and planar_faces.

No Fusion runtime: adsk stubs come from conftest.py. Geometry is faked with plain objects;
units are Fusion cm (helpers take mm).
"""

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit.features import offset_plane, planar_faces

_CORE: Any = sys.modules["adsk.core"]


@dataclass(frozen=True)
class _V:
    x: float
    y: float
    z: float


class _FakePlane:
    """adsk.core.Plane stand-in: normal + origin (cm)."""

    def __init__(self, normal: tuple[float, float, float], origin: tuple[float, float, float]) -> None:
        self.normal = _V(*normal)
        self.origin = _V(*origin)


@pytest.fixture(autouse=True)
def _plane_cast() -> Iterator[None]:
    """Plane.cast returns the fake plane geometry, None for anything else (curved faces)."""
    _CORE.Plane.cast.side_effect = lambda g: g if isinstance(g, _FakePlane) else None
    yield
    _CORE.Plane.cast.side_effect = None


def _plane_face(
    normal: tuple[float, float, float],
    origin: tuple[float, float, float],
    area: float,
    *,
    oriented: tuple[float, float, float] | None = None,
) -> SimpleNamespace:
    """Planar face. oriented = what evaluator reports (orientation-aware); geometry normal stays as given."""
    n = oriented if oriented is not None else normal
    return SimpleNamespace(
        geometry=_FakePlane(normal, origin),
        area=area,
        pointOnFace=_V(*origin),
        evaluator=SimpleNamespace(getNormalAtPoint=lambda _p: (True, _V(*n))),
    )


def _curved_face(area: float) -> SimpleNamespace:
    return SimpleNamespace(geometry=SimpleNamespace(kind="cylinder"), area=area)


def _body(*faces: SimpleNamespace) -> Any:
    return SimpleNamespace(faces=list(faces))


# 10 mm cube in cm: top z=1.0, bottom z=0.0. Side faces have normal x/y, origin z may equal 1.0 (corner).
top = _plane_face((0, 0, 1), (0, 0, 1.0), area=1.0)
# Stale geometry normal +z (what face.geometry.normal reports), evaluator says -z (real orientation).
bottom = _plane_face((0, 0, 1), (0, 0, 0.0), area=1.0, oriented=(0, 0, -1))
side_x = _plane_face((1, 0, 0), (1.0, 0.0, 1.0), area=1.0)
side_y = _plane_face((0, 1, 0), (0.0, 1.0, 0.5), area=1.0)
cube: Any = _body(top, bottom, side_x, side_y)


# ---------------------------------------------------------------------------
# offset_plane
# ---------------------------------------------------------------------------
def test_offset_plane_sets_expression_and_name() -> None:
    comp = MagicMock(name="comp")
    base = MagicMock(name="base_face")
    plane = offset_plane(comp, base, "side_wall_thickness", label="inner_wall")
    inp = comp.constructionPlanes.createInput.return_value
    _CORE.ValueInput.createByString.assert_called_with("side_wall_thickness")
    inp.setByOffset.assert_called_once_with(base, _CORE.ValueInput.createByString.return_value)
    comp.constructionPlanes.add.assert_called_once_with(inp)
    assert plane is comp.constructionPlanes.add.return_value
    assert plane.name == "inner_wall"


# ---------------------------------------------------------------------------
# planar_faces
# ---------------------------------------------------------------------------
def test_box_top_face_only() -> None:
    res = planar_faces(cube, "z", 10.0)
    assert len(res) == 1
    assert res[0] is top


def test_normal_sign_uses_orientation_not_geometry_normal() -> None:
    # bottom geometry normal is +z, evaluator says -z -> sign -1 keeps it, +1 drops it
    res = planar_faces(cube, "z", 0.0, normal_sign=-1)
    assert len(res) == 1 and res[0] is bottom
    assert planar_faces(cube, "z", 0.0, normal_sign=1) == []
    # top at z=10 mm faces +z: sign -1 drops it
    assert planar_faces(cube, "z", 10.0, normal_sign=-1) == []


def test_coplanar_faces_both_returned_sorted_by_area() -> None:
    big = _plane_face((0, 0, 1), (0, 0, 2.0), area=5.0)
    small = _plane_face((0, 0, 1), (3.0, 0, 2.0), area=3.0)
    res = planar_faces(_body(small, big), "z", 20.0)
    assert len(res) == 2
    assert res[0] is big and res[1] is small
    assert [f.area for f in res] == [5.0, 3.0]


def test_non_planar_and_off_tolerance_skipped() -> None:
    curved = _curved_face(area=100.0)
    off = _plane_face((0, 0, 1), (0, 0, 1.05), area=9.0)  # 10.5 mm, outside 0.01 mm tol
    near = _plane_face((0, 0, 1), (0, 0, 1.0005), area=8.0)  # 10.005 mm, inside tol
    res = planar_faces(_body(curved, off, near, top), "z", 10.0)
    assert [f.area for f in res] == [8.0, 1.0]
    assert res[0] is near and res[1] is top


def test_bad_normal_sign_raises() -> None:
    with pytest.raises(ValueError):
        planar_faces(cube, "z", 10.0, normal_sign=0)

"""Offline tests for fusion_kit.view: fake adsk modules injected into sys.modules, no Fusion needed."""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterator

import pytest


class _Point3D:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.xyz = (x, y, z)

    @staticmethod
    def create(x: float, y: float, z: float) -> _Point3D:
        return _Point3D(x, y, z)


class _Vector3D:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.xyz = (x, y, z)

    @staticmethod
    def create(x: float, y: float, z: float) -> _Vector3D:
        return _Vector3D(x, y, z)


class _ViewOrientations:
    # values copied from adsk/core.py ViewOrientations stub
    IsoTopRightViewOrientation = 7
    TopViewOrientation = 10
    FrontViewOrientation = 3
    RightViewOrientation = 9


class _Camera:
    def __init__(self) -> None:
        self.eye: _Point3D | None = None
        self.target: _Point3D | None = None
        self.upVector: _Vector3D | None = None
        self.isSmoothTransition = True
        self.viewOrientation: int | None = None


class _Viewport:
    def __init__(self) -> None:
        self.camera = _Camera()
        self.fit_calls = 0
        self.saved: list[tuple[str, int, int]] = []

    def fit(self) -> bool:
        self.fit_calls += 1
        return True

    def saveAsImageFile(self, filename: str, width: int, height: int) -> bool:
        self.saved.append((filename, width, height))
        return True


class _Application:
    active: _Viewport | None = None

    @classmethod
    def get(cls) -> _Application:
        return cls()

    @property
    def activeViewport(self) -> _Viewport | None:
        return _Application.active


@pytest.fixture
def view(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    adsk = types.ModuleType("adsk")
    core = types.ModuleType("adsk.core")
    vars(core).update(
        Application=_Application,
        Point3D=_Point3D,
        Vector3D=_Vector3D,
        ViewOrientations=_ViewOrientations,
        Viewport=_Viewport,
        DocumentTypes=types.SimpleNamespace(FusionDesignDocumentType=0),  # enums.py reads it at import
    )
    vars(adsk).update(core=core, fusion=sys.modules["adsk.fusion"])
    monkeypatch.setitem(sys.modules, "adsk", adsk)
    monkeypatch.setitem(sys.modules, "adsk.core", core)
    monkeypatch.delitem(sys.modules, "fusion_kit.view", raising=False)
    _Application.active = _Viewport()
    yield importlib.import_module("fusion_kit.view")
    monkeypatch.delitem(sys.modules, "fusion_kit.view", raising=False)


def test_save_screenshot_accepts_eye_and_target_positionally(
    view: types.ModuleType,
) -> None:
    vp = _Application.active
    assert vp is not None
    path = view.save_screenshot("shot.png", (1000.0, 2000.0, 3000.0), (0.0, 0.0, 10.0))
    assert path == "shot.png"
    assert vp.camera.eye is not None and vp.camera.eye.xyz == pytest.approx((100.0, 200.0, 300.0))
    assert vp.camera.target is not None and vp.camera.target.xyz == pytest.approx((0.0, 0.0, 1.0))
    assert vp.camera.upVector is not None and vp.camera.upVector.xyz == (0.0, 0.0, 1.0)
    assert vp.camera.isSmoothTransition is False
    assert vp.fit_calls == 1
    assert vp.saved == [("shot.png", 600, 400)]


@pytest.mark.parametrize(
    ("eye", "expected_up"),
    [
        ((0.0, 0.0, 100.0), (0.0, 1.0, 0.0)),  # straight above target: +Z would be degenerate
        ((0.0, 0.0, -100.0), (0.0, 1.0, 0.0)),  # straight below target
        ((1000.0, 1000.0, 1000.0), (0.0, 0.0, 1.0)),  # iso: +Z stays
    ],
)
def test_save_screenshot_up_vector_avoids_degenerate_view(
    view: types.ModuleType, eye: tuple[float, float, float], expected_up: tuple[float, float, float]
) -> None:
    vp = _Application.active
    assert vp is not None
    view.save_screenshot("shot.png", eye, (0.0, 0.0, 0.0))
    assert vp.camera.upVector is not None and vp.camera.upVector.xyz == expected_up


def test_save_screenshot_no_viewport_raises_clear_error(view: types.ModuleType) -> None:
    _Application.active = None
    with pytest.raises(RuntimeError, match="no active viewport"):
        view.save_screenshot("shot.png")


def test_set_orientation_none_viewport_raises(view: types.ModuleType) -> None:
    _Application.active = None
    with pytest.raises(RuntimeError, match="no active viewport"):
        view.set_orientation("top")


@pytest.mark.parametrize(
    ("name", "expected"),
    [("top", 10), ("front", 3), ("right", 9)],
)
def test_set_orientation_maps_name_to_enum(view: types.ModuleType, name: str, expected: int) -> None:
    vp = _Application.active
    assert vp is not None
    view.set_orientation(name)
    assert vp.camera.viewOrientation == expected
    assert vp.camera.isSmoothTransition is False


def test_set_orientation_iso_reuses_enum(view: types.ModuleType) -> None:
    """iso-top-right must use the shared enums.ISO_TOP_RIGHT object, not a local cast."""
    vp = _Application.active
    assert vp is not None
    view.set_orientation("iso-top-right")
    assert vp.camera.viewOrientation is view.ISO_TOP_RIGHT


def test_set_orientation_unknown_name_raises(view: types.ModuleType) -> None:
    vp = _Application.active
    assert vp is not None
    with pytest.raises(ValueError, match="unknown view orientation"):
        view.set_orientation("back")
    assert vp.camera.viewOrientation is None

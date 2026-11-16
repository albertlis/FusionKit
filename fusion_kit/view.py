"""Viewport screenshot to PNG file + named camera orientations. fusion_mcp_read screenshot returns inline image only; agents need a file."""

from __future__ import annotations

import math
from typing import cast

import adsk.core
import adsk.fusion

from fusion_kit.enums import ISO_TOP_RIGHT

Xyz = tuple[float, float, float]

# Stub constants are typed int; cast to the Camera.viewOrientation parameter type (enums.py pattern).
_ORIENTATIONS: dict[str, adsk.core.ViewOrientations] = {
    "iso-top-right": ISO_TOP_RIGHT,
    "top": cast(adsk.core.ViewOrientations, adsk.core.ViewOrientations.TopViewOrientation),
    "front": cast(adsk.core.ViewOrientations, adsk.core.ViewOrientations.FrontViewOrientation),
    "right": cast(adsk.core.ViewOrientations, adsk.core.ViewOrientations.RightViewOrientation),
}


def _active_viewport() -> adsk.core.Viewport:
    vp = adsk.core.Application.get().activeViewport
    if vp is None:
        raise RuntimeError("no active viewport — Fusion window minimized / no document?")
    return vp


def set_orientation(name: str) -> None:
    """Set active viewport camera to a named preset: "iso-top-right" | "top" | "front" | "right".

    Camera change only, document not modified, no smooth transition. Unknown name -> ValueError.
    save_screenshot() fits and saves; call it after this.
    """
    orientation = _ORIENTATIONS.get(name)
    if orientation is None:
        raise ValueError(f"unknown view orientation {name!r}; expected one of {sorted(_ORIENTATIONS)}")
    vp = _active_viewport()
    cam = vp.camera
    cam.viewOrientation = orientation
    cam.isSmoothTransition = False
    vp.camera = cam


def _up_vector(cam: adsk.core.Camera) -> adsk.core.Vector3D:
    """+Z, except when eye->target runs along Z (|cos| > 0.999): then +Y, since +Z is degenerate there."""
    dx = cam.eye.x - cam.target.x
    dy = cam.eye.y - cam.target.y
    dz = cam.eye.z - cam.target.z
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length > 0.0 and abs(dz) / length > 0.999:
        return adsk.core.Vector3D.create(0.0, 1.0, 0.0)
    return adsk.core.Vector3D.create(0.0, 0.0, 1.0)


def save_screenshot(
    path: str,
    eye_mm: Xyz | None = None,
    target_mm: Xyz | None = None,
    width: int = 600,
    height: int = 400,
    hide: list[adsk.fusion.Occurrence] | None = None,
    fit: bool = True,
) -> str:
    """Save active viewport to path (format from extension, e.g. .png). Returns path.

    eye_mm / target_mm (mm, root frame): when given, camera is set (upVector = +Z, or +Y when the
    view looks straight along Z; no smooth transition).
    Missing one of the two keeps current camera value.
    fit=True (default): fit() before saving. fit=False: camera kept as set (eye_mm/target_mm respected).
    hide: occurrences with light bulb off for the shot; original isLightBulbOn restored in finally.
    Camera change only, document not modified (hide is restored). saveAsImageFile False -> RuntimeError.
    No active viewport -> RuntimeError.
    """
    vp = _active_viewport()
    if eye_mm is not None or target_mm is not None:
        cam = vp.camera
        if eye_mm is not None:
            cam.eye = adsk.core.Point3D.create(*(v / 10 for v in eye_mm))
        if target_mm is not None:
            cam.target = adsk.core.Point3D.create(*(v / 10 for v in target_mm))
        cam.upVector = _up_vector(cam)
        cam.isSmoothTransition = False
        vp.camera = cam
    hidden: list[tuple[adsk.fusion.Occurrence, bool]] = []
    try:
        for occ in hide or []:
            hidden.append((occ, occ.isLightBulbOn))
            occ.isLightBulbOn = False
        if fit:
            vp.fit()
        if not vp.saveAsImageFile(path, width, height):
            raise RuntimeError(f"saveAsImageFile failed: {path}")
    finally:
        for occ, was_on in hidden:
            occ.isLightBulbOn = was_on
    return path

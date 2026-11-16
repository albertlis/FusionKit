"""Dovetail slide joint: pure profile geometry + Fusion adapter.

Pure core (dovetail_profile, _is_axial) imports no adsk, so it runs offline. Adapter (dovetail, mark_mating)
imports adsk and fusion_kit inside the function body only. Units: mm.
dovetail() sketch convention: sketch x = width axis, sketch y = depth axis (tip at +y), extrusion along sketch normal.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import TYPE_CHECKING

from fusion_kit._vec import _cross, _dot, _norm
from fusion_kit.dfam import DFAM_GROUP, MATING_ATTR

if TYPE_CHECKING:
    import adsk.core
    import adsk.fusion

Pt2 = tuple[float, float]
Vec = tuple[float, float, float]
AXIAL_TOL = 1e-6  # 1 - |cos(angle)| below this = parallel (cap-face test)


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------
def dovetail_profile(
    width_mm: float,
    depth_mm: float,
    angle_deg: float = 15.0,
    clearance_mm: float = 0.2,
) -> tuple[list[Pt2], list[Pt2]]:
    """Male tail and female socket outlines (mm) in (x = width, y = depth) coordinates.

    Male: narrow base of width_mm at y=0; flanks lean outward by tan(angle_deg) per mm of depth
    (angle_deg measured from the depth axis); wide tip at y=depth_mm.
    Female: male offset outward for clearance. Each flank shifts horizontally by clearance_mm / cos(angle_deg),
    so the PERPENDICULAR flank gap is clearance_mm per side (horizontal width 2*clearance_mm/cos(angle_deg)
    wider at every y in [0, depth_mm]); tip at depth_mm + clearance_mm (tip gap = clearance_mm); base flush
    at y=0. Vertex order: base-left, base-right, tip-right, tip-left.
    """
    if width_mm <= 0 or depth_mm <= 0:
        raise ValueError(f"dovetail_profile: width/depth must be > 0, got {width_mm}, {depth_mm}")
    if not 0.0 <= angle_deg < 90.0:
        raise ValueError(f"dovetail_profile: angle_deg must be in [0, 90), got {angle_deg}")
    if clearance_mm < 0:
        raise ValueError(f"dovetail_profile: clearance_mm must be >= 0, got {clearance_mm}")
    t = math.tan(math.radians(angle_deg))
    hw = width_mm / 2
    tip_hw = hw + depth_mm * t
    male = [(-hw, 0.0), (hw, 0.0), (tip_hw, depth_mm), (-tip_hw, depth_mm)]
    c = clearance_mm
    h = c / math.cos(math.radians(angle_deg))  # horizontal flank shift; perpendicular gap = c
    fem_tip_y = depth_mm + c
    fem_tip_hw = hw + h + fem_tip_y * t
    female = [
        (-(hw + h), 0.0),
        (hw + h, 0.0),
        (fem_tip_hw, fem_tip_y),
        (-fem_tip_hw, fem_tip_y),
    ]
    return male, female


def _is_axial(n: Vec, axis: Vec, tol: float = AXIAL_TOL) -> bool:
    """True if n is parallel (or anti-parallel) to axis. Inputs need not be unit. Zero vector -> False."""
    ln = _norm(n) * _norm(axis)
    if ln == 0.0:
        return False
    return abs(_dot(n, axis)) / ln >= 1.0 - tol


# ---------------------------------------------------------------------------
# Fusion adapter
# ---------------------------------------------------------------------------
def mark_mating(faces: Iterable[adsk.fusion.BRepFace]) -> None:
    """Tag each face with attribute dfam/mating/1 (dovetail mating-surface marker)."""
    for f in faces:
        f.attributes.add(DFAM_GROUP, MATING_ATTR, "1")


def _closed_poly(s: adsk.fusion.Sketch, origin: adsk.core.Point3D, pts: list[Pt2]) -> None:
    """Closed polygon in sketch space: origin (sketch cm) + pts (mm, sketch x/y). Chained endpoints as sketch.poly; no dims."""
    import adsk.core

    p3 = adsk.core.Point3D.create
    sp = [p3(origin.x + u / 10, origin.y + v / 10, 0.0) for u, v in pts]
    lines = s.sketchCurves.sketchLines
    first = lines.addByTwoPoints(sp[0], sp[1])
    prev = first
    for q in sp[2:]:
        prev = lines.addByTwoPoints(prev.endSketchPoint, q)
    lines.addByTwoPoints(prev.endSketchPoint, first.startSketchPoint)


def _sketch_normal(sketch: adsk.fusion.Sketch) -> Vec:
    x = sketch.xDirection
    y = sketch.yDirection
    return _cross((x.x, x.y, x.z), (y.x, y.y, y.z))


def _unique_edges(faces: Iterable[adsk.fusion.BRepFace]) -> list[adsk.fusion.BRepEdge]:
    seen: dict[str, adsk.fusion.BRepEdge] = {}
    for f in faces:
        for e in f.edges:
            seen.setdefault(e.entityToken, e)
    return list(seen.values())


def dovetail(
    comp: adsk.fusion.Component,
    sketch: adsk.fusion.Sketch,
    body_male: adsk.fusion.BRepBody,
    body_female: adsk.fusion.BRepBody,
    origin_mm: tuple[float, float, float],
    width_mm: float,
    depth_mm: float,
    length_mm: float,
    *,
    label: str,
    angle_deg: float = 15.0,
    clearance_mm: float = 0.2,
    edge_chamfer_mm: float = 0.3,
) -> tuple[adsk.fusion.ExtrudeFeature, adsk.fusion.ExtrudeFeature, list[adsk.fusion.ChamferFeature]]:
    """Sliding dovetail on a shared plane: male tail joined onto body_male, socket cut into body_female.

    sketch    : empty plane sketch = mating plane. Male profile drawn on it.
    origin_mm : model mm of the tail base centre on that plane. Sketch x = width, sketch y = depth (tip at +y).
    Both extrusions run length_mm along the sketch normal (body_male on the -normal side, body_female on +normal side).
    Socket profile: second sketch on sketch.referencePlane (labelled f"{label}_socket_sketch").
    Mating faces = feature faces not parallel to the sketch normal (end caps excluded), tagged by mark_mating
    before chamfer (chamfer rebuilds topology). Convex edges of mating faces get edge_chamfer_mm on each body.
    Returns (tail join feature, socket cut feature, chamfer features; [] if no convex edges).
    """
    from fusion_kit.features import _mm, chamfer, convex_edges, extrude, face_normal, profile
    from fusion_kit.sketch import to_sketch
    from fusion_kit.timeline import check_label, set_label

    check_label(label)
    male_pts, female_pts = dovetail_profile(width_mm, depth_mm, angle_deg, clearance_mm)
    _closed_poly(sketch, to_sketch(sketch, *origin_mm), male_pts)
    socket = comp.sketches.add(sketch.referencePlane)
    set_label(socket, f"{label}_socket_sketch")
    _closed_poly(socket, to_sketch(socket, *origin_mm), female_pts)

    length = _mm(length_mm)
    join = extrude(comp, profile(sketch), length, op="join", bodies=[body_male], label=f"{label}_tail")
    cut = extrude(comp, profile(socket), length, op="cut", bodies=[body_female], label=f"{label}_socket")

    axis = _sketch_normal(sketch)

    def mating(feat: adsk.fusion.ExtrudeFeature) -> list[adsk.fusion.BRepFace]:
        out: list[adsk.fusion.BRepFace] = []
        for f in feat.faces:
            n = face_normal(f)
            if not _is_axial((n.x, n.y, n.z), axis):
                out.append(f)
        return out

    tail_faces = mating(join)
    socket_faces = mating(cut)
    mark_mating([*tail_faces, *socket_faces])

    chamfers: list[adsk.fusion.ChamferFeature] = []
    for body, faces, suffix in (
        (body_male, tail_faces, "tail"),
        (body_female, socket_faces, "socket"),
    ):
        convex = convex_edges(body, _unique_edges(faces))
        if convex:
            chamfers.append(
                chamfer(
                    comp,
                    convex,
                    _mm(edge_chamfer_mm),
                    label=f"{label}_{suffix}_chamfer",
                )
            )
    return join, cut, chamfers

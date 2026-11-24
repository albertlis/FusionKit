"""Construction axes/planes and body transforms (move, rotate). Split from fusion_kit.features."""

from collections.abc import Callable
from typing import Literal

import adsk.core
import adsk.fusion

from fusion_kit.expr import Ex
from fusion_kit.features_common import (
    _axis_index,
    _delete_previous,
    _root_label,
    _vi,
    _xyz,
    collection,
)
from fusion_kit.timeline import check_label, set_label


def _plane_mm(comp: adsk.fusion.Component, expr: str | Ex) -> tuple[str, float]:
    """(Fusion expression, value in mm). Ex carries its value; a str is evaluated by Fusion (returns cm internal)."""
    if isinstance(expr, Ex):
        if expr.unit != "mm":
            raise ValueError(f"expected mm expression, got unit {expr.unit!r} ({expr.e})")
        return expr.e, expr.v
    return expr, comp.parentDesign.unitsManager.evaluateExpression(expr, "mm") * 10.0


def axis_plane(
    comp: adsk.fusion.Component,
    axis: Literal["x", "y", "z"],
    expr: str,
    value_mm: float,
    *,
    label: str,
) -> adsk.fusion.ConstructionPlane:
    """Live construction plane at model axis = expr (e.g. 'basket_width - wall_thickness').

    Base = origin plane normal to axis; offset sign taken from the base normal (xZ normal is not +Y in every
    doc). value_mm = expr's current value, checked against the created plane.
    """
    idx = "xyz".index(axis)
    base = {"x": comp.yZConstructionPlane, "y": comp.xZConstructionPlane, "z": comp.xYConstructionPlane}[axis]
    bn = base.geometry.normal
    signed = expr if (bn.x, bn.y, bn.z)[idx] > 0 else f"-({expr})"
    inp = comp.constructionPlanes.createInput()
    inp.setByOffset(base, _vi(signed))
    check_label(label)  # before add: no orphan plane on a bad label
    plane = comp.constructionPlanes.add(inp)
    og = plane.geometry.origin
    got = (og.x, og.y, og.z)[idx] * 10.0
    if abs(got - value_mm) > 1e-6:
        raise RuntimeError(f"axis_plane: {axis}={got} mm, want {value_mm} mm ({expr})")
    set_label(plane, label)
    return plane


def _axis_by_planes(
    comp: adsk.fusion.Component,
    names: tuple[Literal["x", "y", "z"], Literal["x", "y", "z"]],
    coords: tuple[str | Ex, str | Ex],
    label: str,
) -> adsk.fusion.ConstructionAxis:
    """Live construction axis = intersection of plane names[0]=coords[0] and plane names[1]=coords[1] (mm).

    Helper planes labelled f'{label}_plane_{axis}'. Rebuild: constructions of the previous call with the same
    label are deleted first (else Fusion renames the new ones 'label (1)' and the timeline reports them as unnamed).
    """
    check_label(label)  # before any plane is created
    ae, av = _plane_mm(comp, coords[0])
    be, bv = _plane_mm(comp, coords[1])
    _delete_previous(comp, label)
    pa = axis_plane(comp, names[0], ae, av, label=f"{label}_plane_{names[0]}")
    pb = axis_plane(comp, names[1], be, bv, label=f"{label}_plane_{names[1]}")
    pa.isLightBulbOn = False  # helper construction: hidden, not a visible reference plane
    pb.isLightBulbOn = False
    inp = comp.constructionAxes.createInput()
    if not inp.setByTwoPlanes(pa, pb):
        raise RuntimeError(f"axis {names}: setByTwoPlanes failed")
    axis = comp.constructionAxes.add(inp)
    axis.name = check_label(label)  # set_label() types exclude ConstructionAxis; same effect
    axis.isLightBulbOn = False
    return axis


def axis_y_through(
    comp: adsk.fusion.Component,
    *,
    x: str | Ex,
    z: str | Ex,
    label: str,
) -> adsk.fusion.ConstructionAxis:
    """Live construction axis parallel to Y through (x, z): plane x=const ∩ plane z=const.

    x, z: mm expressions (str e.g. 'front_louver_seed_x' / '0 mm', or Ex). Value checked by axis_plane.
    Direction along Y is chosen by Fusion (undocumented); measured -Y. rotate_body_about_y reads it at runtime.
    """
    return _axis_by_planes(comp, ("x", "z"), (x, z), label)


def axis_x_through(
    comp: adsk.fusion.Component,
    *,
    y: str | Ex,
    z: str | Ex,
    label: str,
) -> adsk.fusion.ConstructionAxis:
    """Live construction axis parallel to X through (y, z): plane y=const ∩ plane z=const.

    y, z: mm expressions (str or Ex). Same rebuild rules as axis_y_through.
    Direction along X is chosen by Fusion (undocumented). rotate_body_about_x reads it at runtime.
    """
    return _axis_by_planes(comp, ("y", "z"), (y, z), label)


def axis_z_through(
    comp: adsk.fusion.Component,
    *,
    x: str | Ex,
    y: str | Ex,
    label: str,
) -> adsk.fusion.ConstructionAxis:
    """Live construction axis parallel to Z through (x, y): plane x=const ∩ plane y=const.

    x, y: mm expressions (str or Ex). Same rebuild rules as axis_y_through.
    """
    return _axis_by_planes(comp, ("x", "y"), (x, y), label)


def _body_coll(
    bodies: adsk.fusion.BRepBody | list[adsk.fusion.BRepBody],
) -> adsk.core.ObjectCollection:
    """Single BRepBody or list -> ObjectCollection (moveFeatures takes a collection)."""
    return collection(bodies if isinstance(bodies, list) else [bodies])


def move_body(
    comp: adsk.fusion.Component,
    bodies: adsk.fusion.BRepBody | list[adsk.fusion.BRepBody],
    *,
    dx: str | Ex = "0 mm",
    dy: str | Ex = "0 mm",
    dz: str | Ex = "0 mm",
    label: str,
) -> adsk.fusion.MoveFeature:
    """Translate bodies by dx / dy / dz in design space (live expressions, e.g. '-outer_width/2 - 10 mm')."""
    inp = comp.features.moveFeatures.createInput2(_body_coll(bodies))
    if not inp.defineAsTranslateXYZ(_vi(dx), _vi(dy), _vi(dz), True):
        raise RuntimeError("move_body: defineAsTranslateXYZ failed")
    check_label(label)  # before add: a bad label must not leave an unnamed move feature
    feat = comp.features.moveFeatures.add(inp)
    set_label(feat, label)
    return feat


def rotate_body(
    comp: adsk.fusion.Component,
    bodies: adsk.fusion.BRepBody | list[adsk.fusion.BRepBody],
    *,
    axis: adsk.fusion.ConstructionAxis | adsk.fusion.BRepEdge | adsk.fusion.SketchLine,
    angle: str | Ex,
    label: str,
) -> adsk.fusion.MoveFeature:
    """Rotate bodies about axis (linear BRepEdge / ConstructionAxis / SketchLine) by angle.

    angle: Ex with unit 'deg' or string with explicit unit, e.g. '90 deg' (bare number = document default).
    Positive angle = right-hand rule about the axis entity's natural direction.
    """
    inp = comp.features.moveFeatures.createInput2(_body_coll(bodies))
    if not inp.defineAsRotate(axis, _vi(angle)):
        raise RuntimeError("rotate_body: defineAsRotate failed")
    check_label(label)  # before add
    feat = comp.features.moveFeatures.add(inp)
    set_label(feat, label)
    return feat


def _rotate_about_root_axis(
    comp: adsk.fusion.Component,
    bodies: adsk.fusion.BRepBody | list[adsk.fusion.BRepBody],
    axis_fn: Callable[..., adsk.fusion.ConstructionAxis],
    along: Literal["x", "y"],
    *,
    label: str,
    angle: str | Ex,
    **coords: str | Ex,
) -> adsk.fusion.MoveFeature:
    """Shared body of rotate_body_about_x / _y: axis from axis_fn, move in comp, angle sign from the built axis.

    Live 2026-10-21: axis built in a NON-root comp + move in that same comp -> "3 : Invalid entity".
    Root-built axis + move in the sub-comp works (measured). Axis (and its 2 planes) therefore live in root.
    Fusion picks the axis direction (undocumented): angle negated when the built axis points negative along `along`.
    """
    check_label(label)  # before the axis is created
    axis = axis_fn(comp.parentDesign.rootComponent, label=_root_label(comp, label), **coords)
    if _xyz(axis.geometry.direction)[_axis_index(along)] < 0:
        angle = -angle if isinstance(angle, Ex) else f"-({angle})"
    return rotate_body(comp, bodies, axis=axis, angle=angle, label=label)


def rotate_body_about_y(
    comp: adsk.fusion.Component,
    bodies: adsk.fusion.BRepBody | list[adsk.fusion.BRepBody],
    *,
    x: str | Ex,
    z: str | Ex,
    angle: str | Ex,
    label: str,
) -> adsk.fusion.MoveFeature:
    """rotate_body about the Y-parallel axis through (x, z; mm expressions or Ex). Axis labelled f'{label}_axis'.

    Positive angle = right-hand rule about +Y (+X moves toward -Z). Fusion builds the axis with direction -Y
    (measured live 2026-10-21), so the angle is negated when the built axis points -Y.
    """
    return _rotate_about_root_axis(comp, bodies, axis_y_through, "y", label=label, angle=angle, x=x, z=z)


def rotate_body_about_x(
    comp: adsk.fusion.Component,
    bodies: adsk.fusion.BRepBody | list[adsk.fusion.BRepBody],
    *,
    y: str | Ex,
    z: str | Ex,
    angle: str | Ex,
    label: str,
) -> adsk.fusion.MoveFeature:
    """rotate_body about the X-parallel axis through (y, z; mm expressions or Ex). Axis labelled f'{label}_axis'.

    Positive angle = right-hand rule about +X (+Y moves toward +Z). Angle negated when Fusion's built axis points -X.
    Axis built in root, move in comp (same reason as rotate_body_about_y).
    """
    return _rotate_about_root_axis(comp, bodies, axis_x_through, "x", label=label, angle=angle, y=y, z=z)


def offset_plane(
    comp: adsk.fusion.Component,
    base: adsk.fusion.BRepFace | adsk.fusion.ConstructionPlane,
    distance: str | Ex,
    *,
    label: str,
) -> adsk.fusion.ConstructionPlane:
    """Construction plane parallel to base (planar face or plane) at signed distance (expr).

    Use instead of sketching on a face: face sketches auto-project outlines and break profiles.
    # ponytail: offset only, angle plane not here (setByAngle rounds to 0.1 deg; gap OPEN).
    """
    inp = comp.constructionPlanes.createInput()
    inp.setByOffset(base, _vi(distance))
    check_label(label)
    plane = comp.constructionPlanes.add(inp)
    set_label(plane, label)
    return plane

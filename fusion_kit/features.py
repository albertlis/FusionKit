"""Fusion 360 feature creation helpers.

docs/gotchas.md gotchas encoded here
--------------------------------
- extrude / revolve: participantBodies = list(comp.bRepBodies) ALWAYS set for non-new ops.
  Default cut/join hits bodies of ALL components otherwise.
- profile(): uses profileLoops.count, never max(area) — concentric-circle trap:
  max area picks the inner disc, not the outer annulus.
- All distance / angle arguments accept str expression OR Ex (expr.e used for createByString).
"""

import math
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Literal, Protocol

import adsk.core
import adsk.fusion

from fusion_kit._vec import _cross, _unit_strict
from fusion_kit.dfam import BED_CHAMFER_ANGLE_DEG, BedDir, bed_axis, bed_wall_ratio, set_bed
from fusion_kit.enums import (
    IDENTICAL_PATTERN,
    OP_CUT,
    OP_INTERSECT,
    OP_JOIN,
    OP_NEW,
    OPTIMIZED_PATTERN,
    PATTERN_SPACING,
    POINT_INSIDE,
    POINT_ON,
    POSITIVE_EXTENT,
)
from fusion_kit.expr import Ex, as_str
from fusion_kit.sketch import fix_point, new_sketch, rect
from fusion_kit.timeline import check_label, set_label

P3 = adsk.core.Point3D.create

Op = Literal["new", "join", "cut", "intersect"]
CombineOp = Literal["join", "cut", "intersect"]

_OPS: dict[Op, adsk.fusion.FeatureOperations] = {
    "new": OP_NEW,
    "join": OP_JOIN,
    "cut": OP_CUT,
    "intersect": OP_INTERSECT,
}


def _vi(expr: str | Ex) -> adsk.core.ValueInput:
    """ValueInput.createByString from a str expression or Ex."""
    return adsk.core.ValueInput.createByString(as_str(expr))


def collection[Base: adsk.core.Base](items: Iterable[Base]) -> adsk.core.ObjectCollection:
    """ObjectCollection from a python iterable, in order. Fusion APIs take ObjectCollection, not list."""
    coll = adsk.core.ObjectCollection.create()
    for it in items:
        coll.add(it)
    return coll


def _within(p: adsk.fusion.Profile, outer: adsk.fusion.Profile) -> bool:
    """p's bbox inside outer's bbox (cm, tol 1e-6). Concentric nesting passes; side-by-side fails."""
    a, b = p.boundingBox, outer.boundingBox
    tol = 1e-6
    return (
        a.minPoint.x >= b.minPoint.x - tol
        and a.minPoint.y >= b.minPoint.y - tol
        and a.maxPoint.x <= b.maxPoint.x + tol
        and a.maxPoint.y <= b.maxPoint.y + tol
    )


def profile(sketch: adsk.fusion.Sketch, outer: bool = True) -> adsk.fusion.Profile:
    """Select a profile from a sketch.

    outer=True  -> outermost holed profile (largest bbox); single profile returned as-is.
                   Raises ValueError if no holed profile, or if any other profile lies outside
                   the chosen one's bbox (disjoint solid = ambiguous).
                   Profiles nested inside its bbox are accepted (concentric trap, docs/gotchas.md).
                   # ponytail: nesting judged by bbox only; a disc inside the bbox but not in a hole passes.
                   Never max(area) — concentric-circle trap (docs/gotchas.md).
    outer=False -> inner solid (profileLoops.count == 1, smallest area). Raises ValueError if none.
    """
    profs = list(sketch.profiles)
    if len(profs) == 1:
        return profs[0]
    if outer:
        # outermost annulus = holed profile with largest bbox extent (>=3 concentric circles -> several annuli)
        holed = [p for p in profs if p.profileLoops.count > 1]
        if not holed:
            raise ValueError(
                f"profile(outer=True): {len(profs)} disjoint profiles, none holed — ambiguous, pick explicitly"
            )

        def extent(p: adsk.fusion.Profile) -> float:
            bb = p.boundingBox
            return bb.minPoint.distanceTo(bb.maxPoint)

        best = max(holed, key=extent)
        stray = [p for p in profs if p is not best and not _within(p, best)]
        if stray:
            raise ValueError(
                f"profile(outer=True): {len(stray)} profile(s) outside the outermost holed profile — ambiguous, pick explicitly"
            )
        return best
    else:
        singles = [p for p in profs if p.profileLoops.count == 1]
        if not singles:
            raise ValueError(f"profile(outer=False): {len(profs)} profiles, none single-loop — pick explicitly")
        return min(singles, key=lambda p: p.areaProperties().area)


def profiles(
    sketch: adsk.fusion.Sketch,
    predicate: Callable[[adsk.fusion.Profile], bool] | None = None,
) -> list[adsk.fusion.Profile]:
    """All sketch profiles in order, optionally only those where predicate(profile) is True."""
    profs = list(sketch.profiles)
    return profs if predicate is None else [p for p in profs if predicate(p)]


def extrude(
    comp: adsk.fusion.Component,
    prof: adsk.fusion.Profile | list[adsk.fusion.Profile],
    distance: str | Ex,
    op: Op = "new",
    start_offset: str | Ex | None = None,
    symmetric: bool = False,
    full_length: bool = True,
    bodies: list[adsk.fusion.BRepBody] | None = None,
    name: str | None = None,
    taper: str | Ex | None = None,
    *,
    label: str,
) -> adsk.fusion.ExtrudeFeature:
    """Extrude one profile along the sketch normal.

    op           : "new" (new body) | "join" | "cut" | "intersect" (into participant bodies).
                   Non-new ops: participantBodies = bodies if given, else list(comp.bRepBodies)
                   (docs/gotchas.md: default would hit bodies of all components). Ignored for "new".
    prof         : one Profile, or a list -> one feature over all of them ([] -> ValueError).
    distance     : one-side length; with symmetric=True the full or half length (see full_length).
    start_offset : FromEntity start: profile plane offset along the normal before extruding
                   ("-2 mm" starts 2 mm below the sketch plane). None = start on the sketch plane.
    symmetric    : extent centred on the sketch plane (Fusion setSymmetricExtent). Exclusive with start_offset.
    full_length  : symmetric only. True -> distance is the total length; False -> half length per side.
    bodies       : non-new ops only: restrict participants (e.g. [plate_a] to join a seam into one body).
                   [] -> ValueError (None = all component bodies).
    name         : rename the first result body (Fusion names it "Body N"; repeated names clash in callers).
                   op "new" only: for join/cut/intersect Fusion's first result body is the PARTICIPANT,
                   renaming it would clobber that body -> ValueError. 0 result bodies -> RuntimeError.
    taper        : one-side only: draft angle expr ("-46 deg"). Sign vs. shrink measured by caller.
    label        : snake_case timeline name of the FEATURE (name= names the body). Required.
    """
    check_label(label)
    if taper is not None and symmetric:
        raise ValueError("extrude: taper only with one-side extent")
    if symmetric and start_offset is not None:
        raise ValueError("extrude: start_offset and symmetric are mutually exclusive")
    if bodies is not None and len(bodies) == 0:
        raise ValueError("extrude: bodies=[] is empty; pass None for all component bodies")
    if name is not None and op != "new":
        raise ValueError(f"extrude: name only with op 'new' (op {op!r} would rename the participant body)")
    profs = prof if isinstance(prof, list) else [prof]
    if not profs:
        raise ValueError("extrude: prof=[] is empty; need at least one profile")
    coll = collection(profs)
    inp = comp.features.extrudeFeatures.createInput(coll, _OPS[op])
    if op != "new":
        inp.participantBodies = list(comp.bRepBodies) if bodies is None else bodies  # docs/gotchas.md: this component
    if symmetric:
        inp.setSymmetricExtent(_vi(distance), full_length)
    else:
        if start_offset is not None:
            inp.startExtent = adsk.fusion.OffsetStartDefinition.create(_vi(start_offset))
        ext = adsk.fusion.DistanceExtentDefinition.create(_vi(distance))
        if taper is None:
            inp.setOneSideExtent(ext, POSITIVE_EXTENT)
        else:
            inp.setOneSideExtent(ext, POSITIVE_EXTENT, _vi(taper))
    feat = comp.features.extrudeFeatures.add(inp)
    set_label(feat, label)
    if name is not None:
        if feat.bodies.count == 0:
            raise RuntimeError("extrude(name=): feature produced no result bodies, nothing to rename")
        feat.bodies.item(0).name = name
    return feat


def revolve(
    comp: adsk.fusion.Component,
    prof: adsk.fusion.Profile,
    axis: adsk.fusion.ConstructionAxis | adsk.fusion.SketchLine,
    op: Op,
    angle: str | Ex = "360 deg",
    *,
    bodies: list[adsk.fusion.BRepBody] | None = None,
    label: str,
) -> adsk.fusion.RevolveFeature:
    """Revolve a profile around axis.

    op: "new" | "join" | "cut" | "intersect"
    angle: Fusion expression or Ex (default "360 deg" = full revolution).
    bodies: non-new ops only, same as extrude: participants (None = all component bodies; [] -> ValueError).
    label: snake_case timeline name. participantBodies always set for non-new ops (docs/gotchas.md).
    """
    check_label(label)
    if bodies is not None and len(bodies) == 0:
        raise ValueError("revolve: bodies=[] is empty; pass None for all component bodies")
    inp = comp.features.revolveFeatures.createInput(prof, axis, _OPS[op])
    if op != "new":
        inp.participantBodies = list(comp.bRepBodies) if bodies is None else bodies
    inp.setAngleExtent(False, _vi(angle))
    feat = comp.features.revolveFeatures.add(inp)
    set_label(feat, label)
    return feat


def _count(comp: adsk.fusion.Component, expr: str | Ex) -> float:
    """Unitless count expression (number or parameter name) evaluated by Fusion in comp's design."""
    return comp.parentDesign.unitsManager.evaluateExpression(as_str(expr), "")


_CUT_COPLANAR_HINT = "mirror/pattern of cut failed (coplanar faces?) -> put instances as profiles in one sketch"


def _has_cut(operations: Iterable[object], cut_op: object) -> bool:
    """Pure: True when any operation in operations equals cut_op."""
    return any(op == cut_op for op in operations)


def _reraise_if_cut(exc: Exception, inputs: Iterable[object], label: str) -> None:
    """Call from an except block. Raises RuntimeError(hint) chained from exc when an input is a cut ExtrudeFeature.

    Returns normally otherwise; caller then re-raises the original exception.
    """
    ops = [f.operation for f in inputs if isinstance(f, adsk.fusion.ExtrudeFeature)]
    if _has_cut(ops, OP_CUT):
        raise RuntimeError(f"{label}: {_CUT_COPLANAR_HINT}") from exc


def pattern_circular(
    comp: adsk.fusion.Component,
    entities: list[adsk.core.Base],
    axis: adsk.fusion.ConstructionAxis,
    qty: str | Ex,
    total: str | Ex = "360 deg",
    *,
    compute: adsk.fusion.PatternComputeOptions = OPTIMIZED_PATTERN,
    label: str,
) -> adsk.fusion.CircularPatternFeature | None:
    """Circular pattern of features around axis.

    qty: Fusion expression or Ex for count (e.g. "6" or a parameter name).
    total: angular span expression or Ex (default "360 deg").
    compute: default OPTIMIZED_PATTERN (faster). Override ADJUST_PATTERN / IDENTICAL_PATTERN when instances
    end on different faces: Optimized then drops or differs (measured on a real part: Adjust used).
    qty evaluating < 2 -> returns None, no feature (Fusion rejects qty < 2). Param change 1 -> N later
    needs a rebuild (Fusion limit): the pattern does not exist to be updated.
    label: snake_case timeline name (validated even when no feature is made).
    """
    check_label(label)
    if _count(comp, qty) < 2:
        return None
    coll = collection(entities)
    pin = comp.features.circularPatternFeatures.createInput(coll, axis)
    pin.quantity = _vi(qty)
    pin.totalAngle = _vi(total)
    pin.patternComputeOption = compute
    try:
        feat = comp.features.circularPatternFeatures.add(pin)
    except Exception as exc:
        _reraise_if_cut(exc, entities, label)
        raise
    set_label(feat, label)
    return feat


def pattern_rectangular(
    comp: adsk.fusion.Component,
    entities: list[adsk.core.Base],
    axis: adsk.fusion.ConstructionAxis | adsk.fusion.BRepEdge | adsk.fusion.SketchLine,
    qty: str | Ex,
    distance: str | Ex,
    distance_type: adsk.fusion.PatternDistanceType = PATTERN_SPACING,
    *,
    compute: adsk.fusion.PatternComputeOptions = OPTIMIZED_PATTERN,
    label: str,
) -> adsk.fusion.RectangularPatternFeature | None:
    """Rectangular pattern of features/bodies along one direction.

    axis         : construction axis, linear edge or sketch line defining the direction.
    qty          : instance count (expression or Ex, e.g. "4" or a parameter name).
    distance     : PATTERN_SPACING (default) -> gap between elements; PATTERN_EXTENT -> total span.
    compute      : default OPTIMIZED_PATTERN (faster); ADJUST_PATTERN / IDENTICAL_PATTERN to override
                   (Optimized drops or differs when instances end on different faces).
    qty evaluating < 2 -> returns None, no feature (Fusion rejects qty < 2). Param change 1 -> N later
    needs a rebuild (Fusion limit): the pattern does not exist to be updated.
    Direction two is forced to qty 1 internally (Fusion default makes 3 copies per instance); one direction exposed.
    # ponytail: one direction only; add a direction_two arg when a mechanism needs a grid.
    label: snake_case timeline name (validated even when no feature is made).
    """
    check_label(label)
    if _count(comp, qty) < 2:
        return None
    coll = collection(entities)
    rin = comp.features.rectangularPatternFeatures.createInput(coll, axis, _vi(qty), _vi(distance), distance_type)
    # Unset direction two = Fusion default of 3 copies per instance (measured: qty 4 -> 12 bodies).
    # Force qty 1 in direction two; entity = same axis (stub types it as Base, None not allowed).
    rin.setDirectionTwo(axis, _vi("1"), _vi(distance))
    rin.patternComputeOption = compute
    try:
        feat = comp.features.rectangularPatternFeatures.add(rin)
    except Exception as exc:
        _reraise_if_cut(exc, entities, label)
        raise
    set_label(feat, label)
    return feat


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


def pattern_grid(
    comp: adsk.fusion.Component,
    entities: list[adsk.core.Base],
    dir_one: adsk.core.Base,
    qty_one: str | Ex,
    spacing_one: str | Ex,
    dir_two: adsk.core.Base,
    qty_two: str | Ex,
    spacing_two: str | Ex,
    *,
    compute: adsk.fusion.PatternComputeOptions = OPTIMIZED_PATTERN,
    label: str,
) -> adsk.fusion.RectangularPatternFeature:
    """Two-direction rectangular pattern, live: qty / spacing are expressions (e.g. 'max(2; floor(...))').

    Quantity 1 in one direction is allowed (column or row only). compute: default OPTIMIZED_PATTERN (faster);
    ADJUST_PATTERN / IDENTICAL_PATTERN to override (Optimized drops or differs when instances end on different
    faces). IDENTICAL copies every instance from the seed faces (seed must be a constant-depth cut away from
    other features). Direction sign = entity
    direction (verify placement with probes). Unlike pattern_rectangular, no build-time qty check: the
    expression may change later and Fusion re-evaluates it.
    """
    coll = collection(entities)
    rin = comp.features.rectangularPatternFeatures.createInput(
        coll, dir_one, _vi(qty_one), _vi(spacing_one), PATTERN_SPACING
    )
    rin.setDirectionTwo(dir_two, _vi(qty_two), _vi(spacing_two))
    rin.patternComputeOption = compute
    check_label(label)
    try:
        feat = comp.features.rectangularPatternFeatures.add(rin)
    except Exception as exc:
        _reraise_if_cut(exc, entities, label)
        raise
    set_label(feat, label)
    return feat


class _Tokened(Protocol):
    @property
    def entityToken(self) -> str: ...


def _dedupe_tools[Tok: _Tokened](target: _Tokened, tools: Sequence[Tok]) -> list[Tok]:
    """Tools minus duplicates (same entityToken, first occurrence wins) minus the target. Order kept.

    pattern.bodies may repeat a seed; Fusion rejects duplicate combine inputs.
    Raises ValueError when nothing is left (before any Fusion call).
    """
    seen: set[str] = {target.entityToken}
    out: list[Tok] = []
    for tool in tools:
        if tool.entityToken in seen:
            continue
        seen.add(tool.entityToken)
        out.append(tool)
    if not out:
        raise ValueError("combine: no tool bodies left after dedupe by entityToken / target removal")
    return out


def combine(
    comp: adsk.fusion.Component,
    target: adsk.fusion.BRepBody,
    tools: list[adsk.fusion.BRepBody],
    op: CombineOp = "cut",
    keep_tools: bool = False,
    *,
    label: str,
) -> adsk.fusion.CombineFeature:
    """Boolean target <op> tools into target (Fusion combineFeatures).

    op        : "join" | "cut" | "intersect" (CombineOp: type-checked, no runtime guard).
    tools     : deduped by entityToken, target dropped, order kept; ValueError if none left.
    keep_tools: False (default) consumes the tool bodies.
    """
    coll = collection(_dedupe_tools(target, tools))
    inp = comp.features.combineFeatures.createInput(target, coll)
    inp.operation = _OPS[op]
    inp.isKeepToolBodies = keep_tools
    check_label(label)  # before add: keep_tools=False would consume the tools first
    feat = comp.features.combineFeatures.add(inp)
    set_label(feat, label)
    return feat


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


def single_body(comp: adsk.fusion.Component, prefix: str = "") -> adsk.fusion.BRepBody:
    """The one BRepBody of comp whose name starts with prefix ('' = any body). Else RuntimeError.

    The message lists every matched body (name + bbox mm), so a stray body shows up by name, not just by count.
    """
    bodies = [comp.bRepBodies.item(i) for i in range(comp.bRepBodies.count)]
    matched = [b for b in bodies if b.name.startswith(prefix)]
    if len(matched) == 1:
        return matched[0]
    boxes: list[str] = []
    for b in matched:
        bb = b.boundingBox
        boxes.append(
            f"{b.name} X {bb.minPoint.x * 10:.2f}..{bb.maxPoint.x * 10:.2f} "
            f"Y {bb.minPoint.y * 10:.2f}..{bb.maxPoint.y * 10:.2f} "
            f"Z {bb.minPoint.z * 10:.2f}..{bb.maxPoint.z * 10:.2f}"
        )
    listing = ": " + "; ".join(boxes) if boxes else ""
    raise RuntimeError(f"single_body({prefix!r}): {len(matched)} bodies, expected 1{listing}")


def _plane_mm(comp: adsk.fusion.Component, expr: str | Ex) -> tuple[str, float]:
    """(Fusion expression, value in mm). Ex carries its value; a str is evaluated by Fusion (returns cm internal)."""
    if isinstance(expr, Ex):
        if expr.unit != "mm":
            raise ValueError(f"expected mm expression, got unit {expr.unit!r} ({expr.e})")
        return expr.e, expr.v
    return expr, comp.parentDesign.unitsManager.evaluateExpression(expr, "mm") * 10.0


def _owned_by(name: str, label: str) -> bool:
    """Construction made by _axis_by_planes(label): axis 'label', planes 'label_plane_{x|y|z}', each optionally
    with Fusion's duplicate suffix ' (n)'. Exact match: a user plane 'roof_plane_trim' is not owned by 'roof'."""
    return re.fullmatch(rf"{re.escape(label)}(_plane_[xyz])?( \(\d+\))?", name) is not None


def _root_label(comp: adsk.fusion.Component, label: str) -> str:
    """Label of the root helper axis for a rotate in comp. Scoped by comp name: comps sharing a label must not
    share root constructions. Sanitized scope has no '__', so '<scope>__<label>_axis' is unambiguous."""
    scope = re.sub(r"[^a-z0-9]+", "_", comp.name.lower()).strip("_")
    return check_label(f"{scope}__{label}_axis")


def _delete_previous(comp: adsk.fusion.Component, label: str) -> None:
    """Delete constructionAxes / constructionPlanes of comp left by the previous call with the same label."""
    for coll in (comp.constructionAxes, comp.constructionPlanes):
        for i in range(coll.count - 1, -1, -1):
            item = coll.item(i)
            if _owned_by(item.name, label):
                item.deleteMe()


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


def face_normal(face: adsk.fusion.BRepFace, point: adsk.core.Point3D | None = None) -> adsk.core.Vector3D:
    """Face normal at point (default face.pointOnFace) via evaluator.getNormalAtPoint.

    Respects face orientation. face.geometry.normal (Plane) ignores it: reported +Z on a groove bottom (gaps file).
    Raises ValueError if the evaluator returns no normal.
    """
    ok, n = face.evaluator.getNormalAtPoint(face.pointOnFace if point is None else point)
    if not ok:
        raise ValueError("face_normal: evaluator returned no normal (point not on face?)")
    return n


def _xyz(v: adsk.core.Vector3D | adsk.core.Point3D) -> tuple[float, float, float]:
    return (v.x, v.y, v.z)


def faces_by_normal(
    body: adsk.fusion.BRepBody,
    normal: tuple[float, float, float],
    min_dot: float = 0.6,
) -> list[adsk.fusion.BRepFace]:
    """Faces of body whose face_normal (at pointOnFace) has cos >= min_dot with normal.

    normal is normalized here; zero-length -> ValueError. Faces with zero-length evaluator normal are skipped.
    """
    u = _unit_strict(normal)
    out: list[adsk.fusion.BRepFace] = []
    for f in body.faces:
        n = _xyz(face_normal(f))
        ln = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
        if ln < 1e-12:
            continue
        if (n[0] * u[0] + n[1] * u[1] + n[2] * u[2]) / ln >= min_dot:
            out.append(f)
    return out


def _in_face_dir(
    body: adsk.fusion.BRepBody,
    mid: tuple[float, float, float],
    n: tuple[float, float, float],
    t: tuple[float, float, float],
    eps: float,
) -> tuple[float, float, float] | None:
    """Unit in-face direction from the edge (through mid, along t) into the face with normal n. None = ambiguous.

    Candidate unit(n x t) lies in the face plane, perpendicular to the edge; its sign is fixed by containment:
    mid + eps*d is ON the body surface when d points into the face (the face continues there).
    Neither sign ON -> None. No pointOnFace: on a non-convex face (L-shape) it can sit beyond the edge line.
    """
    c = _unit_strict(_cross(n, t))
    for s in (1.0, -1.0):
        d = (s * c[0], s * c[1], s * c[2])
        probe = P3(mid[0] + eps * d[0], mid[1] + eps * d[1], mid[2] + eps * d[2])
        if body.pointContainment(probe) == POINT_ON:
            return d
    return None


def convex_edges(
    body: adsk.fusion.BRepBody,
    edges: Iterable[adsk.fusion.BRepEdge],
    probe_mm: float = 0.1,
) -> list[adsk.fusion.BRepEdge]:
    """Edges of body that are convex creases (interior dihedral < 180 deg).

    Only 2-face edges with non-parallel normals are considered; others are dropped. Per edge, at midpoint M:
      t   = unit(n_A x n_B): edge direction.
      u_X = unit(n_X x t) in-face direction from the edge into face X; sign by containment
            (M + eps*u_X ON body => into X). Neither sign ON => edge dropped (ambiguous).
      P   = M + eps * unit(u_A + u_B), eps = probe_mm (mm -> cm).
      P INSIDE body => convex (the wedge between u_A and u_B is material). P OUTSIDE => concave. POINT_ON => dropped.
    No pointOnFace used (it may sit beyond the edge line on a non-convex face). Raises ValueError if face_normal
    gets no normal at M.
    NOT the bisector of outward normals: that probe is OUTSIDE for convex AND concave corners (L-corner test).
    """
    eps = probe_mm / 10  # mm -> cm
    out: list[adsk.fusion.BRepEdge] = []
    for e in edges:
        faces = list(e.faces)
        if len(faces) != 2:
            continue
        fa, fb = faces
        mid = e.pointOnEdge
        m = _xyz(mid)
        na = _unit_strict(_xyz(face_normal(fa, mid)))
        nb = _unit_strict(_xyz(face_normal(fb, mid)))
        cross = _cross(na, nb)
        if math.sqrt(cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2) < 1e-6:
            continue  # parallel normals: tangent (flat) or folded-back edge, not a convex crease
        t = _unit_strict(cross)
        ua = _in_face_dir(body, m, na, t, eps)
        ub = _in_face_dir(body, m, nb, t, eps)
        if ua is None or ub is None:
            continue  # no containment sign for a face: ambiguous, drop
        s = (ua[0] + ub[0], ua[1] + ub[1], ua[2] + ub[2])
        if math.sqrt(s[0] ** 2 + s[1] ** 2 + s[2] ** 2) < 1e-6:
            continue  # u_A = -u_B: straight through, no crease
        su = _unit_strict(s)
        probe = P3(m[0] + eps * su[0], m[1] + eps * su[1], m[2] + eps * su[2])
        if body.pointContainment(probe) == POINT_INSIDE:
            out.append(e)
    return out


def _xy_hit(lo: tuple[float, float], hi: tuple[float, float], x_mm: float, y_mm: float, tol_mm: float) -> bool:
    """True if XY point (mm) lies in box [lo, hi] (mm) grown by tol_mm on each side. Pure."""
    return lo[0] - tol_mm <= x_mm <= hi[0] + tol_mm and lo[1] - tol_mm <= y_mm <= hi[1] + tol_mm


def edges_at_xy(
    body: adsk.fusion.BRepBody,
    x_mm: float,
    y_mm: float,
    tol_mm: float = 0.05,
) -> list[adsk.fusion.BRepEdge]:
    """Edges of body whose XY bounding box contains (x_mm, y_mm) within tol_mm. Z ignored.

    Bbox test, not distance to edge: a diagonal edge whose bbox covers the point matches too.
    Empty list = no edge there.
    """
    out: list[adsk.fusion.BRepEdge] = []
    for e in body.edges:
        bb = e.boundingBox
        lo = (bb.minPoint.x * 10, bb.minPoint.y * 10)
        hi = (bb.maxPoint.x * 10, bb.maxPoint.y * 10)
        if _xy_hit(lo, hi, x_mm, y_mm, tol_mm):
            out.append(e)
    return out


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


def planar_faces(
    body: adsk.fusion.BRepBody,
    axis: str,
    at_mm: float,
    normal_sign: int | None = None,
    tol_mm: float = 0.01,
) -> list[adsk.fusion.BRepFace]:
    """Planar faces of body with normal parallel to axis and plane at axis == at_mm (+-tol_mm).

    normal_sign +1/-1: keep only faces whose orientation-aware normal (face_normal) points that way.
    Returns ALL matches, area desc: coplanar faces (leftovers after a join) are all returned.
    """
    if normal_sign not in (None, 1, -1):
        raise ValueError(f"planar_faces: normal_sign must be 1, -1 or None, got {normal_sign!r}")
    ax = _axis_index(axis)
    at = at_mm / 10  # mm -> cm
    tol = tol_mm / 10
    out: list[adsk.fusion.BRepFace] = []
    for f in body.faces:
        plane = adsk.core.Plane.cast(f.geometry)
        if plane is None:  # not planar
            continue
        nc = (plane.normal.x, plane.normal.y, plane.normal.z)
        if abs(nc[ax]) <= 1 - 1e-6:  # normal not parallel to axis
            continue
        oc = (plane.origin.x, plane.origin.y, plane.origin.z)
        if abs(oc[ax] - at) > tol:
            continue
        if normal_sign is not None:
            fn = face_normal(f)
            fc = (fn.x, fn.y, fn.z)
            if (1 if fc[ax] > 0 else -1) != normal_sign:
                continue
        out.append(f)
    return sorted(out, key=lambda f: f.area, reverse=True)


def fillet(
    comp: adsk.fusion.Component,
    edges: list[adsk.fusion.BRepEdge],
    radius: str | Ex,
    tangent_chain: bool = False,
    *,
    label: str,
) -> adsk.fusion.FilletFeature:
    """Constant-radius fillet on edges. radius: mm expression or Ex.

    tangent_chain=True also fillets edges tangent-connected to the picked ones (silent spread);
    default False = only the picked edges.
    """
    coll = collection(edges)
    fi = comp.features.filletFeatures.createInput()
    fi.edgeSetInputs.addConstantRadiusEdgeSet(coll, _vi(radius), tangent_chain)
    check_label(label)
    feat = comp.features.filletFeatures.add(fi)
    set_label(feat, label)
    return feat


def chamfer(
    comp: adsk.fusion.Component,
    edges: list[adsk.fusion.BRepEdge],
    distance: str | Ex,
    tangent_chain: bool = False,
    *,
    label: str,
    distance2: str | Ex | None = None,
) -> adsk.fusion.ChamferFeature:
    """Chamfer on edges. distance: mm expression or Ex.

    distance2 None = equal-distance chamfer. distance2 set = two-distance chamfer: distance on one
    adjacent face, distance2 on the other (isFlipped fixed False; no flip retry here).
    tangent_chain=True also chamfers edges tangent-connected to the picked ones; default False.
    """
    coll = collection(edges)
    ci = comp.features.chamferFeatures.createInput2()
    if distance2 is None:
        ci.chamferEdgeSets.addEqualDistanceChamferEdgeSet(coll, _vi(distance), tangent_chain)
    else:
        ci.chamferEdgeSets.addTwoDistancesChamferEdgeSet(coll, _vi(distance), _vi(distance2), False, tangent_chain)
    check_label(label)
    feat = comp.features.chamferFeatures.add(ci)
    set_label(feat, label)
    return feat


def _split_probe[EdgeT](
    edges: Sequence[EdgeT],
    min_len: float,
    length: Callable[[EdgeT], float],
    probe: Callable[[EdgeT], bool],
) -> tuple[list[EdgeT], list[EdgeT], list[EdgeT]]:
    """Partition edges into (accepted, refused, short). short = length < min_len, never probed; others probed once."""
    accepted: list[EdgeT] = []
    refused: list[EdgeT] = []
    short: list[EdgeT] = []
    for e in edges:
        if length(e) < min_len:
            short.append(e)
        elif probe(e):
            accepted.append(e)
        else:
            refused.append(e)
    return accepted, refused, short


def chamfer_greedy(
    comp: adsk.fusion.Component,
    edges: Sequence[adsk.fusion.BRepEdge],
    distance: str | Ex,
    *,
    label: str,
    min_len_mm: float = 0.0,
) -> adsk.fusion.ChamferFeature | None:
    """One chamfer over the edges Fusion accepts. None if no edge accepted.

    Each edge with length >= min_len_mm is probed ALONE: chamfer add + deleteMe, timeline label f"{label}_probe".
    RuntimeError from the probe = refused, edge dropped. Shorter edges skipped unprobed. Then ONE chamfer labelled
    `label` over the accepted edges. RuntimeError from that final chamfer propagates (caller decides, e.g. fallback).
    Per-edge probes, not a growing group: growing group recomputed the whole body each probe (measured 642 s).
    Prints accepted / refused / skipped counts and refused positions (mm). edge.length is cm (Fusion internal).
    """

    def probe(e: adsk.fusion.BRepEdge) -> bool:
        try:
            chamfer(comp, [e], distance, label=f"{label}_probe").deleteMe()
        except RuntimeError:
            return False
        return True

    accepted, refused, short = _split_probe(edges, min_len_mm / 10.0, lambda e: e.length, probe)
    print(f"{label}: accepted {len(accepted)}, refused {len(refused)}, skipped < {min_len_mm} mm {len(short)}")
    for e in refused:
        print(f"{label}: refused at ({e.pointOnEdge.x * 10:.1f}, {e.pointOnEdge.y * 10:.1f}) mm")
    if not accepted:
        return None
    return chamfer(comp, accepted, distance, label=label)


def mirror(
    comp: adsk.fusion.Component,
    feats: Sequence[adsk.fusion.Feature],
    plane: adsk.fusion.ConstructionPlane | adsk.fusion.BRepFace,
    *,
    compute: adsk.fusion.PatternComputeOptions = OPTIMIZED_PATTERN,
    label: str,
) -> adsk.fusion.MirrorFeature:
    """Mirror features about a construction plane or planar face.

    compute: default OPTIMIZED_PATTERN (faster); ADJUST_PATTERN / IDENTICAL_PATTERN to override
    (Optimized drops or differs when instances end on different faces).
    """
    check_label(label)
    feats = list(feats)
    if not feats:
        raise ValueError(f"mirror {label!r}: no features given")
    if all(f.bodies.count == 0 for f in feats):
        # Fusion: NO_TARGET_BODY / ASM_PATTERN_FEATURES_NO_PASTE_INT_EDGES when no feature produced a body
        raise ValueError(f"mirror {label!r}: none of {len(feats)} feature(s) produced a body")
    inp = comp.features.mirrorFeatures.createInput(collection(feats), plane)
    inp.patternComputeOption = compute
    try:
        feat = comp.features.mirrorFeatures.add(inp)
    except Exception as exc:
        _reraise_if_cut(exc, feats, label)
        raise
    set_label(feat, label)
    return feat


def mirror_bodies(
    comp: adsk.fusion.Component,
    bodies: Sequence[adsk.fusion.BRepBody],
    plane: adsk.fusion.ConstructionPlane | adsk.fusion.BRepFace,
    *,
    label: str,
) -> list[adsk.fusion.BRepBody]:
    """Mirror bodies about plane via MirrorFeature (body input, not features). Originals kept.

    Returns the mirrored bodies (feature output). Raises RuntimeError when the feature yields no body.
    """
    check_label(label)
    bodies = list(bodies)
    if not bodies:
        raise ValueError(f"mirror_bodies {label!r}: no bodies given")
    inp = comp.features.mirrorFeatures.createInput(collection(bodies), plane)
    inp.patternComputeOption = OPTIMIZED_PATTERN
    feat = comp.features.mirrorFeatures.add(inp)
    set_label(feat, label)
    out = list(feat.bodies)
    if not out:
        raise RuntimeError(f"mirror_bodies {label!r}: feature produced no body")
    return out


_BED_FLAT_TOL_CM = 1e-4  # bed face flat at min Z: bbox z spread, cm (1 um); one value for both branches


_BED_MAX_NZ = 0.707  # DfAM bed_chamfer overhang limit (docs/dfam_guidelines.md): chamfer face |n_z| must be below this


def _bed_loop_edges(
    body: adsk.fusion.BRepBody, only_largest_bed_face: bool, bed: BedDir, outer: bool
) -> list[adsk.fusion.BRepEdge]:
    """Outer (outer=True) or inner (False) loop edges of the bed face(s): planar faces with outward normal = bed direction, at the body bbox extreme."""
    a, s = bed_axis(bed)
    bb = body.boundingBox
    zb = _xyz(bb.minPoint if s < 0 else bb.maxPoint)[a]
    normal = (float(s) * (a == 0), float(s) * (a == 1), float(s) * (a == 2))
    edges: list[adsk.fusion.BRepEdge] = []
    if only_largest_bed_face:
        beds = [
            f
            for f in faces_by_normal(body, normal, min_dot=0.999)
            if isinstance(f.geometry, adsk.core.Plane)
            and abs(_xyz(f.boundingBox.minPoint)[a] - zb) < _BED_FLAT_TOL_CM
            and abs(_xyz(f.boundingBox.maxPoint)[a] - zb) < _BED_FLAT_TOL_CM
        ]
        if beds:
            bed_face = max(beds, key=lambda f: f.area)
            edges = [e for lp in bed_face.loops if lp.isOuter == outer for e in lp.edges]
    else:
        for f in body.faces:
            if not isinstance(f.geometry, adsk.core.Plane):
                continue
            fb = f.boundingBox
            if abs(_xyz(fb.minPoint)[a] - zb) > _BED_FLAT_TOL_CM or abs(_xyz(fb.maxPoint)[a] - zb) > _BED_FLAT_TOL_CM:
                continue
            for lp in f.loops:
                if lp.isOuter == outer:
                    edges.extend(lp.edges)
    return edges


def _bed_outer_edges(
    body: adsk.fusion.BRepBody, only_largest_bed_face: bool, bed: BedDir = "-z"
) -> list[adsk.fusion.BRepEdge]:
    """Outer-loop edges of the bed face(s)."""
    return _bed_loop_edges(body, only_largest_bed_face, bed, True)


def _bed_inner_edges(
    body: adsk.fusion.BRepBody, only_largest_bed_face: bool, bed: BedDir = "-z"
) -> list[adsk.fusion.BRepEdge]:
    """Inner-loop edges (holes, windows) of the bed face(s)."""
    return _bed_loop_edges(body, only_largest_bed_face, bed, False)


def _bed_dihedral_deg(edge: adsk.fusion.BRepEdge, bed: BedDir) -> float:
    """Interior dihedral (deg) of the solid along a bed edge = 180 - angle(outward bed normal, outward side normal).
    Convex edges only (bed outline convex, see bed_chamfer). Square corner -> 90; 15 deg dovetail wall -> 105."""
    a, s = bed_axis(bed)
    bed_n = (float(s) * (a == 0), float(s) * (a == 1), float(s) * (a == 2))
    sides: list[tuple[float, float, float]] = []
    for f in edge.faces:
        n = f.evaluator.getNormalAtPoint(f.pointOnFace)[1]
        v = (n.x, n.y, n.z)
        if sum(p * q for p, q in zip(v, bed_n)) < 0.999:
            sides.append(v)
    if len(sides) != 1:
        raise RuntimeError(f"bed_chamfer: bed edge has {len(sides)} non-bed faces, expected 1")
    dot = sum(p * q for p, q in zip(sides[0], bed_n))
    return 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def bed_chamfer(
    comp: adsk.fusion.Component,
    body: adsk.fusion.BRepBody,
    distance: str | Ex,
    *,
    label: str,
    only_largest_bed_face: bool = False,
    angle_deg: float = BED_CHAMFER_ANGLE_DEG,
    bed: BedDir = "-z",
    min_edge_mm: float = 0.0,
    inner_loops: bool = False,
) -> adsk.fusion.ChamferFeature:
    """Elephant-foot chamfer on outer loops of the print bed face. Inner loops (holes) stay sharp unless inner_loops.

    min_edge_mm: skip bed edges shorter than this (mm). A ~2 mm ledge between two chamfered edges cannot take a
    1 mm chamfer chain -> ASM_BL_UNFIN_SHEET (side_wall dovetail ledge 1.92 mm, measured); 0 = all edges.
    inner_loops: also chamfer the inner loops (holes, windows) of the bed face(s), same two-distance rule and dihedral
    groups (a hole wall normal to the bed is phi 90), same min_edge_mm. False (default) keeps holes sharp = fits.

    bed = bed face direction ('-z' default = min-Z face; '+x' = X-max face). Stored on body as dfam/bed (set_bed)
    so report() checks the same bed. Chamfer face |n| is read on the bed axis.
    distance = 90-deg-equivalent WALL leg (mm expr or Ex): bed footprint = distance / tan(angle_deg) for every phi; wall leg = distance * bed_wall_ratio(90, angle_deg) / bed_wall_ratio(phi, angle_deg). phi = the
    edge's interior dihedral (90 square corner, 105 for a 15 deg dovetail wall). Edges are grouped by phi: one two-distance
    edge set per group, all in ONE feature.
    angle_deg = chamfer FACE angle FROM HORIZONTAL, must be in (45, 90) exclusive, else ValueError.
    46 (default) -> |n_bed| = cos 46 = 0.695 < 0.707 (DfAM). 45 gives 0.7071 and FAILS, so it is rejected (no equal legs).
    Two-distance sets (addTwoDistancesChamferEdgeSet). The Fusion API does not document which distance lands on the bed
    face, so isFlipped=False is tried first, then isFlipped=True. An orientation is accepted only if every new chamfer
    face's angle acos(|n_bed|) equals angle_deg within 0.5 deg AND |n_bed| < 0.707. Raises RuntimeError otherwise.
    Live (side_wall, bed +x): with one ratio for all edges, the 105 deg faces came out 38.27 deg -> always rejected.

    only_largest_bed_face=False: outer loops of EVERY flat face at min Z.
    only_largest_bed_face=True: outer loop of the LARGEST planar face at min Z with normal -Z only. Use when thin
    walls (e.g. 1.6 mm) stand on the bed: their own bed loops fail with ASM_BL_UNFIN_SHEET under a 1 mm chamfer.
    Only convex bed edges: a concave edge is a silent
    no-op (docs/gotchas.md), so keep the bed outline convex. Raises ValueError if no bed edges found.
    """
    if not 45.0 < angle_deg < 90.0:
        raise ValueError(f"bed_chamfer {label!r}: angle_deg must be in (45, 90) exclusive, got {angle_deg}")
    axis, _sign = bed_axis(bed)
    angle_tol_deg = 0.5  # measured chamfer face angle must equal angle_deg within this (deg)
    side = as_str(distance)
    check_label(label)
    for flip in (False, True):
        edges = _bed_outer_edges(body, only_largest_bed_face, bed)
        if inner_loops:
            edges += _bed_inner_edges(body, only_largest_bed_face, bed)
        if min_edge_mm > 0:
            edges = [e for e in edges if e.length * 10.0 >= min_edge_mm]
        if not edges:
            raise ValueError(f"bed_chamfer: no outer loop edges on the {bed} (bed) face")
        groups: dict[float, list[adsk.fusion.BRepEdge]] = {}
        for e in edges:
            groups.setdefault(round(_bed_dihedral_deg(e, bed), 1), []).append(e)
        ci = comp.features.chamferFeatures.createInput2()
        ratio90 = bed_wall_ratio(90.0, angle_deg)
        for phi, grp in sorted(groups.items()):
            bed_leg = f"({side}) * {ratio90!r}"  # bed footprint = 90-deg footprint of distance, any phi
            wall_leg = f"({side}) * {ratio90 / bed_wall_ratio(phi, angle_deg)!r}"
            ci.chamferEdgeSets.addTwoDistancesChamferEdgeSet(collection(grp), _vi(bed_leg), _vi(wall_leg), flip, False)
        feat = comp.features.chamferFeatures.add(ci)
        nz = [abs(_xyz(f.evaluator.getNormalAtPoint(f.pointOnFace)[1])[axis]) for f in feat.faces]
        angles = [math.degrees(math.acos(min(1.0, v))) for v in nz]
        if nz and max(nz) < _BED_MAX_NZ and all(abs(a - angle_deg) <= angle_tol_deg for a in angles):
            set_label(feat, label)
            set_bed(body, bed)
            return feat
        feat.deleteMe()
    raise RuntimeError(
        f"bed_chamfer {label!r}: no orientation gives chamfer face angle {angle_deg} deg with |n_bed| < {_BED_MAX_NZ}"
    )


def _axis_index(axis: str) -> int:
    """'x'|'y'|'z' (any case) -> 0|1|2; anything else ValueError."""
    i = "xyz".find(axis.lower())
    if i < 0 or len(axis) != 1:
        raise ValueError(f"axis must be 'x'|'y'|'z', got {axis!r}")
    return i


def _in_inner_loop(e: adsk.fusion.BRepEdge) -> bool:
    """True if e bounds a hole, pocket or tube bore (non-outer loop) of any adjacent face.

    Compares tempId: identity is per script run, so never persist or compare across runs.

    Pocket/through-hole rim is geometrically identical to an outer top rim (90 deg convex corner,
    faces maxZ equal), so only topology separates them: the rim sits in an inner loop of the top face.
    """
    return any(not lp.isOuter and e.tempId in {x.tempId for x in lp.edges} for f in e.faces for lp in f.loops)


def select_edges(
    body: adsk.fusion.BRepBody,
    circle_r: float | None = None,
    axis: str | None = None,
    convex_top: bool | None = None,
    tol_mm: float = 1e-3,
    on_plane: tuple[str, float] | None = None,
    face_normal: tuple[float, float, float] | None = None,
    min_dot: float = 0.6,
) -> list[adsk.fusion.BRepEdge]:
    """Filter body edges by geometry predicates. All active filters must pass (AND logic).

    circle_r   : keep circular/arc edges whose radius == circle_r mm (within tol_mm).
    axis       : "x"|"y"|"z" (any case) -> keep straight edges parallel to that axis; else ValueError.
    convex_top : keep horizontal edges (z span <= tol) whose adjacent faces all have maxZ <= the edge's z
                 and that do not bound a hole/pocket/bore (inner loop; a tube's bore rim included). Top rim of a box:
                 True -> 4 edges, even with a pocket or through-hole in the top. Vertical edges are excluded (z span > 0).
    on_plane   : (axis, value_mm) -> keep edges whose whole bbox span on that axis is at that coordinate
                 (+-tol). Edges lying in the plane, not faces: ("z", 0) on a plate returns its top-face edges
                 (plus any other edge at z=0). An arc bulging off the plane is excluded.
    face_normal: (x, y, z) -> keep edges with >= 1 adjacent face in faces_by_normal(body, face_normal, min_dot).
    min_dot    : cos threshold for face_normal (default 0.6). Ignored when face_normal is None.
    tol_mm     : geometric tolerance in mm (default 1e-3 mm = 1e-4 cm, the old default).
    """
    tol = tol_mm / 10  # mm -> cm (Fusion internal units)
    normal_ok = None if face_normal is None else {f.tempId for f in faces_by_normal(body, face_normal, min_dot)}
    ax = None if axis is None else _axis_index(axis)
    plane_ax = None if on_plane is None else _axis_index(on_plane[0])
    plane_cm = 0.0 if on_plane is None else on_plane[1] / 10  # mm -> cm
    out: list[adsk.fusion.BRepEdge] = []
    for e in body.edges:
        g = e.geometry
        if circle_r is not None:
            if not isinstance(g, (adsk.core.Circle3D, adsk.core.Arc3D)):
                continue
            if abs(g.radius - circle_r / 10) > tol:  # radius in cm; circle_r in mm
                continue
        if ax is not None:
            if not isinstance(g, adsk.core.Line3D):
                continue
            a, b = g.startPoint, g.endPoint
            d = (abs(a.x - b.x), abs(a.y - b.y), abs(a.z - b.z))
            if any(d[i] > tol for i in range(3) if i != ax):
                continue
        if plane_ax is not None:
            # whole edge in the plane: bbox span on the axis, not just end vertices (an arc bulging off-plane passed)
            bb = e.boundingBox
            lo = (bb.minPoint.x, bb.minPoint.y, bb.minPoint.z)[plane_ax]
            hi = (bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z)[plane_ax]
            if abs(lo - plane_cm) > tol or abs(hi - plane_cm) > tol:
                continue
        if convex_top is not None:
            bb = e.boundingBox
            z = bb.maxPoint.z
            # horizontal only: a vertical edge has maxZ == top of its side faces and used to pass (bug, 8 not 4)
            horizontal = abs(bb.maxPoint.z - bb.minPoint.z) <= tol
            is_ct = horizontal and all(f.boundingBox.maxPoint.z <= z + 1e-6 for f in e.faces) and not _in_inner_loop(e)
            if is_ct != convex_top:
                continue
        if normal_ok is not None and not any(f.tempId in normal_ok for f in e.faces):
            continue
        out.append(e)
    return out


_GROOVE_MIN_SPAN_MM = 35.0  # spans <= this get no relief grooves
_GROOVE_OVERRUN_MM = 1.0  # seed rect runs this far past the body so the cut goes through


def _mm(v: float) -> str:
    """Fixed-point mm expression (no scientific notation: Fusion expressions reject '1e-05 mm')."""
    return f"{v:.6f} mm"


def groove_positions(length_mm: float, pitch_mm: float = 30.0) -> list[float]:
    """Groove centres along one span, symmetric about 0, spacing pitch_mm.

    Count = floor(length / pitch), at least 1; span <= 35 mm -> []. Caller offsets by the body centre.
    """
    if length_mm <= _GROOVE_MIN_SPAN_MM:
        return []
    n = max(1, math.floor(length_mm / pitch_mm))
    return [(i - (n - 1) / 2) * pitch_mm for i in range(n)]


def _seed_rect(
    s: adsk.fusion.Sketch,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    bed_dir: BedDir = "-z",
    bed_mm: float = 0.0,
) -> None:
    """Seed groove rectangle on the bed plane (mm). (x0, x1) / (y0, y1): MODEL coords along the bed's two in-plane
    axes, lower axis index first ('-z' -> x, y; '+x' -> y, z). bed_mm = bed plane coordinate on its normal axis.

    Corners are MODEL points: rect() and fix_point() take model coords and convert through the sketch (live probe:
    yZ sketch x = -Z, xZ sketch y = -Z, xY as model). Both corners dimensioned from the sketch origin with
    unsigned dims: rect_xy() dims corner-to-corner and the solver's sign pick flipped the far edge (live X seed,
    centre 14.2 instead of 15). rect() corners stay jittered; the solver snaps them.
    """
    a, _ = bed_axis(bed_dir)
    pa, pb = [k for k in range(3) if k != a]
    c0: list[float] = [0.0, 0.0, 0.0]
    c1: list[float] = [0.0, 0.0, 0.0]
    c0[a] = c1[a] = bed_mm
    c0[pa], c0[pb] = x0, y0
    c1[pa], c1[pb] = x1, y1
    bottom, _, top, _ = rect(s, (c0[0], c0[1], c0[2]), (c1[0], c1[1], c1[2]))
    fix_point(s, bottom.startSketchPoint, (_mm(c0[0]), _mm(c0[1]), _mm(c0[2])))
    fix_point(s, top.startSketchPoint, (_mm(c1[0]), _mm(c1[1]), _mm(c1[2])))


# Model axes by letter, typed for axis_plane / axis_*_through.
_AXES: tuple[Literal["x", "y", "z"], ...] = ("x", "y", "z")


def _relief_family(
    comp: adsk.fusion.Component,
    bed: adsk.fusion.ConstructionPlane,
    body: adsk.fusion.BRepBody,
    along: Literal["x", "y", "z"],
    *,
    label: str,
    width_mm: float,
    depth_mm: float,
    pitch_mm: float,
    bed_dir: BedDir = "-z",
    depth_sign: int = 1,
    bed_mm: float = 0.0,
) -> list[adsk.fusion.Feature]:
    """Grooves spaced along `along` (an in-plane axis of bed_dir), each through the full span of the other in-plane
    axis: seed cut + rectangular pattern. Seed rect in model coords (_seed_rect).
    depth_sign: +1 if the extrude of depth_mm goes into the body, -1 if the plane normal points out of it.
    bed_mm: bed plane coordinate (mm) on the bed normal axis."""
    a, _ = bed_axis(bed_dir)
    i = "xyz".index(along)  # spacing axis
    if i == a:
        raise ValueError(f"warp groove family {along!r} is normal to bed {bed_dir!r}")
    j = 3 - a - i  # groove run axis: the other in-plane axis
    bb = body.boundingBox
    lo_i, hi_i = getattr(bb.minPoint, _AXES[i]) * 10, getattr(bb.maxPoint, _AXES[i]) * 10  # cm -> mm
    lo_j, hi_j = getattr(bb.minPoint, _AXES[j]) * 10, getattr(bb.maxPoint, _AXES[j]) * 10
    centres = groove_positions(hi_i - lo_i, pitch_mm)
    if not centres:
        return []
    first = (lo_i + hi_i) / 2 + centres[0]  # seed = first centre; pattern adds +pitch per instance
    u0, u1 = first - width_mm / 2, first + width_mm / 2
    v0, v1 = lo_j - _GROOVE_OVERRUN_MM, hi_j + _GROOVE_OVERRUN_MM
    iv = {i: (u0, u1), j: (v0, v1)}  # model intervals per in-plane axis
    pa, pb = [k for k in range(3) if k != a]  # lower in-plane axis first, as _seed_rect expects
    (x0, x1), (y0, y1) = iv[pa], iv[pb]
    s = new_sketch(comp, bed, label=f"{label}_{along}_sketch")
    _seed_rect(s, x0, y0, x1, y1, bed_dir=bed_dir, bed_mm=bed_mm)
    seed = extrude(comp, profile(s), _mm(depth_sign * depth_mm), op="cut", bodies=[body], label=f"{label}_{along}_seed")
    if along == "x":
        axis = axis_x_through(comp, y="0 mm", z="0 mm", label=f"{label}_x_axis")
    elif along == "y":
        axis = axis_y_through(comp, x="0 mm", z="0 mm", label=f"{label}_y_axis")
    else:
        axis = axis_z_through(comp, x="0 mm", y="0 mm", label=f"{label}_z_axis")
    # Pattern steps +pitch along the built axis. Fusion picks its direction (measured -Y on axis_y_through):
    # negative along the spacing axis -> negate pitch, else copies land outside the body. Seed stays at lowest centre.
    step = -pitch_mm if _xyz(axis.geometry.direction)[i] < 0 else pitch_mm
    out: list[adsk.fusion.Feature] = [seed]
    # IDENTICAL, not default OPTIMIZED: with the other family present, OPTIMIZED truncated the body (live: vol 43.7 -> 20.5).
    pat = pattern_rectangular(
        comp, [seed], axis, str(len(centres)), _mm(step), compute=IDENTICAL_PATTERN, label=f"{label}_{along}_pattern"
    )
    if pat is not None:
        out.append(pat)
    return out


def warp_relief_grooves(
    comp: adsk.fusion.Component,
    body: adsk.fusion.BRepBody,
    *,
    label: str,
    width_mm: float = 0.8,
    depth_mm: float = 0.3,
    pitch_mm: float = 30.0,
    bed: BedDir = "-z",
) -> list[adsk.fusion.Feature]:
    """Anti-warp relief grooves cut into body from its print bed face (bed = BedDir; '-z' default = min Z of bbox).

    Two families = the two axes in the bed plane, full span. Per family: one seed groove (width_mm wide, depth_mm deep)
    + pattern_rectangular over groove_positions of that family's span, spacing pitch_mm. Stores bed on body (set_bed).
    Labels: <label>_bed, <label>_{a,b}_{sketch,seed,pattern}, <label>_{a,b}_axis (a, b = the in-plane axes).
    """
    check_label(label)
    a, sgn = bed_axis(bed)
    corner = body.boundingBox.minPoint if sgn < 0 else body.boundingBox.maxPoint
    bed_mm = getattr(corner, _AXES[a]) * 10  # cm -> mm
    plane = axis_plane(comp, _AXES[a], _mm(bed_mm), bed_mm, label=f"{label}_bed")
    # cut runs from the bed INTO the body = -sign * e_a; the plane normal sign comes from the base plane
    depth_sign = 1 if getattr(plane.geometry.normal, _AXES[a]) * -sgn > 0 else -1
    set_bed(body, bed)
    fams: list[Literal["x", "y", "z"]] = [ax for k, ax in enumerate(_AXES) if k != a]
    return [
        *_relief_family(
            comp,
            plane,
            body,
            fams[0],
            label=label,
            width_mm=width_mm,
            depth_mm=depth_mm,
            pitch_mm=pitch_mm,
            bed_dir=bed,
            depth_sign=depth_sign,
            bed_mm=bed_mm,
        ),
        *_relief_family(
            comp,
            plane,
            body,
            fams[1],
            label=label,
            width_mm=width_mm,
            depth_mm=depth_mm,
            pitch_mm=pitch_mm,
            bed_dir=bed,
            depth_sign=depth_sign,
            bed_mm=bed_mm,
        ),
    ]

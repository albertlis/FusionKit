"""Circular, rectangular and grid patterns, mirror, combine. Split from fusion_kit.features."""

from collections.abc import Iterable, Sequence
from typing import Protocol

import adsk.core
import adsk.fusion

from fusion_kit.enums import (
    OP_CUT,
    OPTIMIZED_PATTERN,
    PATTERN_SPACING,
)
from fusion_kit.expr import Ex, as_str
from fusion_kit.features_common import (
    _OPS,
    CombineOp,
    _vi,
    collection,
)
from fusion_kit.timeline import check_label, set_label


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
    always: bool = False,
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
    always       : True -> skip the qty < 2 early return and always add the feature (qty may be 1). For a
                   pattern that must exist so a later live qty change works (e.g. count 0 -> N). Fusion
                   may still reject qty < 2 at add; that error propagates.
    Direction two is forced to qty 1 internally (Fusion default makes 3 copies per instance); one direction exposed.
    # ponytail: one direction only; add a direction_two arg when a mechanism needs a grid.
    label: snake_case timeline name (validated even when no feature is made).
    """
    check_label(label)
    if not always and _count(comp, qty) < 2:
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

    tools deduped by entityToken, target dropped; ValueError if none left
    """
    coll = collection(_dedupe_tools(target, tools))
    inp = comp.features.combineFeatures.createInput(target, coll)
    inp.operation = _OPS[op]
    inp.isKeepToolBodies = keep_tools
    check_label(label)  # before add: keep_tools=False would consume the tools first
    feat = comp.features.combineFeatures.add(inp)
    set_label(feat, label)
    return feat


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

    returns mirrored bodies
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

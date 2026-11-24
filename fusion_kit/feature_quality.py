"""Feature-quality rules: timeline smells that a more specific Fusion feature replaces.

Live entry: check(design) -> ["SKQ_WARN <rule>: <feature name> <detail>", ...]. All WARN, nothing blocks.
Rules are plain functions over duck-typed Fusion objects (objectType, named attributes), so tests run
with MagicMock / SimpleNamespace. Fusion lengths are cm; every detail string is mm.
Opt-out: attribute group "skq", name "skq_skip" on the feature entity, value "<rule>:<reason>"
(several rules: separated by ';'). Reason required.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from itertools import groupby
from typing import TYPE_CHECKING, Any

from fusion_kit._vec import _dot, _sub, _unit
from fusion_kit.dfam import parse_skip

if TYPE_CHECKING:  # annotations only; adsk stays unimported at runtime
    import adsk.fusion

ATTR_GROUP = "skq"
ATTR_NAME = "skq_skip"
EXTENT_TOL_MM = 0.01  # end-face coplanarity / bbox overshoot / plane-midpoint tolerance (+/-0.01 mm)
SHALLOW_DEPTH_MM = 1.0  # extrude / cut shallower than this = intentional (grooves, chamfers): no WARN
CM_TO_MM = 10.0
CUT_OPERATION = 1  # adsk FeatureOperations.CutFeatureOperation
PARALLEL_MIN_DOT = 0.999  # |n1 . n2| above this = parallel planes

RULES = (
    "extent_distance_matches_body",
    "cut_blind_through",
    "twin_extrudes",
    "hole_as_extrude",
    "plane_offset_expr",
    "edge_by_edge",
    "thin_as_offset",
)

Point = tuple[float, float, float]
PlaneMm = tuple[Point, Point]  # (origin mm, normal)
Entry = tuple[str, Any]  # (timeline name, entity)
SingleRule = Callable[[Any, list[Entry]], str | None]


# ---------------------------------------------------------------------------
# plumbing: duck-typed access, opt-out attribute
# ---------------------------------------------------------------------------
def _type(obj: Any) -> str:
    """adsk objectType 'adsk::fusion::ExtrudeFeature' -> 'ExtrudeFeature'; '' when unknown."""
    t = getattr(obj, "objectType", None)
    return t.split("::")[-1] if isinstance(t, str) else ""


def _items(coll: Any) -> list[Any]:
    """Collection (count/item) or list -> list; None -> []; a single object -> [obj]."""
    if coll is None:
        return []
    if isinstance(coll, (list, tuple)):
        return list(coll)
    count = getattr(coll, "count", None)
    if isinstance(count, int) and hasattr(coll, "item"):
        return [coll.item(i) for i in range(count)]
    return [coll]


def _suppressed(ent: Any) -> bool:
    return getattr(ent, "isSuppressed", False) is True


def _parse_skip_value(value: str) -> set[str]:
    """'<rule>:<reason>[;<rule>:<reason>]' -> known rule names with a reason. Malformed parts ignored, never raise."""
    out: set[str] = set()
    for part in value.split(";"):
        try:
            out.update(parse_skip(part))
        except ValueError:
            continue
    return out & set(RULES)


def _skip_rules(ent: Any) -> set[str]:
    attrs = getattr(ent, "attributes", None)
    attr = attrs.itemByName(ATTR_GROUP, ATTR_NAME) if attrs is not None else None
    value = getattr(attr, "value", None)
    return _parse_skip_value(value) if isinstance(value, str) else set()


def _op(ent: Any) -> object:
    return getattr(ent, "operation", None)


# ---------------------------------------------------------------------------
# geometry (pure, mm / unit vectors)
# ---------------------------------------------------------------------------
def _madd(p: Point, v: Point, k: float) -> Point:
    return (p[0] + k * v[0], p[1] + k * v[1], p[2] + k * v[2])


def _end_on_plane(origin_mm: Point, normal: Point, depth_mm: float, planes: list[PlaneMm]) -> bool:
    """True when origin +/- depth * normal lies on a plane parallel to normal (within EXTENT_TOL_MM)."""
    if depth_mm <= EXTENT_TOL_MM:
        return False
    u = _unit(normal)
    for sign in (1.0, -1.0):
        end = _madd(origin_mm, u, sign * depth_mm)
        for p_origin, p_normal in planes:
            pu = _unit(p_normal)
            if abs(_dot(u, pu)) < PARALLEL_MIN_DOT:
                continue
            if abs(_dot(_sub(end, p_origin), pu)) <= EXTENT_TOL_MM:
                return True
    return False


def _far_side_mm(origin_mm: Point, normal: Point, bmin: Point, bmax: Point, sign: float) -> float:
    """Distance (mm) from origin to the bbox corner farthest along sign * normal; negative = behind origin."""
    u = _unit(normal)
    corners = [(x, y, z) for x in (bmin[0], bmax[0]) for y in (bmin[1], bmax[1]) for z in (bmin[2], bmax[2])]
    return max(sign * _dot(_sub(c, origin_mm), u) for c in corners)


def _plane_mm(plane: Any) -> PlaneMm:
    o, n = plane.origin, plane.normal
    return ((o.x * CM_TO_MM, o.y * CM_TO_MM, o.z * CM_TO_MM), (n.x, n.y, n.z))


def _point_mm(p: Any) -> Point:
    return (p.x * CM_TO_MM, p.y * CM_TO_MM, p.z * CM_TO_MM)


# ---------------------------------------------------------------------------
# readers of Fusion entities (duck-typed)
# ---------------------------------------------------------------------------
def _is_two_sided(ent: Any) -> bool:
    return getattr(ent, "extentTwo", None) is not None


def _signed_distance_mm(extent: Any) -> float | None:
    """DistanceExtentDefinition -> signed distance in mm; any other extent -> None."""
    if _type(extent) != "DistanceExtentDefinition":
        return None
    return float(extent.distance.value) * CM_TO_MM


def _profiles(ent: Any) -> list[Any]:
    """Sketch profiles of the feature; [] when any is not a sketch Profile (e.g. extrude of a BRepFace)."""
    profs = _items(getattr(ent, "profile", None))
    return profs if all(_type(p) == "Profile" for p in profs) else []


def _profile_plane_mm(ent: Any) -> PlaneMm | None:
    profs = _profiles(ent)
    if not profs:
        return None
    return _plane_mm(profs[0].plane)


def _profile_tokens(ent: Any) -> frozenset[str]:
    return frozenset(str(p.entityToken) for p in _profiles(ent))


def _profile_curves(ent: Any) -> list[list[Any]]:
    """One list of ProfileCurve per profile loop, over every profile of the feature."""
    return [_items(loop.profileCurves) for prof in _profiles(ent) for loop in _items(prof.profileLoops)]


def _bodies_box_mm(ent: Any) -> tuple[Point, Point] | None:
    """Combined bbox (mm) of the feature's bodies; None when no bodies."""
    boxes = [body.boundingBox for body in _items(getattr(ent, "bodies", None))]
    if not boxes:
        return None
    mins = [_point_mm(b.minPoint) for b in boxes]
    maxs = [_point_mm(b.maxPoint) for b in boxes]
    lo: Point = (min(p[0] for p in mins), min(p[1] for p in mins), min(p[2] for p in mins))
    hi: Point = (max(p[0] for p in maxs), max(p[1] for p in maxs), max(p[2] for p in maxs))
    return lo, hi


def _token(obj: Any) -> str:
    return str(getattr(obj, "entityToken", id(obj)))


def _own_face_tokens(ent: Any) -> set[str]:
    """entityTokens of the faces this feature itself created (start/end/side faces); missing attr -> none."""
    return {
        _token(face) for attr in ("startFaces", "endFaces", "sideFaces") for face in _items(getattr(ent, attr, None))
    }


def _earlier_planes_mm(earlier: list[Entry], own_faces: set[str]) -> list[PlaneMm]:
    """Planes of construction planes and planar faces of earlier features' bodies, minus faces this feature created.

    An earlier feature reports the final state of a shared body, so its body faces include this feature's own end
    cap (join/cut into the same body). own_faces = entityTokens of this feature's start/end/side faces -> only those
    faces are skipped; other faces of the same body (and of other bodies) still count.
    """
    planes: list[PlaneMm] = []
    for _, ent in earlier:
        if _type(ent) == "ConstructionPlane":
            planes.append(_plane_mm(ent.geometry))
        for body in _items(getattr(ent, "bodies", None)):
            for face in _items(getattr(body, "faces", None)):
                if _token(face) in own_faces:
                    continue
                geom = face.geometry
                if _type(geom) == "Plane":
                    planes.append(_plane_mm(geom))
    return planes


# ---------------------------------------------------------------------------
# rules: one function per rule, returns detail text (mm) or None
# ---------------------------------------------------------------------------
def rule_extent_distance_matches_body(ent: Any, earlier: list[Entry]) -> str | None:
    """One-side distance extrude whose end lies on an earlier face/plane -> to-object extent.

    -> detail or None. One-side Distance extrude whose end (either sign) lies within 0.01 mm of a
    parallel plane of an earlier construction plane / body face -> use to= (ToEntityExtent). Plane-
    level only, face extent not checked. Faces the feature itself created
    (startFaces/endFaces/sideFaces, matched by entityToken) are ignored (own end cap); other faces
    of the same body and other bodies count. Pure over duck-typed entities
    """
    if _type(ent) != "ExtrudeFeature" or _is_two_sided(ent):
        return None
    dist = _signed_distance_mm(ent.extentOne)
    plane = _profile_plane_mm(ent)
    if dist is None or plane is None or abs(dist) < SHALLOW_DEPTH_MM:
        return None
    if not _end_on_plane(plane[0], plane[1], abs(dist), _earlier_planes_mm(earlier, _own_face_tokens(ent))):
        return None
    return f"distance {abs(dist):.2f} mm ends on an earlier face/plane -> use to= (ToEntityExtent)"


def rule_cut_blind_through(ent: Any, earlier: list[Entry]) -> str | None:
    """Blind cut whose depth runs past the far side of the cut body -> through_all.

    -> detail or None. Cut Distance deeper than the far side of the feature's body bbox (sign from
    distance) -> through_all. Pure
    """
    if _type(ent) != "ExtrudeFeature" or _op(ent) != CUT_OPERATION or _is_two_sided(ent):
        return None
    dist = _signed_distance_mm(ent.extentOne)
    plane = _profile_plane_mm(ent)
    box = _bodies_box_mm(ent)
    if dist is None or dist == 0 or plane is None or box is None or abs(dist) < SHALLOW_DEPTH_MM:
        return None
    sign = 1.0 if dist > 0 else -1.0
    far = _far_side_mm(plane[0], plane[1], box[0], box[1], sign)
    if abs(dist) <= far + EXTENT_TOL_MM:  # strict: overshoot past the body bbox must exceed the tolerance
        return None
    return f"cut {abs(dist):.2f} mm runs past body end at {far:.2f} mm -> through_all"


def rule_twin_extrudes(ent: Any, earlier: list[Entry]) -> str | None:
    """Two one-side distance extrudes from the same profile in opposite directions -> symmetric / two sides.

    -> detail or None. Two one-side Distance extrudes, same profile token set, opposite distance
    sign -> symmetric / two sides. Direction is inferred from the distance sign only (no direction
    property on ExtrudeFeature)
    """
    if _type(ent) != "ExtrudeFeature" or _is_two_sided(ent):
        return None
    dist = _signed_distance_mm(ent.extentOne)
    tokens = _profile_tokens(ent)
    if dist is None or dist == 0 or not tokens:
        return None
    for other_name, other in earlier:
        if _type(other) != "ExtrudeFeature" or _is_two_sided(other):
            continue
        other_dist = _signed_distance_mm(other.extentOne)
        if other_dist is None or other_dist == 0:
            continue
        if (dist > 0) != (other_dist > 0) and _profile_tokens(other) == tokens:
            return f"opposite-sign extrude of same profile as {other_name} -> symmetric / two sides"
    return None


def rule_hole_as_extrude(ent: Any, earlier: list[Entry]) -> str | None:
    """Cut extrude of a single circle profile -> holes.hole_simple / holes.hole_counterbore.

    -> detail or None. Cut extrude with one profile, one loop, one SketchCircle curve ->
    holes.hole_simple / holes.hole_counterbore. Pure
    """
    if _type(ent) != "ExtrudeFeature" or _op(ent) != CUT_OPERATION:
        return None
    loops = _profile_curves(ent)
    if len(_profiles(ent)) != 1 or len(loops) != 1:
        return None
    if [_type(c.sketchEntity) for c in loops[0]] == ["SketchCircle"]:
        return "cut of a single circle profile -> holes.hole_simple / holes.hole_counterbore"
    return None


def _has_half_offset(expr: str) -> bool:
    """Expression divides by exactly 2 ('h/2', 'w / 2'); '/2.5' and '/20' do not match."""
    return re.search(r"/\s*2(?![\d.])", expr) is not None


def _reference_offsets_mm(ent: Any, earlier: list[Entry], u: Point) -> list[float]:
    """Positions (mm) along unit normal u of reference planes that ent could sit midway between.

    Origin plane at 0 mm (only when u is axis-aligned: origin planes are X/Y/Z) plus earlier ConstructionPlanes of
    the same component. Position = dot(plane origin, u), so the sign of a candidate's normal does not matter.
    """
    offsets = [0.0] if max(abs(c) for c in u) >= PARALLEL_MIN_DOT else []
    component = _token(getattr(ent, "parentComponent", None))
    for _, other in earlier:
        if _type(other) != "ConstructionPlane" or _token(getattr(other, "parentComponent", None)) != component:
            continue
        other_origin, other_normal = _plane_mm(other.geometry)
        if abs(_dot(_unit(other_normal), u)) < PARALLEL_MIN_DOT:
            continue
        offsets.append(_dot(other_origin, u))
    return offsets


def _has_midpoint_pair(mid_mm: float, offsets: list[float]) -> bool:
    """True when two offsets a != b exist with (a + b) / 2 within EXTENT_TOL_MM of mid_mm."""
    return any(
        abs(a - b) > EXTENT_TOL_MM and abs((a + b) / 2 - mid_mm) <= EXTENT_TOL_MM
        for i, a in enumerate(offsets)
        for b in offsets[i + 1 :]
    )


def rule_plane_offset_expr(ent: Any, earlier: list[Entry]) -> str | None:
    """Offset construction plane: expression halves a distance AND the plane lies midway between two reference planes.

    -> detail or None. ConstructionPlane offset definition whose offset expression divides by
    exactly 2 -> plane_midplane. Pure
    """
    if _type(ent) != "ConstructionPlane":
        return None
    defn: Any = getattr(ent, "definition", None)
    if _type(defn) != "ConstructionPlaneOffsetDefinition":
        return None
    expr = defn.offset.expression
    if not _has_half_offset(expr):
        return None
    origin, normal = _plane_mm(ent.geometry)
    u = _unit(normal)
    if not _has_midpoint_pair(_dot(origin, u), _reference_offsets_mm(ent, earlier, u)):
        return None
    return f"offset '{expr}' is half a distance -> plane_midplane (Midplane)"


def _edge_key(ent: Any) -> tuple[str, str] | None:
    """(feature type, expression) for a single-edge-set fillet (radius) or chamfer (distance); else None."""
    kind = _type(ent)
    sets = _items(getattr(ent, "edgeSets", None))
    if len(sets) != 1:
        return None
    if kind == "FilletFeature" and _type(sets[0]) == "ConstantRadiusFilletEdgeSet":
        return (kind, sets[0].radius.expression)
    if kind == "ChamferFeature" and _type(sets[0]) == "EqualDistanceChamferEdgeSet":
        return (kind, sets[0].distance.expression)
    return None


def _consecutive_runs(keys: list[tuple[str, str] | None]) -> list[tuple[int, int]]:
    """(first, last) index of every run of >= 2 equal non-None keys."""
    runs: list[tuple[int, int]] = []
    pos = 0
    for key, group in groupby(keys):
        n = sum(1 for _ in group)
        if key is not None and n >= 2:
            runs.append((pos, pos + n - 1))
        pos += n
    return runs


def rule_edge_by_edge(entries: list[Entry]) -> list[tuple[str, str]]:
    """Consecutive fillets / chamfers with the same expression -> one edge set + tangent chain.

    Returns (feature name, detail) for every member after the first of each run.

    -> list[(feature name, detail)]. Consecutive single-edge-set fillets (ConstantRadius radius) or
    chamfers (EqualDistance distance) with identical expression -> one edge set + tangent chain;
    reports every member after the first of a run. Opt-out per member. Pure
    """
    keys = [_edge_key(ent) for _, ent in entries]
    hits: list[tuple[str, str]] = []
    for first, last in _consecutive_runs(keys):
        key = keys[first]
        expr = key[1] if key is not None else ""
        for j in range(first + 1, last + 1):
            name, ent = entries[j]
            if "edge_by_edge" in _skip_rules(ent):
                continue
            hits.append((name, f"same {expr} as {entries[j - 1][0]} -> one edge set, tangent chain"))
    return hits


def _offset_wall(profile_tokens: list[str], offset_child_tokens: set[str]) -> bool:
    """At least two profile curves are children of an offset constraint -> wall of a thin extrude."""
    return len(set(profile_tokens) & offset_child_tokens) >= 2


def rule_thin_as_offset(ent: Any, earlier: list[Entry]) -> str | None:
    """Solid extrude whose profile is drawn as offset curves -> thin extrude with thickness param.

    -> detail or None. Non-thin ExtrudeFeature whose profile holds >= 2 curves that are children of
    an OffsetConstraint in its parent sketch -> thin extrude. Pure
    """
    if _type(ent) != "ExtrudeFeature" or getattr(ent, "isThinExtrude", False) is True:
        return None
    tokens = [str(c.sketchEntity.entityToken) for loop in _profile_curves(ent) for c in loop]
    child: set[str] = set()
    for prof in _profiles(ent):
        for con in _items(getattr(prof.parentSketch, "geometricConstraints", None)):
            if _type(con) == "OffsetConstraint":
                child.update(str(cur.entityToken) for cur in con.childCurves)
    if _offset_wall(tokens, child):
        return "profile drawn as offset curves -> thin extrude (thickness param)"
    return None


SINGLE_RULES: tuple[tuple[str, SingleRule], ...] = (
    ("extent_distance_matches_body", rule_extent_distance_matches_body),
    ("cut_blind_through", rule_cut_blind_through),
    ("twin_extrudes", rule_twin_extrudes),
    ("hole_as_extrude", rule_hole_as_extrude),
    ("plane_offset_expr", rule_plane_offset_expr),
    ("thin_as_offset", rule_thin_as_offset),
)


# ---------------------------------------------------------------------------
# live entry
# ---------------------------------------------------------------------------
def check(design: adsk.fusion.Design) -> list[str]:
    """Feature-quality WARN lines over the design timeline (suppressed items and groups skipped).

    -> list[str] — `SKQ_WARN <rule>: <feature> <detail>` lines over the timeline (groups and
    suppressed items skipped; edge_by_edge run pass after the per-item rules). Rules:
    extent_distance_matches_body, cut_blind_through, twin_extrudes, hole_as_extrude,
    plane_offset_expr, edge_by_edge, thin_as_offset. Opt-out attr ("skq","skq_skip")
    `<rule>:<reason>`. Features with a non-sketch (BRepFace) profile skipped. All WARN, blocks
    nothing
    """
    tl = design.timeline
    entries: list[Entry] = []
    for i in range(tl.count):
        it = tl.item(i)
        if it.isGroup or it.entity is None or _suppressed(it.entity):
            continue
        entries.append((it.name, it.entity))
    out: list[str] = []
    for pos, (name, ent) in enumerate(entries):
        skip = _skip_rules(ent)
        for rule, fn in SINGLE_RULES:
            if rule in skip:
                continue
            detail = fn(ent, entries[:pos])
            if detail:
                out.append(f"SKQ_WARN {rule}: {name} {detail}")
    for name, detail in rule_edge_by_edge(entries):
        out.append(f"SKQ_WARN edge_by_edge: {name} {detail}")
    return out

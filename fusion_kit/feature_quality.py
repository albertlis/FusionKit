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
from fusion_kit.dfam_types import DFAM_GROUP, WARP_GROOVE_ATTR

if TYPE_CHECKING:  # annotations only; adsk stays unimported at runtime
    import adsk.fusion

ATTR_GROUP = "skq"
ATTR_NAME = "skq_skip"
ROLE_GROUP = "fk"  # feature attribute: which builder made the feature (features_bed.bed_chamfer tags ROLE_BED_CHAMFER)
ROLE_NAME = "role"
ROLE_BED_CHAMFER = "bed_chamfer"
BED_CHAMFER_MARK = "bed_chamfer"  # label fallback when the role attribute is absent (features built before the tag)
EXTENT_TOL_MM = 0.01  # end-face coplanarity / bbox overshoot / plane-midpoint tolerance (+/-0.01 mm)
SHALLOW_DEPTH_MM = 1.0  # extrude / cut shallower than this = intentional (grooves, chamfers): no WARN
_CM_TO_MM = 10.0
CUT_OPERATION = 1  # adsk FeatureOperations.CutFeatureOperation
NEW_BODY_OPERATION = 3  # adsk FeatureOperations.NewBodyFeatureOperation (stub fusion.py:669)
PARALLEL_MIN_DOT = 0.999  # |n1 . n2| above this = parallel planes

RULES = (
    "extent_distance_matches_body",
    "cut_blind_through",
    "twin_extrudes",
    "hole_as_extrude",
    "plane_offset_expr",
    "edge_by_edge",
    "thin_as_offset",
    "finishing_not_last",
    "finishing_split",
    "warp_not_last",
    "sketch_extrude_is_offset",
    "finish_ops_order",
    "finish_ops_split",
)

Point = tuple[float, float, float]
PlaneMm = tuple[Point, Point]  # (origin mm, normal)
Entry = tuple[str, Any]  # (timeline name, entity)
SingleRule = Callable[[Any, "_Earlier | list[Entry]"], str | None]


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


def _end_on_plane(origin_mm: Point, normal: Point, depth_mm: float, planes: list[tuple[Point, Point]]) -> bool:
    """True when origin +/- depth * normal lies on a plane parallel to normal (within EXTENT_TOL_MM).

    planes: (origin mm, UNIT normal) pairs from _Earlier.planes_excluding; unit normals are cached per face at index
    time, so no per-call normalisation (this loop runs once per earlier plane per extrude: was the O(T*F) hot spot).
    """
    if depth_mm <= EXTENT_TOL_MM:
        return False
    u = _unit(normal)
    for sign in (1.0, -1.0):
        end = _madd(origin_mm, u, sign * depth_mm)
        for p_origin, pu in planes:
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
    return ((o.x * _CM_TO_MM, o.y * _CM_TO_MM, o.z * _CM_TO_MM), (n.x, n.y, n.z))


def _point_mm(p: Any) -> Point:
    return (p.x * _CM_TO_MM, p.y * _CM_TO_MM, p.z * _CM_TO_MM)


# ---------------------------------------------------------------------------
# readers of Fusion entities (duck-typed)
# ---------------------------------------------------------------------------
def _is_two_sided(ent: Any) -> bool:
    return getattr(ent, "extentTwo", None) is not None


def _signed_distance_mm(extent: Any) -> float | None:
    """DistanceExtentDefinition -> signed distance in mm; any other extent -> None."""
    if _type(extent) != "DistanceExtentDefinition":
        return None
    return float(extent.distance.value) * _CM_TO_MM


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


class _Earlier:
    """Running index over the timeline entries already walked (what is known before the current entry).

    add(name, ent) once per entry, after its rules ran. A body's faces are read once per distinct body (entityToken):
    an earlier feature reports the final state of a shared body, so its planes do not change between entries.
    Own-face exclusion is applied at query time (planes_excluding), by entityToken.
    """

    def __init__(self) -> None:
        self._bodies: set[str] = set()
        self.cplanes: list[tuple[str, PlaneMm]] = []  # (parent component token, construction plane)
        self._faces: list[
            tuple[str, PlaneMm, Any, str, Point]
        ] = []  # (face token, plane, face, body token, unit normal) of planar faces seen
        self._cplane_units: list[tuple[Point, Point]] = []  # (origin mm, unit normal) per construction plane
        self._pending: list[tuple[str, Any]] = []  # extrudes not yet keyed by profile (keyed on first twin query)
        self._by_profile: dict[frozenset[str], list[tuple[str, bool]]] = {}  # profile tokens -> (name, distance > 0)

    def add(self, name: str, ent: Any) -> None:
        if _type(ent) == "ConstructionPlane":
            plane = _plane_mm(ent.geometry)
            self.cplanes.append((_token(getattr(ent, "parentComponent", None)), plane))
            self._cplane_units.append((plane[0], _unit(plane[1])))
        for body in _items(getattr(ent, "bodies", None)):
            token = _token(body)
            if token in self._bodies:
                continue
            self._bodies.add(token)
            for face in _items(getattr(body, "faces", None)):
                geom = face.geometry
                if _type(geom) == "Plane":
                    plane = _plane_mm(geom)
                    self._faces.append((_token(face), plane, face, token, _unit(plane[1])))
        if _type(ent) == "ExtrudeFeature":
            self._pending.append((name, ent))

    def extrudes_on_profile(self, tokens: frozenset[str]) -> list[tuple[str, bool]]:
        """(name, distance > 0) of earlier one-side distance extrudes on exactly these profile tokens, timeline order.

        Pending extrudes are keyed here, once each, on the first twin query that follows them (the extent of an
        earlier feature is read only when a twin check needs it).
        """
        for name, ent in self._pending:
            if _is_two_sided(ent):
                continue
            dist = _signed_distance_mm(ent.extentOne)
            if dist is not None and dist != 0:
                self._by_profile.setdefault(_profile_tokens(ent), []).append((name, dist > 0))
        self._pending.clear()
        return self._by_profile.get(tokens, [])

    def planes_excluding(self, own_faces: set[str]) -> list[tuple[Point, Point]]:
        """Construction planes + planar faces of seen bodies, minus faces the current feature created (own_faces).

        own_faces = entityTokens of the current feature's start/end/side faces: its own end cap on a shared body is
        skipped; other faces of the same body (and of other bodies) still count.
        """
        return self._cplane_units + [(p[0], u) for tok, p, _, _, u in self._faces if tok not in own_faces]

    def coplanar_faces(self, plane: PlaneMm, own_faces: set[str], bodies: set[str]) -> list[Any]:
        """Planar faces of the bodies (entityTokens) not in own_faces, lying in plane (parallel, EXTENT_TOL_MM)."""
        return [
            face
            for tok, p, face, body, _ in self._faces
            if body in bodies and tok not in own_faces and _coplanar_mm(plane, p)
        ]


def _index(earlier: _Earlier | list[Entry]) -> _Earlier:
    """_Earlier passed through; a list of (name, entity) -> index built over that list (one-off calls, tests)."""
    if isinstance(earlier, _Earlier):
        return earlier
    idx = _Earlier()
    for name, ent in earlier:
        idx.add(name, ent)
    return idx


# ---------------------------------------------------------------------------
# rules: one function per rule, returns detail text (mm) or None
# ---------------------------------------------------------------------------
def rule_extent_distance_matches_body(ent: Any, earlier: _Earlier | list[Entry]) -> str | None:
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
    if not _end_on_plane(plane[0], plane[1], abs(dist), _index(earlier).planes_excluding(_own_face_tokens(ent))):
        return None
    return f"distance {abs(dist):.2f} mm ends on an earlier face/plane -> use to= (ToEntityExtent)"


def rule_cut_blind_through(ent: Any, earlier: _Earlier | list[Entry]) -> str | None:
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


def rule_twin_extrudes(ent: Any, earlier: _Earlier | list[Entry]) -> str | None:
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
    for other_name, other_positive in _index(earlier).extrudes_on_profile(tokens):
        if (dist > 0) != other_positive:
            return f"opposite-sign extrude of same profile as {other_name} -> symmetric / two sides"
    return None


def rule_hole_as_extrude(ent: Any, earlier: _Earlier | list[Entry]) -> str | None:
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


def _reference_offsets_mm(ent: Any, earlier: _Earlier | list[Entry], u: Point) -> list[float]:
    """Positions (mm) along unit normal u of reference planes that ent could sit midway between.

    Origin plane at 0 mm (only when u is axis-aligned: origin planes are X/Y/Z) plus earlier ConstructionPlanes of
    the same component. Position = dot(plane origin, u), so the sign of a candidate's normal does not matter.
    """
    offsets = [0.0] if max(abs(c) for c in u) >= PARALLEL_MIN_DOT else []
    component = _token(getattr(ent, "parentComponent", None))
    for other_component, (other_origin, other_normal) in _index(earlier).cplanes:
        if other_component != component or abs(_dot(_unit(other_normal), u)) < PARALLEL_MIN_DOT:
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


def rule_plane_offset_expr(ent: Any, earlier: _Earlier | list[Entry]) -> str | None:
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


def rule_thin_as_offset(ent: Any, earlier: _Earlier | list[Entry]) -> str | None:
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


# ---------------------------------------------------------------------------
# finishing ops: fillet / chamfer last on a body, one feature per kind (one-pass state, body by entityToken)
# ---------------------------------------------------------------------------
FINISH_KINDS = ("FilletFeature", "ChamferFeature")
MODIFIER_KINDS = (
    "ExtrudeFeature",
    "RevolveFeature",
    "CombineFeature",
    "RectangularPatternFeature",
    "CircularPatternFeature",
    "PathPatternFeature",
    "MirrorFeature",
)
WARP_MARK = "warp"  # name substring: warp relief grooves (and a warp-labeled finishing op) are exempt


def _is_warp(name: str) -> bool:
    return WARP_MARK in name.lower()


def is_warp_groove(name: str, ent: Any) -> bool:
    """Warp relief groove: features_warp tag (WARP_GROOVE_ATTR in DFAM_GROUP); fallback: name has 'warp'."""
    attrs = getattr(ent, "attributes", None)
    attr = attrs.itemByName(DFAM_GROUP, WARP_GROOVE_ATTR) if attrs is not None else None
    return attr is not None or _is_warp(name)


def _body_tokens(ent: Any) -> list[str]:
    return list(dict.fromkeys(_token(body) for body in _items(getattr(ent, "bodies", None))))


def _is_bed_chamfer(name: str, ent: Any) -> bool:
    """Chamfer from features_bed.bed_chamfer: role attribute fk/role decides when present, else name has bed_chamfer."""
    if _type(ent) != "ChamferFeature":
        return False
    attrs = getattr(ent, "attributes", None)
    attr = attrs.itemByName(ROLE_GROUP, ROLE_NAME) if attrs is not None else None
    value = getattr(attr, "value", None)
    if isinstance(value, str):
        return value == ROLE_BED_CHAMFER
    return BED_CHAMFER_MARK in name


class _Finishing:
    """Running state for finishing_not_last / finishing_split. step() once per entry, in timeline order.

    finishing_not_last: fillet / chamfer on a body, then a modifier (extrude, revolve, combine, pattern, mirror)
    on the same body -> WARN at the modifier. Warp-labeled modifiers (grooves after bed chamfer) do not trigger.
    finishing_split: 2nd fillet or 2nd chamfer feature on the same body -> WARN once, at that 2nd feature.
    Bed chamfers (features_bed.bed_chamfer) are neither first nor 2nd: two-distance sets cannot join chamfer_groups.
    Opt-out: finishing_not_last on the fillet / chamfer, finishing_split on the 2nd feature.
    """

    def __init__(self) -> None:
        self._open: dict[str, list[tuple[str, Any]]] = {}  # body token -> fillet/chamfer not yet followed by a modifier
        self._seen: dict[tuple[str, str], list[str]] = {}  # (body token, kind) -> names of fillet/chamfer features

    def step(self, name: str, ent: Any) -> list[tuple[str, str, str]]:
        """-> (rule, feature name, detail) lines for this entry; updates the running state."""
        kind = _type(ent)
        if is_warp_groove(name, ent):
            return []
        hits: list[tuple[str, str, str]] = []
        if kind in FINISH_KINDS:
            word = "fillet" if kind == "FilletFeature" else "chamfer"
            group_fn = "fillet_groups" if kind == "FilletFeature" else "chamfer_groups"
            bed = _is_bed_chamfer(name, ent)
            for tok in _body_tokens(ent):
                self._open.setdefault(tok, []).append((name, ent))
                if bed:  # still a finishing op for finishing_not_last; never first, never 2nd for finishing_split
                    continue
                names = self._seen.setdefault((tok, kind), [])
                names.append(name)
                if len(names) == 2 and "finishing_split" not in _skip_rules(ent):
                    hits.append(
                        (
                            "finishing_split",
                            name,
                            f"2nd {word} on same body (first: {names[0]}) -> one feature: features.{group_fn}",
                        )
                    )
        elif kind in MODIFIER_KINDS:
            for tok in _body_tokens(ent):
                for fname, fent in self._open.pop(tok, []):
                    if "finishing_not_last" not in _skip_rules(fent):
                        hits.append(
                            (
                                "finishing_not_last",
                                name,
                                f"{fname} ({_type(fent)}) on same body is not last -> fillet/chamfer after {kind}",
                            )
                        )
        return hits


class _WarpLast:
    """warp_not_last: warp relief grooves are the last features of a body (after bed chamfer and every other op).

    One pass: a body token seen with a groove (is_warp_groove) is marked with that groove's name. Every later
    fillet / chamfer / modifier on that body -> WARN at that feature (one line per feature, not per body).
    """

    def __init__(self) -> None:
        self._grooved: dict[str, str] = {}  # body token -> name of the first groove feature on it

    def step(self, name: str, ent: Any) -> list[tuple[str, str, str]]:
        """-> (rule, feature name, detail) lines for this entry; updates the running state."""
        tokens = _body_tokens(ent)
        kind = _type(ent)
        if is_warp_groove(name, ent):  # warp-labeled fillet / chamfer is exempt but marks no groove
            if kind not in FINISH_KINDS:
                for tok in tokens:
                    self._grooved.setdefault(tok, name)
            return []
        if kind not in FINISH_KINDS and kind not in MODIFIER_KINDS:
            return []
        if "warp_not_last" in _skip_rules(ent):
            return []
        for tok in tokens:
            groove = self._grooved.get(tok)
            if groove is not None:
                detail = f"{kind} after warp groove {groove} on same body -> grooves last"
                return [("warp_not_last", name, detail)]
        return []


BODY_OP_KINDS = (
    *MODIFIER_KINDS,
    "HoleFeature",
    "SweepFeature",
    "LoftFeature",
    "ShellFeature",
    "ThreadFeature",
    "SplitBodyFeature",
)


def _body_label(body: Any) -> str:
    """Body name for messages; entity token when the body has no name."""
    name = getattr(body, "name", None)
    return name if isinstance(name, str) and name else _token(body)


def _set_exprs(ent: Any) -> list[str]:
    """Expression of every single-expression edge set: ConstantRadius fillet radius, EqualDistance chamfer distance.
    Whitespace dropped ('0.5 mm' == '0.5mm'). Two-distance / angle sets are not listed."""
    out: list[str] = []
    for es in _items(getattr(ent, "edgeSets", None)):
        if _type(es) == "ConstantRadiusFilletEdgeSet":
            out.append(re.sub(r"\s+", "", es.radius.expression))
        elif _type(es) == "EqualDistanceChamferEdgeSet":
            out.append(re.sub(r"\s+", "", es.distance.expression))
    return out


class _FinishOrder:
    """finish_ops_order: a body-modifying feature (BODY_OP_KINDS) after a fillet / chamfer on the same body.

    Fillets / chamfers are the LAST ops of a body. Bed chamfer counts as a finishing op. Warp-labeled / warp-tagged
    features are exempt (grooves follow the bed chamfer by design). One WARN per offending feature, first body hit.
    Opt-out: finish_ops_order on the modifying feature.
    """

    def __init__(self) -> None:
        self._finished: dict[str, str] = {}  # body token -> "fillet" | "chamfer" of the first finishing op on it

    def step(self, name: str, ent: Any) -> list[tuple[str, str, str]]:
        """-> (rule, feature name, detail) lines for this entry; updates the running state."""
        kind = _type(ent)
        if is_warp_groove(name, ent):
            return []
        if kind in FINISH_KINDS:
            word = "fillet" if kind == "FilletFeature" else "chamfer"
            for tok in _body_tokens(ent):
                self._finished.setdefault(tok, word)
            return []
        if kind not in BODY_OP_KINDS or "finish_ops_order" in _skip_rules(ent):
            return []
        for body in _items(getattr(ent, "bodies", None)):
            word = self._finished.get(_token(body))
            if word is not None:
                detail = f"after {word} on {_body_label(body)} -> move before finishing block (timeline order)"
                return [("finish_ops_order", name, detail)]
        return []


class _FinishSplit:
    """finish_ops_split: two fillets (or two chamfers) on one body with the same radius / distance expression ->
    one feature with all edges. Different expressions are fine. Bed chamfers (two-distance sets, role tag) never
    count. One WARN per extra feature, naming the first one. Opt-out: finish_ops_split on the later feature.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str, str], tuple[str, Any]] = {}  # (body token, kind, expr) -> first (name, entity)

    def step(self, name: str, ent: Any) -> list[tuple[str, str, str]]:
        """-> (rule, feature name, detail) lines for this entry; updates the running state."""
        kind = _type(ent)
        if kind not in FINISH_KINDS or is_warp_groove(name, ent) or _is_bed_chamfer(name, ent):
            return []
        exprs = _set_exprs(ent)
        skip = "finish_ops_split" in _skip_rules(ent)
        hits: list[tuple[str, str, str]] = []
        for tok in _body_tokens(ent):
            for expr in exprs:
                first_name, first_ent = self._seen.setdefault((tok, kind, expr), (name, ent))
                if first_ent is not ent and not skip and not hits:
                    # name field carries "<a>," so the printed line reads "finish_ops_split: <a>, <b> same <expr> -> ..."
                    hits.append(
                        ("finish_ops_split", f"{first_name},", f"{name} same {expr} -> one feature with all edges")
                    )
        return hits


OFFSET_AREA_TOL = 0.01  # profile area vs face area, relative (+/-1%)


def _coplanar_mm(a: PlaneMm, b: PlaneMm) -> bool:
    """Parallel planes (|n . n| >= PARALLEL_MIN_DOT) with a's origin within EXTENT_TOL_MM of b's plane."""
    nb = _unit(b[1])
    if abs(_dot(_unit(a[1]), nb)) < PARALLEL_MIN_DOT:
        return False
    return abs(_dot(_sub(a[0], b[0]), nb)) <= EXTENT_TOL_MM


def rule_sketch_extrude_is_offset(ent: Any, earlier: _Earlier | list[Entry]) -> str | None:
    """Single-profile extrude whose profile is coplanar with an earlier planar body face and has the same outline
    (area within 1%) -> extrude from face / offset face, no sketch.

    -> detail or None. Own faces of the feature are ignored. Only faces of the body the extrude modifies count
    (its bodies: join / cut / intersect); NewBody extrudes are skipped. Profile area is read only when a coplanar
    face exists.
    """
    if _type(ent) != "ExtrudeFeature" or _op(ent) == NEW_BODY_OPERATION:
        return None
    plane = _profile_plane_mm(ent)
    profs = _profiles(ent)
    if plane is None or len(profs) != 1:
        return None
    targets = set(_body_tokens(ent))
    if not targets:
        return None
    faces = _index(earlier).coplanar_faces(plane, _own_face_tokens(ent), targets)
    if not faces:
        return None
    area = profs[0].areaProperties().area
    for face in faces:
        face_area = face.area
        if area > 0 and abs(area - face_area) <= OFFSET_AREA_TOL * face_area:
            return "profile == face -> extrude from face / offset face (no sketch)"
    return None


SINGLE_RULES: tuple[tuple[str, SingleRule], ...] = (
    ("extent_distance_matches_body", rule_extent_distance_matches_body),
    ("cut_blind_through", rule_cut_blind_through),
    ("twin_extrudes", rule_twin_extrudes),
    ("hole_as_extrude", rule_hole_as_extrude),
    ("plane_offset_expr", rule_plane_offset_expr),
    ("thin_as_offset", rule_thin_as_offset),
    ("sketch_extrude_is_offset", rule_sketch_extrude_is_offset),
)


# ---------------------------------------------------------------------------
# live entry
# ---------------------------------------------------------------------------
def check(design: adsk.fusion.Design, over_budget: Callable[[], bool] | None = None) -> list[str]:
    """Feature-quality WARN lines over the design timeline (suppressed items and groups skipped).

    over_budget: polled before each entry; True -> stop, return the lines so far plus
    'SKIP fq budget: <k> of <N> entries not checked' (edge_by_edge pass skipped too).

    -> list[str] — `SKQ_WARN <rule>: <feature> <detail>` lines over the timeline (groups and
    suppressed items skipped; edge_by_edge run pass after the per-item rules). Rules:
    extent_distance_matches_body, cut_blind_through, twin_extrudes, hole_as_extrude,
    plane_offset_expr, edge_by_edge, thin_as_offset, finishing_not_last, finishing_split, warp_not_last,
    sketch_extrude_is_offset, finish_ops_order, finish_ops_split. Opt-out attr ("skq","skq_skip")
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
    index = _Earlier()  # single pass: rules see the running state of entries before the current one
    finishing = _Finishing()
    warp_last = _WarpLast()
    order = _FinishOrder()
    split = _FinishSplit()
    for pos, (name, ent) in enumerate(entries):
        if over_budget is not None and over_budget():
            out.append(f"SKIP fq budget: {len(entries) - pos} of {len(entries)} entries not checked")
            return out
        skip = _skip_rules(ent)
        for rule, fn in SINGLE_RULES:
            if rule in skip:
                continue
            detail = fn(ent, index)
            if detail:
                out.append(f"SKQ_WARN {rule}: {name} {detail}")
        for rule, hit_name, detail in (
            *finishing.step(name, ent),
            *warp_last.step(name, ent),
            *order.step(name, ent),
            *split.step(name, ent),
        ):
            out.append(f"SKQ_WARN {rule}: {hit_name} {detail}")
        index.add(name, ent)
    for name, detail in rule_edge_by_edge(entries):
        out.append(f"SKQ_WARN edge_by_edge: {name} {detail}")
    return out

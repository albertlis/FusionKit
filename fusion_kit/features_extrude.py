"""Profiles, extrude, revolve, shell and construction planes/axes from profiles. Split from fusion_kit.features."""

from collections.abc import Callable

import adsk.core
import adsk.fusion

from fusion_kit.enums import (
    POSITIVE_EXTENT,
)
from fusion_kit.expr import Ex
from fusion_kit.features_common import (
    _OPS,
    Op,
    _delete_previous,
    _vi,
    collection,
)
from fusion_kit.timeline import check_label, set_label


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
    distance: str | Ex | None,
    op: Op = "new",
    start_offset: str | Ex | None = None,
    symmetric: bool = False,
    full_length: bool = True,
    bodies: list[adsk.fusion.BRepBody] | None = None,
    name: str | None = None,
    taper: str | Ex | None = None,
    *,
    label: str,
    to: adsk.core.Base | None = None,
    to_offset: str | Ex | None = None,
    through_all: bool = False,
    two_sides: tuple[str | Ex, str | Ex] | None = None,
    start_from: adsk.core.Base | None = None,
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
    to           : end at a construction plane / face / body (ToEntityExtentDefinition); to_offset = offset from it.
    through_all  : cut/extrude through all (ThroughAllExtentDefinition).
    two_sides    : (side1, side2) distances, one per direction from the profile plane (setTwoSidesExtent).
    start_from   : start at a plane/face (FromEntityStartDefinition, zero offset). Exclusive with start_offset.
                   to / through_all / two_sides replace distance (pass distance=None); they are mutually exclusive.

    distance=None with to/through_all/two_sides; to/through_all/two_sides mutually exclusive,
    symmetric/taper not with them, start_from exclusive with start_offset/symmetric (ValueError)
    """
    check_label(label)
    extent_kinds = [to is not None, through_all, two_sides is not None]
    if sum(extent_kinds) > 1:
        raise ValueError("extrude: to, through_all, two_sides are mutually exclusive")
    special = any(extent_kinds)
    if to_offset is not None and to is None:
        raise ValueError("extrude: to_offset needs to=")
    if special and distance is not None:
        raise ValueError("extrude: distance given with to / through_all / two_sides; pass distance=None")
    if not special and distance is None:
        raise ValueError("extrude: distance required unless to / through_all / two_sides")
    if special and symmetric:
        raise ValueError("extrude: symmetric only with a distance extent")
    if start_from is not None and start_offset is not None:
        raise ValueError("extrude: start_from and start_offset are mutually exclusive")
    if start_from is not None and symmetric:
        raise ValueError("extrude: start_from and symmetric are mutually exclusive")
    if two_sides is not None and (start_offset is not None or start_from is not None):
        raise ValueError("extrude: two_sides starts on the profile plane; drop start_offset / start_from")
    if taper is not None and (symmetric or special):
        raise ValueError("extrude: taper only with one-side distance extent")
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
    if symmetric and distance is not None:
        inp.setSymmetricExtent(_vi(distance), full_length)
    elif two_sides is not None:
        inp.setTwoSidesExtent(
            adsk.fusion.DistanceExtentDefinition.create(_vi(two_sides[0])),
            adsk.fusion.DistanceExtentDefinition.create(_vi(two_sides[1])),
        )
    else:
        if start_offset is not None:
            inp.startExtent = adsk.fusion.OffsetStartDefinition.create(_vi(start_offset))
        if start_from is not None:
            inp.startExtent = adsk.fusion.FromEntityStartDefinition.create(start_from, _vi("0 mm"))
        if to is not None:
            ext = (
                adsk.fusion.ToEntityExtentDefinition.create(to, False)
                if to_offset is None
                else adsk.fusion.ToEntityExtentDefinition.create(to, False, _vi(to_offset))
            )
        elif through_all:
            ext = adsk.fusion.ThroughAllExtentDefinition.create()
        else:
            assert distance is not None  # special is False here, so line 178 guarantees distance; narrows the type
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
    angle: str | Ex | None = None,
    *,
    bodies: list[adsk.fusion.BRepBody] | None = None,
    symmetric: bool = False,
    to: adsk.core.Base | None = None,
    label: str,
) -> adsk.fusion.RevolveFeature:
    """Revolve a profile around axis.

    op: "new" | "join" | "cut" | "intersect"
    angle: Fusion expression or Ex. None = full revolution ("360 deg"). Exclusive with to=.
    symmetric: angle split evenly on both sides of the profile plane (setAngleExtent(True, ...)).
    to: end at a construction plane / face (setOneSideToExtent). Exclusive with angle and symmetric.
    bodies: non-new ops only, same as extrude: participants (None = all component bodies; [] -> ValueError).
    label: snake_case timeline name. participantBodies always set for non-new ops (docs/gotchas.md).

    angle None = 360 deg; to= exclusive with angle/symmetric
    """
    check_label(label)
    if bodies is not None and len(bodies) == 0:
        raise ValueError("revolve: bodies=[] is empty; pass None for all component bodies")
    if to is not None and angle is not None:
        raise ValueError("revolve: to and angle are mutually exclusive")
    if to is not None and symmetric:
        raise ValueError("revolve: to and symmetric are mutually exclusive")
    inp = comp.features.revolveFeatures.createInput(prof, axis, _OPS[op])
    if op != "new":
        inp.participantBodies = list(comp.bRepBodies) if bodies is None else bodies
    if to is not None:
        inp.setOneSideToExtent(to)
    else:
        inp.setAngleExtent(symmetric, _vi("360 deg" if angle is None else angle))
    feat = comp.features.revolveFeatures.add(inp)
    set_label(feat, label)
    return feat


def shell(
    comp: adsk.fusion.Component,
    faces: list[adsk.fusion.BRepFace],
    thickness: str | Ex,
    *,
    label: str,
) -> adsk.fusion.ShellFeature:
    """Hollow the body: remove faces (open faces), wall thickness expression (inside).

    faces: faces to REMOVE (ShellFeatureInput.inputEntities). [] -> ValueError.

    faces = faces to remove; [] -> ValueError
    """
    check_label(label)
    if not faces:
        raise ValueError("shell: faces=[] is empty; pass the faces to open")
    inp = comp.features.shellFeatures.createInput(collection(faces))
    inp.insideThickness = _vi(thickness)
    feat = comp.features.shellFeatures.add(inp)
    set_label(feat, label)
    return feat


def plane_midplane(
    comp: adsk.fusion.Component,
    p1: adsk.core.Base,
    p2: adsk.core.Base,
    *,
    label: str,
) -> adsk.fusion.ConstructionPlane:
    """Live construction plane midway between two planar faces / construction planes (setByTwoPlanes).

    Co-planar inputs -> RuntimeError.

    midway between two planar faces/planes; co-planar -> RuntimeError
    """
    check_label(label)
    inp = comp.constructionPlanes.createInput()
    if not inp.setByTwoPlanes(p1, p2):
        raise RuntimeError("plane_midplane: setByTwoPlanes failed (co-planar inputs?)")
    plane = comp.constructionPlanes.add(inp)
    set_label(plane, label)
    return plane


def axis_by_cylinder(
    comp: adsk.fusion.Component,
    face: adsk.fusion.BRepFace,
    *,
    label: str,
) -> adsk.fusion.ConstructionAxis:
    """Live construction axis coincident with a cylindrical / conical / toroidal face's axis.

    Non-circular face -> RuntimeError. Rebuild: previous constructions with the same label are deleted first.

    axis of cylindrical/conical/toroidal face; other -> RuntimeError
    """
    check_label(label)
    _delete_previous(comp, label)
    inp = comp.constructionAxes.createInput()
    if not inp.setByCircularFace(face):
        raise RuntimeError("axis_by_cylinder: face is not cylindrical / conical / toroidal")
    axis = comp.constructionAxes.add(inp)
    axis.name = check_label(label)  # set_label() types exclude ConstructionAxis; same as _axis_by_planes
    return axis


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

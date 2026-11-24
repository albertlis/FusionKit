"""DfAM BRep helpers: edge, face, annulus and cone classification on Fusion geometry."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from fusion_kit._vec import _dot, _norm, _scale, _sub, _unit
from fusion_kit.dfam_limits import AXIS_PARALLEL, BED_ON_TOL_MM, FULL_CYLINDER_DEG, TOP_EDGE_PARALLEL
from fusion_kit.dfam_rules import bed_footprint_mm
from fusion_kit.dfam_types import DFAM_GROUP, Vec

if TYPE_CHECKING:  # annotations only; runtime adsk import stays lazy inside functions
    import adsk.core
    import adsk.fusion


# ---------------------------------------------------------------------------
# adsk adapter: live BRepBody -> facts. Fusion cm -> mm here.
# adsk and fusion_kit.features imported inside functions (features imports adsk at module level).
# Heuristics below are NOT measured on a live model.
# ---------------------------------------------------------------------------
def _vec(v: adsk.core.Point3D | adsk.core.Vector3D) -> Vec:
    return (v.x, v.y, v.z)


def _mm(v: adsk.core.Point3D | adsk.core.Vector3D) -> Vec:
    return (v.x * 10, v.y * 10, v.z * 10)


def _box_mm(box: adsk.core.BoundingBox3D) -> tuple[Vec, Vec]:
    return _mm(box.minPoint), _mm(box.maxPoint)


def _edge_dir(e: adsk.fusion.BRepEdge) -> Vec | None:
    """Unit start->end direction of a straight or curved edge. None if degenerate (closed loop / zero length)."""
    d = _sub(_mm(e.endVertex.geometry), _mm(e.startVertex.geometry))
    return _unit(d) if _norm(d) > 1e-6 else None


def _on_bed(e: adsk.fusion.BRepEdge, bed_z_mm: float) -> bool:
    """Both endpoints within BED_ON_TOL_MM of bed level bed_z_mm (split-bed pieces sit at z 0.004 mm, not exactly 0)."""
    return all(abs(_mm(v.geometry)[2] - bed_z_mm) <= BED_ON_TOL_MM for v in (e.startVertex, e.endVertex))


def _top_edge(g: adsk.fusion.BRepFace, bed_edge: adsk.fusion.BRepEdge) -> adsk.fusion.BRepEdge | None:
    """Chamfer top edge of g: most parallel edge of g (|cos| >= TOP_EDGE_PARALLEL to bed_edge), not at bed level
    (_on_bed), other face not bed-like. Ties in |cos| (rounded to 3 dp) -> longest edge. None if there is none."""
    bed_dir = _edge_dir(bed_edge)
    if bed_dir is None:
        return None
    bed_z = _mm(bed_edge.pointOnEdge)[2]
    best: adsk.fusion.BRepEdge | None = None
    best_key = (0.0, 0.0)  # (round |cos|, length cm)
    for t in g.edges:
        if t.tempId == bed_edge.tempId or _on_bed(t, bed_z):
            continue
        t_dir = _edge_dir(t)
        if t_dir is None:
            continue
        cos = round(abs(_dot(t_dir, bed_dir)), 3)
        if cos < TOP_EDGE_PARALLEL:
            continue
        if any(abs(_normal(h, t.pointOnEdge)[2]) > AXIS_PARALLEL for h in t.faces if h.tempId != g.tempId):
            continue
        key = (cos, t.length)
        if best is None or key > best_key:
            best, best_key = t, key
    return best


def _bed_span_mm(g: adsk.fusion.BRepFace, bed_edge: adsk.fusion.BRepEdge) -> float:
    """Bed footprint span of g, mm = summed length of ALL g's edges at bed level (_on_bed), slivers < BED_ON_TOL_MM
    skipped. A split bed edge (several pieces + seam) counts as one bed span."""
    bed_z = _mm(bed_edge.pointOnEdge)[2]
    return sum(e.length * 10 for e in g.edges if _on_bed(e, bed_z) and e.length * 10 >= BED_ON_TOL_MM)


def _planar_width_mm(g: adsk.fusion.BRepFace, bed_edge: adsk.fusion.BRepEdge) -> float:
    """Slant width of a planar bed chamfer, mm = area / mean(bed span, top edge length) (trapezoid face).
    bed span = _bed_span_mm. No top edge -> area / bed span."""
    top = _top_edge(g, bed_edge)
    bed_cm = _bed_span_mm(g, bed_edge) / 10
    span_cm = bed_cm if top is None else (bed_cm + top.length) / 2
    return g.area / span_cm * 10


def _bed_leg_mm(g: adsk.fusion.BRepFace, width_mm: float, n_g: Vec) -> float:
    """Bed FOOTPRINT of a bed-edge chamfer g, exact at any interior dihedral (bed_footprint_mm).

    Bed-like neighbour (|n_z| > AXIS_PARALLEL) gives alpha = acos(|n_g . n_bed|). Wall = face across g's top edge
    (_top_edge), phi = 180 - angle(n_bed, n_wall) (features._bed_dihedral_deg convention). Top neighbour bed-like or
    with the same face angle (sliver between two chamfers) -> 0.0. No top edge (fallback): wall = any non-bed
    neighbour, adjacent bed chamfer (same face angle) skipped. No wall found -> 0.0 (unmeasurable, FAILs).
    """
    bed_ns: list[Vec] = []
    other_ns: list[Vec] = []
    bed_edge: adsk.fusion.BRepEdge | None = None
    for e2 in g.edges:
        for h in e2.faces:
            if h.tempId == g.tempId:
                continue
            n_h = _normal(h, e2.pointOnEdge)
            if abs(n_h[2]) > AXIS_PARALLEL:
                bed_ns.append(n_h)
                if bed_edge is None:
                    bed_edge = e2
            else:
                other_ns.append(n_h)
    top = _top_edge(g, bed_edge) if bed_edge is not None else None
    walls = other_ns if top is None else [_normal(h, top.pointOnEdge) for h in top.faces if h.tempId != g.tempId]
    legs: list[float] = []
    for n_b in bed_ns:
        cos_a = abs(_dot(n_g, n_b))
        alpha = math.degrees(math.acos(min(1.0, cos_a)))
        for n_w in walls:
            d = _dot(n_b, n_w)
            if abs(n_w[2]) > AXIS_PARALLEL or abs(abs(d) - cos_a) < 1e-3:
                if top is not None:
                    return 0.0  # sliver: top neighbour bed-like or another chamfer of this face angle
                continue  # fallback: adjacent bed chamfer (same face angle) is not a wall
            phi = 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, d))))
            legs.append(bed_footprint_mm(width_mm, alpha, phi))
    return max(legs, default=0.0)


def _bed_phi_deg(faces: list[adsk.fusion.BRepFace], edge: adsk.fusion.BRepEdge) -> float | None:
    """Interior dihedral (deg) of a bed edge between its two faces = 180 - angle(n0, n1) (features_bed._bed_dihedral_deg).
    None if the edge does not have exactly two faces."""
    if len(faces) != 2:
        return None
    n0, n1 = (_normal(g, edge.pointOnEdge) for g in faces)
    return 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, _dot(n0, n1)))))


def _pin_base_axis(faces: list[adsk.fusion.BRepFace], e: adsk.fusion.BRepEdge) -> Vec | None:
    """Axis of a convex cylinder whose circular edge e meets a plane normal to that axis; None otherwise.

    Convex = outward normal leaves the axis (pin wall). Hole walls (normal toward axis) return None.
    Pin tip rim matches too: facts_from_body keeps only the concave one (pin base) as a fact.
    """
    from adsk import core

    p = _mm(e.pointOnEdge)
    for cyl_face, plane_face in ((faces[0], faces[1]), (faces[1], faces[0])):
        cyl = core.Cylinder.cast(cyl_face.geometry)
        if cyl is None or core.Plane.cast(plane_face.geometry) is None:
            continue
        ax = _unit(_vec(cyl.axis))
        rel = _sub(p, _mm(cyl.origin))
        radial = _sub(rel, _scale(ax, _dot(rel, ax)))
        if _dot(_normal(cyl_face, e.pointOnEdge), radial) <= 0:
            continue  # normal toward axis = concave hole wall
        if abs(_dot(_normal(plane_face, e.pointOnEdge), ax)) <= AXIS_PARALLEL:
            continue  # plane not normal to the cylinder axis
        return ax
    return None


def _circle_of(g: adsk.core.Curve3D) -> tuple[tuple[float, float, float], float] | None:
    """(centre xyz, radius) in cm of a circle/arc edge geometry; None for any other curve (line, ellipse, spline)."""
    center = getattr(g, "center", None)
    radius = getattr(g, "radius", None)
    if center is None or radius is None:
        return None
    return (center.x, center.y, center.z), float(radius)


def annulus_width_mm(face: adsk.fusion.BRepFace) -> float | None:
    """Width (mm) of a planar annulus B-rep face: exactly 2 loops, every edge a circle/arc, one centre and radius per
    loop, both loops share the centre. Width = |r1 - r2|. None if the face is not an annulus.

    -> width mm of a planar 2-loop circle annulus (|r1 - r2|, shared centre); None if not an
    annulus. Used for pin_base slit floor (<= SLIT_MAX_MM 0.2) and printability slit ceiling
    `BRepFace.loops`, `BRepEdge.geometry` (center/radius duck-typed via _circle_of)
    """
    loops = list(face.loops)
    if len(loops) != 2:
        return None
    circles: list[tuple[tuple[float, float, float], float]] = []
    for lp in loops:
        found = [_circle_of(e.geometry) for e in lp.edges]
        arcs = [f for f in found if f is not None]
        if not arcs or len(arcs) != len(found):
            return None
        c0, r0 = arcs[0]
        for c, r in arcs:
            if math.dist(c, c0) > 1e-6 or abs(r - r0) > 1e-6:
                return None
        circles.append((c0, r0))
    (c1, r1), (c2, r2) = circles
    if math.dist(c1, c2) > 1e-6:
        return None
    return abs(r1 - r2) * 10.0  # Fusion lengths in cm


def _pin_floor_width_mm(faces: list[adsk.fusion.BRepFace]) -> float | None:
    """Slit width under a pin base: annulus_width_mm of the planar face of the base edge's face pair.
    None = floor is a solid disc or not a ring (no slit)."""
    from adsk import core

    for g in faces:
        if core.Plane.cast(g.geometry) is not None:
            return annulus_width_mm(g)
    return None


def is_full_cylinder(span_deg: float) -> bool:
    """Concave cylinder group with total angular span >= FULL_CYLINDER_DEG = full hole."""
    return span_deg >= FULL_CYLINDER_DEG


def _cone_width_mm(face: adsk.fusion.BRepFace, edge: adsk.fusion.BRepEdge) -> float | None:
    """Slant width of a conical chamfer strip, mm = area / mean(L_bed, L_top).

    L_bed = summed length of the face's edges at the bed edge's z (a split bed circle = several arcs).
    L_top = summed length of the face's edges wholly above it (wall-side arc). None if no top edge.
    """
    tol = BED_ON_TOL_MM / 10  # cm
    ref = edge.pointOnEdge.z
    l_bed = 0.0
    l_top = 0.0
    for x in face.edges:
        lo, hi = x.boundingBox.minPoint.z, x.boundingBox.maxPoint.z
        if hi - lo <= tol and abs(lo - ref) <= tol:
            l_bed += x.length
        elif lo > ref + tol:
            l_top += x.length
    if l_top == 0.0:
        return None
    return face.area / ((l_bed + l_top) / 2) * 10


def _normal(face: adsk.fusion.BRepFace, point: adsk.core.Point3D | None = None) -> Vec:
    """Unit orientation-aware normal (features.face_normal), at point or pointOnFace."""
    from fusion_kit.features import face_normal

    return _unit(_vec(face_normal(face, point)))


def _adjacent(
    face: adsk.fusion.BRepFace, edge_faces: dict[int, list[adsk.fusion.BRepFace]] | None = None
) -> dict[int, adsk.fusion.BRepFace]:
    """Distinct faces sharing an edge with face, keyed by tempId. edge_faces: tempId -> faces of that edge (cache built
    once by facts_from_body); None or miss = read e.faces."""
    out: dict[int, adsk.fusion.BRepFace] = {}
    for e in face.edges:
        cached = edge_faces.get(e.tempId) if edge_faces is not None else None
        for g in cached if cached is not None else e.faces:
            if g.tempId != face.tempId:
                out[g.tempId] = g
    return out


def _has_attr(face: adsk.fusion.BRepFace, name: str) -> bool:
    try:
        return face.attributes.itemByName(DFAM_GROUP, name) is not None
    except RuntimeError:  # temporary print-frame faces take no attributes (live: "object does not support attributes")
        return False


def _tagged(face: adsk.fusion.BRepFace, name: str, tags: dict[str, frozenset[int]] | None) -> bool:
    """Tag on face. tags None: read from the face. tags given (print-frame copy): tempId set from print_frame_with_tags."""
    if tags is None:
        return _has_attr(face, name)
    return face.tempId in tags.get(name, frozenset())


def _span_deg(face: adsk.fusion.BRepFace) -> float:
    """Angular span of a cylinder face, deg, from u-range of evaluator.parametricRange() (rad). UNMEASURED live."""
    rng = face.evaluator.parametricRange()
    return math.degrees(rng.maxPoint.x - rng.minPoint.x)

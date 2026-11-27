"""Edge and face selection, convex edges, fillet/chamfer. Split from fusion_kit.features."""

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import adsk.core
import adsk.fusion

from fusion_kit._vec import _cross, _unit_strict
from fusion_kit.enums import (
    POINT_INSIDE,
    POINT_ON,
)
from fusion_kit.expr import Ex
from fusion_kit.features_common import (
    P3,
    _axis_index,
    _vi,
    _xyz,
    collection,
    face_normal,
)
from fusion_kit.timeline import check_label, set_label


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


def _is_planar(face: adsk.fusion.BRepFace) -> bool:
    geo = getattr(face, "geometry", None)
    return geo is not None and adsk.core.Plane.cast(geo) is not None


def _signed(d: tuple[float, float, float], opposed: bool) -> tuple[float, float, float]:
    return (-d[0], -d[1], -d[2]) if opposed else d


def _coedge_dir(face: adsk.fusion.BRepFace, edge: adsk.fusion.BRepEdge) -> tuple[float, float, float] | None:
    """Unit edge direction in the coedge sense of face's loop; None if face has no coedge for edge.

    BRep convention: face lies on the left of each coedge seen from its outward normal, so the in-face direction
    away from the edge is n x t. Chord start->end gives the edge sense; BRepCoEdge.isOpposedToEdge flips it.
    Lookup, documented API only (every collection read as count + item(i)):
      1. edge.coEdges (BRepCoEdges) -> coedge.loop.face.tempId == face.tempId
      2. face.loops (BRepLoops) -> loop.coEdges -> coedge.edge.tempId == edge.tempId
    No match -> None -> caller falls back to the probe (counted in CONVEX_STATS["probe_fallback"]).
    """
    a = _xyz(edge.startVertex.geometry)
    b = _xyz(edge.endVertex.geometry)
    d = _unit_strict((b[0] - a[0], b[1] - a[1], b[2] - a[2]))
    for i in range(edge.coEdges.count):
        co = edge.coEdges.item(i)
        if co.loop.face.tempId == face.tempId:
            return _signed(d, co.isOpposedToEdge)
    for i in range(face.loops.count):
        lp = face.loops.item(i)
        for j in range(lp.coEdges.count):
            co = lp.coEdges.item(j)
            if co.edge.tempId == edge.tempId:
                return _signed(d, co.isOpposedToEdge)
    return None


def _topo_convex(
    fa: adsk.fusion.BRepFace,
    fb: adsk.fusion.BRepFace,
    edge: adsk.fusion.BRepEdge,
    na: tuple[float, float, float],
    nb: tuple[float, float, float],
) -> bool | None:
    """Convex (True) / concave (False) from BRep topology, no containment probe. None = not applicable.

    Needs both faces planar and a coedge of face A on edge. u_A = n_A x t_A = in-face direction from edge into A.
    Convex iff A runs to the inner side of B's plane: n_B . u_A < 0. Concave: A runs outside B's half-space.
    """
    if not (_is_planar(fa) and _is_planar(fb)):
        return None
    t = _coedge_dir(fa, edge)
    if t is None:
        return None
    u = _cross(na, t)
    return nb[0] * u[0] + nb[1] * u[1] + nb[2] * u[2] < 0


def _probe_convex(
    body: adsk.fusion.BRepBody,
    mid: adsk.core.Point3D,
    na: tuple[float, float, float],
    nb: tuple[float, float, float],
    eps: float,
) -> bool:
    """Containment probe, convex test (eps in cm). Up to 5 pointContainment calls per edge.

    t = unit(n_A x n_B); u_X = in-face direction from the edge into face X, sign by containment (M + eps*u_X ON body).
    P = M + eps * unit(u_A + u_B): P INSIDE => convex (wedge between u_A and u_B is material).
    Ambiguous (no sign ON, or u_A = -u_B) or POINT_ON => False (dropped).
    NOT the bisector of outward normals: that probe is OUTSIDE for convex AND concave corners (L-corner test).
    """
    m = _xyz(mid)
    t = _unit_strict(_cross(na, nb))
    ua = _in_face_dir(body, m, na, t, eps)
    ub = _in_face_dir(body, m, nb, t, eps)
    if ua is None or ub is None:
        return False
    s = (ua[0] + ub[0], ua[1] + ub[1], ua[2] + ub[2])
    if math.sqrt(s[0] ** 2 + s[1] ** 2 + s[2] ** 2) < 1e-6:
        return False  # u_A = -u_B: straight through, no crease
    su = _unit_strict(s)
    probe = P3(m[0] + eps * su[0], m[1] + eps * su[1], m[2] + eps * su[2])
    return body.pointContainment(probe) == POINT_INSIDE


# Process-wide counters for convex_edges, read live by the manager: topo = decided from coedge topology,
# probe_fallback = containment probe used (non-planar pair or no matching coedge). Reset by assignment if needed.
CONVEX_STATS: dict[str, int] = {"topo": 0, "probe_fallback": 0}


def convex_edges(
    body: adsk.fusion.BRepBody,
    edges: Iterable[adsk.fusion.BRepEdge],
    probe_mm: float = 0.1,
) -> list[adsk.fusion.BRepEdge]:
    """Edges of body that are convex creases (interior dihedral < 180 deg).

    Only 2-face edges with non-parallel normals are considered; others are dropped. Per edge:
      - both faces planar and face A has a coedge on the edge: decided from topology (_topo_convex), zero probes.
        Convex iff n_B . (n_A x t_A) < 0, t_A = coedge direction of A. Hand-checked on unit cube / L prism.
      - otherwise containment probe (_probe_convex, probe_mm mm -> cm). Ambiguous probe => dropped.
    No pointOnFace used (it may sit beyond the edge line on a non-convex face). Raises ValueError if face_normal
    gets no normal at M.

    Planar-planar edges decided from coedge topology (zero probes); containment probe only as
    fallback, counted in `CONVEX_STATS` `pointContainment` (fallback only)
    """
    eps = probe_mm / 10  # mm -> cm
    out: list[adsk.fusion.BRepEdge] = []
    for e in edges:
        faces = list(e.faces)
        if len(faces) != 2:
            continue
        fa, fb = faces
        mid = e.pointOnEdge
        na = _unit_strict(_xyz(face_normal(fa, mid)))
        nb = _unit_strict(_xyz(face_normal(fb, mid)))
        cross = _cross(na, nb)
        if math.sqrt(cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2) < 1e-6:
            continue  # parallel normals: tangent (flat) or folded-back edge, not a convex crease
        convex = _topo_convex(fa, fb, e, na, nb)
        if convex is None:
            CONVEX_STATS["probe_fallback"] += 1
            convex = _probe_convex(body, mid, na, nb, eps)
        else:
            CONVEX_STATS["topo"] += 1
        if convex:
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


def fillet_groups(
    comp: adsk.fusion.Component,
    groups: list[tuple[list[adsk.fusion.BRepEdge], str | Ex]],
    *,
    tangent_chain: bool = False,
    label: str,
) -> adsk.fusion.FilletFeature:
    """One fillet feature, one constant-radius edge set per group: groups = [(edges, radius), ...].

    radius: mm expression or Ex, per group. tangent_chain applies to every group (default False = picked edges only).
    One feature instead of several fillet() calls on one body (feature_quality rule finishing_split).
    """
    if not groups or any(not edges for edges, _ in groups):
        raise ValueError("fillet_groups: needs >= 1 group, every group with >= 1 edge")
    check_label(label)
    fi = comp.features.filletFeatures.createInput()
    for edges, radius in groups:
        fi.edgeSetInputs.addConstantRadiusEdgeSet(collection(edges), _vi(radius), tangent_chain)
    feat = comp.features.filletFeatures.add(fi)
    set_label(feat, label)
    return feat


def chamfer_groups(
    comp: adsk.fusion.Component,
    groups: list[tuple[list[adsk.fusion.BRepEdge], str | Ex]],
    *,
    label: str,
) -> adsk.fusion.ChamferFeature:
    """One chamfer feature, one equal-distance edge set per group: groups = [(edges, distance), ...].

    distance: mm expression or Ex, per group. Tangent chain off (picked edges only), as chamfer() default.
    One feature instead of several chamfer() calls on one body (feature_quality rule finishing_split).
    """
    if not groups or any(not edges for edges, _ in groups):
        raise ValueError("chamfer_groups: needs >= 1 group, every group with >= 1 edge")
    check_label(label)
    ci = comp.features.chamferFeatures.createInput2()
    for edges, distance in groups:
        ci.chamferEdgeSets.addEqualDistanceChamferEdgeSet(collection(edges), _vi(distance), False)
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

    probe each edge alone (RuntimeError = refused, shorter than min_len_mm skipped), ONE chamfer
    over accepted; None if none
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


def _in_inner_loop(e: adsk.fusion.BRepEdge) -> bool:
    """True if e bounds a hole, pocket or tube bore (non-outer loop) of any adjacent face.

    Compares tempId: identity is per script run, so never persist or compare across runs.

    Pocket/through-hole rim is geometrically identical to an outer top rim (90 deg convex corner,
    faces maxZ equal), so only topology separates them: the rim sits in an inner loop of the top face.
    """
    return any(not lp.isOuter and e.tempId in {x.tempId for x in lp.edges} for f in e.faces for lp in f.loops)


Mm3 = tuple[float, float, float]


@dataclass(frozen=True)
class CircleInfo:
    """Circle3D / Arc3D geometry in mm, as seen by select_edges(pred=)."""

    centre_mm: Mm3
    radius_mm: float
    normal: Mm3  # plane normal as Fusion reports it (unit length in practice)

    def parallel_to(self, axis: str) -> bool:
        """True if the circle plane normal is along axis ("x"|"y"|"z", any case): |n[axis]| >= 0.999."""
        return abs(self.normal[_axis_index(axis)]) >= 0.999


@dataclass(frozen=True)
class EdgeInfo:
    """Edge facts in mm for select_edges(pred=). Snapshot taken before pred runs; pred makes no adsk calls.

    start_mm / end_mm: startVertex / endVertex points; None if the edge has no vertex (closed loop).
    bbox_min_mm / bbox_max_mm: edge bounding box.
    circle: CircleInfo for Circle3D / Arc3D, None for every other edge type.
    """

    edge: adsk.fusion.BRepEdge
    start_mm: Mm3 | None
    end_mm: Mm3 | None
    bbox_min_mm: Mm3
    bbox_max_mm: Mm3
    circle: CircleInfo | None


def _mm3(p: adsk.core.Point3D) -> Mm3:
    return (p.x * 10, p.y * 10, p.z * 10)  # Fusion internal cm -> mm


def _edge_info(e: adsk.fusion.BRepEdge) -> EdgeInfo:
    g = e.geometry
    circle = None
    if isinstance(g, (adsk.core.Circle3D, adsk.core.Arc3D)):
        n = g.normal
        circle = CircleInfo(centre_mm=_mm3(g.center), radius_mm=g.radius * 10, normal=(n.x, n.y, n.z))
    sv, ev = e.startVertex, e.endVertex
    bb = e.boundingBox
    return EdgeInfo(
        edge=e,
        start_mm=None if sv is None else _mm3(sv.geometry),
        end_mm=None if ev is None else _mm3(ev.geometry),
        bbox_min_mm=_mm3(bb.minPoint),
        bbox_max_mm=_mm3(bb.maxPoint),
        circle=circle,
    )


def _bbox_in_box(info: EdgeInfo, box: tuple[Mm3, Mm3], tol_mm: float) -> bool:
    """True if the whole edge bbox lies inside box = (lo_mm, hi_mm), grown by tol_mm on each side. Pure."""
    lo, hi = box
    return all(lo[i] - tol_mm <= info.bbox_min_mm[i] and info.bbox_max_mm[i] <= hi[i] + tol_mm for i in range(3))


def select_edges(
    body: adsk.fusion.BRepBody,
    circle_r: float | None = None,
    axis: str | None = None,
    convex_top: bool | None = None,
    tol_mm: float = 1e-3,
    on_plane: tuple[str, float] | None = None,
    face_normal: tuple[float, float, float] | None = None,
    min_dot: float = 0.6,
    pred: Callable[[EdgeInfo], bool] | None = None,
    in_box_mm: tuple[Mm3, Mm3] | None = None,
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
    pred       : callable(EdgeInfo) -> bool, AND-ed with the filters above. Input in mm (EdgeInfo). None = off.
                 Two planes (x=0 AND z=zc): test bbox_min_mm/bbox_max_mm in pred, one call, no second on_plane.
    in_box_mm  : (lo_mm, hi_mm) -> keep edges whose whole bbox lies inside the box (+-tol_mm). None = off.
    """
    tol = tol_mm / 10  # mm -> cm (Fusion internal units)
    normal_ok = None if face_normal is None else {f.tempId for f in faces_by_normal(body, face_normal, min_dot)}
    ax = None if axis is None else _axis_index(axis)
    plane_ax = None if on_plane is None else _axis_index(on_plane[0])
    plane_cm = 0.0 if on_plane is None else on_plane[1] / 10  # mm -> cm
    info_needed = pred is not None or in_box_mm is not None
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
        if info_needed:
            info = _edge_info(e)
            if in_box_mm is not None and not _bbox_in_box(info, in_box_mm, tol_mm):
                continue
            if pred is not None and not pred(info):
                continue
        out.append(e)
    return out

"""DfAM adsk adapter: BRepBody -> HoleFact / EdgeFact / BodyFact via facts_from_body."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from fusion_kit._vec import _cross, _dot, _norm, _scale, _sub, _unit
from fusion_kit.dfam_bed import bed_axis
from fusion_kit.dfam_geom import (
    _adjacent,
    _bed_leg_mm,
    _bed_phi_deg,
    _box_mm,
    _cone_width_mm,
    _has_attr,
    _mm,
    _normal,
    _pin_base_axis,
    _pin_floor_width_mm,
    _planar_width_mm,
    _span_deg,
    _tagged,
    _vec,
    is_full_cylinder,
)
from fusion_kit.dfam_limits import (
    AXIS_PARALLEL,
    BED_ON_TOL_MM,
    CONE_TILT_TOL,
    PARALLEL_TOL,
    RISE_TOL_MM,
    ROOF_NORMAL_TOL,
    SEAM_V_COS_MAX,
)
from fusion_kit.dfam_rules import chamfer_leg_mm
from fusion_kit.dfam_types import (
    BED_DIRS,
    DFAM_GROUP,
    GRIP_FIN_ATTR,
    GRIP_FIN_SECTION_ATTR,
    MATING_ATTR,
    BodyFact,
    EdgeFact,
    EdgeKind,
    EdgeTreatment,
    HoleFact,
    Vec,
)
from fusion_kit.dfam_warp import _groove_rects, _warp_grooves, in_warp_frames, warp_frames

if TYPE_CHECKING:  # annotations only; runtime adsk import stays lazy inside functions
    import adsk.core
    import adsk.fusion


def _grip_fin_section(body: adsk.fusion.BRepBody) -> float | None:
    """grip_fit min fin width (mm) from body attr (DFAM_GROUP, GRIP_FIN_SECTION_ATTR); None if absent.

    Print-frame copies take no attributes (RuntimeError -> None); print_frame_facts re-reads the ORIGINAL body.
    """
    try:
        attrs = body.attributes  # temporary print-frame body: None or RuntimeError
        attr = None if attrs is None else attrs.itemByName(DFAM_GROUP, GRIP_FIN_SECTION_ATTR)
    except RuntimeError:
        return None
    return None if attr is None else float(attr.value)


def facts_from_body(
    body: adsk.fusion.BRepBody, tags: dict[str, frozenset[int]] | None = None
) -> tuple[BodyFact, list[HoleFact], list[EdgeFact]]:
    """Body -> (BodyFact, holes, edges). Units cm -> mm. tags: print-frame tempId sets (print_frame_with_tags); None = read attrs.

    Heuristics (verify live before trusting):
    - hole = concave cylinder group (same axis/origin/radius, split seams summed) with total span
      >= FULL_CYLINDER_DEG. Concave arcs (pocket/floor fillets) are not holes. Convex bosses skipped.
    - horizontal peak: roof = planar faces adjacent to the hole with normal perpendicular to axis
      (teardrop roof); through-wall end faces (normal along axis) excluded.
      peak = roof top Z (or face top Z) - axis Z if > radius + 0.01, else None.
    - vertical seam notch: two adjacent planar walls (normal perpendicular to axis) meeting at 90 +- SEAM_V_TOL_DEG.
      Plain hole has only top+bottom (axis-normal caps, excluded) -> False. Sloped/chamfered top -> False.
    - edge candidates: linear edges parallel Z, outer bed-loop edges, edges of faces with attr dfam/mating.
      Kind precedence: mating > vertical > bed. Hole seams (edge parallel to adjacent concave cyl axis) skipped.
    - circular edge: convex cylinder + plane normal to its axis (_pin_base_axis) = pin_base. Concave one
      = pin base, sharp (chamfer / fillet at pin base not seen: tangent / cone seams skipped).
      Pin base slit_width_mm = width of the planar annulus floor (_pin_floor_width_mm), None if solid floor.
    - tangent edge parallel to a convex cylinder axis = fillet, size = cylinder radius.
      Other creases kept only if convex (features.convex_edges). Adjacent planar face with
      non axis-aligned normal = chamfer, size = chamfer leg on the other face of the edge
      (chamfer_leg_mm; width = face area / edge length). Bed edge with inclined cone face
      (|n_z| in (CONE_TILT_TOL, AXIS_PARALLEL)) = chamfer too, width = area / mean curved boundary length
      (_cone_width_mm). Else sharp.
    - min_wall_mm None and vertical_wall_mm [] (not measured: min_wall rule never fires).
    - bed_step_unsupported / side_screw / has_relief not detected -> False.

    tags = print-frame tempId sets from print_frame_with_tags; None = read attrs from body
    """
    from adsk import core

    from fusion_kit.features import convex_edges, planar_faces

    # One traversal per collection; every loop below reuses these lists (each Fusion collection read = item() calls).
    body_faces = list(body.faces)
    body_edges = list(body.edges)
    face_source = SimpleNamespace(faces=body_faces)  # planar_faces reads only .faces
    edge_faces = {
        e.tempId: list(e.faces) for e in body_edges
    }  # one e.faces read per edge, shared by _adjacent + edge loop
    if tags is None:  # attrs read once per face (was 2 lookups per edge-face incidence); same sets _tagged reads
        tags = {
            name: frozenset(f.tempId for f in body_faces if _has_attr(f, name)) for name in (MATING_ATTR, GRIP_FIN_ATTR)
        }

    lo, hi = _box_mm(body.boundingBox)
    dims = _sub(hi, lo)
    bed_z = lo[2]
    room_mm = min(dims)

    bed = planar_faces(face_source, "z", bed_z, normal_sign=-1, tol_mm=BED_ON_TOL_MM)
    beds = [_box_mm(f.boundingBox) for f in bed]
    bed_max = (
        max(
            max(b[1][0] for b in beds) - min(b[0][0] for b in beds),
            max(b[1][1] for b in beds) - min(b[0][1] for b in beds),
        )
        if beds
        else 0.0
    )
    grooves = _groove_rects(bed, (lo, hi))
    has_warp = _warp_grooves(grooves)
    side_faces: dict[str, list[adsk.fusion.BRepFace]] = {"-z": bed}
    for d in BED_DIRS[1:]:  # secondary print positions: groove families on the other five sides
        a, sgn = bed_axis(d)
        side_faces[d] = planar_faces(
            face_source, "xyz"[a], (hi if sgn > 0 else lo)[a], normal_sign=sgn, tol_mm=BED_ON_TOL_MM
        )
    frames = warp_frames(lo, hi, side_faces, bed_grooves=grooves)

    holes: list[HoleFact] = []
    concave_axis: dict[int, Vec] = {}  # every concave cylinder (holes + fillets): seams skipped below
    concave: list[tuple[adsk.fusion.BRepFace, Vec, Vec, float]] = []  # (face, axis, origin, radius_mm)
    for f in body_faces:
        cyl = core.Cylinder.cast(f.geometry)
        if cyl is None:
            continue
        ax = _unit(_vec(cyl.axis))
        origin = _mm(cyl.origin)
        r = cyl.radius * 10
        p = _mm(f.pointOnFace)
        rel = _sub(p, origin)
        radial = _sub(rel, _scale(ax, _dot(rel, ax)))
        if _dot(_normal(f), radial) >= 0:
            continue  # normal leaves the axis: convex boss, not a hole
        concave_axis[f.tempId] = ax
        concave.append((f, ax, origin, r))

    # Hole = concave cylinder group (split seams share axis/origin/radius) spanning >= FULL_CYLINDER_DEG.
    groups: dict[tuple[float, ...], list[tuple[adsk.fusion.BRepFace, Vec, Vec, float]]] = {}
    for item in concave:
        _f, ax, origin, r = item
        key = (*(round(c, 3) for c in ax), *(round(c, 3) for c in origin), round(r, 3))
        groups.setdefault(key, []).append(item)
    for members in groups.values():
        faces = [m[0] for m in members]
        if not is_full_cylinder(sum(_span_deg(f) for f in faces)):
            continue  # partial concave arc (fillet): not a hole
        _f0, ax, origin, r = members[0]
        vertical = abs(ax[2]) > AXIS_PARALLEL
        adj: dict[int, adsk.fusion.BRepFace] = {}
        for f in faces:
            for g in _adjacent(f, edge_faces).values():
                if core.Plane.cast(g.geometry) is not None:
                    adj[g.tempId] = g
        adj_planar = list(adj.values())
        peak: float | None = None
        seam = False
        if vertical:
            normals = [_normal(g) for g in adj_planar]
            walls = [n for n in normals if abs(_dot(n, ax)) < ROOF_NORMAL_TOL]
            seam = any(abs(_dot(a, b)) <= SEAM_V_COS_MAX for i, a in enumerate(walls) for b in walls[i + 1 :])
        else:
            roof = [g for g in adj_planar if abs(_dot(_normal(g), ax)) < ROOF_NORMAL_TOL]
            top = max([_box_mm(f.boundingBox)[1][2] for f in faces] + [_box_mm(g.boundingBox)[1][2] for g in roof])
            rise = top - origin[2]
            peak = rise if rise > r + RISE_TOL_MM else None
        p = _mm(faces[0].pointOnFace)
        bed_lo_z = min(_box_mm(f.boundingBox)[0][2] for f in faces)
        holes.append(
            HoleFact(
                label=f"hole@({p[0]:.1f},{p[1]:.1f},{p[2]:.1f})",
                axis_vertical=vertical,
                radius_mm=r,
                peak_mm=peak,
                has_seam_notch=seam,
                on_bed=bed_lo_z - bed_z <= BED_ON_TOL_MM,
                bed_step_unsupported=False,  # TODO: not detected by adapter
                side_screw=False,  # TODO: not detected by adapter
                has_relief=False,
            )
        )

    bed_tokens = {e.tempId for f in bed for lp in f.loops if lp.isOuter for e in lp.edges}
    rows: list[tuple[adsk.fusion.BRepEdge, EdgeKind, float | None, list[adsk.fusion.BRepFace]]] = []
    for e in body_edges:
        faces = edge_faces[e.tempId]
        if len(faces) != 2:
            continue
        linear = core.Line3D.cast(e.geometry) is not None
        d = _unit(_sub(_mm(e.endVertex.geometry), _mm(e.startVertex.geometry))) if linear else (0.0, 0.0, 0.0)
        if any(g.tempId in concave_axis and abs(_dot(d, concave_axis[g.tempId])) > AXIS_PARALLEL for g in faces):
            continue  # hole seam, not a part edge
        kind: EdgeKind
        if any(_tagged(g, MATING_ATTR, tags) for g in faces):
            kind = "mating"
        elif linear and abs(d[2]) > AXIS_PARALLEL:
            kind = "vertical"
        elif e.tempId in bed_tokens:
            kind = "bed"
        elif not linear and _pin_base_axis(faces, e) is not None:
            kind = "pin_base"  # circular edge: convex pin wall meets plane normal to its axis
        else:
            continue
        fillet_r: float | None = None
        for g in faces:
            cyl = core.Cylinder.cast(g.geometry)
            if cyl is not None and linear and abs(_dot(d, _unit(_vec(cyl.axis)))) > AXIS_PARALLEL:
                fillet_r = cyl.radius * 10
        if fillet_r is None:
            n0, n1 = (_normal(g, e.pointOnEdge) for g in faces)
            if _norm(_cross(n0, n1)) <= PARALLEL_TOL:
                # tangent to a bed edge: non-planar neighbour (Torus / BSpline fillet, curved or straight edge) = fillet
                curved = [g for g in faces if core.Plane.cast(g.geometry) is None]
                if kind != "bed" or not curved:
                    continue  # smooth tangent seam, not a crease
                tor = core.Torus.cast(curved[0].geometry)
                fillet_r = tor.minorRadius * 10 if tor is not None else 0.0  # 0.0 = radius unknown, kind still fillet
        rows.append((e, kind, fillet_r, faces))

    creases = [e for e, _, fr, _f in rows if fr is None]
    convex = {e.tempId for e in convex_edges(body, creases)} if creases else set()

    edge_facts: list[EdgeFact] = []
    for e, kind, fillet_r, faces in rows:
        m = _mm(e.pointOnEdge)
        label = f"edge@({m[0]:.1f},{m[1]:.1f},{m[2]:.1f})"
        treatment: EdgeTreatment = "sharp"
        size = 0.0
        if fillet_r is not None:
            treatment, size = "fillet", fillet_r
        elif kind == "pin_base":
            if e.tempId in convex:
                continue  # pin tip rim: outer convex corner, not a base
            # concave pin base: chamfer / fillet not measured here, stays sharp
        else:
            if e.tempId not in convex:
                continue  # concave crease: not a printed outer corner
            for g in faces:
                if core.Plane.cast(g.geometry) is not None:
                    n_g = _normal(g, e.pointOnEdge)
                    if max(abs(c) for c in n_g) > AXIS_PARALLEL:
                        continue  # axis-aligned face: not a chamfer
                    width = g.area / e.length * 10  # chamfer face width, mm
                    if kind == "bed":
                        width = _planar_width_mm(g, e)  # trapezoid: area / mean(bed edge, top edge)
                elif kind == "bed" and core.Cone.cast(g.geometry) is not None:
                    n_g = _normal(g, e.pointOnEdge)
                    if not CONE_TILT_TOL < abs(n_g[2]) < AXIS_PARALLEL:
                        continue  # vertical wall (n_z ~ 0) or horizontal top (|n_z| ~ 1): not a chamfer
                    cone_w = _cone_width_mm(g, e)  # curved chamfer: mean of bed-level and top-level boundary lengths
                    width = cone_w if cone_w is not None else g.area / e.length * 10  # no top edge: bed edge alone
                else:
                    continue
                adj_face = faces[1] if faces[0] is g else faces[0]
                if kind == "bed":
                    leg = _bed_leg_mm(g, width, n_g)  # bed leg (two-distance chamfer): steep chamfer = short bed leg
                else:
                    leg = chamfer_leg_mm(width, n_g, _normal(adj_face, e.pointOnEdge))
                treatment, size = "chamfer", leg
                break
        in_groove = in_warp_frames(m, frames)
        on_fin = any(_tagged(g, GRIP_FIN_ATTR, tags) for g in faces)
        edge_facts.append(
            EdgeFact(
                label=label,
                kind=kind,
                treatment=treatment,
                size_mm=size,
                room_mm=room_mm,
                in_warp_groove=in_groove,
                on_grip_fin=on_fin,
                length_mm=e.length * 10,  # BRepEdge.length is cm; same unit conversion as features.bed_chamfer
                slit_width_mm=_pin_floor_width_mm(faces) if kind == "pin_base" else None,
                phi_deg=_bed_phi_deg(faces, e) if kind == "bed" else None,
            )
        )

    body_fact = BodyFact(
        name=body.name,
        bed_max_side_mm=bed_max,
        has_warp_grooves=has_warp,
        height_mm=dims[2],
        footprint_min_mm=min(dims[0], dims[1]),
        min_wall_mm=None,  # TODO: wall thickness not measured; min_wall rule never fires
        vertical_wall_mm=[],  # TODO: same
        has_cavity=body.shells.count > 1,
        grip_fin_section_mm=_grip_fin_section(body),
    )
    return body_fact, holes, edge_facts

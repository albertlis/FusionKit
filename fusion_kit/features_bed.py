"""Print-bed elephant-foot chamfer (bed_chamfer). Split from fusion_kit.features."""

import math

import adsk.core
import adsk.fusion

from fusion_kit.dfam import BED_CHAMFER_ANGLE_DEG, BedDir, bed_axis, set_bed
from fusion_kit.dfam_rules import bed_chamfer_feasible
from fusion_kit.expr import Ex, as_str
from fusion_kit.features_common import (
    _vi,
    _xyz,
    collection,
)
from fusion_kit.features_edges import (
    faces_by_normal,
)
from fusion_kit.timeline import check_label, set_label
from fusion_kit.user_params import eval_fresh

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
    angle_expr: str | None = None,
    bed: BedDir = "-z",
    min_edge_mm: float = 0.0,
    inner_loops: bool = False,
) -> adsk.fusion.ChamferFeature:
    """Elephant-foot chamfer on outer loops of the print bed face. Inner loops (holes) stay sharp unless inner_loops.

    Edges with no positive wall leg (sin phi + cos phi * tan angle_deg <= 0, e.g. 135 deg outer tips) are skipped:
    one BED_CHAMFER_SKIP print line, left sharp; DfAM reports them as WARN, not FAIL.

    min_edge_mm: skip bed edges shorter than this (mm). A ~2 mm ledge between two chamfered edges cannot take a
    1 mm chamfer chain -> ASM_BL_UNFIN_SHEET (side_wall dovetail ledge 1.92 mm, measured); 0 = all edges.
    inner_loops: also chamfer the inner loops (holes, windows) of the bed face(s), same two-distance rule and dihedral
    groups (a hole wall normal to the bed is phi 90), same min_edge_mm. False (default) keeps holes sharp = fits.

    bed = bed face direction ('-z' default = min-Z face; '+x' = X-max face). Stored on body as dfam/bed (set_bed)
    so report() checks the same bed. Chamfer face |n| is read on the bed axis.
    distance = 90-deg-equivalent WALL leg (mm expr or Ex): bed footprint = distance / tan(angle_deg) for every phi; wall leg = distance * bed_wall_ratio(90, angle_deg) / bed_wall_ratio(phi, angle_deg), written as expression text = distance / (sin(phi) + cos(phi) * tan(angle_deg)). Both legs are timeline expressions: angle_deg is in the text, phi is the measured dihedral literal. phi = the
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

    bed = bed face dir ("-z" min Z default, "+x" X-max, ...); stored via set_bed; one two-distance
    edge set per interior-dihedral group: `distance` = 90-deg-equivalent wall leg, bed footprint =
    distance * tan(90 - angle_deg) at any phi, wall leg = distance / (sin(phi) + cos(phi) *
    tan(angle_deg)) (105 = dovetail wall); both legs are timeline expression text (angle inside, phi
    = literal dihedral); `angle_deg` = chamfer face angle FROM HORIZONTAL, must be in (45, 90)
    exclusive else ValueError (46 -> n_z 0.695; 45 FAILS). isFlipped tried False then True; accept
    only if face angle acos(|n_z|) = angle_deg ±0.5 and |n_z| < 0.707 (Fusion order undocumented);
    `min_edge_mm` skips bed edges shorter than mm; `inner_loops=True` also chamfers inner loops
    (holes/windows) of the bed face, same dihedral groups and min_edge_mm (default False = holes
    sharp, fits)
    angle_expr = name of a user parameter (unit deg) holding the chamfer angle: both timeline expressions use it
    instead of the literal, and angle_deg is replaced by its evaluated value (eval_fresh, unit deg) for the build-time
    (45, 90) and face-angle checks. None = literal angle_deg (text unchanged).
    """
    if angle_expr is not None:
        angle_deg = math.degrees(eval_fresh(comp.parentDesign, angle_expr, "deg"))
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
        # no positive wall leg (outer 135 deg tips): left sharp; DfAM reports them as WARN (dfam_rules.bed_chamfer_feasible)
        skipped = {phi: grp for phi, grp in groups.items() if not bed_chamfer_feasible(phi, angle_deg)}
        if skipped and not flip:
            n_skip = sum(len(grp) for grp in skipped.values())
            phis = ", ".join(f"{phi:g}" for phi in sorted(skipped))
            print(f"BED_CHAMFER_SKIP {n_skip} edges (dihedral {phis} deg) label={label}")
        groups = {phi: grp for phi, grp in groups.items() if phi not in skipped}
        if not groups:
            raise ValueError(f"bed_chamfer {label!r}: every bed edge skipped, no positive wall leg at {angle_deg} deg")
        ci = comp.features.chamferFeatures.createInput2()
        # Expression text, not a Python float: the angle stays visible in the timeline.
        # bed leg = distance * cot(angle) = distance * tan(90 - angle), same for every phi (90-deg footprint rule).
        if angle_expr is None:
            bed_leg = f"({side}) * tan({90.0 - angle_deg!r} deg)"
            angle_ref = f"{angle_deg!r} deg"
        else:
            bed_leg = f"({side}) * tan(90 deg - ({angle_expr}))"
            angle_ref = f"({angle_expr})"
        for phi, grp in sorted(groups.items()):
            # wall leg = distance * cot(angle) / bed_wall_ratio(phi) = distance / (sin(phi) + cos(phi) * tan(angle)).
            # phi stays a literal: it is the measured dihedral of this edge group, not a user parameter.
            wall_leg = f"({side}) / (sin({phi!r} deg) + cos({phi!r} deg) * tan({angle_ref}))"
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

"""Print-bed elephant-foot chamfer (bed_chamfer). Split from fusion_kit.features."""

from __future__ import annotations

import math
from collections.abc import Sequence

import adsk.core
import adsk.fusion

from fusion_kit.dfam import BED_CHAMFER_ANGLE_DEG, BedDir, bed_axis, set_bed
from fusion_kit.dfam_bed import add_bed_loop_skips
from fusion_kit.dfam_rules import bed_chamfer_feasible
from fusion_kit.expr import Ex, as_str
from fusion_kit.feature_quality import ROLE_BED_CHAMFER, ROLE_GROUP, ROLE_NAME
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

_Loop = tuple[bool, list[adsk.fusion.BRepEdge]]  # (is outer loop, edges)
_Work = list[tuple[float, adsk.fusion.BRepEdge]]  # one loop to chamfer: (interior dihedral deg, edge)
_LoopW = tuple[_Work, list[adsk.fusion.BRepEdge], bool]  # (work, all loop edges, is outer)
_Skipped = tuple[list[adsk.fusion.BRepEdge], str, bool]  # (all loop edges, reason, is outer)
_Centre = tuple[float, float, float]  # loop reference point, mm, global x y z


def _bed_loops(body: adsk.fusion.BRepBody, only_largest_bed_face: bool, bed: BedDir) -> list[_Loop]:
    """Every loop (outer and inner) of the bed face(s), face order, outer first per face: (is_outer, edges).
    Planar faces with outward normal = bed direction, at the body bbox extreme; only_largest_bed_face = the largest one."""
    a, s = bed_axis(bed)
    bb = body.boundingBox
    zb = _xyz(bb.minPoint if s < 0 else bb.maxPoint)[a]
    normal = (float(s) * (a == 0), float(s) * (a == 1), float(s) * (a == 2))
    loops: list[_Loop] = []
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
            loops = [(lp.isOuter, list(lp.edges)) for lp in bed_face.loops]
    else:
        for f in body.faces:
            if not isinstance(f.geometry, adsk.core.Plane):
                continue
            fb = f.boundingBox
            if abs(_xyz(fb.minPoint)[a] - zb) > _BED_FLAT_TOL_CM or abs(_xyz(fb.maxPoint)[a] - zb) > _BED_FLAT_TOL_CM:
                continue
            loops.extend((lp.isOuter, list(lp.edges)) for lp in f.loops)
    return loops


def _edges_bbox_mm(edges: list[adsk.fusion.BRepEdge]) -> tuple[list[float], list[float]]:
    """Bounding box (min, max) of a set of edges, mm, global x y z."""
    boxes = [(_xyz(e.boundingBox.minPoint), _xyz(e.boundingBox.maxPoint)) for e in edges]
    mins = [min(lo[i] for lo, _hi in boxes) * 10.0 for i in range(3)]
    maxs = [max(hi[i] for _lo, hi in boxes) * 10.0 for i in range(3)]
    return mins, maxs


def _loop_centre_mm(edges: list[adsk.fusion.BRepEdge]) -> _Centre:
    """Bounding-box centre of a loop, mm global."""
    mins, maxs = _edges_bbox_mm(edges)
    return ((mins[0] + maxs[0]) / 2.0, (mins[1] + maxs[1]) / 2.0, (mins[2] + maxs[2]) / 2.0)


_BOX_TOL_MM = 0.01  # two loop bboxes within this (mm, every coordinate) are the same untouched loop


def _loop_box(edges: list[adsk.fusion.BRepEdge]) -> tuple[float, ...]:
    """Loop bounding box, mm global: min x y z then max x y z."""
    mins, maxs = _edges_bbox_mm(edges)
    return (*mins, *maxs)


def _same_box(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return all(abs(p - q) <= _BOX_TOL_MM for p, q in zip(a, b))


def _loop_contains_mm(edges: list[adsk.fusion.BRepEdge], point_mm: _Centre, bed: BedDir) -> bool:
    """True if point_mm lies inside the loop's bounding box on the two axes off the bed normal.
    Bbox test: exact for rectangular windows; a point in the bbox corner outside a round bore also matches."""
    a, _s = bed_axis(bed)
    mins, maxs = _edges_bbox_mm(edges)
    return all(mins[i] <= point_mm[i] <= maxs[i] for i in range(3) if i != a)


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
    strict: bool = False,
    exclude_points_mm: Sequence[_Centre] = (),
) -> adsk.fusion.ChamferFeature:
    """Elephant-foot chamfer on outer loops of the print bed face. Inner loops (holes) stay sharp unless inner_loops.

    Edges with no positive wall leg (sin phi + cos phi * tan angle_deg <= 0, e.g. 135 deg outer tips) are skipped:
    one BED_CHAMFER_SKIP print line, left sharp; DfAM reports them as WARN, not FAIL.

    min_edge_mm: skip bed edges shorter than this (mm). A ~2 mm ledge between two chamfered edges cannot take a
    1 mm chamfer chain -> ASM_BL_UNFIN_SHEET (side_wall dovetail ledge 1.92 mm, measured); 0 = all edges.
    inner_loops: also chamfer the inner loops (holes, windows) of the bed face(s), same two-distance rule and dihedral
    groups (a hole wall normal to the bed is phi 90), same min_edge_mm. False (default) keeps holes sharp = fits.

    Per-loop fallback (active when inner_loops, exclude_points_mm or strict is given; default calls keep the single
    feature / RuntimeError behaviour): all loops go into ONE chamfer feature with one isFlipped value. If that fails,
    each loop is tested alone under both orientations; a chamfer.add() exception counts as a failed test. Loops with no
    valid orientation are left sharp, printed as BED_CHAMFER_LOOP_SKIP centre_mm=(x, y, z) reason. The loops that pass
    are rebuilt in the orientation that keeps the most loops (returned feature, label `label`). Loops valid ONLY in the
    other orientation get a second feature, label `<label>_flip`, same rules with the other isFlipped (not returned,
    still on the timeline). If that second feature fails as a whole, its loops are printed as skipped (reason names it).
    Skipped INNER loops are recorded on the body (attr dfam/bed_loop_skip); dfam_check then treats their rim as a bed
    edge: sharp -> DFAM_FAIL bed_chamfer unless dfam_skip. RuntimeError only when no loop passes, or when strict=True and
    any loop has no valid orientation or its feature fails (then no feature is created).
    exclude_points_mm: global (x, y, z) mm points; an INNER loop whose bounding box (off the bed normal) contains one is
    dropped silently (deliberate sharp bore, e.g. a shaft hole). Outer loops are never excluded.

    bed = bed face direction ('-z' default = min-Z face; '+x' = X-max face). Stored on body as dfam/bed (set_bed)
    so report() checks the same bed. Chamfer face |n| is read on the bed axis.
    distance = 90-deg-equivalent WALL leg (mm expr or Ex): bed footprint = distance / tan(angle_deg) for every phi; wall leg = distance * bed_wall_ratio(90, angle_deg) / bed_wall_ratio(phi, angle_deg), written as expression text = distance / (sin(phi) + cos(phi) * tan(angle_deg)). Both legs are timeline expressions: angle_deg is in the text, phi is the measured dihedral literal. phi = the
    edge's interior dihedral (90 square corner, 105 for a 15 deg dovetail wall). Edges are grouped by phi: one two-distance
    edge set per group, all in ONE feature.
    angle_deg = chamfer FACE angle FROM HORIZONTAL, must be in (45, 90) exclusive, else ValueError.
    46 (default) -> |n_bed| = cos 46 = 0.695 < 0.707 (DfAM). 45 gives 0.7071 and FAILS, so it is rejected (no equal legs).
    Two-distance sets (addTwoDistancesChamferEdgeSet). The Fusion API does not document which distance lands on the bed
    face, so isFlipped=False is tried first, then isFlipped=True. An orientation is accepted only if every new chamfer
    face's angle acos(|n_bed|) equals angle_deg within 0.5 deg AND |n_bed| < 0.707.
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
    fallback = inner_loops or bool(exclude_points_mm) or strict
    flip_label = f"{label}_flip"  # second feature for loops valid only in the other isFlipped (fallback only)
    if fallback:
        check_label(flip_label)
    # bed leg = distance * cot(angle) = distance * tan(90 - angle), same for every phi (90-deg footprint rule).
    if angle_expr is None:
        bed_leg = f"({side}) * tan({90.0 - angle_deg!r} deg)"
        angle_ref = f"{angle_deg!r} deg"
    else:
        bed_leg = f"({side}) * tan(90 deg - ({angle_expr}))"
        angle_ref = f"({angle_expr})"
    orient_reason = f"no orientation gives chamfer face angle {angle_deg} deg with |n_bed| < {_BED_MAX_NZ}"

    def prepare(report: bool) -> tuple[list[_LoopW], list[_Skipped]]:
        """Fresh read of the bed loops -> (loops to chamfer, loops left sharp). Raises if nothing is left."""
        items: list[_LoopW] = []
        skipped: list[_Skipped] = []
        phi_skipped: dict[float, int] = {}
        for outer, edges in _bed_loops(body, only_largest_bed_face, bed):
            if not outer and not inner_loops:
                continue
            if not outer and exclude_points_mm and any(_loop_contains_mm(edges, p, bed) for p in exclude_points_mm):
                if report:
                    cx, cy, cz = _loop_centre_mm(edges)
                    print(f"BED_CHAMFER_EXCLUDE centre_mm=({cx:.2f}, {cy:.2f}, {cz:.2f}) label={label}")
                continue
            kept_edges = [e for e in edges if min_edge_mm <= 0 or e.length * 10.0 >= min_edge_mm]
            if not kept_edges:
                continue
            work: _Work = []
            for e in kept_edges:
                phi = round(_bed_dihedral_deg(e, bed), 1)
                if bed_chamfer_feasible(phi, angle_deg):
                    work.append((phi, e))
                else:
                    phi_skipped[phi] = phi_skipped.get(phi, 0) + 1
            if work:
                items.append((work, edges, outer))
            else:
                skipped.append((edges, f"no positive wall leg at {angle_deg} deg", outer))
        if report and phi_skipped:
            n_skip = sum(phi_skipped.values())
            phis = ", ".join(f"{phi:g}" for phi in sorted(phi_skipped))
            print(f"BED_CHAMFER_SKIP {n_skip} edges (dihedral {phis} deg) label={label}")
        if not items:
            if skipped:
                raise ValueError(
                    f"bed_chamfer {label!r}: every bed edge skipped, no positive wall leg at {angle_deg} deg"
                )
            raise ValueError(f"bed_chamfer: no outer loop edges on the {bed} (bed) face")
        return items, skipped

    def attempt(items: list[_LoopW], flip: bool) -> tuple[adsk.fusion.ChamferFeature | None, str]:
        """One chamfer feature over the given loops, one two-distance set per dihedral group. (None, '') if the
        measured face angle or |n_bed| check fails; (None, message) if add() raised (every mode: a failed attempt)."""
        groups: dict[float, list[adsk.fusion.BRepEdge]] = {}
        for work, _edges, _outer in items:
            for phi, e in work:
                groups.setdefault(phi, []).append(e)
        ci = comp.features.chamferFeatures.createInput2()
        # Expression text, not a Python float: the angle stays visible in the timeline.
        # wall leg = distance * cot(angle) / bed_wall_ratio(phi) = distance / (sin(phi) + cos(phi) * tan(angle)).
        # phi stays a literal: it is the measured dihedral of this edge group, not a user parameter.
        for phi, grp in sorted(groups.items()):
            wall_leg = f"({side}) / (sin({phi!r} deg) + cos({phi!r} deg) * tan({angle_ref}))"
            ci.chamferEdgeSets.addTwoDistancesChamferEdgeSet(collection(grp), _vi(bed_leg), _vi(wall_leg), flip, False)
        try:
            feat = comp.features.chamferFeatures.add(ci)
        except RuntimeError as exc:
            return None, str(exc)
        nz = [abs(_xyz(f.evaluator.getNormalAtPoint(f.pointOnFace)[1])[axis]) for f in feat.faces]
        angles = [math.degrees(math.acos(min(1.0, v))) for v in nz]
        if nz and max(nz) < _BED_MAX_NZ and all(abs(a - angle_deg) <= angle_tol_deg for a in angles):
            return feat, ""
        feat.deleteMe()
        return None, ""

    def finish(
        feat: adsk.fusion.ChamferFeature, skipped: list[_Skipped], name: str = label
    ) -> adsk.fusion.ChamferFeature:
        set_label(feat, name)
        feat.attributes.add(ROLE_GROUP, ROLE_NAME, ROLE_BED_CHAMFER)  # feature_quality finishing_split exemption
        set_bed(body, bed)
        if fallback:
            inner_centres: list[_Centre] = []
            for edges, reason, outer in skipped:
                cx, cy, cz = _loop_centre_mm(edges)
                print(f"BED_CHAMFER_LOOP_SKIP centre_mm=({cx:.2f}, {cy:.2f}, {cz:.2f}) {reason} label={label}")
                if not outer:
                    inner_centres.append((cx, cy, cz))
            if inner_centres:
                add_bed_loop_skips(body, inner_centres)  # DfAM: rim of these inner loops = bed edge, sharp -> FAIL
        return feat

    items, skipped = prepare(report=True)
    add_err = ""
    for flip in (False, True):
        if flip:
            items, skipped = prepare(report=False)
        feat, err = attempt(items, flip)
        if feat is not None:
            return finish(feat, skipped)
        add_err = err or add_err
    if not fallback or len(items) < 2:
        suffix = f" (add() raised: {add_err})" if add_err else ""  # add() error text kept for debugging
        raise RuntimeError(f"bed_chamfer {label!r}: {orient_reason}{suffix}")

    # whole set failed in both orientations: test every loop alone, in both orientations
    n = len(items)
    valid: list[set[bool]] = []
    errs: list[str] = [""] * n
    for i in range(n):
        ok: set[bool] = set()
        for flip in (False, True):
            fresh, _sk = prepare(report=False)
            alone, err = attempt([fresh[i]], flip)
            if alone is not None:
                alone.deleteMe()
                ok.add(flip)
            elif err:
                errs[i] = err
        valid.append(ok)
    for flip in sorted((False, True), key=lambda f: -sum(f in ok for ok in valid)):
        kept = [i for i in range(n) if flip in valid[i]]
        if not kept or (strict and not all(valid)):
            continue
        fresh, fresh_skipped = prepare(report=False)
        feat, _err = attempt([fresh[i] for i in kept], flip)
        if feat is None:
            continue
        # loops whose only valid orientation is the other one: second feature, built after the first (own bboxes
        # identify them in a fresh read; untouched by the first feature)
        other = [i for i in range(n) if i not in kept and valid[i]]
        b_feat: adsk.fusion.ChamferFeature | None = None
        if other:
            keys = [_loop_box(fresh[i][1]) for i in other]
            post, _post_skipped = prepare(report=False)
            b_items = [it for it in post if any(_same_box(_loop_box(it[1]), k) for k in keys)]
            if len(b_items) == len(other):
                b_feat, _err = attempt(b_items, not flip)
            if b_feat is None and strict:
                feat.deleteMe()
                continue
        extra: list[_Skipped] = []
        for i in range(n):
            if i in kept or (b_feat is not None and i in other):
                continue
            work, edges, outer = fresh[i]
            if valid[i]:
                reason = f"combined isFlipped={not flip} chamfer failed"
            elif errs[i]:
                reason = f"add() failed: {errs[i]}"
            else:
                reason = orient_reason
            extra.append((edges, reason, outer))
        out = finish(feat, fresh_skipped + extra)
        if b_feat is not None:
            finish(b_feat, [], flip_label)
        return out
    if strict:
        bad = [_loop_centre_mm(items[i][1]) for i in range(n) if not valid[i]]
        raise RuntimeError(f"bed_chamfer {label!r}: strict=True, loops without valid orientation at centre_mm {bad}")
    raise RuntimeError(f"bed_chamfer {label!r}: {orient_reason} (every loop tested alone and combined)")

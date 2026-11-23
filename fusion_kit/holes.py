"""Screw hole helper: round clearance hole, or slot (play > 0), with 45 deg countersink on the entry edges.

Mechanism-agnostic. Sketch must already lie on the wall face (caller). Positions are model mm.
"""

import math
from collections.abc import Callable
from typing import Literal

import adsk.core
import adsk.fusion

from fusion_kit.dfam import TEARDROP_NOTCH_MAX_D_MM, TEARDROP_NOTCH_MM, TEARDROP_WALL_DEG, teardrop_peak_mm
from fusion_kit.enums import NEGATIVE_EXTENT
from fusion_kit.expr import Ex
from fusion_kit.features import _vi, collection, extrude, profiles, select_edges
from fusion_kit.sketch import anchor, circle, fix_point, new_sketch, param_poly, to_sketch
from fusion_kit.timeline import check_label, set_label

Axis = Literal["x", "y", "z"]

_UNIT: dict[Axis, tuple[float, float, float]] = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def _in_reach(e: adsk.fusion.BRepEdge, center: tuple[float, float, float], plane_i: int, reach_mm: float) -> bool:
    """True if e's bbox lies within reach_mm of center on both in-plane axes (plane axis skipped)."""
    bb = e.boundingBox
    lo = (bb.minPoint.x, bb.minPoint.y, bb.minPoint.z)
    hi = (bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z)
    reach = reach_mm / 10  # mm -> cm
    return all(c / 10 - reach <= lo[i] and hi[i] <= c / 10 + reach for i, c in enumerate(center) if i != plane_i)


def slot_sketch(
    sketch: adsk.fusion.Sketch,
    center: tuple[float, float, float],
    direction: Axis,
    width_mm: float,
    play_mm: float,
) -> None:
    """Round hole (play 0) or centre-point slot (play > 0) of width width_mm, arc centres +-play along direction.

    Overall slot length = 2*play + width_mm. Centre anchored to model point center (mm).
    """
    if play_mm == 0:
        c = circle(sketch, center[0], center[1], width_mm / 2, z=center[2])
        anchor(sketch, c.centerSketchPoint, ref=center)
        return
    cp = sketch.sketchPoints.add(to_sketch(sketch, *center))
    ux, uy, uz = _UNIT[direction]
    tip = (center[0] + ux * play_mm, center[1] + uy * play_mm, center[2] + uz * play_mm)
    sketch.addCenterPointSlot(
        cp,
        to_sketch(sketch, *tip),
        adsk.core.ValueInput.createByString(Ex.mm(width_mm).e),
        True,
    )
    anchor(sketch, cp, ref=center)


def slot_profile(sketch: adsk.fusion.Sketch, width_mm: float, play_mm: float) -> adsk.fusion.Profile:
    """The single-loop profile whose area equals a slot/hole of width_mm and play_mm (round hole when play 0).

    Face sketches also hold pattern diamond and sliver profiles, so smallest-area picks the wrong one.
    Area match is space-agnostic (no centroid frame question). Raises unless exactly one matches (1 %).
    """
    want_mm2 = 2.0 * play_mm * width_mm + math.pi * (width_mm / 2) ** 2
    want_cm2 = want_mm2 / 100.0  # Fusion area in cm2
    hits = [
        p
        for p in sketch.profiles
        if p.profileLoops.count == 1 and abs(p.areaProperties().area - want_cm2) <= 0.01 * want_cm2
    ]
    if len(hits) != 1:
        raise RuntimeError(f"slot_profile: {len(hits)} single-loop profiles of area {want_mm2:.2f} mm2, want 1")
    return hits[0]


def chamfer_reach_mm(play_mm: float, d_mm: float) -> float:
    """Reach (mm) from the hole centre for entry-edge selection: slot outline play + d/2, plus 0.01 epsilon.

    Not head_d/2: a pad or plate edge at play + head_d/2 sits inside head_d reach and gets chamfered (Adversary).
    """
    return play_mm + d_mm / 2 + 0.01


def cut_screw_slot(
    comp: adsk.fusion.Component,
    sketch: adsk.fusion.Sketch,
    center: tuple[float, float, float],
    direction: Axis,
    d_mm: float,
    play_mm: float,
    head_d_mm: float,
    body: adsk.fusion.BRepBody,
    entry_plane: tuple[Axis, float],
    depth_mm: float,
    plane_outward: bool = True,
    *,
    cs_plane: adsk.fusion.ConstructionPlane | adsk.fusion.BRepFace,
    label: str,
) -> None:
    """One-sided cut of a screw hole (play_mm=0) or slot (play_mm>0) INTO body, then flat-bottom countersink.

    center      : model point (mm) of hole/slot centre.
    direction   : model axis the slot runs along; must lie in the sketch plane. Unused when play_mm=0.
    d_mm        : screw shank diameter / slot width (mm).
    play_mm     : screw centre travel +/- play (mm). Slot end-arc centre sits play_mm from centre; overall length = 2*play_mm + d_mm. 0 -> round hole.
    head_d_mm   : countersink diameter at entry face (mm). Flat recess depth = (head_d_mm - d_mm) / 2.
    body        : only body cut and chamfered.
    entry_plane : (axis, value_mm) of the entry face, as select_edges on_plane. A point alone does not give the plane.
    depth_mm    : cut depth into body from entry face (mm). Caller passes wall_thickness + 0.5.
                  Sketch on a face: normal = outward, so distance is negative (into body), symmetric=False.
    cs_plane    : typed plane for the countersink sketch (same plane as sketch). Caller holds the typed ref;
                  sketch.referencePlane returns Base which cannot be passed to new_sketch.
    """
    if cs_plane is None:
        raise ValueError("cut_screw_slot: cs_plane is required (pass the same ConstructionPlane used for sketch)")
    if head_d_mm <= d_mm:
        raise ValueError(f"cut_screw_slot: head_d {head_d_mm} must exceed d {d_mm} (countersink)")
    if play_mm < 0:
        raise ValueError(f"cut_screw_slot: play must be >= 0, got {play_mm}")
    if depth_mm <= 0:
        raise ValueError(f"cut_screw_slot: depth must be > 0, got {depth_mm}")

    slot_sketch(sketch, center, direction, d_mm, play_mm)

    # Sketch normal outward from body: negative distance = into body. Normal pointing into body: positive.
    dist = Ex.mm(-depth_mm if plane_outward else depth_mm)
    extrude(
        comp, slot_profile(sketch, d_mm, play_mm), dist, op="cut", symmetric=False, bodies=[body], label=f"{label}_cut"
    )

    # Entry-face edges within reach of centre: hole rim or slot loop. Outer wall rim spans further -> excluded.
    # ponytail: reach test by bbox only; a foreign edge inside play+head_d/2 of centre on the same plane would pass.
    plane_i = "xyz".index(entry_plane[0])
    reach = chamfer_reach_mm(play_mm, d_mm)
    edges = [e for e in select_edges(body, on_plane=entry_plane) if _in_reach(e, center, plane_i, reach)]
    if not edges:
        raise RuntimeError(f"cut_screw_slot: no entry edges on plane {entry_plane} near {center}")
    # Conical countersink: head_d-wide slot outline extruded with taper down to d at depth.
    # Tapered extrude, not chamfer API (fails on XZ-plane slots under run_part).
    # Wall CS_WALL_DEG from axis (not 45): ceiling n_z = -cos(46) = -0.695 passes the 0.707 overhang test.
    cs_sk = new_sketch(comp, cs_plane, label=f"{label}_cs_sketch")
    slot_sketch(cs_sk, center, direction, head_d_mm, play_mm)
    cs_prof = slot_profile(cs_sk, head_d_mm, play_mm)
    depth = countersink_depth_mm(d_mm, head_d_mm)
    cs_dist = Ex.mm(-depth if plane_outward else depth)
    taper = f"{CS_TAPER_SIGN * CS_WALL_DEG} deg"
    extrude(comp, cs_prof, cs_dist, op="cut", symmetric=False, bodies=[body], taper=taper, label=f"{label}_cs")


def cut_screw_slot_live(
    comp: adsk.fusion.Component,
    plane: adsk.fusion.ConstructionPlane,
    place: Callable[[adsk.fusion.Sketch], adsk.fusion.SketchPoint],
    center: tuple[float, float, float],
    direction: Axis,
    body: adsk.fusion.BRepBody,
    plane_outward: bool,
    width: str,
    play: str,
    head: str,
    depth: str,
    width_mm: float,
    play_mm: float,
    head_mm: float,
    *,
    label: str,
) -> None:
    """Live screw slot: like cut_screw_slot, but every size is an expression and the centre is dimensioned.

    place(sketch) -> centre SketchPoint, already drawn at center (model mm) and dimensioned to params by caller.
    width / play / head / depth: expressions (slot width, centre travel = half length, countersink diameter,
    cut depth). *_mm = their current values, only to pick the profile by area. play must be > 0 (slot).
    Countersink = head-wide slot cut with taper CS_TAPER_SIGN*CS_WALL_DEG down to width.
    """
    if play_mm <= 0:
        raise ValueError(f"cut_screw_slot_live: play must be > 0 (slot), got {play_mm}")
    cs_depth = f"(({head}) - ({width})) / 2 / tan({CS_WALL_DEG} deg)"
    ux, uy, uz = _UNIT[direction]
    tip = (center[0] + ux * play_mm, center[1] + uy * play_mm, center[2] + uz * play_mm)
    for w_expr, w_mm, dist, taper, part in (
        (width, width_mm, depth, None, label),
        (head, head_mm, cs_depth, f"{CS_TAPER_SIGN * CS_WALL_DEG} deg", f"{label}_cs"),
    ):
        sk = new_sketch(comp, plane, label=f"{part}_sketch")
        cp = place(sk)
        res = sk.addCenterPointSlot(
            cp,
            to_sketch(sk, *tip),
            adsk.core.ValueInput.createByString(w_expr),
            True,
            adsk.core.ValueInput.createByString(play),
        )
        axis_line = adsk.fusion.SketchLine.cast(res[4])  # doc: arcs, 2 lines, then centre construction line
        a, b = to_sketch(sk, *center), to_sketch(sk, *tip)
        if abs(a.y - b.y) < 1e-6:
            sk.geometricConstraints.addHorizontal(axis_line)
        elif abs(a.x - b.x) < 1e-6:
            sk.geometricConstraints.addVertical(axis_line)
        else:
            raise ValueError(f"cut_screw_slot_live: slot direction {direction} not along a sketch axis")
        signed = f"-({dist})" if plane_outward else f"({dist})"
        extrude(
            comp, slot_profile(sk, w_mm, play_mm), signed, op="cut", bodies=[body], taper=taper, label=f"{part}_cut"
        )


CS_WALL_DEG = 46.0  # cone wall angle from screw axis; 45 gives n_z = -0.7071, fails overhang check
CS_TAPER_SIGN = -1  # Fusion taper sign that narrows the cut with depth (measured live, see measure in report)


def countersink_depth_mm(d_mm: float, head_d_mm: float) -> float:
    """Cone depth from head_d at the entry face to d: (head_d - d)/2 / tan(CS_WALL_DEG)."""
    return (head_d_mm - d_mm) / 2 / math.tan(math.radians(CS_WALL_DEG))


def _hole(
    comp: adsk.fusion.Component,
    points: list[adsk.fusion.SketchPoint],
    make_input: Callable[[adsk.fusion.HoleFeatures], adsk.fusion.HoleFeatureInput],
    depth: str | Ex | None,
    through_dir: adsk.fusion.ExtentDirections,
    label: str,
) -> adsk.fusion.HoleFeature:
    """One native HoleFeature at all sketch points (one feature, not one per hole).

    depth None -> through all in through_dir. Label checked before add (no orphan feature on a bad label).
    """
    if not points:
        raise ValueError("hole: points is empty, need >= 1 SketchPoint")
    check_label(label)
    hole_features = comp.features.holeFeatures
    inp = make_input(hole_features)
    inp.setPositionBySketchPoints(collection(points))
    if depth is None:
        inp.setAllExtent(through_dir)
    else:
        inp.setDistanceExtent(_vi(depth))
    feat = hole_features.add(inp)
    set_label(feat, label)
    return feat


def hole_simple(
    comp: adsk.fusion.Component,
    points: list[adsk.fusion.SketchPoint],
    diameter: str | Ex,
    depth: str | Ex | None = None,
    *,
    label: str,
    through_dir: adsk.fusion.ExtentDirections = NEGATIVE_EXTENT,
) -> adsk.fusion.HoleFeature:
    """Plain drilled hole of diameter at each sketch point; depth from the sketch plane (None = through all)."""
    return _hole(comp, points, lambda hf: hf.createSimpleInput(_vi(diameter)), depth, through_dir, label)


def hole_counterbore(
    comp: adsk.fusion.Component,
    points: list[adsk.fusion.SketchPoint],
    diameter: str | Ex,
    cb_diameter: str | Ex,
    cb_depth: str | Ex,
    depth: str | Ex | None = None,
    *,
    label: str,
    through_dir: adsk.fusion.ExtentDirections = NEGATIVE_EXTENT,
) -> adsk.fusion.HoleFeature:
    """Counterbore at each sketch point: cb_diameter x cb_depth recess, then diameter hole to depth (None = through all)."""
    return _hole(
        comp,
        points,
        lambda hf: hf.createCounterboreInput(_vi(diameter), _vi(cb_diameter), _vi(cb_depth)),
        depth,
        through_dir,
        label,
    )


def hole_countersink(
    comp: adsk.fusion.Component,
    points: list[adsk.fusion.SketchPoint],
    diameter: str | Ex,
    cs_diameter: str | Ex,
    cs_angle: str | Ex = "90 deg",
    depth: str | Ex | None = None,
    *,
    label: str,
    through_dir: adsk.fusion.ExtentDirections = NEGATIVE_EXTENT,
) -> adsk.fusion.HoleFeature:
    """Countersink at each sketch point: cs_diameter at the face with cs_angle included, then diameter hole to depth (None = through all)."""
    return _hole(
        comp,
        points,
        lambda hf: hf.createCountersinkInput(_vi(diameter), _vi(cs_diameter), _vi(cs_angle)),
        depth,
        through_dir,
        label,
    )


def _plane_axes(normal: Axis) -> tuple[int, int, int]:
    """(normal index, in-plane u index, in-plane v index); in-plane axes in xyz order (normal y -> u=x, v=z)."""
    ni = "xyz".index(normal)
    ai, bi = [i for i in range(3) if i != ni]
    return ni, ai, bi


def _to_model(u: float, v: float, w: float, normal: Axis) -> tuple[float, float, float]:
    """Plane coords (u, v) + normal coord w (mm) -> MODEL point (mm)."""
    ni, ai, bi = _plane_axes(normal)
    p = [0.0, 0.0, 0.0]
    p[ni], p[ai], p[bi] = w, u, v
    return (p[0], p[1], p[2])


def _pt_exprs(uv: tuple[float, float], w: float, normal: Axis) -> tuple[str, str, str]:
    """Plane point (u, v) at normal coord w -> MODEL expression triple (mm) for param_poly / fix_point."""
    x, y, z = _to_model(uv[0], uv[1], w, normal)
    return (f"{x:.6f} mm", f"{y:.6f} mm", f"{z:.6f} mm")


def teardrop_points(
    cx: float, cy: float, r_mm: float, wall_deg: float = TEARDROP_WALL_DEG, up: tuple[float, float] = (0.0, 1.0)
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Teardrop in plane coords: (peak, tan_left, tan_right) for circle centre (cx, cy), radius r_mm.

    Walls tangent to the circle, wall_deg from the up axis (horiz hole: overhang from vertical, roof 90-wall_deg
    from horiz): peak at r/sin(wall_deg) along up. Tangent points sit at angle 90-wall_deg from up, so the radius
    is perpendicular to each wall there.
    """
    if r_mm <= 0:
        raise ValueError(f"teardrop_points: r_mm must be > 0, got {r_mm}")
    if not 0 < wall_deg < 90:
        raise ValueError(f"teardrop_points: wall_deg must be in (0, 90), got {wall_deg}")
    n = math.hypot(up[0], up[1])
    if n == 0:
        raise ValueError("teardrop_points: up must be non-zero")
    ux, uy = up[0] / n, up[1] / n
    wx, wy = -uy, ux  # up rotated +90 deg
    a = math.radians(wall_deg)
    sa, ca = math.sin(a), math.cos(a)
    d = r_mm / sa
    peak = (cx + ux * d, cy + uy * d)
    tan_l = (cx + r_mm * (sa * ux - ca * wx), cy + r_mm * (sa * uy - ca * wy))
    tan_r = (cx + r_mm * (sa * ux + ca * wx), cy + r_mm * (sa * uy + ca * wy))
    return peak, tan_l, tan_r


def teardrop_flat_points(
    cx: float,
    cy: float,
    r_mm: float,
    notch_mm: float = TEARDROP_NOTCH_MM,
    wall_deg: float = TEARDROP_WALL_DEG,
    up: tuple[float, float] = (0.0, 1.0),
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Notch teardrop (small hole) in plane coords: (top_l, top_r, tan_r, tan_l).

    Flanks are the same tangent lines as teardrop_points (wall_deg from up); the full point is clipped flat at
    h = r_mm + notch_mm along up. Top corners at half-width (r - sin(wall) * h) / cos(wall). Needs r > sin(wall) * h,
    else the flat top reaches the full peak r / sin(wall) and ValueError.
    """
    if r_mm <= 0 or notch_mm <= 0:
        raise ValueError(f"teardrop_flat_points: r_mm and notch_mm must be > 0, got {r_mm}, {notch_mm}")
    if not 0 < wall_deg < 90:
        raise ValueError(f"teardrop_flat_points: wall_deg must be in (0, 90), got {wall_deg}")
    n = math.hypot(up[0], up[1])
    if n == 0:
        raise ValueError("teardrop_flat_points: up must be non-zero")
    ux, uy = up[0] / n, up[1] / n
    wx, wy = -uy, ux
    a = math.radians(wall_deg)
    sa, ca = math.sin(a), math.cos(a)
    h = r_mm + notch_mm
    half = (r_mm - sa * h) / ca
    if half <= 0:
        raise ValueError(f"teardrop_flat_points: top {h:.3f} >= full peak {r_mm / sa:.3f} (r {r_mm}, wall {wall_deg})")
    top_l = (cx + h * ux - half * wx, cy + h * uy - half * wy)
    top_r = (cx + h * ux + half * wx, cy + h * uy + half * wy)
    tan_l = (cx + r_mm * (sa * ux - ca * wx), cy + r_mm * (sa * uy - ca * wy))
    tan_r = (cx + r_mm * (sa * ux + ca * wx), cy + r_mm * (sa * uy + ca * wy))
    return top_l, top_r, tan_r, tan_l


def seam_notch_points(
    cx: float, cy: float, r_mm: float, notch_mm: float = 0.3, dir_deg: float = 90.0
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """90 deg V notch in plane coords: (apex, p1, p2).

    Apex sits notch_mm outside the circle along dir_deg. Sides run at +-45 deg off the radial and meet the circle
    at p1 / p2 (first hit). Needs apex radius <= r*sqrt(2) so the sides reach the circle.
    """
    if r_mm <= 0 or notch_mm <= 0:
        raise ValueError(f"seam_notch_points: r_mm and notch_mm must be > 0, got {r_mm}, {notch_mm}")
    big_r = r_mm + notch_mm
    if big_r * big_r / 2 > r_mm * r_mm:
        raise ValueError(f"seam_notch_points: apex radius {big_r} > r*sqrt(2) {r_mm * math.sqrt(2)}; sides miss circle")
    a = math.radians(dir_deg)
    dx, dy = math.cos(a), math.sin(a)
    apex = (cx + big_r * dx, cy + big_r * dy)
    bx, by = -dx, -dy  # sides run inward from apex
    t = big_r / math.sqrt(2) - math.sqrt(r_mm * r_mm - big_r * big_r / 2)
    c45 = math.cos(math.pi / 4)
    s45 = math.sin(math.pi / 4)
    pts = []
    for sgn in (1.0, -1.0):
        sx, sy = bx * c45 - by * sgn * s45, bx * sgn * s45 + by * c45
        pts.append((apex[0] + t * sx, apex[1] + t * sy))
    return apex, pts[0], pts[1]


def sacrificial_depth(cb_depth_mm: float, layer_mm: float = 0.2) -> float:
    """Recess depth that leaves a sacrificial layer_mm skin: cb_depth - layer (mm). ValueError if not > 0."""
    depth = cb_depth_mm - layer_mm
    if depth <= 0:
        raise ValueError(f"sacrificial_depth: cb_depth {cb_depth_mm} - layer {layer_mm} must be > 0, got {depth}")
    return depth


def teardrop_hole(
    comp: adsk.fusion.Component,
    sketch: adsk.fusion.Sketch,
    center_mm: tuple[float, float, float],
    r_mm: float,
    body: adsk.fusion.BRepBody,
    depth_mm: float,
    *,
    label: str,
    wall_deg: float = TEARDROP_WALL_DEG,
    up: tuple[float, float] = (0.0, 1.0),
    normal: Axis = "y",
) -> None:
    """Teardrop hole cut symmetric depth_mm through body, on the sketch plane.

    Flat-topped notch (top at r_mm + TEARDROP_NOTCH_MM) when d <= TEARDROP_NOTCH_MAX_D_MM and that top sits below the
    full point; otherwise full point, apex r / sin(wall). Same threshold as dfam.teardrop_required_peak_mm.

    center_mm : model point (mm) of circle centre. up = (horizontal, vertical) in plane coords (xyz order, normal
                "y" -> x, z; body.py XZ window: up=(0,1) = +Z). normal = sketch plane normal axis.
    Triangle first: param_poly deletes inferred constraints of the sketch, so circle + fix_point come after it.
    Sketch must hold only this teardrop: all sketch profiles are cut.
    """
    check_label(label)
    if r_mm <= 0 or depth_mm <= 0:
        raise ValueError(f"teardrop_hole: r_mm and depth_mm must be > 0, got {r_mm}, {depth_mm}")
    ni, ai, bi = _plane_axes(normal)
    cu, cv, w = center_mm[ai], center_mm[bi], center_mm[ni]
    if 2 * r_mm <= TEARDROP_NOTCH_MAX_D_MM and r_mm + TEARDROP_NOTCH_MM < teardrop_peak_mm(r_mm, wall_deg):
        verts = list(teardrop_flat_points(cu, cv, r_mm, TEARDROP_NOTCH_MM, wall_deg, up))
    else:
        peak, tan_l, tan_r = teardrop_points(cu, cv, r_mm, wall_deg, up)
        verts = [peak, tan_l, tan_r]
    param_poly(sketch, [_pt_exprs(p, w, normal) for p in verts])
    cx, cy, cz = _to_model(cu, cv, w, normal)
    c = circle(sketch, cx, cy, r_mm, z=cz, diameter=f"{2 * r_mm:.6f} mm")
    fix_point(sketch, c.centerSketchPoint, (f"{cx:.6f} mm", f"{cy:.6f} mm", f"{cz:.6f} mm"))
    profs = profiles(sketch)
    if not profs:
        raise RuntimeError("teardrop_hole: sketch has no profiles")
    extrude(comp, profs, Ex.mm(depth_mm), op="cut", symmetric=True, bodies=[body], label=label)


def seam_notch(
    comp: adsk.fusion.Component,
    sketch: adsk.fusion.Sketch,
    center_mm: tuple[float, float, float],
    r_mm: float,
    body: adsk.fusion.BRepBody,
    depth_mm: float,
    *,
    label: str,
    notch_mm: float = 0.3,
    dir_deg: float = 90.0,
    normal: Axis = "y",
) -> None:
    """90 deg V notch at the circle edge, cut symmetric depth_mm through body. Cut AFTER the hole: its cap
    overlaps the hole void. Sketch must hold only this triangle: all sketch profiles are cut."""
    check_label(label)
    if depth_mm <= 0:
        raise ValueError(f"seam_notch: depth_mm must be > 0, got {depth_mm}")
    ni, ai, bi = _plane_axes(normal)
    cu, cv, w = center_mm[ai], center_mm[bi], center_mm[ni]
    pts = seam_notch_points(cu, cv, r_mm, notch_mm, dir_deg)
    param_poly(sketch, [_pt_exprs(p, w, normal) for p in pts])
    profs = profiles(sketch)
    if not profs:
        raise RuntimeError("seam_notch: sketch has no profiles")
    extrude(comp, profs, Ex.mm(depth_mm), op="cut", symmetric=True, bodies=[body], label=label)

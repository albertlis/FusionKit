"""Fully-constrained sketch helpers for Fusion 360.

docs/gotchas.md gotchas encoded here
--------------------------------
- addTwoPointRectangle adds NO horizontal/vertical constraints -> added explicitly.
- rect(): corners jittered by different ~0.04 cm offsets (skewed shape) to avoid Fusion inferring H/V constraints that
  conflict with explicit dimensions (VCS_SKETCH_OVER_CONSTRAINTS).
- Non-XY sketches: caller supplies points in MODEL coords; this module converts via
  sketch.modelToSketchSpace (never assume sketch axis mapping).
- Units: every geometry argument here (x, y, z, r, points) is mm; the helpers divide by 10 before calling Fusion.
  Fusion internal length is cm: .value of a length parameter and evaluateExpression return cm (user_params.snapshot_geo: value * 10).
"""

import contextlib
import math
from collections.abc import Iterator

import adsk.core
import adsk.fusion

from fusion_kit.component import active_design
from fusion_kit.enums import ALIGNED_DIM, HORIZONTAL_DIM, VERTICAL_DIM
from fusion_kit.expr import Ex, as_str
from fusion_kit.timeline import check_label, set_label
from fusion_kit.user_params import eval_fresh

P3 = adsk.core.Point3D.create

# Initial-geometry jitter (mm). Exact shared coordinates make Fusion infer alignment constraints ->
# VCS_SKETCH_OVER_CONSTRAINTS. Initial vertices / circle centres are offset; dims or anchor()/fix_point snap them exact.
JITTER_MM = 0.05
JITTER_STEP_MM = 0.013


def _jitter_offset(k: int, axis: int, jitter_mm: float = JITTER_MM) -> float:
    """Initial offset (mm) of vertex k on coordinate index axis. Distinct per k and axis."""
    return jitter_mm + JITTER_STEP_MM * k * (axis + 1)


def new_sketch(
    comp: adsk.fusion.Component,
    plane: str | adsk.fusion.ConstructionPlane | adsk.fusion.BRepFace = "xy",
    *,
    label: str,
) -> adsk.fusion.Sketch:
    """Create a sketch on a named plane, an explicit ConstructionPlane, or a planar BRepFace.

    plane: "xy" | "xz" | "yz" | adsk.fusion.ConstructionPlane | adsk.fusion.BRepFace (must be planar).
    label: snake_case timeline name (timeline.check_label); required so the timeline reads as intent.
    Sketch coords of a face sketch are in the face's own frame -> use to_sketch() for model mm.
    """
    if isinstance(plane, str):
        pl: adsk.fusion.ConstructionPlane | adsk.fusion.BRepFace = {
            "xy": comp.xYConstructionPlane,
            "xz": comp.xZConstructionPlane,
            "yz": comp.yZConstructionPlane,
        }[plane]
    else:
        pl = plane
    check_label(label)  # before add: a bad label must not leave an unnamed sketch behind
    s = comp.sketches.add(pl)
    set_label(s, label)
    return s


def to_sketch(s: adsk.fusion.Sketch, x_mm: float, y_mm: float, z_mm: float) -> adsk.core.Point3D:
    """MODEL point (mm) -> point in sketch s's own space (cm). Works for any sketch plane or face."""
    return s.modelToSketchSpace(P3(x_mm / 10, y_mm / 10, z_mm / 10))


def _centre_mm(v: float | str | Ex) -> float:
    """Centre coordinate: float = mm; expression string / Ex = evaluated via mm() (fresh, live params)."""
    return mm(as_str(v)) if isinstance(v, str | Ex) else float(v)


def circle(
    s: adsk.fusion.Sketch,
    x: float | str | Ex,
    y: float | str | Ex,
    r: float,
    z: float | str | Ex = 0.0,
    diameter: str | Ex | None = None,
    *,
    concentric_to: adsk.fusion.SketchCircle | adsk.fusion.SketchArc | None = None,
) -> adsk.fusion.SketchCircle:
    """Circle centred at MODEL point (x, y, z), radius r mm. diameter -> parametric diameter dim.

    concentric_to (opt-in): centre = centre of that SketchCircle / SketchArc, tied by addConcentric. x, y, z are then
    unused (no centre position dims from them); radius and diameter dim behave as before.

    x, y, z: mm float, or expression string / Ex. Expressions are evaluated here for the INITIAL drawn position only;
    the parametric position comes from anchor() / fix_point(). Part code: circle(s, "cx_expr", "cy_expr", r) — no mm().

    Centre stays free: position it with anchor(). Initial centre is offset in-plane by _jitter_offset (same as
    param_poly vertices) so Fusion infers no alignment with existing vertices; anchor/fix_point snap it exact.
    k = circles already in the sketch: two circles requested at the same centre must not land on one point
    (Fusion then infers coincidence -> VCS_SKETCH_OVER_CONSTRAINTS).
    The centre is projected onto the sketch plane (sketch z = 0): on an offset plane a model point off the plane
    keeps a nonzero sketch z, the circle floats off-plane and its extrude starts there (cut misses the body).
    """
    if concentric_to is None:
        c0 = to_sketch(s, _centre_mm(x), _centre_mm(y), _centre_mm(z))
    else:
        c0 = concentric_to.centerSketchPoint.geometry
    k = s.sketchCurves.sketchCircles.count
    cx = c0.x + _jitter_offset(k, 0) / 10
    cy = c0.y + _jitter_offset(k, 1) / 10
    c = s.sketchCurves.sketchCircles.addByCenterRadius(P3(cx, cy, 0.0), r / 10)
    if concentric_to is not None:
        s.geometricConstraints.addConcentric(c, concentric_to)
    if diameter is not None:
        g = c.centerSketchPoint.geometry
        s.sketchDimensions.addDiameterDimension(c, P3(g.x + r / 10, g.y + r / 10, 0)).parameter.expression = as_str(
            diameter
        )
    return c


def fillet_corner(
    s: adsk.fusion.Sketch,
    line_a: adsk.fusion.SketchLine,
    line_b: adsk.fusion.SketchLine,
    corner: adsk.fusion.SketchPoint,
    radius_expr: str | Ex,
    radius_mm: float,
) -> adsk.fusion.SketchArc:
    """Sketch fillet at the shared corner of two lines; radius bound to radius_expr via a radial dim.

    corner: sketch point where line_a and line_b meet (endpoint of both); Fusion picks the quadrant from it.
    radius_mm: current value of radius_expr in mm, used for the initial geometry (must match, else the dim re-solves it).
    Raises if addFillet returns None.
    """
    arc = s.sketchCurves.sketchArcs.addFillet(line_a, corner.geometry, line_b, corner.geometry, radius_mm / 10)
    if arc is None:
        raise RuntimeError("fillet_corner: addFillet returned None (lines not joined at corner?)")
    g = arc.centerSketchPoint.geometry
    s.sketchDimensions.addRadialDimension(
        arc, P3(g.x + radius_mm / 10, g.y + radius_mm / 10, 0)
    ).parameter.expression = as_str(radius_expr)
    return arc


def poly(
    s: adsk.fusion.Sketch,
    pts: list[tuple[float, float]] | list[tuple[float, float, float]],
    *,
    lock_axes: bool = False,
) -> list[adsk.fusion.SketchLine]:
    """Closed polygon from MODEL points (mm); 2-tuples mean z=0. No dimensions — use dim() to constrain.

    Returns the SketchLines: line i joins vertex i to vertex i+1 (last closes to first).
    lock_axes=True adds addHorizontal/addVertical to edges axis-aligned in sketch space (|d| < 1e-9 cm).
    """
    if len(pts) < 3:
        raise ValueError(f"poly: need >=3 points for a closed polygon, got {len(pts)}")
    sp = [to_sketch(s, p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in pts]
    lines = s.sketchCurves.sketchLines
    first = prev = lines.addByTwoPoints(sp[0], sp[1])
    out = [first]
    for q in sp[2:]:
        prev = lines.addByTwoPoints(prev.endSketchPoint, q)
        out.append(prev)
    out.append(lines.addByTwoPoints(prev.endSketchPoint, first.startSketchPoint))
    if lock_axes:
        gc = s.geometricConstraints
        n = len(sp)
        for i, ln in enumerate(out):
            a, b = sp[i], sp[(i + 1) % n]
            if abs(b.x - a.x) < 1e-9:
                gc.addVertical(ln)
            if abs(b.y - a.y) < 1e-9:
                gc.addHorizontal(ln)
    return out


RECT_JITTER = 0.04  # cm


def rect(
    s: adsk.fusion.Sketch,
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> list[adsk.fusion.SketchLine]:
    """Sketch-axis-aligned rectangle from two opposite MODEL corners (mm). Adds H/V constraints.

    Two corners get different ~0.04 cm offsets so the initial shape is skewed (no inferred H/V
    constraints, docs/gotchas.md). The drawn rect is therefore NOT at exact size/position: caller MUST
    dimension it (dim()) and anchor() corner a
    (startSketchPoint of line 0, never jittered) so the solver snaps it exact. Returns 4 SketchLines [bottom, right, top, left].
    """
    j = RECT_JITTER
    pa, pb = to_sketch(s, *a), to_sketch(s, *b)
    c = [(pa.x, pa.y), (pb.x + j, pa.y + j), (pb.x, pb.y), (pa.x - j, pb.y - j)]
    pts = [P3(x, y, 0) for x, y in c]
    lines = s.sketchCurves.sketchLines
    l0 = lines.addByTwoPoints(pts[0], pts[1])
    l1 = lines.addByTwoPoints(l0.endSketchPoint, pts[2])
    l2 = lines.addByTwoPoints(l1.endSketchPoint, pts[3])
    l3 = lines.addByTwoPoints(l2.endSketchPoint, l0.startSketchPoint)
    gc = s.geometricConstraints
    gc.addHorizontal(l0)
    gc.addHorizontal(l2)
    gc.addVertical(l1)
    gc.addVertical(l3)
    return [l0, l1, l2, l3]


def dim(
    s: adsk.fusion.Sketch,
    a: adsk.fusion.SketchPoint,
    b: adsk.fusion.SketchPoint,
    expr: str | Ex,
    orientation: adsk.fusion.DimensionOrientations = ALIGNED_DIM,
) -> adsk.fusion.SketchDimension:
    """Distance dimension between two sketch points, bound to expr (aligned by default)."""
    ga, gb = a.geometry, b.geometry
    mid = P3((ga.x + gb.x) / 2 + 0.3, (ga.y + gb.y) / 2 + 0.3, 0)
    d = s.sketchDimensions.addDistanceDimension(a, b, orientation, mid)
    d.parameter.expression = as_str(expr)
    return d


Vec = tuple[float, float, float]


def project_point(s: adsk.fusion.Sketch, entity: adsk.core.Base) -> adsk.fusion.SketchPoint:
    """Associative projection of one point-like entity (construction point, other sketch's point) into s."""
    got = s.project2([entity], True)  # linked: follows the source when params change
    sp = adsk.fusion.SketchPoint.cast(got[0]) if len(got) == 1 else None
    if sp is None:
        raise RuntimeError(f"project_point: projection gave {len(got)} entities, want 1 SketchPoint")
    return sp


def dim_from(
    s: adsk.fusion.Sketch,
    ref: adsk.fusion.SketchPoint,
    ref_mm: Vec,
    p: adsk.fusion.SketchPoint,
    offsets: list[tuple[Vec, str | Ex]],
) -> None:
    """Live position of p: one H/V dim from ref per (MODEL unit vector, expr) offset.

    ref_mm = ref's model position (mm), only used to map each unit vector onto a sketch axis
    (sketch x -> horizontal dim, sketch y -> vertical); a vector not along a sketch axis -> ValueError.
    Dims are unsigned: p must already sit on the right side (draw it at the evaluated position), offsets != 0.
    """
    o = to_sketch(s, *ref_mm)
    for u, e in offsets:
        q = to_sketch(s, ref_mm[0] + u[0], ref_mm[1] + u[1], ref_mm[2] + u[2])
        dx, dy = abs(q.x - o.x), abs(q.y - o.y)  # 1 mm unit -> 0.1 cm
        if dy < 1e-6 and dx > 0.09:
            dim(s, ref, p, e, HORIZONTAL_DIM)
        elif dx < 1e-6 and dy > 0.09:
            dim(s, ref, p, e, VERTICAL_DIM)
        else:
            raise ValueError(f"dim_from: model direction {u} is not along a sketch axis (dx={dx}, dy={dy} cm)")


def polar_point(
    s: adsk.fusion.Sketch,
    centre_point: adsk.fusion.SketchPoint,
    radius_expr: str | Ex,
    angle_expr: str | Ex,
    *,
    radius_mm: float,
    angle_deg: float,
) -> adsk.fusion.SketchPoint:
    """Sketch point at polar offset (radius_expr, angle_expr) from centre_point. Fully constrained by its 2 dims.

    radius_mm / angle_deg: current values, initial geometry only (dims re-solve it, as fillet_corner radius_mm).
    angle_expr: unsigned, from sketch +X counter-clockwise, in (0, 180) deg, e.g. "30 deg".
    Build: construction reference line centre -> (+X, radius), made horizontal, end dimensioned by radius_expr
    (horizontal dim); construction radial line centre -> point, point dimensioned by radius_expr (aligned) and an angular
    dim (reference line vs radial line) bound to angle_expr. Returns the live point (radial line end).
    """
    if not 0.0 < angle_deg < 180.0:
        raise ValueError(f"polar_point: angle_deg must be in (0, 180), got {angle_deg}")
    g = centre_point.geometry
    lines = s.sketchCurves.sketchLines
    k = lines.count
    rc = radius_mm / 10
    a = math.radians(angle_deg)
    ref = lines.addByTwoPoints(centre_point, P3(g.x + rc, g.y, 0.0))
    ref.isConstruction = True
    s.geometricConstraints.addHorizontal(ref)
    dim(s, centre_point, ref.endSketchPoint, radius_expr, HORIZONTAL_DIM)
    rad = lines.addByTwoPoints(
        centre_point,
        P3(g.x + rc * math.cos(a) + _jitter_offset(k, 0) / 10, g.y + rc * math.sin(a) + _jitter_offset(k, 1) / 10, 0.0),
    )
    rad.isConstruction = True
    p = rad.endSketchPoint
    dim(s, centre_point, p, radius_expr)
    ang = s.sketchDimensions.addAngularDimension(ref, rad, P3(g.x + rc / 2, g.y + rc / 2, 0.0))
    ang.parameter.expression = as_str(angle_expr)
    return p


def _mm_expr(v: float) -> str:
    """|v| mm as expression: 6 decimals, trailing zeros stripped. Keeps precision (:g keeps only 6 sig digits)."""
    return f"{abs(v):.6f}".rstrip("0").rstrip(".") + " mm"


def anchor(
    s: adsk.fusion.Sketch,
    p: adsk.fusion.SketchPoint,
    h: str | Ex | None = None,
    v: str | Ex | None = None,
    ref: tuple[float, float, float] | None = None,
) -> None:
    """Fix point p relative to sketch origin (or ref), in SKETCH axes.

    h / v = horizontal / vertical distance expr; None = p lies on that origin line (zero distance,
    a 0 dim is invalid in Fusion -> point-alignment constraint instead). Both None -> coincident.
    Distance dims are UNSIGNED: the side of the origin is decided by p's initial coordinates,
    not by the sign of h / v. Verify the side with body.pointContainment, never from the print.

    ref  : MODEL point (mm) to measure from instead of the origin. A sketch point is added at ref and
           fixed to the origin by |coord| dims in sketch axes (zero coord -> alignment); h / v then
           measure from ref. Optional, far-reference style: e.g. (-50, -50, 0) with h / v as
           "(expr) - (-50 mm)". Flip not reproduced live (origin dims also tracked negative values, see
           a GitHub issue (gap template)), so ref is not a flip fix.
           Works for circle centres (circle(...).centerSketchPoint) and polygon vertices.
    """
    if ref is not None:
        sp = to_sketch(s, *ref)
        r = s.sketchPoints.add(sp)
        # dims from SKETCH coords (cm -> mm): on XZ/YZ sketches model axes are mixed into sketch axes.
        sx, sy = sp.x * 10, sp.y * 10
        anchor(
            s,
            r,
            None if abs(sx) < 1e-6 else _mm_expr(sx),  # tolerance: transform may give 1e-17
            None if abs(sy) < 1e-6 else _mm_expr(sy),
        )
        _anchor_to(s, p, r, h, v)
        return
    _anchor_to(s, p, s.originPoint, h, v)


def _anchor_to(
    s: adsk.fusion.Sketch,
    p: adsk.fusion.SketchPoint,
    o: adsk.fusion.SketchPoint,
    h: str | Ex | None,
    v: str | Ex | None,
) -> None:
    """Constrain p relative to reference point o (origin or ref point), see anchor()."""
    gc = s.geometricConstraints
    if h is None and v is None:
        gc.addCoincident(p, o)
        return
    if h is None:
        gc.addVerticalPoints(o, p)
    else:
        dim(s, o, p, h, HORIZONTAL_DIM)
    if v is None:
        gc.addHorizontalPoints(o, p)
    else:
        dim(s, o, p, v, VERTICAL_DIM)


Pt = tuple[str, str, str]  # MODEL x, y, z as signed expressions


def mm(expr: str) -> float:
    """Expression -> mm. Derived user params are re-evaluated from .expression (no stale .value)."""
    return eval_fresh(active_design(), expr, "mm") * 10


def _sketch_dims(s: adsk.fusion.Sketch, p: Pt) -> tuple[str | None, str | None]:
    """(h, v) unsigned dim exprs from sketch origin for model point p; None = on that origin line."""
    o = to_sketch(s, 0.0, 0.0, 0.0)
    if abs(o.x) > 1e-7 or abs(o.y) > 1e-7:
        raise RuntimeError(f"sketch origin is not the model-origin projection ({o.x}, {o.y})")
    out: list[str | None] = []
    for k in (0, 1):
        found = False
        for i in range(3):
            u = [0.0, 0.0, 0.0]
            u[i] = 1.0
            q = to_sketch(s, u[0], u[1], u[2])
            d = (q.x - o.x) if k == 0 else (q.y - o.y)
            if abs(d) > 0.05:  # 1 mm = 0.1 cm along this sketch axis
                v = mm(p[i])
                out.append(None if abs(v) < 1e-6 else (p[i] if v > 0 else f"-({p[i]})"))
                found = True
                break
        if not found:
            raise RuntimeError(f"sketch axis {k} not along a model axis")
    return out[0], out[1]


def _sign(v: float) -> int:
    return (v > 0) - (v < 0)


def _flip_coord(cur: float, t: float) -> float | None:
    """New sketch coord for one axis, or None = leave it. Only a point on the WRONG side of the axis moves.

    |t| <= 1e-6 (target on the axis) or same sign as target -> None. Else t + RECT_JITTER*sign(t): on the target's
    side, just off the exact value (exact shared coords -> inferred constraints, VCS_SKETCH_OVER_CONSTRAINTS).
    """
    if abs(t) <= 1e-6 or _sign(cur) == _sign(t):
        return None
    return t + RECT_JITTER * _sign(t)


def _anchor_axis(s: adsk.fusion.Sketch, p: adsk.fusion.SketchPoint, k: int, expr: str | None) -> None:
    """One sketch axis of anchor(): k=0 x (horizontal dim), k=1 y (vertical dim). expr None = p on that origin line."""
    o = s.originPoint
    gc = s.geometricConstraints
    if k == 0:
        if expr is None:
            gc.addVerticalPoints(o, p)
        else:
            dim(s, o, p, expr, HORIZONTAL_DIM)
    elif expr is None:
        gc.addHorizontalPoints(o, p)
    else:
        dim(s, o, p, expr, VERTICAL_DIM)


def fix_point(
    s: adsk.fusion.Sketch,
    sp: adsk.fusion.SketchPoint,
    p: Pt,
    *,
    skip_axes: frozenset[int] = frozenset(),
) -> None:
    """Dimension sp from the sketch origin to model point p (expressions).

    skip_axes: sketch axes (0 = x, 1 = y) given no dim and no origin-line constraint here; the linked vertex that
    owns the axis (param_poly hv=True) dims it. Empty = anchor() as before.

    Dims are unsigned: the side comes from sp's current position. So before any dim or constraint, each sketch
    axis where sp sits on the wrong side (sign(current) != sign(target), target not 0) is moved to target +
    RECT_JITTER on the target's side; an earlier solve may have dragged it across an axis. Other axes and
    same-side points are not moved; when nothing flips no move() call is made. move() refusing
    (constraint) -> RuntimeError.
    Concentric circles: fix one centre, tie the others with addCoincident (a second fix_point on the same
    location over-constrains, VCS_SKETCH_OVER_CONSTRAINTS).
    """
    target = to_sketch(s, mm(p[0]), mm(p[1]), mm(p[2]))
    cur = sp.geometry
    nx = _flip_coord(cur.x, target.x)
    ny = _flip_coord(cur.y, target.y)
    if nx is not None or ny is not None:
        new = P3(cur.x if nx is None else nx, cur.y if ny is None else ny, cur.z)
        delta = cur.vectorTo(new)
        if not sp.move(delta):
            raise RuntimeError(f"fix_point: SketchPoint.move refused, delta {delta.length} cm")
    h, v = _sketch_dims(s, p)
    if not skip_axes:
        anchor(s, sp, h, v)
        return
    for k, expr in ((0, h), (1, v)):
        if k not in skip_axes:
            _anchor_axis(s, sp, k, expr)


def _drop_inferred(s: adsk.fusion.Sketch, before: set[str]) -> None:
    """Delete geometric constraints not in `before` (created by the polygon call just made: drawing inference)."""
    for gc in list(s.geometricConstraints):
        if gc.entityToken not in before:
            gc.deleteMe()


def _norm_expr(e: str) -> str:
    return "".join(e.split())


def _in_plane_axes(s: adsk.fusion.Sketch) -> list[tuple[int, int]]:
    """(model axis i, sketch axis k) for each in-plane model axis; the normal axis is left out."""
    o = to_sketch(s, 0.0, 0.0, 0.0)
    units = [to_sketch(s, 1.0, 0.0, 0.0), to_sketch(s, 0.0, 1.0, 0.0), to_sketch(s, 0.0, 0.0, 1.0)]
    normal = max(range(3), key=lambda i: abs(units[i].z - o.z))
    return [(i, 0 if abs(units[i].x - o.x) > abs(units[i].y - o.y) else 1) for i in range(3) if i != normal]


def _hv_plan(s: adsk.fusion.Sketch, pts: list[Pt]) -> tuple[list[frozenset[int]], list[tuple[int, int]]]:
    """hv=True plan: (per-vertex sketch axes to skip, (edge j, sketch axis k) H/V links).

    Node (v, k) = sketch coordinate k of vertex v. Edge j (vertex j -> j+1 mod n) whose endpoints have the same
    normalised expr on an in-plane model axis mapped to sketch axis k is a link: nodes (j, k) and (j+1, k) are equal,
    k=1 -> addHorizontal (sketch y constant), k=0 -> addVertical. Linked nodes form sets; the lowest node of each set
    is dimensioned / anchored, the others are skipped. Non-degenerate polygon -> links form a forest -> fully constrained.
    """
    n = len(pts)
    links = [
        (j, k)
        for i, k in _in_plane_axes(s)
        for j in range(n)
        if _norm_expr(pts[j][i]) == _norm_expr(pts[(j + 1) % n][i])
    ]
    parent = {(v, k): (v, k) for v in range(n) for k in (0, 1)}

    def find(x: tuple[int, int]) -> tuple[int, int]:
        while parent[x] != x:
            x = parent[x]
        return x

    for j, k in links:
        a, b = find((j, k)), find(((j + 1) % n, k))
        if a != b:
            lo, hi = sorted((a, b))
            parent[hi] = lo  # root = lowest node of its set
    skip = [frozenset(k for k in (0, 1) if find((v, k)) != (v, k)) for v in range(n)]
    return skip, links


def model_axis_to_sketch(s: adsk.fusion.Sketch, model_axis: int) -> int:
    """Sketch axis (0 = x, 1 = y) that model axis model_axis (0 = x, 1 = y, 2 = z) runs along in sketch s."""
    o = to_sketch(s, 0.0, 0.0, 0.0)
    unit = [0.0, 0.0, 0.0]
    unit[model_axis] = 1.0
    q = to_sketch(s, unit[0], unit[1], unit[2])
    return 0 if abs(q.x - o.x) > abs(q.y - o.y) else 1


SegPlan = tuple[int, int, int]  # (link sketch axis, length sketch axis, length model axis)


def _segment_plan(s: adsk.fusion.Sketch, pts: list[Pt]) -> list[SegPlan]:
    """mode="segments": SegPlan per edge j (vertex j -> j+1 mod n). Only axis-parallel edges accepted.

    Exactly one in-plane coord expr equal at both ends -> H/V link on that sketch axis; the other in-plane model axis is
    the length. Diagonal edge (both coords differ, or both equal) -> ValueError: an angle constraint would be needed.
    """
    n = len(pts)
    axes = _in_plane_axes(s)
    plan: list[SegPlan] = []
    for j in range(n):
        a, b = pts[j], pts[(j + 1) % n]
        eq = [k for i, k in axes if _norm_expr(a[i]) == _norm_expr(b[i])]
        ln = [(i, k) for i, k in axes if _norm_expr(a[i]) != _norm_expr(b[i])]
        if len(eq) != 1 or len(ln) != 1:
            raise ValueError(
                f"param_poly mode='segments': edge {j} is not axis-parallel (exactly one in-plane coord expr equal at both ends)"
            )
        plan.append((eq[0], ln[0][1], ln[0][0]))
    return plan


def _segments_dims(
    s: adsk.fusion.Sketch, pts: list[Pt], lines: list[adsk.fusion.SketchLine], plan: list[SegPlan]
) -> None:
    """mode="segments" constraints: H/V link per edge, one length dim per edge, first vertex anchored by fix_point.

    One union-find graph per sketch axis: nodes = vertex coordinates + sketch origin; links and length dims are edges.
    A length dim whose ends are already joined is redundant (it would over-constrain) and is not added. Afterwards every
    vertex coordinate must reach the origin, else ValueError (under-constrained).
    """
    n = len(pts)
    parent = list(range(2 * n + 2))  # node 2*v + k: vertex v, sketch axis k; node 2*n + k: origin on axis k

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def join(x: int, y: int) -> bool:
        rx, ry = find(x), find(y)
        if rx == ry:
            return False
        parent[ry] = rx
        return True

    fix_point(s, lines[0].startSketchPoint, pts[0])
    join(0, 2 * n)
    join(1, 2 * n + 1)
    gc = s.geometricConstraints
    for j, (k_link, _k_len, _i_len) in enumerate(plan):
        nxt = (j + 1) % n
        if not join(2 * j + k_link, 2 * nxt + k_link):
            raise ValueError(f"param_poly mode='segments': link on edge {j} is redundant")
        if k_link == 1:
            gc.addHorizontal(lines[j])
        else:
            gc.addVertical(lines[j])
    for j, (_k_link, k_len, i_len) in enumerate(plan):
        nxt = (j + 1) % n
        if not join(2 * j + k_len, 2 * nxt + k_len):
            continue  # ends already fixed relative to each other: this length is implied, dim would over-constrain
        a, b = pts[j][i_len], pts[nxt][i_len]
        va, vb = mm(a), mm(b)
        if abs(vb - va) < 1e-9:
            raise ValueError(f"param_poly mode='segments': edge {j} has zero length")
        expr = f"({b}) - ({a})" if vb > va else f"({a}) - ({b})"
        dim(s, lines[j].startSketchPoint, lines[j].endSketchPoint, expr, HORIZONTAL_DIM if k_len == 0 else VERTICAL_DIM)
    for k in (0, 1):
        for v in range(n):
            if find(2 * v + k) != find(2 * n + k):
                raise ValueError(f"param_poly mode='segments': vertex {v} axis {k} not fixed (under-constrained)")


def param_poly(
    s: adsk.fusion.Sketch,
    pts: list[Pt],
    *,
    jitter_mm: float = JITTER_MM,
    hv: bool = False,
    skip_axes: list[frozenset[int]] | None = None,
    mode: str = "vertices",
) -> list[adsk.fusion.SketchLine]:
    """Closed polygon, every vertex dimensioned to its model-coordinate expressions (fully constrained).

    mode="segments" (opt-in, axis-parallel edges only): H/V link per edge, one length dim per edge (redundant ones
    skipped, see _segments_dims), only the first vertex anchored by coordinate dims. hv / skip_axes not allowed.

    hv=True (opt-in): an edge whose endpoints carry the same coordinate expr (whitespace-insensitive string) on an
    in-plane axis gets addHorizontal / addVertical, and that coordinate is dimensioned once per linked set instead of
    per vertex (_hv_plan). Still fully constrained, expressions stay live. hv=False: one dim per nonzero coordinate.

    Per-vertex in-plane start offset = jitter_mm + JITTER_STEP_MM * k * (axis + 1) mm (gaps L62/L64: exact shared coords ->
    inferred alignment -> VCS_SKETCH_OVER_CONSTRAINTS); the vertex dims snap it back. Geometric constraints created by
    this call (drawing inference) are deleted before the dims go on; constraints already on the sketch are kept. Out-of-plane coordinate stays exact. Line i starts at vertex i.

    `deleteMe`, `addHorizontal`, `addVertical` (hv=True)

    skip_axes (hv=False only): per vertex, sketch axes (0 = x, 1 = y) given no dim. Initial placement only; the caller
    constrains them (geometric constraints). None = dim every nonzero coord as before.
    """
    if skip_axes is not None and (hv or len(skip_axes) != len(pts)):
        raise ValueError("param_poly: skip_axes needs hv=False and one entry per vertex")
    if mode not in ("vertices", "segments"):
        raise ValueError(f"param_poly: mode must be 'vertices' or 'segments', got {mode!r}")
    if mode == "segments" and (hv or skip_axes is not None):
        raise ValueError("param_poly: mode='segments' takes neither hv nor skip_axes")
    seg_plan = _segment_plan(s, pts) if mode == "segments" else None
    o = to_sketch(s, 0.0, 0.0, 0.0)
    units = [
        to_sketch(s, 1.0, 0.0, 0.0),
        to_sketch(s, 0.0, 1.0, 0.0),
        to_sketch(s, 0.0, 0.0, 1.0),
    ]
    normal = max(range(3), key=lambda i: abs(units[i].z - o.z))
    vals: list[tuple[float, float, float]] = []
    for k, (a, b, c) in enumerate(pts):
        v = [mm(a), mm(b), mm(c)]
        for i in range(3):
            if i != normal:
                v[i] += _jitter_offset(k, i, jitter_mm)
        vals.append((v[0], v[1], v[2]))
    before = {
        gc.entityToken for gc in s.geometricConstraints
    }  # constraints already on the sketch (user, earlier polygons)
    lines = poly(s, vals)
    _drop_inferred(s, before)  # inferred alignments would fight the vertex dims
    if seg_plan is not None:
        _segments_dims(s, pts, lines, seg_plan)
        return lines
    if not hv:
        for ln, p, sk in zip(lines, pts, skip_axes or [frozenset()] * len(pts)):
            fix_point(s, ln.startSketchPoint, p, skip_axes=sk)
        return lines
    skip, links = _hv_plan(s, pts)
    for ln, p, sk in zip(lines, pts, skip):
        fix_point(s, ln.startSketchPoint, p, skip_axes=sk)
    gc = s.geometricConstraints
    for j, k in links:  # after the dims: a move() of a constrained point could be refused
        if k == 1:
            gc.addHorizontal(lines[j])
        else:
            gc.addVertical(lines[j])
    return lines


def fixed_poly(s: adsk.fusion.Sketch, pts: list[Pt]) -> list[adsk.fusion.SketchLine]:
    """Closed polygon at EXACT model values; every vertex SketchPoint set isFixed. No dimensions, no parameters.

    Same input as param_poly (model-coordinate expression triples), line i starts at vertex i. Geometric constraints
    created by this call (drawing inference) are deleted; constraints already on the sketch are kept. Fixed points
    make the sketch fully constrained without any dim.

    Use when the profiles are picked in code (keep()) or the sketch is rebuild-only: live parameter change already needs a
    full rebuild there, so parametric dims buy nothing. Measured: param_poly took 46 s for one 64-dim rib band sketch
    (fix_point = 2 expression dims per vertex, ~0.4 s each); this helper does not create those dims.
    NOT parametric: the coordinates are frozen at build time. Use param_poly for anything a user edits as a parameter.
    """
    vals = [(mm(a), mm(b), mm(c)) for a, b, c in pts]
    before = {
        gc.entityToken for gc in s.geometricConstraints
    }  # constraints already on the sketch (user, earlier polygons)
    lines = poly(s, vals)
    _drop_inferred(s, before)
    for ln in lines:
        ln.startSketchPoint.isFixed = True
    return lines


def rect_two_corners(s: adsk.fusion.Sketch, a: Pt, b: Pt, side_h: str, side_v: str) -> None:
    """Axis-aligned rectangle between opposite model corners a, b (expr tuples), fully constrained.

    side_h / side_v = length expr along SKETCH x / SKETCH y (caller maps model axes to sketch axes).
    Corner a is anchored from the sketch origin. 4 dims in total; no timeline item (sketch only).
    """
    bottom, right, _, _ = rect(s, (mm(a[0]), mm(a[1]), mm(a[2])), (mm(b[0]), mm(b[1]), mm(b[2])))
    dim(s, bottom.startSketchPoint, bottom.endSketchPoint, side_h)
    dim(s, right.startSketchPoint, right.endSketchPoint, side_v)
    fix_point(s, bottom.startSketchPoint, a)


def rect_xy(s: adsk.fusion.Sketch, x0: str, y0: str, x1: str, y1: str) -> None:
    """Fully constrained XY rectangle (x0..x1, y0..y1, z=0) added to sketch s; several calls = several profiles."""
    rect_two_corners(s, (x0, y0, "0 mm"), (x1, y1, "0 mm"), f"({x1}) - ({x0})", f"({y1}) - ({y0})")


def rect_xy_sketch(comp: adsk.fusion.Component, label: str, x0: str, y0: str, x1: str, y1: str) -> adsk.fusion.Sketch:
    """XY sketch with one fully constrained rectangle; label goes on the sketch (timeline item)."""
    s = new_sketch(comp, "xy", label=label)
    rect_xy(s, x0, y0, x1, y1)
    return s


def offset_curves(
    s: adsk.fusion.Sketch,
    curves: list[adsk.fusion.SketchCurve],
    distance_expr: str | Ex,
) -> adsk.fusion.OffsetConstraint:
    """Offset connected curves by distance_expr (bound to the offset parameter; + right / - left of curve flow)."""
    gc = s.geometricConstraints
    offset = adsk.core.ValueInput.createByString(as_str(distance_expr))
    inp = gc.createOffsetInput(curves, offset)
    c = gc.addOffset2(inp)
    if c is None:
        raise RuntimeError("offset_curves: addOffset2 returned None (curves not a connected chain?)")
    return c


def center_on_origin(s: adsk.fusion.Sketch, rect_lines: list[adsk.fusion.SketchLine]) -> adsk.fusion.MidPointConstraint:
    """Centre a rect() rectangle on the sketch origin: construction diagonal bottom-start to top-start, origin at its midpoint.

    rect_lines = [bottom, right, top, left] as returned by rect().
    """
    bottom, _, top, _ = rect_lines
    diag = s.sketchCurves.sketchLines.addByTwoPoints(bottom.startSketchPoint, top.startSketchPoint)
    diag.isConstruction = True
    return s.geometricConstraints.addMidPoint(s.originPoint, diag)


@contextlib.contextmanager
def deferred(s: adsk.fusion.Sketch) -> Iterator[adsk.fusion.Sketch]:
    """Batch sketch edits: isComputeDeferred True inside, always reset to False on exit (also on exception).
    Sketch must be new or not yet read by profiles(): reset happens before the caller reads the computed sketch."""
    s.isComputeDeferred = True
    try:
        yield s
    finally:
        s.isComputeDeferred = False

"""Warp relief grooves on the print bed. Split from fusion_kit.features."""

import math
from itertools import pairwise
from typing import Literal

import adsk.core
import adsk.fusion

from fusion_kit.dfam import DFAM_GROUP, BedDir, bed_axis, set_bed
from fusion_kit.dfam_types import WARP_GROOVE_ATTR
from fusion_kit.enums import (
    IDENTICAL_PATTERN,
)
from fusion_kit.features_axes import (
    axis_plane,
)
from fusion_kit.features_common import (
    _mm,
)
from fusion_kit.features_extrude import (
    extrude,
    profile,
    profiles,
)
from fusion_kit.features_pattern import (
    pattern_rectangular,
)
from fusion_kit.sketch import fix_point, new_sketch, rect
from fusion_kit.timeline import check_label
from fusion_kit.user_params import eval_fresh

_GROOVE_MIN_SPAN_MM = 35.0  # spans <= this get no relief grooves; also max segment (edge to groove, groove to groove)


GROOVE_EDGE_MIN_MM = 3.0  # outermost groove centre to bed edge (clears a 1 mm bed chamfer and the groove width)


_GROOVE_OVERRUN_MM = 1.0  # seed rect runs this far past the body so the cut goes through


def _spaced_centres(n: int, spacing_mm: float) -> list[float]:
    """n centres at spacing_mm, symmetric about 0."""
    return [(i - (n - 1) / 2) * spacing_mm for i in range(n)]


def _grooves_ok(length_mm: float, centres: list[float]) -> bool:
    """Outer segment (bed edge to outermost centre) >= GROOVE_EDGE_MIN_MM; every segment < _GROOVE_MIN_SPAN_MM."""
    edges = [-length_mm / 2, *centres, length_mm / 2]
    segs = [b - a for a, b in pairwise(edges)]
    return (
        segs[0] >= GROOVE_EDGE_MIN_MM and segs[-1] >= GROOVE_EDGE_MIN_MM and all(s < _GROOVE_MIN_SPAN_MM for s in segs)
    )


def groove_positions(length_mm: float, pitch_mm: float = 30.0) -> list[float]:
    """Groove centres along one span, symmetric about 0.

    span <= 35 mm -> []. Above 35 mm: first try pitch spacing with n = max(2, floor(length / pitch)) centres
    (dfam _warp_family needs >= 2 parallel lines, so never 1). Kept only if the outer centre is >= GROOVE_EDGE_MIN_MM
    (3 mm) from its bed edge and every segment (edge to groove, groove to groove) is < 35 mm. Otherwise (pitch pair
    too wide for the span, or pitch > 35 mm) use n = max(2, floor(length / 35)) centres at equal segments
    length / (n + 1): every segment < 35 mm and outer >= 3 mm for any span > 35 mm. Caller offsets by the body
    centre; spacing = centres[1] - centres[0].

    span <= 35 mm -> []; else >= 2 centres symmetric about 0 (dfam needs >= 2 lines, never 1): pitch
    spacing n = max(2, floor(L/pitch)) if outer centre >= GROOVE_EDGE_MIN_MM (3 mm) from bed edge
    and every segment < 35 mm; else n = max(2, floor(L/35)) centres at equal segments L/(n+1). Used
    by features_warp._literal_grooves and _relief_family (spacing = centres[1]-centres[0])
    """
    if length_mm <= _GROOVE_MIN_SPAN_MM:
        return []
    centres = _spaced_centres(max(2, math.floor(length_mm / pitch_mm)), pitch_mm)
    if _grooves_ok(length_mm, centres):
        return centres
    n = max(2, math.floor(length_mm / _GROOVE_MIN_SPAN_MM))
    return _spaced_centres(n, length_mm / (n + 1))


def _seed_rect(
    s: adsk.fusion.Sketch,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    bed_dir: BedDir = "-z",
    bed_mm: float = 0.0,
    exprs: dict[int, tuple[str, str]] | None = None,
) -> None:
    """Seed groove rectangle on the bed plane (mm). (x0, x1) / (y0, y1): MODEL coords along the bed's two in-plane
    axes, lower axis index first ('-z' -> x, y; '+x' -> y, z). bed_mm = bed plane coordinate on its normal axis.

    Corners are MODEL points: rect() and fix_point() take model coords and convert through the sketch (live probe:
    yZ sketch x = -Z, xZ sketch y = -Z, xY as model). Both corners dimensioned from the sketch origin with
    unsigned dims: rect_xy() dims corner-to-corner and the solver's sign pick flipped the far edge (live X seed,
    centre 14.2 instead of 15). rect() corners stay jittered; the solver snaps them.
    exprs: optional {model axis index: (lo corner expr, hi corner expr)} replacing that axis' dim literal, e.g.
    {0: ("20 mm - (warp_groove_width) / 2", "20 mm + (warp_groove_width) / 2")} makes the seed width a parameter.
    """
    a, _ = bed_axis(bed_dir)
    pa, pb = [k for k in range(3) if k != a]
    c0: list[float] = [0.0, 0.0, 0.0]
    c1: list[float] = [0.0, 0.0, 0.0]
    c0[a] = c1[a] = bed_mm
    c0[pa], c0[pb] = x0, y0
    c1[pa], c1[pb] = x1, y1
    ex = exprs or {}

    def _pt(c: list[float], corner: int) -> tuple[str, str, str]:
        x = ex[0][corner] if 0 in ex else _mm(c[0])
        y = ex[1][corner] if 1 in ex else _mm(c[1])
        z = ex[2][corner] if 2 in ex else _mm(c[2])
        return (x, y, z)

    bottom, _, top, _ = rect(s, (c0[0], c0[1], c0[2]), (c1[0], c1[1], c1[2]))
    fix_point(s, bottom.startSketchPoint, _pt(c0, 0))
    fix_point(s, top.startSketchPoint, _pt(c1, 1))


# Model axes by letter, typed for axis_plane / axis_*_through.
_AXES: tuple[Literal["x", "y", "z"], ...] = ("x", "y", "z")


# WARP_GROOVE_ATTR (group DFAM_GROUP, defined in dfam_types) tags the sketch and cut(s) of each warp groove family.
# param_sweep reads it to suppress the grooves on probes of params that no groove expression refers to.


def _tag_warp_groove(ent: adsk.fusion.Sketch | adsk.fusion.Feature) -> None:
    attrs = getattr(ent, "attributes", None)  # offline fakes carry no attributes; live Sketch / Feature always do
    if attrs is not None:
        attrs.add(DFAM_GROUP, WARP_GROOVE_ATTR, "1")


def _depth_text(depth_sign: int, depth_mm: float, depth_expr: str | None) -> str:
    """Cut depth for extrude: literal mm (signed by depth_sign), or depth_expr (signed the same way)."""
    if depth_expr is None:
        return _mm(depth_sign * depth_mm)
    return f"({depth_expr})" if depth_sign > 0 else f"-({depth_expr})"


def _literal_grooves(
    s: adsk.fusion.Sketch,
    body: adsk.fusion.BRepBody,
    along: Literal["x", "y", "z"],
    *,
    width_mm: float,
    pitch_mm: float,
    bed_dir: BedDir,
    bed_mm: float,
    width_expr: str | None,
    run_expr: tuple[str, str] | None,
) -> None:
    """Literal family: one groove rect per centre (groove_positions) added to sketch s, spaced along `along`, each
    through the full span of the other in-plane axis. Positions are literal mm; width_expr / run_expr (User Parameter
    expressions) stay live. No rect when the span is <= 35 mm (groove_positions -> []). The caller cuts this family's
    sketch once (warp_relief_grooves)."""
    a, _ = bed_axis(bed_dir)
    i = "xyz".index(along)  # spacing axis
    if i == a:
        raise ValueError(f"warp groove family {along!r} is normal to bed {bed_dir!r}")
    j = 3 - a - i  # groove run axis: the other in-plane axis
    bb = body.boundingBox
    lo_i, hi_i = getattr(bb.minPoint, _AXES[i]) * 10, getattr(bb.maxPoint, _AXES[i]) * 10  # cm -> mm
    lo_j, hi_j = getattr(bb.minPoint, _AXES[j]) * 10, getattr(bb.maxPoint, _AXES[j]) * 10
    mid = (lo_i + hi_i) / 2
    v0, v1 = lo_j - _GROOVE_OVERRUN_MM, hi_j + _GROOVE_OVERRUN_MM
    pa, pb = [k for k in range(3) if k != a]  # lower in-plane axis first, as _seed_rect expects
    run: dict[int, tuple[str, str]] = {}
    if run_expr is not None:
        lo_e, hi_e = run_expr
        run[j] = (f"({lo_e}) - {_mm(_GROOVE_OVERRUN_MM)}", f"({hi_e}) + {_mm(_GROOVE_OVERRUN_MM)}")
    for c in groove_positions(hi_i - lo_i, pitch_mm):
        first = mid + c
        iv = {i: (first - width_mm / 2, first + width_mm / 2), j: (v0, v1)}  # model intervals per in-plane axis
        (x0, x1), (y0, y1) = iv[pa], iv[pb]
        exprs = dict(run)
        if width_expr is not None:
            exprs[i] = (f"{_mm(first)} - ({width_expr}) / 2", f"{_mm(first)} + ({width_expr}) / 2")
        _seed_rect(s, x0, y0, x1, y1, bed_dir=bed_dir, bed_mm=bed_mm, exprs=exprs or None)


def _relief_family(
    comp: adsk.fusion.Component,
    bed: adsk.fusion.ConstructionPlane,
    body: adsk.fusion.BRepBody,
    along: Literal["x", "y", "z"],
    *,
    label: str,
    width_mm: float,
    depth_mm: float,
    pitch_mm: float,
    bed_dir: BedDir = "-z",
    depth_sign: int = 1,
    bed_mm: float = 0.0,
    width_expr: str | None = None,
    depth_expr: str | None = None,
    length_expr: str | None = None,
    margin_mm: float = 0.0,
    margin_expr: str | None = None,
    target_pitch_expr: str | None = None,
    run_expr: tuple[str, str] | None = None,
    along_lo_expr: str | None = None,
) -> list[adsk.fusion.Feature]:
    """LIVE groove family along `along` (an in-plane axis of bed_dir): seed cut + rectangular pattern, in its own
    sketch `<label>_<along>_sketch`. Count, spacing and seed centre are Fusion expressions of length_expr (body extent
    along `along`), margin_expr (bed edge -> first and last centre; margin_mm = its value) and target_pitch_expr
    (pitch_mm = its value). run_expr = (lo, hi) expressions of the run-axis (other in-plane axis) extent, required.
    along_lo_expr: expression of the low edge of `along` (None = literal low edge). Always builds (no 35 mm gate).
    Literal families never come here: warp_relief_grooves cuts them via _literal_grooves, one sketch per family."""
    if length_expr is None or margin_expr is None or target_pitch_expr is None:
        raise ValueError(f"live groove family {along!r}: length_expr, margin_expr, target_pitch_expr required")
    a, _ = bed_axis(bed_dir)
    i = "xyz".index(along)  # spacing axis
    if i == a:
        raise ValueError(f"warp groove family {along!r} is normal to bed {bed_dir!r}")
    j = 3 - a - i  # groove run axis: the other in-plane axis
    bb = body.boundingBox
    lo_i, hi_i = getattr(bb.minPoint, _AXES[i]) * 10, getattr(bb.maxPoint, _AXES[i]) * 10  # cm -> mm
    lo_j, hi_j = getattr(bb.minPoint, _AXES[j]) * 10, getattr(bb.maxPoint, _AXES[j]) * 10
    if margin_mm < GROOVE_EDGE_MIN_MM:
        raise ValueError(f"groove margin {margin_mm} mm < GROOVE_EDGE_MIN_MM {GROOVE_EDGE_MIN_MM}")
    if run_expr is None:
        raise ValueError(f"live groove family {along!r}: run_expr (run-axis extent) required, no literal fallback")
    if hi_i - lo_i - 2 * margin_mm <= 0:
        raise ValueError(f"live groove family {along!r}: span {hi_i - lo_i} mm < 2 x margin {margin_mm} mm")
    first = lo_i + margin_mm  # seed = first centre, margin_mm from the bed edge (expression below)
    # floor(..) + 2, not max(2; floor(..) + 1): Fusion rejects max()/min() in User Parameter expressions (gotchas).
    # +2 gives count >= 2 for any free >= 0 and spacing <= pitch (dfam: every segment < 35 mm).
    qty_text = f"floor(({length_expr} - 2 * ({margin_expr})) / ({target_pitch_expr})) + 2"
    step_text = f"({length_expr} - 2 * ({margin_expr})) / (({qty_text}) - 1)"
    lo_text = _mm(lo_i) if along_lo_expr is None else f"({along_lo_expr})"
    centre_expr = f"({lo_text} + ({margin_expr}))"
    lo_e, hi_e = run_expr
    w_half = f"({width_expr}) / 2" if width_expr is not None else _mm(width_mm / 2)
    seed_exprs: dict[int, tuple[str, str]] = {
        i: (f"{centre_expr} - {w_half}", f"{centre_expr} + {w_half}"),
        # run-axis corners = body extent -/+ overrun, as expressions (no literal extent)
        j: (f"({lo_e}) - {_mm(_GROOVE_OVERRUN_MM)}", f"({hi_e}) + {_mm(_GROOVE_OVERRUN_MM)}"),
    }
    u0, u1 = first - width_mm / 2, first + width_mm / 2
    v0, v1 = lo_j - _GROOVE_OVERRUN_MM, hi_j + _GROOVE_OVERRUN_MM
    iv = {i: (u0, u1), j: (v0, v1)}  # model intervals per in-plane axis (literal positions; exprs overwrite dims)
    pa, pb = [k for k in range(3) if k != a]  # lower in-plane axis first, as _seed_rect expects
    (x0, x1), (y0, y1) = iv[pa], iv[pb]
    s = new_sketch(comp, bed, label=f"{label}_{along}_sketch")
    _tag_warp_groove(s)
    _seed_rect(s, x0, y0, x1, y1, bed_dir=bed_dir, bed_mm=bed_mm, exprs=seed_exprs)
    depth = _depth_text(depth_sign, depth_mm, depth_expr)
    seed = extrude(comp, profile(s), depth, op="cut", bodies=[body], label=f"{label}_{along}_seed")
    _tag_warp_groove(seed)
    # Model origin axis of `along` (points +): no construction axis/planes per family, copies step +spacing.
    # Seed stays at lowest centre.
    if along == "x":
        axis = comp.xConstructionAxis
    elif along == "y":
        axis = comp.yConstructionAxis
    else:
        axis = comp.zConstructionAxis
    out: list[adsk.fusion.Feature] = [seed]
    # IDENTICAL, not default OPTIMIZED: with the other family present, OPTIMIZED truncated the body (live: vol 43.7 -> 20.5).
    pat = pattern_rectangular(
        comp,
        [seed],
        axis,
        qty_text,
        step_text,
        compute=IDENTICAL_PATTERN,
        always=True,  # skips the Python-side qty < 2 check; qty_text is >= 2 by construction
        label=f"{label}_{along}_pattern",
    )
    if pat is not None:
        _tag_warp_groove(pat)
        out.append(pat)
    return out


def _resolve(comp: adsk.fusion.Component, expr: str | None, mm_value: float | None, default: float) -> float:
    """Float twin of a *_expr: eval_fresh in mm (never a cached .value) when expr given, else mm_value, else default."""
    if expr is not None:
        return eval_fresh(comp.parentDesign, expr, "mm") * 10  # eval_fresh -> internal cm
    return default if mm_value is None else mm_value


def warp_relief_grooves(
    comp: adsk.fusion.Component,
    body: adsk.fusion.BRepBody,
    *,
    label: str,
    width_mm: float | None = None,
    depth_mm: float | None = None,
    pitch_mm: float | None = None,
    bed: BedDir = "-z",
    width_expr: str | None = None,
    depth_expr: str | None = None,
    length_exprs: dict[str, str] | None = None,
    margin_mm: float | None = None,
    margin_expr: str | None = None,
    target_pitch_expr: str | None = None,
    run_exprs: dict[str, tuple[str, str]] | None = None,
) -> list[adsk.fusion.Feature]:
    """Anti-warp relief grooves cut into body from its print bed face (bed = BedDir; '-z' default = min Z of bbox).

    Two families = the two axes in the bed plane, full span. Literal family (no length_exprs entry): its groove rects go
    into sketch `<label>_{a}_sketch`, cut once by feature `<label>_{a}` over profiles() (one sketch + cut per literal
    family). Two literal families share NO sketch: their crossing rects enclose cells and profiles() returns those too
    (live: 33 profiles, 3 x 852.64 mm2 cells, 0.6 mm recess of the bed face). Live family (length_exprs entry): seed cut
    `<label>_{a}_seed` + pattern_rectangular `<label>_{a}_pattern` in sketch `<label>_{a}_sketch` (a = the family's
    spacing axis). Stores bed on body (set_bed).
    Labels: <label>_bed, <label>_{a}_sketch, <label>_{a} (literal cut), <label>_{a}_{seed,pattern} (live). Returned list:
    live features first, then the literal cuts in axis order.

    width_expr / depth_expr: optional timeline expression strings, normally User Parameter names
    (e.g. "warp_groove_width"). When given, they replace the literal numbers in the timeline: sketch width dim
    (centre -/+ (width_expr)/2), cut depth. None = literal width_mm / depth_mm.
    width_mm/depth_mm/pitch_mm/margin_mm optional: with the matching expr given, the float is evaluated from user params
    (eval_fresh, never cached .value); the *_mm float is used only when no expr is given.
    Live mode (length_exprs): {family axis letter: Fusion expression of that family's body extent}, e.g.
    {"z": "bracket_length"}. Families in the map get count, spacing and seed centre as expressions:
    count = floor((L - 2 * margin_expr) / target_pitch_expr) + 2, spacing = (L - 2 * margin_expr) / (count - 1),
    seed centre = bed edge + margin_expr. Needs margin_expr, target_pitch_expr; margin_mm / pitch_mm = their values
    (>= GROOVE_EDGE_MIN_MM margin). Live families always build (no 35 mm gate). run_exprs = {run axis letter:
    (lo_expr, hi_expr)}: Fusion expressions of the body extent along the run axis (the other in-plane axis); seed
    run ends = (lo_expr) - 1.000000 mm / (hi_expr) + 1.000000 mm. Required for every live family's run axis, extra
    keys raise ValueError, no literal fallback. Literal families (not in the map) keep literal positions (35 mm gate,
    build-time); their run ends take run_exprs too when that axis is a live family's run axis.
    If run_exprs also names a live family's own spacing axis (other family live), that family's seed centre =
    (lo_expr) + margin_expr; else literal low edge.
    Edits to the expression params update grooves and seed run ends with no re-run; bed choice and literal family
    presence stay build-time. No bed plane / sketch is created when no family builds.
    Literal mode (length_exprs None): pitch_mm stays literal by design: positions and count are computed at
    build -> re-run script after changing pitch or part size. Equal-segment fallback when pitch_mm does not fit the
    span (groove_positions). bed = print bed face (BedDir); two families = in-plane axes; stores set_bed.
    Seed rects in model coords (_seed_rect); cut depth sign from plane normal; live smoke
    tests/smoke/fusion_smoke_bed_orientation.py
    """
    check_label(label)
    a, sgn = bed_axis(bed)
    fams: list[Literal["x", "y", "z"]] = [ax for k, ax in enumerate(_AXES) if k != a]
    lens = length_exprs or {}
    runs = run_exprs or {}
    if length_exprs is not None:
        bad = [k for k in length_exprs if k not in fams]
        if bad:
            raise ValueError(f"length_exprs keys {bad}: not in-plane axes of bed {bed!r} {fams}")
        if margin_expr is None or target_pitch_expr is None:
            raise ValueError("length_exprs needs margin_expr and target_pitch_expr")
    live: list[Literal["x", "y", "z"]] = [ax for ax in fams if lens.get(ax) is not None]
    # a family's seed runs along the OTHER in-plane axis: that is the axis run_exprs must name for a live family
    run_of = {ax: _AXES[3 - a - "xyz".index(ax)] for ax in fams}
    need = sorted({run_of[ax] for ax in live})
    missing = [ax for ax in need if ax not in runs]
    if missing:
        raise ValueError(f"live groove families {live}: run_exprs missing run axis {missing} (no literal fallback)")
    extra = sorted(k for k in runs if k not in need)
    if extra:
        raise ValueError(f"run_exprs keys {extra}: run axis of no live groove family {live}")
    # Float twins: expr wins when given (evaluated from user params), *_mm only when no expr, default otherwise.
    width_mm = _resolve(comp, width_expr, width_mm, 0.8)
    depth_mm = _resolve(comp, depth_expr, depth_mm, 0.6)
    pitch_mm = _resolve(comp, target_pitch_expr, pitch_mm, 30.0)
    margin_mm = _resolve(comp, margin_expr, margin_mm, 0.0)
    bb = body.boundingBox
    # no family builds -> no bed plane / sketch (else an orphan plane). Literal test = same span as _literal_grooves.
    built: list[Literal["x", "y", "z"]] = [
        ax
        for ax in fams
        if ax in live or bool(groove_positions(getattr(bb.maxPoint, ax) * 10 - getattr(bb.minPoint, ax) * 10, pitch_mm))
    ]
    set_bed(body, bed)
    if not built:
        return []
    corner = bb.minPoint if sgn < 0 else bb.maxPoint
    bed_mm = getattr(corner, _AXES[a]) * 10  # cm -> mm
    plane = axis_plane(comp, _AXES[a], _mm(bed_mm), bed_mm, label=f"{label}_bed")
    # cut runs from the bed INTO the body = -sign * e_a; the plane normal sign comes from the base plane
    depth_sign = 1 if getattr(plane.geometry.normal, _AXES[a]) * -sgn > 0 else -1
    out: list[adsk.fusion.Feature] = []
    for ax in live:
        out.extend(
            _relief_family(
                comp,
                plane,
                body,
                ax,
                label=label,
                width_mm=width_mm,
                depth_mm=depth_mm,
                pitch_mm=pitch_mm,
                bed_dir=bed,
                depth_sign=depth_sign,
                bed_mm=bed_mm,
                width_expr=width_expr,
                depth_expr=depth_expr,
                length_expr=lens[ax],
                margin_mm=margin_mm,
                margin_expr=margin_expr,
                target_pitch_expr=target_pitch_expr,
                run_expr=runs.get(run_of[ax]),
                along_lo_expr=runs[ax][0] if ax in runs else None,
            )
        )
    lit: list[Literal["x", "y", "z"]] = [ax for ax in built if ax not in live]
    for ax in lit:
        # own sketch + cut per literal family: profiles() of crossing rects of two families also returns the enclosed cells
        s = new_sketch(comp, plane, label=f"{label}_{ax}_sketch")
        _tag_warp_groove(s)
        _literal_grooves(
            s,
            body,
            ax,
            width_mm=width_mm,
            pitch_mm=pitch_mm,
            bed_dir=bed,
            bed_mm=bed_mm,
            width_expr=width_expr,
            run_expr=runs.get(run_of[ax]),
        )
        cut = extrude(
            comp,
            profiles(s),
            _depth_text(depth_sign, depth_mm, depth_expr),
            op="cut",
            bodies=[body],
            label=f"{label}_{ax}",
        )
        _tag_warp_groove(cut)
        out.append(cut)
    return out

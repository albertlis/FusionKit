"""DfAM warp-relief groove geometry: groove rects, families, print-frame warp zones."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from fusion_kit.dfam_bed import bed_rotation_axes, rotate_point
from fusion_kit.dfam_geom import _box_mm
from fusion_kit.dfam_limits import (
    BED_CHAMFER_ANGLE_DEG,
    BED_ON_TOL_MM,
    WARP_BRIDGE_GAP_MAX_MM,
    WARP_EDGE_BAND_MM,
    WARP_EDGE_PAD_MM,
    WARP_END_INSET_MAX_MM,
    WARP_FULL_COVER_TOL_MM,
    WARP_GAP_TOL,
    WARP_GROOVE_DEPTH_MM,
    WARP_GROOVE_WIDTH_MM,
    WARP_SLOT_ASPECT,
    WARP_STRIP_FULL_COVER_MIN_MM,
    WARP_STRIP_OVERLAP_FRAC,
    WARP_STRIP_OVERLAP_MIN_MM,
)
from fusion_kit.dfam_types import BED_DIRS, GrooveRect, Vec

if TYPE_CHECKING:  # annotations only; runtime adsk import stays lazy inside functions
    import adsk.core
    import adsk.fusion


def _is_groove_width(width_mm: float) -> bool:
    """Gap / slot width within WARP_GROOVE_WIDTH_MM +- WARP_GAP_TOL (0.6-1.0 mm)."""
    return abs(width_mm - WARP_GROOVE_WIDTH_MM) <= WARP_GROOVE_WIDTH_MM * WARP_GAP_TOL


def _rect_width(r: GrooveRect) -> float:
    return min(r[2] - r[0], r[3] - r[1])


def _merge_collinear(a: tuple[int, GrooveRect], b: tuple[int, GrooveRect]) -> GrooveRect | None:
    """Union of two rects on one groove line (same axis, centre line within WARP_EDGE_PAD_MM, along-groove gap
    <= WARP_BRIDGE_GAP_MAX_MM). None if not mergeable."""
    ax, ra = a
    bx, rb = b
    if ax != bx:
        return None
    other = 1 - ax
    if abs((ra[other] + ra[other + 2]) - (rb[other] + rb[other + 2])) / 2 > WARP_EDGE_PAD_MM:
        return None
    if max(rb[ax] - ra[ax + 2], ra[ax] - rb[ax + 2]) > WARP_BRIDGE_GAP_MAX_MM:
        return None
    new = [0.0, 0.0, 0.0, 0.0]
    new[ax], new[ax + 2] = min(ra[ax], rb[ax]), max(ra[ax + 2], rb[ax + 2])
    new[other], new[other + 2] = min(ra[other], rb[other]), max(ra[other + 2], rb[other + 2])
    return (new[0], new[1], new[2], new[3])


def _bridge_collinear(grooves: list[tuple[int, GrooveRect]]) -> list[tuple[int, GrooveRect]]:
    """Merge collinear pieces until none merge. A bed gap across a perpendicular groove stops each piece at the
    crossing: the 0.8 mm gap between two pieces is that groove, so the merged rect covers the crossing squares.
    Body-edge ends are not touched here (_groove_rects end inset)."""
    # Same result as restarting the lex-first pair scan after every merge (O(M^3)), but O(M^2): rows before the
    # cursor i are already known non-mergeable, so after a merge only pairs touching the changed piece are rechecked.
    out = list(grooves)
    i = 0
    while i < len(out):
        hit: tuple[int, GrooveRect] | None = None
        for k in range(i):  # pairs (k, i), k < i: lex-before row i
            m = _merge_collinear(out[k], out[i])
            if m is not None:
                hit = (k, m)
                break
        if hit is not None:
            k, m = hit
            out[k] = (out[k][0], m)
            del out[i]
            i = k
            continue
        for j in range(i + 1, len(out)):  # row i
            m = _merge_collinear(out[i], out[j])
            if m is not None:
                out[i] = (out[i][0], m)
                del out[j]
                break
        else:
            i += 1
    return out


def _rot_box(lo: Vec, hi: Vec, axes: tuple[Vec, Vec, Vec]) -> tuple[Vec, Vec]:
    """Axis-aligned box (mm) in a print frame: rotate_point of the 8 corners, min / max per axis (exact for 90 deg axes)."""
    corners = [rotate_point((x, y, z), axes) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    xs, ys, zs = zip(*corners)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _frame_box(box: adsk.core.BoundingBox3D, axes: tuple[Vec, Vec, Vec] | None) -> tuple[Vec, Vec]:
    """Fusion box (mm) in the given print frame; axes None = body frame."""
    lo, hi = _box_mm(box)
    return (lo, hi) if axes is None else _rot_box(lo, hi, axes)


def _gap_pairs(boxes: list[tuple[Vec, Vec]]) -> list[tuple[int, int, int]]:
    """Candidate (i, j, ax), i < j, for the recess-gap test of _groove_rects: exact superset, so the caller's predicate
    decides. Gap test runs along the axis other than ax, so pairs are swept over boxes sorted by low edge on that axis:
    a valid gap g <= WARP_GROOVE_WIDTH_MM * (1 + WARP_GAP_TOL) means the later box starts within g of the earlier box's
    high edge, so the scan of each box stops at its high edge + that bound. O(B log B + candidate pairs);
    worst case (all boxes overlapping along the sweep axis) stays O(B^2)."""
    reach = WARP_GROOVE_WIDTH_MM * (1.0 + WARP_GAP_TOL) + 1e-9  # eps: float slack, the exact test still decides
    out: list[tuple[int, int, int]] = []
    for ax in (0, 1):
        other = 1 - ax
        order = sorted(range(len(boxes)), key=lambda k: boxes[k][0][other])
        for p, i in enumerate(order):
            top = boxes[i][1][other] + reach
            for j in order[p + 1 :]:
                if boxes[j][0][other] > top:
                    break
                out.append((min(i, j), max(i, j), ax))
    return out


def _groove_rects(
    bed: list[adsk.fusion.BRepFace],
    span_mm: tuple[Vec, Vec] | None = None,
    axes: tuple[Vec, Vec, Vec] | None = None,
) -> list[tuple[int, GrooveRect]]:
    """Warp grooves on the bed as (groove direction 0=x / 1=y, footprint rect mm). Measured via _box_mm.

    axes (print frame, see bed_rotation_axes): measure the faces and span in that frame instead of the body frame;
    the bed then sits on -Z there. None = body frame (bed -z / already print frame).
    span_mm (body min, max, mm, in the same frame): recess-gap strip ends within WARP_END_INSET_MAX_MM of the body boundary extend to it.
    Bed faces stop at the bed chamfer / corner inset, but the groove ceiling runs out to the plate edge (live: ceiling
    x=0.773, strip end 1.062). Ends further in stay put: a collinear notch 10 mm past a strip end is not exempt.

    Two signals:
    - recess gap: two bed faces overlapping along one axis (shared interval >= max(WARP_STRIP_OVERLAP_MIN_MM,
      WARP_STRIP_OVERLAP_FRAC x shorter span, or WARP_STRIP_FULL_COVER_MIN_MM when the interval covers all of the shorter face); spans may differ), separated by a gap ~WARP_GROOVE_WIDTH_MM
      (through grooves split the bed face into strips). Rect = the gap, shared interval along the groove.
    - blind groove: inner loop of a bed face, slot-like (long > WARP_SLOT_ASPECT x short). Rect = loop bbox.
    A plain through hole (round inner loop, no gap) and a multi-foot bed (no gap) do not count.
    Collinear pieces split by a perpendicular groove (crossing gap <= WARP_BRIDGE_GAP_MAX_MM) are merged (_bridge_collinear).
    """
    boxes = [_frame_box(f.boundingBox, axes) for f in bed]
    hits: list[tuple[int, int, int, GrooveRect]] = []  # (i, j, ax, rect)
    for i, j, ax in _gap_pairs(boxes):
        (alo, ahi), (blo, bhi) = boxes[i], boxes[j]
        # shared interval along the groove (equal spans = whole strip; unequal = overlap only)
        lo, hi = max(alo[ax], blo[ax]), min(ahi[ax], bhi[ax])
        shorter = min(ahi[ax] - alo[ax], bhi[ax] - blo[ax])
        # full cover: shared interval == whole shorter face (narrow arm tip cut fully across by the groove).
        # Only then the absolute floor drops to WARP_STRIP_FULL_COVER_MIN_MM; partial overlaps keep the 2 mm floor.
        full_cover = abs((hi - lo) - shorter) <= WARP_FULL_COVER_TOL_MM
        min_overlap = WARP_STRIP_FULL_COVER_MIN_MM if full_cover else WARP_STRIP_OVERLAP_MIN_MM
        if hi - lo < max(min_overlap, WARP_STRIP_OVERLAP_FRAC * shorter):
            continue
        other = 1 - ax
        gap = max(blo[other] - ahi[other], alo[other] - bhi[other])
        if _is_groove_width(gap):
            rect = [0.0, 0.0, 0.0, 0.0]  # x0, y0, x1, y1
            rect[ax], rect[ax + 2] = lo, hi
            rect[other], rect[other + 2] = min(ahi[other], bhi[other]), max(alo[other], blo[other])
            if span_mm is not None:
                # strip end extends to the body boundary only within WARP_END_INSET_MAX_MM of it
                if rect[ax] - span_mm[0][ax] <= WARP_END_INSET_MAX_MM:
                    rect[ax] = span_mm[0][ax]
                if span_mm[1][ax] - rect[ax + 2] <= WARP_END_INSET_MAX_MM:
                    rect[ax + 2] = span_mm[1][ax]
            hits.append((i, j, ax, (rect[0], rect[1], rect[2], rect[3])))
    # emit in (i, j, ax) order = the old all-pairs loop order; _bridge_collinear is order-sensitive
    hits.sort(key=lambda h: h[:3])
    out: list[tuple[int, GrooveRect]] = [(ax, rect) for _i, _j, ax, rect in hits]
    for f in bed:
        for lp in f.loops:
            if lp.isOuter:
                continue
            edge_boxes = [_frame_box(e.boundingBox, axes) for e in lp.edges]
            if not edge_boxes:
                continue
            lo = tuple(min(b[0][k] for b in edge_boxes) for k in (0, 1))
            hi = tuple(max(b[1][k] for b in edge_boxes) for k in (0, 1))
            dx, dy = hi[0] - lo[0], hi[1] - lo[1]
            short = min(dx, dy)
            if short > 0 and max(dx, dy) > WARP_SLOT_ASPECT * short:
                out.append((0 if dx > dy else 1, (lo[0], lo[1], hi[0], hi[1])))
    # Groove ceiling (depth WARP_GROOVE_DEPTH_MM) runs past the bed-face bbox where the groove meets a bed chamfer
    # (BED_CHAMFER_ANGLE_DEG face): horizontal run = depth / tan(angle) = 0.579 mm at 0.6 (was 0.290 at 0.3; live side_wall window ends:
    # rect 11.03 -> ceiling 11.324, rect 27.30 -> ceiling 27.01). Applied after _bridge_collinear so merge gaps stay
    # unchanged; body-boundary ends stay on span_mm.
    ext = WARP_GROOVE_DEPTH_MM / math.tan(math.radians(BED_CHAMFER_ANGLE_DEG))
    res: list[tuple[int, GrooveRect]] = []
    for ax, r in _bridge_collinear(out):
        lo_end, hi_end = r[ax] - ext, r[ax + 2] + ext
        if span_mm is not None:
            lo_end, hi_end = max(lo_end, span_mm[0][ax]), min(hi_end, span_mm[1][ax])
        ext_rect = [r[0], r[1], r[2], r[3]]
        ext_rect[ax], ext_rect[ax + 2] = lo_end, hi_end
        res.append((ax, (ext_rect[0], ext_rect[1], ext_rect[2], ext_rect[3])))
    return res


def _warp_family(grooves: list[tuple[int, GrooveRect]]) -> list[tuple[int, GrooveRect]]:
    """Qualifying warp-groove family, per direction: an axis's grooves count only if that axis holds >= 2 distinct
    parallel centre lines (cross-axis centre within WARP_EDGE_PAD_MM = same line). Collinear pieces of one line
    (dashed slot) and lone slots / single gaps drop out."""
    centres: list[list[float]] = [[], []]  # index = groove direction (0 = along x, 1 = along y)
    for ax, r in grooves:
        centres[ax].append((r[1] + r[3]) / 2 if ax == 0 else (r[0] + r[2]) / 2)
    lines = [0, 0]
    for ax in (0, 1):
        last: float | None = None
        for c in sorted(centres[ax]):
            if last is None or c - last > WARP_EDGE_PAD_MM:
                lines[ax] += 1
                last = c
    return [g for g in grooves if lines[g[0]] >= 2]


def _warp_grooves(grooves: list[tuple[int, GrooveRect]]) -> bool:
    """True if the groove list holds >= 2 parallel warp grooves (>= 2 distinct centre lines in one direction).
    = _warp_family non-empty."""
    return bool(_warp_family(grooves))


def in_groove_rect(point_mm: Vec, r: GrooveRect, bed_z_mm: float) -> bool:
    """One groove rect at a point (mm): z <= bed + WARP_EDGE_BAND_MM, rect width within _is_groove_width,
    point inside the footprint grown by WARP_EDGE_PAD_MM. No family check: caller passes qualifying rects."""
    if point_mm[2] - bed_z_mm > WARP_EDGE_BAND_MM or not _is_groove_width(_rect_width(r)):
        return False
    return r[0] - WARP_EDGE_PAD_MM <= point_mm[0] <= r[2] + WARP_EDGE_PAD_MM and (
        r[1] - WARP_EDGE_PAD_MM <= point_mm[1] <= r[3] + WARP_EDGE_PAD_MM
    )


def in_warp_groove(point_mm: Vec, grooves: list[tuple[int, GrooveRect]], bed_z_mm: float) -> bool:
    """Shared groove predicate: point (mm) sits in the bed-level band of a warp groove.

    True when z <= bed + WARP_EDGE_BAND_MM and the point lies inside a groove-width rect (see _is_groove_width),
    grown by WARP_EDGE_PAD_MM. Rim edges on the gap walls and crossing corners pass; outer corners do not.
    Only the qualifying family counts (_warp_family): a lone slot / single gap never exempts.
    """
    return any(in_groove_rect(point_mm, r, bed_z_mm) for _ax, r in _warp_family(grooves))


WarpFrame = tuple[tuple[Vec, Vec, Vec], float, list[tuple[int, GrooveRect]]]  # (print-frame axes, bed z there, family)


def warp_frames(
    lo: Vec,
    hi: Vec,
    faces_of: dict[str, list[adsk.fusion.BRepFace]],
    bed_grooves: list[tuple[int, GrooveRect]] | None = None,
) -> list[WarpFrame]:
    """Qualifying warp-groove family per print bed side, all six sides (grooves cut from a side face = secondary print
    position, e.g. bed -x). faces_of[bed] = planar faces on that side, outward, body frame, mm (-z = bed faces).
    Each side is measured in its own frame (bed_rotation_axes puts that side on -Z), so _groove_rects and
    in_warp_groove run unchanged there. Sides without a qualifying family are dropped. lo / hi = body box, mm.
    bed_grooves = _groove_rects(faces_of["-z"], (lo, hi)) already measured by the caller: the -z frame is the identity,
    so it is reused instead of measured again. None = measure.
    """
    out: list[WarpFrame] = []
    for bed in BED_DIRS:
        faces = faces_of.get(bed)
        if not faces:
            continue
        axes = bed_rotation_axes(bed)
        r_lo, r_hi = _rot_box(lo, hi, axes)
        if bed == "-z" and bed_grooves is not None:
            fam = _warp_family(bed_grooves)
        else:
            fam = _warp_family(_groove_rects(faces, (r_lo, r_hi), axes))
        if fam:
            out.append((axes, r_lo[2], fam))
    return out


def in_warp_frames(point_mm: Vec, frames: list[WarpFrame]) -> bool:
    """Groove predicate over all sides: point (body frame, mm) sits in the bed band of a qualifying family of any side.
    Each side tests the point rotated into that side's print frame (in_warp_groove there). frames come from warp_frames,
    so each fam is already qualifying: in_groove_rect per rect, no per-point _warp_family (same result, _warp_family is idempotent)."""
    for axes, z, fam in frames:
        p = rotate_point(point_mm, axes)
        if any(in_groove_rect(p, r, z) for _ax, r in fam):
            return True
    return False


def warp_family_mm(body: adsk.fusion.BRepBody) -> list[tuple[int, GrooveRect]]:
    """Qualifying warp-groove family for the printability exemption, mm. [] = no pattern.

    body must be the PRINT-frame body (print_frame_body(body)): bed = planar faces at min z, same as facts_from_body.
    """
    from fusion_kit.features import planar_faces

    lo, hi = _box_mm(body.boundingBox)
    return _warp_family(_groove_rects(planar_faces(body, "z", lo[2], normal_sign=-1, tol_mm=BED_ON_TOL_MM), (lo, hi)))

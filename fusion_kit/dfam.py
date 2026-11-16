"""DfAM rule core: pure FDM design-for-additive checks on plain facts.

Pure module: no adsk import, runs offline. Fusion side builds HoleFact / EdgeFact / BodyFact
from the model; rule funcs return list[Finding]. Units: mm. PETG rules, 0.4 mm nozzle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from fusion_kit._vec import _cross, _dot, _norm, _scale, _sub, _unit

if TYPE_CHECKING:  # annotations only; runtime adsk import stays lazy inside functions
    import adsk.core
    import adsk.fusion

TEARDROP_WALL_DEG = 60.0
TEARDROP_TOL = 0.98  # peak may be 2% under the exact teardrop peak
SEAM_NOTCH_MM = 0.3
BED_CHAMFER_MM = 1.0
BED_CHAMFER_TOL_MM = 0.02  # bed_chamfer size tolerance on the larger leg (measurement noise)
BED_CHAMFER_SMALL_MM = 0.5
BED_CHAMFER_SMALL_BELOW_MM = 3.0  # bed edge / wall room below this -> small chamfer allowed
BED_CHAMFER_ANGLE_DEG = (
    46.0  # chamfer face angle from horizontal: features.bed_chamfer default, steepest allowed (|n_z| < 0.707)
)
WARP_SIDE_MM = 35.0
WARP_GROOVE_WIDTH_MM = 0.8
WARP_GROOVE_DEPTH_MM = 0.3
WARP_PITCH_MM = 30.0
VERT_FILLET_MIN_MM = 3.0
JOINT_CHAMFER_MM = 0.3
JOINT_CLEARANCE_MM = 0.2  # exported; no rule in spec uses it yet
MIN_WALL_MM = 1.0
PETG_WALL_LO_MM = 0.9  # vertical wall strictly inside (LO, HI) -> FAIL (2 perimeters band)
PETG_WALL_HI_MM = 1.6
FIRST_LAYER_MIN_FEATURE_MM = 2.0
SLENDER_RATIO = 4.0  # height / min footprint side above this -> WARN
WARP_GAP_TOL = 0.25  # bed gap within this fraction of WARP_GROOVE_WIDTH_MM = one groove
WARP_SLOT_ASPECT = 4.0  # inner loop longer than this x its width = slot-like (blind) groove
WARP_EDGE_BAND_MM = 0.5  # height above bed within which a groove rim / ceiling counts as warp-groove geometry
WARP_EDGE_PAD_MM = 0.05  # footprint tolerance: rim edges sit exactly on the gap walls
WARP_END_INSET_MAX_MM = (
    2.0  # recess-gap strip end within this of the body boundary extends to it (plate edge / bed chamfer zone)
)
WARP_BRIDGE_GAP_MAX_MM = (
    WARP_GROOVE_WIDTH_MM * (1.0 + WARP_GAP_TOL) + 2.0 * WARP_EDGE_PAD_MM
)  # collinear pieces on one groove line with gap <= this = groove crossing (1.0 mm + pads): bridged
SEAM_V_TOL_DEG = 10.0  # V notch = two hole-axis-parallel walls meeting at 90 +- this
SEAM_V_COS_MAX = math.cos(math.radians(90.0 - SEAM_V_TOL_DEG))  # |n1 . n2| at or below this = 90 +- tol


@dataclass(frozen=True)
class Finding:
    rule: str
    level: Literal["FAIL", "WARN"]
    msg: str

    def line(self) -> str:
        return f"DFAM_{self.level} {self.rule}: {self.msg}"


@dataclass(frozen=True)
class HoleFact:
    label: str
    axis_vertical: bool
    radius_mm: float
    peak_mm: float | None  # teardrop peak height in model; None = no teardrop
    has_seam_notch: bool
    on_bed: bool
    bed_step_unsupported: bool
    side_screw: bool
    has_relief: bool


@dataclass(frozen=True)
class EdgeFact:
    label: str
    kind: Literal["vertical", "bed", "mating"]
    treatment: Literal["sharp", "fillet", "chamfer"]
    size_mm: float
    room_mm: float
    in_warp_groove: bool = False  # rim / corner inside a warp-relief groove footprint (see in_warp_groove)


@dataclass(frozen=True)
class BodyFact:
    name: str
    bed_max_side_mm: float
    has_warp_grooves: bool
    height_mm: float
    footprint_min_mm: float
    min_wall_mm: float | None
    vertical_wall_mm: list[float]
    has_cavity: bool


ADVISORY: list[Finding] = [
    Finding("orient_45", "WARN", "check 45 deg overhangs in print orientation (printability.sweep)"),
    Finding("fin_support", "WARN", "thin fins: check support needs on fin sides"),
    Finding("micro_feature", "WARN", "micro-features may not print: check min size"),
    Finding("thread_flat", "WARN", "threads: flat top/bottom, not vertical helix"),
    Finding("crush_ribs", "WARN", "crush ribs: check fit and print direction"),
    Finding("side_slot", "WARN", "side slots: check support and orientation"),
    Finding("texture", "WARN", "texture: check surface after print orientation"),
]


def teardrop_peak_mm(r_mm: float, wall_deg: float = TEARDROP_WALL_DEG) -> float:
    return r_mm / math.sin(math.radians(wall_deg))


def check_holes(holes: list[HoleFact]) -> list[Finding]:
    out: list[Finding] = []
    for h in holes:
        if not h.axis_vertical:
            req = teardrop_peak_mm(h.radius_mm)
            if h.peak_mm is None or h.peak_mm < req * TEARDROP_TOL:
                got = "none" if h.peak_mm is None else f"{h.peak_mm:.2f}"
                out.append(
                    Finding(
                        "teardrop", "FAIL", f"{h.label} horizontal hole needs teardrop peak {req:.2f} mm, got {got}"
                    )
                )
        elif not h.has_seam_notch:
            out.append(Finding("seam_notch", "FAIL", f"{h.label} vertical hole needs seam notch {SEAM_NOTCH_MM} mm"))
        if h.on_bed and 2 * h.radius_mm < FIRST_LAYER_MIN_FEATURE_MM:
            out.append(
                Finding(
                    "first_layer",
                    "FAIL",
                    f"{h.label} on bed, diameter {2 * h.radius_mm:.2f} < {FIRST_LAYER_MIN_FEATURE_MM} mm",
                )
            )
        if h.bed_step_unsupported:
            out.append(Finding("counterbore_down", "FAIL", f"{h.label} bed step unsupported, counterbore points down"))
        if h.side_screw and not h.has_relief:
            out.append(Finding("relief_slot", "WARN", f"{h.label} side screw without relief slot"))
    return out


def check_edges(edges: list[EdgeFact]) -> list[Finding]:
    out: list[Finding] = []
    for e in edges:
        if e.in_warp_groove and e.kind in ("bed", "vertical"):
            continue  # groove rim / crossing corner: 0.8 mm groove cannot take a 1 mm chamfer or 3 mm fillet
        if e.kind == "vertical":
            if e.treatment == "sharp":
                out.append(
                    Finding(
                        "vertical_corner",
                        "FAIL",
                        f"{e.label} sharp vertical corner, needs fillet >= {VERT_FILLET_MIN_MM} mm",
                    )
                )
            elif e.treatment == "fillet" and e.size_mm < VERT_FILLET_MIN_MM:
                out.append(
                    Finding(
                        "vertical_corner",
                        "FAIL",
                        f"{e.label} fillet r{e.size_mm:.2f} < {VERT_FILLET_MIN_MM} does nothing, use chamfer",
                    )
                )
        elif e.kind == "bed":
            if e.treatment == "fillet":
                out.append(Finding("bed_chamfer", "FAIL", f"{e.label} bed edge needs chamfer, not fillet"))
            elif e.treatment == "sharp":
                out.append(Finding("bed_chamfer", "FAIL", f"{e.label} bed edge sharp, needs chamfer"))
            else:
                req = BED_CHAMFER_SMALL_MM if e.room_mm < BED_CHAMFER_SMALL_BELOW_MM else BED_CHAMFER_MM
                bed_min = bed_leg_min_mm(req)
                if e.size_mm < bed_min:
                    out.append(
                        Finding(
                            "bed_chamfer",
                            "FAIL",
                            f"{e.label} bed leg {e.size_mm:.2f} < {bed_min:.2f} (wall {req:.2f}, room {e.room_mm:.2f} mm)",
                        )
                    )
        else:  # mating
            if e.treatment == "sharp":
                out.append(
                    Finding(
                        "joint_chamfer", "FAIL", f"{e.label} mating edge sharp, needs chamfer >= {JOINT_CHAMFER_MM} mm"
                    )
                )
            elif e.treatment == "chamfer" and e.size_mm < JOINT_CHAMFER_MM:
                out.append(
                    Finding(
                        "joint_chamfer", "FAIL", f"{e.label} mating chamfer {e.size_mm:.2f} < {JOINT_CHAMFER_MM} mm"
                    )
                )
    return out


def check_body_facts(body_fact: BodyFact) -> list[Finding]:
    if body_fact.footprint_min_mm <= 0:
        raise ValueError(f"{body_fact.name}: footprint_min_mm must be > 0, got {body_fact.footprint_min_mm}")
    out: list[Finding] = []
    if body_fact.bed_max_side_mm > WARP_SIDE_MM and not body_fact.has_warp_grooves:
        out.append(
            Finding(
                "warp_grooves",
                "FAIL",
                f"{body_fact.name} bed side {body_fact.bed_max_side_mm:.2f} > {WARP_SIDE_MM} mm, needs warp grooves",
            )
        )
    if body_fact.min_wall_mm is not None and body_fact.min_wall_mm < MIN_WALL_MM:
        out.append(
            Finding(
                "min_wall",
                "FAIL",
                f"{body_fact.name} min wall {body_fact.min_wall_mm:.2f} < {MIN_WALL_MM} mm",
            )
        )
    bad = [w for w in body_fact.vertical_wall_mm if PETG_WALL_LO_MM < w < PETG_WALL_HI_MM]
    if bad:
        walls = ", ".join(f"{w:.2f}" for w in bad)
        out.append(
            Finding(
                "petg_wall",
                "FAIL",
                f"{body_fact.name} vertical walls {walls} mm in PETG band ({PETG_WALL_LO_MM}, {PETG_WALL_HI_MM}) exclusive",
            )
        )
    ratio = body_fact.height_mm / body_fact.footprint_min_mm
    if ratio > SLENDER_RATIO:
        out.append(Finding("slender_fin", "WARN", f"{body_fact.name} height/footprint {ratio:.2f} > {SLENDER_RATIO}"))
    if body_fact.has_cavity:
        out.append(Finding("cavity", "WARN", f"{body_fact.name} cavity present, verify escape hole / support"))
    return out


def check_all(
    body: BodyFact,
    holes: list[HoleFact],
    edges: list[EdgeFact],
    skip: dict[str, str],
) -> list[Finding]:
    found = check_body_facts(body) + check_holes(holes) + check_edges(edges)
    kept = [f for f in found if f.rule not in skip]
    for rule, reason in skip.items():
        if any(f.rule == rule for f in found):
            kept.append(Finding("dfam_skip", "WARN", f"{rule} skipped: {reason}"))
    return kept


def summarize_findings(findings: list[Finding]) -> str:
    n_fail = sum(1 for f in findings if f.level == "FAIL")
    n_warn = sum(1 for f in findings if f.level == "WARN")
    return f"dfam={n_fail}F/{n_warn}W"


def parse_skip(value: str) -> dict[str, str]:
    """Parse dfam_skip attr '<rule>:<reason>[;<rule>:<reason>...]'. Empty entries ignored.

    Raises ValueError on missing rule/reason or a rule given twice. Reason may contain ':'.
    """
    out: dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        rule, sep, reason = part.partition(":")
        rule, reason = rule.strip(), reason.strip()
        if not sep or not rule or not reason:
            raise ValueError(f"dfam_skip entry needs <rule>:<reason>, got {part!r}")
        if rule in out:
            raise ValueError(f"dfam_skip rule {rule!r} given twice")
        out[rule] = reason
    return out


# Rule ids check_all can emit as findings (ADVISORY is never fed through check_all, so not skippable).
SKIPPABLE_RULES: frozenset[str] = frozenset(
    {
        "teardrop",
        "seam_notch",
        "first_layer",
        "counterbore_down",
        "relief_slot",
        "vertical_corner",
        "bed_chamfer",
        "joint_chamfer",
        "warp_grooves",
        "min_wall",
        "petg_wall",
        "slender_fin",
        "cavity",
    }
)


def format_skip(rules: dict[str, str]) -> str:
    """Build dfam_skip value '<rule>:<reason>; ...' from {rule: reason}. Pure.

    Raises ValueError: unknown rule; reason empty, padded with spaces, containing ';' or line breaks.
    ':' inside reason is fine (parse_skip splits on the first ':'). Result round-trips via parse_skip.
    """
    entries: list[str] = []
    for rule, reason in rules.items():
        if rule not in SKIPPABLE_RULES:
            raise ValueError(f"dfam skip: unknown rule {rule!r}; known: {sorted(SKIPPABLE_RULES)}")
        if not reason or reason != reason.strip():
            raise ValueError(f"dfam skip {rule!r}: reason must be non-empty, no surrounding spaces, got {reason!r}")
        if ";" in reason:
            raise ValueError(f"dfam skip {rule!r}: reason may not contain ';' (rule separator), got {reason!r}")
        if "\r" in reason or "\n" in reason:
            raise ValueError(f"dfam skip {rule!r}: reason may not contain line breaks, got {reason!r}")
        entries.append(f"{rule}:{reason}")
    value = "; ".join(entries)
    if parse_skip(value) != rules:
        raise ValueError(f"dfam skip does not round-trip: {value!r}")
    return value


# ---------------------------------------------------------------------------
# adsk adapter: live BRepBody -> facts. Fusion cm -> mm here.
# adsk and fusion_kit.features imported inside functions (features imports adsk at module level).
# Heuristics below are NOT measured on a live model.
# ---------------------------------------------------------------------------
EdgeKind = Literal["vertical", "bed", "mating"]
EdgeTreatment = Literal["sharp", "fillet", "chamfer"]
Vec = tuple[float, float, float]

DFAM_GROUP = "dfam"
MATING_ATTR = "mating"
SKIP_ATTR = "dfam_skip"
AXIS_PARALLEL = 0.99  # |cos| above this = parallel
ROOF_NORMAL_TOL = 0.01  # |n . axis| below this = plane contains hole axis (teardrop roof)
PARALLEL_TOL = 1e-6  # |n1 x n2| below this = parallel normals (tangent / coplanar)
BED_ON_TOL_MM = 0.01
RISE_TOL_MM = 0.01
CONE_TILT_TOL = 0.01  # bed-edge cone |n . z| above this = inclined chamfer (vertical wall ~0, horizontal top ~1)
FULL_CYLINDER_DEG = 300.0  # concave cylinder group spanning >= this = hole; smaller arc = fillet


def _vec(v: adsk.core.Point3D | adsk.core.Vector3D) -> Vec:
    return (v.x, v.y, v.z)


def _mm(v: adsk.core.Point3D | adsk.core.Vector3D) -> Vec:
    return (v.x * 10, v.y * 10, v.z * 10)


def _box_mm(box: adsk.core.BoundingBox3D) -> tuple[Vec, Vec]:
    return _mm(box.minPoint), _mm(box.maxPoint)


def chamfer_leg_mm(width_mm: float, n_chamfer: Vec, n_adjacent: Vec) -> float:
    """Chamfer leg on the adjacent face = chamfer width * |n_chamfer . n_adjacent| (unit-ised).

    Right-angle corner: leg on face A = w * cos(angle chamfer<->A) = w * |n_c . n_A|.
    45 deg symmetric chamfer: w = leg * sqrt(2) -> leg = w / sqrt(2).
    """
    return width_mm * abs(_dot(_unit(n_chamfer), _unit(n_adjacent)))


def bed_leg_min_mm(req_mm: float) -> float:
    """Minimum BED leg for a bed_chamfer of wall size req_mm = req / tan(BED_CHAMFER_ANGLE_DEG) - BED_CHAMFER_TOL_MM.

    The steepest allowed chamfer (BED_CHAMFER_ANGLE_DEG from horizontal) with wall leg req has bed leg req * cot(angle).
    A steeper chamfer has a shorter bed leg and fails.
    """
    return req_mm / math.tan(math.radians(BED_CHAMFER_ANGLE_DEG)) - BED_CHAMFER_TOL_MM


def _bed_leg_mm(g: adsk.fusion.BRepFace, width_mm: float, n_g: Vec) -> float:
    """Bed leg of a bed-edge chamfer = leg on its bed-like neighbour (|n_z| > AXIS_PARALLEL). Wall leg ignored.

    The bed leg decides the overhang: a steep two-distance chamfer has a short bed leg. Max over bed-like neighbours.
    """
    legs: list[float] = []
    for e2 in g.edges:
        for h in e2.faces:
            if h.entityToken == g.entityToken:
                continue
            n_h = _normal(h, e2.pointOnEdge)
            if abs(n_h[2]) > AXIS_PARALLEL:
                legs.append(chamfer_leg_mm(width_mm, n_g, n_h))
    return max(legs, default=0.0)


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


def _adjacent(face: adsk.fusion.BRepFace) -> dict[str, adsk.fusion.BRepFace]:
    """Distinct faces sharing an edge with face, keyed by entityToken."""
    out: dict[str, adsk.fusion.BRepFace] = {}
    for e in face.edges:
        for g in e.faces:
            if g.entityToken != face.entityToken:
                out[g.entityToken] = g
    return out


def _has_attr(face: adsk.fusion.BRepFace, name: str) -> bool:
    return face.attributes.itemByName(DFAM_GROUP, name) is not None


def _span_deg(face: adsk.fusion.BRepFace) -> float:
    """Angular span of a cylinder face, deg, from u-range of evaluator.parametricRange() (rad). UNMEASURED live."""
    rng = face.evaluator.parametricRange()
    return math.degrees(rng.maxPoint.x - rng.minPoint.x)


GrooveRect = tuple[float, float, float, float]  # footprint (x0, y0, x1, y1) mm, bed plane


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
    out = list(grooves)
    found = True
    while found:
        found = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                m = _merge_collinear(out[i], out[j])
                if m is not None:
                    out[i] = (out[i][0], m)
                    del out[j]
                    found = True
                    break
            if found:
                break
    return out


def _groove_rects(
    bed: list[adsk.fusion.BRepFace], span_mm: tuple[Vec, Vec] | None = None
) -> list[tuple[int, GrooveRect]]:
    """Warp grooves on the bed as (groove direction 0=x / 1=y, footprint rect mm). Measured via _box_mm.

    span_mm (body min, max, mm): recess-gap strip ends within WARP_END_INSET_MAX_MM of the body boundary extend to it.
    Bed faces stop at the bed chamfer / corner inset, but the groove ceiling runs out to the plate edge (live: ceiling
    x=0.773, strip end 1.062). Ends further in stay put: a collinear notch 10 mm past a strip end is not exempt.

    Two signals:
    - recess gap: two bed faces with equal span along one axis, separated by a gap ~WARP_GROOVE_WIDTH_MM
      (through grooves split the bed face into strips). Rect = the gap, full strip length along the groove.
    - blind groove: inner loop of a bed face, slot-like (long > WARP_SLOT_ASPECT x short). Rect = loop bbox.
    A plain through hole (round inner loop, no gap) and a multi-foot bed (no gap) do not count.
    Collinear pieces split by a perpendicular groove (crossing gap <= WARP_BRIDGE_GAP_MAX_MM) are merged (_bridge_collinear).
    """
    boxes = [_box_mm(f.boundingBox) for f in bed]
    out: list[tuple[int, GrooveRect]] = []
    for i, (alo, ahi) in enumerate(boxes):
        for blo, bhi in boxes[i + 1 :]:
            for ax in (0, 1):
                if abs(alo[ax] - blo[ax]) > BED_ON_TOL_MM or abs(ahi[ax] - bhi[ax]) > BED_ON_TOL_MM:
                    continue
                other = 1 - ax
                gap = max(blo[other] - ahi[other], alo[other] - bhi[other])
                if _is_groove_width(gap):
                    rect = [0.0, 0.0, 0.0, 0.0]  # x0, y0, x1, y1
                    rect[ax], rect[ax + 2] = min(alo[ax], blo[ax]), max(ahi[ax], bhi[ax])
                    rect[other], rect[other + 2] = min(ahi[other], bhi[other]), max(alo[other], blo[other])
                    if span_mm is not None:
                        # strip end extends to the body boundary only within WARP_END_INSET_MAX_MM of it
                        if rect[ax] - span_mm[0][ax] <= WARP_END_INSET_MAX_MM:
                            rect[ax] = span_mm[0][ax]
                        if span_mm[1][ax] - rect[ax + 2] <= WARP_END_INSET_MAX_MM:
                            rect[ax + 2] = span_mm[1][ax]
                    out.append((ax, (rect[0], rect[1], rect[2], rect[3])))
    for f in bed:
        for lp in f.loops:
            if lp.isOuter:
                continue
            edge_boxes = [_box_mm(e.boundingBox) for e in lp.edges]
            if not edge_boxes:
                continue
            lo = tuple(min(b[0][k] for b in edge_boxes) for k in (0, 1))
            hi = tuple(max(b[1][k] for b in edge_boxes) for k in (0, 1))
            dx, dy = hi[0] - lo[0], hi[1] - lo[1]
            short = min(dx, dy)
            if short > 0 and max(dx, dy) > WARP_SLOT_ASPECT * short:
                out.append((0 if dx > dy else 1, (lo[0], lo[1], hi[0], hi[1])))
    return _bridge_collinear(out)


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


def warp_family_mm(body: adsk.fusion.BRepBody) -> list[tuple[int, GrooveRect]]:
    """Qualifying warp-groove family of a body (body frame, mm) for the printability exemption. [] = no pattern.

    Same bed faces and groove detection as facts_from_body (bed z = body min z).
    """
    from fusion_kit.features import planar_faces

    lo, hi = _box_mm(body.boundingBox)
    return _warp_family(_groove_rects(planar_faces(body, "z", lo[2], normal_sign=-1, tol_mm=BED_ON_TOL_MM), (lo, hi)))


def facts_from_body(body: adsk.fusion.BRepBody) -> tuple[BodyFact, list[HoleFact], list[EdgeFact]]:
    """Body -> (BodyFact, holes, edges). Units cm -> mm.

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
    - tangent edge parallel to a convex cylinder axis = fillet, size = cylinder radius.
      Other creases kept only if convex (features.convex_edges). Adjacent planar face with
      non axis-aligned normal = chamfer, size = chamfer leg on the other face of the edge
      (chamfer_leg_mm; width = face area / edge length). Bed edge with inclined cone face
      (|n_z| in (CONE_TILT_TOL, AXIS_PARALLEL)) = chamfer too, width = area / mean curved boundary length
      (_cone_width_mm). Else sharp.
    - min_wall_mm None and vertical_wall_mm [] (not measured: min_wall rule never fires).
    - bed_step_unsupported / side_screw / has_relief not detected -> False.
    """
    from adsk import core

    from fusion_kit.features import convex_edges, planar_faces

    lo, hi = _box_mm(body.boundingBox)
    dims = _sub(hi, lo)
    bed_z = lo[2]
    room_mm = min(dims)

    bed = planar_faces(body, "z", bed_z, normal_sign=-1, tol_mm=BED_ON_TOL_MM)
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

    holes: list[HoleFact] = []
    concave_axis: dict[str, Vec] = {}  # every concave cylinder (holes + fillets): seams skipped below
    concave: list[tuple[adsk.fusion.BRepFace, Vec, Vec, float]] = []  # (face, axis, origin, radius_mm)
    for f in body.faces:
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
        concave_axis[f.entityToken] = ax
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
        adj: dict[str, adsk.fusion.BRepFace] = {}
        for f in faces:
            for g in _adjacent(f).values():
                if core.Plane.cast(g.geometry) is not None:
                    adj[g.entityToken] = g
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

    bed_tokens = {e.entityToken for f in bed for lp in f.loops if lp.isOuter for e in lp.edges}
    rows: list[tuple[adsk.fusion.BRepEdge, EdgeKind, float | None, list[adsk.fusion.BRepFace]]] = []
    for e in body.edges:
        faces = list(e.faces)
        if len(faces) != 2:
            continue
        linear = core.Line3D.cast(e.geometry) is not None
        d = _unit(_sub(_mm(e.endVertex.geometry), _mm(e.startVertex.geometry))) if linear else (0.0, 0.0, 0.0)
        if any(
            g.entityToken in concave_axis and abs(_dot(d, concave_axis[g.entityToken])) > AXIS_PARALLEL for g in faces
        ):
            continue  # hole seam, not a part edge
        kind: EdgeKind
        if any(_has_attr(g, MATING_ATTR) for g in faces):
            kind = "mating"
        elif linear and abs(d[2]) > AXIS_PARALLEL:
            kind = "vertical"
        elif e.entityToken in bed_tokens:
            kind = "bed"
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
    convex = {e.entityToken for e in convex_edges(body, creases)} if creases else set()

    edge_facts: list[EdgeFact] = []
    for e, kind, fillet_r, faces in rows:
        m = _mm(e.pointOnEdge)
        label = f"edge@({m[0]:.1f},{m[1]:.1f},{m[2]:.1f})"
        treatment: EdgeTreatment = "sharp"
        size = 0.0
        if fillet_r is not None:
            treatment, size = "fillet", fillet_r
        else:
            if e.entityToken not in convex:
                continue  # concave crease: not a printed outer corner
            for g in faces:
                if core.Plane.cast(g.geometry) is not None:
                    n_g = _normal(g, e.pointOnEdge)
                    if max(abs(c) for c in n_g) > AXIS_PARALLEL:
                        continue  # axis-aligned face: not a chamfer
                    width = g.area / e.length * 10  # chamfer face width, mm
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
        in_groove = in_warp_groove(m, grooves, bed_z)
        edge_facts.append(
            EdgeFact(
                label=label, kind=kind, treatment=treatment, size_mm=size, room_mm=room_mm, in_warp_groove=in_groove
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
    )
    return body_fact, holes, edge_facts


def skip_from_body(body: adsk.fusion.BRepBody) -> dict[str, str]:
    """dfam_skip attribute ("dfam", "dfam_skip") -> {rule: reason}. Absent -> {}."""
    attr = body.attributes.itemByName(DFAM_GROUP, SKIP_ATTR)
    return {} if attr is None else parse_skip(attr.value)


def set_skip(body: adsk.fusion.BRepBody, rules: dict[str, str]) -> str:
    """Write dfam_skip attribute ("dfam", "dfam_skip") from {rule: reason} via format_skip.

    Attributes.add upserts (same group+name -> value replaced). Validation runs before any write. Returns value.
    """
    value = format_skip(rules)
    body.attributes.add(DFAM_GROUP, SKIP_ATTR, value)
    return value


def dfam_check(body: adsk.fusion.BRepBody, fail_as_warn: bool = False) -> list[Finding]:
    """All DfAM findings for one body. fail_as_warn=True: FAIL downgraded to WARN (rule names kept)."""
    body_fact, holes, edges = facts_from_body(body)
    found = check_all(body_fact, holes, edges, skip_from_body(body))
    if fail_as_warn:
        return [replace(f, level="WARN") if f.level == "FAIL" else f for f in found]
    return found

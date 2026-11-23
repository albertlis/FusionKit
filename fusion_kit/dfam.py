"""DfAM rule core: pure FDM design-for-additive checks on plain facts.

Pure module: no adsk import, runs offline. Fusion side builds HoleFact / EdgeFact / BodyFact
from the model; rule funcs return list[Finding]. Units: mm. PETG rules, 0.4 mm nozzle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal

from fusion_kit._vec import _cross, _dot, _norm, _scale, _sub, _unit

if TYPE_CHECKING:  # annotations only; runtime adsk import stays lazy inside functions
    import adsk.core
    import adsk.fusion

TEARDROP_WALL_DEG = 45.0  # full-point flank angle from vertical: peak = r / sin 45 = r * sqrt(2)
TEARDROP_TOL = 0.98  # peak may be 2% under the required peak
TEARDROP_NOTCH_MAX_D_MM = 8.0  # horizontal hole diameter <= this: flat-topped micro notch; above: full 45 deg point
TEARDROP_NOTCH_MM = 0.4  # notch case: flat top sits this far above the circle top (peak = r + notch)
TEARDROP_NOTCH_MAX_RATIO = 1.5  # notch case: measured peak above this x required = FAIL (full cone on small hole)
SEAM_NOTCH_MM = 0.3
BED_CHAMFER_MM = 1.0
BED_CHAMFER_TOL_MM = 0.02  # bed_chamfer size tolerance on the larger leg (measurement noise)
BED_CHAMFER_SMALL_MM = 0.5
BED_CHAMFER_SMALL_BELOW_MM = 3.0  # bed edge / wall room below this -> small chamfer allowed
# Sharp bed edge shorter than this = WARN, not FAIL. features.bed_chamfer(min_edge_mm=3.0) skips such edges on purpose:
# a chamfer there leaves a ~0-footprint sliver, and elephant foot over < 3 mm is negligible.
BED_SHORT_EDGE_MM = 3.0
BED_CHAMFER_ANGLE_DEG = (
    46.0  # chamfer face angle from horizontal: features.bed_chamfer default, steepest allowed (|n_z| < 0.707)
)
WARP_SIDE_MM = 35.0
WARP_GROOVE_WIDTH_MM = 0.8
WARP_GROOVE_DEPTH_MM = 0.6
WARP_PITCH_MM = 30.0
VERT_FILLET_MIN_MM = 3.0
JOINT_CHAMFER_MM = 0.3
PIN_BASE_CHAMFER_MIN_MM = 0.3  # pin base (concave circular edge, convex pin wall): chamfer or fillet >= this
SLIT_MAX_MM = 0.2  # slot ceiling between two facing walls at most this far apart = slit the slicer closes, not a bridge
GRIP_FIN_SECTION_MIN_MM = (
    0.4  # grip fin tangential width over the lead-in band below this = knife edge (grip_fin_sliver FAIL)
)
JOINT_CLEARANCE_MM = 0.2  # exported; no rule in spec uses it yet
MIN_WALL_MM = 1.0
PETG_WALL_LO_MM = 0.9  # vertical wall strictly inside (LO, HI) -> FAIL (2 perimeters band)
PETG_WALL_HI_MM = 1.6
FIRST_LAYER_MIN_FEATURE_MM = 2.0
SLENDER_RATIO = 4.0  # height / min footprint side above this -> WARN
WARP_GAP_TOL = 0.25  # bed gap within this fraction of WARP_GROOVE_WIDTH_MM = one groove
WARP_SLOT_ASPECT = 4.0  # inner loop longer than this x its width = slot-like (blind) groove
WARP_EDGE_BAND_MM = (
    WARP_GROOVE_DEPTH_MM + 0.05
)  # height above bed within which a groove rim / ceiling counts as warp-groove geometry (ceiling at depth + eps)
WARP_EDGE_PAD_MM = 0.05  # footprint tolerance: rim edges sit exactly on the gap walls
WARP_END_INSET_MAX_MM = (
    2.0  # recess-gap strip end within this of the body boundary extends to it (plate edge / bed chamfer zone)
)
WARP_BRIDGE_GAP_MAX_MM = (
    WARP_GROOVE_WIDTH_MM * (1.0 + WARP_GAP_TOL) + 2.0 * WARP_EDGE_PAD_MM
)  # collinear pieces on one groove line with gap <= this = groove crossing (1.0 mm + pads): bridged
WARP_STRIP_OVERLAP_MIN_MM = 2.0  # two bed faces across a groove gap: shared interval along the groove >= this ...
WARP_STRIP_OVERLAP_FRAC = 0.5  # ... and >= this x the shorter face span (unequal spans: dovetail tongue on one strip)
WARP_STRIP_FULL_COVER_MIN_MM = (
    0.5  # shared interval covers the whole shorter face (WARP_FULL_COVER_TOL_MM): floor drops to this
)
WARP_FULL_COVER_TOL_MM = 0.05  # overlap vs shorter face span tolerance for full cover
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
    kind: Literal["vertical", "bed", "mating", "pin_base"]
    treatment: Literal["sharp", "fillet", "chamfer"]
    size_mm: float
    room_mm: float
    in_warp_groove: bool = False  # rim / corner inside a warp-relief groove footprint (see in_warp_groove)
    on_grip_fin: bool = False  # an adjacent face carries the grip_fin attr (fin side / tip, see GRIP_FIN_ATTR)
    length_mm: float | None = None  # edge length, mm; None = unknown (sharp bed edge then FAILs as before)
    slit_width_mm: float | None = None  # pin base: width of the annulus floor it sits on, mm; None = no ring floor


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
    grip_fin_section_mm: float | None = None  # grip_fit min fin width over lead-in band (body attr); None = no grip fin


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


def teardrop_notch(r_mm: float) -> bool:
    return 2 * r_mm <= TEARDROP_NOTCH_MAX_D_MM


def teardrop_required_peak_mm(r_mm: float) -> float:
    """Required peak above centre, horizontal hole. d <= 8: min(r + 0.4, r * sqrt2); above: r * sqrt2."""
    full = teardrop_peak_mm(r_mm)
    if teardrop_notch(r_mm):
        return min(r_mm + TEARDROP_NOTCH_MM, full)
    return full


def check_holes(holes: list[HoleFact]) -> list[Finding]:
    out: list[Finding] = []
    for h in holes:
        if not h.axis_vertical:
            req = teardrop_required_peak_mm(h.radius_mm)
            notch = teardrop_notch(h.radius_mm)
            low = h.peak_mm is None or h.peak_mm < req * TEARDROP_TOL
            high = notch and h.peak_mm is not None and h.peak_mm > req * TEARDROP_NOTCH_MAX_RATIO
            if low or high:
                got = "none" if h.peak_mm is None else f"{h.peak_mm:.2f}"
                cap = f" (notch, max {req * TEARDROP_NOTCH_MAX_RATIO:.2f})" if notch else ""
                out.append(
                    Finding(
                        "teardrop",
                        "FAIL",
                        f"{h.label} horizontal hole needs teardrop peak {req:.2f} mm{cap}, got {got}",
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
        if e.on_grip_fin and e.kind in ("bed", "vertical", "pin_base"):
            continue  # 0.8 mm flexing fin: no 3 mm fillet / 1 mm bed leg fits; radial tip = lead-in cone; fin root at shaft = pin base
        if e.kind == "pin_base" and e.slit_width_mm is not None and e.slit_width_mm <= SLIT_MAX_MM:
            continue  # base on the floor of a slit <= SLIT_MAX_MM wide: no chamfer fits, slit inner wall is not a pin
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
                if e.length_mm is not None and e.length_mm < BED_SHORT_EDGE_MM:
                    out.append(
                        Finding(
                            "bed_chamfer",
                            "WARN",
                            f"{e.label} short bed edge {e.length_mm:.2f} mm left sharp (< {BED_SHORT_EDGE_MM}), "
                            "chamfer would leave a sliver",
                        )
                    )
                else:
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
        elif e.kind == "pin_base":
            if e.treatment == "sharp":
                out.append(
                    Finding(
                        "pin_base_chamfer",
                        "FAIL",
                        f"{e.label} pin base sharp, needs chamfer or fillet >= {PIN_BASE_CHAMFER_MIN_MM} mm",
                    )
                )
            elif e.size_mm < PIN_BASE_CHAMFER_MIN_MM:
                out.append(
                    Finding(
                        "pin_base_chamfer",
                        "FAIL",
                        f"{e.label} pin base {e.treatment} {e.size_mm:.2f} < {PIN_BASE_CHAMFER_MIN_MM} mm",
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
    if body_fact.grip_fin_section_mm is not None and body_fact.grip_fin_section_mm < GRIP_FIN_SECTION_MIN_MM:
        out.append(
            Finding(
                "grip_fin_sliver",
                "FAIL",
                f"{body_fact.name} grip fin section {body_fact.grip_fin_section_mm:.2f} mm over lead-in band "
                f"< {GRIP_FIN_SECTION_MIN_MM} mm (knife edge at entry)",
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
        "pin_base_chamfer",
        "warp_grooves",
        "min_wall",
        "petg_wall",
        "slender_fin",
        "cavity",
        "grip_fin_sliver",
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
EdgeKind = Literal["vertical", "bed", "mating", "pin_base"]
EdgeTreatment = Literal["sharp", "fillet", "chamfer"]
Vec = tuple[float, float, float]

DFAM_GROUP = "dfam"
MATING_ATTR = "mating"
GRIP_FIN_ATTR = "grip_fin"  # face attr in DFAM_GROUP: grip_fit fin side / tip faces (same name as grip_fit tag)
GRIP_FIN_SECTION_ATTR = "grip_fin_min_section"  # body attr in DFAM_GROUP: grip_fit min fin width over lead-in band (mm)
SKIP_ATTR = "dfam_skip"
BED_ATTR = "bed"  # body attr in DFAM_GROUP: print bed direction; absent = "-z" (min-Z face)
BedDir = Literal["-z", "+z", "-x", "+x", "-y", "+y"]  # '+x' = bed is the X-max face (outward normal +X)
BED_DIRS: tuple[str, ...] = ("-z", "+z", "-x", "+x", "-y", "+y")
AXIS_PARALLEL = 0.99  # |cos| above this = parallel
ROOF_NORMAL_TOL = 0.01  # |n . axis| below this = plane contains hole axis (teardrop roof)
PARALLEL_TOL = 1e-6  # |n1 x n2| below this = parallel normals (tangent / coplanar)
BED_ON_TOL_MM = 0.01
RISE_TOL_MM = 0.01
CONE_TILT_TOL = 0.01  # bed-edge cone |n . z| above this = inclined chamfer (vertical wall ~0, horizontal top ~1)
TOP_EDGE_PARALLEL = (
    0.95  # |cos| chamfer top edge vs bed edge: roughly parallel (chamfer strip; side edges ~perpendicular)
)
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


def bed_wall_ratio(phi_deg: float, angle_deg: float) -> float:
    """Bed leg / wall leg giving chamfer face angle angle_deg (from bed) on an edge of interior dihedral phi_deg.
    Triangle at the edge vertex (angles phi, alpha, beta): d_bed / d_wall = sin(phi + alpha) / sin(alpha)
    = sin(phi) * cot(alpha) + cos(phi). phi = 90 gives 1 / tan(alpha). Pure. Moved from features._bed_leg_ratio."""
    phi, al = math.radians(phi_deg), math.radians(angle_deg)
    ratio = math.sin(phi) / math.tan(al) + math.cos(phi)
    if ratio <= 0.0:
        raise ValueError(f"bed_chamfer: no chamfer at {angle_deg} deg on a {phi_deg} deg edge (leg ratio {ratio:.3f})")
    return ratio


def bed_footprint_mm(width_mm: float, alpha_deg: float, phi_deg: float) -> float:
    """Bed footprint of a chamfer face of width w (perpendicular to the edge) at face angle alpha from bed, on interior
    dihedral phi. Triangle O (edge), P (bed), Q (wall), |PQ| = w, angles phi@O, alpha@P, gamma = 180 - phi - alpha @Q:
    L_bed = w * sin(gamma) / sin(phi) = w * sin(phi + alpha) / sin(phi) = w * (cos(alpha) + sin(alpha) * cot(phi)).
    phi = 90 -> w * cos(alpha) = chamfer_leg_mm on a square corner. Pure."""
    if not 0.0 < phi_deg < 180.0:
        raise ValueError(f"interior dihedral must be in (0, 180) deg, got {phi_deg}")
    phi, al = math.radians(phi_deg), math.radians(alpha_deg)
    return width_mm * math.sin(phi + al) / math.sin(phi)


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
    loop, both loops share the centre. Width = |r1 - r2|. None if the face is not an annulus."""
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


def bed_axis(bed: str) -> tuple[int, int]:
    """Bed direction -> (axis index 0|1|2 for x|y|z, outward sign). '+x' = bed is the X-max face. Else ValueError."""
    if bed not in BED_DIRS:
        raise ValueError(f"bed must be one of {BED_DIRS}, got {bed!r}")
    return "xyz".index(bed[1]), (1 if bed[0] == "+" else -1)


def bed_from_body(body: adsk.fusion.BRepBody) -> str:
    """Bed direction stored in attribute ("dfam", "bed"). Absent -> "-z"."""
    attr = body.attributes.itemByName(DFAM_GROUP, BED_ATTR)
    if attr is None:
        return "-z"
    bed_axis(attr.value)  # validates stored value
    return attr.value


def set_bed(body: adsk.fusion.BRepBody, bed: BedDir) -> BedDir:
    """Write bed direction to attribute ("dfam", "bed") after bed_axis validation. Returns bed."""
    bed_axis(bed)
    body.attributes.add(DFAM_GROUP, BED_ATTR, bed)
    return bed


def bed_rotation_axes(bed: str) -> tuple[Vec, Vec, Vec]:
    """Rotation R that puts the bed face on -Z, as images of body x, y, z (columns of R). Proper, det +1.

    Outward bed normal (bed_axis sign * e_axis) maps to (0, 0, -1). '-z' = identity. Pure.
    """
    bed_axis(bed)  # validates
    table: dict[str, tuple[Vec, Vec, Vec]] = {
        "-z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "+z": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
        "+x": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
        "-x": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
        "+y": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
        "-y": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
    }
    return table[bed]


_IDENTITY_AXES: tuple[Vec, Vec, Vec] = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _frame_matrix(origin: Vec, axes: tuple[Vec, Vec, Vec]) -> adsk.core.Matrix3D:
    """Matrix3D for a frame: origin + images of x, y, z (Matrix3D.setWithCoordinateSystem). Only adsk call site here."""
    from adsk import core

    ex, ey, ez = (core.Vector3D.create(*c) for c in axes)
    m = core.Matrix3D.create()
    m.setWithCoordinateSystem(core.Point3D.create(*origin), ex, ey, ez)
    return m


def bed_to_minus_z_matrix(bed: str) -> adsk.core.Matrix3D:
    """Matrix3D (rotation only, origin 0) for TemporaryBRepManager.transform: bed face -> min Z (bed_rotation_axes)."""
    return _frame_matrix((0.0, 0.0, 0.0), bed_rotation_axes(bed))


SIG_TOL_CM = 1e-4  # print-frame face match: vertex centroid per axis and area, cm (Fusion internal units)
FaceSig = tuple[Vec, float]  # (vertex centroid cm, area cm2)


def rotate_point(p: Vec, axes: tuple[Vec, Vec, Vec]) -> Vec:
    """Image of p under the rotation whose columns are axes (images of body x, y, z), e.g. bed_rotation_axes(bed). Pure."""
    return (
        p[0] * axes[0][0] + p[1] * axes[1][0] + p[2] * axes[2][0],
        p[0] * axes[0][1] + p[1] * axes[1][1] + p[2] * axes[2][1],
        p[0] * axes[0][2] + p[1] * axes[1][2] + p[2] * axes[2][2],
    )


def _sig_close(a: FaceSig, b: FaceSig, tol: float) -> bool:
    (ac, aa), (bc, ba) = a, b
    return abs(aa - ba) <= tol and all(abs(ac[k] - bc[k]) <= tol for k in range(3))


_CellKey = tuple[int, int, int, int]


def _sig_cell(s: FaceSig, width: float) -> _CellKey:
    (cx, cy, cz), area = s
    return (math.floor(cx / width), math.floor(cy / width), math.floor(cz / width), math.floor(area / width))


_CELL_NEIGHBOURS = [(a, b, c, d) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) for d in (-1, 0, 1)]


def _cell_index(sigs: list[FaceSig], width: float) -> dict[_CellKey, list[int]]:
    out: dict[_CellKey, list[int]] = {}
    for j, s in enumerate(sigs):
        out.setdefault(_sig_cell(s, width), []).append(j)
    return out


def _close_to(s: FaceSig, sigs: list[FaceSig], index: dict[_CellKey, list[int]], width: float, tol: float) -> list[int]:
    """Ascending j with _sig_close(s, sigs[j], tol). Scans the 3^4 neighbour cells of s only (same predicate, exact)."""
    k = _sig_cell(s, width)
    hits = [
        j
        for d in _CELL_NEIGHBOURS
        for j in index.get((k[0] + d[0], k[1] + d[1], k[2] + d[2], k[3] + d[3]), [])
        if _sig_close(s, sigs[j], tol)
    ]
    return sorted(hits)


def match_faces(orig: list[FaceSig], copy: list[FaceSig], tol: float = SIG_TOL_CM) -> dict[int, int]:
    """Original face index -> copy face index, for faces whose signatures agree within tol. Pure.

    Same index wins when it matches (live: TemporaryBRepManager.copy keeps face order). Else the single copy face
    within tol. A copy face matched by more than one original face is not mapped (ambiguous). Missing key = not mapped.
    """

    if tol <= 0:
        raise ValueError(f"match_faces: tol must be > 0, got {tol!r}")
    width = 2.0 * tol  # |delta| <= tol moves a value at most half a cell: every close pair sits in neighbouring cells
    copy_index = _cell_index(copy, width)
    orig_index = _cell_index(orig, width)

    def unique(j: int) -> bool:
        return len(_close_to(copy[j], orig, orig_index, width, tol)) == 1

    out: dict[int, int] = {}
    used: set[int] = set()
    for i, o in enumerate(orig):
        cand = [j for j in _close_to(o, copy, copy_index, width, tol) if j not in used]
        if i in cand and unique(i):
            j = i
        elif len(cand) == 1 and unique(cand[0]):
            j = cand[0]
        else:
            continue
        out[i] = j
        used.add(j)
    return out


def _face_sig(face: adsk.fusion.BRepFace) -> FaceSig:
    """(vertex centroid cm, area cm2). The vertex centroid commutes with rigid motion: compare original vs copy."""
    pts: dict[int, tuple[float, float, float]] = {}
    for e in face.edges:
        for v in (e.startVertex, e.endVertex):
            if v is not None:
                g = v.geometry
                pts[v.tempId] = (g.x, g.y, g.z)
    if not pts:  # no vertices (closed smooth face): point on face stands in
        p = face.pointOnFace
        pts[-1] = (p.x, p.y, p.z)
    n = len(pts)
    centre = (
        sum(p[0] for p in pts.values()) / n,
        sum(p[1] for p in pts.values()) / n,
        sum(p[2] for p in pts.values()) / n,
    )
    return centre, face.area


def print_frame_with_tags(
    body: adsk.fusion.BRepBody,
) -> tuple[adsk.fusion.BRepBody, dict[str, frozenset[int]] | None, list[str]]:
    """(print-frame body, tags, unmapped). bed '-z': (body, None, []) = tags read from the body itself.

    Other beds: TEMPORARY copy rotated by bed_to_minus_z_matrix, faces matched to the original by rotated signature
    (match_faces, before the z shift; shift moves both sides alike), then shifted so min Z = 0. tags: name ->
    tempId set on the copy for every mating / grip_fin tagged original face that mapped. unmapped: "<tag> face <i>".
    """
    from adsk import fusion

    bed = bed_from_body(body)
    if bed == "-z":
        return body, None, []
    mgr = fusion.TemporaryBRepManager.get()
    tmp = mgr.copy(body)
    mgr.transform(tmp, bed_to_minus_z_matrix(bed))
    rot = bed_rotation_axes(bed)
    orig_sigs: list[FaceSig] = []
    src = list(body.faces)
    for f in src:
        c, a = _face_sig(f)
        orig_sigs.append((rotate_point(c, rot), a))
    pair = match_faces(orig_sigs, [_face_sig(f) for f in tmp.faces])
    mgr.transform(tmp, _frame_matrix((0.0, 0.0, -tmp.boundingBox.minPoint.z), _IDENTITY_AXES))
    copy_faces = list(tmp.faces)
    tags: dict[str, frozenset[int]] = {}
    unmapped: list[str] = []
    for name in (MATING_ATTR, GRIP_FIN_ATTR):
        ids: set[int] = set()
        for i, f in enumerate(src):
            if not _has_attr(f, name):
                continue
            if i in pair:
                ids.add(copy_faces[pair[i]].tempId)
            else:
                unmapped.append(f"{name} face {i}")
        tags[name] = frozenset(ids)
    return tmp, tags, unmapped


def print_frame_body(body: adsk.fusion.BRepBody) -> adsk.fusion.BRepBody:
    """Body in PRINT frame: bed face on min Z (floor at z = 0). bed '-z' -> body itself, no copy.

    Other beds: TEMPORARY copy (see print_frame_with_tags). Original body untouched. Copy is not in the design; callers read
    dfam/bed, dfam_skip from the ORIGINAL.
    """
    return print_frame_with_tags(body)[0]


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
    """All DfAM findings for one body. fail_as_warn=True: FAIL downgraded to WARN (rule names kept).

    Geometry is read in PRINT frame (print_frame_body, bed from dfam/bed); non-'-z' beds tag messages "[print frame ...]".
    """
    bed = bed_from_body(body)
    frame, tags, unmapped = print_frame_with_tags(body)
    body_fact, holes, edges = facts_from_body(frame, tags)
    section = _grip_fin_section(body)  # attr lives on the ORIGINAL body, not the print-frame copy
    if section is not None:
        body_fact = replace(body_fact, grip_fin_section_mm=section)
    found = check_all(body_fact, holes, edges, skip_from_body(body))
    if bed != "-z":
        found = [replace(f, msg=f"{f.msg} [print frame, bed {bed}]") for f in found]
        if unmapped:
            found.append(
                Finding(
                    "bed_frame",
                    "WARN",
                    f"bed {bed}: tagged faces not mapped onto print-frame copy ({'; '.join(unmapped)}); their exemptions are not applied",
                )
            )
    if fail_as_warn:
        return [replace(f, level="WARN") if f.level == "FAIL" else f for f in found]
    return found

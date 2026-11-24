"""DfAM pure rule checks on plain facts (no adsk, runs offline)."""

from __future__ import annotations

import math

from fusion_kit._vec import _dot, _unit
from fusion_kit.dfam_limits import (
    BED_CHAMFER_ANGLE_DEG,
    BED_CHAMFER_MM,
    BED_CHAMFER_SMALL_BELOW_MM,
    BED_CHAMFER_SMALL_MM,
    BED_CHAMFER_TOL_MM,
    BED_SHORT_EDGE_MM,
    FIRST_LAYER_MIN_FEATURE_MM,
    GRIP_FIN_SECTION_MIN_MM,
    JOINT_CHAMFER_MM,
    MIN_WALL_MM,
    PETG_WALL_HI_MM,
    PETG_WALL_LO_MM,
    PIN_BASE_CHAMFER_MIN_MM,
    SEAM_NOTCH_MM,
    SLENDER_RATIO,
    SLIT_MAX_MM,
    TEARDROP_NOTCH_MAX_D_MM,
    TEARDROP_NOTCH_MAX_RATIO,
    TEARDROP_NOTCH_MM,
    TEARDROP_TOL,
    TEARDROP_WALL_DEG,
    VERT_FILLET_MIN_MM,
    WARP_SIDE_MM,
)
from fusion_kit.dfam_types import BodyFact, EdgeFact, Finding, HoleFact, Vec

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
                if e.phi_deg is not None and not bed_chamfer_feasible(e.phi_deg, BED_CHAMFER_ANGLE_DEG):
                    out.append(
                        Finding(
                            "bed_chamfer",
                            "WARN",
                            f"{e.label} bed edge phi {e.phi_deg:.1f} deg has no chamfer leg, left sharp (BED_CHAMFER_SKIP)",
                        )
                    )
                elif e.length_mm is not None and e.length_mm < BED_SHORT_EDGE_MM:
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
    = sin(phi) * cot(alpha) + cos(phi). phi = 90 gives 1 / tan(alpha). Pure. Moved from features._bed_leg_ratio.

    -> bed leg / wall leg = sin(phi)/tan(angle)+cos(phi). Pure
    """
    phi, al = math.radians(phi_deg), math.radians(angle_deg)
    ratio = math.sin(phi) / math.tan(al) + math.cos(phi)
    if ratio <= 0.0:
        raise ValueError(f"bed_chamfer: no chamfer at {angle_deg} deg on a {phi_deg} deg edge (leg ratio {ratio:.3f})")
    return ratio


BED_WALL_DENOM_EPS = 1e-3  # wall leg denominator sin(phi) + cos(phi) * tan(angle) at or below this: no chamfer leg


def bed_chamfer_feasible(phi_deg: float, angle_deg: float) -> bool:
    """True if a bed chamfer at face angle angle_deg has a positive wall leg on interior dihedral phi_deg.
    Wall leg = d / (sin phi + cos phi * tan angle); denominator <= eps = no chamfer (outer 135 deg tip at 46 deg: -0.0251).
    Single criterion: features_bed.bed_chamfer skips these edges, check_edges reports them as WARN (BED_CHAMFER_SKIP).
    """
    phi, al = math.radians(phi_deg), math.radians(angle_deg)
    return math.sin(phi) + math.cos(phi) * math.tan(al) > BED_WALL_DENOM_EPS


def bed_footprint_mm(width_mm: float, alpha_deg: float, phi_deg: float) -> float:
    """Bed footprint of a chamfer face of width w (perpendicular to the edge) at face angle alpha from bed, on interior
    dihedral phi. Triangle O (edge), P (bed), Q (wall), |PQ| = w, angles phi@O, alpha@P, gamma = 180 - phi - alpha @Q:
    L_bed = w * sin(gamma) / sin(phi) = w * sin(phi + alpha) / sin(phi) = w * (cos(alpha) + sin(alpha) * cot(phi)).
    phi = 90 -> w * cos(alpha) = chamfer_leg_mm on a square corner. Pure.

    -> bed footprint of a chamfer face = w*sin(phi+alpha)/sin(phi). Pure
    """
    if not 0.0 < phi_deg < 180.0:
        raise ValueError(f"interior dihedral must be in (0, 180) deg, got {phi_deg}")
    phi, al = math.radians(phi_deg), math.radians(alpha_deg)
    return width_mm * math.sin(phi + al) / math.sin(phi)

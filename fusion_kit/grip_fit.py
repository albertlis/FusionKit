"""Press-fit grip fins: thin bending fins for a bore (kind="hole") or peg (kind="shaft"), axis X, Y or Z.

Each fin is a 4-corner strip in the plane perpendicular to the grip axis: a tangential tip edge at the tip radius,
leaning from radial by `lean`, rooted in solid beyond the relief pocket. Relief lets the fin bend instead of
crushing the bearing. Print axis = part axis for axis="z", so fins are vertical walls (no supports).

Axis frame (axis_frame): along = axis, radial = next axis cyclically, tangential = the one after.
  z -> radial x, tangential y (old layout); x -> radial y, tangential z; y -> radial z, tangential x.
  centre = (radial coord, tangential coord) in mm: z: (x, y), x: (y, z), y: (z, x).

Grip zone is always [start, start + depth] along the axis (start = LOW coordinate). `entry` says which end the
bearing enters from ("low" | "high"); the lead-in cone sits there.

kind="hole":  relief ring D/2..D/2+relief, conical roof at the far end (rises to D/2); fin tip at D/2 - interference; root in the wall.
kind="shaft": relief ring between core d/2-relief and the peg surface; fin tip at d/2 + interference;
              root inside the core. The peg must rise above its base (ring cut is open outwards).

Lead-in (`lead_in`): revolve cut of a triangle trims the fin tips at the entry end so a bearing slides on.
hole: default entry low (bed), cone wider at entry. shaft: default entry high (peg tip), cone narrower at tip.

Face tag: fin side + tip faces + axial end caps get attribute DFAM_GROUP/GRIP_FIN_ATTR (from fusion_kit.dfam, source of truth).
docs/dfam_guidelines.md: "Press-fit grip fins" section.
"""

import math
from typing import Literal

import adsk.core
import adsk.fusion

from fusion_kit import features
from fusion_kit.dfam import DFAM_GROUP, GRIP_FIN_ATTR, GRIP_FIN_SECTION_ATTR
from fusion_kit.expr import Ex, as_str, evaluate
from fusion_kit.sketch import mm, new_sketch, param_poly
from fusion_kit.timeline import check_label

GripKind = Literal["hole", "shaft"]
GripAxis = Literal["x", "y", "z"]
EntrySide = Literal["low", "high"]
KINDS: tuple[str, ...] = ("hole", "shaft")
AXES: tuple[GripAxis, ...] = ("x", "y", "z")
ENTRIES: tuple[str, ...] = ("low", "high")
ROOT_OVERLAP_MM = "0.6 mm"  # fin root depth into solid beyond the relief edge (fuses with wall / core)
SHAFT_CUT_OUTER_MM = "0.5 mm"  # shaft relief ring reaches this far beyond the peg surface (empty space)
LEAD_IN_EXTRA_MM = "0.5 mm"  # cone axial height = lead_in + this: slope > 45 deg from horizontal (overhang check)
RELIEF_ROOF_ANGLE = "46 deg"  # relief far-end roof: cone slope from horizontal; 45 exact fails the overhang check
FIN_FACE_TOL_MM = 0.05  # radial band tolerance for the fin face tag
FIN_ROOF_OVERLAP_MM = (
    "0.05 mm"  # fin far end sits this far into solid above the relief roof (join fuses, no hanging face)
)
FIN_GAP_MM = 0.4  # min arc gap between neighbouring fin root footprints at the relief radius (no overlap)
FIN_RAMP_ANGLE = (
    "46 deg"  # fin entry-end cone slope from horizontal (same convention as RELIEF_ROOF_ANGLE; 45 exact fails)
)
FIN_RAMP_OVERCUT_MM = "0.2 mm"  # ramp cut line runs this far past the fin tip: no tangential contact with the tip face


def axis_frame(axis: str) -> tuple[int, int, int]:
    """(along, radial, tangential) coordinate indices (x=0, y=1, z=2) for a grip axis. Pure.

    Right-handed cycle: x -> (y, z), y -> (z, x), z -> (x, y). z is the old layout.
    """
    if axis not in AXES:
        raise ValueError(f"grip_fit: axis {axis!r} not in {AXES}")
    a = AXES.index(axis)
    return a, (a + 1) % 3, (a + 2) % 3


def default_entry(kind: GripKind) -> EntrySide:
    """Old behaviour: hole entered at its low end (bed), shaft at its high end (peg tip). Pure."""
    if kind not in KINDS:
        raise ValueError(f"grip_fit: kind {kind!r} not in {KINDS}")
    return "low" if kind == "hole" else "high"


def _entry_side(kind: GripKind, entry: str | None) -> EntrySide:
    side = default_entry(kind) if entry is None else entry
    if side == "low":
        return "low"
    if side == "high":
        return "high"
    raise ValueError(f"grip_fit: entry {side!r} not in {ENTRIES}")


def model_point(axis: str, radial: str, tangential: str, along: str) -> tuple[str, str, str]:
    """(x, y, z) model-coordinate expressions from radial / tangential / along-axis parts. Pure string assembly."""
    a, r, t = axis_frame(axis)
    out = [""] * 3
    out[a], out[r], out[t] = along, radial, tangential
    return out[0], out[1], out[2]


def fin_corners(
    kind: GripKind,
    diameter: str | Ex,
    interference: str | Ex,
    fin_thickness: str | Ex,
    relief: str | Ex,
    lean: str | Ex,
) -> list[tuple[str, str]]:
    """Fin quad as (radial, tangential) mm expressions about the nominal circle centre.

    Order: tip-, tip+, root_a, root_b (polygon). Tip edge is tangential, so its midpoint sits exactly
    at the tip radius (the innermost point for hole, the outermost for shaft). Root corners sit at
    the lean-rotated root centre +/- half thickness.
    Angles are Fusion expressions ("45 deg"); lengths are mm expressions. Pure string assembly.
    """
    if kind not in KINDS:
        raise ValueError(f"grip_fit: kind {kind!r} not in {KINDS}")
    d = f"({as_str(diameter)})"
    i = f"({as_str(interference)})"
    t = f"({as_str(fin_thickness)})"
    r = f"({as_str(relief)})"
    lam = f"({as_str(lean)})"
    ov = f"({ROOT_OVERLAP_MM})"
    half = f"({t} / 2)"
    span = f"({r} + {ov} + {i})"  # |r_root - r_tip|, same for both kinds
    if kind == "hole":
        sigma = 1
        r_root = f"({d} / 2 + {r} + {ov})"
        r_tip = f"({d} / 2 - {i})"
    else:
        sigma = -1
        r_root = f"({d} / 2 - {r} - {ov})"
        r_tip = f"({d} / 2 + {i})"
    rc_t = f"({span} * tan({lam}))"  # root centre tangential offset
    side = f"{half} * sin({lam})"  # radial offset of root corners
    if sigma > 0:
        ra_r, rb_r = f"({r_root} - {side})", f"({r_root} + {side})"
    else:
        ra_r, rb_r = f"({r_root} + {side})", f"({r_root} - {side})"
    ra_t = f"({rc_t} + {half} * cos({lam}))"
    rb_t = f"({rc_t} - {half} * cos({lam}))"
    return [(r_tip, f"-{half}"), (r_tip, half), (ra_r, ra_t), (rb_r, rb_t)]


def _fin_quad_mm(
    kind: GripKind,
    diameter_mm: float,
    relief_mm: float,
    fin_thickness_mm: float,
    interference_mm: float,
    lean_rad: float,
) -> list[tuple[float, float]]:
    """Float twin of fin_corners: (radial, tangential) mm of tip-, tip+, root_a, root_b. Pure."""
    if kind not in KINDS:
        raise ValueError(f"grip_fit: kind {kind!r} not in {KINDS}")
    ov = evaluate(ROOT_OVERLAP_MM, {})
    half = fin_thickness_mm / 2
    span = relief_mm + ov + interference_mm
    side = half * math.sin(lean_rad)
    rc_t = span * math.tan(lean_rad)
    ra_t = rc_t + half * math.cos(lean_rad)
    rb_t = rc_t - half * math.cos(lean_rad)
    if kind == "hole":
        r_root = diameter_mm / 2 + relief_mm + ov
        r_tip = diameter_mm / 2 - interference_mm
        ra_r, rb_r = r_root - side, r_root + side
    else:
        r_root = diameter_mm / 2 - relief_mm - ov
        r_tip = diameter_mm / 2 + interference_mm
        ra_r, rb_r = r_root + side, r_root - side
    return [(r_tip, -half), (r_tip, half), (ra_r, ra_t), (rb_r, rb_t)]


def fin_gap_mm(
    n_fins: int,
    kind: GripKind,
    diameter_mm: float,
    relief_mm: float,
    fin_thickness_mm: float,
    interference_mm: float,
    lean_rad: float,
) -> float:
    """Arc gap (mm) between neighbouring fin root footprints at the relief radius, for n_fins equally spaced.

    Band = fin radial extent in the relief zone: shaft [d/2 - relief, d/2 + interference (tip)], hole
    [d/2 - interference (tip), d/2 + relief]. Footprint widens outward (lean), so the gap is the MIN over the band,
    sampled at 41 radii (ends inset 0.02 mm). Gap at radius rho = rho * (2 pi / n - footprint angle). Pure.
    """
    if kind == "hole":
        band_lo, band_hi = diameter_mm / 2 - interference_mm, diameter_mm / 2 + relief_mm
    else:
        band_lo, band_hi = diameter_mm / 2 - relief_mm, diameter_mm / 2 + interference_mm
    gaps = []
    for k in range(41):
        rho = band_lo + 0.02 + (band_hi - band_lo - 0.04) * k / 40
        lo, hi = fin_footprint_rad(kind, diameter_mm, relief_mm, fin_thickness_mm, interference_mm, lean_rad, rho)
        gaps.append(rho * (2 * math.pi / n_fins - (hi - lo)))
    return min(gaps)


def _in_convex_quad(quad: list[tuple[float, float]], x: float, y: float) -> bool:
    """Point inside convex quad (corners in order, either winding). Pure."""
    signs = []
    for k in range(4):
        (ax, ay), (bx, by) = quad[k], quad[(k + 1) % 4]
        signs.append((bx - ax) * (y - ay) - (by - ay) * (x - ax))
    return all(s >= 0 for s in signs) or all(s <= 0 for s in signs)


def fin_footprint_rad(
    kind: GripKind,
    diameter_mm: float,
    relief_mm: float,
    fin_thickness_mm: float,
    interference_mm: float,
    lean_rad: float,
    rho_mm: float,
) -> tuple[float, float]:
    """(lo, hi) polar angle (rad, 0 = seed ray, + toward tangential) of the fin quad on the circle of radius rho_mm.

    Circle intersection with the quad (radial, tangential) edges; the single arc inside the quad. Pure.
    """
    quad = _fin_quad_mm(kind, diameter_mm, relief_mm, fin_thickness_mm, interference_mm, lean_rad)
    angles: list[float] = []
    for k in range(4):
        (r1, t1), (r2, t2) = quad[k], quad[(k + 1) % 4]
        dr, dt = r2 - r1, t2 - t1
        a = dr * dr + dt * dt
        b = 2 * (r1 * dr + t1 * dt)
        c = r1 * r1 + t1 * t1 - rho_mm * rho_mm
        disc = b * b - 4 * a * c
        if disc < 0:
            continue
        for s in ((-b - math.sqrt(disc)) / (2 * a), (-b + math.sqrt(disc)) / (2 * a)):
            if 0 <= s <= 1:
                angles.append(math.atan2(t1 + s * dt, r1 + s * dr))
    angles.sort()
    arcs: list[tuple[float, float]] = []
    for i in range(len(angles) - 1):
        a0, a1 = angles[i], angles[i + 1]
        mid = (a0 + a1) / 2
        if a1 - a0 > 1e-12 and _in_convex_quad(quad, rho_mm * math.cos(mid), rho_mm * math.sin(mid)):
            arcs.append((a0, a1))
    if len(arcs) != 1:
        raise ValueError(f"grip_fit: fin footprint on r={rho_mm:.3f} mm is {len(arcs)} arcs, expected 1")
    return arcs[0]


def max_fin_count(
    diameter_mm: float,
    kind: GripKind,
    relief_mm: float,
    fin_thickness_mm: float,
    interference_mm: float,
    lean_rad: float,
) -> int:
    """Largest fin count whose root footprints keep >= FIN_GAP_MM between neighbours at the relief radius. 0 if none. Pure."""
    best = 0
    n = 1
    while fin_gap_mm(n, kind, diameter_mm, relief_mm, fin_thickness_mm, interference_mm, lean_rad) >= FIN_GAP_MM:
        best = n
        n += 1
    return best


def _check_fin_count(
    kind: GripKind,
    diameter: str | Ex,
    interference: str | Ex,
    fin_thickness: str | Ex,
    relief: str | Ex,
    lean: str | Ex,
    fin_count: str | Ex,
) -> None:
    """ValueError when fin_count exceeds max_fin_count. Runs only when every input evaluates offline (literals);
    parametric inputs (user parameter names) skip the check. Fusion design values are not read here."""
    try:
        d, i, t, r, lam = (evaluate(as_str(x), {}) for x in (diameter, interference, fin_thickness, relief, lean))
        n = int(round(evaluate(as_str(fin_count), {})))
    except NameError:
        return
    m = max_fin_count(d, kind, r, t, i, lam)
    if n > m:
        raise ValueError(
            f"grip_fit: fin_count {n} exceeds max {m} for {kind} d={d} mm relief={r} mm "
            f"(fin root footprints overlap, neighbour gap < {FIN_GAP_MM} mm); use fin_count <= {m}"
        )


def _quad_width_at_radial(quad: list[tuple[float, float]], r_mm: float) -> float:
    """Tangential width (mm) of convex quad (radial, tangential corners) on the line radial = r_mm. 0.0 if missed. Pure."""
    ts: list[float] = []
    for k in range(4):
        (r1, t1), (r2, t2) = quad[k], quad[(k + 1) % 4]
        if abs(r2 - r1) < 1e-12:
            if abs(r_mm - r1) < 1e-9:
                ts.extend((t1, t2))
            continue
        s = (r_mm - r1) / (r2 - r1)
        if 0.0 <= s <= 1.0:
            ts.append(t1 + s * (t2 - t1))
    return max(ts) - min(ts) if ts else 0.0


def fin_section_min_mm(
    kind: GripKind,
    diameter_mm: float,
    relief_mm: float,
    fin_thickness_mm: float,
    interference_mm: float,
    lean_rad: float,
    lead_mm: float,
) -> float:
    """Min tangential fin width (mm) over the lead-in radial band: tip .. lead-in outer radius (the lead-in cone trims
    this band at the entry end). hole: [D/2 - i, D/2 - i + lead]; shaft: [D/2 + i - lead, D/2 + i]. 41 samples, ends
    inset min(0.02, span / 4). Lean 0 = rectangle, so the result is exactly fin_thickness. Pure floats.
    Feeds grip_fin_sliver (dfam): value < GRIP_FIN_SECTION_MIN_MM = knife edge at entry.
    """
    if kind not in KINDS:
        raise ValueError(f"grip_fit: kind {kind!r} not in {KINDS}")
    if lead_mm <= 0:
        raise ValueError(f"grip_fit: lead_mm {lead_mm} must be > 0")
    quad = _fin_quad_mm(kind, diameter_mm, relief_mm, fin_thickness_mm, interference_mm, lean_rad)
    r_tip = diameter_mm / 2 - interference_mm if kind == "hole" else diameter_mm / 2 + interference_mm
    r_cut = r_tip + lead_mm if kind == "hole" else r_tip - lead_mm
    lo, hi = min(r_tip, r_cut), max(r_tip, r_cut)
    inset = min(0.02, (hi - lo) / 4)
    return min(_quad_width_at_radial(quad, lo + inset + (hi - lo - 2 * inset) * k / 40) for k in range(41))


def lead_in_corners(
    kind: GripKind,
    diameter: str | Ex,
    interference: str | Ex,
    lead_in: str | Ex,
    start: str | Ex,
    depth: str | Ex,
    entry: EntrySide | None = None,
) -> list[tuple[str, str]]:
    """Lead-in trim triangle as (radial, along) mm expressions about the grip axis. Pure string assembly.

    Revolve it 360 deg as a cut. Entry end: low -> z=start; high -> z=start+depth. None = kind default
    (hole low, shaft high).
    Corners: entry tip-side (r_tip, z_entry), entry outer (r_entry, z_entry), far (r_tip, z_far).
    r_tip = fin tip radius (hole D/2-i, shaft D/2+i); r_entry = r_tip + lead_in (hole, wider at entry) |
    r_tip - lead_in (shaft, narrower at entry). The radial rule follows kind, not entry.
    Height H = lead_in + LEAD_IN_EXTRA_MM; z_far = z_entry + H (entry low) | z_entry - H (entry high).
    Cone slope: dz/dr = H/lead_in > 1 -> steeper than 45 deg from horizontal (no overhang fail in print).
    """
    if kind not in KINDS:
        raise ValueError(f"grip_fit: kind {kind!r} not in {KINDS}")
    side = _entry_side(kind, entry)
    d = f"({as_str(diameter)})"
    i = f"({as_str(interference)})"
    lead = f"({as_str(lead_in)})"
    h = f"({lead} + ({LEAD_IN_EXTRA_MM}))"
    st = as_str(start)
    if kind == "hole":
        r_tip = f"({d} / 2 - {i})"
        r_entry = f"({r_tip} + {lead})"
    else:
        r_tip = f"({d} / 2 + {i})"
        r_entry = f"({r_tip} - {lead})"
    if side == "low":
        z_entry = f"({st})"
        z_far = f"({z_entry} + {h})"
    else:
        z_entry = f"(({st}) + ({as_str(depth)}))"
        z_far = f"({z_entry} - {h})"
    return [(r_tip, z_entry), (r_entry, z_entry), (r_tip, z_far)]


def lead_in_trims_body(lead_mm: float, interference_mm: float, relief_mm: float) -> bool:
    """True when the lead-in triangle reaches solid core (shaft) / seat wall (hole) past the relief pocket. Pure floats.

    Entry corner sits (lead - interference) mm beyond the bore wall (hole) or inside the core edge (shaft); the relief
    pocket is relief mm deep. Reach > relief -> triangle cuts solid (entry chamfer). Otherwise the triangle lies in the
    pocket, below the fin entry ramp (fin_ramp_corners removes that band), so a revolve cut has no target body.
    Boundary (reach == relief) touches only a face -> False. Fin tips are clipped only at a higher lead threshold
    (test_lead_in_fin_clip_threshold_above_wall), so this guard decides the cut.
    """
    return lead_mm > interference_mm + relief_mm


def fin_ramp_corners(
    kind: GripKind,
    diameter: str | Ex,
    interference: str | Ex,
    relief: str | Ex,
    entry_along: str | Ex,
    entry: EntrySide,
) -> list[tuple[str, str]]:
    """Fin entry ramp triangle as (radial, along) mm expressions about the grip axis. Pure string assembly.

    Revolve it 360 deg as a cut on the fin bodies only (never the wall). z_e = fin entry end plane (entry_along:
    start for low, start + depth for high = entry face). Corners: root (r_root, z_e), overcut (r_end, z_e), cone end
    (r_end, z_e +/- (span + e) * tan(FIN_RAMP_ANGLE)); sign + for entry low, - for entry high.
    The cut line runs root -> tip at FIN_RAMP_ANGLE from horizontal: fin tip starts later (low) / ends earlier (high)
    than the root, measured from entry. Entry end = cone, no flat down-facing fin foot.
    hole : r_root = D/2 + relief + ROOT_OVERLAP_MM, r_end = D/2 - interference - FIN_RAMP_OVERCUT_MM.
    shaft: r_root = d/2 - relief - ROOT_OVERLAP_MM, r_end = d/2 + interference + FIN_RAMP_OVERCUT_MM.
    span = relief + ROOT_OVERLAP_MM + interference (same as fin_corners); e = FIN_RAMP_OVERCUT_MM.
    """
    if kind not in KINDS:
        raise ValueError(f"grip_fit: kind {kind!r} not in {KINDS}")
    if entry not in ENTRIES:
        raise ValueError(f"grip_fit: entry {entry!r} not in {ENTRIES}")
    d = f"({as_str(diameter)})"
    i = f"({as_str(interference)})"
    rel = f"({as_str(relief)})"
    ov = f"({ROOT_OVERLAP_MM})"
    e = f"({FIN_RAMP_OVERCUT_MM})"
    z_e = f"({as_str(entry_along)})"
    if kind == "hole":
        r_root, r_end = f"({d} / 2 + {rel} + {ov})", f"({d} / 2 - {i} - {e})"
    else:
        r_root, r_end = f"({d} / 2 - {rel} - {ov})", f"({d} / 2 + {i} + {e})"
    rise = f"(({rel} + {ov} + {i} + {e}) * tan({FIN_RAMP_ANGLE}))"
    z_end = f"({z_e} + {rise})" if entry == "low" else f"({z_e} - {rise})"
    return [(r_root, z_e), (r_end, z_e), (r_end, z_end)]


def relief_profile_corners(
    kind: GripKind,
    diameter: str | Ex,
    relief: str | Ex,
    start: str | Ex,
    depth: str | Ex,
    roof_angle: str | Ex = RELIEF_ROOF_ANGLE,
    entry: EntrySide = "low",
) -> list[tuple[str, str]]:
    """Relief pocket profile as (radial, along) mm expressions about the grip axis. Pure string assembly.

    Revolve it 360 deg as a cut. Zone = [start, start + depth]. Quad: open end on the entry face, outer wall r_out
    across the zone, then a conical roof rising inward at roof_angle from horizontal (dz/dr = tan(roof_angle)).
    entry low : open end at start; roof far end at start + depth + rise (past the zone, away from entry).
    entry high: open end at start + depth; roof far end at start - rise (before start, away from entry). Mirror of low.
    hole: ring r in [D/2, D/2 + relief] (bore wall to relief edge); roof rises inward to the bore wall D/2.
          Ceiling width = relief; nothing cut inside D/2 (bore beyond the zone is already empty).
    shaft: groove r in [D/2 - relief, D/2 + SHAFT_CUT_OUTER_MM], roof rises from outer to core radius.
    No segment perpendicular to the axis past the open end (a flat ceiling is an unprintable overhang).
    Roof rise = (r_out - r_in) * tan(roof_angle) >= relief depth.
    """
    side = _entry_side(kind, entry)
    st = f"({as_str(start)})"
    z_end = f"({st} + ({as_str(depth)}))"
    r_in, r_out = _relief_radii(kind, diameter, relief)
    rise = relief_roof_rise(kind, diameter, relief, roof_angle)
    if side == "low":
        return [(r_in, st), (r_out, st), (r_out, z_end), (r_in, f"({z_end} + {rise})")]
    return [(r_in, z_end), (r_out, z_end), (r_out, st), (r_in, f"({st} - {rise})")]


def _relief_radii(kind: GripKind, diameter: str | Ex, relief: str | Ex) -> tuple[str, str]:
    """(r_in, r_out) mm expressions of the relief ring about the grip axis. Pure string assembly, kind not checked."""
    d = f"({as_str(diameter)})"
    rel = f"({as_str(relief)})"
    if kind == "hole":
        return f"({d} / 2)", f"({d} / 2 + {rel})"
    return f"({d} / 2 - {rel})", f"({d} / 2 + ({SHAFT_CUT_OUTER_MM}))"


def relief_roof_rise(
    kind: GripKind,
    diameter: str | Ex,
    relief: str | Ex,
    roof_angle: str | Ex = RELIEF_ROOF_ANGLE,
) -> str:
    """Axial rise (mm expression) of the relief conical roof = (r_out - r_in) * tan(roof_angle), same constants as
    relief_profile_corners. Far end of the roof = start + depth + this (entry low) | start - this (entry high). Pure string assembly.
    Size a pocket from it, entry low: depth = wanted_roof_end - start - relief_roof_rise(...).
    Entry high: start = wanted_roof_end + relief_roof_rise(...); depth = entry face - start."""
    if kind not in KINDS:
        raise ValueError(f"grip_fit: kind {kind!r} not in {KINDS}")
    r_in, r_out = _relief_radii(kind, diameter, relief)
    return f"(({r_out} - {r_in}) * tan({as_str(roof_angle)}))"


def fin_band_mm(kind: GripKind, diameter_mm: float, interference_mm: float, relief_mm: float) -> tuple[float, float]:
    """Radial band (lo, hi) mm of fin side/tip faces: between tip radius and relief edge, +/- FIN_FACE_TOL_MM.

    hole: tip D/2-i, relief edge D/2+relief. shaft: tip D/2+i, relief edge D/2-relief. Plain floats (offline-testable).
    """
    if kind not in KINDS:
        raise ValueError(f"grip_fit: kind {kind!r} not in {KINDS}")
    half = diameter_mm / 2
    if kind == "hole":
        r_tip, r_edge = half - interference_mm, half + relief_mm
    else:
        r_tip, r_edge = half + interference_mm, half - relief_mm
    return min(r_tip, r_edge) - FIN_FACE_TOL_MM, max(r_tip, r_edge) + FIN_FACE_TOL_MM


def is_grip_fin_face(
    normal_z: float,
    radial_mm: float,
    z_mm: float,
    band: tuple[float, float],
    zone: tuple[float, float],
) -> bool:
    """Fin face rule (pure). normal_z / z_mm = normal and position component ALONG the grip axis (any axis).

    Caller passes planar faces only (relief cylinder and lead-in cone are non-planar).
    Tagged: parallel to axis (|n_axis| < 1e-3: fin side walls and tip face), radius in band, along-axis in zone.
    Not tagged here: faces normal to axis (|n_axis| ~ 1: top/bottom, incl. axial end caps: see is_grip_fin_end_face),
    bore / relief wall (cylinder), lead-in cone (not planar).
    """
    lo, hi = band
    z_lo, z_hi = zone
    return abs(normal_z) < 1e-3 and lo <= radial_mm <= hi and z_lo <= z_mm <= z_hi


def is_grip_fin_end_face(
    normal_axis: float,
    radial_mm: float,
    z_mm: float,
    band: tuple[float, float],
    zone: tuple[float, float],
) -> bool:
    """Fin axial end cap (pure). Planar face normal to the grip axis (|n_axis| ~ 1), radius in band, along-axis
    position in zone (+/- FIN_FACE_TOL_MM). The fin's far end cap meets the peg at a pin base edge: without this tag
    the edge has no grip_fin face and the pin_base_chamfer exemption (dfam on_grip_fin) never applies.
    Lead-in cone end is not planar: not matched. Sign of normal_axis ignored.
    """
    lo, hi = band
    z_lo, z_hi = zone
    in_zone = z_lo - FIN_FACE_TOL_MM <= z_mm <= z_hi + FIN_FACE_TOL_MM
    return abs(abs(normal_axis) - 1.0) < 1e-3 and lo <= radial_mm <= hi and in_zone


def _tag_fin_faces(
    body: adsk.fusion.BRepBody,
    centre_r_mm: float,
    centre_t_mm: float,
    axis: GripAxis,
    kind: GripKind,
    diameter_mm: float,
    interference_mm: float,
    relief_mm: float,
    start_mm: float,
    depth_mm: float,
) -> None:
    """Attribute DFAM_GROUP/GRIP_FIN_ATTR = "1" on fin side + tip faces (is_grip_fin_face) and axial end caps
    (is_grip_fin_end_face) of body.

    Only planar faces (objectType Plane) are candidates: relief wall and bore (cylinders) and the lead-in cone are
    excluded by type. Radial position (about centre_r / centre_t) and along-axis position from face.pointOnFace
    (cm -> mm). Zone = [start, start + depth_mm] along the axis, depth_mm = fin extrude length (to roof end).
    """
    band = fin_band_mm(kind, diameter_mm, interference_mm, relief_mm)
    zone = (start_mm, start_mm + depth_mm)
    a, r, t = axis_frame(axis)
    for face in body.faces:
        if face.geometry.objectType != adsk.core.Plane.classType():
            continue
        n = features.face_normal(face)
        p = face.pointOnFace
        pc = (p.x * 10, p.y * 10, p.z * 10)  # cm -> mm
        radial = math.hypot(pc[r] - centre_r_mm, pc[t] - centre_t_mm)
        n_axis = (n.x, n.y, n.z)[a]
        if is_grip_fin_face(n_axis, radial, pc[a], band, zone) or is_grip_fin_end_face(
            n_axis, radial, pc[a], band, zone
        ):
            face.attributes.add(DFAM_GROUP, GRIP_FIN_ATTR, "1")


def _normal_sign(plane: adsk.fusion.ConstructionPlane, axis: GripAxis) -> int:
    """+1 if the plane normal points +axis, else -1. Decides how the axis-normal extrudes are started."""
    n = plane.geometry.normal
    return 1 if (n.x, n.y, n.z)[axis_frame(axis)[0]] > 0 else -1


def _extrude_zone(
    comp: adsk.fusion.Component,
    prof: adsk.fusion.Profile,
    depth: str | Ex,
    normal_sign: int,
    *,
    op: features.Op,
    label: str,
    bodies: list[adsk.fusion.BRepBody] | None = None,
) -> adsk.fusion.ExtrudeFeature:
    """Extrude the zone [start, start + depth] along the axis from a profile on the plane at start.

    features.extrude always goes +sketch normal. normal_sign +1 (normal = +axis): plain extrude.
    normal_sign -1 (normal = -axis): start_offset = -depth along normal (= +depth along axis), then depth along
    normal, so the span is still [start, start + depth]. The branch is picked at runtime per plane, nothing
    hardcoded per axis; the live smoke (tests/smoke/fusion_smoke_grip_fit_axis.py) records which case each axis hit.
    """
    start_offset = None if normal_sign > 0 else f"-({as_str(depth)})"
    return features.extrude(comp, prof, depth, op=op, start_offset=start_offset, bodies=bodies, label=label)


def _record_fin_section(
    body: adsk.fusion.BRepBody,
    kind: GripKind,
    diameter_mm: float,
    interference_mm: float,
    relief_mm: float,
    fin_thickness_mm: float,
    lean: str | Ex,
    lead_mm: float,
) -> None:
    """Write fin_section_min_mm to body attr (DFAM_GROUP, GRIP_FIN_SECTION_ATTR); keeps the min of an existing value.

    lean must evaluate offline (literal angle); a user-parameter lean skips the write (NameError), like _check_fin_count.
    """
    try:
        lean_rad = evaluate(as_str(lean), {})
    except NameError:
        return
    section = fin_section_min_mm(kind, diameter_mm, relief_mm, fin_thickness_mm, interference_mm, lean_rad, lead_mm)
    existing = body.attributes.itemByName(DFAM_GROUP, GRIP_FIN_SECTION_ATTR)
    if existing is None:
        body.attributes.add(DFAM_GROUP, GRIP_FIN_SECTION_ATTR, f"{section:.4f}")
    else:
        existing.value = f"{min(float(existing.value), section):.4f}"


def _axis_through(
    comp: adsk.fusion.Component,
    axis: GripAxis,
    coords: dict[str, str],
    *,
    label: str,
) -> adsk.fusion.ConstructionAxis:
    """Live construction axis along `axis` through the two other coordinates (coords keyed "x"/"y"/"z")."""
    if axis == "x":
        return features.axis_x_through(comp, y=coords["y"], z=coords["z"], label=label)
    if axis == "y":
        return features.axis_y_through(comp, x=coords["x"], z=coords["z"], label=label)
    return features.axis_z_through(comp, x=coords["x"], y=coords["y"], label=label)


def grip_fit(
    comp: adsk.fusion.Component,
    body: adsk.fusion.BRepBody,
    centre: tuple[str | Ex, str | Ex],
    start: str | Ex,
    depth: str | Ex,
    diameter: str | Ex,
    kind: GripKind = "hole",
    *,
    axis: GripAxis = "z",
    entry: EntrySide | None = None,
    interference: str | Ex = "0.6 mm",
    fin_thickness: str | Ex = "0.8 mm",
    fin_count: str | Ex = "16",
    relief: str | Ex = "1.2 mm",
    lean: str | Ex = "45 deg",
    lead_in: str | Ex = "1 mm",
    label: str,
) -> adsk.fusion.CombineFeature:
    """Grip fins in a bore (kind="hole") or on a peg (kind="shaft") of body, axis parallel to X, Y or Z.

    axis     : grip axis "x" | "y" | "z" (default "z" = old behaviour).
    centre   : (radial, tangential) mm of the axis, expressions (Ex or str). Zero allowed.
               z: (x, y); x: (y, z); y: (z, x) — see axis_frame.
    start    : LOW coordinate of the grip zone along the axis (mm expression, model coords).
    depth    : grip zone length > 0; zone = [start, start + depth] (mm expression). Fin height = relief depth.
               Fin span = full zone: entry low -> start .. start + depth + relief_roof_rise + FIN_ROOF_OVERLAP_MM (far end
               inside solid past the roof); entry high -> start - relief_roof_rise - FIN_ROOF_OVERLAP_MM .. start + depth
               (entry face). The fin entry end is ramped (fin_ramp_corners): cone at FIN_RAMP_ANGLE, root at the entry face.
    diameter : nominal bore D (hole) or peg d (shaft), mm expression.
    entry    : end the bearing enters from, "low" | "high". None = kind default: hole low (bed), shaft high (peg tip).
               The lead-in cone sits at this end.
    Steps: relief revolve cut (trapezoid, conical roof at far end) -> one seed fin (sketch, NewBody) -> circular pattern ->
           fin entry ramp revolve cut (fin bodies only, FIN_RAMP_ANGLE cone) -> one join of all fins into body ->
           lead-in revolve cut only if lead_in_trims_body (else skipped) -> fin face tag
           (DFAM_GROUP/GRIP_FIN_ATTR).
           hole: the cut also makes the bore (do not pre-cut).
    Build order matters: relief cut before fin join (a join after a cut fills holes, docs/gotchas.md).
    lead_in  : lead-in cone height/radial reach at the entry end (mm expr). Must satisfy 0 < lead_in and
               lead_in + 0.5 mm <= depth. The cone is cut only when lead_in > interference + relief (lead_in_trims_body):
               below that it has no solid to trim and no lead-in feature is created. Decided at build time.
    label    : snake_case timeline prefix; steps get f"{label}_*" names.
    """
    check_label(label)
    corners = fin_corners(kind, diameter, interference, fin_thickness, relief, lean)  # ValueError before Fusion
    _check_fin_count(kind, diameter, interference, fin_thickness, relief, lean, fin_count)  # ValueError before Fusion
    side = _entry_side(kind, entry)  # ValueError before Fusion
    _along, r, t = axis_frame(axis)  # ValueError before Fusion
    cr, ct, st = as_str(centre[0]), as_str(centre[1]), as_str(start)
    rise = relief_roof_rise(kind, diameter, relief)
    # fin sketch plane: low -> start (fins grow +axis to far roof end); high -> start - rise - overlap (fins grow to entry face)
    fin_st = f"({st})" if side == "low" else f"({st} - {rise} - {FIN_ROOF_OVERLAP_MM})"
    plane = features.axis_plane(comp, axis, fin_st, mm(fin_st), label=f"{label}_plane")
    normal_sign = _normal_sign(plane, axis)
    d, rel = as_str(diameter), as_str(relief)
    d_mm, rel_mm, i_mm = mm(d), mm(rel), mm(as_str(interference))
    lead_mm, lead_extra_mm, depth_mm = mm(as_str(lead_in)), mm(LEAD_IN_EXTRA_MM), mm(as_str(depth))
    if not depth_mm > 0:
        raise ValueError(f"grip_fit: depth {depth_mm} mm must be > 0")
    if not (lead_mm > 0 and lead_mm + lead_extra_mm <= depth_mm):
        raise ValueError(f"grip_fit: lead_in {lead_mm} mm + {LEAD_IN_EXTRA_MM} must fit in depth {depth_mm} mm")
    lead_corners = lead_in_corners(kind, diameter, interference, lead_in, start, depth, side)  # expressions only

    relief_corners = relief_profile_corners(kind, diameter, relief, start, depth, entry=side)  # expressions only
    fin_entry = st if side == "low" else f"({st} + {as_str(depth)})"  # fin entry end plane z_e = entry face
    ramp_corners = fin_ramp_corners(kind, diameter, interference, relief, fin_entry, side)  # expressions only
    fin_len = f"({as_str(depth)} + {rise} + {FIN_ROOF_OVERLAP_MM})"  # zone + roof rise + overlap

    axis_obj = _axis_through(comp, axis, {AXES[r]: cr, AXES[t]: ct}, label=f"{label}_axis")
    # Relief: revolve cut of the trapezoid in the plane through the grip axis (tangential coord = ct).
    ct_mm = mm(ct)
    relief_plane = features.axis_plane(comp, AXES[t], ct, ct_mm, label=f"{label}_relief_plane")
    relief_sketch = new_sketch(comp, relief_plane, label=f"{label}_relief_sketch")
    param_poly(relief_sketch, [model_point(axis, f"({cr}) + {rr}", ct, zz) for rr, zz in relief_corners])
    features.revolve(comp, features.profile(relief_sketch), axis_obj, "cut", bodies=[body], label=f"{label}_relief")

    fin_sketch = new_sketch(comp, plane, label=f"{label}_fin_sketch")
    pts = [model_point(axis, f"({cr}) + {rad}", f"({ct}) + {tan}", fin_st) for rad, tan in corners]
    param_poly(fin_sketch, pts)
    fin = _extrude_zone(comp, features.profile(fin_sketch), fin_len, normal_sign, op="new", label=f"{label}_fin")

    pat = features.pattern_circular(comp, [fin], axis_obj, fin_count, label=f"{label}_pattern")
    tools = list(pat.bodies) if pat is not None else list(fin.bodies)
    # Fin entry ramp: revolve cut on the fin bodies only (wall untouched), after the pattern (pattern copies the extrude
    # feature, not later cuts). No fin bodies -> no target -> no cut created.
    if tools:
        ramp_sketch = new_sketch(comp, relief_plane, label=f"{label}_fin_ramp_sketch")
        param_poly(ramp_sketch, [model_point(axis, f"({cr}) + {rr}", ct, zz) for rr, zz in ramp_corners])
        features.revolve(comp, features.profile(ramp_sketch), axis_obj, "cut", bodies=tools, label=f"{label}_fin_ramp")
    join = features.combine(comp, body, tools, op="join", label=f"{label}_join")

    # Lead-in: revolve cut of the trim triangle (tangential coord = ct). Fins run the full zone; the entry end is already
    # cone-shaped by the ramp. Cut only when lead_in_trims_body (reaches solid); otherwise it sits in the relief pocket
    # with no target body (Fusion "Brak bryły docelowej") -> not created.
    if lead_in_trims_body(lead_mm, i_mm, rel_mm):
        lead_plane = features.axis_plane(comp, AXES[t], ct, ct_mm, label=f"{label}_lead_plane")
        lead_sketch = new_sketch(comp, lead_plane, label=f"{label}_lead_sketch")
        param_poly(lead_sketch, [model_point(axis, f"({cr}) + {rr}", ct, zz) for rr, zz in lead_corners])
        features.revolve(comp, features.profile(lead_sketch), axis_obj, "cut", bodies=[body], label=f"{label}_lead_in")

    _record_fin_section(body, kind, d_mm, i_mm, rel_mm, mm(as_str(fin_thickness)), lean, lead_mm)
    _tag_fin_faces(body, mm(cr), ct_mm, axis, kind, d_mm, i_mm, rel_mm, mm(fin_st), mm(fin_len))
    return join

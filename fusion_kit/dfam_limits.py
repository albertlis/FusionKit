"""DfAM thresholds and geometry tolerances. Pure constants, no adsk."""

from __future__ import annotations

import math

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


SIG_TOL_CM = 1e-4  # print-frame face match: vertex centroid per axis and area, cm (Fusion internal units)

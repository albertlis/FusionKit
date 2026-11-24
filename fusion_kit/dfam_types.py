"""DfAM fact dataclasses, attribute names, bed and edge type aliases. Pure, no adsk."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    phi_deg: float | None = (
        None  # bed edge: interior dihedral, deg (chamfer feasibility, dfam_rules.bed_chamfer_feasible)
    )


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


GrooveRect = tuple[float, float, float, float]  # footprint (x0, y0, x1, y1) mm, bed plane

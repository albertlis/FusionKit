"""User parameters of the demo bracket. Units mm, full English names."""

PARAMS: dict[str, tuple[str, str, str]] = {
    "plate_length": ("90 mm", "mm", "plate size along X"),
    "plate_width": ("60 mm", "mm", "plate size along Y"),
    "plate_thickness": ("5 mm", "mm", "plate thickness along Z"),
    "mounting_hole_diameter": ("5.5 mm", "mm", "mounting hole diameter"),
    "hole_edge_offset": ("8 mm", "mm", "mounting hole centre distance from plate edge"),
    "wall_height": ("40 mm", "mm", "upright wall height above bed"),
    "wall_thickness": ("5 mm", "mm", "upright wall thickness along Y"),
    "vertical_corner_fillet_radius": (
        "3 mm",
        "mm",
        "fillet on outer vertical corners of plate and wall ends (DfAM min 3 mm)",
    ),
    "gusset_thickness": ("4 mm", "mm", "gusset thickness along X"),
    "gusset_size": ("25 mm", "mm", "gusset leg length"),
    "gusset_x_offset": ("plate_length / 2 - 15 mm", "mm", "gusset centre offset from plate centre along X"),
    "slot_length": ("30 mm", "mm", "wall slot length along X"),
    "slot_width": ("6 mm", "mm", "wall slot vertical side height along Z"),
    "slot_center_height": ("17 mm", "mm", "wall slot side centre height above bed (slot apex 36 mm, wall top 40 mm)"),
    "slot_roof_rise": (
        "16 mm",
        "mm",
        "wall slot gable roof rise above slot shoulders (46.8 deg, under 45 deg overhang limit)",
    ),
    "slot_corner_chamfer_size": ("0.5 mm", "mm", "chamfer on the vertical corners of the wall slot"),
}
